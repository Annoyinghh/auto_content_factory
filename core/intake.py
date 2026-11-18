"""Rolling collection database and observed platform rankings."""

import datetime as dt
import json
import re
import sqlite3
from collections import defaultdict
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit
from xml.etree import ElementTree

from config import PROFILES
from core.daily_news import KEYWORDS, RESTRICTED_TOPICS, _feed_date, collect_news, parse_feed, plain_text, story_key, web_url


REFRESH_MINUTES = 10
RETENTION_DAYS = 7
RANK_BASE = "https://www.tunbang.com/hot"
MIN_REPORT_LENGTH = 300
# Some Windows DNS proxies return benchmarking addresses for public sites.
_BENCHMARK_PUBLISHERS = frozenset({
    "www.36kr.com", "36kr.com", "www.ifanr.com", "www.ithome.com", "www.chinanews.com.cn", "www.chinanews.com",
    "www.bbc.com", "www.thepaper.cn", "m.thepaper.cn", "news.qq.com", "news.bjd.com.cn", "xinwen.bjd.com.cn",
    "www.news.cn", "www.xinhuanet.com", "www1.xinhuanet.com", "m.gmw.cn", "news.gmw.cn", "news.cctv.com", "sports.cctv.com",
    "sports.cctv.cn", "news.ifeng.com", "i.ifeng.com", "feng.ifeng.com", "news.zhibo8.com", "www.zhibo8.com",
    "www.hupu.com", "m.hupu.com", "sports.iqilu.com", "www.cqnews.net", "3g.china.com",
    "news.sina.com.cn", "sports.sina.com.cn", "finance.sina.cn", "k.sina.cn", "www.sohu.com", "m.sohu.com",
    "www.163.com", "www.gov.cn", "www.mofcom.gov.cn", "ch.mofcom.gov.cn", "www.ciie.org",
})
_SOCIAL_DOMAINS = ("douyin.com", "xiaohongshu.com", "bilibili.com", "b23.tv", "weibo.com", "zhihu.com", "huya.com",
                   "v.qq.com", "haokan.baidu.com", "baike.baidu.com")


def parse_rank_page(page):
    entries = []
    for row in re.findall(r'<li class="row">(.*?)</li>', page, re.S):
        rank = re.search(r'<span class="rank[^\"]*">(\d+)</span>', row)
        link = re.search(r'<a class="row-title" href="([^\"]+)"[^>]*>(.*?)</a>', row, re.S)
        heat = re.search(r'<span class="hot">(.*?)</span>', row, re.S)
        if rank and link and web_url(plain_text(link[1])) and plain_text(link[2]):
            entries.append({"rank": int(rank[1]), "url": plain_text(link[1]), "title": plain_text(link[2]),
                            "heat": plain_text(heat[1]) if heat else ""})
    stamp = re.search(r"数据更新于\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", plain_text(page))
    if not entries or not stamp:
        raise ValueError("榜单页面缺少排名或更新时间")
    published = dt.datetime.fromisoformat(stamp[1]).replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return {"entries": sorted(entries, key=lambda entry: entry["rank"])[:60], "published": published.timestamp()}


def fetch_rank(platform):
    import requests

    url = f"{RANK_BASE}/{platform}/"
    with requests.get(url, timeout=(8, 15), stream=True) as response:
        response.raise_for_status()
        page = bytearray()
        for chunk in response.iter_content(65536):
            page.extend(chunk)
            if len(page) > 1_000_000:
                raise ValueError("榜单页面过大")
    return {**parse_rank_page(page.decode("utf-8")), "url": url, "source": "豚榜 · 公开榜单聚合"}


