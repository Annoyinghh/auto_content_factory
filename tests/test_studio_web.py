"""End-to-end tests for the Broadcast Web Studio server and UI."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from starlette.requests import Request

from studio_web import create_studio_app


def _call_app(app, method: str, path: str, *, query: str = "", body: dict | None = None, headers: list[tuple[bytes, bytes]] | None = None):
    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": raw_body, "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode("utf-8"),
        "headers": headers or [(b"content-type", b"application/json")],
    }
    request = Request(scope, receive)
    for route in app.routes:
        if route.path == path and method in route.methods:
            return asyncio.run(route.endpoint(request))
    raise AssertionError(f"No route matched {method} {path}")


class StudioWebTest(unittest.TestCase):
    def test_workbench_html_has_all_five_views_and_zero_cringe_or_emojis(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_studio_app(Path(tmp))
            resp = _call_app(app, "GET", "/")
            self.assertEqual(resp.status_code, 200)
            html = resp.body.decode("utf-8")
            for required in (
                "AI 自媒体运营工作台",
                "全网热点捕捉",
                "今日爬取热点与全网信源池",
                "分镜口播工坊",
                "母带终审室与合成排期",
                "开放平台账号授权与线上作品核对",
            ):
                self.assertIn(required, html)
            for forbidden in ("🎬", "🔥", "🚀", "💡", "🎉", "搞钱脑洞", "小众暴利", "傻瓜式"):
                self.assertNotIn(forbidden, html)

    def test_api_state_and_story_selection_and_cut_editing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            app = create_studio_app(out)

            # 1. Select a story from intake into xiaohongshu
            resp = _call_app(
                app,
                "POST",
                "/api/action",
                body={
                    "action": "select_story",
                    "day": "2026-11-08",
                    "platform": "xiaohongshu",
                    "payload": {
                        "story": {
                            "title": "TrendForce：预计 2027 年翻新内存在智能手机市场渗透率达 7-9%",
                            "summary": "集邦咨询最新存储行业研究显示，移动 DRAM 合约价格持续走高。",
                            "url": "https://www.ithome.com/1/023/756.htm",
                            "source": "IT之家",
                            "published": "2026-11-08T08:00:00+00:00",
                            "category": "消费",
                        }
                    },
                },
            )
            self.assertEqual(resp.status_code, 200)
            payload = json.loads(resp.body)
            self.assertTrue(payload["ok"])
            xhs = payload["edition"]["items"]["xiaohongshu"]
            self.assertEqual(xhs["story"]["source"], "IT之家")
            self.assertGreaterEqual(len(xhs["draft"]["scenes"]), 2)

            # 2. Save modified storyboard scenes
            scenes = xhs["draft"]["scenes"]
            scenes[0]["title"] = "核心价格变动核对"
            resp2 = _call_app(
                app,
                "POST",
                "/api/action",
                body={
                    "action": "save_cut",
                    "day": "2026-11-08",
                    "platform": "xiaohongshu",
                    "payload": {"scenes": scenes},
                },
            )
            self.assertEqual(resp2.status_code, 200)
            saved = json.loads(resp2.body)
            self.assertEqual(
                saved["edition"]["items"]["xiaohongshu"]["draft"]["scenes"][0]["title"],
                "核心价格变动核对",
            )

            # 3. Add a copywriting reference case
            resp3 = _call_app(
                app,
                "POST",
                "/api/action",
                body={
                    "action": "add_case",
                    "platform": "xiaohongshu",
                    "payload": {
                        "platform": "xiaohongshu",
                        "title": "电子产品选购参数核对结构",
                        "url": "https://www.xiaohongshu.com/explore/123456",
                        "evidence": "收藏率高于同类均值",
                        "keywords": "数码 内存 选购",
                        "notes": "先列价格分水岭，再给两条核对标准。",
                    },
                },
            )
            self.assertEqual(resp3.status_code, 200)
            cw = json.loads(resp3.body)["copywriting"]["cases"]["xiaohongshu"]
            self.assertTrue(any(c["title"] == "电子产品选购参数核对结构" for c in cw))

    def test_media_endpoint_blocks_outside_files_and_supports_byte_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            sample = out / "video_test.mp4"
            sample.write_bytes(b"0123456789abcdefghijklmnopqrstuvwxyz")
            app = create_studio_app(out)

            # Reject file outside allowed roots
            outside = Path(tmp).parent / "outside_secret.txt"
            resp_bad = _call_app(app, "GET", "/api/media", query=f"path={outside}")
            self.assertEqual(resp_bad.status_code, 404)

            # Support HTTP Range request
            from urllib.parse import quote
            resp_range = _call_app(
                app,
                "GET",
                "/api/media",
                query=f"path={quote(str(sample))}",
                headers=[(b"range", b"bytes=0-9")],
            )
            self.assertEqual(resp_range.status_code, 206)
            self.assertEqual(resp_range.headers.get("accept-ranges"), "bytes")
            self.assertEqual(resp_range.headers.get("content-range"), "bytes 0-9/36")


if __name__ == "__main__":
    unittest.main()
