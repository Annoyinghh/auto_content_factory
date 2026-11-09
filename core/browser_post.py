"""Hand one release to the signed-in browser via a short-lived loopback request."""

import json
import re
import secrets
import shutil
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from config import CREATOR_URLS


RESULTS = {"ready", "partial", "no_form", "transfer_error", "needs_check"}


def prepare_browser_post(platform, package, *, timeout=120):
    if platform not in {"douyin", "bilibili"}:
        raise ValueError("该平台暂不支持浏览器填稿。")
    video = Path(package["video"])
    if not video.is_file() or video.suffix.lower() != ".mp4" or not video.stat().st_size:
        raise ValueError("找不到可上传的 MP4，请重新剪辑成片。")
    title = package["title"].strip()
    caption = package["caption"].strip()
    body = caption[len(title):].lstrip() if caption.startswith(title + "\n") else caption
    job = json.dumps({"title": title, "body": body, "filename": video.name}, ensure_ascii=False).encode()
    token = secrets.token_hex(32)
    result, connected, handed_off = None, False, False

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *args):
            pass

        def authorized(self):
            origin = self.headers.get("Origin")
            return (
                self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                and (origin is None or re.fullmatch(r"chrome-extension://[a-p]{32}", origin))
                and secrets.compare_digest(self.headers.get("X-ACF-Token", "").encode(), token.encode())
            )

        def reply(self, code, content_type=None, length=0):
            self.send_response(code)
            self.send_header("Cache-Control", "no-store")
            if content_type:
                self.send_header("Content-Type", content_type)
            if code != 204:
                self.send_header("Content-Length", str(length))
            self.end_headers()

        def do_GET(self):
            nonlocal connected, handed_off
            if not self.authorized():
                self.reply(403)
            elif self.path == "/job":
                connected = True
                self.reply(200, "application/json", len(job))
                self.wfile.write(job)
            elif self.path != "/video":
                self.reply(404)
            elif handed_off:
                self.reply(409)
            else:
                with video.open("rb") as source:
                    self.reply(200, "video/mp4", video.stat().st_size)
                    shutil.copyfileobj(source, self.wfile, 256 * 1024)
                handed_off = True

        def do_POST(self):
            nonlocal result
            if not self.authorized():
                self.reply(403)
            elif not self.path.startswith("/result/") or self.path[8:] not in RESULTS:
                self.reply(404)
            elif self.path[8:] in {"ready", "partial"} and not handed_off:
                self.reply(409)
            else:
                result = self.path[8:]
                self.reply(204)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 0.25
        if not webbrowser.open(f"{CREATOR_URLS[platform]}#acf-post={server.server_port}.{token}"):
            raise RuntimeError("无法打开默认浏览器。")
        started = time.monotonic()
        while result is None and time.monotonic() - started < (timeout if connected else min(timeout, 25)):
            server.handle_request()
    return result or ("needs_check" if handed_off else "no_connection")
