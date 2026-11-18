import datetime as dt
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import requests

from core.intake import read_intake, refresh_intake


YESTERDAY = dt.datetime(2026, 11, 6, 23, 55).astimezone()
MORNING = dt.datetime(2026, 11, 7, 0, 5).astimezone()
AFTERNOON = dt.datetime(2026, 11, 7, 14, 5).astimezone()
TOMORROW = dt.datetime(2026, 11, 8, 0, 5).astimezone()


def report(title, published):
    return {"title": title, "summary": "详细报道：本地人工智能研究有新进展。", "source": "测试资讯",
            "url": "https://example.com/report", "published": published.astimezone(dt.timezone.utc).isoformat()}


def board(published, *titles):
    return {"published": published.timestamp(), "source": "公开榜单", "url": "https://example.com/ranks",
            "entries": [{"rank": index, "title": title, "url": f"https://example.com/{index}", "heat": "100"}
                        for index, title in enumerate(titles, 1)]}


class DashboardIntakeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / f"test_output_{uuid4().hex}"
        self.root.mkdir()
        self.addCleanup(self.root.rmdir)
        self.addCleanup((self.root / "collection.sqlite3").unlink, missing_ok=True)

    def test_today_reports_use_the_first_local_intake_not_publication_time(self):
        before = report("昨天已经入库", YESTERDAY)
        after = report("今天收录的昨天报道", YESTERDAY)
        repeat = report("今天收录的昨天报道！", AFTERNOON)
        with patch("core.intake.fetch_rank", return_value=board(MORNING)), \
                patch("core.intake.collect_news", side_effect=[([before], []), ([before, after], []), ([repeat], [])]):
            refresh_intake(self.root, YESTERDAY)
            refresh_intake(self.root, MORNING)
            refresh_intake(self.root, AFTERNOON)
            today = read_intake(self.root, AFTERNOON).get("today", {})

        self.assertEqual([(row["title"], row["collected_at"], row["category"]) for row in today.get("articles", [])],
                         [(after["title"], MORNING.timestamp(), "科技")])

    def test_hotspot_counts_keep_unique_entries_per_platform_across_refreshes(self):
        with patch("core.intake.collect_news", return_value=([], [])), \
                patch("core.intake.fetch_rank", side_effect=lambda platform: board(MORNING, "共同话题", "上午话题")
                      if platform == "douyin" else board(MORNING, "共同话题")):
            refresh_intake(self.root, MORNING)
            with patch("core.intake.fetch_rank", side_effect=lambda platform: board(AFTERNOON, "下午话题", "共同话题！")
                       if platform == "douyin" else board(AFTERNOON, "共同话题")):
                refresh_intake(self.root, AFTERNOON)
            today = read_intake(self.root, AFTERNOON).get("today", {})

        self.assertEqual(today.get("rank_counts"), {"douyin": 3, "bilibili": 1, "xiaohongshu": 1})

    def test_failed_refresh_at_midnight_does_not_count_yesterdays_cached_boards(self):
        story = report("昨天报道", AFTERNOON)
        with patch("core.intake.collect_news", side_effect=[([story], []), ([], [])]), \
                patch("core.intake.fetch_rank", return_value=board(AFTERNOON, "昨天热榜")):
            refresh_intake(self.root, AFTERNOON)
            with patch("core.intake.fetch_rank", side_effect=requests.Timeout):
                snapshot = refresh_intake(self.root, TOMORROW)

        self.assertTrue(snapshot["ranks"]["douyin"]["entries"])
        self.assertEqual(snapshot.get("today"), {"date": "2026-11-08", "articles": [], "rank_counts": {}})