def _connect(output_dir):
    db = sqlite3.connect(Path(output_dir) / "collection.sqlite3", timeout=5)
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, collected REAL, story TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS ranks (platform TEXT PRIMARY KEY, snapshot TEXT, error TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS rank_observations (day TEXT, platform TEXT, id TEXT, "
                   "PRIMARY KEY(day, platform, id)) WITHOUT ROWID")
        db.execute("CREATE TABLE IF NOT EXISTS intake_state (id INTEGER PRIMARY KEY, checked REAL, errors TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS topic_search (id TEXT PRIMARY KEY, checked REAL)")
    return db


def read_intake(output_dir, now=None):
    now = now or dt.datetime.now().astimezone()
    day = now.astimezone().date()
    today = {"date": day.isoformat(), "articles": [], "rank_counts": {}}
    if not (Path(output_dir) / "collection.sqlite3").is_file():
        return {"articles": [], "ranks": {}, "checked": None, "errors": [], "today": today}
    cutoff = (now - dt.timedelta(days=RETENTION_DAYS)).timestamp()
    start = dt.datetime.combine(day, dt.time.min).timestamp()
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min).timestamp()
    articles = []
    with closing(_connect(output_dir)) as db:
        for collected, data in db.execute("SELECT collected, story FROM articles WHERE collected >= ?", (cutoff,)):
            story = json.loads(data)
            articles.append(story)
            if start <= collected < end:
                today["articles"].append({**story, "collected_at": collected,
                                          "category": story.get("category") or category_for_story(story)})
        ranks = {platform: {**(json.loads(data) if data else {}), "error": error}
                 for platform, data, error in db.execute("SELECT platform, snapshot, error FROM ranks")}
        today["rank_counts"] = dict(db.execute("SELECT platform, COUNT(*) FROM rank_observations "
                                                "WHERE day=? GROUP BY platform", (today["date"],)))
        checked, errors = db.execute("SELECT checked, errors FROM intake_state WHERE id = 1").fetchone() or (None, "[]")
    today["articles"].sort(key=lambda story: story["collected_at"], reverse=True)
    return {"articles": sorted(articles, key=lambda row: row["published"], reverse=True), "ranks": ranks,
            "checked": checked, "errors": json.loads(errors), "today": today}


def refresh_intake(output_dir, now):
    import requests

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        articles = pool.submit(collect_news, now)
        ranks = {platform: pool.submit(fetch_rank, platform) for platform in PROFILES}
        stories, errors = articles.result()
        changes = {}
        for platform, result in ranks.items():
            try:
                changes[platform] = (result.result(), "")
            except (ValueError, requests.RequestException) as error:
                changes[platform] = (None, f"榜单更新失败（{type(error).__name__}）")

    day = now.astimezone().date()
    cutoff = (now - dt.timedelta(days=RETENTION_DAYS)).timestamp()
    with closing(_connect(output_dir)) as db, db:
        db.execute("DELETE FROM articles WHERE collected < ?", (cutoff,))
        db.execute("DELETE FROM topic_search WHERE checked < ?", (cutoff,))
        # Upgrade legacy copies of the same RSS excerpt without replacing a read article.
        db.executemany("INSERT INTO articles VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET story="
                       "json_set(articles.story, '$.content_origin', 'feed', '$.summary_truncated', "
                       "json_extract(excluded.story, '$.summary_truncated')) "
                       "WHERE json_extract(excluded.story, '$.content_origin') = 'feed' AND "
                       "json_extract(articles.story, '$.content_origin') IS NULL AND "
                       "json_extract(articles.story, '$.url') = json_extract(excluded.story, '$.url') AND "
                       "json_extract(articles.story, '$.summary') = json_extract(excluded.story, '$.summary')",
                       ((story_key(story), now.timestamp(), json.dumps(story, ensure_ascii=False)) for story in stories))
        db.executemany(
            "INSERT INTO ranks VALUES (?, ?, ?) ON CONFLICT(platform) DO UPDATE SET "
            "snapshot=CASE WHEN ranks.snapshot IS NULL OR "
            "json_extract(excluded.snapshot, '$.published') >= json_extract(ranks.snapshot, '$.published') "
            "THEN excluded.snapshot ELSE ranks.snapshot END, error=excluded.error",
            ((platform, json.dumps(board, ensure_ascii=False) if board is not None else None, error)
             for platform, (board, error) in changes.items()),
        )
        db.execute("DELETE FROM rank_observations WHERE day < ?", ((day - dt.timedelta(days=RETENTION_DAYS)).isoformat(),))
        db.executemany("INSERT OR IGNORE INTO rank_observations VALUES (?, ?, ?)",
                       ((day.isoformat(), platform, story_key(entry)) for platform, (board, _) in changes.items()
                        if board is not None for entry in board.get("entries", ())))
        db.execute("UPDATE ranks SET snapshot=NULL WHERE json_extract(snapshot, '$.published') < ?", (cutoff,))
        db.execute("INSERT OR REPLACE INTO intake_state VALUES (1, ?, ?)", (now.timestamp(), json.dumps(errors)))
    return read_intake(output_dir, now)


