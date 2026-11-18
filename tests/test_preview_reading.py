import unittest
from unittest.mock import patch

from core.workflow import _render_preview,_render_trial,read_edition,write_json
from test_daily import scratch_dir


class PreviewReadingTests(unittest.TestCase):
    def test_opening_preview_has_only_the_chapter_and_source_it_contains(self):
        with scratch_dir() as root:
            output=root/'bilibili';item={'id':'work','editorial':{'reports':[]},'edit':{'scenes':[]},'edit_revision':'rev'}
            edition=root/'edition.json';write_json(edition,{'items':{'bilibili':item}})
            with patch('core.editing.render_case',return_value=self.case()), \
                    patch('core.audio_engine.generate_voice_and_subtitles',return_value={'scene_durations':[2.],'total_duration':2.}), \
                    patch('core.video_engine.render_platform_video',return_value=output/'video.mp4'), \
                    patch('core.reading.capture_frame',return_value=True):
                result=_render_preview(edition,'bilibili',item,output)
            self.assertEqual(len(result['reading_pages']),1)
            self.assertEqual(result['reading_pages'][0]['source_url'],'https://example.com/a')
            self.assertEqual(result['reading_pages'][0]['start_ms'],0)

    def case(self):
        return {'title':'资讯合辑','scenes':[{'title':'现场','text':'先核对这一条报道。','layout':'brief'},
            {'title':'结果','text':'再说明另一条报道。','layout':'brief'}],
            'chapter_starts':[{'scene':0,'title':'现场'},{'scene':1,'title':'结果'}],
            'news':{'source':'原文','chapter_sources':{'现场':[{'title':'现场原文','source':'报道甲','url':'https://example.com/a'}],
                                                       '结果':[{'title':'结果原文','source':'报道乙','url':'https://example.com/b'}]}}}

    def test_full_trial_carries_exact_chapter_times_and_sources(self):
        with scratch_dir() as root,patch('core.audio_engine.generate_voice_and_subtitles',return_value={
                'scene_durations':[2.,3.],'total_duration':5.}), \
                patch('core.video_engine.render_platform_video',return_value=root/'video.mp4'), \
                patch('core.reading.capture_frame',return_value=True):
            result=_render_trial(self.case(),'bilibili',root)
        self.assertEqual([p['start_ms'] for p in result['reading_pages']],[0,2000])
        self.assertEqual(result['reading_pages'][1]['source_url'],'https://example.com/b')

    def test_cached_full_trial_gets_real_chapter_metadata_without_reencoding(self):
        with scratch_dir() as root:
            output=root/'bilibili';movie=output/'production/work/full-preview/key/video.mp4'
            movie.parent.mkdir(parents=True);movie.write_bytes(b'previous verified output')
            item={'id':'work','edit':{'scenes':[]},'edit_revision':'rev','full_preview_revision':'rev',
                  'full_preview':{'video':str(movie),'duration':5.}}
            edition=root/'edition.json';write_json(edition,{'items':{'bilibili':item}})
            with patch('core.editing.render_case',return_value=self.case()),patch('core.workflow._render_key',return_value='key'), \
                    patch('core.workflow._render_trial',side_effect=AssertionError('Verified movie was reencoded')), \
                    patch('core.audio_engine.generate_voice_and_subtitles',return_value={'scene_durations':[2.,3.],'total_duration':5.}), \
                    patch('core.reading.capture_frame',return_value=True):
                result=_render_preview(edition,'bilibili',item,output,whole=True)
            self.assertEqual([p['start_ms'] for p in result['reading_pages']],[0,2000])
            self.assertEqual(read_edition(edition)['items']['bilibili']['full_preview']['reading_pages'],result['reading_pages'])
            self.assertEqual(movie.read_bytes(),b'previous verified output')
