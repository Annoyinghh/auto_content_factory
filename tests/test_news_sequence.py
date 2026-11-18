import subprocess
import unittest
from unittest.mock import patch

from PIL import Image

from config import get_ffmpeg
from core.editing import render_case, validate_cut
from core.video_engine import _encode_news_scenes
from core.video_sequence import expand_sequences
from test_daily import scratch_dir


class NewsSequenceTests(unittest.TestCase):
    def test_graphic_fragments_do_not_shadow_a_later_original_clip_group(self):
        graphic={'layout':'graphic','text':'图解口播。','visual':{'layout':'evidence'}}
        scenes,durations=expand_sequences([graphic,graphic,self.scene(1)],[2.,2.,6.])
        self.assertEqual([s['asset']['page_url'] for s in scenes[2:]],['a','b'])
        self.assertEqual(durations,[2.,2.,3.,3.])

    def test_portrait_original_stays_sharp_in_a_landscape_source_backdrop(self):
        with scratch_dir() as root:
            source=root/'portrait.mp4'
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i',
                'color=red:size=64x112:rate=10:duration=1','-vf','drawbox=x=24:y=0:w=16:h=112:color=blue:t=fill',
                '-c:v','libx264',str(source)],check=True,capture_output=True)
            asset={'kind':'video','path':str(source),'duration':1,'width':64,'height':112}
            scene={'layout':'footage','asset':asset,'motion':'cut',
                   'visual':{'start':0,'end':1,'framing':'fit'}}
            with patch('core.video_engine.generate_news_slide',return_value=Image.new('RGBA',(128,72),(0,0,0,0))):
                files=list(_encode_news_scenes({'scenes':[scene]},'bilibili',[.8],root,128,72,10))
            raw=subprocess.run([get_ffmpeg(),'-v','error','-ss','0.3','-i',str(root/files[0]),
                '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],check=True,capture_output=True).stdout
            picture=Image.frombytes('RGB',(128,72),raw)
            self.assertGreater(picture.crop((0,0,16,72)).resize((1,1)).getpixel((0,0))[0],90,
                               'The sides must use the real source, not empty black columns')
            self.assertGreater(picture.getpixel((64,36))[2],180,'Keep the central detail sharp and uncropped')

    def setUp(self):
        self.assets = [dict(page_url=key, path=key+'.mp4', kind='video', duration=10)
                       for key in ('a','b')]
        self.clips = [dict(media=key, start=1., end=4., intent='展厅实景', reason='已观察的场景')
                      for key in ('a','b')]
        self.visual = dict(layout='footage', start=1., end=4., framing='fit',
                           intent='展厅实景', reason='已观察的场景', clips=self.clips)
        self.shot = dict(title='展会现场', text='展品集中呈现了不同生活场景。', media='a', zoom=False,
                         motion='cut', visual=self.visual)

    def test_only_selected_bounded_sequence_clips_survive_edit_validation(self):
        clean = validate_cut({'scenes':[self.shot,self.shot]},self.assets)
        self.assertEqual(clean['scenes'][0]['visual'].get('clips'),self.clips)
        for bad in ({**self.clips[1],'media':'unselected'}, {**self.clips[1],'end':11},
                    {**self.clips[1],'start':float('nan')}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                validate_cut({'scenes':[{**self.shot,'visual':{**self.visual,'clips':[self.clips[0],bad]}},
                                        self.shot]},self.assets)

    def test_sequence_assets_reach_the_normal_render_case(self):
        item={'story':dict(title='选题',category='创作',source='原文',url=''),
              'edit':{'scenes':[self.shot,self.shot]}}
        with patch('core.media_review.usable_media',return_value=self.assets):
            case=render_case(item,'bilibili')
        self.assertEqual([row['asset']['page_url'] for row in case['scenes'][0]['visual']['clips']],['a','b'])
        self.assertEqual({row['page_url'] for row in case['news']['media']},{'a','b'})

    def scene(self, group=0):
        return dict(layout='footage',clip_group=group,text='同一段口播。',motion='cut',
                    asset=self.assets[0],visual={**self.visual,'clips':[
                        {**clip,'asset':asset} for clip,asset in zip(self.clips,self.assets)]})

    def test_split_narration_continues_across_real_clips_without_repeating_them(self):
        scenes,durations=expand_sequences([self.scene(),self.scene()],[2.,4.])
        self.assertEqual(durations,[2.,1.,3.])
        self.assertEqual([row['asset']['page_url'] for row in scenes],['a','a','b'])
        self.assertEqual([(row['visual']['start'],row['visual']['end']) for row in scenes],[(1.,3.),(3.,4.),(1.,4.)])
        self.assertEqual(sum(durations),6.)
        self.assertNotIn('clips',scenes[0]['visual'])

    def test_short_sequences_remain_an_explicit_material_gap(self):
        with self.assertRaisesRegex(RuntimeError,'时长不足'):
            expand_sequences([self.scene()],[10.])

    def test_adjacent_verified_interview_segments_can_continue_under_neighboring_paragraphs(self):
        first=self.scene(0);second=self.scene(1)
        first['visual']['clips']=first['visual']['clips'][:1]
        second['visual']['clips']=[{**first['visual']['clips'][0],'start':4.,'end':7.}]
        for shot in (first,second):shot.update(context_sequence=True,chapter_title='同一章')
        scenes,durations=expand_sequences([first,second],[5.,3.])
        self.assertAlmostEqual(sum(durations),8.)
        ranges=[(s['visual']['start'],s['visual']['end']) for s in scenes]
        self.assertAlmostEqual(ranges[0][0],1.)
        self.assertAlmostEqual(ranges[-1][1],7.)
        self.assertTrue(all(abs(a[1]-b[0])<1e-6 for a,b in zip(ranges,ranges[1:])))
        second['context_sequence']=False
        with self.assertRaisesRegex(RuntimeError,'时长不足'):
            expand_sequences([first,second],[5.,3.])

    def test_source_frame_rounding_does_not_turn_a_complete_clip_into_a_false_gap(self):
        scene=self.scene()
        scene['visual']['clips']=scene['visual']['clips'][:1]
        _,durations=expand_sequences([scene],[4.52])
        self.assertAlmostEqual(sum(durations),4.52)

    def test_actual_encoding_cuts_to_the_second_video_without_freezing_the_first(self):
        with scratch_dir() as root:
            for asset,color in zip(self.assets,('red','blue')):
                asset['path']=str(root/(asset['page_url']+'.mp4'))
                subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i',
                                f'color={color}:size=128x72:rate=10:duration=5','-c:v','libx264',asset['path']],
                               check=True,capture_output=True)
            scenes,durations=expand_sequences([self.scene()],[6.])
            with patch('core.video_engine.generate_news_slide',return_value=Image.new('RGBA',(128,72),(0,0,0,0))):
                files=list(_encode_news_scenes({'scenes':scenes},'bilibili',durations,root,128,72,10))
            self.assertEqual(len(files),2)
            means=[]
            for name in files:
                raw=subprocess.run([get_ffmpeg(),'-v','error','-ss','1','-i',str(root/name),'-frames:v','1',
                    '-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],check=True,capture_output=True).stdout
                means.append(tuple(raw))
            self.assertGreater(means[0][0],means[0][2]+100)
            self.assertGreater(means[1][2],means[1][0]+100)
