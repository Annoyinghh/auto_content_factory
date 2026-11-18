import copy
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image

from config import get_ffmpeg
from core.video_sequence import align_source_narration, expand_sequences
from core.news_frame import frame_box, frame_slide
from core.video_engine import _encode_news_scenes
from core.editing import validate_cut, render_case
from test_daily import scratch_dir


class NewsFrameTests(unittest.TestCase):
    def test_portrait_diagram_uses_the_actual_viewport_without_nested_letterboxing(self):
        scene=dict(title='数据解读',text='准确率为76.5%。',layout='graphic',motion='cut',
                   diagram='percent_bars',labels=['76.5%'])
        case=dict(title='测试',news_frame='source-news-v1',scenes=[scene])
        with scratch_dir() as root, patch('core.video_engine.label_diagram',return_value='diagram.mp4') as diagram, \
                patch('core.video_engine._run_ffmpeg'),patch('pathlib.Path.replace'):
            list(_encode_news_scenes(case,'xiaohongshu',[5.],root,1080,1920,24))
        x0,y0,x1,y1=frame_box(1080,1920,{'diagram':True})
        self.assertEqual(diagram.call_args.kwargs['viewport'],(x1-x0,y1-y0))
        self.assertGreater(y1,1920*.8)
        plate=frame_slide(case,0,1080,1920,'xiaohongshu',video_overlay=True)
        self.assertEqual(plate.getpixel((x0+2,y1-2))[3],0)

    def test_source_excerpt_survives_cut_validation_and_render_preparation(self):
        quote={'source_id':'report-1','quote':'通知明确列出了办理时间和服务范围。'}
        shot=dict(title='通知安排',text='通知列出了办理时间。',media='',zoom=False,evidence=[quote])
        clean=validate_cut({'scenes':[shot,shot]},[])
        self.assertEqual(clean['scenes'][0].get('evidence'),[quote])
        item={'story':{'title':'通知安排','url':'https://example.org/notice','source':'发布单位','category':'资讯'},
              'edit':clean,'media':[]}
        with patch('core.editing.build_news_case',return_value={'title':'通知安排','news':{'series':'资讯'}}):
            case=render_case(item,'bilibili')
        self.assertEqual(case['scenes'][0].get('evidence'),[quote])
        self.assertEqual(case.get('news_frame'),'source-news-v1')

    def scene(self):
        assets = [dict(page_url=key, path=key+'.mp4', kind='video', duration=12) for key in ('a','b')]
        clips = [dict(media=a['page_url'], asset=a, start=0., end=6., intent='对应对象', reason='核对原片', for_text=text)
                 for a,text in zip(assets, ('先介绍第一款设备。','再介绍第二款设备。'))]
        return dict(title='两款设备', text='先介绍第一款设备。再介绍第二款设备。',
                    clip_group=0, narration_group=0, layout='footage', asset=assets[0],
                    motion='cut', visual=dict(layout='footage', start=0., end=6., framing='fit', clips=clips))

    def test_measured_speech_switches_subject_at_its_own_boundary(self):
        original=self.scene()
        speech=align_source_narration([original])
        self.assertEqual([s['text'] for s in speech],['先介绍第一款设备。','再介绍第二款设备。'])
        shots,times=expand_sequences(speech,[2.,8.])
        self.assertEqual([s['asset']['page_url'] for s in shots],['a','b'])
        self.assertEqual(times,[2.,8.])
        self.assertEqual(original,self.scene(),'Preparation must not rewrite saved draft data')

    def test_split_paragraph_is_rejoined_before_binding_measured_speech(self):
        first=self.scene();second=copy.deepcopy(first)
        first['text']='先介绍第一款设备。'
        second['text']='再介绍第二款设备。'
        speech=align_source_narration([first,second])
        self.assertEqual([s['asset']['page_url'] for s in speech],['a','b'])

    def test_unmapped_sentence_and_stale_assignment_stop_before_rendering(self):
        for change in ('missing','stale','mixed'):
            scene=self.scene()
            if change=='missing':scene['visual']['clips'].pop()
            if change=='stale':scene['text']='用户已经换了选题。'
            if change=='mixed':scene['visual']['clips'][1].pop('for_text')
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'口播|对应'):
                align_source_narration([scene])

    def test_unbound_legacy_sequences_keep_existing_timing_behavior(self):
        scene=self.scene()
        for clip in scene['visual']['clips']:clip.pop('for_text')
        self.assertEqual(align_source_narration([scene]),[scene])

    def test_original_notice_image_is_fitted_inside_the_frame(self):
        with scratch_dir() as root:
            image=root/'notice.png'
            Image.new('RGB',(60,120),'red').save(image)
            scene=dict(title='官方通知中的安排',text='对应通知内容。',layout='footage',
                       asset=dict(kind='image',preview=str(image),title='通知原图',page_url='https://example.org/notice'))
            case=dict(title='今日资讯',news_frame='source-news-v1',news={'series':'资讯观察'},scenes=[scene])
            picture=frame_slide(case,0,640,360,'bilibili',video_overlay=False)
            x0,y0,x1,y1=frame_box(640,360)
            self.assertEqual(picture.getpixel(((x0+x1)//2,(y0+y1)//2))[:3],(255,0,0))
            self.assertNotEqual(picture.getpixel((x0+2,(y0+y1)//2))[:3],(255,0,0))

    def test_actual_frame_animation_leaves_news_picture_uncovered(self):
        with scratch_dir() as root:
            source=root/'original.mp4'
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i',
                'color=red:size=160x90:rate=12:duration=2','-c:v','libx264',str(source)],check=True,capture_output=True)
            scene=dict(title='对应原片的标题',text='这是原片对应的口播。',layout='footage',motion='cut',
                       asset=dict(kind='video',path=str(source),duration=2,page_url='https://example.org/source',author='原报道'),
                       visual=dict(start=0.,end=2.,framing='fit'))
            case=dict(title='新闻包装检验',news_frame='source-news-v1',news={'series':'资讯观察'},scenes=[scene])
            files=list(_encode_news_scenes(case,'bilibili',[1.8],root,640,360,12))
            pictures=[]
            for t in (.5,1.3):
                data=subprocess.run([get_ffmpeg(),'-v','error','-ss',str(t),'-i',str(root/files[0]),
                    '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],check=True,capture_output=True).stdout
                pictures.append(Image.frombytes('RGB',(640,360),data))
            x0,y0,x1,y1=frame_box(640,360)
            self.assertGreater(pictures[0].getpixel(((x0+x1)//2,(y0+y1)//2))[0],200)
            self.assertNotEqual(pictures[0].crop((0,0,640,y0)).tobytes(),pictures[1].crop((0,0,640,y0)).tobytes())

    def test_source_audit_records_the_actual_centered_excerpt(self):
        scene=dict(title='设备',text='对应设备。',layout='footage',motion='cut',
                   asset=dict(kind='video',path='original.mp4',duration=20,page_url='https://example.org/news'),
                   visual=dict(start=2.,end=12.,framing='fit'))
        with scratch_dir() as root,patch('core.video_engine.generate_news_slide',return_value=Image.new('RGBA',(128,72))), \
                patch('core.video_engine._run_ffmpeg'),patch('pathlib.Path.replace'):
            audit=[]
            list(_encode_news_scenes({'scenes':[scene]},'bilibili',[2.],root,128,72,10,source_audit=audit))
        self.assertEqual((audit[0]['source_in'],audit[0]['source_out']),(6.,8.))

    def test_portrait_source_and_quoted_column_use_separate_space(self):
        with scratch_dir() as root:
            source=root/'portrait.mp4'
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i',
                'color=red:size=90x160:rate=12:duration=2','-c:v','libx264',str(source)],check=True,capture_output=True)
            asset=dict(kind='video',path=str(source),duration=2,width=90,height=160,page_url='https://example.org/news')
            scene=dict(title='统计发布会',text='对应原文的数据。',layout='footage',motion='cut',asset=asset,
                       evidence=[dict(quote='这一数据来自本次公开发布。')],visual=dict(start=0.,end=2.,framing='fit'))
            files=list(_encode_news_scenes(dict(title='本周经济',news_frame='source-news-v1',scenes=[scene]),
                'bilibili',[1.8],root,640,360,12))
            data=subprocess.run([get_ffmpeg(),'-v','error','-ss','0.5','-i',str(root/files[0]),'-frames:v','1',
                '-f','rawvideo','-pix_fmt','rgb24','pipe:1'],check=True,capture_output=True).stdout
            picture=Image.frombytes('RGB',(640,360),data)
            x0,y0,x1,y1=frame_box(640,360,asset)
            self.assertLess(x1,320)
            self.assertGreater(picture.getpixel(((x0+x1)//2,(y0+y1)//2))[0],200)
            self.assertLess(picture.getpixel((620,300))[0],100)

    def test_news_excerpt_does_not_turn_into_synthetic_footage(self):
        scene=dict(title='通知安排',text='服务时间以通知原文为准。',layout='graphic',motion='orbit',
                   evidence=[dict(source_id='notice',quote='服务时间以通知原文为准。')])
        with scratch_dir() as root,patch('core.video_engine.motion_loop') as synthetic, \
                patch('core.video_engine._run_ffmpeg'),patch('pathlib.Path.replace'):
            list(_encode_news_scenes({'title':'通知安排','news_frame':'source-news-v1','scenes':[scene]},
                                    'bilibili',[2.],root,640,360,12))
        synthetic.assert_not_called()


if __name__=='__main__':unittest.main()
