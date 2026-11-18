import unittest
from unittest.mock import patch

from core import workflow
from core.wpf_workspace import workspace_stage
from tests.test_works_library import scratch_dir


DAY = "2026-11-09"


def commons_page(page_id, mime, filename, license_name="CC0"):
    return {"pageid": page_id, "index": page_id, "title": "File:" + filename, "imageinfo": [{
        "mime": mime, "size": 1000, "url": "https://upload.wikimedia.org/" + filename,
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:" + filename,
        "extmetadata": {"Artist": {"value": "Author"}, "LicenseShortName": {"value": license_name},
                        "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/zero/1.0/"}},
    }]}


class MaterialSearchTest(unittest.TestCase):
    def test_specific_recipe_is_not_replaced_by_generic_food_alias(self):
        from core.material_search import _keywords
        with patch("core.local_ai.chat_json", return_value={"query": "tomato scrambled eggs"}):
            term, _ = _keywords("美食教程：番茄炒蛋")
        self.assertEqual(term, "tomato scrambled eggs")

    def test_chinese_topic_is_converted_to_visible_search_subjects(self):
        from core.material_search import _keywords
        with patch("core.local_ai.chat_json", return_value={"query": "scrambled eggs"}) as model:
            term, _ = _keywords("家常炒鸡蛋的做法")
        self.assertEqual(term, "scrambled eggs")
        self.assertEqual(model.call_args.args[1]["topic"], "家常炒鸡蛋的做法")

    def test_keyword_results_keep_requested_type_and_offer_related_term(self):
        with scratch_dir() as output, patch("core.news_media.requests.get") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"query": {"pages": [
                commons_page(1, "image/jpeg", "Computer.jpg"),
                commons_page(2, "video/webm", "Computer.webm"),
                commons_page(3, "image/jpeg", "Restricted.jpg", "CC BY-SA 4.0"),
            ]}}
            result = workspace_stage("workspace_media_search", DAY, output, None, {"query": "计算机", "kind": "image"})
            self.assertEqual([row["page_id"] for row in result["items"]], [1])
            self.assertIn("计算器", result["suggestions"])
            self.assertIn("filetype:bitmap", request.call_args.kwargs["params"]["gsrsearch"])
            self.assertFalse((output / "material-library").exists())

    def test_attach_keeps_source_attribution_and_checks_work_identity(self):
        with scratch_dir() as output:
            path = workflow.edition_path(output, DAY)
            path.parent.mkdir(parents=True)
            asset = {"page_url": "https://commons.wikimedia.org/wiki/File:Computer.jpg", "kind": "image",
                     "title": "Computer", "author": "Author", "license": "CC0", "path": str(output / "photo.jpg")}
            workflow.write_json(path, {"items": {"douyin": {"id": "current", "media": [asset],
                                                            "media_review": {asset["page_url"]: "removed"}}}})
            with patch("core.material_search.save_material", return_value=asset) as save:
                with self.assertRaisesRegex(RuntimeError, "选题已更换"):
                    workflow.run_stage("attach_search_media", DAY, output, "douyin", {"id": "old", "page_id": 1})
                save.assert_not_called()
                workflow.run_stage("attach_search_media", DAY, output, "douyin", {"id": "current", "page_id": 1})
            current = workflow.read_edition(path)["items"]["douyin"]
            self.assertEqual(current["media"], [asset])
            self.assertEqual(current["media_review"][asset["page_url"]], "pending")
