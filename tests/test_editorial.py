import datetime as dt
import unittest
from unittest.mock import patch

from core.daily_news import story_key
from core.workflow import edition_path, read_edition, run_stage
from test_daily import scratch_dir


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now().astimezone()
        self.day = self.now.date()
        self.reports = [
            {"title": title, "url": f"https://example.com/{i}", "source": "测试报道",
             "summary": "深地科学观测与工程方法有各自的范围。" * 24, "published": self.now.isoformat()}
            for i, title in enumerate(("万米科探井", "万米钻探设备", "万米岩芯观测"))
        ]
        self.snapshot = {"articles": self.reports, "ranks": {"douyin": {
            "published": self.now.timestamp(), "entries": [{"title": "万米钻探", "rank": 1,
            "url": "https://example.com/topic", "heat": "热"}]}}}

    def test_both_entrypoints_persist_only_confirmed_report_snapshots(self):
        from core.editorial import source_options

        ids = [story_key(report) for report in self.reports]
        for anchor in ({"kind": "article", "id": ids[0]},
                       {"kind": "hotspot", "platform": "douyin", "title": "万米钻探"}):
            with self.subTest(anchor=anchor), scratch_dir() as root, \
                    patch("core.intake.read_intake", return_value=self.snapshot), \
                    patch("core.editorial.chat_json", return_value={"ids": [ids[2], ids[1]]}):
                options = source_options(self.snapshot, anchor, self.now)
                self.assertEqual(len(options["reports"]), 3)
                item = run_stage("editorial_create", self.day, root, "bilibili",
                                 {"anchor": anchor, "source_ids": ids[:2]}, now=self.now)
                self.assertEqual([r["id"] for r in item["editorial"]["reports"]], ids[:2])
                self.assertEqual(item["editorial"]["reports"][0]["summary"], self.reports[0]["summary"])
                self.assertNotIn(ids[2], [r["id"] for r in read_edition(
                    edition_path(root, self.day))["items"]["bilibili"]["editorial"]["reports"]])
                with self.assertRaisesRegex(RuntimeError, "确认"):
                    run_stage("render", self.day, root, "bilibili")

    def test_batch_evidence_rejects_unapproved_source_and_unknown_value(self):
        from core.editorial import check_evidence

        report = {**self.reports[0], "id": "approved", "summary": "工程报告给出的实测井深为10910米。"}
        shot = {"text": "实测井深10910米。", "evidence": [
            {"source_id": "approved", "quote": "工程报告给出的实测井深为10910米。"}]}
        check_evidence([shot], [report])
        with self.assertRaisesRegex(ValueError, "来源"):
            check_evidence([{**shot, "evidence": [{"source_id": "other", "quote": report["summary"]}]}], [report])
        with self.assertRaisesRegex(ValueError, "3000"):
            check_evidence([{**shot, "text": "实测井深3000米。"}], [report])

    def test_batch_repairs_only_the_shot_with_an_unsupported_number(self):
        from core.editorial import _write_batch

        quote = "工程报告给出的实测井深为10910米。"
        report = {"id": "report", "summary": quote}
        evidence = {"1": {"source_id": "report", "quote": quote}}
        valid = {"title": "实测深度", "text": "井深的实测值是10910米。", "media": "", "motion": "push",
                 "evidence_ids": ["1"]}
        invalid = {**valid, "title": "测量结果", "text": "报告记录的深度是3000米，除了"}
        corrected = "报告中所说的是钻完时的实际深度。"
        extras = [{**valid, "title": "先看读数", "text": "报告列出了最终的井深。"},
                  {**valid, "title": "解释数据", "text": "这个深度来自现场测量。"}]
        with patch("core.editorial.chat_json", side_effect=[{"scenes": [valid, invalid, *extras]},
                   {"scenes": [{"index": 1, "text": corrected}]}, {"issues": []}]) as model:
            batch = _write_batch({}, {}, [report], [], evidence, 4, 60)
        self.assertEqual([row["text"] for row in batch["scenes"]],
                         [valid["text"], corrected, *(row["text"] for row in extras)])
        revised = model.call_args_list[1].args[1]["scenes"]
        self.assertEqual([row["unsupported_numbers"] for row in revised], [["3000"]])
        self.assertTrue(revised[0]["incomplete"])

    def test_changing_one_report_preserves_unrelated_chapter(self):
        from core.editorial import plan_chapters

        rows = [{**row, "id": story_key(row)} for row in self.reports]
        previous = [{"story": {**row, "title": f"章节{i}"}, "question": "核对工程证据", "reports": [row]}
                    for i, row in enumerate(rows)]
        replacement = {**rows[1], "id": "updated", "title": "钻机新资料"}
        editorial = {"anchor": {"title": "万米科探井"}, "reports": [rows[0], replacement, rows[2]]}
        with scratch_dir() as root, patch("core.editorial.chat_json") as model:
            chapters, _ = plan_chapters(editorial, "bilibili", root / "outline.json",
                                        previous=previous, previous_plan={"throughline": "核对工程进展"})
        self.assertEqual(chapters[0], previous[0])
        self.assertEqual(chapters[2], previous[2])
        self.assertEqual(chapters[1]["reports"], [replacement])
        model.assert_not_called()

    def test_preview_leaves_long_export_for_explicit_current_revision(self):
        from pathlib import Path

        ids = [story_key(row) for row in self.reports]
        anchor = {"kind": "article", "id": ids[0]}
        scenes = [{"title": f"章节{i}", "text": f"这是第{i}段验收口播。", "media": "", "zoom": False}
                  for i in range(3)]
        draft = {"scenes": scenes, "editorial_plan": {"throughline": "验收"},
                 "chapter_starts": [{"title": scene["title"], "scene": i, "source_ids": [ids[0]]}
                                    for i, scene in enumerate(scenes)]}
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=self.snapshot):
            item = run_stage("editorial_create", self.day, root, "bilibili",
                             {"anchor": anchor, "source_ids": ids[:2]}, now=self.now)
            reports = item["editorial"]["reports"]
            chapters = [{"story": {**reports[0], "title": s["title"]}, "question": "解释依据", "reports": reports}
                        for s in scenes]
            with patch("core.editorial.plan_chapters", return_value=(chapters, draft["editorial_plan"])), \
                    patch("core.editorial.write_batches", return_value=draft), \
                    patch("core.workflow._prepare_media", return_value=[]), \
                    patch("core.audio_engine.generate_voice_and_subtitles", return_value={"total_duration": 34}), \
                    patch("core.video_engine.render_platform_video", return_value=Path("preview.mp4")), \
                    patch("core.workflow._render", return_value={"video": "full.mp4"}) as render:
                preview = run_stage("editorial_preview", self.day, root, "bilibili", item["id"])
                self.assertEqual(preview["video"], "preview.mp4")
                current = read_edition(edition_path(root, self.day))["items"]["bilibili"]
                self.assertNotIn("edit", current)
                self.assertEqual(current["preview_revision"], current["suggestion_revision"])
                with self.assertRaisesRegex(RuntimeError, "确认"):
                    run_stage("editorial_export", self.day, root, "bilibili",
                              {"id": item["id"], "revision": current["suggestion_revision"]})
                render.assert_not_called()
                edited = {**draft, "scenes": [{**scenes[0], "text": "已核对开场。"}, *scenes[1:]]}
                run_stage("save", self.day, root, "bilibili", edited)
                current = read_edition(edition_path(root, self.day))["items"]["bilibili"]
                self.assertNotEqual(current["preview_revision"], current["edit_revision"])
                self.assertEqual(run_stage("editorial_export", self.day, root, "bilibili",
                                 {"id": item["id"], "revision": current["edit_revision"]})["video"], "full.mp4")

    def test_material_review_controls_new_drafts_and_keeps_existing_video(self):
        from core.media_review import usable_media
        from core.workflow import _merge_media, write_json

        asset = {"page_url": "https://example.com/asset", "title": "待核对照片", "path": "original.jpg"}
        draft = {"scenes": [{"title": "开场", "text": "开场文字", "zoom": False, "media": asset["page_url"]}]}
        item = {"id": "issue", "media": [], "edit": draft, "edit_revision": "old",
                "preview_media": [asset["page_url"]], "preview_revision": "old",
                "package": {"video": "existing.mp4"}}
        with scratch_dir() as root:
            path = edition_path(root, self.day)
            path.parent.mkdir(parents=True)
            write_json(path, {"items": {"bilibili": item}})
            _merge_media(path, "bilibili", [asset])
            self.assertEqual(usable_media(read_edition(path)["items"]["bilibili"]), [])
            payload = {"page_url": asset["page_url"], "id": "issue", "state": "approved"}
            self.assertEqual(run_stage("review_media", self.day, root, "bilibili", payload), "approved")
            self.assertEqual(usable_media(read_edition(path)["items"]["bilibili"]), [asset])
            self.assertEqual(run_stage("review_media", self.day, root, "bilibili",
                                      {**payload, "state": "removed"}), "removed")
            changed = read_edition(path)["items"]["bilibili"]
            self.assertEqual(changed["edit"]["scenes"][0]["media"], "")
            self.assertNotEqual(changed["edit_revision"], "old")
            self.assertIsNone(changed["preview_revision"])
            self.assertEqual(changed["package"]["video"], "existing.mp4")
