import datetime as dt
import json
import socket
import unittest
from io import BytesIO
from unittest.mock import patch

from core.daily_news import story_key
from core.editorial import confirm_sources, plan_chapters, source_options, write_batches
from test_daily import scratch_dir


class EditorialBriefTests(unittest.TestCase):
    def test_scope_retrieval_keeps_only_real_selected_passages(self):
        from core.editorial import scope_excerpts
        rows = {'1':{'source_id':'r','quote':'第一种做法先拌匀蛋液再下锅。'},
                '2':{'source_id':'r','quote':'第二种做法需要把番茄去皮。'}}
        with patch('core.editorial.chat_json', return_value={'ids':['1']}):
            result = scope_excerpts('做菜', '怎样做？', {'notes':['只讲第一种做法']},rows)
        self.assertEqual(result, {'1':rows['1']})
        with patch('core.editorial.chat_json', return_value={'ids':['forged']}):
            with self.assertRaises(ValueError):
                scope_excerpts('做菜','怎样做？',{},rows)

    def test_instructional_video_format_is_not_news_bulletin(self):
        from core.editorial import _short_format
        from core.video_styles import select_video_style
        style = select_video_style('douyin', '番茄炒蛋家常教程')
        self.assertNotEqual(_short_format('douyin', 60, video_style=style), '抖音快报')
        self.assertEqual(_short_format('douyin', 60, video_style=select_video_style('douyin', '今日热点')), '抖音快报')

    def test_next_chapter_receives_all_previous_narration_to_avoid_repeating_steps(self):
        report = {**self.rows[0], 'id': self.ids[0]}
        editorial = {'anchor': self.anchor, 'reports': [report]}
        chapters = [{'story': {'title': title}, 'question': title, 'reports': [report]}
                    for title in ('准备', '下锅')]
        seen = []
        def writer(context, *args, **_options):
            seen.append(context.get('earlier_narration', ''))
            return {'source_ids': [report['id']], 'scenes': [
                {'title': context['chapter'], 'text': context['chapter']+'有不同的动作与条件需要说明。',
                 'media': '', 'motion': 'cut', 'zoom': False}]}
        with scratch_dir() as root, patch('core.editorial._write_batch', side_effect=writer):
            write_batches({'editorial':editorial, 'chapters':chapters, 'target_seconds':60, 'editorial_plan':{}},
                          'douyin', root, lambda _:[], lambda *_:None)
        self.assertEqual(seen[0], '')
        self.assertIn('准备有不同的动作与条件需要说明。', seen[1])

    def test_one_source_short_video_does_not_force_repeated_chapters(self):
        report = {**self.rows[0], 'id': self.ids[0]}
        outline = {'throughline':'怎样操作？', 'chapters':[
            {'title':'操作过程', 'question':'怎样操作？', 'source_ids':[report['id']]}]}
        with scratch_dir() as root, patch('core.editorial.chat_json', return_value=outline):
            chapters, _ = plan_chapters({'anchor':{**self.anchor, 'title':report['title']}, 'reports':[report]}, 'douyin',
                                         root/'outline.json', target_seconds=60)
        self.assertEqual(len(chapters), 1)

    def test_one_source_short_video_can_compare_two_later_procedures(self):
        report = {**self.rows[0], 'id': self.ids[0], 'summary': '前文资料。' * 180 + '蒜香版在起锅前加入蒜末。'}
        outline = {'throughline': '两种做法怎样下锅？', 'chapters': [
            {'title': title, 'question': f'{title}怎样操作？', 'source_ids': [report['id']]}
            for title in ('混炒', '蒜香')]}
        with scratch_dir() as root, patch('core.editorial.chat_json', return_value=outline) as model:
            chapters, _ = plan_chapters({'anchor': {**self.anchor, 'title': report['title']}, 'reports': [report]}, 'douyin',
                                        root/'outline.json', target_seconds=60)
        self.assertIn('蒜香版在起锅前加入蒜末。', model.call_args.args[1]['reports'][0]['summary'])
        self.assertEqual([chapter['story']['title'] for chapter in chapters], ['混炒', '蒜香'])

    def test_numbered_version_keeps_its_steps_and_excludes_other_versions(self):
        from core.editorial import procedure_sections
        report_text = ('第五种蒜香版\r\n\r\n先煎蛋，再炒番茄。\r\n\r\n'
                       '【1】把番茄切成大颗粒。\r\n【2】放蒜末翻炒后出锅。\r\n'
                       '第六种煎蛋版\r\n【1】把番茄切成圆片。\r\n【2】逐个煎蛋并翻面。\r\n'
                       '下面开始总结\r\n番茄与鸡蛋的用量可自行调节。')
        sections = procedure_sections(report_text)
        self.assertEqual(list(sections), ['第五种蒜香版','第六种煎蛋版'])
        self.assertIn('放蒜末翻炒', sections['第五种蒜香版'])
        self.assertNotIn('圆片', sections['第五种蒜香版'])
        self.assertNotIn('总结', sections['第六种煎蛋版'])

    def test_tutorial_navigation_is_not_sent_as_a_writing_step(self):
        report = {**self.rows[0], 'id': self.ids[0], 'summary':
                  '第一种家常版\n详细步骤如下▼▼▼\n【1】把番茄切成颗粒。\n【2】搅匀蛋液后下锅。\n\n' + '来源背景。' * 65}
        chapter = {'story': {'title': '第一种家常版'}, 'question': '怎样操作？',
                   'reports': [report], 'source_section': '第一种家常版'}
        cut = {'source_ids': [report['id']], 'scenes': [
            {'title': '切配', 'text': '先把番茄切小。', 'media': '', 'zoom': False},
            {'title': '下锅', 'text': '搅匀蛋液后下锅。', 'media': '', 'zoom': False}]}
        with scratch_dir() as root, patch('core.editorial._write_chapter_parts', return_value=cut) as writer:
            write_batches({'editorial': {'reports': [report]}, 'chapters': [chapter],
                           'target_seconds': 60, 'editorial_plan': {}},
                          'douyin', root, lambda _: [], lambda *_: None, writing_only=True)
        self.assertTrue(writer.call_args.args[0]['ordered_steps'])
        self.assertTrue(all('步骤如下' not in row['quote'] for row in writer.call_args.args[3].values()))

    def test_short_scene_schema_leaves_room_to_finish_a_sentence(self):
        from core.editorial import _write_chapter_parts
        text = '把番茄切成小颗粒后和已经调好味的蛋液拌匀，再倒进滑过油的热锅里，保持受热均匀，炒到适合自己的口感后出锅。'
        def writer(context, schema, *args, **_options):
            limit = schema['properties']['scenes']['items']['properties']['text']['maxLength']
            self.assertGreaterEqual(limit, len(text))
            return {'scenes':[{'text':text}], 'source_ids':['r']}
        with scratch_dir() as root, patch('core.editorial._write_batch', side_effect=writer):
            _write_chapter_parts({'writing_only':True,'position':'片头'}, [], [], {'1':{'quote':'原文'}},
                                 7,264,root/'batch.json',short=True)

    def setUp(self):
        self.now = dt.datetime.now().astimezone()
        self.rows = [
            {"title": title, "url": f"https://example.com/{i}", "source": "日报",
             "summary": "观测人员说明该项工程的测量过程与方法。" * 24, "published": self.now.isoformat()}
            for i, title in enumerate(("钻探进展", "观测方法", "未勾选的文章"))
        ]
        self.ids = [story_key(row) for row in self.rows]
        self.anchor = {"kind": "article", "id": self.ids[0]}
        self.snapshot = {"articles": self.rows, "ranks": {}}

    def confirm(self, ids, brief):
        return confirm_sources(self.snapshot, {"anchor": self.anchor, "source_ids": ids, "brief": brief}, self.now)

    def test_local_source_suggestions_rank_related_candidates_without_contacting_ai(self):
        related = {**self.rows[1], "title": "钻探记录"}
        snapshot = {**self.snapshot, "articles": [self.rows[2], related, self.rows[0]]}
        with patch("core.editorial.chat_json", side_effect=AssertionError("Unselected reports reached AI")):
            options = source_options(snapshot, self.anchor, self.now, recommend=False, local_rank=True)
        self.assertEqual(options["recommended"], [self.ids[0], story_key(related)])
        self.assertEqual({row["id"] for row in options["reports"]},
                         {self.ids[0], self.ids[2], story_key(related)})

    def test_brief_accepts_one_report_and_normalizes_duplicate_user_inputs(self):
        key = self.ids[1]
        editorial = self.confirm([key], {"notes": [" 聚焦测量方法 ", "聚焦测量方法"],
                                        "asset_paths": ["D:/private/source.jpg", "D:/private/source.jpg"],
                                        "focus_id": "note:1",
                                        "sequence": ["asset:1", "note:1", f"report:{key}", "asset:0"]})
        self.assertEqual([r["id"] for r in editorial["reports"]], [key])
        self.assertEqual(editorial["brief"], {
            "focus_id": "note:0", "notes": ["聚焦测量方法"], "asset_paths": ["D:/private/source.jpg"],
            "sequence": ["asset:0", "note:0", f"report:{key}"],
        })
        with self.assertRaises(ValueError):
            confirm_sources(self.snapshot, {"anchor": self.anchor, "source_ids": [key]}, self.now)

    def test_only_selected_public_links_are_re_read_as_evidence(self):
        from core.editorial import read_editorial_link

        link = "https://publisher.example/science"
        body = ("<html><head><title>外部原文标题</title></head><article>"
                + "".join(f"<p>观测人员公布了关于这项工程的测量记录，并介绍了如何选择适用的现场测量方案，避免错误解读。{i}。</p>"
                          for i in range(9)) + "</article></html>").encode()
        from urllib3.response import HTTPResponse

        def response(pool, method, target, **options):
            self.assertEqual((pool.host, pool.assert_hostname, target), ("93.184.215.14", "publisher.example",
                                                                        "/science"))
            self.assertEqual(options["headers"]["Host"], "publisher.example")
            return HTTPResponse(body=BytesIO(body), preload_content=False, status=200,
                                headers={"Content-Type": "text/html; charset=utf-8"})

        def dns(host, *args, **kwargs):
            self.assertEqual(host, "publisher.example")
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.215.14", 443))]

        with patch("socket.getaddrinfo", side_effect=dns), \
                patch("urllib3.HTTPSConnectionPool.urlopen", autospec=True, side_effect=response):
            report = read_editorial_link(link, self.now)
            self.assertEqual(report["title"], "外部原文标题")
            self.assertEqual(report["published"], "")
            editorial = self.confirm([report["id"]], {"extra_reports": [
                {**report, "summary": "伪造正文" * 100},
                {"id": "unselected", "url": "https://unselected.example/", "summary": "无关"},
            ]})
        self.assertEqual(editorial["reports"], [report])
        self.assertNotIn("extra_reports", editorial["brief"])

    def test_manual_link_rejects_redirects_to_http_and_private_addresses(self):
        from core.editorial import read_editorial_link
        from urllib3.response import HTTPResponse

        def dns(host, *args, **kwargs):
            address = {"publisher.example": "93.184.215.14", "private.example": "127.0.0.1",
                       "translated.example": "64:ff9b::7f00:1"}[host]
            return [(socket.AF_INET6 if ":" in address else socket.AF_INET,
                     socket.SOCK_STREAM, 0, "", (address, 443))]

        for url in ("http://publisher.example/insecure", "https://private.example/", "https://translated.example/"):
            with self.subTest(url=url), patch("socket.getaddrinfo", side_effect=dns) as resolve, \
                    patch("urllib3.HTTPSConnectionPool.urlopen", side_effect=lambda *args, **kwargs: HTTPResponse(
                        body=BytesIO(), preload_content=False, status=302, headers={"Location": url})) as request:
                with self.assertRaises(ValueError):
                    read_editorial_link("https://publisher.example/news", self.now)
                self.assertEqual(resolve.call_count, 1 if url.startswith("http:") else 2)
                self.assertEqual(request.call_count, 1)

    def test_known_public_publisher_uses_its_pinned_tls_proxy_address(self):
        from core.editorial import read_editorial_link
        from urllib3.response import HTTPResponse

        body = ("<title>公开报道</title><article><p>" +
                "公共图书馆发布周末开放通知，馆方提供可核对的公开服务说明。" * 20 +
                "</p></article>").encode()
        dns = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("198.18.0.1", 443))]
        with patch("socket.getaddrinfo", return_value=dns), \
                patch("urllib3.HTTPSConnectionPool.urlopen", autospec=True, side_effect=lambda *args, **kwargs: HTTPResponse(
                    body=BytesIO(body), preload_content=False, status=200,
                    headers={"Content-Type": "text/html; charset=utf-8"})) as request:
            with self.assertRaises(ValueError):
                read_editorial_link("https://unlisted.example/article", self.now)
            self.assertEqual(request.call_count, 0)
            self.assertEqual(read_editorial_link("https://www.news.cn/article", self.now)["title"], "公开报道")
            pool = request.call_args.args[0]
            self.assertEqual((pool.host, pool.assert_hostname, pool.cert_reqs),
                             ("198.18.0.1", "www.news.cn", "CERT_REQUIRED"))

    def test_brief_rejects_missing_evidence_and_unselected_focus(self):
        for ids, brief in [
            ([], {"notes": ["请把这里的数字当作证据"]}),
            (self.ids[:2], {"focus_id": self.ids[2]}),
            (self.ids[:2], {"notes": ["内容"], "sequence": ["note:9"]}),
            (self.ids[:2], {"notes": ["字" * 2001]}),
        ]:
            with self.subTest(brief=brief), self.assertRaises(ValueError):
                self.confirm(ids, brief)

    def test_outline_follows_selected_order_and_regenerates_after_focus_changes(self):
        selected = self.ids[:2][::-1]
        editorial = self.confirm(selected, {"notes": ["优先讲测量的办法"], "focus_id": "note:0",
                                             "asset_paths": ["D:/private/photo.jpg"]})
        calls = []

        def model(instructions, context, schema, **_kwargs):
            calls.append(context)
            self.assertIn("不可当作事实", instructions)
            self.assertEqual([row["id"] for row in context["reports"]], selected)
            self.assertNotIn("D:/private", json.dumps(context))
            return {"throughline": "怎么确认进展？", "chapters": [
                {"title": title, "question": "核对证据", "source_ids": self.ids[:2]}
                for title in ("变化", "影响")
            ]}

        with scratch_dir() as directory, patch("core.editorial.chat_json", side_effect=model):
            outline = directory / "outline.json"
            chapters, previous = plan_chapters(editorial, "xiaohongshu", outline, target_seconds=60)
            self.assertEqual([row["id"] for row in chapters[0]["reports"]], selected)
            plan_chapters(editorial, "xiaohongshu", outline, previous=chapters, previous_plan=previous,
                          target_seconds=60)
            changed = self.confirm(selected, {"notes": ["优先讲测量的办法"], "focus_id": selected[0]})
            plan_chapters(changed, "xiaohongshu", outline, previous=chapters, previous_plan=previous,
                          target_seconds=60)
        self.assertEqual([call["brief"]["focus_id"] for call in calls], ["note:0", selected[0]])

    def test_writing_revisits_cached_batches_for_new_notes(self):
        selected = self.ids[0]
        editorial = self.confirm([selected], {"notes": ["解释测量步骤"], "focus_id": "note:0",
                                              "asset_paths": ["D:/private/hidden.jpg", "D:/private/draft.jpg"]})
        chapter = {"story": {"title": "实际进展"}, "question": "观测如何进行？", "reports": editorial["reports"]}
        item = {"editorial": editorial, "chapters": [chapter], "target_seconds": 60,
                "editorial_plan": {"throughline": "观测如何进行？"},
                "media": [{"local": True, "title": "draft.jpg", "page_url": "local:asset", "kind": "image",
                           "brief_asset_index": 1}]}
        calls = []

        def writer(context, schema, reports, visuals, excerpts, count, target, **_options):
            calls.append(context)
            self.assertNotIn("private", json.dumps(context))
            self.assertNotIn("draft.jpg", json.dumps(context))
            self.assertEqual({row["source_id"] for row in excerpts.values()}, {selected})
            self.assertEqual(context["visuals"][0]["brief_id"], "asset:1")
            return {"source_ids": [selected], "scenes": [
                {"title": "进展", "text": "可以先了解这项观测的步骤。", "motion": "cut", "media": "", "zoom": False,
                 "evidence": [next(iter(excerpts.values()))]},
                {"title": "方法", "text": "这些步骤都围绕工程测量展开。", "motion": "cut", "media": "", "zoom": False,
                 "evidence": [next(iter(excerpts.values()))]},
            ]}

        with (scratch_dir() as directory, patch("core.editorial._write_batch", side_effect=writer),
              patch("core.editorial.scope_excerpts", side_effect=lambda _title, _question, _brief, excerpts: excerpts)):
            for _ in range(2):
                write_batches(item, "xiaohongshu", directory, lambda _: [], lambda *_: None)
            updated = self.confirm([selected], {"notes": ["解释仪器用途"], "focus_id": "note:0",
                                                "asset_paths": editorial["brief"]["asset_paths"]})
            write_batches({**item, "editorial": updated}, "xiaohongshu", directory, lambda _: [], lambda *_: None)
            updated["brief"]["asset_paths"][0] = "D:/private/replacement.jpg"
            write_batches({**item, "editorial": updated}, "xiaohongshu", directory, lambda _: [], lambda *_: None)
        self.assertEqual([call["brief"]["notes"] for call in calls],
                         [["解释测量步骤"], ["解释仪器用途"], ["解释仪器用途"]])

    def test_selected_passage_from_a_long_paragraph_can_be_cited(self):
        primary, background = self.ids[:2]
        paragraph = "观测人员用现场仪器连续记录了工程测量过程" * 25
        self.rows[0]["summary"] = paragraph + "。"
        quote = paragraph[:280]
        editorial = self.confirm([primary, background], {"focus_id": primary, "focus_quote": quote})
        used = []

        def writer(context, schema, reports, visuals, excerpts, count, target, **_options):
            passage = next((row for row in excerpts.values() if row["quote"] == quote), None)
            if passage:
                used.append(passage["source_id"])
            return {"source_ids": [report["id"] for report in reports], "scenes": [{
                "title": context["chapter"], "text": f"接下来讲清楚{context['chapter']}。",
                "motion": "cut", "media": "", "zoom": False,
                "evidence": [passage or next(iter(excerpts.values()))],
            }]}

        outline = {"throughline": "怎样确认进展？", "chapters": [
            {"title": title, "question": "怎样确认结果？", "source_ids": [background]}
            for title in ("工程进展", "观测过程")
        ]}
        with scratch_dir() as directory, patch("core.editorial.chat_json", return_value=outline), \
                patch("core.editorial._write_batch", side_effect=writer):
            chapters, plan = plan_chapters(editorial, "xiaohongshu", directory / "outline.json", target_seconds=60)
            write_batches({"editorial": editorial, "chapters": chapters, "target_seconds": 60,
                           "editorial_plan": plan}, "xiaohongshu", directory, lambda _: [], lambda *_: None)
        self.assertEqual(used, [primary])
