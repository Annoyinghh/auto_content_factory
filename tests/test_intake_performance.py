import datetime as dt
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from core.intake import _match_score, match_hotspots
from desktop import Desk


class IntakePerformanceTest(unittest.TestCase):
    def test_matching_skips_unrelated_articles_and_preserves_first_tie(self):
        now = dt.datetime.now(dt.timezone.utc)
        title = "新品发布科技品牌年度发布会"
        first = {"title": title, "summary": "报道" * 200, "id": "first"}
        second = {**first, "id": "second"}
        articles = [{"title": f"国际贸易出口数据{i}"} for i in range(100)] + [first, second]
        snapshot = {"articles": articles, "ranks": {
            "douyin": {"published": now.timestamp(), "entries": [{"title": title}]},
        }}

        with patch("core.intake._match_score", wraps=_match_score) as score:
            rows = match_hotspots(snapshot, now)

        self.assertIs(rows["douyin"][0]["article"], first)
        self.assertEqual(score.call_count, 2)

    def test_slow_collection_read_does_not_block_the_ui_thread(self):
        desk = Desk.__new__(Desk)
        desk.jobs = queue.Queue()
        desk.intake_request = desk.intake_applied = 0
        desk.intake = Mock()
        desk.update_dashboard = Mock()
        entered, release = threading.Event(), threading.Event()
        snapshot = {"articles": [], "ranks": {}, "checked": None}

        def read(_):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("the caller blocked on the database read")
            return snapshot

        with patch("desktop.read_intake", side_effect=read):
            try:
                desk.refresh_intake()
                self.assertTrue(entered.wait(2))
                desk.intake.update.assert_not_called()
            finally:
                release.set()
            callback, result, error = desk.jobs.get(timeout=2)
            self.assertIsNone(error)
            callback(result, error)

        desk.intake.update.assert_called_once_with(snapshot, {})
        desk.update_dashboard.assert_called_once()