def _signature(text):
    text = re.sub(r"(?<=[A-Za-z0-9])\s*(?:对战|对阵|迎战|vs\.?)\s*(?=[A-Za-z0-9])", "vs", text, flags=re.I)
    compact = re.sub(r"[\W_]+", "", text.casefold())
    terms = {compact[i:i + 3] for i in range(len(compact) - 2)}
    pairs = {compact[i:i + 2] for i in range(len(compact) - 1)}
    return compact, terms, pairs


def _anchors(title):
    phrases = re.findall(r"《([^》]{3,24})》|([a-z][a-z0-9]+(?: [a-z0-9]+){1,2})", title.casefold())
    result = {re.sub(r"[\W_]+", "", quoted or latin) for quoted, latin in phrases}
    # A named event at the start of a headline can match a longer report about that event.
    event = re.match(r"^[\u4e00-\u9fff]{2,3}?(?:博会|会|节|杯|展)", title)
    if event:
        result.add(event[0])
    place = re.search(r"(?:相信|拍到(?:了)?|走进|探访|来到)([\u4e00-\u9fff]{2,6}?(?:湖|湾|山|岛|海|河|镇|谷))(?=的|[，。！!？?]|$)", title)
    if place:
        result.add(place[1])
    return {phrase for phrase in result if len(phrase) >= 3}


def has_report(story):
    note = bool(story and story.get('source_platform') == 'xiaohongshu'
                and story.get('content_type') == 'photo_note' and story.get('source_type') == 'reference'
                and re.fullmatch(r'[a-f0-9]{24}', str(story.get('source_note_id', ''))))
    minimum = 20 if note else MIN_REPORT_LENGTH
    if story and story.get('content_origin') == 'article' and story.get('content_type') == 'video_article':
        from core.source_media import candidates
        if any(row['kind'] == 'video' for row in candidates(story)):
            minimum = 60
    return bool(story and story.get('content_origin') != 'feed' and not story.get("headline_only")
                and len(story.get("summary", "")) >= minimum)


def _match_score(topic, anchors, article):
    name, terms, pairs = topic
    headline, tokens, headline_pairs = article
    if not headline:
        return 0
    if name in headline or (len(headline) >= 4 and headline in name):
        return 2
    if 6 <= len(name) <= 12 and re.search(re.escape(name[:4]) + r".{0,14}" + re.escape(name[-2:]), headline):
        return 1.75
    common = len(terms & tokens)
    if common >= 3 and common / len(terms) >= 0.5:
        return 1 + common / len(terms)
    if common and len(name) >= 10:
        shared = len(pairs & headline_pairs)
        if shared >= 5 and shared / len(pairs) >= 0.4:
            return 1 + shared / len(pairs)
    return max((len(anchor) / len(name) for anchor in anchors if anchor in headline), default=0) if anchors else 0


def match_hotspots(snapshot, now=None, *, require_report=False, max_age_hours=4):
    now = now or dt.datetime.now().astimezone()
    cutoff = (now - dt.timedelta(hours=max_age_hours)).timestamp()
    latest = now.timestamp() + 600
    boards = snapshot["ranks"]
    fresh = {platform for platform, board in boards.items() if cutoff <= board.get("published", 0) <= latest}
    articles = [(story, _signature(story["title"])) for story in snapshot["articles"]
                if fresh and story.get("source_type") != "reference" and (not require_report or has_report(story))]
    by_term = defaultdict(list)
    for index, (_, (_, tokens, _)) in enumerate(articles):
        for token in tokens:
            by_term[token].append(index)
    matched = {}
    for platform, board in boards.items():
        if platform not in fresh:
            matched[platform] = [{**row, "article": None} for row in board.get("entries", [])]
            continue
        rows = []
        for row in board.get("entries", []):
            topic = _signature(row["title"])
            name, terms, _ = topic
            anchors = _anchors(row["title"])
            best, best_score = None, 0
            if len(name) >= 4:
                # Every scoring branch needs a shared trigram or an exact anchor.
                # Original order still decides ties between equally complete reports.
                candidates = set()
                for term in terms | {anchor[:3] for anchor in anchors}:
                    candidates.update(by_term.get(term, ()))
                for index in sorted(candidates):
                    article, signature = articles[index]
                    score = _match_score(topic, anchors, signature)
                    if score > best_score or (score and score == best_score and has_report(article) and not has_report(best)):
                        best, best_score = article, score
            rows.append({**row, "article": best})
        matched[platform] = rows
    return matched


