import asyncio
import datetime as dt
import unittest
from unittest.mock import AsyncMock, patch

from core.daily_news import story_key
from core.workflow import edition_path, read_edition, run_stage
from test_daily import scratch_dir


class SpeechDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_stream_fails_in_time_for_offline_fallback(self):
        from core.audio_engine import _synthesize_text

        async def stalled(_path):
            await asyncio.Event().wait()

        with patch("core.audio_engine.EDGE_SCENE_TIMEOUT", .01, create=True), \
                patch("core.audio_engine.edge_tts.Communicate") as client:
            client.return_value.save = AsyncMock(side_effect=stalled)
            with self.assertRaisesRegex(RuntimeError, "TimeoutError"):
                await asyncio.wait_for(_synthesize_text("测试", "voice", "+0%", "unused", max_retries=1), 2)
            client.return_value.save.assert_awaited_once()


class EditorialTests(unittest.TestCase):
    def test_pronoun_step_receives_earlier_source_steps_for_its_subject(self):
        from core.editorial import _write_batch
        excerpts = {'a':{'source_id':'r','quote':'【1】将蛋液下锅。'},
                    'b':{'source_id':'r','quote':'【2】待其煎至金黄。'}}
        report = {'id':'r','summary':'\n'.join(r['quote'] for r in excerpts.values())}
        replies = [{'title':'下蛋液','text':'将鸡蛋液倒进锅里。'},
                   {'title':'煎蛋','text':'让鸡蛋煎到金黄。'}, {'issues':[]}, {'issues':[]}]
        with patch('core.editorial.chat_json',side_effect=replies) as model:
            _write_batch({'chapter':'蒜香版','required_steps':list(excerpts),'writing_only':True,
                          'chapter_excerpts':excerpts}, {'properties':{'scenes':{'items':{}}}},
                         [report],[],excerpts,2,60)
        self.assertIn('将蛋液下锅',model.call_args_list[1].args[1]['前序操作原文'])
        self.assertEqual(model.call_args.args[1]['chapter_excerpts'],{'a':excerpts['a']})
        self.assertEqual(list(model.call_args.args[1]['scoped_excerpts'].values()),[excerpts['b']])

    def test_short_dish_description_does_not_invent_a_cooking_order(self):
        from core.editorial import _write_batch
        excerpts = {'dish':{'source_id':'r','quote':'是西红柿鸡蛋混炒','instruction':False},
                    'feature':{'source_id':'r','quote':'其特点是简单方便快捷无需后续调味','instruction':False}}
        report = {'id':'r','summary':'\n'.join(row['quote'] for row in excerpts.values())}
        schema = {'properties':{'scenes':{'items':{}}}}
        with patch('core.editorial.chat_json',return_value={'issues':[]}) as model:
            result = _write_batch({'chapter':'混炒版','required_steps':list(excerpts),'writing_only':True,
                                   'earlier_narration':'另一种做法出锅前才加入盐。'},
                                  schema,[report],[],excerpts,2,50)
        self.assertEqual(model.call_count,2)
        self.assertEqual(result['scenes'][0]['text'],'这版是西红柿鸡蛋混炒。')
        self.assertNotIn('先',result['scenes'][0]['text'])
        self.assertNotIn('earlier_narration',model.call_args.args[1])
        self.assertEqual(model.call_args.args[1]['chapter'],'混炒版')

    def test_ordered_tutorial_keeps_every_original_clause_in_small_batches(self):
        from core.editorial import instruction_steps
        source = {'1':{'source_id':'recipe','quote':'【1】先切番茄，再切成小块。'},
                  '2':{'source_id':'recipe','quote':'【2】把鸡蛋放到碗里，加入现切葱花和食用盐，充分搅拌均匀之后再倒入切好的番茄混合，最后下锅。'}}
        steps = instruction_steps(source)
        self.assertGreater(len(steps),len(source))
        for key,row in source.items():
            self.assertEqual(''.join(part['quote'] for identity,part in steps.items() if identity.startswith(key+'.')),row['quote'])
        self.assertTrue(all(row['source_id']=='recipe' for row in steps.values()))
        self.assertIsNone(instruction_steps({'1':source['1'],'2':source['1']}))
        self.assertIsNone(instruction_steps({'1':{'source_id':'news','quote':'今天发布了相关报道。'}}))

    def test_ordered_parts_receive_only_their_steps_and_keep_completed_batches(self):
        from core.editorial import _write_chapter_parts
        excerpts = {str(i):{'source_id':'recipe','quote':f'操作{i}。'} for i in range(8)}
        seen = []
        def write(context, schema, reports, visuals, scoped, count, target, **_options):
            seen.append(context)
            self.assertEqual(context['required_steps'],list(scoped))
            self.assertEqual(count,len(scoped))
            return {'scenes':[{'title':key,'text':row['quote'],'media':'','zoom':False} for key,row in scoped.items()],
                    'source_ids':['recipe']}
        with scratch_dir() as root, patch('core.editorial._write_batch',side_effect=write) as call:
            context = {'ordered_steps':True,'writing_only':True,'position':'片头'}
            result = _write_chapter_parts(context,[],[],excerpts,8,260,root/'batch.json',short=True)
            self.assertEqual([s['text'] for s in result['scenes']],[row['quote'] for row in excerpts.values()])
            self.assertEqual([len(c['required_steps']) for c in seen],[3,3,2])
            _write_chapter_parts(context,[],[],excerpts,8,260,root/'batch.json',short=True)
            self.assertEqual(call.call_count,3)

    def setUp(self):
        self.now = dt.datetime.now().astimezone()
        self.day = self.now.date()
        self.reports = [
            {"title": title, "url": f"https://example.com/{i}", "source": "测试报道",
             "summary": "深地科学观测与工程方法有各自的范围。" * 24, "published": self.now.isoformat()}
            for i, title in enumerate(("万米科探井", "万米钻探设备", "万米岩芯观测"))
        ]
        self.snapshot = {"articles": self.reports, "ranks": {"douyin": {
            "published": self.now.timestamp(), "entries": [{"title": "万米钻探", "rank": 1,
            "url": "https://example.com/topic", "heat": "热"}]}}}

    def test_douyin_duration_change_creates_new_draft_for_same_reports(self):
        ids = [story_key(report) for report in self.reports[:2]]
        anchor = {"kind": "article", "id": ids[0]}
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=self.snapshot):
            first = run_stage("editorial_create", self.day, root, "douyin",
                              {"anchor": anchor, "source_ids": ids, "target_seconds": 60}, now=self.now)
            second = run_stage("editorial_create", self.day, root, "douyin",
                               {"anchor": anchor, "source_ids": ids, "target_seconds": 180}, now=self.now)
        self.assertEqual([first["target_seconds"], second["target_seconds"]], [60, 180])
        self.assertNotEqual(first["id"], second["id"])

    def test_both_entrypoints_persist_only_confirmed_report_snapshots(self):
        from core.editorial import source_options

        ids = [story_key(report) for report in self.reports]
        for anchor in ({"kind": "article", "id": ids[0]},
                       {"kind": "hotspot", "platform": "douyin", "title": "万米钻探"}):
            with self.subTest(anchor=anchor), scratch_dir() as root, \
                    patch("core.intake.read_intake", return_value=self.snapshot), \
                    patch("core.editorial.chat_json", return_value={"ids": [ids[2], ids[1]]}):
                options = source_options(self.snapshot, anchor, self.now)
                self.assertEqual(len(options["reports"]), 3)
                item = run_stage("editorial_create", self.day, root, "bilibili",
                                 {"anchor": anchor, "source_ids": ids[:2]}, now=self.now)
                self.assertEqual([r["id"] for r in item["editorial"]["reports"]], ids[:2])
                self.assertEqual(item["editorial"]["reports"][0]["summary"], self.reports[0]["summary"])
                self.assertNotIn(ids[2], [r["id"] for r in read_edition(
                    edition_path(root, self.day))["items"]["bilibili"]["editorial"]["reports"]])
                with self.assertRaisesRegex(RuntimeError, "确认"):
                    run_stage("render", self.day, root, "bilibili")

    def test_batch_evidence_rejects_unapproved_source_and_unknown_value(self):
        from core.editorial import check_evidence

        report = {**self.reports[0], "id": "approved", "summary": "工程报告给出的实测井深为10910米。"}
        shot = {"text": "实测井深10910米。", "evidence": [
            {"source_id": "approved", "quote": "工程报告给出的实测井深为10910米。"}]}
        check_evidence([shot], [report])
        with self.assertRaisesRegex(ValueError, "来源"):
            check_evidence([{**shot, "evidence": [{"source_id": "other", "quote": report["summary"]}]}], [report])
        with self.assertRaisesRegex(ValueError, "3000"):
            check_evidence([{**shot, "text": "实测井深3000米。"}], [report])

    def test_batch_repairs_only_the_shot_with_an_unsupported_number(self):
        from core.editorial import _write_batch

        quote = "工程报告给出的实测井深为10910米。"
        report = {"id": "report", "summary": quote}
        evidence = {"1": {"source_id": "report", "quote": quote}}
        valid = {"title": "实测深度", "text": "井深的实测值是10910米。", "media": "", "motion": "push",
                 "evidence_ids": ["1"]}
        invalid = {**valid, "title": "测量结果", "text": "报告记录的深度是3000米，除了"}
        corrected = "报告中所说的是钻完时的实际深度。"
        extras = [{**valid, "title": "先看读数", "text": "报告列出了最终的井深。"},
                  {**valid, "title": "解释数据", "text": "这个深度来自现场测量。"}]
        with patch("core.editorial.chat_json", side_effect=[{"scenes": [valid, invalid, *extras]},
                   {"scenes": [{"index": 1, "focus":"删除缺少来源的数值，解释读数性质。", "text": corrected}]}, {"issues": []}]) as model:
            batch = _write_batch({}, {}, [report], [], evidence, 4, 60)
        self.assertEqual([row["text"] for row in batch["scenes"]],
                         [valid["text"], corrected, *(row["text"] for row in extras)])
        revised = model.call_args_list[1].args[1]["scenes"]
        self.assertEqual([row["unsupported_numbers"] for row in revised], [["3000"]])
        self.assertTrue(revised[0]["incomplete"])

    def test_batch_applies_structured_graphic_choice_to_selected_shot(self):
        from core.editorial import _write_batch

        quote = "观测站分别记录降雪量和积雪深度。"
        report = {"id": "report", "summary": quote}
        excerpts = {"1": {"source_id": "report", "quote": quote}}
        first = {"title": "观测", "text": "先看气象站如何测量。", "motion": "cut", "media": "", "evidence_ids": ["1"]}
        second = {**first, "title": "区分", "text": "雪的厚度和降雪量是两项不同的观测。"}
        response = {"scenes": [first, second], "graphic": {"index": 1, "style": "scan"}}
        with patch("core.editorial.chat_json", side_effect=[response, {"issues": []}]):
            cut = _write_batch({}, {"properties": {"graphic": {}}}, [report], [], excerpts, 2, 40)
        self.assertEqual([shot["motion"] for shot in cut["scenes"]], ["cut", "scan"])

    def test_comparison_sends_both_approved_quotes_to_the_fact_check(self):
        from core.editorial import _write_batch

        quotes = ["早上的公告显示中心移动时速为13公里。", "后续公告显示中心移动时速为19公里。"]
        reports = [{"id": str(i), "summary": quote} for i, quote in enumerate(quotes)]
        excerpts = {report["id"]: {"source_id": report["id"], "quote": report["summary"]} for report in reports}
        first = {"title": "中心移动", "text": "中心移动时速从13公里升到19公里。", "media": "", "motion": "cut",
                 "evidence_ids": ["0"]}
        second = {**first, "text": "公告记录的是风暴中心的位置变化。"}
        with patch("core.editorial.chat_json", side_effect=[{"scenes": [first, second]}, {"issues": []}]) as model:
            cut = _write_batch({}, {}, reports, [], excerpts, 2, 45)
        self.assertEqual(cut["source_ids"], ["0", "1"])
        self.assertEqual(model.call_args.args[1]["scenes"][0]["evidence"], list(excerpts.values()))

    def test_full_citation_slots_can_rebind_to_the_passage_with_both_missing_values(self):
        from core.editorial import _write_batch

        quotes = ["施工队在一片沙漠腹地架起设备。", "同期工程报告披露了许多进展。", "邻近油田公布累计50%的施工进度。",
                  "科探井从地表到5500米这段用时50多天。"]
        reports = [{"id": str(i), "summary": quote} for i, quote in enumerate(quotes)]
        excerpts = {r["id"]: {"source_id": r["id"], "quote": r["summary"]} for r in reports}
        first = {"title": "上半程进展", "text": "头50多天，钻头已经下探到5500米。", "media": "", "motion": "cut",
                 "evidence_ids": ["0", "1", "2"]}
        second = {**first, "text": "施工设备架在沙漠中。", "evidence_ids": ["0"]}
        with patch("core.editorial.chat_json", side_effect=[{"scenes": [first, second]}, {"issues": []}]) as model:
            _write_batch({}, {}, reports, [], excerpts, 2, 45)
        sent = model.call_args.args[1]["scenes"][0]["evidence"]
        self.assertEqual(len(sent), 3)
        self.assertIn(excerpts["3"], sent)

    def test_short_batch_rewrites_when_narration_exceeds_its_duration_budget(self):
        from core.editorial import _write_batch

        quote = "当地观测显示道路已经恢复通行。"
        reports = [{"id": "r", "summary": quote}]
        excerpts = {"1": {"source_id": "r", "quote": quote}}
        shot = {"title": "通行", "text": "当地道路恢复通行。", "media": "", "motion": "cut", "evidence_ids": ["1"]}
        other = {**shot, "text": "出门可再查看公告。"}
        long = {**shot, "text": "先看看这条消息给出行安排带来的变化，再听当地观察人员是怎样说明道路恢复状态的。"}
        with patch("core.editorial.chat_json", side_effect=[{"scenes": [long, other]}, {"scenes": [shot, other]},
                                                         {"issues": []}]) as model:
            batch = _write_batch({"format": "抖音快报"}, {}, reports, [], excerpts, 2, 25)
        self.assertEqual(batch["scenes"][0]["text"], shot["text"])
        self.assertIn("字", model.call_args_list[1].args[1]["correction"])

    def test_long_chapter_resumes_from_its_last_verified_writing_part(self):
        from core.editorial import write_batches

        reports = [{**r, "id": str(i), "summary": r["summary"] * 2} for i, r in enumerate(self.reports)]
        item = {"editorial": {"reports": reports}, "editorial_plan": {"throughline": "设备如何运作？"},
                "chapters": [{"story": reports[0], "question": "设备如何运作？", "reports": reports}],
                "target_seconds": 720}
        valid = {"scenes": [{"title": title, "text": title + "。", "media": "", "zoom": False}
                            for title in ("井口的设备", "钻头的动作", "读数的变化")], "source_ids": ["0"]}
        with scratch_dir() as root, patch("core.editorial._write_batch", side_effect=[valid, RuntimeError("模型暂离线")]) as write:
            with self.assertRaisesRegex(RuntimeError, "模型暂离线"):
                write_batches(item, "bilibili", root, lambda _: [], lambda *_: None, writing_only=True)
            self.assertEqual([call.args[5] for call in write.call_args_list], [3, 3])
            self.assertIn("井口的设备", write.call_args.args[0]["earlier_narration"])
            self.assertEqual(len(list(root.glob("batch-*.json"))), 1)

    def test_long_chapter_gives_each_part_different_evidence(self):
        from core.editorial import _write_chapter_parts

        excerpts = {str(i): {"source_id": "report", "quote": f"这是本章第{i}条独立的报道事实。"} for i in range(12)}
        finished = {"scenes": [{"text": "本段报道已核对。"}], "source_ids": ["report"]}
        with scratch_dir() as root, patch("core.editorial._write_batch", return_value=finished) as write:
            _write_chapter_parts({"position": "中段", "writing_only": True}, [], [], excerpts, 9, 1200,
                                 root / "chapter.json", short=False)
        groups = [set(call.args[4]) for call in write.call_args_list]
        self.assertEqual([len(group) for group in groups], [4, 4, 4])
        self.assertEqual(set.union(*groups), excerpts.keys())
        self.assertIn("本段报道已核对", write.call_args.args[0]["earlier_narration"])

    def test_failed_number_repair_can_drop_only_the_unsupported_sentence(self):
        from core.editorial import _write_batch

        quote = "报道称突破万米之后，钻探速度明显放慢。"
        report = {"id": "r", "summary": quote}
        excerpts = {"1": {"source_id": "r", "quote": quote}}
        supported = "钻得越深，这段的进尺越慢。"
        text = "井底剩下1000米。" + supported
        first = {"title": "速度变化", "text": text, "evidence_ids": ["1"], "motion": "cut", "media": ""}
        other = {**first, "text": "进尺变化来自报道的记录。"}
        ineffective = {"scenes": [{"index": 0, "focus":"说明进尺变化。", "text": text}]}
        with patch("core.editorial.chat_json", side_effect=[{"scenes": [first, other]}, ineffective,
                                                         ineffective, {"issues": []}]):
            cut = _write_batch({}, {}, [report], [], excerpts, 2, 45)
        self.assertEqual(cut["scenes"][0]["text"], supported)

    def test_assembling_parts_drops_exact_repeats_and_preserves_chapter_offsets(self):
        from core.editorial import write_batches

        reports = [{**r, "id": str(i), "summary": r["summary"] * 2} for i, r in enumerate(self.reports)]
        chapters = [{"story": report, "question": "核对报道", "reports": [report]} for report in reports[:2]]
        item = {"editorial": {"reports": reports}, "editorial_plan": {}, "chapters": chapters, "target_seconds": 60}
        def cut(titles, source):
            return {"scenes": [{"title": title, "text": title + "。", "media": "", "zoom": False} for title in titles],
                    "source_ids": [source]}
        batches = [cut(["现场已经停工", "工作人员正在检查"], "0"),
                   cut(["现场已经停工", "观测设备准备就绪", "检查记录已经发布"], "1")]
        with scratch_dir() as root, patch("core.editorial._write_batch", side_effect=batches):
            result = write_batches(item, "douyin", root, lambda _: [], lambda *_: None, writing_only=True)
        self.assertEqual([row["scene"] for row in result["chapter_starts"]], [0, 2])
        self.assertEqual(len(result["scenes"]), 4)

    def test_master_speech_uses_decoded_scene_lengths(self):
        import subprocess
        from config import get_ffmpeg
        from core.audio_engine import _assemble_speech

        with scratch_dir() as root:
            clips = [root / "first.mp3", root / "second.mp3"]
            for path, length in zip(clips, (.16, .23), strict=True):
                subprocess.run([get_ffmpeg(), "-loglevel", "error", "-f", "lavfi", "-i",
                                f"sine=sample_rate=24000:duration={length}", "-q:a", "3", str(path)],
                               check=True, capture_output=True)
            master = root / "speech.mp3"
            self.assertEqual(_assemble_speech(clips, master), [.16, .23])
            pcm = subprocess.check_output([get_ffmpeg(), "-loglevel", "error", "-i", str(master),
                                           "-f", "s16le", "-"])
            self.assertEqual(len(pcm), (3840 + 5520) * 2)
            self.assertFalse(list(root.glob("*.partial.*")))

    def test_truncated_mux_cannot_replace_a_previous_export(self):
        from core.video_engine import render_platform_video

        with scratch_dir() as root:
            saved = root / "video_bilibili.mp4"
            saved.write_bytes(b"previous export")

            def truncated(*args, **kwargs):
                (root / "video_bilibili.partial.mp4").write_bytes(b"truncated export")

            with patch("core.video_engine._encode_news_scenes", return_value=["cut_00.mp4"]), \
                    patch("core.video_engine._run_ffmpeg", side_effect=truncated) as encode, \
                    patch("core.video_engine.get_audio_duration", return_value=326.51), \
                    self.assertRaisesRegex(RuntimeError, "视频合成时长异常"):
                render_platform_video({"target_seconds": 720,"scenes":[{}]}, "bilibili",
                                      {"scene_durations": [845.448], "total_duration": 845.448}, root)
            args = encode.call_args.args[0]
            self.assertIn("-nostdin", args)
            self.assertEqual(args[args.index("-t") + 1], "845.448000")
            self.assertEqual(saved.read_bytes(), b"previous export")
            self.assertFalse((root / "video_bilibili.partial.mp4").exists())

    def test_footage_stays_full_frame_around_source_and_number(self):
        from core.visual_engine import generate_episode_slide

        scene = {"layout": "footage", "title": "全市平均降雪量", "documentary_number": "32.3毫米",
                 "asset": {"author": "摄影者", "caption_label": "历史资料"}}
        image = generate_episode_slide({"scenes": [scene]}, 0, 640, 360, "bilibili", video_overlay=True)
        self.assertEqual(image.getpixel((0, 180))[3], 0)
        self.assertLess(image.getpixel((0, 0))[3], 200)
        self.assertLess(image.getpixel((0, 359))[3], 200)
        self.assertGreater(image.getchannel("A").crop((310, 175, 610, 245)).getextrema()[1], 240)

    def test_case_rewrite_keeps_the_saved_cut_until_review(self):
        from core.workflow import write_json

        old = {"scenes": [{"title": "原稿", "text": "已保存的旁白。", "media": "", "zoom": False}]}
        reference = {"title": "案例", "url": "https://www.bilibili.com/video/BV1", "notes": "从镜头里的细节切入。"}
        candidate = {"scenes": [{**old["scenes"][0], "text": "从一把尺讲起。"}],
                     "chapter_starts": [{"title": "开场", "scene": 0, "style_reference": reference}]}
        chapter = {"story": {"title": "开场"}, "reports": []}
        item = {"id": "issue", "editorial": {"anchor": {}, "reports": []}, "chapters": [chapter],
                "editorial_plan": {"throughline": "实况"}, "edit": old, "edit_revision": "saved",
                "media_checked": True, "opening_media_checked": True, "preview": {"video": "old.mp4"}}
        with scratch_dir() as root:
            path = edition_path(root, self.day)
            (path.parent / "bilibili").mkdir(parents=True)
            write_json(path, {"items": {"bilibili": item}})
            with patch("core.editorial.write_batches", return_value=candidate), \
                    patch('core.editorial.retrieve_chapter_evidence', side_effect=lambda _editorial, chapters, *_args: chapters), \
                    patch("core.workflow._render_preview") as render:
                result = run_stage("editorial_preview", self.day, root, "bilibili", {"id": "issue", "rewrite": True})
            self.assertEqual(result, candidate)
            current = read_edition(path)["items"]["bilibili"]
            self.assertEqual(current["edit"], old)
            self.assertEqual(current["suggestion"], candidate)
            self.assertEqual(current["preview"], {"video": "old.mp4"})
            render.assert_not_called()

    def test_changing_one_report_preserves_unrelated_chapter(self):
        from core.editorial import plan_chapters

        rows = [{**row, "id": story_key(row)} for row in self.reports]
        previous = [{"story": {**row, "title": f"章节{i}"}, "question": "核对工程证据", "reports": [row]}
                    for i, row in enumerate(rows)]
        replacement = {**rows[1], "id": "updated", "title": "钻机新资料"}
        editorial = {"anchor": {"title": "万米科探井"}, "reports": [rows[0], replacement, rows[2]]}
        with scratch_dir() as root, patch("core.editorial.chat_json") as model:
            chapters, _ = plan_chapters(editorial, "bilibili", root / "outline.json",
                                        previous=previous, previous_plan={"throughline": "核对工程进展"})
        self.assertEqual(chapters[0], previous[0])
        self.assertEqual(chapters[2], previous[2])
        self.assertEqual(chapters[1]["reports"], [replacement])
        model.assert_not_called()

    def test_preview_leaves_long_export_for_explicit_current_revision(self):
        from pathlib import Path

        ids = [story_key(row) for row in self.reports]
        anchor = {"kind": "article", "id": ids[0]}
        scenes = [{"title": f"章节{i}", "text": f"这是第{i}段验收口播。", "media": "", "zoom": False}
                  for i in range(3)]
        draft = {"scenes": scenes, "editorial_plan": {"throughline": "验收"},
                 "chapter_starts": [{"title": scene["title"], "scene": i, "source_ids": [ids[0]]}
                                    for i, scene in enumerate(scenes)]}
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=self.snapshot):
            item = run_stage("editorial_create", self.day, root, "bilibili",
                             {"anchor": anchor, "source_ids": ids[:2]}, now=self.now)
            reports = item["editorial"]["reports"]
            chapters = [{"story": {**reports[0], "title": s["title"]}, "question": "解释依据", "reports": reports}
                        for s in scenes]
            with patch("core.editorial.plan_chapters", return_value=(chapters, draft["editorial_plan"])), \
                    patch('core.editorial.retrieve_chapter_evidence', side_effect=lambda _editorial, chapters, *_args: chapters), \
                    patch("core.editorial.write_batches", return_value=draft), \
                    patch("core.workflow._prepare_media", return_value=[]), \
                    patch("core.audio_engine.generate_voice_and_subtitles", return_value={"total_duration": 34,"scene_durations":[11,11,12]}), \
                    patch("core.video_engine.render_platform_video", return_value=Path("preview.mp4")), \
                    patch("core.workflow._render", return_value={"video": "full.mp4"}) as render:
                preview = run_stage("editorial_preview", self.day, root, "bilibili", item["id"])
                self.assertEqual(preview["video"], "preview.mp4")
                current = read_edition(edition_path(root, self.day))["items"]["bilibili"]
                self.assertNotIn("edit", current)
                self.assertEqual(current["preview_revision"], current["suggestion_revision"])
                with self.assertRaisesRegex(RuntimeError, "确认"):
                    run_stage("editorial_export", self.day, root, "bilibili",
                              {"id": item["id"], "revision": current["suggestion_revision"]})
                render.assert_not_called()
                edited = {**draft, "scenes": [{**scenes[0], "text": "已核对开场。"}, *scenes[1:]]}
                run_stage("save", self.day, root, "bilibili", edited)
                current = read_edition(edition_path(root, self.day))["items"]["bilibili"]
                self.assertNotEqual(current["preview_revision"], current["edit_revision"])
                self.assertEqual(run_stage("editorial_export", self.day, root, "bilibili",
                                 {"id": item["id"], "revision": current["edit_revision"]})["video"], "full.mp4")

    def test_material_review_controls_new_drafts_and_keeps_existing_video(self):
        from core.media_review import usable_media
        from core.workflow import _merge_media, write_json

        asset = {"page_url": "https://example.com/asset", "title": "待核对照片", "path": "original.jpg"}
        draft = {"scenes": [{"title": "开场", "text": "开场文字", "zoom": False, "media": asset["page_url"]}]}
        item = {"id": "issue", "media": [], "edit": draft, "edit_revision": "old",
                "preview_media": [asset["page_url"]], "preview_revision": "old",
                "package": {"video": "existing.mp4"}}
        with scratch_dir() as root:
            path = edition_path(root, self.day)
            path.parent.mkdir(parents=True)
            write_json(path, {"items": {"bilibili": item}})
            _merge_media(path, "bilibili", [asset])
            self.assertEqual(usable_media(read_edition(path)["items"]["bilibili"]), [])
            payload = {"page_url": asset["page_url"], "id": "issue", "state": "approved"}
            self.assertEqual(run_stage("review_media", self.day, root, "bilibili", payload), "approved")
            self.assertEqual(usable_media(read_edition(path)["items"]["bilibili"]), [asset])
            self.assertEqual(run_stage("review_media", self.day, root, "bilibili",
                                      {**payload, "state": "removed"}), "removed")
            changed = read_edition(path)["items"]["bilibili"]
            self.assertEqual(changed["edit"]["scenes"][0]["media"], "")
            self.assertNotEqual(changed["edit_revision"], "old")
            self.assertIsNone(changed["preview_revision"])
            self.assertEqual(changed["package"]["video"], "existing.mp4")

    def test_opening_uses_approved_related_media_without_editing_narration(self):
        from core.editing import render_case
        from core.workflow import write_json

        reports = [{**report, "id": story_key(report)} for report in self.reports[:2]]
        titles = ["深井开场", "工程背景"]
        narration = "钻井项目公布进展，本期沿着两个报道来源逐项核对工程记录。" * 6
        cut = {"scenes": [{"title": title, "text": narration, "media": "", "zoom": False} for title in titles],
               "chapter_starts": [{"scene": i, "title": title} for i, title in enumerate(titles)]}
        chapters = [{"story": {**report, "title": title}, "reports": [report]}
                    for title, report in zip(titles, reports)]
        with scratch_dir() as root:
            path = edition_path(root, self.day)
            path.parent.mkdir(parents=True)
            assets = []
            for kind in ("image", "video"):
                file = root / kind
                file.write_bytes(b"asset fixture")
                assets.append({"kind": kind, "title": kind, "path": str(file), "page_url": f"https://example.com/{kind}"})
            item = {"id": "episode", "editorial": {"anchor": {"title": "深井"}}, "story": reports[0],
                    "chapters": chapters, "edit": cut, "edit_revision": "original", "preview_revision": "original"}
            write_json(path, {"items": {"bilibili": item}})
            with patch("core.news_media.download_story_media", return_value=(assets, [])) as search:
                run_stage("media", self.day, root, "bilibili")
            self.assertEqual(search.call_args.args[0], reports[0])
            self.assertEqual(search.call_args.kwargs["hotspot"], item["editorial"]["anchor"])
            current = read_edition(path)["items"]["bilibili"]
            self.assertFalse(any(s.get("asset") for s in render_case(current, "bilibili")["scenes"]))
            for asset in assets:
                run_stage("review_media", self.day, root, "bilibili",
                          {"id": item["id"], "page_url": asset["page_url"], "state": "approved"})
            current = read_edition(path)["items"]["bilibili"]
            self.assertIsNone(current["preview_revision"])
            self.assertNotEqual(current["edit_revision"], "original")
            rendered = render_case(current, "bilibili")
            opening = rendered["scenes"][:5]
            self.assertEqual([s["layout"] for s in opening], ["opening"] * 5)
            self.assertEqual([s.get("asset", {}).get("kind") for s in opening], ["video", "image", None, None, None])
            self.assertEqual(opening[2]["opening_report"]["url"], reports[0]["url"])
            second = rendered["chapter_starts"][1]["scene"]
            self.assertEqual("".join(s["text"] for s in rendered["scenes"][:second]), narration)
