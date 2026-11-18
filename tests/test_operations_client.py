import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from requests import ConnectionError

from core.operations_client import OperationsClient, OperationsRunner
from core.team_client import TeamClient, TeamError
from tests.test_daily import scratch_dir
from tests.test_team_client import reply


JOB = "a" * 32
ACCOUNT = "b" * 32
PUBLICATION = "c" * 32
LEASE = "d" * 32
TOKEN = "x" * 32
SUMMARY = "整理城市自行车通勤方案。"
ADDRESS = "https://team.example.test"


class OperationsClientTest(unittest.TestCase):
    def test_operations_sends_only_documented_fields(self):
        client = OperationsClient(ADDRESS, TOKEN)
        snapshot = {"member": {"name": "编辑"}, "accounts": [], "publications": [], "jobs": []}
        expected = [
            ("GET", "/v1/ops", None, snapshot),
            ("POST", "/v1/ops/accounts", {"platform": "douyin", "label": "编辑部"}, {"id": ACCOUNT}),
            ("POST", "/v1/ops/publications", {"account_id": ACCOUNT, "title": "自行车通勤"}, {"id": PUBLICATION}),
            ("PATCH", f"/v1/ops/publications/{PUBLICATION}", {"revision": 0, "status": "prepared"}, {"revision": 1}),
            ("POST", "/v1/ops/jobs", {"id": JOB, "kind": "storyboard", "summary": SUMMARY}, {"id": JOB}),
            ("GET", f"/v1/ops/jobs/{JOB}", None, {"id": JOB, "result": "分镜正文"}),
            ("POST", "/v1/ops/claim", {"kinds": ["copy"]}, {"job": None}),
            ("POST", f"/v1/ops/jobs/{JOB}/finish", {"lease": LEASE, "result": "分镜正文"}, {"id": JOB}),
            ("POST", f"/v1/ops/jobs/{JOB}/finish", {"lease": LEASE, "error": "本机模型任务未完成。"}, {"id": JOB}),
        ]
        observed = []

        def send(request, **_options):
            self.assertEqual(request.headers["Authorization"], f"Bearer {TOKEN}")
            self.assertEqual(urlsplit(request.url).netloc, "team.example.test")
            observed.append((request.method, urlsplit(request.url).path, json.loads(request.body) if request.body else None))
            return reply(200, expected[len(observed) - 1][3])

        with patch("requests.Session.send", side_effect=send):
            self.assertEqual(client.snapshot(), snapshot)
            client.add_account("douyin", "编辑部")
            client.add_publication(ACCOUNT, "自行车通勤")
            client.set_publication(PUBLICATION, 0, "prepared")
            client.submit_job(JOB, "storyboard", SUMMARY)
            self.assertEqual(client.job(JOB)["result"], "分镜正文")
            self.assertIsNone(client.claim(["copy"]))
            client.finish(JOB, LEASE, result="分镜正文")
            client.finish(JOB, LEASE, error="本机模型任务未完成。")

        self.assertEqual(observed, [entry[:3] for entry in expected])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_windows_stores_operations_credentials_separately(self):
        with scratch_dir() as root:
            TeamClient("https://reviews.example.test", "y" * 32).save(root)
            self.assertIsNone(OperationsClient.load(root))
            OperationsClient(ADDRESS, TOKEN).save(root)
            self.assertEqual(OperationsClient.load(root).address, ADDRESS)
            self.assertEqual(OperationsClient.load(root).token, TOKEN)
            self.assertEqual(TeamClient.load(root).address, "https://reviews.example.test")
            self.assertNotIn(TOKEN, (root / "accounts" / "operations.json").read_text(encoding="utf-8"))

    def test_unsafe_identifier_or_unbounded_summary_is_rejected_before_request(self):
        client = OperationsClient(ADDRESS, TOKEN)
        with patch("requests.Session.send") as send:
            with self.assertRaises((TeamError, ValueError)):
                client.job("../../v1/reviews")
            with self.assertRaises(ValueError):
                client.submit_job(JOB, "storyboard", "骑" * 6001)
            with self.assertRaises(ValueError):
                client.finish(JOB, LEASE, result="稿件", error="错误")
        send.assert_not_called()