def _search_reports(row, now, *, reference=False, output_dir=None):
    import requests

    title = row["title"]
    # Search the named work or location instead of a slogan wrapped around it.
    work = re.match(r"《([^》]{3,30})》", title)
    anchors = _anchors(title)
    query = work[1] if work else (max(anchors, key=len) if anchors else title[:72])
    query = row.get('search_query') or query
    sources = (
        ("https://cn.bing.com/search", {"q": query, "format": "rss"}),
        ("https://www.bing.com/search", {"q": query, "format": "rss"}),
        ("https://www.bing.com/news/search", {"q": query, "format": "rss", "cc": "us", "setlang": "zh-hans"}),
        ("https://news.google.com/rss/search", {"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}),
    )
    topic = _signature(title)
    if len(topic[0]) < 4:
        return []
    for endpoint, params in sources:
        try:
            with requests.get(endpoint, params=params, timeout=(5, 8), stream=True) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 1_500_000:
                        raise ValueError("新闻搜索 RSS 过大")
                reports = parse_feed(bytes(body), now, search_results=True,
                                     allow_undated=endpoint.endswith(".com/search"), allow_older=reference)
        except (requests.RequestException, ValueError, ElementTree.ParseError):
            continue
        ranked = sorted(((_match_score(topic, anchors, _signature(report["title"])), i, report)
                         for i, report in enumerate(reports)), key=lambda entry: (-entry[0], entry[1]))
        usable = []
        for score, _, report in ranked:
            if not score or len(usable) >= 6:
                break
            parts = urlsplit(report["url"])
            if parts.hostname in {"bing.com", "www.bing.com", "cn.bing.com"} and parts.path.lower() == "/news/apiclick.aspx":
                publisher_url = parse_qs(parts.query).get("url", [""])[0]
                if web_url(publisher_url):
                    report["url"] = publisher_url
            if _social_domain(urlsplit(report["url"]).hostname or ""):
                continue
            report["headline_only"] = True
            if reference:
                # Web-search RSS dates can be crawl times, not publication dates.
                report["published"] = ""
            usable.append(report)
        if endpoint.startswith(("https://www.bing.com/", "https://cn.bing.com/")) or reference:
            verified_reports = []
            for report in usable:
                verified = _read_report(report, now, allow_older=True) if reference else _read_report(report, now)
                if reference and not has_report(verified):
                    from core.browser_source import read_public_article
                    verified = read_public_article(report["url"], output_dir) or verified
                if reference and not has_report(verified) and output_dir is not None:
                    from core.official_capture import capture_official_notice
                    try:
                        capture=capture_official_notice(report,output_dir)
                    except (ValueError,OSError):
                        capture=None
                    if capture:
                        page=capture['capture']
                        verified={**report,'title':page['title'],'summary':page['summary'],'published':page['published'],
                                  'source':page.get('source') or '官方通知','headline_only':False,'content_origin':'article'}
                if has_report(verified):
                    if not reference:
                        return [verified]
                    verified_reports.append({**verified, "source_type": "reference"})
            if verified_reports:
                return verified_reports
        elif usable:
            return usable[:3]
    return []


def _social_domain(host):
    return any(host == domain or host.endswith("." + domain) for domain in _SOCIAL_DOMAINS)


def collect_topic_reports(output_dir, anchor, now, *, query=""):
    """Fetch the chosen topic, independently of the background ranking rotation."""
    from core.editorial import source_options

    snapshot = read_intake(output_dir, now)
    chosen = source_options(snapshot, anchor, now, recommend=False)["anchor"]
    query = query.strip() or chosen["title"]
    if not 1 <= len(query) <= 160:
        raise ValueError("请输入 1–160 字的资料关键词。")
    topic, anchors = _signature(query), _anchors(query)
    found = _search_reports({"title": query}, now, reference=True, output_dir=output_dir)
    reports = []
    for row in found:
        if not _match_score(topic, anchors, _signature(row["title"])):
            continue
        verified = row if has_report(row) else _read_report(row, now, allow_older=True)
        if has_report(verified):
            reports.append(verified)
    cutoff = (now - dt.timedelta(days=RETENTION_DAYS)).timestamp()
    with closing(_connect(output_dir)) as db, db:
        db.execute("DELETE FROM articles WHERE collected < ?", (cutoff,))
        db.executemany("INSERT OR REPLACE INTO articles VALUES (?, ?, ?)",
                       ((story_key(row), now.timestamp(), json.dumps(row, ensure_ascii=False)) for row in reports))
    result = source_options(read_intake(output_dir, now), anchor, now, recommend=False, local_rank=True)
    # Explicitly edited keywords may broaden the topic; keep that search visible in the UI.
    result.update(collected=len(reports), collection_query=query, searched_ids=[story_key(row) for row in reports])
    return result


class _ArticleText(HTMLParser):
    def __init__(self, container=None):
        super().__init__()
        self.container = container
        self.stack = []
        self.body_depth = None
        self.body_seen = False
        self.skip = 0
        self.paragraph = None
        self.parts = []
        self.published = ""
        self.title = ""
        self.site = ""
        self.title_parts = []
        self.in_title = False
        self.in_heading = False
        self.heading_parts = []
        self.time_depth = None
        self.clock = ''
        self.media = []
        self.pending_media = []

    def _inside(self):
        return self.container is None or self.body_depth is not None

    def _finish_paragraph(self):
        if self.paragraph is not None:
            paragraph = " ".join("".join(self.paragraph).split())
            if len(paragraph) >= (2 if self.container else 35) and paragraph not in self.parts:
                self.parts.append(paragraph)
                for index in self.pending_media:
                    self.media[index]['context'] = paragraph[:350]
                self.pending_media.clear()
            self.paragraph = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag in {'p', 'div', 'section', 'article', 'aside', 'footer', 'header', 'nav'}:
            self._finish_paragraph()
        if tag == "title":
            self.in_title = True
        if tag == 'h1':
            self.in_heading = True
        if tag == "meta":
            name = (fields.get("property") or fields.get("name") or fields.get("itemprop") or "").lower()
            if name in {
                "article:published_time", "pubdate", "publishdate", "pubtime", "datepublished", "og:release_date",
            }:
                self.published = fields.get("content") or self.published
            if name in {"og:title", "twitter:title"}:
                self.title = fields.get("content") or self.title
            if name in {"og:site_name", 'source'}:
                self.site = fields.get("content") or self.site
        blocked = (tag in {"script", "style", "nav", "footer", "header", "aside"}
                   or fields.get('id') == 'articleEdit'
                   or bool(set((fields.get('class') or '').split()) & {'adEditor', 'left_name'}))
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append((tag, blocked))
            self.skip += int(blocked)
            if self.container and self.body_depth is None:
                key, value = self.container
                if tag == 'div' and value in (fields.get(key) or '').split():
                    self.body_depth = len(self.stack)
                    self.body_seen = True
            if tag == 'span' and 'time' in (fields.get('class') or '').split():
                self.time_depth = len(self.stack)
        if tag == "p" and self._inside() and not self.skip:
            self.paragraph = []
        if self.container and self._inside() and not self.skip and len(self.media) < 64:
            video = tag == 'span' and 'pageVideo' in (fields.get('class') or '').split()
            link = fields.get('video_src') if video else (fields.get('data-original') or fields.get('src')) if tag == 'img' else None
            if link:
                def dimension(key):
                    value = fields.get(key) or ''
                    return int(value) if value.isdecimal() and len(value) <= 5 else 0
                self.media.append({'kind': 'video' if video else 'image', 'url': link,
                    'poster': fields.get('poster') or '', 'width': dimension('video_width' if video else 'width'),
                    'height': dimension('video_height' if video else 'height')})
                self.pending_media.append(len(self.media)-1)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == 'h1':
            self.in_heading = False
        if tag in {'p', 'div', 'section', 'article', 'aside', 'footer', 'header', 'nav'}:
            self._finish_paragraph()
        index = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if index is not None:
            self.skip -= sum(int(blocked) for _, blocked in self.stack[index:])
            del self.stack[index:]
            if self.body_depth is not None and len(self.stack) < self.body_depth:
                self.body_depth = None
            if self.time_depth is not None and len(self.stack) < self.time_depth:
                self.time_depth = None

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.in_heading:
            self.heading_parts.append(data)
        if self.time_depth is not None and re.fullmatch(r'\s*\d{2}:\d{2}:\d{2}\s*', data):
            self.clock = data.strip()
        if self._inside() and not self.skip and self.paragraph is not None:
            self.paragraph.append(data)

    def close(self):
        super().close()
        self._finish_paragraph()


def _read_report(story, now=None, *, public_request=None, allow_older=False):
    import ipaddress
    import socket
    import requests

    manual = public_request is not None
    if manual:
        public_ipv6 = ipaddress.ip_network("2000::/3")
    if story.get('content_origin') == 'article' or (story.get('content_origin') != 'feed'
            and not story.get("headline_only") and len(story.get("summary", "")) >= 1000):
        return story
    url = story["url"]
    try:
        for _ in range(5):
            if manual and (len(url) > 2048 or "\\" in url or any(c.isspace() or ord(c) < 32 for c in url)):
                return story
            parts = urlsplit(url)
            host = parts.hostname
            if (parts.scheme not in {"http", "https"} or not host or parts.username is not None
                    or parts.port not in {None, 80, 443}
                    or (manual and (parts.scheme != "https" or parts.port not in {None, 443}))
                    or host in {"news.google.com", "www.bing.com", "cn.bing.com", "bing.com"}
                    or _social_domain(host)):
                return story
            addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                           type=socket.SOCK_STREAM)
            ips = {ipaddress.ip_address(entry[4][0]) for entry in addresses}
            benchmark = ipaddress.ip_network("198.18.0.0/15")
            trusted_proxy_name = parts.scheme == "https" and host in _BENCHMARK_PUBLISHERS
            if not ips or any(ip.is_multicast or (not ip.is_global and not (trusted_proxy_name and ip in benchmark))
                              for ip in ips):
                return story
            if manual and any(ip.version == 6 and (ip not in public_ipv6 or ip.sixtofour) for ip in ips):
                return story
            connection = (public_request(parts, min(ips, key=lambda ip: (ip.version, int(ip)))) if manual
                          else requests.get(url, timeout=(4, 8), allow_redirects=False, stream=True))
            with connection as response:
                redirected = 300 <= response.status < 400 if manual else response.is_redirect
                if redirected:
                    url = urljoin(url, response.headers["Location"])
                    continue
                if manual:
                    if not 200 <= response.status < 300:
                        return story
                    blocks = response.stream(65536)
                    encoding = requests.utils.get_encoding_from_headers(response.headers)
                else:
                    response.raise_for_status()
                    blocks = response.iter_content(65536)
                    encoding = response.encoding
                if "text/html" not in response.headers.get("Content-Type", ""):
                    return story
                body = bytearray()
                for block in blocks:
                    body.extend(block)
                    if len(body) > 2_000_000:
                        return story
                encoding = encoding if encoding and encoding.lower() != "iso-8859-1" else "utf-8"
                raw = body.decode(encoding, errors="replace")
                container = ({'www.ithome.com': ('id', 'paragraph'),
                              'www.chinanews.com.cn': ('class', 'left_zw'),
                              'www.chinanews.com': ('class', 'left_zw'),
                              'www.news.cn': ('id', 'detail')}).get(host)
                if host == 'www.news.cn' and not re.fullmatch(r'/(?:[a-zA-Z0-9_-]+/)*\d{8}/[a-f0-9]{32}/c\.html', parts.path):
                    container = None
                parser = _ArticleText(container)
                parser.feed(raw)
                parser.close()
                if container and not parser.body_seen:
                    return story
                text = "\n".join(parser.parts)
                structured_body = re.search(r'"articleBody"\s*:\s*("(?:\\.|[^"\\])*")', raw)
                if structured_body and not container:
                    structured_text = plain_text(json.loads(structured_body[1]))[:7000]
                    if len(structured_text) > len(text):
                        text = structured_text
                media = [{**row, 'url': urljoin(url, row['url']),
                          'poster': urljoin(url, row['poster']) if row.get('poster') else ''} for row in parser.media]
                candidate_report = {**story, 'url': url, 'media_candidates': media}
                from core.source_media import candidates
                has_video = any(row['kind'] == 'video' for row in candidates(candidate_report))
                if len(text) >= (60 if has_video else MIN_REPORT_LENGTH) and (story.get('content_origin') == 'feed'
                        or story.get("headline_only") or len(text) > len(story.get("summary", ""))):
                    published = story["published"]
                    if not published:
                        structured = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw)
                        raw_stamp = parser.published or (structured[1] if structured else "")
                        if host == 'www.news.cn' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_stamp) and parser.clock:
                            raw_stamp += 'T' + parser.clock + '+08:00'
                        try:
                            stamp = _feed_date(raw_stamp)
                        except (TypeError, ValueError, OverflowError):
                            if not manual and not allow_older:
                                return story
                        else:
                            if not manual and (not now or stamp > now + dt.timedelta(minutes=10)
                                               or not allow_older and stamp < now - dt.timedelta(hours=48)):
                                return story
                            published = stamp.isoformat()
                    if manual or not story.get('title'):
                        story = {**story, "title": plain_text(parser.title or "".join(parser.title_parts or parser.heading_parts))[:120],
                                 "source": (plain_text(parser.site) or host)[:35]}
                    credits = re.findall(r'(?m)^(?:作者|记者|摄制)[：:]\s*(.{1,120})', text)
                    return {**story, "url": url, "published": published, "summary": text[:7000], "headline_only": False,
                            'content_origin': 'article', 'summary_truncated': len(text) > 7000,
                            **({'media_candidates': media} if media else {}),
                            **({'content_type': 'video_article'} if has_video else {}),
                            **({'author': '、'.join(credits[:3])} if credits and not story.get('author') else {})}
                return story
    except (requests.RequestException, OSError, ValueError, KeyError):
        pass
    return story


