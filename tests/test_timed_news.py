import unittest
from unittest.mock import patch

from core import grounded_news,workflow
from test_daily import scratch_dir


class TimedNewsTests(unittest.TestCase):
    def test_every_clause_needs_a_matching_clip_before_a_bundle_is_accepted(self):
        asset={'page_url':'original','kind':'video','duration':10}
        segments=[{'id':'one','start':0.,'end':10.,'observations':['展台上的原型机。']}]
        def choose(_instruction,context,_schema,**_kwargs):
            return {'shots':[{'index':0,'bundle':next(iter(context['bundles'])),
                             'clause_for_clip':{'one':0},'intent':'原型机','motion':'cut'}]}
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=choose):
            plan,picks=grounded_news.select_clips([{'title':'两类设备','text':'先介绍原型机。再介绍加工中心。'}],
                [asset],[{'media':'original','segments':segments}],'展区','writer','editor',root,'bilibili',durations=[7.])
        self.assertFalse(picks)
        self.assertEqual(plan[0]['layout'],'evidence')

    def test_reselection_can_measure_speech_without_reusing_stale_clip_bindings(self):
        clip={'for_text':'用户修改前的口播。'}
        draft={'scenes':[{'text':'用户修改后的口播。','visual':{'clips':[clip]}}]}
        def prepare(item,_platform):
            self.assertNotIn('clips',item['edit']['scenes'][0]['visual'])
            return {'scenes':[{'text':draft['scenes'][0]['text'],'narration_group':0}]}
        with scratch_dir() as root,patch('core.editing.render_case',side_effect=prepare), \
                patch('core.audio_engine.generate_voice_and_subtitles',return_value={'scene_durations':[3.]}):
            timing=workflow._narration_timing({'id':'work'},draft,'bilibili',root)
        self.assertEqual(timing,{0:3.})
        self.assertEqual(draft['scenes'][0]['visual']['clips'],[clip])

    def test_duration_candidates_include_a_coherent_source_instead_of_only_mixed_fillers(self):
        lengths=[3.52,3.76,6.6,3.28,3.16,3.12,9.24,9.86]
        available={str(i):{'media':'warehouse' if i<5 else 'other','start':0.,'end':length}
                   for i,length in enumerate(lengths)}
        bundles=grounded_news._duration_bundles(available,29.4)
        self.assertTrue(any(all(available[key]['media']=='warehouse' for key in b['clip_ids'])
                            for b in bundles.values()))
        self.assertTrue(all(b['seconds']>=29.4*2/3 for b in bundles.values()))

    def test_original_speech_locates_interviews_without_replacing_visible_action_checks(self):
        asset={'page_url':'original','kind':'video','duration':12}
        segments=[{'id':str(i),'start':float(i),'end':float(i+1),'observations':['人物站在展台前。'],
                   'speech':[{'start':float(i),'end':float(i+1),
                              'text':'智能仓储平台采用本土研发。' if i>=8 else '全球消费展区介绍。'}]}
                  for i in range(12)]
        def choose(_instruction,context,_schema,**_kwargs):
            self.assertTrue(set(map(str,range(8,12)))<=set(context['clips']))
            self.assertTrue(all('recognized_speech' in row for row in context['clips'].values()))
            bundle=next(iter(context['bundles']))
            return {'shots':[{'index':0,'bundle':bundle,'clause_for_clip':dict.fromkeys(context['clips'],0),
                             'intent':'展台采访','motion':'cut'}]}
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=choose), \
                patch('core.directing.compare_observations',return_value={'match':False,'description':'没有可见操作。'}) as check:
            _plan,picks=grounded_news.select_clips([{'title':'智能仓储平台','text':'画面中正在操作智能仓储平台。'}],
                [asset],[{'media':'original','segments':segments}],'本土研发','writer','editor',root,'bilibili',durations=[3.])
            self.assertFalse(picks)
            self.assertEqual(check.call_args.args[0],['人物站在展台前。'])

    def test_single_scene_reselection_keeps_an_untouched_approved_speech_group(self):
        whole='原来已经核对过的完整配音。'
        draft={'scenes':[{},{}],'editorial_plan':{'narration_segments':{'1':[whole]}}}
        item={'id':'work','video_style':{'id':'roundup'}}
        def render(work,_platform):
            kept=(work['edit'].get('editorial_plan') or {}).get('narration_segments',{}).get('1')
            tail=kept or ['重新拆开的前半段。','重新拆开的后半段。']
            return {'scenes':[{'narration_group':0,'text':'新目标。'},
                             *({'narration_group':1,'text':text} for text in tail)]}
        values={'新目标。':2.,whole:9.,'重新拆开的前半段。':5.,'重新拆开的后半段。':6.}
        with scratch_dir() as root,patch('core.directing.production_models',return_value={'design':'d','editing':'e'}), \
                patch('core.directing.direct_cut',return_value={}) as direct, \
                patch('core.editing.render_case',side_effect=render), \
                patch('core.audio_engine.generate_voice_and_subtitles',side_effect=lambda case,*_: {
                    'scene_durations':[values[s['text']] for s in case['scenes']]}), \
                patch('core.workflow._render_key',return_value='timing'):
            workflow._direct_scenes(item,draft,'bilibili',root/'visual',[],{0})
        self.assertEqual(direct.call_args.kwargs['durations'][1],9.)

    def test_frozen_short_tail_is_not_merged_a_second_time(self):
        from core.episodes import split_episode_shots
        chunks=['这是已经生成并测量完成的第一段口播。','完。']
        case={'chapter_starts':[{'scene':0,'title':'阶段'}],'news':{'source':'原文'},
              'narration_segments':{'0':chunks},'scenes':[{'title':'阶段','text':''.join(chunks),'narration_group':0,
                 'asset':{'kind':'video','page_url':'clip'},'visual':{'layout':'footage'}}]}
        result=split_episode_shots(case,{'chapters':[{'story':{'title':'阶段','source':'原文'}}]})
        self.assertEqual([s['text'] for s in result['scenes']],chunks)

    def test_stale_speech_fragments_never_replace_a_users_edited_text(self):
        from core.episodes import split_episode_shots
        text='这是用户重新修改后的口播。'
        case={'chapter_starts':[{'scene':0,'title':'阶段'}],'news':{'source':'原文'},
              'narration_segments':{'0':['这是旧的口播。']},'scenes':[{'title':'阶段','text':text,'narration_group':0,
                 'asset':{'kind':'video','page_url':'clip'},'visual':{'layout':'footage'}}]}
        result=split_episode_shots(case,{'chapters':[{'story':{'title':'阶段','source':'原文'}}]})
        self.assertEqual(''.join(s['text'] for s in result['scenes']),text)

    def test_bundle_playback_follows_its_assigned_narration_clauses(self):
        asset={'page_url':'original','path':'original.mp4','kind':'video','duration':15}
        segments=[{'id':str(i),'start':begin,'end':end,'observations':['机械手操作零件。']}
                  for i,(begin,end) in enumerate(((0,4),(4,10),(10,15)))]
        def choose(_instruction,context,_schema,**_kwargs):
            bundle=next(key for key,value in context['bundles'].items() if len(value['clip_ids'])==3)
            return {'shots':[{'index':0,'bundle':bundle,'clause_for_clip':{'0':1,'1':0,'2':0},
                             'intent':'机械手操作','motion':'cut'}]}
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=choose), \
                patch('core.directing.compare_observations',return_value={'match':True,'description':'对应操作。'}):
            _plan,picks=grounded_news.select_clips([{'title':'机械手','text':'先抓取零件。再放置零件。'}],[asset],
                [{'media':'original','segments':segments}],'展区','writer','editor',root,'bilibili',durations=[17.])
        self.assertEqual([c['clip_id'] for c in picks[0]['clips']],['1','2','0'])
        self.assertEqual(picks[0]['clip_id'],'1')

    def test_measured_timeline_speech_stays_whole_when_reselected_as_video(self):
        from core.episodes import split_episode_shots
        text='这段已经测量过的完整口播需要保持原来的配音边界。'*5
        case={'chapter_starts':[{'scene':0,'title':'阶段'}],'news':{'source':'原文'},
              'narration_segments':{'0':[text]},'scenes':[{'title':'阶段','text':text,'narration_group':0,
                 'asset':{'kind':'video','page_url':'clip'},'visual':{'layout':'footage'}}]}
        result=split_episode_shots(case,{'chapters':[{'story':{'title':'阶段','source':'原文'}}]})
        self.assertEqual([s['text'] for s in result['scenes']],[text])

    def test_a_failed_demonstration_check_cannot_leave_a_partial_short_bundle(self):
        asset={'page_url':'original','path':'original.mp4','kind':'video','duration':15}
        segments=[{'id':str(i),'start':begin,'end':end,'observations':['机械手在设备区抓取零件。']}
                  for i,(begin,end) in enumerate(((0,4),(4,10),(10,15)))]
        def choose(_instruction,context,_schema,**_kwargs):
            bundle=next(key for key,value in context['bundles'].items() if len(value['clip_ids'])==3)
            return {'shots':[{'index':0,'bundle':bundle,'clause_for_clip':dict.fromkeys(context['clips'],0),
                             'intent':'抓取零件','motion':'cut'}]}
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=choose), \
                patch('core.directing.compare_observations',side_effect=[{'match':True,'description':'抓取。'},
                                                                       {'match':False,'description':'只有静态设备。'}]):
            plan,picks=grounded_news.select_clips([{'title':'机械手','text':'画面中机械手正在抓取零件。'}],
                [asset],[{'media':'original','segments':segments}],'展区','writer','editor',root,'bilibili',durations=[17.])
        self.assertFalse(picks)
        self.assertEqual(plan[0]['layout'],'evidence')

    def test_measured_group_duration_reaches_the_normal_news_director(self):
        case={'scenes':[{'narration_group':0,'text':'第一句。'},
                        {'narration_group':0,'text':'第二句。'}, {'narration_group':1,'text':'最后一句。'}]}
        item={'id':'work','video_style':{'id':'roundup'}}
        with scratch_dir() as root,patch('core.directing.production_models',return_value={'design':'d','editing':'e'}), \
                patch('core.directing.direct_cut',return_value={}) as direct, \
                patch('core.editing.render_case',return_value=case), \
                patch('core.audio_engine.generate_voice_and_subtitles',return_value={'scene_durations':[2.,3.,7.]}), \
                patch('core.workflow._render_key',return_value='timing'):
            workflow._direct_scenes(item,{'scenes':[{},{}]},'bilibili',root/'visual',[],{0,1})
        self.assertEqual(direct.call_args.kwargs.get('durations'),{0:5.,1:7.})

    def test_timed_selection_returns_only_a_complete_verified_bundle(self):
        asset={'page_url':'original','path':'original.mp4','kind':'video','duration':15}
        segments=[{'id':str(i),'start':begin,'end':end,'observations':['展区实拍，参观者观察设备。']}
                  for i,(begin,end) in enumerate(((0,4),(4,10),(10,15)))]
        shot={'title':'展区观察','text':'本届展会让参观者接触不同的工业设备。'}
        def choose(_instruction,context,_schema,**_kwargs):
            bundle=next(key for key,value in context['bundles'].items() if len(value['clip_ids'])==3)
            return {'shots':[{'index':0,'bundle':bundle,'clause_for_clip':dict.fromkeys(context['clips'],0),
                             'intent':'展区实拍','motion':'cut'}]}
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=choose):
            plan,picks=grounded_news.select_clips([shot],[asset],[{'media':'original','segments':segments}],
                '展区','writer','editor',root,'bilibili',durations=[17.])
        self.assertEqual(plan[0]['layout'],'footage')
        clips=picks[0]['clips']
        self.assertEqual([c['clip_id'] for c in clips],['0','1','2'])
        self.assertGreaterEqual(sum(c['end']-c['start'] for c in clips),17*2/3)

    def test_reserved_source_clips_cannot_be_reused_to_make_up_duration(self):
        asset={'page_url':'original','path':'original.mp4','kind':'video','duration':15}
        segments=[{'id':str(i),'start':begin,'end':end,'observations':['展区实拍。']}
                  for i,(begin,end) in enumerate(((0,4),(4,10),(10,15)))]
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=AssertionError('Reserved footage was reused')):
            plan,picks=grounded_news.select_clips([{'title':'展区','text':'这一段需要足够的原片。'}],[asset],
                [{'media':'original','segments':segments}],'展区','writer','editor',root,'bilibili',
                durations=[17.],reserved=['1'])
        self.assertFalse(picks)
        self.assertEqual(plan[0]['layout'],'evidence')

    def test_an_insufficient_observed_pool_remains_an_explicit_gap(self):
        asset={'page_url':'original','path':'original.mp4','kind':'video','duration':2}
        with scratch_dir() as root,patch('core.grounded_news.chat_json',side_effect=AssertionError('No valid bundle exists')):
            plan,picks=grounded_news.select_clips([{'title':'展区','text':'这段口播需要更长的真实素材。'}],
                [asset],[{'media':'original','segments':[{'id':'only','start':0,'end':2,'observations':['展区实拍。']}]}],
                '展区','writer','editor',root,'bilibili',durations=[20.])
        self.assertFalse(picks)
        self.assertEqual(plan[0]['layout'],'evidence')
