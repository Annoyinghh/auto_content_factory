import json
import secrets
import unittest
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from unittest.mock import Mock, patch

from core.browser_post import prepare_browser_post
from core.workflow import edition_path, run_stage, write_json
from tests.test_daily import scratch_dir


class BrowserPostTests(unittest.TestCase):
    def test_note_handoff_requires_every_image_before_ready(self):
        with scratch_dir() as out:
            images = [out / "cover.jpg", out / "page.png"]
            for image in images:
                image.write_bytes(image.name.encode())
            requests = []
            panel = Mock(poll=Mock(return_value=None))

            def launch(url, **draft):
                parts = urlsplit(url)
                self.assertIn("target=image", parts.query)
                port, token = parts.fragment.removeprefix("acf-post=").removesuffix("&panel=1").split(".")
                base = f"http://127.0.0.1:{port}"
                headers = {"X-ACF-Token": token}

                def read(path, method="GET"):
                    with urlopen(Request(base + path, headers=headers, method=method), timeout=2) as response:
                        return response.read()

                def receive():
                    try:
                        requests.append(json.loads(read("/job"))["images"])
                        requests.append(read("/asset/0"))
                        with self.assertRaises(HTTPError) as premature:
                            read("/result/ready", "POST")
                        requests.append(premature.exception.code)
                        requests.append(read("/asset/1"))
                        read("/result/ready", "POST")
                    except Exception as error:
                        requests.append(error)

                thread = Thread(target=receive, daemon=True)
                threads.append(thread)
                thread.start()
                return panel

            threads = []
            outcome = prepare_browser_post("xiaohongshu", {"title": "图文", "caption": "正文", "pictures": images},
                                           open_panel=launch, timeout=4)
            threads[0].join()
            self.assertEqual(requests, [[{"name": "cover.jpg", "type": "image/jpeg"},
                                         {"name": "page.png", "type": "image/png"}], b"cover.jpg", 409, b"page.png"])
            self.assertEqual(outcome, "ready")

    def test_douyin_panel_does_not_open_a_browser_when_closed_before_login(self):
        with scratch_dir() as out, patch("core.browser_post.webbrowser.open") as browser:
            video = out / "video.mp4"
            video.write_bytes(b"reviewed video")
            panel = Mock(return_value=Mock(poll=Mock(return_value=0)))
            package = {"video": str(video), "title": "作品标题", "caption": "配文"}

            result = prepare_browser_post("douyin", package, open_panel=panel, timeout=1)

            self.assertEqual(result, "closed")
            browser.assert_not_called()
            url = urlsplit(panel.call_args.args[0])
            self.assertEqual(url.hostname, "creator.douyin.com")
            self.assertRegex(url.fragment, r"^acf-post=\d{1,5}\.[a-f0-9]{64}&panel=1$")

    def test_browser_draft_uses_the_current_reviewed_video_without_api_authorization(self):
        day = "2026-11-08"
        with scratch_dir() as out, patch("core.browser_post.prepare_browser_post", return_value="ready") as prepare:
            edition = edition_path(out, day)
            edition.parent.mkdir(parents=True)
            video = out / "视频.mp4"
            video.write_bytes(b"reviewed video")
            package = {"video": str(video), "title": "已审作品", "caption": "稿件配文"}
            write_json(edition, {"items": {"douyin": {"package": package, "edit_revision": "ok", "package_revision": "ok"}}})

            result = run_stage("browser_post", day, out, "douyin", {"video": str(video)})

            self.assertEqual(result, "ready")
            prepare.assert_called_once_with("douyin", package)
            self.assertFalse((out / "submitted.txt").exists())

    def test_browser_draft_rejects_a_held_or_already_submitted_video(self):
        day = "2026-11-08"
        with scratch_dir() as out, patch("core.browser_post.prepare_browser_post") as prepare:
            edition = edition_path(out, day)
            edition.parent.mkdir(parents=True)
            video = str(out / "video.mp4")
            item = {"package": {"video": video}, "held_video": video}
            write_json(edition, {"items": {"bilibili": item}})
            with self.assertRaisesRegex(RuntimeError, "暂缓"):
                run_stage("browser_post", day, out, "bilibili", {"video": video})
            item.pop("held_video")
            write_json(edition, {"items": {"bilibili": item}})
            (out / "submitted.txt").write_text("BV-approved", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "已提交"):
                run_stage("browser_post", day, out, "bilibili", {"video": video})
            prepare.assert_not_called()

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
