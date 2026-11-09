"""Create a daily editorial edition from published business and technology feeds."""

import datetime as dt
import hashlib
import html
import os
import re
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from config import PROFILES


FEEDS = ("https://www.36kr.com/feed", "https://www.ifanr.com/feed", "https://www.ithome.com/rss/",
         "https://www.chinanews.com.cn/rss/scroll-news.xml", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml")
KEYWORDS = {
    "消费": ("消费", "订阅", "会员", "涨价", "降价", "零售", "电商", "价格", "品牌", "购物", "出行"),
    "职场": ("职场", "招聘", "求职", "办公", "实习", "就业", "远程", "工作流", "创业", "自由职业"),
    "科技": ("芯片", "人工智能", "大模型", "生成式", "机器人", "开源", "智能手机", "笔记本", "AI"),
    "商业": ("融资", "财报", "并购", "供应链", "商业模式", "营收", "上市", "出海", "开店", "工厂"),
}
RESTRICTED_TOPICS = ("地震", "枪击", "恐袭", "伤亡", "遇难", "疫情", "选举", "战争", "军队", "荐股", "疗效")
def plain_text(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(value))).split())


def web_url(value):
    parsed = urlsplit(value)
    return parsed.scheme in ("https", "http") and bool(parsed.hostname) and not parsed.username


def _feed_date(value):
    try:
        stamp = dt.datetime.fromisoformat(value)
    except ValueError:
        stamp = parsedate_to_datetime(value)
    return stamp.replace(tzinfo=stamp.tzinfo or dt.timezone.utc)


def parse_feed(xml, now, *, search_results=False, allow_undated=False):
    if isinstance(xml, bytes) and b"<!DOCTYPE" in xml.upper():
        raise ValueError("资讯源不应包含 XML 文档类型声明")
    root = ElementTree.fromstring(xml)
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    channel = root.find("channel")
    channel = root if channel is None else channel
    records = []
    for entry in [*channel.findall("item"), *channel.findall("entry")][:60]:
        title = plain_text(entry.findtext("title", ""))
        summary = plain_text(entry.findtext("description") or entry.findtext("summary") or entry.findtext("content", ""))
        if search_results and (len(summary) < 12 or summary == title):
            summary = f"公开报道标题：{title}。"
        link = next(
            (node.get("href") or node.text for node in entry.findall("link")
             if node.get("rel", "alternate") == "alternate" and (node.get("href") or node.text)),
            "",
        ).strip()
        if not title or len(summary) < 12 or summary == title or not web_url(link):
            continue
        if any(word in title for word in RESTRICTED_TOPICS):
            continue
        published = entry.findtext("pubDate") or entry.findtext("published") or entry.findtext("updated", "")
        try:
            stamp = _feed_date(published)
        except (TypeError, ValueError, OverflowError):
            if not allow_undated or published:
                continue
            stamp = None
        if stamp and not now - dt.timedelta(hours=48) <= stamp <= now + dt.timedelta(minutes=10):
            continue
        parsed = urlsplit(link)
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query) if not key.startswith("utm_")])
        link = urlunsplit(parsed._replace(query=query, fragment=""))
        records.append({
            "title": title[:120],
            "summary": summary[:750],
            "url": link,
            "source": plain_text((entry.findtext("source") or entry.findtext("Source") or parsed.hostname)
                                 if search_results else channel.findtext("title", ""))[:35] or parsed.hostname,
            "published": stamp.astimezone(dt.timezone.utc).isoformat() if stamp else "",
        })
    return records


def collect_news(now):
    import requests

    sources = os.getenv("CONTENT_NEWS_FEEDS", "").split()
    records, errors = [], []
    for url in sources or FEEDS:
        try:
            if not web_url(url):
                raise ValueError("只接受 HTTP(S) RSS 地址")
            with requests.get(url, stream=True, timeout=(8, 15)) as response:
                response.raise_for_status()
                data = bytearray()
                for block in response.iter_content(65536):
                    data.extend(block)
                    if len(data) > 4_000_000:
                        raise ValueError("RSS 超出 4 MB")
                records.extend(parse_feed(bytes(data), now))
        except (ValueError, ElementTree.ParseError, requests.RequestException) as error:
            errors.append(f"{urlsplit(url).hostname or '资讯源'}：{type(error).__name__}")
    return records, errors


def story_key(story):
    title = re.sub(r"[\W_]+", "", story["title"].casefold())
    return hashlib.sha256(title.encode()).hexdigest()[:20]


def select_stories(stories, seen):
    unique = {}
    for story in sorted(stories, key=lambda entry: entry["published"], reverse=True):
        unique.setdefault(story_key(story), story)

    candidates = []
    for key, story in unique.items():
        title, summary = story["title"].casefold(), story["summary"].casefold()
        scores = {
            category: sum(3 if word.casefold() in title else int(word.casefold() in summary) for word in words)
            for category, words in KEYWORDS.items()
        }
        category = max(scores, key=scores.get)
        if scores[category]:
            candidates.append((key, {**story, "category": category}, scores[category]))

    chosen = {}
    for platform, profile in PROFILES.items():
        ranked = sorted(
            candidates,
            key=lambda item: (profile["priority"][item[1]["category"]], item[2], item[1]["published"]),
            reverse=True,
        )
        for key, story, _ in ranked:
            if (platform, key) not in seen:
                chosen[platform] = story
                break
    return chosen