def supplement_hotspot_reports(output_dir, now):
    snapshot = read_intake(output_dir, now)
    matches = match_hotspots(snapshot, now, require_report=True)
    cutoff = (now - dt.timedelta(hours=4)).timestamp()
    with closing(_connect(output_dir)) as db:
        recent = {row[0] for row in db.execute("SELECT id FROM topic_search WHERE checked >= ?", (now.timestamp() - 45 * 60,))}
    candidates = []
    for platform, rows in matches.items():
        if sum(bool(row["article"]) for row in rows) >= 8 or not cutoff <= snapshot["ranks"][platform].get("published", 0) <= now.timestamp() + 600:
            continue
        safe = [row for row in rows[:32] if not row["article"] and story_key(row) not in recent
                and not any(word in row["title"] for word in RESTRICTED_TOPICS)]
        named = [row for row in safe[5:] if _anchors(row["title"])
                 or re.search(r"发布|上线|夺冠|展会|决赛|发布会|更新|首发|票房|开幕|赛事|模型", row["title"])]
        selected = [*safe[:5], *named[:3]]
        candidates.extend(selected)
    candidates = {row["title"]: row for row in candidates}
    with ThreadPoolExecutor(max_workers=5) as pool:
        reports = [story for batch in pool.map(lambda row: _search_reports(row, now), candidates.values()) for story in batch]
    if candidates:
        with closing(_connect(output_dir)) as db, db:
            db.executemany("INSERT OR REPLACE INTO topic_search VALUES (?, ?)",
                           ((story_key(row), now.timestamp()) for row in candidates.values()))
            db.executemany("INSERT INTO articles VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET story=excluded.story "
                           "WHERE json_extract(excluded.story, '$.headline_only') = 0 AND "
                           "(json_extract(articles.story, '$.headline_only') = 1 OR "
                           "length(json_extract(excluded.story, '$.summary')) > length(json_extract(articles.story, '$.summary')))",
                           ((story_key(story), now.timestamp(), json.dumps(story, ensure_ascii=False)) for story in reports))
    snapshot = read_intake(output_dir, now)
    selected = {}
    for rows in match_hotspots(snapshot, now).values():
        for row in rows[:24]:
            if row["article"]:
                selected[story_key(row["article"])] = row["article"]
    with ThreadPoolExecutor(max_workers=5) as pool:
        enriched = list(pool.map(_read_report, selected.values()))
    if enriched:
        with closing(_connect(output_dir)) as db, db:
            db.executemany("UPDATE articles SET story=? WHERE id=?",
                           ((json.dumps(story, ensure_ascii=False), story_key(story)) for story in enriched))


def category_for_story(story):
    content = (story["title"] + " " + story["summary"]).casefold()
    scores = {category: sum(word.casefold() in content for word in words) for category, words in KEYWORDS.items()}
    return max(scores, key=scores.get) if any(scores.values()) else "综合"
