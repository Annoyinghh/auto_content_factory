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


BROWSER_POST_PLATFORMS = frozenset({"douyin", "bilibili", "xiaohongshu"})
RESULTS = {"ready", "partial", "no_form", "transfer_error", "needs_check"}
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def prepare_browser_post(platform, package, *, timeout=120, open_panel=None):
    if platform not in BROWSER_POST_PLATFORMS:
        raise ValueError("该平台暂不支持浏览器填稿。")
    pictures = package.get("pictures")
    if pictures is not None:
        if platform != "xiaohongshu" or open_panel is None or not pictures:
            raise ValueError("请选择可投稿的小红书图文笔记。")
        assets = {f"/asset/{index}": Path(path) for index, path in enumerate(pictures)}
        types = IMAGE_TYPES
    else:
        assets = {"/video": Path(package["video"])}
        types = {".mp4": "video/mp4"}
    if any(asset.suffix.lower() not in types or not asset.is_file() or not asset.stat().st_size
           for asset in assets.values()):
        raise ValueError("素材文件缺失或格式不支持，请回到作品库核对。" if pictures is not None else
                         "找不到可上传的 MP4，请重新剪辑成片。")
    title = package["title"].strip()
    caption = package["caption"].strip()
    body = caption[len(title):].lstrip() if caption.startswith(title + "\n") else caption
    metadata = {"title": title, "body": body}
    if pictures is not None:
        metadata["images"] = [{"name": asset.name, "type": types[asset.suffix.lower()]} for asset in assets.values()]
    else:
        metadata["filename"] = assets["/video"].name
    job = json.dumps(metadata, ensure_ascii=False).encode()
    token = secrets.token_hex(32)
    result, connected, handed_off = None, False, set()

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
            nonlocal connected
            if not self.authorized():
                self.reply(403)
            elif self.path == "/job":
                connected = True
                self.reply(200, "application/json", len(job))
                self.wfile.write(job)
            elif self.path not in assets:
                self.reply(404)
            elif self.path in handed_off:
                self.reply(409)
            else:
                asset = assets[self.path]
                with asset.open("rb") as source:
                    self.reply(200, types[asset.suffix.lower()], asset.stat().st_size)
                    shutil.copyfileobj(source, self.wfile, 256 * 1024)
                handed_off.add(self.path)

        def do_POST(self):
            nonlocal result
            if not self.authorized():
                self.reply(403)
            elif not self.path.startswith("/result/") or self.path[8:] not in RESULTS:
                self.reply(404)
            elif self.path[8:] in {"ready", "partial"} and len(handed_off) != len(assets):
                self.reply(409)
            else:
                result = self.path[8:]
                self.reply(204)

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 0.25
        route = CREATOR_URLS[platform]
        if pictures is not None:
            route = route.replace("target=video", "target=image")
        url = f"{route}#acf-post={server.server_port}.{token}"
        panel = None
        if open_panel is not None:
            panel = open_panel(f"{url}&panel=1", title=title, body=body,
                               files=[str(asset.resolve()) for asset in assets.values()])
            if panel is None:
                raise RuntimeError("无法打开工作台投稿面板。")
        elif not webbrowser.open(url):
            raise RuntimeError("无法打开默认浏览器。")
        started = time.monotonic()
        while result is None and time.monotonic() - started < (timeout if panel or connected else min(timeout, 25)):
            if panel and panel.poll() is not None:
                return "needs_check" if handed_off else "closed"
            server.handle_request()
    return result or ("needs_check" if handed_off else "no_connection")
