import subprocess
import unittest
from unittest.mock import patch

from PIL import Image
from config import get_ffmpeg
from core.editing import validate_cut
from core.video_engine import _encode_news_scenes
from tests.test_works_library import scratch_dir


class ClipWindowTests(unittest.TestCase):
    def test_single_shot_preview_renders_footage_without_episode_group_metadata(self):
        from core.editing import render_case
        with scratch_dir() as root:
            source=root/'source.mp4'
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i','testsrc2=size=128x72:rate=10:duration=1',
                '-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True)
            asset={'kind':'video','page_url':'clip','path':str(source),'duration':1,'video_format':'mov'}
            visual={'layout':'footage','start':0,'end':1,'framing':'fit','intent':'真实动作','reason':'已核对'}
            item={'story':{'title':'动作预览','category':'创作','source':'本机','url':''},
                  'edit':{'scenes':[{'title':'动作','text':'这是对应的实际动作。','media':'clip','zoom':False,'visual':visual}]}}
            with patch('core.media_review.usable_media',return_value=[asset]):
                case=render_case(item,'douyin')
            self.assertEqual(case['scenes'][0].get('layout'),'footage')
            case['scenes'][0].pop('clip_group',None)
            with patch('core.video_engine.generate_news_slide',return_value=Image.new('RGBA',(128,72),(0,0,0,0))):
                files=list(_encode_news_scenes(case,'douyin',[.8],root,128,72,10))
            decoded=subprocess.run([get_ffmpeg(),'-v','error','-i',str(root/files[0]),'-f','null','NUL'],capture_output=True)
            self.assertEqual(decoded.returncode,0,decoded.stderr)

    def test_edit_preserves_end_and_rejects_ranges_outside_the_source(self):
        asset={'kind':'video','page_url':'clip','duration':10}
        visual={'layout':'footage','framing':'fit','start':1,'end':4,'intent':'切配','reason':'原片对应动作'}
        scene={'title':'切配','text':'先切番茄。','media':'clip','zoom':False,'visual':visual}
        self.assertEqual(validate_cut({'scenes':[scene,scene]},[asset])['scenes'][0]['visual'].get('end'),4)
        for end in (0,11,float('nan'),True):
            with self.subTest(end=end),self.assertRaises(ValueError):
                validate_cut({'scenes':[{**scene,'visual':{**visual,'end':end}},scene]},[asset])

    def test_short_continuous_range_does_not_play_the_following_action_or_freeze(self):
        with scratch_dir() as root:
            source=root/'source.mp4'
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i','testsrc2=size=128x72:rate=10:duration=2',
                '-f','lavfi','-i','color=blue:size=128x72:rate=10:duration=2','-filter_complex','concat=n=2:v=1:a=0',
                '-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True,capture_output=True)
            asset={'kind':'video','path':str(source),'duration':4,'video_format':'mov'}
            scene={'layout':'footage','clip_group':0,'asset':asset,'motion':'cut',
                   'visual':{'start':0,'end':2,'framing':'fit'}}
            with patch('core.video_engine.generate_news_slide',return_value=Image.new('RGBA',(128,72),(0,0,0,0))):
                files=list(_encode_news_scenes({'scenes':[scene]},'douyin',[3],root,128,72,10))
            raw=subprocess.run([get_ffmpeg(),'-v','error','-i',str(root/files[0]),'-vf','fps=2,scale=32:18',
                '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],check=True,capture_output=True).stdout
            frames=[raw[i:i+32*18*3] for i in range(0,len(raw),32*18*3)]
            self.assertEqual(len(frames),6)
            self.assertGreaterEqual(len(set(frames)),5)
            last=frames[-1]
            self.assertGreater(sum(last[0::3])/len(last[0::3]),20,'Last frame must not be the excluded blue scene')

    def test_insufficient_range_is_not_padded_into_a_completed_shot(self):
        with scratch_dir() as root:
            scene={'layout':'footage','clip_group':0,'asset':{'kind':'video','path':'unused.mp4','duration':10},
                   'visual':{'start':1,'end':2,'framing':'fit'}}
            with patch('core.video_engine.generate_news_slide',return_value=Image.new('RGBA',(128,72))), \
                    self.assertRaisesRegex(RuntimeError,'片段.*不足|不足.*片段'):
                list(_encode_news_scenes({'scenes':[scene]},'douyin',[6],root,128,72,10))


if __name__=='__main__':
    unittest.main()
