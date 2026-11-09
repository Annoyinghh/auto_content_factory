import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from requests import Response

from core.news_release import submit_bilibili, submit_douyin
from tests.test_daily import scratch_dir


def reply(payload):
    response = Response()
    response.status_code = 200
    response._content = json.dumps(payload).encode()
    return response


class MultipartReleaseTest(unittest.TestCase):
    def test_douyin_posts_all_parts_before_submitting_and_reuses_receipt(self):
        calls = []

        def send(request, **kwargs):
            url = urlsplit(request.url)
            query = parse_qs(url.query)
            self.assertEqual(query["open_id"], ["personal"])
            self.assertEqual(request.headers["access-token"], "token")
            calls.append(url.path)
            if url.path == "/video/part/init/":
                return reply({"data": {"error_code": 0, "upload_id": "@upload"}})
            if url.path == "/video/part/upload/":
                self.assertEqual(query["upload_id"], ["@upload"])
                number = str(calls.count(url.path))
                self.assertEqual(query["part_number"], [number])
                self.assertIn(b'name="video"', request.body)
                self.assertIn({"1": b"abcdefg", "2": b"hijklmn"}[number], request.body)
                return reply({"extra": {"error_code": 0}})
            if url.path == "/video/part/complete/":
                self.assertEqual(query["upload_id"], ["@upload"])
                return reply({"data": {"error_code": 0, "video": {"video_id": "video-id"}}})
            self.assertEqual(url.path, "/video/create/")
            self.assertEqual(json.loads(request.body)["video_id"], "video-id")
            return reply({"data": {"error_code": 0, "item_id": "douyin-receipt"}})

        with (scratch_dir() as output,
              patch("core.news_release.DOUYIN_SINGLE_LIMIT", 8, create=True),
              patch("core.news_release.DOUYIN_PART_SIZE", 8, create=True),
              patch("core.accounts.douyin_credentials", return_value=("token", "personal")),
              patch("requests.Session.send", side_effect=send)):
            video = output / "长视频.mp4"
            video.write_bytes(b"abcdefghijklmn")
            package = {"video": str(video), "caption": "本地测试"}
            self.assertEqual(submit_douyin(package, output), "douyin-receipt")
            self.assertEqual(submit_douyin(package, output), "douyin-receipt")
            self.assertEqual((output / "submitted.txt").read_text(), "douyin-receipt")
        self.assertEqual(calls, ["/video/part/init/", "/video/part/upload/", "/video/part/upload/",
                                 "/video/part/complete/", "/video/create/"])

    def test_bilibili_merges_binary_parts_before_submitting(self):
        calls = []

        def send(request, **kwargs):
            url = urlsplit(request.url)
            query = parse_qs(url.query)
            calls.append(url.path.rsplit("/", 1)[-1])
            if url.path.endswith("/video/init"):
                self.assertEqual(json.loads(request.body)["utype"], "0")
                return reply({"code": 0, "data": {"upload_token": "upload"}})
            if url.path == "/video/v2/part/upload":
                self.assertEqual(request.headers["Content-Type"], "application/octet-stream")
                self.assertNotIn("Authorization", request.headers)
                number = calls.count("upload")
                self.assertEqual(query, {"upload_token": ["upload"], "part_number": [str(number)]})
                self.assertEqual(request.body, [b"abcdef", b"ghijkl", b"m"][number - 1])
                return reply({"code": 0})
            if url.path.endswith("/video/complete"):
                self.assertEqual(query["upload_token"], ["upload"])
                self.assertEqual(request.headers["Access-Token"], "token")
                return reply({"code": 0})
            if url.path.endswith("/cover/upload"):
                return reply({"code": 0, "data": {"url": "https://example.com/cover.png"}})
            self.assertTrue(url.path.endswith("/add-by-utoken"))
            self.assertEqual(query["upload_token"], ["upload"])
            return reply({"code": 0, "data": {"resource_id": "BVdemo"}})

        with (scratch_dir() as output,
              patch("core.news_release.BILI_SINGLE_LIMIT", 8, create=True),
              patch("core.news_release.BILI_PART_SIZE", 6, create=True),
              patch("core.accounts.bilibili_credentials", return_value=("client", "secret", "token")),
              patch("core.accounts.bilibili_partition", return_value=124),
              patch("requests.Session.send", side_effect=send)):
            video, cover = output / "长视频.mp4", output / "cover.png"
            video.write_bytes(b"abcdefghijklm")
            cover.write_bytes(b"cover")
            package = {"video": str(video), "cover": str(cover), "title": "本地测试", "tags": ["科技"],
                       "credit_caption": "测试素材"}
            self.assertEqual(submit_bilibili(package, output), "BVdemo")
            self.assertEqual((output / "submitted.txt").read_text(), "BVdemo")
        self.assertEqual(calls, ["init", "upload", "upload", "upload", "complete", "upload", "add-by-utoken"])
