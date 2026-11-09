import json
import secrets
import unittest
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

from core.browser_post import prepare_browser_post


class BrowserPostTests(unittest.TestCase):
    def test_one_video_is_exposed_only_to_the_current_browser_job(self):
        video = Path(f"browser-post-test-{secrets.token_hex(6)}.mp4")
        video.write_bytes(b"video fixture")
        self.addCleanup(video.unlink)
        package = {"video": str(video), "title": "作品标题", "caption": "作品标题\n\n正文\n\n素材署名"}

        def browser(url):
            parts = urlsplit(url)
            self.assertEqual(parts.hostname, "member.bilibili.com")
            port, token = parts.fragment.removeprefix("acf-post=").split(".")
            self.browser = (f"http://127.0.0.1:{port}", token)
            return True

        def connect():
            base, token = self.browser
            with self.assertRaises(HTTPError) as rejected:
                urlopen(base + "/video", timeout=1)
            self.assertEqual(rejected.exception.code, 403)
            headers = {"X-ACF-Token": token}
            with urlopen(Request(base + "/job", headers=headers), timeout=1) as response:
                self.assertEqual(json.load(response)["body"], "正文\n\n素材署名")
            with urlopen(Request(base + "/video", headers=headers), timeout=1) as response:
                self.assertEqual(response.read(), b"video fixture")
            with self.assertRaises(HTTPError) as repeated:
                urlopen(Request(base + "/video", headers=headers), timeout=1)
            self.assertEqual(repeated.exception.code, 409)
            with urlopen(Request(base + "/result/ready", headers=headers, method="POST"), timeout=1) as response:
                self.assertEqual(response.status, 204)

        failure = []
        thread = None

        def start_browser(url):
            nonlocal thread
            browser(url)

            def run():
                try:
                    connect()
                except Exception as exc:
                    failure.append(exc)

            thread = Thread(target=run, daemon=True)
            thread.start()
            return True

        with patch("core.browser_post.webbrowser.open", side_effect=start_browser):
            outcome = prepare_browser_post("bilibili", package, timeout=3)
        thread.join()
        if failure:
            raise failure[0]
        self.assertEqual(outcome, "ready")
