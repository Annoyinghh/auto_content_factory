"""Original browser pixels from a public government notice, with capture provenance."""

import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image


class OfficialCaptureError(ValueError):
    """An eligible official page could not produce a verifiable screenshot."""


def official_notice_url(value):
    """Return a canonical HTTPS government URL; reject credentials and all ports."""
    if not isinstance(value, str) or len(value) > 4000 or re.search(r"[\s\\\x00-\x1f\x7f]", value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        if (parts.scheme != "https" or parts.netloc.casefold() != host
                or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*gov\.cn", host)
                or len(host) > 253):
            return None
        return urlunsplit(("https", host, parts.path or "/", parts.query, ""))
    except (ValueError, TypeError):
        return None


def article_capture_url(value):
    """Known publisher article routes; these are never labelled official notices."""
    if not isinstance(value, str) or not re.fullmatch(r'https://www\.ithome\.com/\d+/\d+/\d+\.htm', value):
        return None
    return value


def _reader_path():
    from config import BASE_DIR

    if getattr(sys, "frozen", False):
        return BASE_DIR / "post_panel" / "SourceReader.exe"
    current = BASE_DIR / "build" / "notice-capture-current" / "SourceReader.exe"
    return current if current.is_file() else BASE_DIR / "build" / "post_panel" / "SourceReader.exe"


def _image_details(path):
    if not path.is_file() or not 0 < path.stat().st_size <= 16_000_000:
        raise OfficialCaptureError("官方公告截图没有生成或文件大小异常。")
    with Image.open(path, formats=("PNG",)) as image:
        width, height = image.size
        if not 320 <= width <= 1600 or not 200 <= height <= 1800:
            raise OfficialCaptureError("官方公告截图尺寸异常，未加入素材库。")
        image.verify()
    return width, height, hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_capture(data, url, *, article_page=False):
    if (not isinstance(data, dict) or data.get("capture_method") != "webview2-original-page"
            or (article_capture_url if article_page else official_notice_url)(data.get("url")) != url or data.get("capture_url") != url
            or not isinstance(data.get("title"), str) or not 4 <= len(data["title"].strip()) <= 500
            or not isinstance(data.get("summary"), str) or not 120 <= len(data["summary"]) <= 7000
            or not isinstance(data.get("published"), str) or not data["published"]):
        return False
    try:
        captured = dt.datetime.fromisoformat(data["captured_at"].replace("Z", "+00:00"))
        return captured.utcoffset() is not None
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _asset(report, url, key, image, capture, dimensions):
    from core.daily_news import story_key

    width, height, digest = dimensions
    official = bool(official_notice_url(url))
    label = '官方公告原网页截图' if official else '报道原网页截图'
    return {
        "origin": "article", "kind": "image", "capture_kind": "official_notice" if official else "article_page",
        "caption_label": '官方原网页' if official else '报道原网页',
        "source_media_id": key, "source_report_id": story_key(report),
        "page_url": url, "source_page": url, "path": str(image), "preview": str(image),
        "title": capture["title"][:300] + " · " + label,
        "context": capture["summary"][:350], "source_title": report["title"],
        "source_summary": str(report.get("summary") or capture["summary"])[:6000],
        "source_published": report.get("published") or capture["published"],
        "published": capture["published"], "author": capture.get("source") or urlsplit(url).hostname,
        "license": label + " · 使用范围待核对", "license_url": url, "rights": "unverified",
        "width": width, "height": height, "capture_url": url, "captured_at": capture["captured_at"],
        "collected_at": capture["captured_at"], "capture_method": capture["capture_method"],
        "capture_sha256": digest, "capture": capture,
        "resolution_note": "原网页像素 · 标题与正文节选，完整内容见来源网页",
    }


def capture_official_notice(report, output_dir, *, article_page=False):
    """Cache a real notice screenshot, return None for other URLs, raise on capture failure.

    Only the report's already-read URL is opened. The helper never rewrites article
    text or layout, and captures one original-page crop no larger than 1600×1800.
    """
    url = (article_capture_url if article_page else official_notice_url)(report.get("url"))
    if not url:
        return None
    from core.workflow import _lock, write_json

    output = Path(output_dir).resolve()
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    folder = output / "material-library" / ("notice-" + key)
    folder.mkdir(parents=True, exist_ok=True)
    image, manifest = folder / "original.png", folder / "material.json"
    with _lock(folder / ".writes.sqlite3", timeout=60):
        try:
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            capture = saved.get("capture")
            if (_valid_capture(capture, url, article_page=article_page) and saved.get("source_page") == url
                    and saved.get("capture_url") == url):
                dimensions = _image_details(image)
                if saved.get("capture_sha256") == dimensions[2]:
                    asset = _asset(report, url, key, image, capture, dimensions)
                    write_json(manifest, asset)
                    return asset
        except (OSError, ValueError, TypeError, AttributeError, Image.DecompressionBombError):
            pass
        helper = _reader_path()
        if not helper.is_file():
            raise OfficialCaptureError("官方公告截图失败：缺少网页读取组件 SourceReader.exe，请重新构建桌面组件。")
        profile = output / ".official-source-browser"
        profile.mkdir(parents=True, exist_ok=True)
        temporary = folder / (".capture-" + uuid.uuid4().hex + ".png")
        try:
            with _lock(output / "official-source-browser.sqlite3", timeout=60):
                result = subprocess.run([str(helper), url, str(profile), "--capture-article" if article_page else "--capture", str(temporary)],
                    capture_output=True, timeout=50, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or len(result.stdout) > 100_000:
                detail = result.stderr.decode("utf-8", errors="replace").strip()[:250]
                raise OfficialCaptureError("官方公告截图失败：" + (detail or "网页未成功读取，请检查原文是否可公开访问。"))
            capture = json.loads(result.stdout.decode("utf-8-sig"))
            if not _valid_capture(capture, url, article_page=article_page):
                raise OfficialCaptureError("官方公告截图失败：原文地址、标题、日期或正文未通过核验。")
            # The subprocess chooses no storage path: only this exact generated file is accepted.
            dimensions = _image_details(temporary)
            temporary.replace(image)
            asset = _asset(report, url, key, image, capture, dimensions)
            write_json(manifest, asset)
            return asset
        except OfficialCaptureError:
            raise
        except (OSError, ValueError, subprocess.TimeoutExpired, Image.DecompressionBombError) as error:
            raise OfficialCaptureError("官方公告截图失败：" + type(error).__name__ + "，未加入素材库。") from error
        finally:
            temporary.unlink(missing_ok=True)
