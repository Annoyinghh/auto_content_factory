"""Bounded discovery of original Xinhua videos from a monthly economic release."""

import datetime as dt
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from core.intake import _read_report, has_report
from core.source_media import candidates


_INDEX = 'https://www.news.cn/fortune/index.htm'
_ZONE = dt.timezone(dt.timedelta(hours=8))
_ARTICLE = re.compile(r'/(?:[a-zA-Z0-9_-]+/)*(\d{8})/[a-f0-9]{32}/c\.html')
_PERIOD = re.compile(r'(?<!\d)(?:(\d{4})年)?(\d{1,2})月份?国民经济运行')


def _article_date(url):
    try:
        parts = urlsplit(url)
        match = _ARTICLE.fullmatch(parts.path)
        if (parts.scheme == 'https' and parts.hostname == 'www.news.cn' and not parts.username
                and not parts.password and parts.port in {None, 443} and not parts.query and match):
            return dt.datetime.strptime(match[1], '%Y%m%d').date()
    except (ValueError, TypeError):
        pass
    return None


def _published_day(report, now):
    try:
        published = dt.datetime.fromisoformat(report.get('published', ''))
        if published.tzinfo and now - dt.timedelta(days=7) <= published <= now + dt.timedelta(minutes=10):
            return published.astimezone(_ZONE).date()
    except (ValueError, TypeError):
        pass
    return None


def _fetch_html(url):
    # Callers supply only a validated article route or this fixed public category.
    if url != _INDEX and not _article_date(url):
        raise ValueError('不是新华网原文链接')
    with requests.get(url, timeout=(4, 8), stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.is_redirect or 'text/html' not in response.headers.get('Content-Type', ''):
            raise ValueError('原文页面不可读取')
        body = bytearray()
        for block in response.iter_content(65536):
            body.extend(block)
            if len(body) > 2_000_000:
                raise ValueError('原文页面过大')
    return body.decode('utf-8', errors='replace')


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.href, self.text = [], '', []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href, self.text = dict(attrs).get('href', ''), []

    def handle_data(self, value):
        if self.href:
            self.text.append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.rows.append((self.href, ''.join(self.text).strip()))
            self.href = ''


def _event_body(body, year, month, release):
    periods = list(_PERIOD.finditer(body))
    if periods:
        current = [p for p in periods if int(p[2]) == month and (not p[1] or int(p[1]) == year)
                   and body[max(0, p.start()-2):p.start()] != '去年']
        if not current:
            return False
        if any(p[1] for p in current):
            return True
    # Short press-conference clips sometimes identify the reporting period only
    # inside a statistic. Require both the same release day and official venue.
    if (f'{release.month}月{release.day}日' not in body or '国家统计局' not in body
            or not re.search(r'国务院新闻办.{0,8}新闻发布会', body)):
        return False
    period = re.compile(rf'(?<!\d)(?:(\d{{4}})年|今年)?(?:1[至—–-]|前)?{month}(?:个)?月份?')
    return any((not p[1] or int(p[1]) == year) and body[max(0, p.start()-2):p.start()] != '去年'
               for p in period.finditer(body))


def _media_key(asset):
    parts = urlsplit(asset['download_url'])
    # Alias article pages can prefix the same production asset with another
    # article ID. The remaining observed Xinhua production ID is unchanged.
    path = re.sub(r'/\d{8}[a-f0-9]{32}_(Xx[A-Za-z0-9]+_\d{8}_CBV[A-Za-z0-9]+\.mp4)$', r'/\1', parts.path)
    return urlunsplit(('https', parts.hostname, path, '', ''))


def discover_related_reports(selected_reports, anchor, now, *, limit=8):
    """Return verified related video reports; never write, download media or publish.

    At most eight linked articles are read, plus one seed and one category HTML.
    This narrowly supports monthly national-economic releases; unrelated topics
    return no additions rather than broadening into generic financial footage.
    """
    period = _PERIOD.search(str(anchor))
    if not period or not 1 <= int(period[2]) <= 12:
        return []
    month = int(period[2])
    seeds = []
    for report in selected_reports:
        release = _article_date(report.get('url', ''))
        if release and release == _published_day(report, now):
            year = release.year - (month > release.month)
            body = str(report.get('summary', ''))
            if ((not period[1] or int(period[1]) == year) and '国家统计局' in body
                    and _event_body(body, year, month, release)):
                seeds.append((report, year, release))
    if not seeds:
        return []
    seed, year, release = seeds[0]
    seen = {r.get('url', '').split('#', 1)[0] for r in selected_reports}
    media_seen = {_media_key(a) for r in selected_reports for a in candidates(r) if a['kind'] == 'video'}
    result, reads = [], 0
    budget = max(0, min(8, int(limit)))
    for discovery_url in (seed['url'], _INDEX):
        if reads >= budget:
            break
        try:
            parser = _Links()
            parser.feed(_fetch_html(discovery_url))
            parser.close()
        except (requests.RequestException, OSError, ValueError):
            continue
        links = []
        for href, title in parser.rows:
            url = urljoin(discovery_url, href).split('#', 1)[0]
            if url in seen or _article_date(url) != release:
                continue
            seen.add(url)
            # Rank, never accept, by headline. Full original prose below makes
            # the final event decision, including titles without the anchor.
            priority = 4 * ('国家统计局' in title) + 2 * ('国民经济' in title)
            priority += bool(re.search(r'市场需求|消费|零售|进出口|工业|服务业|产业|价格|投资|就业', title))
            links.append((priority, url))
        for _, url in sorted(links, key=lambda row: -row[0]):
            if reads >= budget:
                break
            reads += 1
            report = _read_report({'url': url, 'title': '', 'summary': '', 'published': '',
                                   'headline_only': True}, now, allow_older=True)
            if (report.get('url') != url or _published_day(report, now) != release or not has_report(report)
                    or not _event_body(str(report.get('summary', '')), year, month, release)):
                continue
            videos = [a for a in candidates(report) if a['kind'] == 'video']
            fresh = {_media_key(a) for a in videos} - media_seen
            if not fresh:
                continue
            media_seen.update(fresh)
            result.append({**report, 'related_seed_url': seed['url'], 'discovery_url': discovery_url})
    return result
