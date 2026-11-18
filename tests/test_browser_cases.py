import unittest
from unittest.mock import patch

from test_daily import scratch_dir


class BrowserCaseTests(unittest.TestCase):
    def test_tutorial_collection_replaces_a_promotion_with_another_same_topic_note(self):
        from core.browser_cases import _work
        from core.copywriting import collect_topic_cases,list_cases
        ids=['6a0c1b78000000000702fe3a','6a058bfb000000003501faca','6a4dfa1b000000000702cc44']
        works=[_work({'id':n,'type':'normal','title':'家常美食教程'+str(i),
            'desc':('同款调味料的配料表和零添加介绍。推荐购买。'*4 if i==1 else '真实的家常美食教程，先准备食材，然后分步骤操作。'*3),
            'url':'https://www.xiaohongshu.com/explore/'+n},photo_notes=True) for i,n in enumerate(ids)]
        with scratch_dir() as root, patch('core.copywriting._read_public',return_value=b'<rss><channel/></rss>'), \
                patch('core.browser_cases.collect_browser_cases',return_value={'works':works}) as browser, \
                patch('core.copywriting.chat_json',return_value={'related':False}), \
                patch('core.copywriting.analyze_case',return_value={'keywords':'美食 教程','notes':'按准备与操作分段。'}):
            result=collect_topic_cases(root,'xiaohongshu',{'topic':'家常美食教程'},limit=2,photo_notes=True)
            titles=[c['title'] for c in list_cases(root,'xiaohongshu')]
        self.assertEqual(titles,[works[2]['title'],works[0]['title']])
        self.assertEqual(len(result['source_ids']),2)
        self.assertEqual(browser.call_args.kwargs['limit'],3)

    def test_collected_note_refreshes_assembly_materials_even_if_style_is_already_known(self):
        from core.browser_cases import _work
        from core.copywriting import collect_topic_cases,list_cases
        from core.intake import read_intake,has_report
        from core.source_media import candidates
        data={'id':'6a0c1b78000000000702fe3a',
              'url':'https://www.xiaohongshu.com/explore/6a0c1b78000000000702fe3a?xsec_token=first',
              'type':'normal','title':'番茄炒蛋的做法','desc':'先备好番茄和鸡蛋，再分开炒熟。'*5,
              'time':1779670800000,'author':'食谱作者',
              'images':[{'url':'https://sns-webpic-qc.xhscdn.com/old/notes/1040g3sample000000!webp','width':1080,'height':1440}]}
        first=_work(data,photo_notes=True)
        second=_work({**data,'url':data['url'].replace('first','second')},photo_notes=True)
        with scratch_dir() as root, patch('core.copywriting._read_public',return_value=b'<rss><channel/></rss>'), \
                patch('core.browser_cases.collect_browser_cases',side_effect=[{'works':[first]},{'works':[second]}]), \
                patch('core.copywriting.analyze_case',return_value={'keywords':'番茄 炒蛋','notes':'按备料和下锅两段组织。'}) as model:
            first_result=collect_topic_cases(root,'xiaohongshu',{'topic':'番茄炒蛋'},photo_notes=True)
            second_result=collect_topic_cases(root,'xiaohongshu',{'topic':'番茄炒蛋'},photo_notes=True)
            reports=read_intake(root)['articles']
            refreshed=list_cases(root,'xiaohongshu')
        self.assertEqual(model.call_count,1)
        self.assertEqual(len(reports),1)
        self.assertTrue(has_report(reports[0]))
        self.assertTrue(reports[0]['url'].endswith('second'))
        self.assertTrue(reports[0]['published'].startswith('2026-05-25'))
        self.assertEqual(len(candidates(reports[0])),1)
        self.assertEqual(reports[0]['reference_topics'],['番茄炒蛋'])
        self.assertTrue(refreshed[0]['url'].endswith('second'))
        self.assertIn('发布 2026-05-25',refreshed[0]['evidence'])
        from core.daily_news import story_key
        self.assertEqual(first_result['source_ids'],[story_key(reports[0])])
        self.assertEqual(second_result['source_ids'],first_result['source_ids'])

    def test_browser_work_keeps_accessible_url_and_confirmed_photo_type(self):
        from core.browser_cases import _work
        url = 'https://www.xiaohongshu.com/explore/6a0c1b78000000000702fe3a?xsec_token=public-link'
        data = {'id':'6a0c1b78000000000702fe3a','url':url,'type':'normal',
                'title':'番茄炒蛋怎么做','desc':'正文里的具体操作和调味说明。'*8,
                'stats':{'likedCount':'3.8万','collectedCount':'3.1万'},'images':[{'url':'https://example.com/image.jpg'}]}
        work = _work(data,photo_notes=True)
        self.assertEqual(work['url'],url)
        self.assertIn('3.8万',work['evidence'])
        self.assertEqual(work['excerpt'],data['desc'])
        self.assertIsNone(_work({**data,'type':'video'},photo_notes=True))
        self.assertIsNone(_work({**data,'url':'https://www.xiaohongshu.com/404'},photo_notes=True))

    def test_collect_uses_browser_fallback_and_deduplicates_signed_links(self):
        from core.copywriting import collect_topic_cases,list_cases
        work = {'title':'番茄炒蛋怎么做','url':'https://www.xiaohongshu.com/explore/6a0c1b78000000000702fe3a?xsec_token=link-one',
                'excerpt':'番茄炒蛋切配与调味的正文。'*8,'evidence':'公开图文笔记 · 页面显示点赞 356'}
        with scratch_dir() as root, patch('core.copywriting._read_public',return_value=b'<rss><channel/></rss>'), \
                patch('core.browser_cases.collect_browser_cases',return_value={'works':[work]}) as browser, \
                patch('core.copywriting.analyze_case',return_value={'keywords':'番茄 炒蛋','notes':'从具体做法切入，再解释关键一步。'}):
            first = collect_topic_cases(root,'xiaohongshu',{'topic':'番茄炒蛋'},photo_notes=True)
            self.assertEqual(first['saved'],[work['title']])
            self.assertEqual(list_cases(root,'xiaohongshu')[0]['url'],work['url'])
            browser.return_value={'works':[{**work,'url':work['url'].replace('link-one','link-two')}]}
            second = collect_topic_cases(root,'xiaohongshu',{'topic':'番茄炒蛋'},photo_notes=True)
        self.assertEqual(second['saved'],[])
        self.assertEqual(second['skipped'],1)

    def test_login_requirement_is_reported_without_inventing_a_case(self):
        from core.copywriting import collect_topic_cases
        with scratch_dir() as root, patch('core.copywriting._read_public',return_value=b'<rss><channel/></rss>'), \
                patch('core.browser_cases.collect_browser_cases',return_value={'works':[],'requires_login':True}), \
                patch('core.copywriting.analyze_case') as model:
            result = collect_topic_cases(root,'xiaohongshu',{'topic':'番茄炒蛋'})
        self.assertEqual(result['saved'],[])
        self.assertTrue(result['requires_login'])
        model.assert_not_called()
