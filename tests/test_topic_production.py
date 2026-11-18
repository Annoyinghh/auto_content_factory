import datetime as dt
import unittest
from unittest.mock import patch

from core.topic_production import select_weekly_reports, collect_original_assets, produce_topic, _reference_case
from core.intake import _ArticleText
from core.source_media import candidates
from test_daily import scratch_dir


class TopicProductionTests(unittest.TestCase):
    def test_weekly_reference_requires_long_retrospective_work(self):
        cases=[{'title':title,'url':f'https://www.bilibili.com/video/BV{i:010d}',
                'evidence':'公开简介','notes':'真实参考方法'} for i,title in enumerate(
                    ('未来一周重大经济事件','一周经济快讯','全球经济年度回顾'))]
        durations={row['url']:seconds for row,seconds in zip(cases,(900,60,1800))}
        with patch('core.copywriting.retrieve_cases',return_value=cases), \
                patch('core.copywriting._bilibili_work',side_effect=lambda url:{'duration':durations[url]}) as read:
            selected=_reference_case('.', 'bilibili','经济周报','',weekly=True)
        self.assertEqual(selected,cases[2])
        self.assertNotIn(cases[0]['url'],[call.args[0] for call in read.call_args_list])

    def test_explicit_reference_keeps_requested_work_even_for_different_subject(self):
        case={'title':'历史专题','url':'https://www.bilibili.com/video/BV111BeBPEaD/',
              'evidence':'公开简介','notes':'用问题串联事实'}
        with patch('core.copywriting.list_cases',return_value=[case]), \
                patch('core.copywriting.retrieve_cases',return_value=[]) as ranked:
            self.assertEqual(_reference_case('.', 'bilibili','经济','贸易',
                'https://www.bilibili.com/video/BV111BeBPEaD?from=search'),case)
        ranked.assert_not_called()

    def test_resume_carries_current_revisions_and_requires_quality_success(self):
        from core.workflow import write_json, edition_path
        now=dt.datetime(2026,11,16,12,tzinfo=dt.timezone.utc)
        for ready in (False,True):
            with self.subTest(ready=ready),scratch_dir() as root:
                path=edition_path(root,now.date())
                path.parent.mkdir(parents=True)
                item={'id':'job1','edit_revision':'edit2','suggestion_revision':'suggest3',
                      'topic_job':{'stage':'production','publish':False,'error':'前次失败'},
                      'production_audit':{'ready':ready,'message':'画面未覆盖'}}
                write_json(path,{'items':{'bilibili':item}})
                with patch('core.workflow.run_stage',return_value={'video':'full.mp4'}) as stage:
                    if ready:
                        self.assertEqual(produce_topic(now.date(),root,'bilibili',{'id':'job1'},now),{'video':'full.mp4'})
                    else:
                        with self.assertRaisesRegex(RuntimeError,'画面未覆盖'):
                            produce_topic(now.date(),root,'bilibili',{'id':'job1'},now)
                self.assertEqual(stage.call_args.args[0],'auto_cut')
                self.assertEqual(stage.call_args.args[4],{'id':'job1','export':True,
                    'revision':'edit2','suggestion_revision':'suggest3'})
                from core.workflow import read_edition
                job=read_edition(path)['items']['bilibili']['topic_job']
                self.assertEqual(job['stage'],'complete' if ready else 'production')
                self.assertFalse(job['publish'])
                if ready:self.assertNotIn('error',job)

    def test_weekly_selection_excludes_other_subjects_unknown_and_future_dates(self):
        now=dt.datetime(2026,11,16,12,tzinfo=dt.timezone.utc)
        rows=[{'title':title,'category':'科技','published':date,'url':'https://example.org/'+str(i),
               'summary':'原文内容。'*80} for i,(title,date) in enumerate([
                   ('芯片技术进展','2026-11-15T12:00:00+00:00'),
                   ('国家队夺冠','2026-11-15T12:00:00+00:00'),
                   ('人工智能旧新闻','2026-10-01T12:00:00+00:00'),
                   ('机器人新消息',''),('半导体未来消息','2026-11-20T12:00:00+00:00')])]
        selected=select_weekly_reports(rows,'week_tech',now)
        self.assertEqual([r['title'] for r in selected],['芯片技术进展'])

    def test_ithome_lazy_originals_are_read_instead_of_placeholder_icons(self):
        reader=_ArticleText(('id','paragraph'))
        reader.feed('<div id="paragraph"><p><img src="//img.ithome.com/images/v2/t.png" '
                    'data-original="https://img.ithome.com/newsuploadfiles/2026/11/product.jpg" '
                    'width="1200" height="800"></p><p>厂商公开的设备图片。</p></div>')
        reader.close()
        report={'title':'设备发布','url':'https://www.ithome.com/1/027/855.htm','media_candidates':reader.media}
        assets=candidates(report)
        self.assertEqual(len(assets),1)
        self.assertIn('/newsuploadfiles/',assets[0]['download_url'])
        report['media_candidates'][0]['url']='https://img.ithome.com.evil.test/newsuploadfiles/a.jpg'
        self.assertEqual(candidates(report),[])

    def test_economy_selection_does_not_mistake_marketing_title_for_economic_news(self):
        now=dt.datetime(2026,11,16,12,tzinfo=dt.timezone.utc)
        common={'published':now.isoformat(),'url':'https://example.org/news','summary':'真实原文'}
        selected=select_weekly_reports([{**common,'title':'公司首席市场官卸任'},
                                       {**common,'title':'本月汽车零售数据公布'}],'week_economy',now)
        self.assertEqual([r['title'] for r in selected],['本月汽车零售数据公布'])

    def test_collector_uses_article_assets_and_official_capture_not_stock_search(self):
        report={'id':'r1','title':'公告','url':'https://example.org/news','summary':'原文'}
        asset={'page_url':'source#one','kind':'image','path':'original.jpg','preview':'original.jpg'}
        with scratch_dir() as root,patch('core.topic_production.candidates',return_value=[{'source_media_id':'one'}]), \
                patch('core.topic_production.cache_candidate',return_value=asset), \
                patch('core.official_capture.capture_official_notice',return_value=None):
            assets,warnings=collect_original_assets([report],root)
        self.assertEqual(assets[0]['verified_report_id'],'r1')
        self.assertEqual(assets[0]['source_id'],'r1')
        self.assertFalse(warnings)

    def test_no_media_article_uses_original_page_capture(self):
        report={'id':'r1','title':'芯片测试','url':'https://www.ithome.com/1/028/065.htm','summary':'完整原文'}
        asset={'page_url':report['url'],'kind':'image','path':'page.png','capture_kind':'article_page'}
        with scratch_dir() as root, patch('core.topic_production.candidates',return_value=[]), \
                patch('core.official_capture.capture_official_notice',side_effect=[None,asset]) as capture:
            assets,warnings=collect_original_assets([report],root)
        self.assertEqual(assets[0]['verified_report_id'],'r1')
        self.assertTrue(capture.call_args.kwargs['article_page'])
        self.assertFalse(warnings)


if __name__=='__main__':unittest.main()
