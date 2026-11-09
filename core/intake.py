"""Rolling collection database and observed platform rankings."""

import datetime as dt
import json
import re
import sqlite3
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
        db.execute("CREATE TABLE IF NOT EXISTS intake_state (id INTEGER PRIMARY KEY, checked REAL, errors TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS topic_search (id TEXT PRIMARY KEY, checked REAL)")
    return db


def read_intake(output_dir, now=None):
    now = now or dt.datetime.now().astimezone()
    if not (Path(output_dir) / "collection.sqlite3").is_file():
        return {"articles": [], "ranks": {}, "checked": None, "errors": []}
    cutoff = (now - dt.timedelta(days=RETENTION_DAYS)).timestamp()
    with closing(_connect(output_dir)) as db:
        articles = [json.loads(row[0]) for row in db.execute("SELECT story FROM articles WHERE collected >= ?", (cutoff,))]
        ranks = {platform: {**(json.loads(data) if data else {}), "error": error}
                 for platform, data, error in db.execute("SELECT platform, snapshot, error FROM ranks")}
        checked, errors = db.execute("SELECT checked, errors FROM intake_state WHERE id = 1").fetchone() or (None, "[]")
    return {"articles": sorted(articles, key=lambda row: row["published"], reverse=True), "ranks": ranks,
            "checked": checked, "errors": json.loads(errors)}


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
                changes[platform] = (json.dumps(result.result(), ensure_ascii=False), "")
            except (ValueError, requests.RequestException) as error:
                changes[platform] = (None, f"榜单更新失败（{type(error).__name__}）")

    cutoff = (now - dt.timedelta(days=RETENTION_DAYS)).timestamp()
    with closing(_connect(output_dir)) as db, db:
        db.execute("DELETE FROM articles WHERE collected < ?", (cutoff,))
        db.execute("DELETE FROM topic_search WHERE checked < ?", (cutoff,))
        db.executemany("INSERT OR IGNORE INTO articles VALUES (?, ?, ?)",
                       ((story_key(story), now.timestamp(), json.dumps(story, ensure_ascii=False)) for story in stories))
        db.executemany(
            "INSERT INTO ranks VALUES (?, ?, ?) ON CONFLICT(platform) DO UPDATE SET "
            "snapshot=COALESCE(excluded.snapshot, ranks.snapshot), error=excluded.error",
            ((platform, *values) for platform, values in changes.items()),
        )
        db.execute("UPDATE ranks SET snapshot=NULL WHERE json_extract(snapshot, '$.published') < ?", (cutoff,))
        db.execute("INSERT OR REPLACE INTO intake_state VALUES (1, ?, ?)", (now.timestamp(), json.dumps(errors)))
    return read_intake(output_dir, now)


def _signature(text):
    text = re.sub(r"(?<=[A-Za-z0-9])\s*(?:对战|对阵|迎战|vs\.?)\s*(?=[A-Za-z0-9])", "vs", text, flags=re.I)
    compact = re.sub(r"[\W_]+", "", text.casefold())
    return compact, {compact[i:i + 3] for i in range(len(compact) - 2)}


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
    return bool(story and not story.get("headline_only") and len(story.get("summary", "")) >= MIN_REPORT_LENGTH)


def _match_score(name, terms, anchors, headline, tokens):
    if not headline:
        return 0
    common = len(terms & tokens)
    if name in headline or (len(headline) >= 4 and headline in name):
        return 2
    if 6 <= len(name) <= 12 and re.search(re.escape(name[:4]) + r".{0,14}" + re.escape(name[-2:]), headline):
        return 1.75
    if common >= 3 and common / len(terms) >= 0.5:
        return 1 + common / len(terms)
    if common and len(name) >= 10:
        pairs = {name[i:i + 2] for i in range(len(name) - 1)}
        shared = sum(pair in headline for pair in pairs)
        if shared >= 5 and shared / len(pairs) >= 0.4:
            return 1 + shared / len(pairs)
    return max((len(anchor) / len(name) for anchor in anchors if anchor in headline), default=0)


def match_hotspots(snapshot, now=None, *, require_report=False):
    now = now or dt.datetime.now().astimezone()
    cutoff = (now - dt.timedelta(hours=4)).timestamp()
    articles = [(story, *_signature(story["title"])) for story in snapshot["articles"]
                if not require_report or has_report(story)]
    matched = {}
    for platform, board in snapshot["ranks"].items():
        rows = []
        fresh = cutoff <= board.get("published", 0) <= now.timestamp() + 600
        for row in board.get("entries", []):
            name, terms = _signature(row["title"])
            anchors = _anchors(row["title"])
            best, best_score = None, 0
            if fresh and len(name) >= 4:
                for article, headline, tokens in articles:
                    score = _match_score(name, terms, anchors, headline, tokens)
                    if score > best_score or (score and score == best_score and has_report(article) and not has_report(best)):
                        best, best_score = article, score
            rows.append({**row, "article": best})
        matched[platform] = rows
    return matched


