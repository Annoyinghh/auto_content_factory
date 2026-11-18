import unittest

from core.douyin_profiles import douyin_profiles, profile_directory, save_profile
from tests.test_daily import scratch_dir


class DouyinProfileTests(unittest.TestCase):
    def test_default_session_is_retained_and_new_accounts_are_isolated(self):
        with scratch_dir() as root:
            original = root / "browser" / "douyin"
            self.assertEqual(profile_directory(root, "default"), original)
            first = save_profile(root, "科技号")
            second = save_profile(root, "生活号")
            save_profile(root, "主号", profile_id="default")

            self.assertEqual(douyin_profiles(root), {"default": "主号", first: "科技号", second: "生活号"})
            self.assertEqual(profile_directory(root, "default"), original)
            self.assertNotEqual(profile_directory(root, first), profile_directory(root, second))
            with self.assertRaises(ValueError):
                save_profile(root, " 科技号 ")
            with self.assertRaises(ValueError):
                profile_directory(root, "../unrelated")
