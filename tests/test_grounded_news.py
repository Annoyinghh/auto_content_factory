import unittest
from unittest.mock import patch

from core import grounded_news
from test_daily import scratch_dir


class GroundedNewsTests(unittest.TestCase):
    def setUp(self):
        self.assets=[{'page_url':'source','kind':'video','duration':30,'width':1080,'height':1920,
                     'source_title':'展会适老化产品','source_report_id':'r'}]
        self.indices=[{'identity':'version1','media':'source','segments':[{'id':'observed1','start':2.,'end':8.,
                      'observations':['一位参观者站在展台前。','展台旁有白色轮椅。','参观者观察设备。']}]}]
        self.shots=[{'title':'适老生活','text':'展会展示了适老化产品。'}]

    def reply(self,role='context',clip='observed1',text=None):
        return {'shots':[{'index':0,'clips':[{'clip_id':clip,'usage':role,'intent':'展台与轮椅',
                    'for_text':text or self.shots[0]['text']}] if clip else [],'intent':'展台与轮椅','motion':'cut'}]}

    def test_selection_resolves_only_observed_clip_ids_to_real_bounds(self):
        with scratch_dir() as root,patch('core.grounded_news.chat_json',return_value=self.reply()):
            plan,picks=grounded_news.select_clips(self.shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili')
        self.assertEqual((picks[0]['start'],picks[0]['end']),(2.,8.))
        self.assertEqual(picks[0]['media'],'source')
        self.assertEqual(picks[0]['usage'],'context')
        self.assertEqual(plan[0]['layout'],'footage')

    def test_context_footage_cannot_stand_in_for_an_explicit_demonstration(self):
        shots=[{'title':'操作','text':'画面中轮椅正在升降。'}]
        with scratch_dir() as root,patch('core.grounded_news.chat_json',return_value=self.reply(text=shots[0]['text'])):
            _,picks=grounded_news.select_clips(shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili')
        self.assertEqual(picks,{})

    def test_unobserved_action_is_not_downgraded_to_background_when_verification_fails(self):
        shots=[{'title':'操作','text':'画面中轮椅正在升降。'}]
        with scratch_dir() as root,patch('core.grounded_news.chat_json',return_value=self.reply('demonstration',text=shots[0]['text'])), \
                patch('core.directing.compare_observations',return_value={'match':False,'description':'没有升降。'}):
            _,picks=grounded_news.select_clips(shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili')
        self.assertEqual(picks,{})

    def test_unknown_clip_ids_cannot_inject_another_source(self):
        with scratch_dir() as root,patch('core.grounded_news.chat_json',return_value=self.reply(clip='unselected')):
            with self.assertRaises(ValueError):
                grounded_news.select_clips(self.shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili')

    def test_cached_selection_is_reused_for_identical_observed_inputs(self):
        with scratch_dir() as root:
            with patch('core.grounded_news.chat_json',return_value=self.reply()):
                first=grounded_news.select_clips(self.shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili')
            with patch('core.grounded_news.chat_json',side_effect=AssertionError('cache miss')):
                self.assertEqual(grounded_news.select_clips(self.shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili'),first)

    def test_changing_only_old_visual_choices_does_not_rewrite_the_same_narration(self):
        with scratch_dir() as root:
            before=[{**self.shots[0],'media':'old','visual':{'layout':'evidence'}}]
            after=[{**self.shots[0],'media':'source','visual':{'layout':'footage'}}]
            with patch('core.grounded_news.chat_json',return_value=self.reply()):
                first=grounded_news.select_clips(before,self.assets,self.indices,'展会','writer','vision',root,'bilibili')
            with patch('core.grounded_news.chat_json',side_effect=AssertionError('unchanged narration was unnecessarily reselected')):
                self.assertEqual(grounded_news.select_clips(after,self.assets,self.indices,'展会','writer','vision',root,'bilibili'),first)

    def test_each_following_shot_chooses_an_unused_observed_clip(self):
        indices=[{**self.indices[0],'segments':[self.indices[0]['segments'][0],
            {'id':'observed2','start':12.,'end':18.,'observations':['另一位参观者体验轮椅。']*3}]}]
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=[self.reply(),self.reply(clip='observed2')]):
            _,picks=grounded_news.select_clips(self.shots*2,self.assets,indices,'展会','writer','vision',root,'bilibili')
        self.assertEqual({row['clip_id'] for row in picks.values()},{'observed1','observed2'})

    def test_one_narration_can_use_multiple_verified_clips_and_reserves_all_of_them(self):
        indices=[{**self.indices[0],'segments':[self.indices[0]['segments'][0],
            {'id':'observed2','start':12.,'end':18.,'observations':['另一位参观者体验轮椅。']*3},
            {'id':'observed3','start':21.,'end':28.,'observations':['展厅里的采访。']*3}]}]
        first=self.reply();first['shots'][0]['clips'].append({**first['shots'][0]['clips'][0],'clip_id':'observed2'})
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=[first,self.reply(clip='observed3')]) as model:
            _,picks=grounded_news.select_clips(self.shots*2,self.assets,indices,'展会','writer','vision',root,'bilibili')
        self.assertEqual([clip['media'] for clip in picks[0]['clips']],['source','source'])
        self.assertEqual([(clip['start'],clip['end']) for clip in picks[0]['clips']],[(2.,8.),(12.,18.)])
        self.assertEqual(set(model.call_args.args[1]['clips']),{'observed3'})

    def test_demonstration_verifies_the_actual_narrated_clause_not_an_invented_target(self):
        text='画面中参观者观察设备。'
        reply=self.reply('demonstration',text=text)
        shots=[{'title':'现场','text':text+'展会展示了适老化产品。'}]
        with scratch_dir() as root,patch('core.grounded_news.chat_json',return_value=reply), \
                patch('core.directing.compare_observations',return_value={'match':True,'description':'已见观察设备。'}) as verify:
            _,picks=grounded_news.select_clips(shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili')
        self.assertEqual(verify.call_args.args[1],text)
        self.assertEqual(picks[0]['clips'][0]['for_text'],text)

    def test_visual_alignment_cannot_invent_a_sentence_outside_the_narration(self):
        with scratch_dir() as root,patch('core.grounded_news.chat_json',return_value=self.reply(text='产品已普及。')):
            with self.assertRaisesRegex(ValueError,'口播'):
                grounded_news.select_clips(self.shots,self.assets,self.indices,'展会','writer','vision',root,'bilibili')

    def test_short_proposal_is_revised_with_the_measured_source_duration_budget(self):
        indices=[{**self.indices[0],'segments':[self.indices[0]['segments'][0],
            {'id':'observed2','start':12.,'end':18.,'observations':['另一位参观者体验轮椅。']*3}]}]
        longer=[{**self.shots[0],'text':self.shots[0]['text']*5}]
        revised=self.reply();revised['shots'][0]['clips'].append({**revised['shots'][0]['clips'][0],'clip_id':'observed2'})
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=[self.reply(),revised]) as model:
            _,picks=grounded_news.select_clips(longer,self.assets,indices,'展会','writer','vision',root,'bilibili')
        self.assertEqual(len(picks[0]['clips']),2)
        self.assertGreater(model.call_args.args[1]['insufficient_duration'][0]['needed_seconds'],6)
