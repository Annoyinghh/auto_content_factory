"""Read user-supplied public HTTPS article links as editorial candidates."""

import hashlib
from contextlib import contextmanager
from urllib.parse import urldefrag, urlsplit

from core.intake import _read_report, has_report


@contextmanager
def _pinned_response(parts, address):
    import urllib3

    host = parts.hostname.rstrip(".").encode("idna").decode("ascii")
    authority = f"[{host}]" if ":" in host else host
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    try:
        # The connection must use the vetted IP; TLS still authenticates the original host.
        with urllib3.HTTPSConnectionPool(
            str(address), server_hostname=host, assert_hostname=host, cert_reqs="CERT_REQUIRED",
            timeout=urllib3.Timeout(connect=4, read=8),
        ) as pool, pool.urlopen(
            "GET", target, headers={"Host": authority}, redirect=False, retries=False, preload_content=False,
        ) as response:
            yield response
    except urllib3.exceptions.HTTPError as error:
        raise OSError("Unable to fetch public article") from error


def editorial_link_id(url):
    return hashlib.sha256(("editorial-link:" + urldefrag(url).url).encode()).hexdigest()[:20]


def read_editorial_link(url, now, *, output_dir=None):
    if not isinstance(url, str) or not 1 <= len(url.strip()) <= 2048:
        raise ValueError("请输入完整的 HTTPS 公开报道链接。")
    try:
        url = urldefrag(url.strip()).url
    except ValueError as error:
        raise ValueError("请输入完整的 HTTPS 公开报道链接。") from error
    report = None
    if urlsplit(url).hostname in {"www.sohu.com", "m.sohu.com"}:
        from core.browser_source import read_public_article
        report = read_public_article(url, output_dir)
    if not report:
        report = _read_report({"url": url, "title": "", "source": "", "published": "",
                              "headline_only": True}, now, public_request=_pinned_response)
    if not report["title"] or not has_report(report):
        raise ValueError("无法读取公开报道正文。请使用可直接打开的 HTTPS 原文链接。")
    link = urldefrag(report["url"]).url
    if output_dir is not None:
        from contextlib import closing
        from core.intake import _connect
        from core.daily_news import story_key
        import json
        with closing(_connect(output_dir)) as db, db:
            db.execute("INSERT OR REPLACE INTO articles VALUES (?, ?, ?)",
                       (story_key(report), now.timestamp(), json.dumps(report, ensure_ascii=False)))
    return {**report, "id": editorial_link_id(link), "url": link, "available": True}
