import unittest
from pathlib import Path
from unittest.mock import patch

from core.copywriting import add_case, generate_batch, retrieve_cases
from test_daily import scratch_dir


class CopywritingTest(unittest.TestCase):
    def test_retrieval_prefers_the_right_topic_and_platform(self):
        with scratch_dir() as root:
            add_case(root, "bilibili", "家庭阳台水培香草", "https://www.bilibili.com/video/BV123",
                     "作者提供的播放截图", "园艺 水培 罗勒", "先展示根系，再讲搭建条件。")
            result = retrieve_cases(root, "bilibili", "阳台水培罗勒")
            self.assertEqual(result[0]["title"], "家庭阳台水培香草")
            self.assertTrue(all(case["platform"] == "bilibili" for case in result))
            self.assertTrue(retrieve_cases(root, "xiaohongshu", "穿搭"))
            self.assertTrue(retrieve_cases(root, "douyin", "旅行"))

    def test_batch_keeps_successes_when_one_platform_fails(self):
        variants = [
            {"angle": "通勤实拍", "title": "从通勤开始", "script": "出门前先把水和钥匙放在同一格。", "caption": "通勤实拍"},
            {"angle": "整理习惯", "title": "整理只做一步", "script": "每天回家先清掉包里的临时物件。", "caption": "整理习惯"},
        ]
        with scratch_dir() as root, patch("core.copywriting.chat_json", side_effect=[
            *variants, RuntimeError("本地模型暂不可用"), *variants,
        ]) as chat:
            result = generate_batch(root, "通勤背包怎么整理", "侧袋可以放水杯。", ("xiaohongshu", "douyin", "bilibili"), 2)
            directory = Path(result["directory"])
            self.assertTrue((directory / "xiaohongshu.md").is_file())
            self.assertTrue((directory / "bilibili.md").is_file())
            self.assertFalse((directory / "douyin.md").exists())
            self.assertIn("douyin", result["errors"])
            self.assertIn("参考案例", (directory / "xiaohongshu.md").read_text(encoding="utf-8"))
            self.assertIn("结构参考", chat.call_args_list[0].args[1])

    def test_new_batch_receives_previous_angle_and_title(self):
        first = {"angle": "看根系", "title": "阳台罗勒先看根", "script": "先看看根系。", "caption": "阳台观察"}
        second = {"angle": "选容器", "title": first["title"], "script": "先选能稳住植株的容器。", "caption": "容器选择"}
        with scratch_dir() as root, patch("core.copywriting.chat_json", side_effect=[
            first, second,
        ]) as chat:
            earlier = generate_batch(root, "阳台罗勒", "", ("bilibili",), 1)
            later = generate_batch(root, "阳台罗勒", "", ("bilibili",), 1)
            self.assertNotEqual(earlier["directory"], later["directory"])
            self.assertEqual(later["results"]["bilibili"]["drafts"][0]["title"], f"{first['title']}｜容易忽略的细节")
            self.assertEqual(chat.call_args.args[1]["避免重复"], [first["title"]])
            self.assertNotEqual(chat.call_args_list[0].args[1]["本篇写作任务"], chat.call_args.args[1]["本篇写作任务"])
