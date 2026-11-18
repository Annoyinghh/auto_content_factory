import unittest
from unittest.mock import patch
from pathlib import Path
from core.news_diagrams import visual_for, frames, narration_parts, rate_name
from core.video_styles import diagram_labels
from core.directing import audit_visuals
from core.workflow import write_json, read_edition, edition_path, _prepare_original_media
from core.editorial import plan_chapters
from test_daily import scratch_dir


class NewsDiagramTests(unittest.TestCase):
    def test_rate_captions_handle_spaced_natural_narration(self):
        text='准确率达 76.5%，比 69% 的目标更高。'
        self.assertEqual(rate_name(text,'76.5%'),'准确率')
        self.assertEqual(rate_name(text,'69%'),'目标准确率')
        self.assertEqual(rate_name('系统扩展效率达到了 96%。','96%'),'系统扩展效率')

    def test_diagram_changes_with_each_spoken_fact_without_dropping_attribution(self):
        text='谷歌披露了结果。8192颗芯片约102秒达标。扩展至4096颗芯片时，效率达到96%。数据来自原文。'
        shot={'text':text,'evidence':[{'quote':text}]}
        shot['visual']=visual_for(shot)
        parts=narration_parts(shot)
        self.assertEqual(''.join(p['text'] for p in parts),text)
        self.assertEqual([p['visual']['layout'] for p in parts],['chip_grid','percent_bars'])
        self.assertNotIn('96%',parts[0]['text'])
        self.assertNotIn('8192',parts[1]['text'])

    def test_each_explanation_has_its_own_narration_bound_animation(self):
        examples = [('8192颗Ironwood TPU参加测试。', 'chip_grid'),
                    ('准确率为76.5%，高于69%的目标。', 'percent_bars'),
                    ('代码生成与学习同步进行。', 'parallel_flow'),
                    ('4项使用谷歌自研芯片，另外6项采用英伟达GPU。', 'result_split')]
        for text, kind in examples:
            with self.subTest(kind=kind):
                shot = {'text':text, 'evidence':[{'quote':text}]}
                visual = visual_for(shot)
                self.assertEqual(visual['layout'], kind)
                self.assertTrue(diagram_labels(kind, visual['labels'], text))
                stream = frames(kind,visual['labels'],text,2,4,640,360)
                rendered = list(stream)
                self.assertEqual(len(rendered),8)
                self.assertEqual(len(rendered[0]),640*360*3)
                self.assertNotEqual(rendered[0],rendered[-1])
                late = list(frames(kind,visual['labels'],text,6,1,640,360))
                self.assertNotEqual(late[3],late[5], 'Animation must not freeze after its opening reveal')
                self.assertTrue(audit_visuals({'scenes':[{**shot,'visual':visual}]},[])['ready'])

    def test_fabricated_number_or_unpictured_action_cannot_become_an_animation(self):
        self.assertIsNone(visual_for({'text':'准确率为99%。','evidence':[{'quote':'准确率为76.5%。'}]}))
        self.assertIsNone(visual_for({'text':'机器人将杯子放入盒中。','evidence':[{'quote':'机器人将杯子放入盒中。'}]}))
        self.assertIsNone(visual_for({'text':'准确率为76.5%。'}))
        self.assertEqual(diagram_labels('percent_bars',['99%'],'准确率为76.5%。'),[])

    def test_measured_rate_takes_priority_over_generic_chip_grid(self):
        text = '640颗Ironwood芯片的测试准确率为76.5%。'
        self.assertEqual(visual_for({'text':text,'evidence':[{'quote':text}]})['layout'], 'percent_bars')
        text = '256 块用于预训练基准的 NVIDIA GB300 GPU 在31.96分钟内达到目标质量。'
        visual = visual_for({'text':text,'evidence':[{'quote':text}]})
        self.assertEqual(visual['layout'], 'chip_grid')
        self.assertEqual(visual['labels'], ['256 块 NVIDIA GB300 GPU'])
        text = '英伟达将10240颗GB300的1.38分钟列为本轮该项目最短用时。'
        self.assertEqual(visual_for({'text':text,'evidence':[{'quote':text}]})['labels'], ['10240颗 GB300'])
        self.assertIsNone(visual_for({'text':'6项使用谷歌自研芯片，另外4项采用英伟达GPU。',
                                     'evidence':[{'quote':'4项使用谷歌自研芯片，另外6项采用英伟达GPU。'}]}))

    def test_short_single_news_does_not_repeat_one_report_as_three_versions(self):
        report={'id':'r','title':'芯片测试结果公布','summary':'准确率为76.5%，高于69%的目标。'*20}
        with scratch_dir() as root, patch('core.editorial.chat_json',side_effect=AssertionError('no artificial versions')):
            chapters,_=plan_chapters({'anchor':{'title':report['title']},'reports':[report]},
                                     'xiaohongshu',root/'plan.json',target_seconds=120)
        self.assertEqual(len(chapters),1)
        self.assertTrue(chapters[0]['source_excerpts'])

    def test_normal_creation_collects_originals_and_keeps_user_removals(self):
        report={'id':'r','title':'芯片测试','url':'https://www.ithome.com/1/028/065.htm','summary':'原文'}
        assets=[{'page_url':key,'verified_report_id':'r','automatic_review':'原文已解码'} for key in ('new','removed')]
        with scratch_dir() as root, patch('core.intake._read_report',return_value=report), \
                patch('core.intake._search_reports',return_value=[]), \
                patch('core.topic_production.collect_original_assets',return_value=(assets,[])), \
                patch('core.news_media.download_story_media',side_effect=AssertionError('no stock search')):
            path=edition_path(root,'2026-11-17');path.parent.mkdir(parents=True)
            item={'id':'i','editorial':{'reports':[report]},'media':[assets[1]],'media_review':{'removed':'removed'}}
            write_json(path,{'items':{'xiaohongshu':item}})
            _prepare_original_media(path,'xiaohongshu',item,root)
            saved=read_edition(path)['items']['xiaohongshu']
        self.assertEqual(saved['media_review'],{'removed':'removed','new':'approved'})
        self.assertTrue(saved['original_media_checked'])


if __name__=='__main__':unittest.main()
