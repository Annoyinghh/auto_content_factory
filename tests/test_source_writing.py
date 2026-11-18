import unittest
from unittest.mock import patch

from core import editorial,scene_index
from test_daily import scratch_dir


class SourceWritingTests(unittest.TestCase):
    def test_a_mixed_copy_and_number_repair_keeps_the_configured_fact_writer(self):
        responses=[{'scenes':[{'index':i,'focus':'根据证据改写。','text':'这是有来源的完整口播。'}]} for i in range(2)]
        with patch('core.local_ai.model_for',return_value='acf-writer-qwen35:9b'), \
                patch('core.local_ai.installed_models',return_value={'acf-writer:8b'}), \
                patch('core.editorial.chat_json',side_effect=responses) as model:
            editorial._rewrite_shots([{'text':'原稿。'},{'text':'旧数字。'}],
                [{'index':0,'forbidden':['连续照抄'],'evidence':[]},
                 {'index':1,'forbidden':[],'unsupported_numbers':['80'],'evidence':[]}])
            self.assertNotIn('model',model.call_args.kwargs)

    def test_copy_repair_uses_the_available_text_model_and_factual_repair_keeps_the_writer(self):
        response={'scenes':[{'index':0,'focus':'改变语序，保留证据。','text':'这是重组后的完整口播。'}]}
        scenes=[{'text':'待修正的完整口播。'}]
        with patch('core.local_ai.model_for',return_value='acf-writer-qwen35:9b'), \
                patch('core.local_ai.installed_models',return_value={'acf-writer:8b'}), \
                patch('core.editorial.chat_json',return_value=response) as model:
            editorial._rewrite_shots(scenes,[{'index':0,'forbidden':['连续照抄的原文'],'evidence':[]}])
            self.assertEqual(model.call_args.kwargs.get('model'),'acf-writer:8b')
            self.assertNotIn('forbidden', model.call_args.args[1]['scenes'][0])
            self.assertNotIn('text', model.call_args.args[1]['scenes'][0])
            self.assertIn('先说结果', model.call_args.args[0])
            editorial._rewrite_shots(scenes,[{'index':0,'review_findings':['改正无依据的结论'],'evidence':[]}])
            self.assertNotIn('model',model.call_args.kwargs)

    def test_copy_repair_keeps_other_configured_models_and_works_without_the_text_backup(self):
        response={'scenes':[{'index':0,'focus':'重组证据。','text':'这是完整的改写口播。'}]}
        for primary,installed in [('another-writer',{'acf-writer:8b'}),('acf-writer-qwen35:9b',set())]:
            with self.subTest(primary=primary),patch('core.local_ai.model_for',return_value=primary), \
                    patch('core.local_ai.installed_models',return_value=installed), \
                    patch('core.editorial.chat_json',return_value=response) as model:
                editorial._rewrite_shots([{'text':'待修改。'}],[{'index':0,'forbidden':['重复原文'],'evidence':[]}])
                self.assertNotIn('model',model.call_args.kwargs)

    def test_title_repair_does_not_feed_unsupported_old_dates_back_to_the_model(self):
        def repair(_instruction,context,_schema):
            self.assertNotIn('title',context['scenes'][0])
            return {'titles':[{'index':0,'title':'首次参展时的技术支持'}]}
        scenes=[{'title':'2018年首届展会','text':'八年前，艾伦为进口叉车提供技术支持。'}]
        with patch('core.editorial.chat_json',side_effect=repair):
            self.assertTrue(editorial.align_news_titles(scenes))
        self.assertEqual(scenes[0]['title'],'首次参展时的技术支持')

    def test_article_provenance_makes_approved_footage_available_to_each_citing_chapter(self):
        quote='原文记载了设备用途和展台参观过程。'
        report={'id':'r','title':'设备实拍','summary':quote*100}
        chapters=[{'story':{**report,'title':title},'question':title,'reports':[report],
                   'source_excerpts':{'1':{'source_id':'r','quote':quote}}} for title in ('设备用途','参观过程')]
        asset={'page_url':'original','kind':'video','duration':12,'source_report_id':'r','title':'原片','chapters':[0]}
        item={'editorial':{'reports':[report]},'editorial_plan':{'throughline':'设备'},'chapters':chapters,
              'media':[asset],'media_review':{'original':'approved'},'target_seconds':720}
        indices=[{'media':'original','segments':[{'id':'actual','start':0.,'end':8.,'observations':['展台设备。']}]}]
        def write(context,*_args,**_kwargs):
            return {'scenes':[{'title':context['chapter']+tail,'text':context['chapter']+tail+'。','media':'','zoom':False}
                              for tail in ('片段甲','片段乙')],'source_ids':['r']}
        with scratch_dir() as root,patch('core.editorial._write_batch',side_effect=write) as model:
            editorial.write_batches(item,'bilibili',root,lambda _:[],lambda *_:None,
                                     writing_only=True,source_indexes=indices)
        self.assertEqual([len(c.args[0]['source_footage']) for c in model.call_args_list],[1,1])

    def test_an_explicitly_empty_source_index_reaches_the_writing_part_as_a_gap(self):
        context={'position':'中段','writing_only':True,'question':'设备的用途？'}
        excerpts={'1':{'source_id':'r','quote':'报道说明设备的用途。'}}
        finished={'scenes':[{'text':'用途已说明。'}],'source_ids':['r']}
        with scratch_dir() as root,patch('core.editorial._write_batch',return_value=finished) as write:
            editorial._write_chapter_parts(context,[],[],excerpts,2,100,root/'part.json',
                                           short=False,source_indexes=[])
        self.assertEqual(write.call_args.args[0]['source_footage'],[])

    def test_catalog_contains_only_current_bounded_originals_and_separate_speech(self):
        assets=[{'page_url':'selected','kind':'video','duration':12,'source_report_id':'r'}]
        indices=[{'media':'selected','segments':[
            {'id':'actual','start':2.,'end':8.,'observations':['机械手在展台前抓取零件。'],
             'speech':[{'start':3.,'end':5.,'text':'自动转写可能有错字。'}]},
            {'id':'outside','start':9.,'end':15.,'observations':['越界。']}]},
            {'media':'not-selected','segments':[{'id':'other','start':0.,'end':3.,'observations':['其他素材。']}]}]
        catalog=scene_index.writing_catalog(assets,indices,'机械手')
        self.assertEqual([c['clip_id'] for c in catalog],['actual'])
        self.assertEqual(catalog[0]['source_id'],'r')
        self.assertEqual(catalog[0]['recognized_speech'],['自动转写可能有错字。'])
        self.assertNotIn('自动转写', ''.join(catalog[0]['observations']))
        self.assertEqual((catalog[0]['start'],catalog[0]['end']),(2.,8.))

    def test_planning_cache_changes_when_selected_footage_changes(self):
        reports=[{'id':str(i),'title':f'报道{i}','summary':'这篇原文记录了设备展示。','source':'原文','published':''}
                 for i in range(3)]
        source={'anchor':{'title':'设备展示'},'reports':reports}
        plan={'throughline':'设备展示有哪些变化？','chapters':[
            {'title':f'变化{i}','question':f'报道{i}说了什么？','source_ids':[str(i)]} for i in range(3)]}
        with scratch_dir() as root,patch('core.editorial.chat_json',return_value=plan) as model:
            editorial.plan_chapters(source,'bilibili',root/'plan.json',available_media=[{'clip_id':'old'}])
            editorial.plan_chapters(source,'bilibili',root/'plan.json',available_media=[{'clip_id':'new'}])
            self.assertEqual(model.call_count,2)
            self.assertEqual(model.call_args.args[1]['available_media'],[{'clip_id':'new'}])
            editorial.plan_chapters(source,'bilibili',root/'plan.json',available_media=[{'clip_id':'new'}])
            self.assertEqual(model.call_count,2)

    def test_writer_receives_approved_observed_footage_without_promoting_asr_to_facts(self):
        quote='原文记载，参观者在展台了解机械手的用途。'
        report={'id':'r','title':'设备展示','summary':quote*90}
        asset={'page_url':'approved','kind':'video','duration':12,'source_report_id':'r','title':'实拍',
               'brief_source':'source.video'}
        item={'editorial':{'reports':[report]},'editorial_plan':{'throughline':'设备展示'},
              'chapters':[{'story':report,'question':'机械手有什么用途？','reports':[report],
                           'source_excerpts':{'1':{'source_id':'r','quote':quote}}}],
              'media':[asset,{**asset,'page_url':'pending','title':'待审核素材'}],
              'media_review':{'approved':'approved','pending':'pending'},'target_seconds':720}
        observed=[{'media':key,'segments':[{'id':key,'start':0.,'end':8.,'observations':['展台机械手。'],
                   'speech':[{'start':0.,'end':3.,'text':'转写声称月球有酒店。'}]}]} for key in ('approved','pending')]
        finished={'scenes':[{'title':title,'text':title+'。','media':'','zoom':False}
                            for title in ('机械手的用途','展台的参观者')],'source_ids':['r']}
        with scratch_dir() as root,patch('core.editorial._write_batch',return_value=finished) as write:
            editorial.write_batches(item,'bilibili',root,lambda _:[],lambda *_:None,
                                     writing_only=True,source_indexes=observed)
        context=write.call_args.args[0]
        self.assertEqual({c['media'] for c in context['source_footage']},{'approved'})
        self.assertEqual({v['id'] for v in context['visuals']},{'approved'})
        self.assertEqual(context['excerpts'],{'1':{'source_id':'r','quote':quote}})
        self.assertNotIn('recognized_speech', context['source_footage'][0])
        with scratch_dir() as root,patch('core.editorial._write_batch',return_value=finished) as write:
            editorial.write_batches(item,'bilibili',root,lambda _:[],lambda *_:None,
                                     writing_only=True,source_indexes=[])
        self.assertEqual(write.call_args.args[0]['source_footage'],[])

