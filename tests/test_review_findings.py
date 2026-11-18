import unittest
from unittest.mock import patch

from core import editorial


class ReviewFindingTests(unittest.TestCase):
    def test_trimmed_news_and_pending_title_survive_a_model_outage(self):
        import json
        from core.local_ai import LocalAIUnavailable
        from test_daily import scratch_dir
        excerpts={'1':{'source_id':'r','quote':'建设前可以进行仿真。'},
                  '2':{'source_id':'r','quote':'现场提供操作演示。'}}
        draft={'scenes':[{'focus':'说清设计能力。','title':'确保运行顺利',
                         'text':'建设前可以做仿真。这就能确保以后顺利运行。','evidence_ids':['1']},
                        {'focus':'介绍演示。','title':'现场演示','text':'现场提供了操作演示。','evidence_ids':['2']}]}
        binding={'sentences':[{'index':i,'basis':'对应原文。','evidence_ids':ids}
                              for i,ids in enumerate((['1'],[],['2']))]}
        context={'retrieved_evidence':True,'writing_only':True,'trim_unmatched_tail':True}
        reports=[{'id':'r','summary':'\n'.join(row['quote'] for row in excerpts.values())}]
        with scratch_dir() as root:
            checkpoint=root/'working.json'
            with patch('core.editorial.chat_json',side_effect=[draft,binding,LocalAIUnavailable('offline')]), \
                    self.assertRaises(LocalAIUnavailable):
                editorial._write_batch(context,{},reports,[],excerpts,2,100,checkpoint=checkpoint)
            saved=json.loads(checkpoint.read_text(encoding='utf-8'))['draft']
            self.assertEqual(saved['scenes'][0]['text'],'建设前可以做仿真。')
            self.assertEqual(saved['_source_title_repairs'],[0])
            rebound={'sentences':[{'index':i,'basis':'对应。','evidence_ids':[str(i+1)]} for i in range(2)]}
            with patch('core.editorial.chat_json',side_effect=[rebound,
                    {'titles':[{'index':0,'title':'建设前的仿真'}]},{'issues':[]}]) as resumed:
                result=editorial._write_batch(context,{},reports,[],excerpts,2,100,checkpoint=checkpoint)
            self.assertIn('只重选事实引用',resumed.call_args_list[0].args[0])
            self.assertEqual(result['scenes'][0]['title'],'建设前的仿真')
            self.assertFalse(checkpoint.exists())

    def test_news_writer_removes_an_unsupported_trailing_conclusion_then_reviews_the_remaining_facts(self):
        excerpts={'1':{'source_id':'r','quote':'设备支持在建设之前进行设计和仿真。'},
                  '2':{'source_id':'r','quote':'现场演示了设备的操作流程。'}}
        draft={'scenes':[{'focus':'介绍仿真能力。','title':'确保未来运行顺畅',
                         'text':'建设之前，可以先做设计仿真。这能确保以后的运行绝对顺畅。','evidence_ids':['1']},
                         {'focus':'介绍操作演示。','title':'操作演示','text':'现场有一段设备操作演示。','evidence_ids':['2']}]}
        binding={'sentences':[{'index':0,'basis':'建设前的能力。','evidence_ids':['1']},
                              {'index':1,'basis':'原文没有效果保证。','evidence_ids':[]},
                              {'index':2,'basis':'原文记录演示。','evidence_ids':['2']}]}
        with patch('core.editorial.chat_json',side_effect=[draft,binding,
                {'titles':[{'index':0,'title':'建设前的设计仿真'}]},{'issues':[]}]) as model:
            result=editorial._write_batch({'retrieved_evidence':True,'writing_only':True,'trim_unmatched_tail':True},{},
                [{'id':'r','summary':'\n'.join(e['quote'] for e in excerpts.values())}],[],excerpts,2,100)
        self.assertEqual(result['scenes'][0]['text'],'建设之前，可以先做设计仿真。')
        self.assertEqual(result['scenes'][0]['title'],'建设前的设计仿真')
        self.assertIn('逐镜核对',model.call_args.args[0])

    def test_trim_mode_still_rejects_an_unsupported_opening_or_interior_sentence(self):
        excerpts={'1':{'source_id':'r','quote':'已知的事实。'}}
        for flags in ((False,True),(True,False,True)):
            scenes=[{'text':'已知的事实。'+'额外的一句。'*(len(flags)-1),'evidence_ids':['old']}]
            before=[dict(scene) for scene in scenes]
            response={'sentences':[{'index':i,'basis':'核对。','evidence_ids':['1'] if keep else []}
                                   for i,keep in enumerate(flags)]}
            with self.subTest(flags=flags),patch('core.editorial.chat_json',return_value=response), \
                    self.assertRaisesRegex(ValueError,'缺少本批原文支持'):
                editorial.rebind_news_evidence(scenes,excerpts,trim_unmatched_tail=True)
            self.assertEqual(scenes,before)

    def test_trim_mode_never_keeps_an_entirely_unsupported_scene_or_partially_mutates_other_scenes(self):
        scenes=[{'text':'一个有证据的陈述。','evidence_ids':['old']},
                {'text':'一个没有来源的判断。','evidence_ids':['old']}]
        before=[dict(scene) for scene in scenes]
        response={'sentences':[{'index':0,'basis':'有来源。','evidence_ids':['1']},
                               {'index':1,'basis':'没有来源。','evidence_ids':[]}]}
        with patch('core.editorial.chat_json',return_value=response),self.assertRaisesRegex(ValueError,'缺少本批原文支持'):
            editorial.rebind_news_evidence(scenes,{'1':{'source_id':'r','quote':'有证据的陈述。'}},trim_unmatched_tail=True)
        self.assertEqual(scenes,before)

    def test_confirmed_news_error_is_repaired_before_resuming_review(self):
        citation={'source_id':'r','quote':'当地道路预计明天恢复通行，目前施工仍在进行。'}
        draft={'scenes':[{'focus':'说明恢复时间。','title':'道路通行','text':'当地道路已经恢复通行。','evidence_ids':['1']},
                         {'focus':'说明施工状态。','title':'施工状态','text':'道路施工目前仍在进行。','evidence_ids':['1']}]}
        replies=iter([draft,{'issues':['把预计恢复写成已经恢复。']},
                      {'decisions':[{'issue_id':0,'needs_fix':True}]},{'issues':[]}])
        writings=[]
        def model(instruction,*_args,**_kwargs):
            if '写本章' in instruction:
                writings.append(instruction)
                self.assertEqual(len(writings),1,'A factual repair must not restart the whole writer')
            return next(replies)
        def repair(scenes,revisions):
            self.assertIn('把预计恢复写成已经恢复。',revisions[0]['review_findings'])
            scenes[0]['text']='当地道路预计明天恢复通行。'
        with patch('core.editorial.chat_json',side_effect=model),patch('core.editorial.rebind_news_evidence'), \
                patch('core.editorial._rewrite_shots',side_effect=repair) as rewrite:
            result=editorial._write_batch({'retrieved_evidence':True,'writing_only':True},{},
                [{'id':'r','summary':citation['quote']}],[],{'1':citation},2,60)
        self.assertEqual(rewrite.call_count,1)
        self.assertEqual(result['scenes'][0]['text'],'当地道路预计明天恢复通行。')
        self.assertEqual(result['scenes'][1]['text'],draft['scenes'][1]['text'])

    def test_equivalent_chinese_quantity_headings_do_not_trigger_a_rewrite(self):
        scenes=[{'title':'超480件展品首发','text':'超过四百八十件展品亮相。'},
                {'title':'6G终端原型机','text':'六代通信终端原型机在展台亮相。'},
                {'title':'8届展会','text':'双方合作已经走过八届展会。'}]
        with patch('core.editorial.chat_json',side_effect=AssertionError('Equivalent quantities were rejected')):
            self.assertFalse(editorial.align_news_titles(scenes))

    def test_completed_title_repair_survives_an_outage_at_factual_review(self):
        import json
        from core.local_ai import LocalAIUnavailable
        from test_daily import scratch_dir
        citation={'source_id':'r','quote':'共有20项权益。'}
        context={'retrieved_evidence':True,'writing_only':True}
        draft={'scenes':[{'focus':'介绍权益。','title':'47万人参展','text':'此次推出了20项权益。','evidence_ids':['1']},
                         {'focus':'说明记录。','title':'权益记录','text':'相关权益已经有原文记录。','evidence_ids':['1']}]}
        with scratch_dir() as root,patch('core.editorial.rebind_news_evidence'):
            checkpoint=root/'batch.json'
            with patch('core.editorial.chat_json',side_effect=[draft,{'titles':[{'index':0,'title':'20项权益'}]},
                    LocalAIUnavailable('offline')]),self.assertRaises(LocalAIUnavailable):
                editorial._write_batch(context,{},[{'id':'r','summary':citation['quote']}],
                    [],{'1':citation},2,60,checkpoint=checkpoint)
            self.assertEqual(json.loads(checkpoint.read_text(encoding='utf-8'))['draft']['scenes'][0]['title'],'20项权益')
            with patch('core.editorial.chat_json',return_value={'issues':[]}) as resumed:
                result=editorial._write_batch(context,{},[{'id':'r','summary':citation['quote']}],
                    [],{'1':citation},2,60,checkpoint=checkpoint)
            self.assertEqual(result['scenes'][0]['title'],'20项权益')
            self.assertEqual(resumed.call_count,1)

    def test_short_quoted_names_are_not_stitched_into_one_copied_sentence(self):
        names='“坚守多边主义”“科技新动能”“绿色和可持续的全球化”“可信赖的中国”'
        self.assertFalse(editorial._copied_run('讨论围绕'+names+'等方向，另行说明活动安排。',
            [{'summary':'论坛围绕'+names+'等方向展开。'}]))

    def test_copying_long_prose_is_detected_even_if_the_draft_adds_short_quotes(self):
        single='现场负责人介绍“智能装配系统”已经完成现场调试并将投入后续生产。'
        self.assertTrue(editorial._copied_run(single,[{'summary':single}]))
        prose='这段材料详细记录了现场的准备流程以及设备运转过程并说明各环节之间的衔接关系与实际操作条件。'
        for text in (prose,'这段材料详细记录了“现场的准备流程”以及设备运转过程并说明各环节之间的衔接关系与实际操作条件。'):
            self.assertTrue(editorial._copied_run(text,[{'summary':prose}]))
        self.assertTrue(editorial._copied_run('“'+prose+'”',[{'summary':'工作人员介绍：“'+prose+'”'}]))

    def test_news_headline_cannot_retain_a_number_removed_from_its_narration(self):
        citation={'source_id':'r','quote':'共有20项权益。'}
        draft={'scenes':[{'focus':'介绍权益数量。','title':'47万人参展','text':'此次推出了20项权益。','evidence_ids':['1']},
                         {'focus':'说明已有记录。','title':'权益记录','text':'相关权益已经有原文记录。','evidence_ids':['1']}]}
        with patch('core.editorial.chat_json',return_value=draft), \
                patch('core.editorial.rebind_news_evidence'), \
                self.assertRaisesRegex(RuntimeError,'标题.*口播'):
            editorial._write_batch({'retrieved_evidence':True},{},[{'id':'r','summary':citation['quote']}],
                                   [],{'1':citation},2,60)

    def test_retrieved_news_batch_requires_a_grounded_explanation_plan(self):
        citation={'source_id':'r','quote':'工业设备在展会上亮相。'}
        draft={'scenes':[{'title':'展会','text':'展会上展示了一台工业设备。','evidence_ids':['1']},
                         {'title':'现场','text':'参观者可以看到这台设备。','evidence_ids':['1']}]}
        with patch('core.editorial.chat_json',return_value=draft), \
                self.assertRaisesRegex(RuntimeError,'讲述角度'):
            editorial._write_batch({'retrieved_evidence':True},{},[{'id':'r','summary':citation['quote']}],
                                   [],{'1':citation},2,60)

    def test_rewrite_keeps_its_plan_private_and_preserves_the_selected_source(self):
        citation={'source_id':'original','quote':'设备可以完成多种工序。'}
        scene={'title':'设备','text':'原来的口播。','evidence':[citation]}
        reply={'scenes':[{'index':0,'focus':'改从设备能力切入，说明能完成哪些工序。',
                          'text':'多种加工工序，可以交给这台设备完成。'}]}
        with patch('core.editorial.chat_json',return_value=reply):
            editorial._rewrite_shots([scene],[{'index':0,'evidence':[citation]}])
        self.assertEqual(scene,{'title':'设备','text':reply['scenes'][0]['text'],'evidence':[citation]})

    def test_rewrite_requires_the_model_to_plan_a_new_explanation_before_narrating(self):
        scene={'text':'原来的口播。','evidence':[]}
        with patch('core.editorial.chat_json',return_value={'scenes':[{'index':0,'text':'仍在重复原句。'}]}), \
                self.assertRaisesRegex(ValueError,'改写'):
            editorial._rewrite_shots([scene],[{'index':0,'evidence':[]}])
        self.assertEqual(scene['text'],'原来的口播。')

    def test_each_sentence_keeps_its_own_supporting_passage_when_combined_into_one_shot(self):
        excerpts={'screen':{'source_id':'r','quote':'屏幕上，仓库逐渐成形。'},
                  'design':{'source_id':'r','quote':'可以在建设之前完成设计和仿真验证。'}}
        scenes=[{'text':'屏幕上的仓库渐渐成形。建设之前，可以完成设计和仿真验证。','evidence_ids':['screen']}]
        result={'sentences':[{'index':0,'evidence_ids':['screen'],'basis':'屏幕所见'},
                             {'index':1,'evidence_ids':['design'],'basis':'建设前能力'}]}
        with patch('core.editorial.chat_json',return_value=result) as rank:
            editorial.rebind_news_evidence(scenes,excerpts)
        self.assertEqual(scenes[0]['evidence_ids'],['screen','design'])
        self.assertEqual(len(rank.call_args.args[1]['sentences']),2)

    def test_sentence_binding_cannot_accept_an_unknown_or_missing_passage(self):
        scenes=[{'text':'这项技术已经正式投产。','evidence_ids':['1']}]
        excerpts={'1':{'source_id':'r','quote':'这项技术仍在研发。'}}
        for ids in (['unselected'],[]):
            with self.subTest(ids=ids),patch('core.editorial.chat_json',return_value={
                    'sentences':[{'index':0,'evidence_ids':ids,'basis':'缺少投产证据'}]}),self.assertRaises(ValueError):
                editorial.rebind_news_evidence(scenes,excerpts)

    def test_completed_number_repair_is_not_repeated_after_review_outage(self):
        from core.local_ai import LocalAIUnavailable
        from test_daily import scratch_dir
        report={'id':'r','summary':'工地总长50米，现场施工已经完成。'}
        excerpt={'1':{'source_id':'r','quote':report['summary']}}
        draft={'scenes':[{'title':'工程','text':'这项工程的总长度为80米。','evidence_ids':['1'],'media':'','motion':'cut'},
                         {'title':'现场','text':'现场的施工现在已经完成。','evidence_ids':['1'],'media':'','motion':'cut'}]}
        def repair(scenes,_revisions):
            scenes[0]['text']='这项工程的总长度为50米。'
        with scratch_dir() as root:
            checkpoint=root/'current-batch.json'
            with patch('core.editorial.chat_json',side_effect=[draft,LocalAIUnavailable('offline')]), \
                    patch('core.editorial._rewrite_shots',side_effect=repair) as rewrite,self.assertRaises(LocalAIUnavailable):
                editorial._write_batch({}, {},[report],[],excerpt,2,50,checkpoint=checkpoint)
            self.assertEqual(rewrite.call_count,1)
            with patch('core.editorial.chat_json',return_value={'issues':[]}), \
                    patch('core.editorial._rewrite_shots',side_effect=AssertionError('Completed numeric repair was lost')):
                saved=editorial._write_batch({}, {},[report],[],excerpt,2,50,checkpoint=checkpoint)
            self.assertEqual(saved['scenes'][0]['text'],'这项工程的总长度为50米。')

    def test_failed_batch_checkpoint_is_repaired_after_restart_and_removed_when_passed(self):
        from test_daily import scratch_dir
        report={'id':'r','summary':'当地道路预计明天恢复通行，目前施工仍在进行。'}
        excerpt={'1':{'source_id':'r','quote':report['summary']}}
        wrong={'scenes':[{'title':'道路','text':'当地道路已经恢复通行。','evidence_ids':['1'],'media':'','motion':'cut'},
                         {'title':'施工','text':'道路施工目前仍在进行。','evidence_ids':['1'],'media':'','motion':'cut'}]}
        fixed={'scenes':[{**wrong['scenes'][0],'text':'当地道路预计明天恢复通行。'},dict(wrong['scenes'][1])]}
        replies=[wrong,{'issues':['把预计恢复写成已恢复。']},{'decisions':[{'issue_id':0,'needs_fix':True}]}]*3
        with scratch_dir() as root:
            checkpoint=root/'current-batch.json'
            with patch('core.editorial.chat_json',side_effect=replies),self.assertRaisesRegex(RuntimeError,'预计恢复'):
                editorial._write_batch({}, {},[report],[],excerpt,2,50,checkpoint=checkpoint)
            self.assertTrue(checkpoint.is_file())
            with patch('core.editorial.chat_json',side_effect=[fixed,{'issues':[]}]) as writer:
                editorial._write_batch({}, {},[report],[],excerpt,2,50,checkpoint=checkpoint)
            self.assertEqual(writer.call_args_list[0].args[1]['previous_draft']['scenes'][0]['text'],'当地道路已经恢复通行。')
            self.assertFalse(checkpoint.exists())

    def test_generated_batch_survives_model_outage_during_review(self):
        from core.local_ai import LocalAIUnavailable
        from test_daily import scratch_dir
        report={'id':'r','summary':'当地道路已经恢复通行，目前路面状态正常。'}
        excerpt={'1':{'source_id':'r','quote':report['summary']}}
        draft={'scenes':[{'title':'道路','text':'当地道路已经可以正常通行。','evidence_ids':['1'],'media':'','motion':'cut'},
                         {'title':'路面','text':'目前道路的路面状态正常。','evidence_ids':['1'],'media':'','motion':'cut'}]}
        wrong={'scenes':[{**draft['scenes'][0],'text':'这条道路目前仍未恢复通行。'},dict(draft['scenes'][1])]}
        with scratch_dir() as root:
            checkpoint=root/'current-batch.json'
            replies=[wrong,{'issues':['原文已经通行，口播说仍未恢复。']},
                     {'decisions':[{'issue_id':0,'needs_fix':True}]},draft,LocalAIUnavailable('offline')]
            with patch('core.editorial.chat_json',side_effect=replies),self.assertRaises(LocalAIUnavailable):
                editorial._write_batch({}, {},[report],[],excerpt,2,50,checkpoint=checkpoint)
            with patch('core.editorial.chat_json',return_value={'issues':[]}) as review:
                saved=editorial._write_batch({}, {},[report],[],excerpt,2,50,checkpoint=checkpoint)
            self.assertEqual(saved['scenes'][0]['text'],draft['scenes'][0]['text'])
            self.assertEqual(review.call_count,1,'A generated batch must resume at review, not start writing again')
            self.assertFalse(checkpoint.exists())

    def test_rejected_news_batch_is_repaired_from_its_actual_prior_draft(self):
        report={'id':'r','summary':'当地道路预计明天恢复通行，目前施工仍在进行。'}
        excerpt={'1':{'source_id':'r','quote':report['summary']}}
        wrong={'scenes':[{'title':'道路','text':'当地道路已经恢复通行。','evidence_ids':['1'],'media':'','motion':'cut'},
                         {'title':'施工','text':'道路施工目前仍在进行。','evidence_ids':['1'],'media':'','motion':'cut'}]}
        fixed={'scenes':[{**wrong['scenes'][0],'text':'当地道路预计明天恢复通行。'},dict(wrong['scenes'][1])]}
        replies=[wrong,{'issues':['把预计恢复写成已恢复。']},
                 {'decisions':[{'issue_id':0,'needs_fix':True}]},fixed,{'issues':[]}]
        with patch('core.editorial.chat_json',side_effect=replies) as writer:
            cut=editorial._write_batch({}, {},[report],[],excerpt,2,50)
        retry=writer.call_args_list[3].args[1]
        self.assertEqual(retry['previous_draft']['scenes'][0]['text'],'当地道路已经恢复通行。')
        self.assertEqual(retry['previous_draft']['scenes'][1]['text'],cut['scenes'][1]['text'])
        self.assertEqual(cut['scenes'][0]['text'],'当地道路预计明天恢复通行。')

    def setUp(self):
        self.excerpts = {'1': {'source_id': 'r', 'quote': '道路预计明天恢复通行。'}}
        self.scenes = [{'text': '道路计划明天重新通行。', 'evidence': [self.excerpts['1']]}]

    def test_supported_paraphrase_commentary_is_not_a_failed_factual_review(self):
        issue = '只是用词差异，核心语义一致，没有事实错配。'
        with patch('core.editorial.chat_json', return_value={
                'decisions': [{'issue_id': 0, 'needs_fix': False}]}):
            self.assertEqual(editorial.confirm_review_findings([issue], self.scenes, self.excerpts, {}), [])

    def test_actual_plan_result_mismatch_stays_a_blocking_finding(self):
        issue = '原文说预计明天恢复，口播却说已经恢复。'
        scenes = [{'text': '道路已经恢复通行。', 'evidence': [self.excerpts['1']]}]
        with patch('core.editorial.chat_json', return_value={
                'decisions': [{'issue_id': 0, 'needs_fix': True}]}):
            self.assertEqual(editorial.confirm_review_findings([issue], scenes, self.excerpts, {}), [issue])

    def test_missing_duplicate_or_unrecognized_decisions_cannot_clear_findings(self):
        for decisions in ([], [{'issue_id': 1, 'needs_fix': False}],
                          [{'issue_id': 0, 'needs_fix': False}, {'issue_id': 0, 'needs_fix': False}],
                          [{'issue_id': 0, 'needs_fix': 'false'}]):
            with self.subTest(decisions=decisions), patch('core.editorial.chat_json', return_value={'decisions': decisions}):
                with self.assertRaises(ValueError):
                    editorial.confirm_review_findings(['需要复核'], self.scenes, self.excerpts, {})

    def test_batch_accepts_a_checked_paraphrase_without_rewriting_correct_narration(self):
        report = {'id': 'r', 'summary': '当地观测显示道路已经恢复通行。'}
        excerpts = {'1': {'source_id': 'r', 'quote': report['summary']}}
        scenes = [{'title': '道路', 'text': '当地这条路已经能正常通行了。', 'evidence_ids': ['1'], 'media': '', 'motion': 'cut'},
                  {'title': '消息', 'text': '这次观测记录的是道路恢复状态。', 'evidence_ids': ['1'], 'media': '', 'motion': 'cut'}]
        with patch('core.editorial.chat_json', side_effect=[{'scenes': scenes},
                {'issues': ['两句话与引用原文语义一致，没有错配。']},
                {'decisions': [{'issue_id': 0, 'needs_fix': False}]}]):
            result = editorial._write_batch({}, {}, [report], [], excerpts, 2, 50)
        self.assertEqual(result['scenes'][0]['text'], '当地这条路已经能正常通行了。')
