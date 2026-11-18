import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from config import get_ffmpeg
from core import scene_index
from test_daily import scratch_dir


class SceneIndexTests(unittest.TestCase):
    def test_original_speech_is_a_time_bounded_locator_separate_from_visual_observations(self):
        with scratch_dir() as root:
            source=root/'source.video';source.write_bytes(b'fixture')
            asset={'path':str(source),'kind':'video','duration':6,'page_url':'original'}
            speech=[{'start':1.,'end':2.,'text':'原片谈到仓储平台。'},
                    {'start':4.,'end':5.,'text':'原片谈到医疗设备。'}]
            with patch('core.scene_index.detect_segments',return_value=[{'start':0.,'end':3.},{'start':3.,'end':6.}]), \
                    patch('core.directing._frame_jpeg',return_value=b'frame'), \
                    patch('core.directing.observe_sequence',return_value=['展台上的受访者。']*3), \
                    patch('core.video_timeline.original_speech',return_value=speech) as transcribe:
                result=scene_index.index_video(asset,root/'index','vision',with_speech=True)
            self.assertEqual([s['speech'] for s in result['segments']],[[speech[0]],[speech[1]]])
            self.assertEqual(result['segments'][0]['observations'],['展台上的受访者。']*3)
            transcribe.assert_called_once()
            # Speech must not contaminate the blind frame cache or require rerunning vision.
            with patch('core.directing.observe_sequence',side_effect=AssertionError('visual cache missed')):
                visual_only=scene_index.index_video(asset,root/'index','vision')
            self.assertTrue(all('speech' not in row for row in visual_only['segments']))

    def test_model_outage_stops_the_batch_and_preserves_completed_scenes_for_resume(self):
        from core.local_ai import LocalAIUnavailable
        with scratch_dir() as root:
            video=root/'source.video';video.write_bytes(b'fixture')
            asset={'path':str(video),'kind':'video','duration':9,'page_url':'original'}
            spans=[{'start':float(i),'end':float(i+3)} for i in (0,3,6)]
            with patch('core.scene_index.detect_segments',return_value=spans), \
                    patch('core.directing._frame_jpeg',return_value=b'frame'), \
                    patch('core.directing.observe_sequence',side_effect=[['展厅中的设备。']*3,LocalAIUnavailable('backend timeout')]) as model:
                with self.assertRaisesRegex(LocalAIUnavailable,'保留.*1'):
                    scene_index.index_video(asset,root/'index','vision')
                self.assertEqual(model.call_count,2)
            with patch('core.directing._frame_jpeg',return_value=b'frame'), \
                    patch('core.directing.observe_sequence',return_value=['后续展台设备。']*3) as model:
                resumed=scene_index.index_video(asset,root/'index','vision')
            self.assertTrue(resumed['complete'])
            self.assertEqual(model.call_count,2)
    def test_real_hard_cut_and_long_shots_produce_bounded_source_intervals(self):
        with scratch_dir() as root:
            video = root/'two-scenes.mp4'
            subprocess.run([get_ffmpeg(), '-v','error','-f','lavfi','-i','color=red:size=320x240:rate=10:duration=3',
                '-f','lavfi','-i','color=blue:size=320x240:rate=10:duration=3',
                '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0','-an','-c:v','libx264',str(video)],
                check=True,capture_output=True)
            spans = scene_index.detect_segments({'path':str(video),'duration':6,'kind':'video'})
        self.assertTrue(any(abs(row['end']-3)<.2 for row in spans))
        self.assertEqual(spans[0]['start'],0)
        self.assertAlmostEqual(spans[-1]['end'],6)
        self.assertTrue(all(0 < row['end']-row['start'] <= 12 for row in spans))
        long = scene_index.segment_bounds([], 31)
        self.assertEqual(long[0]['start'],0)
        self.assertEqual(long[-1]['end'],31)
        self.assertTrue(all(a['end']==b['start'] for a,b in zip(long,long[1:])))
        self.assertTrue(all(0 < row['end']-row['start'] <= 12 for row in long))

    def test_failed_segments_retry_without_reanalyzing_completed_segments(self):
        with scratch_dir() as root:
            video=root/'source.video';video.write_bytes(b'fixture')
            asset={'path':str(video),'duration':25,'kind':'video','page_url':'original','source_report_id':'r'}
            spans=[{'start':0.,'end':10.},{'start':10.,'end':20.},{'start':20.,'end':25.}]
            with patch('core.scene_index.detect_segments',return_value=spans), \
                    patch('core.directing._frame_jpeg',return_value=b'frame'), \
                    patch('core.directing.observe_sequence',side_effect=[['设备在展厅中。']*3,RuntimeError('offline'),['记者站在展厅。']*3]):
                first=scene_index.index_video(asset,root/'index','vision')
            self.assertFalse(first['complete']);self.assertEqual(len(first['segments']),2)
            with patch('core.scene_index.detect_segments',side_effect=AssertionError('cut cache missed')), \
                    patch('core.directing._frame_jpeg',return_value=b'frame'), \
                    patch('core.directing.observe_sequence',return_value=['有人操作界面。']*3) as observe:
                second=scene_index.index_video(asset,root/'index','vision')
            self.assertTrue(second['complete']);self.assertEqual(len(second['segments']),3)
            self.assertEqual(observe.call_count,1)
            self.assertEqual(second['segments'][1]['observations'],['有人操作界面。']*3)
            with patch('core.directing.observe_sequence',side_effect=AssertionError('observation cache missed')):
                self.assertEqual(scene_index.index_video(asset,root/'index','vision'),second)

    def test_changed_file_does_not_reuse_old_visual_observations(self):
        with scratch_dir() as root:
            video=root/'source.video';video.write_bytes(b'old')
            asset={'path':str(video),'duration':3,'kind':'video','page_url':'original'}
            with patch('core.scene_index.detect_segments',return_value=[{'start':0.,'end':3.}]), \
                    patch('core.directing._frame_jpeg',return_value=b'frame'), \
                    patch('core.directing.observe_sequence',return_value=['旧画面。']*3):
                old=scene_index.index_video(asset,root/'index','vision')
            video.write_bytes(b'a different source video')
            with patch('core.scene_index.detect_segments',return_value=[{'start':0.,'end':3.}]), \
                    patch('core.directing._frame_jpeg',return_value=b'frame'), \
                    patch('core.directing.observe_sequence',return_value=['新画面。']*3):
                new=scene_index.index_video(asset,root/'index','vision')
            self.assertNotEqual(old['identity'],new['identity'])
            self.assertEqual(new['segments'][0]['observations'],['新画面。']*3)

    def test_invalid_bounds_are_not_sent_to_the_frame_decoder(self):
        for value in (0,-1,float('inf'),float('nan')):
            with self.subTest(value=value),self.assertRaises(ValueError):
                scene_index.segment_bounds([],value)

    def test_limited_range_video_frames_are_decoded_as_valid_jpeg(self):
        import io
        from PIL import Image
        from core.directing import _frame_jpeg
        with scratch_dir() as root:
            video=root/'limited.mp4'
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i','color=red:size=320x240:rate=10:duration=2',
                '-c:v','libx264','-pix_fmt','yuv420p','-color_range','tv',str(video)],check=True,capture_output=True)
            encoded=_frame_jpeg({'path':str(video)},1.8)
            with Image.open(io.BytesIO(encoded)) as frame:self.assertEqual(frame.size,(768,576))

    def test_audio_tail_past_last_video_frame_does_not_break_scene_observation(self):
        import io
        from PIL import Image
        with scratch_dir() as root:
            video=root/'video.mp4'
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i','color=red:size=320x240:rate=10:duration=2',
                            '-c:v','libx264',str(video)],check=True,capture_output=True)
            asset={'path':str(video),'kind':'video','duration':2.05,'page_url':'source'}
            def observe(frames,_model):
                for frame in frames:
                    with Image.open(io.BytesIO(frame)) as image:self.assertGreater(image.width,0)
                return ['红色画面。']*3
            with patch('core.directing.observe_sequence',side_effect=observe):
                result=scene_index.index_video(asset,root/'index','vision')
            self.assertTrue(result['complete'],result['errors'])
