import datetime as dt
import unittest
from unittest.mock import patch

from core.daily_news import story_key
from core.editorial import source_options
from core.workflow import run_stage
from test_daily import scratch_dir


class TopicFlowTests(unittest.TestCase):
    def test_chinese_search_rss_date_does_not_drop_every_result(self):
        from core.daily_news import parse_feed
        xml = ('<rss><channel><item><title>番茄炒蛋的家常做法</title>'
               '<link>https://example.com/recipe</link><pubDate>周日, 28 12月 2025 11:13:00 GMT</pubDate>'
               '<description>图文介绍食材和家常操作方法</description></item></channel></rss>').encode()
        found = parse_feed(xml, self.now, search_results=True, allow_older=True)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['published'], '2025-12-28T11:13:00+00:00')

    def test_explicit_reference_search_keeps_older_material_without_calling_it_current_news(self):
        from core.daily_news import parse_feed
        now = dt.datetime(2026, 11, 10, tzinfo=dt.timezone.utc)
        xml = ('<rss><channel><item><title>番茄炒蛋的家常做法</title>'
               '<link>https://example.com/recipe</link><pubDate>Tue, 10 Nov 2020 08:00:00 GMT</pubDate>'
               '<description>食材与操作步骤</description></item></channel></rss>').encode()
        self.assertEqual(parse_feed(xml, now), [])
        found = parse_feed(xml, now, search_results=True, allow_older=True)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]['published'].startswith('2020-11-10'))

    def test_topic_search_collects_multiple_readable_sources_not_first_only(self):
        from core.intake import _search_reports
        rows = [{"title": f"番茄炒蛋做法{i}", "url": f"https://example.com/{i}", "published": "",
                 "summary": "", "source": "原文"} for i in range(3)]
        def read(row, now, **kwargs):
            self.assertTrue(kwargs.get("allow_older"))
            return {**row, "summary": "可读原文。" * 70, "headline_only": False}
        with patch("requests.get") as get, patch("core.intake.parse_feed", return_value=rows), \
                patch("core.intake._read_report", side_effect=read):
            get.return_value.__enter__.return_value.iter_content.return_value = [b"<rss/>"]
            found = _search_reports({"title": "番茄炒蛋"}, self.now, reference=True)
        self.assertEqual(len(found), 3)
        self.assertTrue(all(row["source_type"] == "reference" for row in found))

    def test_familiar_lifestyle_topic_also_uses_live_search_not_fixed_demo_photos(self):
        from PIL import Image
        from core.note_images import collect_lifestyle_photos
        photo = Image.new("RGB", (1000, 1200))
        with scratch_dir() as root, \
                patch("core.local_ai.chat_json", return_value={"sets": [["subway train", "train seat"]]}), \
                patch("core.material_search._keywords", return_value=("commute", [])), \
                patch("core.note_images.find_note_photo", side_effect=[(photo, {"title": "train", "page_url": "a"}),
                                                                      (photo, {"title": "seat", "page_url": "b"})]) as search, \
                patch("core.news_media.requests.get", side_effect=AssertionError("不能走预选演示图")), \
                patch("core.note_images.describe_photo", return_value="地铁座位"):
            bundle, = collect_lifestyle_photos("下班路上", "", ["地铁通勤"], root)
        self.assertEqual(len(bundle), 2)
        self.assertEqual(search.call_count, 2)

    def test_clear_original_video_is_not_forced_to_low_resolution_to_save_time(self):
        from core.news_media import _video_download
        url = "https://upload.wikimedia.org/wikipedia/commons/a/ab/cooking.webm"
        download, kind = _video_download({}, {"mime": "video/webm", "size": 40_000_000,
                                             "duration": 130, "url": url})
        self.assertEqual(download, url)
        self.assertEqual(kind, "matroska")

    def test_photo_writing_does_not_accept_semantically_unfounded_copy(self):
        from core.copywriting import _generate
        bad = {"title": "朋友家的厨房", "script": "一起做饭的时候，大家就在旁边帮忙。" * 6, "caption": "一起备菜。"}
        with patch("core.copywriting.chat_json", return_value=bad) as writer, \
                patch("core.copywriting._photo_copy_issues", return_value="不同来源不能编成朋友在同一个厨房备菜"):
            with self.assertRaisesRegex(ValueError, "不同来源"):
                _generate("xiaohongshu", "备菜日常", "", [], 1, [], 0, illustrated=True,
                          photo_sets=[[(None, {"title": "户外备菜"}), (None, {"title": "室内厨房"})]])
        self.assertEqual(writer.call_count, 3)
        self.assertIn("待修改稿", writer.call_args.args[1])

    def test_single_concrete_photo_keyword_is_actually_searched(self):
        from core.news_media import find_note_photo
        with scratch_dir() as root, patch("core.news_media.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"query": {"pages": []}}
            self.assertIsNone(find_note_photo("whisk", root / "photo.tmp", set(), sharp=True))
        get.assert_called_once()
        self.assertIn("whisk", get.call_args.kwargs["params"]["gsrsearch"])

    def test_irrelevant_photo_is_replaced_instead_of_aborting_the_whole_note(self):
        from PIL import Image
        from core.note_images import collect_lifestyle_photos
        photo = Image.new("RGB", (1000, 1200), "navy")
        candidates = [(photo, {"title": title, "page_url": title}) for title in ("town", "eggs", "vegetables")]
        with scratch_dir() as root, patch("core.local_ai.chat_json", return_value={"sets": [["scrambled eggs", "cutting vegetables"]]}), \
                patch("core.note_images.find_note_photo", side_effect=candidates), \
                patch("core.note_images.describe_photo", side_effect=[ValueError("不相关"), "炒蛋", "切菜"]):
            bundle, = collect_lifestyle_photos("家常菜", "", ["做菜动作"], root)
        self.assertEqual([credit["title"] for _, credit in bundle], ["炒蛋", "切菜"])

    def setUp(self):
        self.now = dt.datetime.now().astimezone()
        self.topic = "超日常美食教程速来get"
        self.anchor = {"kind": "hotspot", "platform": "xiaohongshu", "title": self.topic}
        self.reports = [{"title": title, "url": f"https://example.com/{i}", "source": "原文",
                         "published": self.now.isoformat(), "summary": "这是可以核对的完整资料。" * 40}
                        for i, title in enumerate(("Meta AI 模型日常使用教程", "超日常美食教程：番茄炒蛋"))]
        self.snapshot = {"articles": self.reports, "ranks": {"xiaohongshu": {
            "entries": [{"title": self.topic, "url": "https://example.com/topic"}]}}}

    def test_recommendations_do_not_confuse_shared_generic_words_with_same_topic(self):
        result = source_options(self.snapshot, self.anchor, self.now, recommend=False, local_rank=True)
        self.assertNotIn(story_key(self.reports[0]), result["recommended"])
        self.assertIn(story_key(self.reports[1]), result["recommended"])
        self.assertEqual(len(result["reports"]), 2)  # Manual library remains selectable.

    def test_collected_hotspot_note_stays_related_even_when_its_title_omits_the_topic(self):
        note={'title':'感谢网友的配方','url':'https://www.xiaohongshu.com/explore/6a4dfa1b000000000702cc44',
              'source':'小红书图文','published':'2026-07-08','summary':'肠粉切块，按步骤蒸好后淋上料汁。'*4,
              'source_platform':'xiaohongshu','source_note_id':'6a4dfa1b000000000702cc44',
              'content_type':'photo_note','source_type':'reference','reference_topics':[self.topic]}
        result=source_options({**self.snapshot,'articles':[*self.reports,note]},self.anchor,self.now,
                              recommend=False,local_rank=True)
        self.assertIn(story_key(note),result['recommended'])
        self.assertNotIn(story_key(self.reports[0]),result['recommended'])

    def test_custom_topic_does_not_have_to_exist_on_rankings(self):
        result = source_options(self.snapshot, {"kind": "custom", "title": "番茄炒蛋"}, self.now,
                                recommend=False, local_rank=True)
        self.assertEqual(result["anchor"]["title"], "番茄炒蛋")

    def test_assembly_without_media_does_not_claim_media_collection_is_complete(self):
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=self.snapshot):
            item = run_stage("editorial_create", self.now.date(), root, "douyin", {
                "anchor": self.anchor, "source_ids": [story_key(self.reports[1])],
                "target_seconds": 60, "brief": {"notes": [], "asset_paths": []}}, now=self.now)
        self.assertFalse(item.get("media_checked", False))
        self.assertFalse(item.get("opening_media_checked", False))

    def test_topic_collection_fetches_selected_topic_and_persists_only_matching_reports(self):
        from core.intake import read_intake
        with scratch_dir() as root, patch("core.intake._search_reports", return_value=self.reports) as search:
            result = run_stage("editorial_collect", self.now.date(), root, "douyin",
                               {"anchor": {"kind": "custom", "title": self.topic}}, now=self.now)
            collected = read_intake(root, self.now)["articles"]
        self.assertEqual(search.call_args.args[0]["title"], self.topic)
        self.assertEqual([r["title"] for r in collected], [self.reports[1]["title"]])
        self.assertEqual(result["collected"], 1)

    def test_xhs_assembly_collects_native_notes_and_exposes_their_photos(self):
        from core.browser_cases import _work,store_browser_reference
        from core.intake import read_intake
        original_read=read_intake
        work=_work({'id':'6a4dfa1b000000000702cc44','type':'normal','title':'感谢网友的配方',
            'desc':'肠粉切块，蒸好之后调制料汁并淋上。'*4,
            'url':'https://www.xiaohongshu.com/explore/6a4dfa1b000000000702cc44',
            'images':[{'url':'https://sns-webpic-qc.xhscdn.com/notes/1040g3sample000000!webp'}]},photo_notes=True)
        def collect(root,platform,payload,**kwargs):
            key=store_browser_reference(root,work,topic=payload['topic'])
            return {'saved':[work['title']],'source_ids':[key]}
        def snapshot(root,now=None):
            return {**self.snapshot,'articles':[*self.reports,*original_read(root,now)['articles']]}
        with scratch_dir() as root, patch('core.intake.read_intake',side_effect=snapshot), \
                patch('core.copywriting.collect_topic_cases',side_effect=collect) as native, \
                patch('core.intake._search_reports') as generic:
            result=run_stage('editorial_collect',self.now.date(),root,'xiaohongshu',{'anchor':self.anchor},now=self.now)
        self.assertEqual(result['collected'],1)
        key=result['searched_ids'][0]
        self.assertIn(key,result['recommended'])
        self.assertTrue(next(r for r in result['reports'] if r['id']==key)['media_candidates'])
        self.assertTrue(native.call_args.kwargs['photo_notes'])
        generic.assert_not_called()

    def test_selected_photos_have_distinct_versions_and_keep_real_provenance(self):
        from PIL import Image
        from core.note_images import selected_note_photos

        with scratch_dir() as root:
            paths = []
            for i, color in enumerate(("red", "green", "blue", "yellow")):
                path = root / f"selected-{i}.jpg"
                Image.new("RGB", (1000, 1200), color).save(path)
                paths.append(str(path))
            with patch("core.note_images.describe_photo", return_value="桌上的饭菜"):
                bundles = selected_note_photos(root, paths, "吃饭", 2)
            self.assertEqual([len(bundle) for bundle in bundles], [2, 2])
            self.assertEqual(len({credit["page_url"] for bundle in bundles for _, credit in bundle}), 4)
            with self.assertRaisesRegex(ValueError, "不同"):
                selected_note_photos(root, paths[:2] * 2, "吃饭", 2)

    def test_low_resolution_selected_image_is_not_silently_upscaled(self):
        from PIL import Image
        from core.note_images import selected_note_photos

        with scratch_dir() as root:
            paths = []
            for i in range(4):
                path = root / f"small-{i}.jpg"
                Image.new("RGB", (100, 120), (i * 60, 0, 0)).save(path)
                paths.append(str(path))
            with self.assertRaisesRegex(ValueError, "清晰"):
                selected_note_photos(root, paths, "吃饭", 2)

    def test_all_cards_cannot_pass_video_quality_gate(self):
        from core.directing import audit_visuals
        draft = {"scenes": [{"media": "", "visual": {"layout": "evidence"}} for _ in range(6)]}
        audit = audit_visuals(draft, [])
        self.assertFalse(audit["ready"])
        self.assertEqual(audit["cards"], 6)
        draft["scenes"][:4] = [{"media": "clip", "visual": {"layout": "footage", "start": 3}} for _ in range(4)]
        audit = audit_visuals(draft, [{"page_url": "clip", "kind": "video"}])
        self.assertTrue(audit["ready"])
        self.assertEqual(audit["video_shots"], 4)

    def test_author_can_make_a_video_from_own_material_without_fake_news_reports(self):
        from core.editorial import confirm_sources
        notes = "下班回家之后，想拍一段给自己做饭的小片子。先把蔬菜洗干净，再切好放在盘子里，最后留一段坐下来吃饭的画面。不要写成新闻播报，也不要编造时间和价格。"
        result = confirm_sources(self.snapshot, {"anchor": {"kind": "custom", "title": "回家做饭"},
            "source_ids": [], "brief": {"notes": [notes], "asset_paths": []}}, self.now)
        self.assertEqual(result["mode"], "original")
        self.assertEqual(result["reports"][0]["source_type"], "author_notes")
        self.assertEqual(result["reports"][0]["summary"], notes)
        self.assertEqual(result["reports"][0]["url"], "")

    def test_empty_topic_and_media_alone_are_not_fabricated_into_a_news_story(self):
        from core.editorial import confirm_sources
        with self.assertRaises(ValueError):
            confirm_sources(self.snapshot, {"anchor": {"kind": "custom", "title": "回家做饭"},
                "source_ids": [], "brief": {"notes": [], "asset_paths": ["clip.mp4"]}}, self.now)

    def test_original_video_does_not_inherit_the_news_roundup_template(self):
        notes = "回家先把蔬菜洗好，然后切好放在盘里。再整理灶台旁边的东西，给自己留出做饭的位置。最后把做好的菜端上桌，坐下来吃饭。"
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=self.snapshot):
            item = run_stage("editorial_create", self.now.date(), root, "douyin", {
                "anchor": {"kind": "custom", "title": "回家做饭"}, "source_ids": [],
                "target_seconds": 60, "brief": {"notes": [notes], "asset_paths": []}}, now=self.now)
        self.assertEqual(item["video_style"]["id"], "original")
        self.assertNotIn("报道", item["story"]["source"])


if __name__ == "__main__":
    unittest.main()
