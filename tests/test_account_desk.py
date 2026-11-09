import tkinter as tk
import unittest
from unittest.mock import patch

from core.workflow import run_stage
from studio_accounts import AccountDesk


class AccountDeskTest(unittest.TestCase):
    def test_delayed_page_cannot_overwrite_newer_view_or_switch_accounts(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.withdraw()
        jobs = []
        account = "account-a"
        names = {"douyin": "抖音", "bilibili": "B站"}
        with patch("studio_accounts.account_hint", side_effect=lambda platform: (account, account)):
            desk = AccountDesk(root, names, lambda action, done: jobs.append(done))
            desk.load()
            for name in ("B站", "抖音"):
                desk.channel.set(name)
                desk.change_platform()
            desk.load()
            jobs[1]({"items": [{"title": "新列表", "status": "", "url": ""}], "next": 20}, None)
            jobs[0]({"items": [], "next": None}, None)
            self.assertEqual(desk.next_page, 20)
            desk.load(desk.next_page)
            account = "account-b"
            jobs[2]({"items": [{"title": "旧账号作品", "status": "", "url": ""}], "next": None}, None)
            self.assertFalse(desk.videos.get_children())
            self.assertIsNone(desk.next_page)
            self.assertIn("账号已切换", desk.status.cget("text"))
            desk.load()
            jobs[3]({"items": [{"title": "账号 B 的作品", "status": "", "url": "https://www.douyin.com/video/1"}],
                     "next": None}, None)
            desk.videos.selection_set(desk.videos.get_children()[0])
            account = "account-c"
            with patch("studio_accounts.webbrowser.open") as open_link:
                desk.open_video()
                open_link.assert_not_called()
            self.assertFalse(desk.videos.get_children())

    def test_worker_checks_expected_account_before_requesting_works(self):
        account = {"identity": "other-account", "open_id": "other-account", "access_token": "token", "client": "client"}
        with (
            patch("core.accounts._active_account", return_value=account),
            patch("core.accounts._settings", return_value=("client", "secret", "", "")),
            patch("core.news_release._douyin_request", return_value={}) as douyin,
            patch("core.news_release._bili_post", return_value={}) as bili,
        ):
            for platform in ("douyin", "bilibili"):
                with self.subTest(platform=platform), self.assertRaisesRegex(RuntimeError, "账号已变更"):
                    run_stage("videos", None, platform=platform, payload={"cursor": 0, "expected_account": "original"})
            douyin.assert_not_called()
            bili.assert_not_called()