def _search_reports(row, now):
    import requests

    title = row["title"]
    # Search the named work or location instead of a slogan wrapped around it.
    work = re.match(r"《([^》]{3,30})》", title)
    anchors = _anchors(title)
    query = work[1] if work else (max(anchors, key=len) if anchors else title[:72])
    sources = (
        ("https://www.bing.com/search", {"q": query, "format": "rss"}),
        ("https://www.bing.com/news/search", {"q": query, "format": "rss", "cc": "us", "setlang": "zh-hans"}),
        ("https://news.google.com/rss/search", {"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"}),
    )
    name, terms = _signature(title)
    if len(name) < 4:
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
                reports = parse_feed(bytes(body), now, search_results=True, allow_undated=endpoint.endswith(".com/search"))
        except (requests.RequestException, ValueError, ElementTree.ParseError):
            continue
        ranked = sorted(((_match_score(name, terms, anchors, *_signature(report["title"])), i, report)
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
            usable.append(report)
        if endpoint.startswith("https://www.bing.com/"):
            for report in usable:
                verified = _read_report(report, now)
                if has_report(verified):
                    return [verified]
        elif usable:
            return usable[:3]
    return []


def _social_domain(host):
    return any(host == domain or host.endswith("." + domain) for domain in _SOCIAL_DOMAINS)


class _ArticleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.paragraph = None
        self.parts = []
        self.published = ""

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            fields = dict(attrs)
            if (fields.get("property") or fields.get("name") or fields.get("itemprop") or "").lower() in {
                "article:published_time", "pubdate", "publishdate", "pubtime", "datepublished", "og:release_date",
            }:
                self.published = fields.get("content") or self.published
        if tag in {"script", "style", "nav", "footer", "header", "aside"}:
            self.skip += 1
        if tag == "p" and not self.skip:
            self.paragraph = []

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "header", "aside"}:
            self.skip = max(0, self.skip - 1)
        if tag == "p" and self.paragraph is not None:
            paragraph = " ".join("".join(self.paragraph).split())
            if len(paragraph) >= 35 and paragraph not in self.parts:
                self.parts.append(paragraph)
            self.paragraph = None

    def handle_data(self, data):
        if not self.skip and self.paragraph is not None:
            self.paragraph.append(data)


def _read_report(story, now=None):
    import ipaddress
    import socket
    import requests

    if not story.get("headline_only") and len(story.get("summary", "")) >= 1000:
        return story
    url = story["url"]
    try:
        for _ in range(5):
            parts = urlsplit(url)
            host = parts.hostname
            if (parts.scheme not in {"http", "https"} or not host or parts.username or parts.port not in {None, 80, 443}
                    or host in {"news.google.com", "www.bing.com", "cn.bing.com", "bing.com"}
                    or _social_domain(host)):
                return story
            addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80),
                                           type=socket.SOCK_STREAM)
            ips = {ipaddress.ip_address(entry[4][0]) for entry in addresses}
            benchmark = ipaddress.ip_network("198.18.0.0/15")
            trusted_proxy_name = parts.scheme == "https" and host in _BENCHMARK_PUBLISHERS
            if not ips or any(not ip.is_global and not (trusted_proxy_name and ip in benchmark) for ip in ips):
                return story
            with requests.get(url, timeout=(4, 8), allow_redirects=False, stream=True) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                response.raise_for_status()
                if "text/html" not in response.headers.get("Content-Type", ""):
                    return story
                body = bytearray()
                for block in response.iter_content(65536):
                    body.extend(block)
                    if len(body) > 2_000_000:
                        return story
                encoding = response.encoding if response.encoding and response.encoding.lower() != "iso-8859-1" else "utf-8"
                raw = body.decode(encoding, errors="replace")
                parser = _ArticleText()
                parser.feed(raw)
                text = "\n".join(parser.parts)[:7000]
                structured_body = re.search(r'"articleBody"\s*:\s*("(?:\\.|[^"\\])*")', raw)
                if structured_body:
                    structured_text = plain_text(json.loads(structured_body[1]))[:7000]
                    if len(structured_text) > len(text):
                        text = structured_text
                if len(text) >= MIN_REPORT_LENGTH and (story.get("headline_only") or len(text) > len(story.get("summary", ""))):
                    published = story["published"]
                    if not published:
                        structured = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw)
                        stamp = _feed_date(parser.published or (structured[1] if structured else ""))
                        if not now or not now - dt.timedelta(hours=48) <= stamp <= now + dt.timedelta(minutes=10):
                            return story
                        published = stamp.isoformat()
                    return {**story, "url": url, "published": published, "summary": text, "headline_only": False}
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
