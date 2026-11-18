import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.quality import (
    detect_webhook_kind,
    dispatch_quality_notice,
    duration_bounds,
    flagged_caption,
    inspect_item,
    inspect_package,
    list_notices,
    resolve_notices,
    save_webhook,
    send_webhook,
    validate_webhook_url,
    webhook_summary,
)
from core.workflow import edition_path, read_edition, write_json
from core.wpf_publish import _release_list
from core.wpf_team import team_stage
from core.wpf_workspace import workspace_stage


def make_mp4(path, payload=1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((16).to_bytes(4, "big") + b"ftyp" + b"\x00" * 8 + b"\x00" * payload)
    return path


class InspectPackageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.video = make_mp4(self.root / "final.mp4")

    def tearDown(self):
        self.directory.cleanup()

    def inspect(self, item=None, platform="douyin", package=None):
        package = package or {"video": str(self.video), "duration": 110}
        return inspect_package(item or {"target_seconds": 120}, platform, package)

    def test_valid_package_passes(self):
        result = self.inspect()
        self.assertTrue(result["ready"])
        self.assertEqual(result["findings"], [])
        self.assertTrue(result["checks"]["file"]["ok"])

    def test_single_source_topic_uses_same_duration_range_as_export(self):
        item={'target_seconds':120,'topic_job':{'scope':'topic'},
              'editorial':{'reports':[{'url':'https://example.com/news'}]}}
        result=self.inspect(item=item,platform='xiaohongshu',package={'video':str(self.video),'duration':68})
        self.assertTrue(result['checks']['duration']['ok'])
        self.assertEqual(result['checks']['duration']['range'],[60,144])
        item['topic_job']['scope']='week_tech'
        self.assertFalse(self.inspect(item=item,package={'video':str(self.video),'duration':68})['checks']['duration']['ok'])
        self.assertTrue(result["checks"]["duration"]["ok"])

    def test_missing_video_fails(self):
        result = self.inspect(package={"video": str(self.root / "none.mp4"), "duration": 110})
        self.assertFalse(result["ready"])
        self.assertTrue(any("成片文件" in line for line in result["findings"]))

    def test_corrupt_video_fails(self):
        self.video.write_bytes(b"not an mp4" * 100)
        result = self.inspect()
        self.assertFalse(result["ready"])

    def test_duration_out_of_range_fails(self):
        result = self.inspect(package={"video": str(self.video), "duration": 200})
        self.assertFalse(result["ready"])
        self.assertTrue(any("时长" in line for line in result["findings"]))
        self.assertEqual(result["checks"]["duration"]["range"], [96, 144])

    def test_episode_duration_bounds(self):
        result = self.inspect(item={"target_seconds": 720}, platform="bilibili",
                              package={"video": str(self.video), "duration": 700})
        self.assertTrue(result["ready"])
        result = self.inspect(item={"target_seconds": 720}, platform="bilibili",
                              package={"video": str(self.video), "duration": 300})
        self.assertFalse(result["ready"])

    def test_card_only_cut_fails_visuals_and_notes_pending_media(self):
        item = {
            "target_seconds": 120,
            "editorial": {"anchor": {"title": "测试"}},
            "media": [{"page_url": "https://example.com/1", "kind": "video"}],
            "edit": {"scenes": [{"media": "", "visual": {}} for _ in range(4)]},
        }
        result = self.inspect(item=item)
        self.assertFalse(result["ready"])
        self.assertFalse(result["checks"]["visuals"]["ready"])
        self.assertEqual(result["checks"]["media"]["pending"], 1)

    def test_inspect_item_draft_and_manual_rejection(self):
        draft_item = {
            "target_seconds": 60,
            "media": [{"page_url": "local://1", "kind": "video", "local": True}],
            "edit": {"scenes": [
                {"title": "开场", "text": "这是一段大概五十秒左右的测试口播文案内容，" * 9, "media": "local://1", "visual": {"layout": "footage"}},
                {"title": "结尾", "text": "收尾总结一句话，帮观众快速记住核心结论。" * 5, "media": "local://1", "visual": {"layout": "footage"}},
            ]},
        }
        precheck = inspect_item(draft_item, "douyin")
        self.assertEqual(precheck["stage"], "draft")
        self.assertTrue(precheck["ready"])

        rejected = {**draft_item, "package": {"video": str(self.video), "duration": 58},
                    "quality_review": {"decision": "rejected", "reason": "画面节奏太慢"}}
        post = inspect_item(rejected, "douyin")
        self.assertFalse(post["ready"])
        self.assertTrue(any("画面节奏太慢" in f for f in post["findings"]))

    def test_duration_bounds(self):
        self.assertEqual(duration_bounds("douyin", 60), (48, 72))
        self.assertEqual(duration_bounds("xiaohongshu", 180), (144, 180))
        self.assertEqual(duration_bounds("bilibili", 720), (600, 900))
        self.assertIsNone(duration_bounds("douyin", 0))


class WebhookAndNoticeWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.directory.name)
        self.output_dir = self.data_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.directory.cleanup()

    def test_webhook_detection_and_ssrf_guard(self):
        self.assertEqual(detect_webhook_kind("https://open.feishu.cn/open-apis/bot/v2/hook/abc")[0], "feishu")
        self.assertEqual(detect_webhook_kind("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=123")[0], "wecom")
        self.assertEqual(detect_webhook_kind("https://oapi.dingtalk.com/robot/send?access_token=123")[0], "dingtalk")
        with self.assertRaises(ValueError):
            validate_webhook_url("http://192.168.1.10/hook")
        with self.assertRaises(ValueError):
            validate_webhook_url("https://10.0.0.5/hook")

    def test_webhook_save_and_dispatch_formats_feishu_payload(self):
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/secret123456"
        info = save_webhook(self.data_dir, url, enabled=True, label="内容审片飞书群")
        self.assertTrue(info["configured"])
        self.assertEqual(info["kind"], "feishu")
        self.assertNotIn("secret123456", info["masked_url"])
        self.assertEqual(webhook_summary(self.data_dir)["label"], "内容审片飞书群")

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {"code": 0, "msg": "success"}
        with patch("core.quality.requests.post", return_value=mock_resp) as post:
            send_webhook(url, "标题", ["第一行"])
            sent_json = post.call_args.kwargs["json"]
            self.assertEqual(sent_json["msg_type"], "text")
            self.assertIn("标题\n第一行", sent_json["content"]["text"])

            receipt = dispatch_quality_notice(
                self.output_dir, "2026-11-15", "douyin", "测试成片", ["时长不足"], source="auto"
            )
            self.assertIn("内容审片飞书群", receipt["channels"])
            notices = list_notices(self.output_dir)
            self.assertEqual(len(notices), 1)
            self.assertEqual(notices[0]["title"], "测试成片")
            resolve_notices(self.output_dir, notice_id=notices[0]["id"])
            self.assertEqual(list_notices(self.output_dir), [])

    def test_workspace_quality_decide_and_approve_all_media_and_publish_fallback(self):
        day = "2026-11-11"
        video = make_mp4(self.output_dir / "daily" / day / "douyin" / "out.mp4")
        path = edition_path(self.output_dir, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {
            "date": day,
            "items": {
                "douyin": {
                    "id": "ep1",
                    "status": "ready",
                    "target_seconds": 60,
                    "editorial": {"anchor": {"title": "番茄炒蛋"}},
                    "story": {"title": "番茄炒蛋"},
                    "media": [{"page_url": "https://example.com/v1", "kind": "video"}],
                    "edit": {"scenes": [{"title": "一", "text": "炒蛋步骤" * 15, "media": "https://example.com/v1", "visual": {"layout": "footage"}}]},
                    "edit_revision": "r1",
                    "package_revision": "r1",
                    "package": {"title": "番茄炒蛋", "video": str(video), "duration": 58, "caption": "配文"},
                }
            },
            "feed_errors": [],
        })

        # Approve all pending media
        res = workspace_stage("workspace_approve_all_media", day, self.output_dir, "douyin", {})
        self.assertEqual(res["approved"], 1)

        # Reject without team.json configured — must succeed locally without error!
        decided = workspace_stage("workspace_quality_decide", day, self.output_dir, "douyin",
                                  {"decision": "rejected", "reason": "看着不太合适"})
        self.assertFalse(decided["inspection"]["ready"])
        self.assertEqual(read_edition(path)["items"]["douyin"]["held_video"], str(video))
        self.assertEqual(len(list_notices(self.output_dir)), 1)

        # team_flag also works offline without team.json!
        workspace_stage("workspace_quality_decide", day, self.output_dir, "douyin",
                        {"decision": "approved", "reason": "恢复测试"})
        preview = team_stage("team_share_preview", day, self.output_dir, "douyin", {})
        flagged = team_stage("team_flag", day, self.output_dir, "douyin",
                             {"version": preview["version"], "reason": "离线标记不合适"})
        self.assertIn("本机待改清单", flagged["receipt"]["channels"])

        # publish_list auto_fallback finds 2026-11-11 when queried on empty 2026-11-15
        rel = _release_list("2026-11-15", self.output_dir, auto_fallback=True)
        self.assertEqual(rel["active_day"], "2026-11-11")
        self.assertEqual(len(rel["rows"]), 1)
        self.assertTrue(rel["rows"][0]["held"])

    def test_quick_create_creates_original_editorial_draft(self):
        now = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc)
        created = workspace_stage(
            "workspace_quick_create", "2026-11-15", self.output_dir, "douyin",
            {
                "title": "三分钟学会家庭拿铁拉花",
                "script": "今天分享在家用家用咖啡机做拿铁奶泡的关键步骤：第一步冷藏全脂牛奶打发到六十度，第二步融合液面，第三步压低缸嘴推心形。",
                "asset_paths": [],
                "target_seconds": 60,
            },
            now=now,
        )
        self.assertEqual(created["platform"], "douyin")
        saved = read_edition(edition_path(self.output_dir, "2026-11-15"))["items"]["douyin"]
        self.assertEqual(saved["editorial"]["mode"], "original")
        self.assertEqual(saved["story"]["title"], "三分钟学会家庭拿铁拉花")


class FlaggedCaptionTest(unittest.TestCase):
    def test_auto_prefix_and_findings(self):
        caption = flagged_caption({"caption": "原标题\n正文"}, ["时长不符合"], source="auto")
        self.assertTrue(caption.startswith("【自动检测未通过】"))
        self.assertIn("· 时长不符合", caption)
        self.assertIn("原标题", caption)

    def test_manual_prefix(self):
        caption = flagged_caption({"caption": ""}, [], source="manual")
        self.assertEqual(caption, "【人工标记不合适】")


if __name__ == "__main__":
    unittest.main()
