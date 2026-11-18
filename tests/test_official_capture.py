import hashlib
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from core.daily_news import story_key
from core.official_capture import OfficialCaptureError, capture_official_notice, official_notice_url, article_capture_url
from tests.test_works_library import scratch_dir


class OfficialCaptureTests(unittest.TestCase):
    def setUp(self):
        self.url = "https://www.gov.cn/zhengce/content/202611/notice.htm"
        self.report = {"url": self.url, "title": "关于节假日安排的通知", "summary": "已读取的公告正文。" * 30,
                       "published": "2026-11-12"}
        self.capture = {"url": self.url, "capture_url": self.url, "title": self.report["title"],
                        "summary": self.report["summary"], "published": "2026-11-12", "source": "中国政府网",
                        "captured_at": "2026-11-16T12:00:00Z", "capture_method": "webview2-original-page"}

    def reader(self, args, **kwargs):
        self.assertEqual(args[1], self.url)
        self.assertEqual(args[3], "--capture")
        # This synthetic PNG exercises file validation only; it is never used by a live capture.
        Image.new("RGB", (1000, 1200), "white").save(args[4])
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.capture).encode(), stderr=b"")

    def test_exact_official_https_hosts_only(self):
        for url in ("https://gov.cn/a", "https://www.gov.cn/a", "https://news.sz.gov.cn/a"):
            self.assertEqual(official_notice_url(url), url)
        for url in ("http://www.gov.cn/a", "https://gov.cn.evil.test/a", "https://evilgov.cn/a",
                    "https://www.gov.cn@evil.test/a", "https://user@www.gov.cn/a", "https://www.gov.cn:443/a",
                    "https://www.gov.cn:8443/a", "https://127.0.0.1/a", "https://[::1]/a",
                    "https://www.gov.cn./a", "https://www.gov.cn\\@evil.test/a", "https://www.gov.cn:/a",
                    "https://www.gov.cn\n/a", " https://www.gov.cn/a", "https://-bad.gov.cn/a", None):
            with self.subTest(url=url):
                self.assertIsNone(official_notice_url(url))
        self.assertEqual(official_notice_url("https://WWW.GOV.CN/a#section"), "https://www.gov.cn/a")

    def test_nonofficial_report_does_not_start_browser(self):
        with patch("core.official_capture.subprocess.run") as run:
            self.assertIsNone(capture_official_notice({"url": "https://news.example/a"}, "unused"))
            run.assert_not_called()

    def test_publisher_capture_is_explicit_and_does_not_claim_official_status(self):
        url = 'https://www.ithome.com/1/028/065.htm'
        self.assertEqual(article_capture_url(url),url)
        for invalid in ('https://www.ithome.com/login',url+'?redirect=http://localhost',
                        url.replace('www.ithome.com','www.ithome.com.evil.test'),
                        url.replace('www.ithome.com','www.ithome.com:443')):
            self.assertIsNone(article_capture_url(invalid))
        report = {**self.report,'url':url}
        capture = {**self.capture,'url':url,'capture_url':url}
        def reader(args, **kwargs):
            self.assertEqual(args[3], '--capture-article')
            Image.new('RGB',(1000,1200),'white').save(args[4])
            return SimpleNamespace(returncode=0,stdout=json.dumps(capture).encode(),stderr=b'')
        with scratch_dir() as root:
            helper=root/'SourceReader.exe';helper.touch()
            with patch('core.official_capture._reader_path',return_value=helper), \
                    patch('core.official_capture.subprocess.run',side_effect=reader):
                self.assertIsNone(capture_official_notice(report,root))
                asset=capture_official_notice(report,root,article_page=True)
            self.assertEqual(asset['capture_kind'],'article_page')
            self.assertNotIn('官方',asset['title'])
            self.assertEqual(asset['caption_label'],'报道原网页')

    def test_pixels_and_provenance_are_cached_and_bound_to_report(self):
        with scratch_dir() as output:
            helper = output / "SourceReader.exe"
            helper.touch()
            with patch("core.official_capture._reader_path", return_value=helper), \
                    patch("core.official_capture.subprocess.run", side_effect=self.reader) as run:
                asset = capture_official_notice(self.report, output)
                self.assertEqual(asset["origin"], "article")
                self.assertEqual(asset["kind"], "image")
                self.assertEqual(asset["source_report_id"], story_key(self.report))
                self.assertEqual(asset["source_page"], self.url)
                self.assertEqual(asset["capture_url"], self.url)
                self.assertEqual(asset["captured_at"], self.capture["captured_at"])
                self.assertEqual(asset["capture_sha256"], hashlib.sha256(Path(asset["path"]).read_bytes()).hexdigest())
                changed = dict(self.report, title="同一公告更新后的采集标题")
                again = capture_official_notice(changed, output)
                self.assertEqual(again["path"], asset["path"])
                self.assertEqual(again["source_report_id"], story_key(changed))
                self.assertEqual(run.call_count, 1)
                # Changed pixels must not be accepted as an authentic cached capture.
                Image.new("RGB", (1000, 1200), "black").save(asset["path"])
                capture_official_notice(self.report, output)
                self.assertEqual(run.call_count, 2)

    def test_missing_helper_is_explicit_failure(self):
        with scratch_dir() as output, patch("core.official_capture._reader_path", return_value=Path("missing.exe")):
            with self.assertRaisesRegex(OfficialCaptureError, "SourceReader"):
                capture_official_notice(self.report, output)

    def test_browser_failure_is_not_a_successful_asset(self):
        with scratch_dir() as output:
            helper = output / "SourceReader.exe"
            helper.touch()
            with patch("core.official_capture._reader_path", return_value=helper), \
                    patch("core.official_capture.subprocess.run", return_value=SimpleNamespace(
                        returncode=1, stdout=b"", stderr=b"Navigation failed")):
                with self.assertRaisesRegex(OfficialCaptureError, "Navigation failed"):
                    capture_official_notice(self.report, output)
                self.assertEqual(list(output.rglob("material.json")), [])

    def test_changed_url_missing_date_and_no_screenshot_are_rejected(self):
        for change in ({"url": "https://www.gov.cn/login"}, {"capture_url": "https://evil.test/a"},
                       {"published": ""}, {"summary": "Access denied"}, {"captured_at": "yesterday"},
                       {"capture_method": "rendered-text"}):
            with self.subTest(change=change), scratch_dir() as output:
                helper = output / "SourceReader.exe"
                helper.touch()
                with patch("core.official_capture._reader_path", return_value=helper), \
                        patch("core.official_capture.subprocess.run", return_value=SimpleNamespace(returncode=0,
                            stdout=json.dumps(dict(self.capture, **change)).encode(), stderr=b"")):
                    with self.assertRaises(OfficialCaptureError):
                        capture_official_notice(self.report, output)
                    self.assertEqual(list(output.rglob("material.json")), [])

    def test_missing_pixels_and_timeout_are_explicit_failures(self):
        with scratch_dir() as output:
            helper = output / "SourceReader.exe"
            helper.touch()
            with patch("core.official_capture._reader_path", return_value=helper), \
                    patch("core.official_capture.subprocess.run", return_value=SimpleNamespace(returncode=0,
                        stdout=json.dumps(self.capture).encode(), stderr=b"")):
                with self.assertRaisesRegex(OfficialCaptureError, "没有生成"):
                    capture_official_notice(self.report, output)
            with patch("core.official_capture._reader_path", return_value=helper), \
                    patch("core.official_capture.subprocess.run", side_effect=subprocess.TimeoutExpired("reader", 50)):
                with self.assertRaisesRegex(OfficialCaptureError, "TimeoutExpired"):
                    capture_official_notice(self.report, output)
            self.assertEqual(list(output.rglob("material.json")), [])


if __name__ == "__main__":
    unittest.main()
