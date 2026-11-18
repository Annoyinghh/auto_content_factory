import unittest
from unittest.mock import patch

from test_daily import scratch_dir
from core import copywriting
from core.spoken_style import resolve_reference, sentence_cut, writing_instruction
from core.video_styles import select_video_style, style_context


class SpokenStyleTests(unittest.TestCase):
    def test_photo_and_description_cannot_be_used_as_spoken_reference(self):
        with scratch_dir() as root:
            for evidence in ('公开图文笔记', '作品简介（未读取视频口播）'):
                wrong = dict(title='芯片新闻', url='https://www.xiaohongshu.com/explore/abcdef',
                             evidence=evidence, keywords='芯片', notes='荐股营销')
                with patch('core.copywriting.retrieve_cases', return_value=[wrong]):
                    ref = resolve_reference(root, 'xiaohongshu', '谷歌芯片新进展', explicit=wrong)
                self.assertNotIn('荐股', str(ref))
                self.assertEqual(ref['match_kind'], '同类口播参考（非同题）')

    def test_off_topic_does_not_force_tech_speech_into_cooking(self):
        with scratch_dir() as root:
            self.assertIsNone(resolve_reference(root, 'xiaohongshu', '周末美食烘焙教程'))

    def test_real_speech_profile_survives_library_round_trip(self):
        profile = {'basis': 'transcript', 'methods': ['先提具体问题，再分两步回答'], 'timing_verified': False}
        with scratch_dir() as root:
            copywriting.add_case(root, 'bilibili', '芯片解释', 'https://www.bilibili.com/video/BV12345',
                                 '原视频口播转写', '芯片', '分两步解释', speech_profile=profile)
            ref = resolve_reference(root, 'bilibili', '芯片解释')
            self.assertEqual(ref['speech_profile'], profile)
            self.assertEqual(ref['match_kind'], '同平台相关口播')

    def test_all_video_platforms_have_distinct_executable_writing_rules(self):
        rules = []
        for platform in ('xiaohongshu', 'douyin', 'bilibili'):
            style = select_video_style(platform, 'AI芯片新闻')
            self.assertIsNotNone(style)
            context = style_context(style)
            self.assertTrue(context['speech']['sentence'])
            rules.append(context['speech']['opening'])
            self.assertIn('一镜', writing_instruction({'video_style':context}))
        self.assertEqual(len(set(rules)), 3)

    def test_sentence_alignment_keeps_exact_words_evidence_and_chapter_boundaries(self):
        source = [{'source_id':'r', 'quote':'谷歌发布测试结果，扩展效率96%，共有8192颗芯片。'}]
        scene = {'title':'测试', 'text':'谷歌公布了测试。扩展效率为96%。', 'media':'', 'zoom':False, 'evidence':source}
        draft = {'scenes':[scene, dict(scene,text='另一项结果。')],
                 'chapter_starts':[{'scene':0,'title':'甲'},{'scene':1,'title':'乙'}]}
        cut = sentence_cut(draft)
        self.assertEqual(''.join(x['text'] for x in cut['scenes']), ''.join(x['text'] for x in draft['scenes']))
        self.assertEqual([m['scene'] for m in cut['chapter_starts']], [0,2])
        self.assertEqual(cut['scenes'][1]['evidence'], source)
        self.assertEqual(sentence_cut(cut), cut)

    def test_quoted_punctuation_does_not_break_a_sentence_or_user_edits(self):
        scene = {'title':'引述', 'text':'他说：“开始了吗？已经开始。”随后发布结果。', 'media':'', 'zoom':False}
        self.assertEqual(len(sentence_cut({'scenes':[scene]})['scenes']), 2)
        locked = {'scenes':[scene], 'confirmed_scenes':[0]}
        self.assertEqual(sentence_cut(locked), locked)

    def test_split_does_not_display_another_subjects_number_in_heading(self):
        scene = {'title':'谷歌扩展效率96%', 'text':'谷歌扩展效率为96%。英伟达的另一项指标是75%。', 'media':'', 'zoom':False}
        cut = sentence_cut({'scenes':[scene]})
        self.assertNotIn('96%', cut['scenes'][1]['title'])
        self.assertIn('英伟达', cut['scenes'][1]['title'])

    def test_short_topic_can_use_one_complete_source_but_not_headline_or_weekly(self):
        from core.topic_production import validate_topic_sources
        source = {'summary':'这篇原文提供了完整可核对的信息和具体的事件经过。'*20}
        self.assertTrue(validate_topic_sources([source], 'topic', 120))
        for scope, seconds, reports in [('week_tech',720,[source]), ('topic',720,[source]),
                                         ('topic',120,[{'summary':'只有一句标题。'}]), ('topic',120,[])]:
            with self.subTest(scope=scope,seconds=seconds), self.assertRaises(RuntimeError):
                validate_topic_sources(reports, scope, seconds)

    def test_render_keeps_complete_spoken_sentence_instead_of_splitting_number(self):
        from core.episodes import split_episode_shots
        text = '谷歌云在 MLPerf 基准测试中提交了十项成绩，其中四项来自自研第七代 Ironwood TPU，另外六项采用英伟达 GPU。'
        case = {'scenes':[{'title':'成绩来源','text':text}], 'news':{'source':'原文'},
                'editorial_plan':{'spoken_style_version':'spoken-style-v1'}}
        result = split_episode_shots(case, {'chapters':[], 'target_seconds':120})
        self.assertEqual([s['text'] for s in result['scenes']], [text])

    def test_single_source_topic_exports_a_complete_minute_without_padding(self):
        from core.episodes import episode_case
        from core.publisher import package_case_for_all_platforms
        report={'title':'新闻','source':'原文','url':'https://example.com/news'}
        item={'story':report,'chapters':[{'story':report,'reports':[report]}],
              'target_seconds':120,'topic_job':{'scope':'topic'}}
        case=episode_case(item,'xiaohongshu',{})
        with scratch_dir() as root, patch('core.publisher.generate_voice_and_subtitles',return_value={'total_duration':68}), \
                patch('core.publisher.render_platform_video',side_effect=RuntimeError('render reached')):
            with self.assertRaisesRegex(RuntimeError,'render reached'):
                package_case_for_all_platforms(case,['xiaohongshu'],output_root=root)


if __name__ == '__main__':
    unittest.main()
