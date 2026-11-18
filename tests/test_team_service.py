import json
import socket
import threading
import unittest
from http.client import HTTPConnection, HTTPResponse
from unittest.mock import patch
from urllib.parse import urlencode
from wsgiref.simple_server import WSGIRequestHandler, make_server

from core.team_service import TeamService
from tests.test_daily import scratch_dir


REVIEWS = "/v1/reviews"
MP4 = b"\x00\x00\x00\x14ftypisom\x00\x00\x00\x00isom\x00\x00\x00\x0cmdatdata"


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args):
        pass


class TeamServiceTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(scratch_dir())
        self.app = TeamService(directory)
        self.producer = self.app.create_member("制片")
        self.reviewer = self.app.create_member("审片")
        self.server = self.enterContext(make_server("127.0.0.1", 0, self.app, handler_class=QuietHandler))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, token=None, *, data=None, video=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")
        if video is not None:
            headers["Content-Type"] = "video/mp4"
            body = video
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, headers)
            with connection.getresponse() as response:
                raw = response.read()
                result = json.loads(raw) if response.getheader("Content-Type").startswith("application/json") else raw
                return response.status, result
        finally:
            connection.close()

    def create_review(self, title="工作室样片"):
        status, body = self.request("POST", REVIEWS, self.producer, data={
            "title": title, "platform": "bilibili", "caption": "当日文案", "bytes": len(MP4),
        })
        self.assertEqual(status, 201)
        return f"{REVIEWS}/{body['id']}"

    def test_members_can_submit_a_followup_and_open_both_versions_after_upload(self):
        first = self.create_review()
        first_id = first.rsplit("/", 1)[1]
        followup = {"title": "改版样片", "platform": "bilibili", "caption": "新版文案",
                    "bytes": len(MP4), "previous_id": first_id}
        self.assertEqual(self.request("POST", REVIEWS, self.reviewer, data=followup)[0], 404)
        self.assertEqual(self.request("PUT", f"{first}/video", self.producer, video=MP4)[0], 200)
        self.assertEqual(self.request("POST", f"{first}/feedback", self.reviewer, data={
            "revision": 0, "decision": "changes_requested", "text": "调整片头",
        })[0], 200)
        self.assertEqual(self.request("POST", REVIEWS, self.reviewer, data={**followup, "platform": "douyin"})[0], 400)
        status, record = self.request("POST", REVIEWS, self.reviewer, data=followup)
        self.assertEqual(status, 201)
        second = f"{REVIEWS}/{record['id']}"
        self.assertEqual(self.request("GET", first, self.producer)[1]["versions"], [])
        self.assertEqual(self.request("PUT", f"{second}/video", self.reviewer, video=MP4)[0], 200)

        original = self.request("GET", first, self.producer)[1]
        updated = self.request("GET", second, self.producer)[1]
        self.assertEqual((original["status"], original["feedback"][0]["text"]), ("changes_requested", "调整片头"))
        self.assertEqual([(item["id"], item["relation"]) for item in original["versions"]], [(record["id"], "next")])
        self.assertEqual([(item["id"], item["relation"]) for item in updated["versions"]], [(first_id, "previous")])
        self.assertEqual((updated["author"], updated["status"], updated["feedback"]), ("审片", "pending", []))

    def test_filtered_inbox_keeps_older_reviews_reachable_during_updates(self):
        def publish(title, when):
            with patch("core.team_service._now", return_value=when):
                path = self.create_review(title)
            self.assertEqual(self.request("PUT", f"{path}/video", self.producer, video=MP4)[0], 200)
            return path

        def page(**parameters):
            query = urlencode({"q": "样片 %", "status": "pending", "limit": 1, **parameters})
            status, body = self.request("GET", f"{REVIEWS}?{query}", self.reviewer)
            self.assertEqual(status, 200)
            return body

        timestamp = "2026-11-09T12:00:00.000+00:00"
        matching = sorted((publish("样片 % 一", timestamp), publish("样片 % 二", timestamp)), reverse=True)
        publish("样片 其他", timestamp)
        self.create_review("样片 % 尚未上传")
        first = page()
        cursor = matching[0].rsplit("/", 1)[1]
        self.assertEqual(first["next_cursor"], cursor)
        self.assertEqual([item["id"] for item in first["items"]], [cursor])

        publish("样片 % 新到", "2026-11-09T12:01:00.000+00:00")
        self.assertEqual(self.request("POST", f"{matching[0]}/feedback", self.reviewer, data={
            "revision": 0, "decision": "approved", "text": "",
        })[0], 200)
        second = page(before=cursor)
        self.assertEqual([item["id"] for item in second["items"]], [matching[1].rsplit("/", 1)[1]])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.request("GET", f"{REVIEWS}?before=invalid", self.reviewer)[0], 400)

    def test_only_submitter_can_make_an_export_ready_and_members_can_download_it(self):
        self.assertEqual(self.request("GET", REVIEWS)[0], 401)
        review = self.create_review()
        video = f"{review}/video"
        self.assertEqual(self.request("GET", REVIEWS, self.reviewer), (200, {"items": []}))
        self.assertEqual(self.request("GET", review, self.reviewer)[0], 404)
        self.assertEqual(self.request("GET", video, self.reviewer)[0], 404)
        self.assertEqual(self.request("PUT", video, self.reviewer, video=MP4)[0], 403)
        self.assertEqual(self.request("PUT", video, self.producer, video=b"x" * len(MP4))[0], 400)

        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3) as client:
            headers = (f"PUT {video} HTTP/1.0\r\nAuthorization: Bearer {self.producer}\r\n"
                       f"Content-Type: video/mp4\r\nContent-Length: {len(MP4)}\r\n\r\n")
            client.sendall(headers.encode("ascii") + MP4[:-5])
            client.shutdown(socket.SHUT_WR)
            with HTTPResponse(client) as response:
                response.begin()
                self.assertEqual(response.status, 400)
        self.assertEqual(self.request("GET", video, self.reviewer)[0], 404)
        self.assertEqual(list(self.app.videos.iterdir()), [])

        self.assertEqual(self.request("PUT", video, self.producer, video=MP4)[0], 200)
        self.assertEqual(self.request("PUT", video, self.producer, video=MP4[:-4] + b"edit")[0], 409)
        self.assertEqual(self.request("GET", video, self.reviewer), (200, MP4))
        status, body = self.request("GET", REVIEWS, self.reviewer)
        self.assertEqual(status, 200)
        record, = body["items"]
        self.assertEqual(record, {
            "id": review.rsplit("/", 1)[1], "title": "工作室样片", "platform": "bilibili",
            "caption": "当日文案", "bytes": len(MP4), "created_at": record["created_at"],
            "author": "制片", "status": "pending", "revision": 0,
        })

    def test_feedback_is_attributed_and_stale_revisions_cannot_change_the_review(self):
        review = self.create_review()
        self.assertEqual(self.request("PUT", f"{review}/video", self.producer, video=MP4)[0], 200)
        feedback = f"{review}/feedback"
        for token, decision, revision, note in (
            (self.reviewer, "comment", 0, "请复核片头"),
            (self.producer, "approved", 1, "已核对"),
        ):
            status, body = self.request("POST", feedback, token, data={
                "text": note, "decision": decision, "revision": revision,
            })
            self.assertEqual(status, 200)
            self.assertEqual(body["revision"], revision + 1)
            self.assertEqual(body["status"], "pending" if decision == "comment" else "approved")

        stale = self.request("POST", feedback, self.reviewer, data={
            "text": "旧版本意见", "decision": "changes_requested", "revision": 1,
        })
        self.assertEqual(stale[0], 409)
        status, detail = self.request("GET", review, self.reviewer)
        self.assertEqual((status, detail["revision"], detail["status"]), (200, 2, "approved"))
        self.assertEqual([(message["author"], message["decision"], message["text"]) for message in detail["feedback"]],
                         [("审片", "comment", "请复核片头"), ("制片", "approved", "已核对")])

        self.app.revoke_member("审片")
        self.assertEqual(self.request("GET", review, self.reviewer)[0], 401)
        replacement = self.app.create_member("审片")
        self.assertNotEqual(replacement, self.reviewer)
        self.assertEqual(self.request("GET", review, replacement)[0], 200)