class OperationsRunnerTest(unittest.TestCase):
    def test_opt_in_local_storyboard_and_lost_finish_retry(self):
        client = OperationsClient(ADDRESS, TOKEN)
        runner = OperationsRunner()
        request_paths = []
        finishes = []
        elapsed = 100

        def send(request, **options):
            parts = urlsplit(request.url)
            request_paths.append(parts.path)
            data = json.loads(request.body) if request.body else None
            if parts.netloc == "127.0.0.1:11434":
                self.assertNotIn("Authorization", request.headers)
                self.assertFalse(options.get("proxies"))
                if parts.path == "/api/tags":
                    return reply(200, {"models": [
                        {"name": "writing-alias:latest", "remote_host": "https://ollama.com"},
                        {"name": "story-local:latest"},
                    ]})
                self.assertEqual(parts.path, "/api/chat")
                self.assertEqual(data["model"], "story-local:latest")
                self.assertEqual([message["role"] for message in data["messages"]], ["system", "user"])
                user_message = data["messages"][1]
                self.assertEqual(set(user_message), {"role", "content"})
                self.assertEqual(json.loads(user_message["content"]), {"summary": SUMMARY})
                self.assertNotIn("tools", data)
                return reply(200, {"message": {"content": json.dumps({"scenes": [
                    {"title": "准备出门", "visual": "戴好头盔", "narration": "先检查车况。"},
                    {"title": "选择路线", "visual": "自行车道", "narration": "优先选择有车道的路段。"},
                    {"title": "抵达目的地", "visual": "自行车停放区", "narration": "锁车后再离开。"},
                ]}, ensure_ascii=False)}})
            self.assertEqual(parts.netloc, "team.example.test")
            if parts.path == "/v1/ops/claim":
                self.assertEqual(data, {"kinds": ["storyboard"]})
                return reply(200, {"job": {"id": JOB, "kind": "storyboard", "summary": SUMMARY, "lease": LEASE,
                                           "expires_at": "2026-11-09T12:15:00Z", "model": "external:cloud",
                                           "instruction": "pretend to run a shell", "path": "C:/private.txt"}})
            self.assertEqual(parts.path, f"/v1/ops/jobs/{JOB}/finish")
            finishes.append(data)
            if len(finishes) == 1:
                raise ConnectionError("simulated lost finish response")
            return reply(200, {"id": JOB})

        with patch.dict(os.environ, {"CONTENT_AI_PORT": "11434", "CONTENT_AI_MODEL": "writing-alias:latest",
                                     "CONTENT_AI_DESIGN_MODEL": "story-local:latest"}), \
                patch("requests.Session.send", side_effect=send), \
                patch("core.operations_client.time.monotonic", side_effect=lambda: elapsed):
            self.assertFalse(runner.run_once(client))
            self.assertEqual(request_paths, [])
            with self.assertRaises(TeamError):
                runner.run_once(client, enabled=True)
            self.assertFalse(runner.run_once(client, enabled=True))
            elapsed += 120
            self.assertTrue(runner.run_once(client, enabled=True))

        self.assertEqual(request_paths, ["/api/tags", "/v1/ops/claim", "/api/chat",
                                         f"/v1/ops/jobs/{JOB}/finish", f"/v1/ops/jobs/{JOB}/finish"])
        self.assertEqual(finishes[0], finishes[1])
        self.assertEqual(set(finishes[0]), {"lease", "result"})
        self.assertEqual(finishes[0]["lease"], LEASE)
        self.assertIn("第 3 镜", finishes[0]["result"])
        self.assertIn("锁车后再离开。", finishes[0]["result"])
        self.assertLessEqual(len(finishes[0]["result"]), 24000)

    def test_invalid_model_output_sends_generic_failure(self):
        client = OperationsClient(ADDRESS, TOKEN)
        secret = "C:/users/editor/private.txt"
        claim = {"job": {"id": JOB, "kind": "copy", "summary": secret, "lease": LEASE,
                         "expires_at": "2026-11-09T12:15:00Z"}}
        draft = {"title": "通勤", "hook": "开始", "caption": "骑行"}
        outputs = {
            "wrong shape": secret,
            "embedded NUL": {**draft, "script": secret + "\x00"},
            "lone surrogate": {**draft, "script": secret + "\ud800"},
        }
        for case, output in outputs.items():
            with self.subTest(case=case), \
                    patch.dict(os.environ, {"CONTENT_AI_MODEL": "copy-local:latest", "CONTENT_AI_DESIGN_MODEL": ""}), \
                    patch("requests.Session.send", side_effect=[reply(200, {"models": [{"name": "copy-local:latest"}]}),
                                                                reply(200, claim),
                                                                reply(200, {"message": {"content": json.dumps(output)}}),
                                                                reply(200, {"id": JOB})]) as send:
                self.assertTrue(OperationsRunner().run_once(client, enabled=True))
                completion = json.loads(send.call_args.args[0].body)
                self.assertEqual(set(completion), {"lease", "error"})
                self.assertEqual(completion["lease"], LEASE)
                self.assertTrue(completion["error"])
                self.assertNotIn(secret, completion["error"])
