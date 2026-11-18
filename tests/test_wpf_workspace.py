import csv
import datetime as dt
import hashlib
import json
import unittest
from unittest.mock import patch

from core.intake import REFRESH_MINUTES, refresh_intake
from core.workflow import edition_path, write_json
from core import wpf_workspace
from tests.test_dashboard_intake import board, report
from tests.test_works_library import save, scratch_dir


NOW = dt.datetime(2026, 11, 9, 13).astimezone()
TODAY = NOW.date()
OLDER_DAY = TODAY - dt.timedelta(days=1)


class WorkspaceTest(unittest.TestCase):
    def call(self, stage, output, payload=None):
        return wpf_workspace.workspace_stage(stage, OLDER_DAY, output, None, payload, now=NOW)

    def test_snapshot_keeps_local_today_separate_from_selected_day(self):
        with scratch_dir() as output:
            title = "本地人工智能研究有新进展"
            source = {**report(title, NOW), "summary": "一段原始报道正文。" * 40}
            with patch("core.intake.collect_news", return_value=([source], [])), \
                    patch("core.intake.fetch_rank", return_value=board(NOW, title)):
                refresh_intake(output, NOW)

            snapshot = self.call("workspace_snapshot", output)
            self.assertEqual(snapshot["date"], TODAY.isoformat())
            self.assertEqual(snapshot["day"], OLDER_DAY.isoformat())
            article = snapshot["articles"][0]
            self.assertEqual(article["title"], title)
            self.assertEqual(len(article["id"]), 20)
            matched = snapshot["ranks"]["douyin"]["entries"][0]
            self.assertEqual(matched["article"]["id"], article["id"])
            self.assertTrue(matched["has_report"])

            compact = self.call("workspace_snapshot", output, {"summary_only": True})
            self.assertEqual(compact["today"]["articles"][0]["id"], article["id"])
            self.assertEqual(compact["today"]["rank_counts"]["douyin"], 1)
            self.assertFalse(compact.get("articles"))
            self.assertFalse(compact.get("ranks"))
            self.assertFalse(compact.get("editions"))

    def test_collection_refreshes_only_when_due_or_forced(self):
        with scratch_dir() as output, \
                patch("core.intake.collect_news", return_value=([], [])), \
                patch("core.intake.fetch_rank", return_value=board(NOW)), \
                patch("core.intake.supplement_hotspot_reports"):
            read_only = self.call("workspace_collection", output, {"refresh": "never"})
            self.assertIsNone(read_only["checked"])
            self.assertFalse(read_only["refreshed"])
            fresh = self.call("workspace_collection", output)
            self.assertTrue(fresh["refreshed"])
            self.assertEqual(fresh["checked"], NOW.timestamp())
            self.assertFalse(self.call("workspace_collection", output)["refreshed"])
            self.assertTrue(self.call("workspace_collection", output, {"refresh": "force"})["refreshed"])
            later = NOW + dt.timedelta(minutes=REFRESH_MINUTES)
            self.assertTrue(wpf_workspace.workspace_stage(
                "workspace_collection", TODAY, output, None, None, now=later)["refreshed"])

    def test_existing_account_metrics_keys_and_today_csv_are_preserved(self):
        with scratch_dir() as root:
            output = root / "output"
            key = hashlib.sha256("douyin/browser/default".encode()).hexdigest()
            initial = self.call("workspace_snapshot", output)["accounts"][0]
            self.assertEqual((initial["key"], initial["label"], initial["profile_id"]),
                             (key, "抖音主账号", "default"))
            self.call("workspace_profile_save", output, {"label": "城市观察", "profile_id": "default"})
            csv_path = root / "今日.csv"
            self.call("workspace_metrics_template", output, {"path": str(csv_path)})
            with csv_path.open("a", encoding="utf-8", newline="") as file:
                csv.writer(file).writerow(("抖音", "城市观察", TODAY, 0, 123, "", 7, -2))

            response = self.call("workspace_metrics_import", output, {"path": str(csv_path)})

            self.assertEqual(response["imported"], 1)
            self.assertEqual(response["accounts"][0]["metrics"], [0, 123, None, 7, -2])
            saved = json.loads((root / "accounts" / "daily-metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, {"date": TODAY.isoformat(), "rows": {key: [0, 123, None, 7, -2]}})

    def test_edition_and_works_use_saved_and_relocated_files(self):
        with scratch_dir() as output:
            edition = edition_path(output, OLDER_DAY)
            self.assertEqual(self.call("workspace_edition", output)["items"], {})
            self.assertEqual(self.call("workspace_works", output), {"albums": [], "recycled": []})
            video = edition.parent / "douyin" / "final.mp4"
            video.parent.mkdir(parents=True)
            video.touch()
            write_json(edition, {"date": OLDER_DAY.isoformat(), "items": {"douyin": {
                "story": {"title": "旧选题"}, "package": {"video": r"C:\old\output\2026-11-08\douyin\final.mp4"},
            }}})
            self.assertEqual(self.call("workspace_edition", output)["items"]["douyin"]["story"]["title"], "旧选题")
            albums = self.call("workspace_works", output)["albums"]
            self.assertEqual(albums[0]["works"][0]["package"]["video"], str(video.resolve()))
            self.assertTrue(albums[0]["works"][0]["editable"])

    def test_recycle_persists_per_work_without_touching_shared_source_files(self):
        with scratch_dir() as output:
            batch = "copywriting/20261108-a"
            document = save(output, f"{batch}/xiaohongshu.md")
            picture = save(output, f"{batch}/shared.png")
            source = save(output, f"{batch}/batch.json", {"topic": "同一选题", "results": {
                "xiaohongshu": {"file": document.name, "drafts": [
                    {"title": "同名作品", "script": text, "pictures": [{"file": picture.name}]}
                    for text in ["第一篇", "第二篇"]
                ]},
            }})
            video = save(output, "library/bilibili/video_bilibili.mp4")
            save(output, "library/manifest.json", {"title": "同一选题", "platforms": {
                "bilibili": {"video": str(video)},
            }})
            initial = self.call("workspace_works", output)
            rows = initial["albums"][0]["works"]
            note = next(row for row in rows if (row["draft"] or {}).get("script") == "第一篇")
            film = next(row for row in rows if row["kind"] == "video")
            self.assertEqual(len({row.get("id") for row in rows}), 3)

            self.call("workspace_work_recycle", output, {"id": note["id"], "restore": False})
            self.call("workspace_work_recycle", output, {"id": film["id"], "restore": False})
            persisted = self.call("workspace_works", output)
            self.assertEqual({row["id"] for row in persisted["recycled"]}, {note["id"], film["id"]})
            self.assertEqual([row["draft"]["script"] for row in persisted["albums"][0]["works"]], ["第二篇"])
            self.assertTrue(all(path.is_file() for path in [source, document, picture, video]))

            restored = self.call("workspace_work_recycle", output, {"id": note["id"], "restore": True})
            self.assertEqual({row["id"] for row in restored["albums"][0]["works"]},
                             {row["id"] for row in rows} - {film["id"]})
            with self.assertRaises(ValueError):
                self.call("workspace_work_recycle", output, {"id": "../batch.json", "restore": False})

    def test_empty_recycle_bin_hides_only_confirmed_recycled_records(self):
        with scratch_dir() as output:
            video = save(output, "library/bilibili/video_bilibili.mp4")
            manifest = save(output, "library/manifest.json", {"title": "成片", "platforms": {
                "bilibili": {"video": str(video)},
            }})
            work_id = self.call("workspace_works", output)["albums"][0]["works"][0]["id"]
            with self.assertRaisesRegex(ValueError, "回收站"):
                self.call("workspace_work_empty", output, {"ids": [work_id]})
            self.call("workspace_work_recycle", output, {"id": work_id, "restore": False})
            self.assertEqual(self.call("workspace_work_empty", output, {"ids": [work_id]})["recycled"], [])
            self.assertEqual(self.call("workspace_works", output), {"albums": [], "recycled": []})
            self.assertTrue(video.is_file() and manifest.is_file())
            with self.assertRaisesRegex(ValueError, "从回收站清空"):
                self.call("workspace_work_recycle", output, {"id": work_id, "restore": True})

    def test_matrix_combines_my_matching_account_without_merging_a_colleague(self):
        with scratch_dir() as root:
            output = root / "output"
            self.call("workspace_profile_save", output, {"label": "城市号", "profile_id": "default"})
            with patch("core.wpf_team._load") as load:
                load.return_value.snapshot.return_value = {"member": {"name": "我方"}, "accounts": [
                    {"id": "a" * 32, "platform": "douyin", "label": "城市号", "owner": "我方"},
                    {"id": "b" * 32, "platform": "douyin", "label": "城市号", "owner": "同事"},
                ]}
                response = self.call("workspace_account_matrix", output, {"include_team": True})
            self.assertEqual(len(response["accounts"]), 2)
            mine, theirs = response["accounts"]
            self.assertEqual((mine["profile_id"], mine["owner"], mine["shared"]), ("default", "我方", True))
            self.assertEqual((theirs.get("profile_id"), theirs["owner"]), (None, "同事"))

    def test_matrix_registers_other_platforms_locally_and_orders_them_by_type(self):
        with scratch_dir() as root:
            output = root / "output"
            bilibili = self.call("workspace_matrix_account_save", output, {"platform": "bilibili", "label": "频道"})
            xiaohongshu = self.call("workspace_matrix_account_save", output, {"platform": "xiaohongshu", "label": "旅行号"})
            self.call("workspace_matrix_account_save", output, {"platform": "wechat_channels", "label": "视频号"})
            self.call("workspace_matrix_account_save", output, {
                "id": bilibili["id"], "platform": "bilibili", "label": "新频道",
            })
            response = self.call("workspace_account_matrix", output, {"include_team": False})
            accounts = response["accounts"]
            self.assertEqual([row["platform"] for row in accounts],
                             ["douyin", "wechat_channels", "xiaohongshu", "bilibili"])
            self.assertEqual(accounts[-1]["label"], "新频道")
            self.assertEqual(len({row["category"] for row in accounts}), 3)
            self.assertTrue(accounts[-1]["can_share"])
            self.assertFalse(accounts[1]["can_share"])
            self.assertEqual(accounts[2]["profile_id"], xiaohongshu["id"])
            self.assertTrue(accounts[2]["can_publish"])
            self.assertFalse(any(row.get("profile_id") or row.get("can_publish") for row in (accounts[1], accounts[3])))
            with self.assertRaises(ValueError):
                self.call("workspace_matrix_account_save", output, {"platform": "xiaohongshu", "label": "旅行号"})
