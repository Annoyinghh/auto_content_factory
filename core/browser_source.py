"""Public-page browser fallback, without service accounts or exported cookies."""

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urldefrag


def read_public_article(url, output_dir=None):
    from config import BASE_DIR, OUTPUT_DIR
    from core.workflow import _lock

    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in {"www.sohu.com", "m.sohu.com"}
            or parts.port not in {None, 443} or parts.username or parts.password
            or not parts.path.startswith("/a/")):
        return None
    helper = (BASE_DIR if getattr(sys, "frozen", False) else BASE_DIR / "build") / "post_panel" / "SourceReader.exe"
    if not helper.is_file():
        return None
    output = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    profile = output / ".source-browser"
    profile.mkdir(parents=True, exist_ok=True)
    with _lock(output / "source-browser.sqlite3", timeout=40):
        try:
            result = subprocess.run([str(helper), url, str(profile.resolve())], capture_output=True, timeout=45,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode or len(result.stdout) > 100_000:
                return None
            article = json.loads(result.stdout.decode("utf-8-sig"))
            if (not isinstance(article, dict) or urldefrag(article.get("url", "")).url != urldefrag(url).url
                    or not isinstance(article.get("title"), str) or not article["title"].strip()
                    or not isinstance(article.get("summary"), str) or not 300 <= len(article["summary"]) <= 7000):
                return None
            return {**article, "title": article["title"][:120], "source": "搜狐原文 · 浏览器读取",
                    "source_type": "reference", "headline_only": False}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
