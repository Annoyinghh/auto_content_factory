import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.browser_source import read_public_article
from tests.test_works_library import scratch_dir


class BrowserSourceTests(unittest.TestCase):
    def test_only_known_public_article_routes_can_start_reader(self):
        with patch("core.browser_source.subprocess.run") as run:
            for url in ("http://www.sohu.com/a/1", "https://localhost/a/1", "https://www.sohu.com@127.0.0.1/a/1",
                        "https://www.sohu.com:8443/a/1", "https://www.sohu.com/login"):
                self.assertIsNone(read_public_article(url))
            run.assert_not_called()

    def test_browser_excerpt_preserves_date_and_untrusted_media_as_candidates(self):
        url = "https://www.sohu.com/a/1"
        article = {"url": url, "title": "番茄炒蛋", "summary": "可读原文。" * 70, "published": "2025-12-28 11:16",
                   "media_candidates": [{"kind": "image", "url": "https://example.com/photo.jpg"}]}
        with scratch_dir() as output, patch("core.browser_source.Path.is_file", return_value=True), \
                patch("core.browser_source.subprocess.run", return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(article).encode())):
            found = read_public_article(url, output)
        self.assertEqual(found["source_type"], "reference")
        self.assertEqual(found["published"], article["published"])
        self.assertEqual(found["media_candidates"], article["media_candidates"])
        self.assertNotIn("path", found["media_candidates"][0])

    def test_redirect_or_login_page_is_not_accepted_as_article(self):
        with scratch_dir() as output, patch("core.browser_source.Path.is_file", return_value=True), \
                patch("core.browser_source.subprocess.run", return_value=SimpleNamespace(returncode=0,
                    stdout=json.dumps({"url": "https://www.sohu.com/login", "title": "登录", "summary": "x"*400}).encode())):
            self.assertIsNone(read_public_article("https://www.sohu.com/a/1", output))


if __name__ == "__main__":
    unittest.main()
