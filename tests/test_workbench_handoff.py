import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.daily_news import story_key
from core.local_ai import DEFAULT_MODEL, VISUAL_MODELS, model_for
from core.media_review import usable_media
from core.workflow import edition_path, read_edition, run_stage
from test_daily import NOW, scratch_dir


class HandoffTests(unittest.TestCase):
    def test_prepared_eight_billion_writer_is_used_with_separate_vision(self):
        import os
        with patch.dict(os.environ,{},clear=True), patch('core.local_ai.installed_models',return_value={
                'acf-writer:8b','acf-vision-qwen35:4b',DEFAULT_MODEL}) as installed:
            self.assertEqual(model_for('writing'),'acf-writer:8b')
            self.assertEqual(model_for('editing'),'acf-vision-qwen35:4b')
            installed.return_value.add('acf-writer-qwen35:9b')
            self.assertEqual(model_for('writing'),'acf-writer-qwen35:9b')
            self.assertEqual(model_for('design'),'acf-writer-qwen35:9b')
            os.environ['CONTENT_AI_MODEL']='chosen-writer'
            self.assertEqual(model_for('writing'),'chosen-writer')

    def test_prepared_vision_model_is_preferred_without_overriding_explicit_choices(self):
        import os
        preferred='acf-vision-qwen35:4b'
        with patch.dict(os.environ,{},clear=True), patch('core.local_ai.installed_models',return_value={preferred,DEFAULT_MODEL}):
            self.assertEqual(model_for('design'),preferred)
            self.assertEqual(model_for('editing'),preferred)
            self.assertEqual(model_for('writing'),preferred)
            os.environ['CONTENT_AI_EDIT_MODEL']='chosen-vision'
            self.assertEqual(model_for('editing'),'chosen-vision')

    def test_explicit_assembly_media_does_not_trigger_unrelated_stock_search(self):
        from core.workflow import _prepare_media, write_json
        with scratch_dir() as root:
            path = edition_path(root, NOW.date())
            directory = path.parent / 'douyin'
            directory.mkdir(parents=True)
            asset = {'page_url': 'https://source.example/article#media', 'kind': 'video',
                     'path': str(root / 'cut.video'), 'brief_source': str(root / 'cut.video'),
                     'source_page': 'https://source.example/article'}
            item = {'id': 'test', 'editorial': {'anchor': {'title': '番茄炒蛋'}, 'brief': {'asset_paths': [asset['path']]}},
                    'chapters': [{'story': {'title': '切番茄'}, 'reports': [{'id': 'r', 'url': asset['source_page']}]}],
                    'media': [asset], 'media_review': {asset['page_url']: 'approved'}}
            write_json(path, {'items': {'douyin': item}})
            with patch('core.news_media.download_story_media', return_value=([], [])) as download:
                result = _prepare_media(path, 'douyin', item, directory)
            download.assert_not_called()
            self.assertEqual(result[0]['chapters'], [0])
            self.assertEqual(result[0]['source_page'], asset['source_page'])

    def test_visual_selection_prefers_installed_instruction_model_and_keeps_fallback(self):
        import os

        fallback = VISUAL_MODELS["editing"][1]
        with patch.dict(os.environ, {}, clear=True), patch("core.local_ai.installed_models") as installed:
            installed.return_value = {fallback}
            self.assertEqual(model_for("editing"), fallback)
            installed.return_value.add("qwen3-vl:4b-instruct")
            self.assertEqual(model_for("editing"), "qwen3-vl:4b-instruct")
            os.environ["CONTENT_AI_EDIT_MODEL"] = "custom-visual"
            self.assertEqual(model_for("editing"), "custom-visual")

    def test_bundled_text_model_is_used_when_the_default_is_missing(self):
        import os

        fallback = VISUAL_MODELS["design"][1]
        with patch.dict(os.environ, {}, clear=True), patch("core.local_ai.installed_models") as installed:
            installed.return_value = {fallback}
            self.assertEqual(model_for("writing"), fallback)
            installed.return_value.add(DEFAULT_MODEL)
            self.assertEqual(model_for("writing"), DEFAULT_MODEL)
            os.environ["CONTENT_AI_MODEL"] = "chosen-model"
            self.assertEqual(model_for("writing"), "chosen-model")

    def test_xiaohongshu_video_uses_only_selected_inputs_and_preserves_copy(self):
        report = {
            "title": "周末图书馆延时开放",
            "summary": "这篇报道介绍图书馆周六延长开放的情况，并解释了服务安排。" * 20,
            "source": "馆方公告",
            "published": NOW.isoformat(),
            "url": "https://example.com/library",
        }
        key = story_key(report)
        snapshot = {"articles": [report], "ranks": {}}
        day = NOW.date()
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot):
            photo = root / "selected.jpg"
            Image.new("RGB", (40, 30), "navy").save(photo)
            payload = {
                "anchor": {"kind": "article", "id": key}, "source_ids": [key], "target_seconds": 60,
                "brief": {"focus_id": key, "focus_quote": "介绍图书馆周六延长开放的情况",
                          "asset_paths": [str(photo)], "notes": ["说明适合哪些读者"]},
            }
            first = run_stage("editorial_create", day, root, "xiaohongshu", payload, now=NOW)
            self.assertEqual(first["target_seconds"], 60)
            self.assertEqual(first["editorial"]["brief"]["focus_quote"], payload["brief"]["focus_quote"])
            asset, = usable_media(first)
            self.assertEqual(asset["brief_asset_index"], 0)
            self.assertTrue(Path(asset["path"]).is_file())
            self.assertNotEqual(Path(asset["path"]), photo)
            self.assertEqual(run_stage("editorial_create", day, root, "xiaohongshu", payload, now=NOW)["id"], first["id"])
            photo.unlink()
            payload["brief"]["notes"] = ["重点讲周末时段"]
            second = run_stage("editorial_create", day, root, "xiaohongshu", payload, now=NOW)
            self.assertNotEqual(second["id"], first["id"])
            self.assertEqual(read_edition(edition_path(root, day))['previous_items']['xiaohongshu'][-1],first)
            self.assertEqual(usable_media(second)[0]["path"], asset["path"])
            payload["brief"]["asset_paths"] = []
            third = run_stage("editorial_create", day, root, "xiaohongshu", payload, now=NOW)
            self.assertEqual(usable_media(third), [])
            self.assertEqual(read_edition(edition_path(root, day))["items"]["xiaohongshu"]["id"], third["id"])
            payload["brief"]["focus_quote"] = "这里没有出现在原文里"
            with self.assertRaisesRegex(ValueError, "摘录"):
                run_stage("editorial_create", day, root, "xiaohongshu", payload, now=NOW)
