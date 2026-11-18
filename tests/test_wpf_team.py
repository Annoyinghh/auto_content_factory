import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.wpf_team import team_stage
from core.workflow import edition_path, write_json
from tests.test_daily import scratch_dir


class WpfTeamTest(unittest.TestCase):
    def test_only_the_previewed_local_release_is_uploaded(self):
        day, platform = "2026-11-09", "douyin"
        with scratch_dir() as root:
            output = root / "output"
            path = edition_path(output, day)
            path.parent.mkdir(parents=True)
            video = path.parent / "video.mp4"
            video.write_bytes(b"local release")
            package = {"title": "样片", "caption": "已确认的配文", "video": str(video)}
            item = {"id": "one", "package": package, "edit_revision": "one", "package_revision": "one"}
            write_json(path, {"items": {platform: item}})
            with patch("core.wpf_team.TeamClient.load") as load:
                preview = team_stage("team_share_preview", day, output, platform, {})
                self.assertEqual(preview["caption"], package["caption"])
                self.assertNotIn(str(video), json.dumps(preview))
                package["caption"] = "还没确认的配文"
                write_json(path, {"items": {platform: item}})
                with self.assertRaisesRegex(ValueError, "重新确认"):
                    team_stage("team_share", day, output, platform, {"version": preview["version"]})
                load.assert_not_called()
                confirmed = team_stage("team_share_preview", day, output, platform, {})
                client = load.return_value
                client.share.return_value = "a" * 32
                result = team_stage("team_share", day, output, platform, {"version": confirmed["version"]})
                self.assertEqual(result, {"id": "a" * 32})
                client.share.assert_called_once_with(
                    platform, package, previous_id=None, resume_dir=root / "team-cache" / "uploads",
                )

    def test_filled_post_only_marks_an_unambiguous_owned_publication(self):
        platform, title, label = "douyin", "样片", "编辑部"
        item = {"id": "a" * 32, "owner": "本人", "platform": platform, "title": title,
                "account_label": label, "revision": 3, "status": "planned"}
        client = Mock()
        records = [dict(item, owner="同事"), dict(item, status="submitted"), item]
        client.snapshot.return_value = {"member": {"name": "本人"}, "publications": records}
        with patch("core.wpf_team.OperationsClient.load", return_value=client):
            payload = {"title": title, "account_label": label}
            self.assertEqual(team_stage("team_publication_prepared", None, Path("output"), platform, payload),
                             {"changed": True})
            client.set_publication.assert_called_once_with(item["id"], 3, "prepared")
            records.append(dict(item, id="b" * 32))
            client.set_publication.reset_mock()
            self.assertEqual(team_stage("team_publication_prepared", None, Path("output"), platform, payload),
                             {"changed": False})
            client.set_publication.assert_not_called()
