import hashlib
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from requests import ConnectionError, Response

from core.team_client import TeamClient, TeamError
from tests.test_daily import scratch_dir


REVIEW_ID = "a" * 32
REVIEW = f"/v1/reviews/{REVIEW_ID}"
VIDEO = b"abcdefghijklmn"


def reply(status, data=None):
    response = Response()
    response.status_code = status
    response._content = json.dumps(data).encode()
    response._content_consumed = True
    return response


class TeamClientTest(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(scratch_dir())
        self.client = TeamClient("http://127.0.0.1:8765", "x" * 32)
        self.video = self.directory / "片头.mp4"
        self.video.write_bytes(VIDEO)
        self.package = {"video": str(self.video), "title": "工作室样片", "caption": "当日文案"}

    def test_local_service_keeps_single_file_upload(self):
        journal = self.directory / "uploads"

        def send(request, **_options):
            path = urlsplit(request.url).path
            if path.endswith("/upload"):
                self.assertEqual(request.method, "PUT")
                return reply(404)
            if request.method == "POST":
                self.assertEqual(path, "/v1/reviews")
                self.assertEqual(json.loads(request.body), {
                    "title": "工作室样片", "platform": "bilibili", "caption": "当日文案", "bytes": len(VIDEO),
                })
                return reply(201, {"id": REVIEW_ID})
            self.assertEqual((request.method, path), ("PUT", f"{REVIEW}/video"))
            self.assertEqual(request.headers["Content-Type"], "video/mp4")
            self.assertEqual(request.body.read(), VIDEO)
            return reply(200)

        with patch("requests.Session.send", side_effect=send) as calls:
            self.assertEqual(self.client.share("bilibili", self.package, resume_dir=journal), REVIEW_ID)
        self.assertEqual(calls.call_count, 3)
        self.assertFalse(list(journal.glob("*.json")))

    def test_multipart_retries_only_the_interrupted_request(self):
        calls, uploaded = [], {}

        def send(request, **_options):
            path = urlsplit(request.url).path
            calls.append(path)
            self.assertEqual(request.headers["Authorization"], "Bearer " + "x" * 32)
            self.assertEqual(request.headers["User-Agent"], "AutoContentFactory-Team/1.0")
            if path == "/v1/reviews":
                self.assertEqual(request.method, "POST")
                return reply(201, {"id": REVIEW_ID, "upload_mode": "multipart", "part_bytes": 6})
            if path == f"{REVIEW}/complete":
                self.assertEqual((request.method, json.loads(request.body)), ("POST", {}))
                self.assertEqual(b"".join(uploaded.values()), VIDEO)
                return reply(502 if calls.count(path) == 1 else 200)
            self.assertEqual(request.method, "PUT")
            self.assertEqual(request.headers["Content-Type"], "video/mp4")
            prefix = f"{REVIEW}/parts/"
            self.assertTrue(path.startswith(prefix), path)
            number = int(path.removeprefix(prefix))
            self.assertEqual(request.body, [b"abcdef", b"ghijkl", b"mn"][number - 1])
            if number == 2 and calls.count(path) == 1:
                raise ConnectionError("connection lost")
            if number == 2 and calls.count(path) == 2:
                return reply(503)
            uploaded[number] = request.body
            return reply(200)

        with patch("requests.Session.send", side_effect=send):
            self.assertEqual(self.client.share("bilibili", self.package), REVIEW_ID)
        self.assertEqual(calls, [
            "/v1/reviews", f"{REVIEW}/parts/1", f"{REVIEW}/parts/2", f"{REVIEW}/parts/2",
            f"{REVIEW}/parts/2", f"{REVIEW}/parts/3", f"{REVIEW}/complete", f"{REVIEW}/complete",
        ])

    def test_multipart_does_not_retry_a_rejected_part(self):
        with patch("requests.Session.send", side_effect=[
            reply(201, {"id": REVIEW_ID, "upload_mode": "multipart", "part_bytes": 6}), reply(403),
        ]) as calls:
            with self.assertRaises(TeamError) as error:
                self.client.share("bilibili", self.package)
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(urlsplit(calls.call_args.args[0].url).path, f"{REVIEW}/parts/1")

    def test_cloud_upload_resumes_after_restart_and_recovers_lost_completion(self):
        sent = {}
        upload_id = None
        phase = "interrupted"
        journal = self.directory / "uploads"

        def send(request, **_options):
            nonlocal upload_id
            path = urlsplit(request.url).path
            if path.endswith("/upload"):
                self.assertEqual(request.method, "PUT")
                review_id = path.split("/")[-2]
                if upload_id is None:
                    upload_id = review_id
                self.assertEqual(review_id, upload_id)
                return reply(200, {"id": upload_id, "upload_mode": "multipart", "part_bytes": 6,
                                   "ready": phase == "confirmed", "uploaded_parts": [
                                       {"part_number": number, "digest": hashlib.sha256(data).hexdigest()}
                                       for number, data in sent.items()
                                   ]})
            if path.endswith("/complete"):
                self.assertEqual(phase, "lost_completion")
                self.assertEqual(b"".join(sent.values()), VIDEO)
                raise ConnectionError("completion response was lost")
            number = int(path.split("/")[-1])
            if phase == "interrupted" and number == 2:
                raise ConnectionError("connection lost")
            self.assertNotIn(number, sent, "A committed part was sent again")
            sent[number] = request.body
            return reply(200)

        with patch("requests.Session.send", side_effect=send), patch("core.team_client.time.sleep"):
            with self.assertRaises(TeamError):
                self.client.share("bilibili", self.package, resume_dir=journal)
            self.assertEqual(set(sent), {1})

            phase = "lost_completion"
            reopened = TeamClient(self.client.address, self.client.token)
            with self.assertRaises(TeamError):
                reopened.share("bilibili", self.package, resume_dir=journal)

            phase = "confirmed"
            self.assertEqual(reopened.share("bilibili", self.package, resume_dir=journal), upload_id)
            self.assertFalse(list(journal.glob("*.json")))

    def test_truncated_source_never_completes_upload(self):
        calls = []

        def send(request, **_options):
            path = urlsplit(request.url).path
            calls.append(path)
            if path == "/v1/reviews":
                self.video.write_bytes(VIDEO[:8])
                return reply(201, {"id": REVIEW_ID, "upload_mode": "multipart", "part_bytes": 6})
            return reply(200)

        with patch("requests.Session.send", side_effect=send):
            with self.assertRaisesRegex(TeamError, "文件大小已改变"):
                self.client.share("bilibili", self.package)
        self.assertEqual(calls, ["/v1/reviews", f"{REVIEW}/parts/1"])

    def test_cached_video_requires_current_membership(self):
        item = {"id": REVIEW_ID, "bytes": len(VIDEO)}
        cache = self.directory / "cache"
        response = reply(200)
        response._content = VIDEO
        with patch("requests.Session.send", return_value=response):
            cached = self.client.download(item, cache)
        self.assertEqual(cached.read_bytes(), VIDEO)

        with patch("requests.Session.send", return_value=reply(401)) as calls:
            with self.assertRaises(TeamError) as error:
                self.client.download(item, cache)
        self.assertEqual(error.exception.status, 401)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(urlsplit(calls.call_args.args[0].url).path, REVIEW)
