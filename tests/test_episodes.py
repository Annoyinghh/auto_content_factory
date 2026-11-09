import unittest
import datetime as dt
from email.utils import format_datetime
from unittest.mock import patch

from core.editing import render_case, suggest_cut, validate_cut
from core.publisher import package_case_for_all_platforms
from test_daily import scratch_dir


class EpisodeTests(unittest.TestCase):
    def test_chinese_magnitude_and_approximate_age_match_the_same_evidence(self):
        from core.episodes import _numbers

        self.assertEqual(_numbers("距今五亿多年的岩层"), {"500000000"})
        self.assertEqual(_numbers("距今 5 亿余年的岩层"), {"500000000"})
        self.assertEqual(_numbers("设备额定 1.2 万米"), {"12000"})

    def test_manual_selection_keeps_exact_topics_and_archives_previous_work(self):
        from core.workflow import edition_path, read_edition, run_stage, write_json

        now = dt.datetime.now(dt.timezone.utc)
        reports = [{"title": f"展会报道{i}完整信息", "url": f"https://example.com/{i}", "source": "测试媒体",
                    "summary": "正文可核对。" * 70, "published": now.isoformat()} for i in range(5)]
        board = {"published": now.timestamp(), "entries": [
            {"rank": i + 1, "title": report["title"], "url": report["url"], "heat": "10万"}
            for i, report in enumerate(reports)]}
        snapshot = {"articles": reports, "ranks": {"bilibili": board}}
        wanted = [reports[i]["title"] for i in (3, 0, 2)]
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot):
            path = edition_path(root, now.date())
            path.parent.mkdir(parents=True)
            previous = {"story": {"title": "上次成片"}, "package": {"video": "old.mp4"}}
            write_json(path, {"items": {"bilibili": previous}})
            item = run_stage("select_episode", now.date(), root, "bilibili", wanted, now=now)
            self.assertEqual([ch["story"]["title"] for ch in item["chapters"]], wanted)
            self.assertEqual(run_stage("select_episode", now.date(), root, "bilibili", wanted, now=now), item)
            with self.assertRaisesRegex(RuntimeError, "榜单已更新"):
                run_stage("select_episode", now.date(), root, "bilibili", [*wanted[:2], "已消失"], now=now)
            self.assertEqual(read_edition(path)["previous_items"]["bilibili"], [previous])

    def test_semantic_plan_keeps_successful_chapter_when_model_interrupts(self):
        chapters = [{"story": {"title": f"资讯 {i}", "summary": "详细报道内容。" * 60,
                                "source": "测试媒体", "url": f"https://example.com/{i}"}}
                    for i in range(3)]
        item = {"story": {"title": "今日资讯"}, "chapters": chapters, "selected_hotspots": ["a", "b", "c"],
                "media": [], "target_seconds": 120}
        plan = {"throughline": "观察共同的变化", "opening": "先提问", "closing": "回看证据",
                "beats": [{"chapter": i, "bridge": "按顺序换题"} for i in (2, 0, 1)]}
        shot = {"title": "已证实细节", "text": "已核实的细节。" * 6, "media": "", "zoom": False, "motion": "pull"}
        response = {"scenes": [shot] * 5}
        with scratch_dir() as root:
            with patch("core.episodes.chat_json", side_effect=[plan, response, {"fixes": []}, RuntimeError("interrupt")]):
                with self.assertRaisesRegex(RuntimeError, "interrupt"):
                    suggest_cut(item, "bilibili", work=root, references=["B站写法"])
            with patch("core.episodes.chat_json", side_effect=[response, {"fixes": []}, response, {"fixes": []}]) as resumed:
                item["edit"] = suggest_cut(item, "bilibili", work=root, references=["B站写法"])
            self.assertEqual(resumed.call_count, 4)
        self.assertEqual([start["title"] for start in item["edit"]["chapter_starts"]], ["资讯 2", "资讯 0", "资讯 1"])
        self.assertEqual(item["edit"]["editorial_plan"], plan)
        self.assertTrue(all(scene["motion"] == "pull" for scene in render_case(item, "bilibili")["scenes"]))

    def test_editor_rejects_invented_number_after_fact_review(self):
        from core.episodes import _review_chapter

        story = {"title": "比赛打平", "summary": "两队最终比分为4比4。"}
        cut = {"scenes": [{"title": "比赛", "text": "最后第99分钟打进扳平球。"}]}
        with patch("core.episodes.chat_json", return_value={"fixes": []}):
            with self.assertRaisesRegex(RuntimeError, "99"):
                _review_chapter(cut, story)
        correction = {"scene": 0, "title": "比分", "text": "两队最终比分为4比4。"}
        with patch("core.episodes.chat_json", return_value={"fixes": [correction]}):
            self.assertEqual(_review_chapter(cut, story)["scenes"][0]["text"], correction["text"])
        story = {"title": "钻井报告", "summary": "完钻井深10910米。"}
        cut = {"scenes": [{"title": "井深", "text": "井深一万零九百米。"}]}
        with patch("core.episodes.chat_json", return_value={"fixes": []}):
            with self.assertRaisesRegex(RuntimeError, "10900"):
                _review_chapter(cut, story)

    def test_reader_uses_rendered_scene_offsets_and_chapter_sources(self):
        from core.reading import build_pages

        case = {"scenes": [{"text": text} for text in ("开场说明。", "补充背景。", "第二章口播。")],
                "chapter_starts": [{"scene": 0, "title": "第一章"}, {"scene": 2, "title": "第二章"}],
                "news": {"sources": [{"title": "第二章", "source": "测试媒体", "url": "https://example.com/report"}]}}
        audio = {"scene_durations": [1.25, 2.125, 3.0], "total_duration": 6.375}
        with scratch_dir() as root:
            pages = build_pages(case, audio, root / "video.mp4")
        self.assertEqual([page["start_ms"] for page in pages], [0, 3375])
        self.assertEqual(pages[0]["excerpt"], "开场说明。 补充背景。")
        self.assertEqual(pages[1]["source_url"], "https://example.com/report")
        self.assertIsNone(pages[1]["frame"])

    def test_hotspot_search_upgrades_a_headline_with_public_article_text(self):
        from requests import Response
        from core.intake import _search_reports, has_report

        now = dt.datetime.now(dt.timezone.utc)
        title = "成都蓉城致歉"
        feed = f"<rss><channel><item><title>{title}</title><link>https://www.thepaper.cn/newsDetail_test</link>" \
               f"<pubDate>{format_datetime(now)}</pubDate></item></channel></rss>"
        article = "<article>" + "".join(f"<p>第{i}段，{('报道正文详细说明了现场和后续安排。' * 4)}</p>" for i in range(7)) + "</article>"

        def response(url, **kwargs):
            result = Response()
            result.status_code = 200
            result.encoding = "utf-8"
            result.headers["Content-Type"] = "text/html; charset=utf-8"
            result._content = (feed if "bing.com" in url else article).encode("utf-8")
            result._content_consumed = True
            return result

        with patch("requests.get", side_effect=response), \
                patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            reports = _search_reports({"title": title}, now)
        self.assertEqual(len(reports), 1)
        self.assertTrue(has_report(reports[0]))
        self.assertIn("后续安排", reports[0]["summary"])

    def test_episode_selection_preserves_previous_short_video_and_uses_distinct_news(self):
        from core.workflow import edition_path, run_stage, write_json
        now = dt.datetime.now().astimezone()
        stories = [{"title": f"不同事件{i}新进展", "url": f"https://example.com/{i}", "summary": "已披露正文。" * 90,
                    "source": "测试媒体", "published": now.isoformat()} for i in range(6)]
        snapshot = {"articles": stories, "ranks": {"bilibili": {"published": now.timestamp(), "entries": [
            {"rank": i + 1, "title": story["title"], "url": story["url"], "heat": "10万"}
            for i, story in enumerate(stories)]}}, "errors": [], "checked": now.timestamp()}
        with scratch_dir() as output:
            path = edition_path(output, now.date()); path.parent.mkdir(parents=True)
            previous = {"story": {"title": "旧短视频"}, "package": {"video": "previous.mp4"}}
            write_json(path, {"items": {"bilibili": previous}})
            with patch("core.intake.read_intake", return_value=snapshot):
                edition = run_stage("queue_episodes", now.date(), output, now=now)
            self.assertEqual(edition["previous_items"]["bilibili"], [previous])
            self.assertEqual(len(edition["items"]["bilibili"]["chapters"]), 6)
            self.assertEqual(edition["items"]["bilibili"]["target_seconds"], 720)

    def test_chapters_survive_ai_generation_and_short_shot_split(self):
        chapters = [{"story": {"title": f"资讯 {i}", "summary": "已核实的报道正文。" * 80,
                                "source": "测试媒体", "url": f"https://example.com/{i}"},
                     "hotspot": {"rank": i + 1}} for i in range(6)]
        item = {"story": {"title": "今日资讯"}, "chapters": chapters, "media": [], "target_seconds": 720}
        shot = {"title": "已披露的细节", "text": "依据原报道介绍已经核实的情况。" * 8, "media": "", "zoom": False}
        with patch("core.episodes.chat_json", return_value={"scenes": [shot] * 6}) as ai:
            item["edit"] = suggest_cut(item, "bilibili")
        self.assertEqual(ai.call_count, 6)
        self.assertEqual(len(item["edit"]["scenes"]), 36)
        validate_cut(item["edit"], [], episode=True)
        case = render_case(item, "bilibili")
        self.assertEqual(len(case["news"]["sources"]), 6)
        self.assertEqual(len(case["chapter_starts"]), 6)
        self.assertGreater(len(case["scenes"]), 36)
        self.assertEqual({shot["layout"] for shot in case["scenes"]}, {"chapter", "brief", "detail"})
        self.assertTrue(all(case["scenes"][start["scene"]]["layout"] == "chapter" for start in case["chapter_starts"]))
        self.assertEqual("".join(row["text"] for row in case["scenes"]),
                         "".join(row["text"] for row in item["edit"]["scenes"]))

    def test_duration_is_checked_before_expensive_video_render(self):
        with scratch_dir() as root, patch("core.publisher.generate_voice_and_subtitles", return_value={"total_duration": 40}), \
                patch("core.publisher.render_platform_video") as render:
            with self.assertRaisesRegex(RuntimeError, "10–15"):
                package_case_for_all_platforms({"id": "episode", "title": "资讯", "target_seconds": 720},
                                               ["bilibili"], output_root=root)
            render.assert_not_called()
