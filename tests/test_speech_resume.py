import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from config import get_ffmpeg
from core.audio_engine import generate_voice_and_subtitles
from test_daily import scratch_dir


class SpeechResumeTests(unittest.TestCase):
    def test_visual_changes_reuse_speech_but_changed_words_are_synthesized_again(self):
        async def synthesize(_text,_voice,_rate,filename):
            subprocess.run([get_ffmpeg(),'-v','error','-f','lavfi','-i','sine=duration=0.3',
                            '-c:a','libmp3lame',filename],check=True,capture_output=True)
        def case(text='展会现场。'):
            return {'resume':True,'scenes':[{'text':text,'title':'现场'}]}
        with scratch_dir() as root:
            with patch('core.audio_engine._synthesize_text',side_effect=synthesize) as voice:
                first=generate_voice_and_subtitles(case(),'bilibili',root/'visual-v1')
                self.assertEqual(voice.call_count,1)
            with patch('core.audio_engine._synthesize_text',side_effect=AssertionError('unchanged speech synthesized again')):
                second=generate_voice_and_subtitles(case(),'bilibili',root/'visual-v2')
            self.assertAlmostEqual(first['total_duration'],second['total_duration'])
            with patch('core.audio_engine._synthesize_text',side_effect=synthesize) as voice:
                generate_voice_and_subtitles(case('下一段现场。'),'bilibili',root/'visual-v3')
                self.assertEqual(voice.call_count,1)
