import unittest
import datetime as dt
from pathlib import Path
from unittest.mock import patch

from core.copywriting import add_case, chapter_cases, collect_topic_cases, generate_batch, refresh_hotspot_library, retrieve_cases
from test_daily import scratch_dir


class CopywritingTest(unittest.TestCase):
    def test_regenerating_source_photos_can_reuse_a_previous_recipe_title_and_facts(self):
        from core.copywriting import _generate
        body='先将肠粉斜切成小块，水开后上锅蒸好，再把蒜末和辣椒圈炒香。接着按原文加入生抽和老抽，拌好之后将料汁淋在肠粉上。准备时把需要的食材放在手边，蒸肠粉和调料汁可以按步骤图对照，最后再确认调味。'
        old={'title':'蒜香肠粉','script':body,'caption':'酱汁拌好，开饭。 #肠粉 #家常菜'}
        with patch('core.copywriting.chat_json',return_value=old), \
                patch('core.copywriting._photo_copy_issues',return_value=''):
            draft,=_generate('xiaohongshu','美食教程','',[],1,[old],0,illustrated=True,
                             photo_sets=[[(None,{'title':'肠粉与蒜末辣椒'})]])
        self.assertEqual(draft['title'],old['title'])
        self.assertEqual(draft['script'],body)

    def test_caption_repair_preserves_the_checked_recipe_body(self):
        from core.copywriting import _generate
        body='准备好肠粉和蒜末辣椒，按原文的顺序蒸好肠粉，再调制料汁。酱油和其他调料按图片中的说明加入，拌匀之后淋在肠粉上。先把需要的食材放在手边，再开始操作，过程中的每一步都可以对照图片确认，不用临时翻找。'
        with patch('core.copywriting.chat_json',side_effect=[{'title':'蒜香肠粉','script':body,'caption':'蒜香肠粉'},
                {'caption':'酱汁拌好，开饭。 #肠粉 #家常菜'}]) as writer, \
                patch('core.copywriting._photo_copy_issues',return_value=''):
            draft,=_generate('xiaohongshu','美食教程','',[],1,[],0,illustrated=True,
                             photo_sets=[[(None,{'title':'肠粉与蒜末辣椒'})]])
        self.assertEqual(draft['script'],body)
        self.assertNotEqual(draft['caption'],draft['title'])
        self.assertEqual(writer.call_count,2)

    def test_conflicting_original_and_image_amount_is_not_presented_as_settled(self):
        from core.copywriting import _photo_copy_issues
        with patch('core.copywriting.chat_json') as review:
            reason=_photo_copy_issues({'title':'料汁','script':'加入2勺老抽和2勺蚝油。','caption':'家常做法'},[],
                '正文：加入2勺老抽、2勺蚝油。配图文字：2勺老抽、1勺蚝油。')
        self.assertIn('蚝油',reason)
        self.assertIn('冲突',reason)
        review.assert_not_called()

    def test_same_original_note_can_supply_its_writing_method_under_a_broad_hotspot(self):
        from core.copywriting import _note_reference
        case={'title':'感谢网友的配方','notes':'开头用对比手法突出酱料对食物的提升；用优惠促销引导购买。'}
        found,method=_note_reference([case],'超日常美食教程速来get','关键步骤与容易忽略的细节',
                                     photo_first=True,source_matched=True)
        self.assertEqual(found,case)
        self.assertEqual(method,'开头用对比手法突出酱料对食物的提升')

    def test_photo_audit_rejects_invented_amounts_before_model_review(self):
        from core.copywriting import _photo_copy_issues
        draft={'title':'家常焖饭','script':'加入200毫升清水，焖30分钟。','caption':'晚饭'}
        with patch('core.copywriting.chat_json') as review:
            reason=_photo_copy_issues(draft,[], '图字：加入100毫升清水，焖20分钟。')
        self.assertIn('200毫升',reason)
        self.assertIn('30分钟',reason)
        review.assert_not_called()

    def test_photo_audit_must_identify_a_claim_in_the_draft_not_in_its_evidence(self):
        from core.copywriting import _photo_copy_issues
        draft={'title':'焖饭','script':'鸡腿焖五分钟就能熟。','caption':'晚饭'}
        photos=[(None,{'title':'手拿锅铲，正在翻炒食材。','source_report_id':'same-note'})]
        with patch('core.copywriting.chat_json',return_value={'issues':[{
                'claim_id':1,'reason':'原文没有这个时间，不能保证熟透。'}]}):
            self.assertIn('焖五分钟就能熟',_photo_copy_issues(draft,photos,'原文没有烹饪时长。'))
        with patch('core.copywriting.chat_json',return_value={'issues':[{
                'claim_id':99,'reason':'正文没有手部动作。'}]}):
            with self.assertRaisesRegex(ValueError,'正文编号'):
                _photo_copy_issues(draft,photos,'原文没有烹饪时长。')

    def test_photo_versions_use_their_own_source_recipe_and_reference(self):
        from core.copywriting import _generate
        from PIL import Image
        urls=['https://www.xiaohongshu.com/explore/6a0c1b78000000000702fe3a',
              'https://www.xiaohongshu.com/explore/6a058bfb000000003501faca']
        facts=['第一份食谱用四个鸡蛋，先备料再下锅。','第二份食谱用三个鸡蛋，调味后收汁。']
        photos=[[(Image.new('RGB',(1080,1440)),{'title':'番茄与鸡蛋的菜肴','page_url':url,
                 'source_summary':fact,'source_title':'原文食谱'})] for url,fact in zip(urls,facts)]
        references=[{'title':'番茄炒蛋','url':url,'evidence':'公开图文','notes':note} for url,note in zip(urls,
            ['按备料与下锅分段。','强调调味与收汁两个阶段。'])]
        texts=['先把番茄切好，鸡蛋搅散，备料齐了再开火。锅边不用临时找调味料，做饭的顺序也容易记住。把食材分开放在手边，再按原文的顺序下锅；想吃一顿家常饭的时候，从这一道熟悉的菜开始，慢慢把步骤做顺。',
               '蛋液先炒到合适的状态，再让番茄的汤汁和它拌在一起。最后的调味可以分两次确认，别急着把所有料都倒下去。出锅前看一眼汤汁留得够不够，吃饭的时候拌一点米饭，晚餐的主角就有了。']
        replies=[{'title':title,'script':text*2,'caption':'一点家常味道 #家常菜 #番茄炒蛋'} for title,text in zip(['先备好，再开火','把汤汁留给米饭'],texts)]
        with patch('core.copywriting.chat_json',side_effect=replies) as writer, \
                patch('core.copywriting._photo_copy_issues',return_value='') as audit:
            drafts=_generate('xiaohongshu','番茄炒蛋教程','混合背景材料',references,2,[],0,
                ['按备料与下锅分段。','强调调味与收汁。'],illustrated=True,photo_sets=photos)
        self.assertEqual([c.args[1]['事实'] for c in writer.call_args_list],facts)
        self.assertEqual([c.args[2] for c in audit.call_args_list],facts)
        self.assertEqual([d['reference']['url'] for d in drafts],urls)
        self.assertTrue(all('title_reference' not in d for d in drafts))

    def test_lifestyle_mode_writes_from_distinct_licensed_photos(self):
        from PIL import Image
        from core.workflow import run_stage

        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        topic = "旅行拍照姿势"
        snapshot = {"articles": [], "ranks": {"xiaohongshu": {"published": now.timestamp(),
                    "entries": [{"title": topic, "url": "https://www.xiaohongshu.com/explore/travel", "rank": 1}]}}}
        sources = [[(Image.new("RGB", (800, 1200)), {"title": scene, "author": "摄影师", "license": "CC0",
                     "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "page_url": f"https://example.com/{i}"})]
                   for i, scene in enumerate(("两个人过街", "街角落下的光"))]
        replies = [{"title": title, "script": line, "caption": "#生活随手拍"}
                   for title, line in (("一起走回去吧", "刚才我们并肩走了一段。"),
                                       ("一起走回去吧", "今天要不要一起走一段？路边有两个能并肩走的位置，话慢慢说，路慢慢走。"
                                        "等想起还有什么没讲完，也不用急着拿起手机。" * 2),
                                       ("拐角处的光", "等红灯的时候，不妨看看落在路口的光。明明走过同一条路那么多回，"
                                        "偶尔也会因为脚边的一块亮色，想在原地多站几秒。" * 2))]
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot), \
                patch("core.copywriting.collect_topic_cases", return_value={"saved": []}), \
                patch("core.note_images.collect_source_note_photos", return_value=sources), \
                patch("core.copywriting._photo_copy_issues", return_value=""), \
                patch("core.copywriting.chat_json", side_effect=replies) as chat:
            batch = run_stage("xiaohongshu_notes", now.date(), root, now=now,
                              payload={"topic": topic, "facts": "", "count": 2, "photo_first": True})
            drafts = batch["results"]["xiaohongshu"]["drafts"]
            self.assertEqual([draft["pictures"][0]["photo"]["title"] for draft in drafts], ["两个人过街", "街角落下的光"])
            self.assertIn("两个人过街", str(chat.call_args_list[0].args[1]))
            self.assertIn("街角落下的光", str(chat.call_args_list[2].args[1]))
            self.assertNotIn("刚才", drafts[0]["script"])
            self.assertTrue(all("pages" not in draft for draft in drafts))

    def test_keyword_photo_notes_without_a_hotspot_board(self):
        from PIL import Image
        from core.workflow import run_stage

        photos = [(Image.new("RGB", (950, 1150), "orange"), {"title": str(i), "author": "作者", "license": "CC0",
                   "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "page_url": f"https://example.com/{i}"})
                  for i in range(4)]
        for (_,credit),title in zip(photos,["手中的锅和鸡蛋", "筷子和面条", "手里的书", "床头的灯"]):
            credit['title']=title

        now = dt.datetime.now().astimezone()
        with scratch_dir() as root, patch("core.copywriting._hotspot_material") as board, \
                patch("core.copywriting.collect_topic_cases", return_value={"saved": [],'source_ids':['source-a','source-b']}), \
                patch("core.note_images.collect_source_note_photos", return_value=[photos[:2],photos[2:]]) as collect, \
                patch("core.copywriting._photo_copy_issues", return_value=""), \
                patch("core.copywriting.chat_json", side_effect=[
                    {"title": "一个人也吃晚饭", "script": "筷子挑起一口面。忙完一天，今天也可以慢慢吃完这碗饭。" * 4,
                     "caption": "#下班后的晚饭"},
                    {"title": "灯亮着就翻两页", "script": "把手机放一边，翻几页书。要回的消息还有明天，眼前这页可以慢慢读。" * 3,
                     "caption": "#下班后读书"},
                ]):
            batch = run_stage("xiaohongshu_notes", now.date(), root, now=now, payload={
                "topic": "下班后独处", "facts": "", "count": 2, "photo_first": True, "keyword_topic": True})
            drafts = batch["results"]["xiaohongshu"]["drafts"]
            board.assert_not_called()
            self.assertIsNone(batch["hotspot"])
            self.assertEqual(collect.call_args.args[-1],['source-a','source-b'])
            self.assertTrue(all(len(draft["pictures"]) == 2 for draft in drafts))
            self.assertEqual(len({pic["photo"]["page_url"] for draft in drafts for pic in draft["pictures"]}), 4)

    def test_commute_keyword_pairs_distinct_photos_and_retries_invented_sounds(self):
        from PIL import Image
        from core.workflow import run_stage

        photos = [(Image.new("RGB", (960, 1440), "gray"), {"title": str(i), "author": "作者", "license": "CC0",
                   "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "page_url": f"https://example.com/{i}"})
                  for i in range(4)]
        for (_,credit),title in zip(photos,["耳机和地铁座位", "闸机", "斑马线旁撑着的伞、路口驶过的公交车", "湿路面"]):
            credit['title']=title

        script = "耳机戴上，消息可以晚一站再回。人已经下班了，手还习惯性地往工作群点。有空座就坐会儿，把白天在脑子里排的待办先放一边。到站之前，这几分钟可以什么都不用安排。"
        rain = "撑着伞在路口等灯，消息响了，也可以等过了马路再看。下雨天赶路，谁没试过一手拎包一手收伞，腾不出手还惦记着没回的那句话。先把脚下这一段走稳，等到了屋檐下再说。"
        drafts = [{"title": title, "script": body, "caption": "今天就慢一点。 #通勤日常 #生活碎片"}
                  for title, body in (("坐地铁回家", "车厢里传来一阵刺耳的刹车声。" + script),
                                      ("地铁通勤", "不要输出多余的解释。直接输出 JSON 格式。" * 6),
                                      ("消息晚一站再回", script),
                                      ("过马路慢一点", rain + "人已经下班了，手还习惯性地往工作群点。"),
                                      ("伞和公交", "斑马线旁撑着的伞、路口驶过的公交车。" + rain),
                                      ("收好伞再回消息", rain))]
        now = dt.datetime.now().astimezone()
        with scratch_dir() as root, patch("core.copywriting.collect_topic_cases", return_value={"saved": []}), \
                patch("core.note_images.collect_source_note_photos", return_value=[photos[:2],photos[2:]]), \
                patch("core.copywriting._photo_copy_issues", return_value=""), \
                patch("core.copywriting.chat_json", side_effect=drafts) as write:
            batch = run_stage("xiaohongshu_notes", now.date(), root, now=now, payload={
                "topic": "下班路上", "facts": "", "count": 2, "photo_first": True, "keyword_topic": True})
            self.assertFalse(batch["errors"])
            notes = batch["results"]["xiaohongshu"]["drafts"]
            self.assertIn("耳机", write.call_args_list[0].args[1]["首图可见细节"])
            self.assertIn("伞", write.call_args_list[3].args[1]["首图可见细节"])
            self.assertEqual([note["script"] for note in notes], [script, rain])
            self.assertEqual(len({pic["photo"]["page_url"] for note in notes for pic in note["pictures"]}), 4)

    def test_hotspot_note_worker_rejects_yesterdays_board_before_collecting(self):
        from core.workflow import run_stage

        now = dt.datetime(2026, 11, 9, 1, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        snapshot = {"articles": [], "ranks": {"xiaohongshu": {"published": now.timestamp() - 7200,
                    "entries": [{"title": "旧热点", "url": "https://example.com/old", "rank": 1}]}}}
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot), \
                patch("core.copywriting.collect_topic_cases") as collect:
            with self.assertRaisesRegex(ValueError, "今日榜单"):
                run_stage("xiaohongshu_notes", now.date(), root, payload={"topic": "旧热点", "facts": "", "count": 2}, now=now)
            collect.assert_not_called()

    def test_hotspot_note_worker_learns_before_writing_and_saves_two_source_traces_with_images(self):
        from PIL import Image
        from core.workflow import run_stage

        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        topic = "旅行拍照姿势"
        url = "https://www.xiaohongshu.com/explore/abc123"
        snapshot = {"articles": [], "ranks": {"xiaohongshu": {"published": now.timestamp(),
                    "url": "https://example.com/rank", "entries": [{"title": topic, "url": url, "rank": 1}]}}}
        pages = [{"title": label, "text": "在安全步道练习，人物朝取景留白的位置看。", "image": "步道上的人物",
                  "photo_query": ""} for label in ("封面", "场景", "动作", "清单")]
        replies = [{"title": title, "script": line * 8, "caption": "#手机摄影", "pages": pages}
                   for title, line in (("一个人拍背影", "先把手机固定在安全的位置。"),
                                       ("请朋友拍走路照", "先请朋友选定你要走入的画面。"))]
        work = {"title": topic, "url": url, "excerpt": "旅行拍照姿势先找开阔步道，然后介绍人物站位和视线的关系。",
                "evidence": "公开图文笔记 · 4张图"}
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot), \
                patch("core.copywriting._public_work", return_value=work), \
                patch("core.browser_cases.collect_browser_cases", return_value={"works": []}), \
                patch("core.note_images.find_note_photo", return_value=None), \
                patch("core.copywriting.analyze_case", return_value={"keywords": topic, "notes": "按拍照动作分组"}), \
                patch("core.copywriting.chat_json", side_effect=replies) as chat:
            result = run_stage("xiaohongshu_notes", now.date(), root, payload={"topic": topic, "facts": "", "count": 2}, now=now)
            self.assertFalse(result["errors"])
            self.assertEqual(result["case_collection"]["saved"], [topic])
            self.assertEqual(chat.call_args_list[0].args[1]["结构参考"][0]["url"], url)
            for draft in result["results"]["xiaohongshu"]["drafts"]:
                self.assertEqual(draft["reference"]["url"], url)
                self.assertEqual(draft["borrowed"], "按拍照动作分组")
                self.assertEqual(len(draft["pictures"]), 4)
                for picture in draft["pictures"]:
                    with Image.open(picture["file"]) as image:
                        self.assertEqual(image.size, (1080, 1440))
            self.assertIn("本版借鉴", Path(result["results"]["xiaohongshu"]["file"]).read_text(encoding="utf-8"))

    def test_illustrated_cover_retains_verified_photo_credit(self):
        from PIL import Image
        from core.note_images import render_notes

        pages = [{"title": "拍照", "text": "在开阔步道练习。", "image": "步道", "photo_query": "woman walking trail"}] * 4
        draft = {"title": "顺着步道往前走", "pages": pages}
        credit = {"title": "Walking", "author": "Photographer", "license": "CC BY 4.0",
                  "license_url": "https://creativecommons.org/licenses/by/4.0/", "page_url": "https://commons.wikimedia.org/wiki/File:Test.jpg"}
        with scratch_dir() as root, patch("core.note_images.find_note_photo", side_effect=[(Image.new("RGB", (600, 800), "#70817B"), credit), None]):
            render_notes([draft], root, topic="步道练习")
            self.assertEqual(draft["pictures"][0]["photo"], credit)
            self.assertIsNone(draft["pictures"][1]["photo"])
            with Image.open(draft["pictures"][0]["file"]) as cover:
                self.assertEqual(cover.size, (1080, 1440))

    def test_hotspot_text_mode_keeps_matching_writing_and_creates_no_images(self):
        from core.workflow import run_stage

        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        topic = "旅行拍照姿势"
        good = "https://www.xiaohongshu.com/explore/good"
        marketing = "https://www.xiaohongshu.com/explore/sales"
        snapshot = {"articles": [], "ranks": {"xiaohongshu": {"published": now.timestamp(),
                    "entries": [{"title": topic, "url": good, "rank": 1}]}}}
        replies = [{"title": title, "script": title + "，欢迎来聊聊。", "caption": "#旅行故事"}
                   for title in ("想约朋友走走", "旧照里有哪一刻")]
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot), \
                patch("core.copywriting.collect_topic_cases", return_value={"saved": []}), \
                patch("core.copywriting.chat_json", side_effect=replies) as chat:
            add_case(root, "xiaohongshu", topic, good, "公开图文", topic,
                     "用一个出游场景开场；结尾邀请读者留言互动")
            add_case(root, "xiaohongshu", topic, marketing, "公开图文", topic,
                     "用价格锚点引导咨询和转化")
            for suffix in ("tips1", "tips2", "tips3"):
                add_case(root, "xiaohongshu", topic, f"https://www.xiaohongshu.com/explore/{suffix}", "公开图文", topic,
                         "结尾给旅行拍照姿势和拍摄地点提示")
            batch = run_stage("xiaohongshu_notes", now.date(), root, now=now,
                              payload={"topic": topic, "facts": "", "count": 2, "text_only": True})
            result = batch["results"]["xiaohongshu"]
            self.assertEqual([x["borrowed"] for x in result["drafts"]],
                             ["用一个出游场景开场", "结尾邀请读者留言互动"])
            self.assertEqual([x["reference"]["url"] for x in result["drafts"]], [good, good])
            self.assertTrue(all(not x["pictures"] and "pages" not in x for x in result["drafts"]))
            self.assertFalse(list(Path(batch["directory"]).rglob("*.jpg")))
            self.assertNotIn(marketing, str(chat.call_args_list))

    def test_travel_action_pages_show_distinct_steps_without_searching_stock(self):
        from core.note_images import render_notes

        def draft(action):
            cover = {"title": "封面", "text": "在开阔步道练习。", "image": "封面", "photo_query": ""}
            step = {**cover, "title": action}
            return {"title": action, "pages": [cover, step]}

        drafts = [draft("固定手机"), draft("自然迈步")]
        with scratch_dir() as root, patch("core.note_images.find_note_photo", return_value=None) as search:
            render_notes(drafts, root, topic="旅行拍照姿势")
            self.assertEqual([x["pictures"][1]["action"] for x in drafts], ["fixed", "walk"])
            self.assertTrue(all(not x["pictures"][1]["photo"] for x in drafts))
            self.assertTrue(all("固定" not in call.args[0] and "迈步" not in call.args[0] for call in search.call_args_list))

    def test_chapter_cases_require_a_related_actual_platform_work(self):
        chapter = {"story": {"title": "珊瑚育苗"}, "reports": [{"title": "海洋珊瑚育苗观察", "summary": "珊瑚苗在水下生长。"}]}
        with scratch_dir() as root:
            add_case(root, "bilibili", "珊瑚育苗热点", "https://www.tunbang.com/hot/bilibili/",
                     "热词排行", "珊瑚 育苗", "一个热词摘要。")
            self.assertEqual(chapter_cases(root, "bilibili", chapter), [])
            add_case(root, "bilibili", "珊瑚育苗实录", "https://www.bilibili.com/video/BV123",
                     "公开作品页", "珊瑚 育苗", "把镜头落在一颗小苗。")
            self.assertEqual(chapter_cases(root, "bilibili", chapter), [])
            add_case(root, "bilibili", "珊瑚育苗实录", "https://www.bilibili.com/video/BV123",
                     "原视频口播转写", "珊瑚 育苗", "把镜头落在一颗小苗。",
                     speech_profile={'basis':'transcript','methods':['从一颗小苗解释育苗过程']})
            self.assertEqual([c["title"] for c in chapter_cases(root, "bilibili", chapter)], ["珊瑚育苗实录"])
            self.assertEqual(chapter_cases(root, "douyin", chapter), [])

    def test_retrieval_prefers_the_right_topic_and_platform(self):
        with scratch_dir() as root:
            add_case(root, "bilibili", "家庭阳台水培香草", "https://www.bilibili.com/video/BV123",
                     "作者提供的播放截图", "园艺 水培 罗勒", "先展示根系，再讲搭建条件。")
            result = retrieve_cases(root, "bilibili", "阳台水培罗勒")
            self.assertEqual(result[0]["title"], "家庭阳台水培香草")
            self.assertTrue(all(case["platform"] == "bilibili" for case in result))
            self.assertEqual(retrieve_cases(root, "xiaohongshu", "穿搭"), [])
            self.assertEqual(retrieve_cases(root, "douyin", "旅行"), [])

    def test_batch_keeps_successes_when_one_platform_fails(self):
        variants = [
            {"angle": "通勤实拍", "title": "从通勤开始", "script": "出门前先把水和钥匙放在同一格。", "caption": "通勤实拍"},
            {"angle": "整理习惯", "title": "整理只做一步", "script": "每天回家先清掉包里的临时物件。", "caption": "整理习惯"},
        ]
        pages = [{"title": label, "text": "文案", "image": "团队实拍"}
                 for label in ("封面", "使用场景", "判断要点", "清单")]
        with scratch_dir() as root, patch("core.copywriting.chat_json", side_effect=[
            *({**draft, "script": draft["script"] * 8, "pages": pages} for draft in variants),
            RuntimeError("本地模型暂不可用"), *variants,
        ]) as chat:
            result = generate_batch(root, "通勤背包怎么整理", "侧袋可以放水杯。", ("xiaohongshu", "douyin", "bilibili"), 2)
            directory = Path(result["directory"])
            self.assertTrue((directory / "xiaohongshu.md").is_file())
            self.assertTrue((directory / "bilibili.md").is_file())
            self.assertFalse((directory / "douyin.md").exists())
            self.assertIn("douyin", result["errors"])
            document = (directory / "xiaohongshu.md").read_text(encoding="utf-8")
            self.assertIn("参考案例", document)
            self.assertIn("笔记正文", document)
            self.assertIn("图 4 · 清单", document)
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

    def test_public_work_enters_same_platform_rag_without_storing_raw_excerpt(self):
        url = "https://www.xiaohongshu.com/explore/abc123"
        excerpt = "先写出通勤换包时容易漏带水杯的场景，然后用实际收纳顺序带读者检查每个夹层。"
        metadata = f'<meta property="og:title" content="通勤背包"><meta property="og:description" content="{excerpt}">'
        with scratch_dir() as root, patch("core.copywriting._read_public", return_value=metadata.encode()), \
                patch("core.copywriting.analyze_case", return_value={"keywords": "通勤 背包", "notes": "场景后接清单"}) as analyze:
            result = collect_topic_cases(root, "xiaohongshu", {"url": url})
            self.assertEqual(result["saved"], ["通勤背包"])
            case = retrieve_cases(root, "xiaohongshu", "通勤背包", actual_works=True)[0]
            self.assertEqual(case["url"], url)
            self.assertEqual(case["notes"], "场景后接清单")
            self.assertTrue(case["collected_at"])
            self.assertNotIn(excerpt, str(case))
            self.assertEqual(collect_topic_cases(root, "xiaohongshu", {"url": url})["saved"], [])
            self.assertEqual(analyze.call_count, 1)

    def test_each_xiaohongshu_brief_reaches_only_its_own_draft(self):
        briefs = ("一个人旅行怎么拍背影。只写固定机位。", "朋友帮拍怎么拍走路照。只写走动连拍。")
        pages = [{"title": label, "text": "页面文字", "image": "团队实拍"}
                 for label in ("封面", "场景", "动作", "清单")]
        replies = [{"title": title, "script": line * 8, "caption": "旅拍练习", "pages": pages}
                   for title, line in (("一个人拍背影", "先把手机固定在安全的位置。"),
                                       ("请朋友拍走路照", "先请朋友选定你要走入的画面。"))]
        with scratch_dir() as root, patch("core.copywriting.chat_json", side_effect=replies) as chat:
            batch = generate_batch(root, "旅拍拍照", "可以在步道练习。", ("xiaohongshu",), 2, briefs=briefs)
        self.assertFalse(batch["errors"])
        self.assertEqual([call.args[1]["本篇写作任务"] for call in chat.call_args_list], list(briefs))
        self.assertEqual([draft["angle"] for draft in batch["results"]["xiaohongshu"]["drafts"]],
                         ["一个人旅行怎么拍背影", "朋友帮拍怎么拍走路照"])

    def test_hotspot_writing_has_fresh_report_and_separate_case_reference(self):
        now = dt.datetime.now().astimezone()
        topic = "云台公园新增夜场"
        report = {"title": topic, "url": "https://example.com/news", "published": now.isoformat(),
                  "summary": "公园在本周六开放夜场，入口设在东门。" * 30}
        snapshot = {"articles": [report], "ranks": {"douyin": {"published": now.timestamp(),
                    "entries": [{"title": topic, "url": "https://www.douyin.com/search/example", "rank": 1}]}}}
        model = {"title": "公园夜场怎么走", "script": "入口设在东门。", "caption": "出行信息"}
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot), \
                patch("core.copywriting.chat_json", return_value=model) as chat:
            result = generate_batch(root, topic, "", ("douyin",), 1, hotspot_platform="douyin")
        self.assertEqual(result["source"]["url"], report["url"])
        self.assertEqual(result["hotspot"]["rank"], 1)
        self.assertIn("入口设在东门", chat.call_args.args[1]["事实"])

    def test_fresh_hotspot_refresh_learns_once_and_can_be_retrieved_by_next_draft(self):
        now = dt.datetime.now().astimezone()
        topic = "云台公园新增夜场"
        url = "https://www.xiaohongshu.com/explore/night2026"
        snapshot = {"articles": [], "ranks": {"xiaohongshu": {"published": now.timestamp(),
                    "entries": [{"title": topic, "url": url, "rank": 1}]}}}
        excerpt = "云台公园夜场先介绍入口和时间，再按行走顺序给出路上的提示。"
        metadata = f'<meta property="og:title" content="{topic}"><meta property="og:description" content="{excerpt}">'
        with scratch_dir() as root, patch("core.intake.read_intake", return_value=snapshot), \
                patch("core.local_ai.installed_models", return_value={"qwen3.5:4b"}), \
                patch("core.copywriting._read_public", return_value=metadata.encode()), \
                patch("core.copywriting.analyze_case", return_value={"keywords": "云台公园 夜场", "notes": "先讲入口"}) as analyze:
            self.assertEqual(refresh_hotspot_library(root, now)["saved"], [topic])
            self.assertEqual(refresh_hotspot_library(root, now)["saved"], [])
            self.assertEqual(analyze.call_count, 1)
            self.assertEqual(retrieve_cases(root, "xiaohongshu", topic, actual_works=True)[0]["notes"], "先讲入口")
