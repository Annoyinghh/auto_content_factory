import unittest
from unittest.mock import patch
from test_daily import scratch_dir

from core.video_timeline import validate_segments, resolve_windows


class VideoTimelineTests(unittest.TestCase):
    def test_managed_original_reuses_its_speech_index_between_drafts(self):
        from core.video_timeline import source_windows
        with scratch_dir() as root, patch('core.video_timeline.prepare_speech_model'), \
                patch('core.video_timeline.transcribe_video',return_value=[]) as transcribe:
            folder=root/'material-library'/('source-'+'a'*20)
            asset={'kind':'video','origin':'article','duration':61,'source_media_id':'a'*20,
                   'path':str(folder/'asset_0.video')}
            for name in ('draft-a','draft-b'):
                source_windows(asset,[{'text':'观察动作'}],{},'writer',root/name)
            self.assertEqual([c.args[1] for c in transcribe.call_args_list],[folder/'.speech']*2)

    def test_verified_recipe_section_allows_visual_lag_without_crossing_versions(self):
        from core.video_timeline import source_windows
        segments = [
            {'id':0,'start':0,'end':2,'text':'第一种是混炒版'},
            {'id':1,'start':10,'end':13,'text':'准备下锅'},
            {'id':2,'start':13,'end':17,'text':'均匀受热'},
            {'id':3,'start':20,'end':23,'text':'完成这一步'},
            {'id':4,'start':30,'end':32,'text':'第二种是另一版'}]
        chosen = {0:[{'start':10,'end':13,'text':'准备下锅'}],
                  1:[{'start':20,'end':23,'text':'完成这一步'}]}
        with scratch_dir() as root, patch('core.video_timeline.prepare_speech_model'), \
                patch('core.video_timeline.transcribe_video',return_value=segments), \
                patch('core.video_timeline.choose_windows',return_value=chosen):
            windows = source_windows({'kind':'video','origin':'article','duration':50},
                [{'text':'下锅'},{'text':'完成'}],{'source_section':'第一种混炒版'},'writer',root/'new-draft'/'timeline')
            self.assertEqual(len(list((root/'new-draft'/'timeline').glob('windows-*.json'))),1)
        self.assertEqual([row['end'] for row in windows],[30,30])

    def test_numbered_article_section_uses_the_demonstration_not_the_intro(self):
        from core.video_timeline import _spoken_section
        rows = [{'id':i,'start':t,'end':t+1,'text':text} for i,(t,text) in enumerate((
            (2,'第一种为混炒版'), (5,'第二种为蒜香版'), (15,'第一种为混炒版'),
            (19,'把食材搅匀'), (26,'第一种方法更方便'), (39,'第二种是蒜香版')))]
        self.assertEqual([r['id'] for r in _spoken_section(rows,'第一种混炒版')], [2,3,4])
        self.assertEqual(_spoken_section(rows,'第二种蒜香版'), [])

    def test_silent_video_still_has_bounded_visual_candidates(self):
        from core.video_timeline import source_windows
        asset = {'kind':'video','path':'silent.mp4','duration':61}
        with scratch_dir() as root, patch('core.video_timeline.prepare_speech_model'), \
                patch('core.video_timeline.transcribe_video',return_value=[]), \
                patch('core.video_timeline.choose_windows') as choose:
            windows = source_windows(asset,[{'text':'观察动作'}],{},'writer',root)
        choose.assert_not_called()
        self.assertTrue(windows)
        self.assertTrue(all(0 <= row['start'] < row['end'] <= 61 and row['end']-row['start'] <= 6 for row in windows))
        self.assertTrue(any(row['start']>50 for row in windows))

    def test_silent_handle_does_not_cross_next_spoken_step_and_cache_tracks_brief(self):
        from core.video_timeline import source_windows
        segments = [{'id':0,'start':10,'end':13,'text':'切配'},
                    {'id':1,'start':17,'end':20,'text':'下锅'}]
        chosen = {0:[{'start':10,'end':13,'text':'切配'}]}
        with scratch_dir() as root, patch('core.video_timeline.prepare_speech_model'), \
                patch('core.video_timeline.transcribe_video',return_value=segments), \
                patch('core.video_timeline.choose_windows',return_value=chosen) as choose:
            args = ({'kind':'video','duration':30},[{'text':'切配'}])
            first = source_windows(*args,{'notes':['第一种']},'writer',root)
            self.assertEqual(first[0]['end'],17)
            self.assertEqual(source_windows(*args,{'notes':['第一种']},'writer',root),first)
            self.assertEqual(choose.call_count,1)
            source_windows(*args,{'notes':['第二种']},'writer',root)
            self.assertEqual(choose.call_count,2)

    def test_only_finite_in_range_speech_is_indexed(self):
        rows = [{'start':0,'end':2,'text':'切番茄'},
                {'start':2,'end':3,'text':'   '},
                {'start':3,'end':float('inf'),'text':'伪时间'},
                {'start':4,'end':12,'text':'越过原片'},
                {'start':5,'end':6,'text':'低置信度噪音','no_speech_prob':.95}]
        self.assertEqual(validate_segments(rows,10),[{'id':0,'start':0.,'end':2.,'text':'切番茄'}])

    def test_model_selects_existing_ids_and_cannot_supply_its_own_times(self):
        segments = [{'id':0,'start':11.2,'end':14.1,'text':'把番茄切小'},
                    {'id':1,'start':14.1,'end':17.4,'text':'打散鸡蛋'},
                    {'id':2,'start':42,'end':45,'text':'下锅'}]
        choices = {'shots':[{'index':0,'ranges':[{'first':0,'last':1}]}]}
        self.assertEqual(resolve_windows(choices,segments,1),{0:[{'start':11.2,'end':17.4,'text':'把番茄切小 打散鸡蛋'}]})
        for ranges in ([{'first':0,'last':9}], [{'first':1,'last':0}],
                       [{'first':0,'last':2}], [{'first':0,'last':0,'start':0,'end':500}]):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                resolve_windows({'shots':[{'index':0,'ranges':ranges}]},segments,1)

    def test_missing_duplicate_or_invented_shot_is_not_silently_used(self):
        segment = [{'id':0,'start':1,'end':3,'text':'切配'}]
        for rows in ([],[{'index':1,'ranges':[]}],[{'index':0,'ranges':[]},{'index':0,'ranges':[]}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                resolve_windows({'shots':rows},segment,1)
        self.assertEqual(resolve_windows({'shots':[{'index':0,'ranges':[]}]},segment,1),{0:[]})


if __name__ == '__main__':
    unittest.main()
