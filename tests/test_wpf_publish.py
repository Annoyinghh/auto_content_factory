import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from core.matrix_accounts import save_local_account
from core.workflow import edition_path, write_json
from core.wpf_publish import _identity_key, publish_stage
from core.wpf_workspace import workspace_stage
from test_daily import scratch_dir


class WpfPublishTests(unittest.TestCase):
    def test_xiaohongshu_uses_registered_personal_account_for_video_and_note(self):
        with scratch_dir() as root, patch("core.wpf_publish.DATA_DIR", root), \
                patch("core.wpf_publish.account_hint", side_effect=AssertionError("personal posting must not read OAuth")), \
                patch("core.wpf_publish.subprocess.Popen") as launch, \
                patch("core.browser_post.webbrowser.open") as browser:
            # Run against the real publication and workspace routing with a stand-in window.
            process = launch.return_value
            process.stdin = MagicMock()
            process.poll.return_value = 0
            process.wait.return_value = 0
            helper = root / "build/post_panel/PostingPanel.exe"
            helper.parent.mkdir(parents=True)
            helper.touch()
            extension = root / "browser_extension"
            extension.mkdir()
            (extension / "manifest.json").touch()
            with patch("core.wpf_publish.BASE_DIR", root), patch.dict("os.environ", {"ACF_STUDIO_ROOT": str(root)}):
                day, output = "2026-11-09", root / "output"
                account = save_local_account(root, "xiaohongshu", "旅行号")
                other = save_local_account(root, "xiaohongshu", "读书号")
                request = {"profile_id": account["id"], "owner": 100}
                video = output / "video.mp4"
                output.mkdir()
                video.write_bytes(b"reviewed video")
                edition = edition_path(output, day)
                edition.parent.mkdir(parents=True)
                write_json(edition, {"items": {"xiaohongshu": {"package": {
                    "video": str(video), "title": "笔记标题", "caption": "笔记标题\n视频文案",
                }}}})
                listing = publish_stage("publish_list", day, output, None, {})
                self.assertTrue(listing["rows"][0]["can_fill"])
                self.assertEqual(listing["personal_profiles"]["xiaohongshu"][account["id"]], "旅行号")
                self.assertTrue(publish_stage("publish_personal_login", day, output, "xiaohongshu", request))
                first_profile = launch.call_args.args[0][2]
                self.assertTrue(publish_stage("publish_personal_login", day, output, "xiaohongshu",
                                              {**request, "profile_id": other["id"]}))
                self.assertNotEqual(launch.call_args.args[0][2], first_profile)
                listing = publish_stage("publish_list", day, output, None, {})
                self.assertEqual(listing["preferred_profiles"]["xiaohongshu"], other["id"])
                result = publish_stage("publish_prepare", day, output, "xiaohongshu", {**request, "video": str(video)})
                self.assertEqual(result, {"status": "closed", "account": "旅行号"})
                self.assertEqual(launch.call_args.args[0][2], first_profile)
                message = process.stdin.__enter__.return_value.write.call_args.args[0].decode("ascii").splitlines()
                url = urlsplit(message[0])
                self.assertEqual((url.hostname, url.path), ("creator.xiaohongshu.com", "/publish/publish"))
                self.assertEqual(parse_qs(url.query)["target"], ["video"])
                self.assertRegex(url.fragment, r"^acf-post=\d+\.[a-f0-9]{64}&panel=1$")
                self.assertEqual(json.loads(message[1])["body"], "视频文案")

                batch = output / "copywriting/20261109-a"
                batch.mkdir(parents=True)
                picture = batch / "cover.jpg"
                picture.write_bytes(b"picture")
                write_json(batch / "batch.json", {"topic": "旅行", "results": {"xiaohongshu": {"drafts": [
                    {"title": "图文笔记", "script": "笔记正文", "pictures": [{"file": "cover.jpg"}]},
                ]}}})
                albums = workspace_stage("workspace_works", day, output, None, {})["albums"]
                note = next(work for album in albums for work in album["works"] if work["kind"] == "image")
                note_request = {**request, "id": note["id"]}
                listing = publish_stage("publish_list", day, output, None, {"note_id": note["id"]})
                self.assertEqual(listing["preferred_profiles"]["xiaohongshu"], account["id"])
                self.assertEqual([row["title"] for row in listing["rows"] if row.get("note_id") == note["id"]], ["图文笔记"])
                self.assertEqual(publish_stage("publish_personal_note", day, output, "xiaohongshu", note_request),
                                 {"status": "closed", "account": "旅行号"})
                message = process.stdin.__enter__.return_value.write.call_args.args[0].decode("ascii").splitlines()
                self.assertEqual(parse_qs(urlsplit(message[0]).query)["target"], ["image"])
                self.assertRegex(urlsplit(message[0]).fragment, r"^acf-post=\d+\.[a-f0-9]{64}&panel=1$")
                self.assertEqual(json.loads(message[1]), {"title": "图文笔记", "body": "笔记正文", "files": [str(picture)]})
                workspace_stage("workspace_work_recycle", day, output, None, {"id": note["id"], "restore": False})
                with self.assertRaisesRegex(ValueError, "找不到"):
                    publish_stage("publish_personal_note", day, output, "xiaohongshu", note_request)
                browser.assert_not_called()

    def test_creator_pagination_is_bound_to_the_account_that_supplied_the_cursor(self):
        with scratch_dir() as root, patch("core.wpf_publish.DATA_DIR", root), \
                patch("core.wpf_publish.account_hint", return_value=("测试账号", "active-identity")), \
                patch("core.wpf_publish.run_stage", return_value={"items": [], "next": 8}) as videos:
            request = {"cursor": 7, "identity_key": _identity_key("old-identity")}
            result = publish_stage("publish_platform_works", "2026-11-09", root / "output", "douyin", request)
            self.assertEqual(videos.call_args.args[4], {"cursor": 0, "expected_account": "active-identity"})
            self.assertNotIn("active-identity", str(result))
            request["identity_key"] = result["identity_key"]
            publish_stage("publish_platform_works", "2026-11-09", root / "output", "douyin", request)
            self.assertEqual(videos.call_args.args[4]["cursor"], 7)

    def test_creator_list_discards_a_response_when_the_account_changes_mid_request(self):
        with scratch_dir() as root, patch("core.wpf_publish.DATA_DIR", root), \
                patch("core.wpf_publish.account_hint", side_effect=[("账号 A", "a"), ("账号 B", "b")]), \
                patch("core.wpf_publish.run_stage", return_value={"items": [], "next": None}):
            with self.assertRaisesRegex(RuntimeError, "账号已切换"):
                publish_stage("publish_platform_works", "2026-11-09", root / "output", "bilibili", {})
