import csv
import datetime as dt
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from core.account_metrics import import_metrics, read_metrics, write_template


DAY = dt.date(2026, 11, 9)
HEADER = ("平台", "账号", "日期", "发布", "播放或阅读", "点赞", "评论", "新增关注")
ACCOUNTS = {("抖音", "城市观察"): "browser-profile-1", ("B站", "城市观察"): "authorized-account-2"}


class AccountMetricsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / f"test_output_{uuid4().hex}"
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.source = self.root / "input.csv"

    def save_csv(self, *rows):
        with self.source.open("w", encoding="utf-8-sig", newline="") as output:
            csv.writer(output).writerows((HEADER, *rows))

    def test_today_csv_preserves_zero_blank_and_signed_net_followers(self):
        self.save_csv(("抖音", "城市观察", DAY, 0, 123, "", 7, -2), ("B站", "城市观察", DAY, 2, "", 4, "", "+3"))

        self.assertEqual(import_metrics(self.root, self.source, ACCOUNTS, day=DAY), 2)

        self.assertEqual(read_metrics(self.root, day=DAY), {
            "browser-profile-1": (0, 123, None, 7, -2), "authorized-account-2": (2, None, 4, None, 3),
        })
        self.assertEqual(read_metrics(self.root, day=DAY + dt.timedelta(days=1)), {})

    def test_a_bad_row_cannot_partially_update_a_saved_day(self):
        original = ("抖音", "城市观察", DAY, 1, 100, 10, 2, 5)
        self.save_csv(original)
        import_metrics(self.root, self.source, ACCOUNTS, day=DAY)
        change = ("抖音", "城市观察", DAY, 9, 999, 99, 9, 9)
        for invalid in (
            ("抖音", "城市观察", DAY, 4, "", "", "", ""),
            ("小红书", "城市观察", DAY, 4, "", "", "", ""),
            ("B站", "城市观察 ", DAY, 4, "", "", "", ""),
            ("B站", "城市观察", DAY - dt.timedelta(days=1), 4, "", "", "", ""),
            ("B站", "城市观察", DAY, 4, "1,000", "", "", ""),
            ("B站", "城市观察", DAY, 4, "", -2, "", ""),
        ):
            with self.subTest(row=invalid):
                self.save_csv(change, invalid)
                with self.assertRaisesRegex(ValueError, "3"):
                    import_metrics(self.root, self.source, ACCOUNTS, day=DAY)
                self.assertEqual(read_metrics(self.root, day=DAY), {"browser-profile-1": (1, 100, 10, 2, 5)})

    def test_reimport_replaces_the_row_and_preserves_accounts_omitted_from_csv(self):
        self.save_csv(("抖音", "城市观察", DAY, 1, 100, 10, 2, 5), ("B站", "城市观察", DAY, 2, 200, 20, 4, 10))
        import_metrics(self.root, self.source, ACCOUNTS, day=DAY)
        self.save_csv(("抖音", "城市观察", DAY, "", 125, "", "", -1))
        import_metrics(self.root, self.source, ACCOUNTS, day=DAY)

        self.assertEqual(read_metrics(self.root, day=DAY), {
            "browser-profile-1": (None, 125, None, None, -1), "authorized-account-2": (2, 200, 20, 4, 10),
        })

    def test_template_has_the_required_utf8_header_and_no_sample_accounts(self):
        write_template(self.source)

        with self.source.open(encoding="utf-8-sig", newline="") as source:
            self.assertEqual(list(csv.reader(source)), [list(HEADER)])
