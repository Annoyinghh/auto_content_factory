import copy
import unittest
from unittest.mock import patch

from core import copywriting, editorial
from test_daily import scratch_dir


class NaturalNewsWritingTests(unittest.TestCase):
    def test_short_news_assigns_disjoint_source_batches_instead_of_repeating_facts(self):
        excerpts = {str(i): {'source_id':'r', 'quote': f'事实{i}。' + '真实进展。' * 48} for i in range(3)}
        context = {'position':'片头','writing_only':True,'retrieved_evidence':True,'visuals':[]}
        received = []
        def write(part, _schema, _reports, _visuals, selected, *_args, **_kwargs):
            received.append(set(selected))
            return {'source_ids':['r'],'scenes':[{'text':'独立事实。'}]}
        with scratch_dir() as root, patch('core.editorial._write_batch', side_effect=write):
            editorial._write_chapter_parts(context,[],[],excerpts,6,480,root/'short.json',short=True)
        self.assertEqual(received, [{'0'},{'1'},{'2'}])

    def test_news_excludes_reference_footer_and_advertising_notice(self):
        fact = '谷歌披露了测试数据，芯片集群的扩展效率达到96%。'
        report = {'id': 'r', 'title': '芯片测试', 'summary': fact + '\n参考\n'
                  'New AI training performance benchmarks for Google TPUs\n'
                  '广告声明：文内含有的对外跳转链接用于传递更多信息。'}
        self.assertEqual(editorial._weekly_source_passages(report), [{'source_id': 'r', 'quote': fact}])
        self.assertTrue(editorial._source_metadata('广告声明：文内含有对外跳转链接。', report['title']))

    def test_exact_model_identifier_is_not_a_copied_prose_span(self):
        phrase = '测试中，640颗Ironwood芯片运行Qwen3.5-397B'
        self.assertFalse(editorial._copied_run(phrase + '，任务已完成。', [{'summary': phrase + '，用时18.02分钟。'}]))
        prose = '记者走进企业生产车间，看到工人按照现场制定的流程逐项检查设备状态，并记录每道工序的运行情况。'
        self.assertTrue(editorial._copied_run(phrase + prose, [{'summary': phrase + prose}]))

    def test_amount_before_change_does_not_hide_statistical_scope(self):
        quote = '全国固定资产投资（不含农户）364824亿元，同比下降8.1%。'
        evidence = [{'source_id': 'r', 'quote': quote}]
        for text in (quote, '全国固定资产投资（不含农户）同比下降8.1%。'):
            editorial.check_evidence([{'text': text, 'evidence': evidence}], [{'id': 'r', 'summary': quote}])
        for text in ('全国固定资产投资364824亿元，同比下降8.1%。',
                     '全国固定资产投资同比下降8.1%。'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, '统计口径'):
                editorial.check_evidence([{'text': text, 'evidence': evidence}], [{'id': 'r', 'summary': quote}])

    def test_weekly_passages_remove_dateline_without_changing_facts(self):
        fact = '统计局公布了经济运行数据，工业生产继续增长。'
        report = {'id': 'r', 'title': '经济运行', 'summary': '新华社北京11月16日电（记者李明）' + fact}
        rows = editorial._weekly_source_passages(report)
        self.assertEqual(rows, [{'source_id': 'r', 'quote': fact}])
        self.assertIn(rows[0]['quote'], report['summary'])

    def test_writing_only_does_not_receive_photo_captions_or_speech_as_facts(self):
        quote = '统计局公布了经济运行数据，工业生产继续增长。'
        context = {'position': '片头', 'writing_only': True, 'production_mode': 'weekly',
                   'retrieved_evidence': True, 'visuals': [{'id': 'photo', 'kind': 'image', 'title': '出席名单'}]}
        assets = [{'page_url': 'photo', 'kind': 'image', 'verified_report_id': 'r'}]
        catalog = [{'media': 'photo', 'publisher_caption': '会议名单',
                    'observations': ['室内发布会'], 'recognized_speech': '语音里的其他事实'}]
        def write(part, *_args, **_kwargs):
            self.assertNotIn('名单', str(part))
            self.assertNotIn('语音里的其他事实', str(part))
            self.assertIn('室内发布会', str(part))
            return {'source_ids': ['r'], 'scenes': [{'text': '工业生产继续增长。'}]}
        with scratch_dir() as root, patch('core.editorial._write_batch', side_effect=write), \
                patch('core.scene_index.writing_catalog', return_value=catalog):
            editorial._write_chapter_parts(context, [], assets, {'1': {'source_id': 'r', 'quote': quote}},
                                          3, 100, root/'chapter.json', short=False, source_indexes=[])
        self.assertIn('publisher_caption', catalog[0])

    def test_statistic_binding_ignores_punctuation_around_an_explicit_source_period(self):
        quote = '10月份，全国规模以上工业增加值同比增长4.8%。'
        source = {'id': 'r', 'summary': quote}
        evidence = [{'source_id': 'r', 'quote': quote}]
        for text in ('10月份全国规模以上工业增加值同比增长4.8%。',
                     '数据显示10月份全国规模以上工业增加值同比增长4.8%。'):
            with self.subTest(text=text):
                editorial.check_evidence([{'text': text, 'evidence': evidence}], [source])
        with self.assertRaisesRegex(ValueError, '统计口径'):
            editorial.check_evidence([{'text': '10月份全国工业增加值同比增长4.8%。', 'evidence': evidence}], [source])

    def test_statistical_value_stays_bound_to_its_named_measure(self):
        quote = '社会消费商品和服务零售总额同比增长2.5%，其中服务零售额增长4.9%。'
        source = {'id': 'r', 'summary': quote}
        evidence = [{'source_id': 'r', 'quote': quote}]
        editorial.check_evidence([{'text': quote, 'evidence': evidence}], [source])
        wrong = quote.replace('社会消费商品和服务零售总额', '社会消费品零售总额')
        with self.assertRaisesRegex(ValueError, '统计口径.*商品和服务'):
            editorial.check_evidence([{'text': wrong, 'evidence': evidence}], [source])

    def test_two_source_metrics_do_not_allow_swapping_their_percentages(self):
        quote = '社会消费商品和服务零售总额同比增长2.5%。社会消费品零售总额同比增长1.8%。'
        source = {'id': 'r', 'summary': quote}
        evidence = [{'source_id': 'r', 'quote': quote}]
        editorial.check_evidence([{'text': quote, 'evidence': evidence}], [source])
        with self.assertRaisesRegex(ValueError, '统计口径'):
            editorial.check_evidence([{'text': '社会消费品零售总额同比增长2.5%。', 'evidence': evidence}], [source])

    def test_statistic_binding_allows_connectives_but_not_changed_scope_or_comparison(self):
        quote = '全国规模以上工业增加值同比增长4.8%。其中服务零售额增长4.9%。'
        source = {'id': 'r', 'summary': quote}
        evidence = [{'source_id': 'r', 'quote': quote}]
        editorial.check_evidence([{'text': '服务零售额增长4.9%。但全国规模以上工业增加值同比增长4.8%。',
                                    'evidence': evidence}], [source])
        for wrong in ('全国工业增加值同比增长4.8%。', '全国规模以上工业增加值环比增长4.8%。'):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, '统计口径'):
                editorial.check_evidence([{'text': wrong, 'evidence': evidence}], [source])

    def test_exact_statistical_phrases_do_not_force_changes_to_measure_names(self):
        quote = ('10月份，全国规模以上工业增加值同比增长4.8%，环比增长0.21%；'
                 '全国服务业生产指数同比增长4.6%。'
                 '1至10月份，社会消费商品和服务零售总额同比增长2.5%，其中服务零售额增长4.9%。')
        self.assertFalse(editorial._copied_run(quote, [{'summary': quote}]))
        scene = {'text': quote.replace('4.8%', '4.7%'), 'evidence': [{'source_id': 'r', 'quote': quote}]}
        with self.assertRaisesRegex(ValueError, '数字'):
            editorial.check_evidence([scene], [{'id': 'r', 'summary': quote}])

    def test_statistical_phrase_exception_does_not_exempt_surrounding_copied_prose(self):
        prose = '记者走进企业生产车间，看到工人按照现场制定的流程逐项检查设备状态，并记录每道工序的运行情况。'
        statistic = '规模以上工业增加值同比增长4.8%。'
        self.assertTrue(editorial._copied_run(prose + statistic, [{'summary': prose + statistic}]))
        conclusion = '这意味着此前所有生产障碍都已得到解决，全国企业已经进入产销两旺的新阶段。'
        self.assertTrue(editorial._copied_run(statistic + conclusion, [{'summary': statistic + conclusion}]))

    def test_rejected_rewrite_explains_the_failed_contract_and_keeps_original(self):
        scenes = [{'text': '原来完整的口播。'}]
        reply = {'scenes': [{'index': 0, 'focus': '先说进度。', 'text': '工人正在检查'}]}
        with patch('core.editorial.chat_json', return_value=reply), \
                self.assertRaisesRegex(ValueError, '第1镜.*完整句.*工人正在检查'):
            editorial._rewrite_shots(scenes, [{'index': 0, 'evidence': []}])
        self.assertEqual(scenes[0]['text'], '原来完整的口播。')

    def test_one_substantive_fact_can_complete_a_single_shot_batch(self):
        quote = '原文记录了机械手仍在安装，调试安排在安装完成以后。'
        excerpts = {'1': {'source_id': 'r', 'quote': quote}}
        draft = {'scenes': [{'focus': '说明进度。', 'title': '安装进度',
                            'text': '这台机械手正在安装。之后才会调试。', 'evidence_ids': ['1']}]}
        with patch('core.editorial.chat_json', side_effect=[draft, {'issues': []}]), \
                patch('core.editorial.rebind_news_evidence'):
            result = editorial._write_batch({'retrieved_evidence': True, 'writing_only': True}, {},
                                            [{'id': 'r', 'summary': quote}], [], excerpts, 1, 30)
        self.assertEqual(len(result['scenes']), 1)
        self.assertEqual(result['source_ids'], ['r'])

    def test_weekly_part_cannot_read_another_source_through_media_captions(self):
        excerpts = {'1': {'source_id': 'macro', 'quote': '工业增加值同比增长4.8%，服务业生产指数同比增长4.6%。' * 8},
                    '2': {'source_id': 'demand', 'quote': '居民发展型和品质型消费意识提升，智能绿色商品消费持续扩大。' * 8}}
        assets = [{'page_url': source, 'kind': 'image', 'verified_report_id': source}
                  for source in ('macro', 'demand')]
        context = {'position': '片头', 'writing_only': True, 'production_mode': 'weekly',
                   'retrieved_evidence': True, 'visuals': [
                       {'id': 'macro', 'title': '统计数据发布会'},
                       {'id': 'demand', 'title': '居民发展型和品质型消费意识提升'}]}
        groups = []
        def write(part, _schema, _reports, _visuals, selected, *_args, **_kwargs):
            if '1' in selected:
                self.assertNotIn('品质型', str(part))
                self.assertEqual([a['id'] for a in part['visuals']], ['macro'])
            groups.append(selected)
            return {'source_ids': ['macro'], 'scenes': [{'text': '统计数据已经发布。'}]}
        with scratch_dir() as root, patch('core.editorial._write_batch', side_effect=write):
            editorial._write_chapter_parts(context, [], assets, excerpts, 6, 500, root/'chapter.json', short=False)
        self.assertEqual(len(groups), 2)

    def test_weekly_retrieval_preserves_groups_and_complete_body_paragraphs_without_model_selection(self):
        industry = '工业生产较快增长，服务业平稳增长。10月份，规模以上工业增加值同比增长4.8%。服务业生产指数同比增长4.6%。'
        caption = '11月16日，国务院新闻办公室举行发布会，发言人介绍经济运行情况并答记者问。新华社记者 李鑫 摄'
        reports = [{'id': 'macro', 'title': '经济数据-新华网', 'summary': caption + '\n' + industry},
                   {'id': 'chip', 'title': '芯片投资', 'summary': '公司计划投资芯片生产项目，项目仍处于准备阶段。'},
                   {'id': 'demand', 'title': '市场需求', 'summary': '设备工器具购置投资同比增长8.9%。'}]
        chapters = [{'story': reports[0], 'question': '本组报道公布了什么？', 'reports': [reports[0], reports[2]]},
                    {'story': reports[1], 'question': '本组报道公布了什么？', 'reports': [reports[1]]}]
        with scratch_dir() as root, patch('core.editorial.scope_excerpts',
                side_effect=AssertionError('Weekly grouping must not be reassigned by a retrieval model')):
            result = editorial.retrieve_chapter_evidence({'reports': reports, 'production_mode': 'weekly'}, chapters, root)
        self.assertEqual([[r['id'] for r in c['reports']] for c in result], [['macro', 'demand'], ['chip']])
        first = list(result[0]['source_excerpts'].values())
        self.assertIn(industry, [r['quote'] for r in first])
        self.assertEqual(first[0]['role'], 'context')
        self.assertTrue(all(r['source_id'] != 'chip' for r in first))
        self.assertIn('仍处于准备阶段', str(result[1]['source_excerpts']))

    def test_weekly_batches_follow_substance_not_caption_length_or_requested_scene_count(self):
        quotes = {'caption': {'source_id': 'r', 'quote': '发布会出席人员名单。' * 20, 'role': 'context'},
                  'body': {'source_id': 'r', 'quote': '工业生产较快增长。规模以上工业增加值同比增长4.8%，服务业生产指数同比增长4.6%。'}}
        batches = []
        def write(context, schema, _reports, _visuals, excerpts, count, target, **_kwargs):
            batches.append((context, excerpts, count, target))
            return {'source_ids': ['r'], 'scenes': [{'text': '工业增加值同比增长4.8%。'}]}
        with scratch_dir() as root, patch('core.editorial._write_batch', side_effect=write):
            editorial._write_chapter_parts({'position': '片头', 'writing_only': True,
                'production_mode': 'weekly', 'retrieved_evidence': True}, [], [], quotes, 12, 1000,
                root/'chapter.json', short=False)
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0][2], 1)
        self.assertLessEqual(batches[0][3], round(len(quotes['body']['quote']) * 1.2))
        self.assertIn('body', batches[0][1])
        self.assertNotIn('caption', batches[0][1])

    def test_weekly_passage_batches_preserve_each_fact_and_keep_heading_with_its_details(self):
        paragraphs = ['工业生产较快增长', '10月份，工业增加值同比增长4.8%，环比增长0.21%。']
        paragraphs += [f'第{i}项调查记录了企业生产、安装和调试的具体安排，工作人员按既定次序完成现场检查。' for i in range(12)]
        report = {'id': 'r', 'title': '经济运行', 'summary': '\n'.join(paragraphs)}
        chapter = {'story': report, 'question': '本组报道公布了什么？', 'reports': [report]}
        seen = []
        def write(_context, _schema, _reports, _visuals, excerpts, count, target, **_kwargs):
            self.assertGreaterEqual(count, 1)
            self.assertLessEqual(count, 3)
            self.assertLessEqual(target, round(sum(len(r['quote']) for r in excerpts.values()) * 1.2))
            seen.extend(excerpts)
            return {'source_ids': ['r'], 'scenes': [{'text': '本批现场安排已经交代。'}]}
        with scratch_dir() as root, patch('core.editorial.scope_excerpts', side_effect=AssertionError):
            result = editorial.retrieve_chapter_evidence({'reports': [report], 'production_mode': 'weekly'}, [chapter], root)
            excerpts = result[0]['source_excerpts']
            self.assertTrue(any(paragraphs[0] in r['quote'] and '4.8%' in r['quote'] for r in excerpts.values()))
            with patch('core.editorial._write_batch', side_effect=write):
                editorial._write_chapter_parts({'position': '中段', 'writing_only': True,
                    'production_mode': 'weekly', 'retrieved_evidence': True}, [report], [], excerpts, 20, 1800,
                    root/'chapter.json', short=False)
        self.assertEqual(seen, list(excerpts))

    def test_evidence_overflow_splits_only_at_sentence_boundaries_without_losing_claims(self):
        sentences = ['工厂开始安装设备。', '工程师随后调整接口。', '现场另有操作培训。', '调试安排在安装之后。']
        excerpts = {str(i): {'source_id': 'r', 'quote': text} for i, text in enumerate(sentences)}
        scenes = [{'title': '工厂安排', 'text': ''.join(sentences), 'media': 'original', 'focus': '交代安排。'}]
        response = {'sentences': [{'index': i, 'evidence_ids': [str(i)], 'basis': '对应原文。'}
                                  for i in range(len(sentences))]}
        with patch('core.editorial.chat_json', return_value=response):
            origins = editorial.rebind_news_evidence(scenes, excerpts)
        self.assertEqual(origins, [0, 0])
        self.assertEqual([s['evidence_ids'] for s in scenes], [['0', '1', '2'], ['3']])
        self.assertEqual(''.join(s['text'] for s in scenes), ''.join(sentences))
        self.assertTrue(all(s['media'] == 'original' and s['focus'] == '交代安排。' for s in scenes))

    def test_evidence_splitting_remains_atomic_when_a_later_scene_is_unsupported(self):
        excerpts = {str(i): {'source_id': 'r', 'quote': '原文记录了现场的安装安排。'} for i in range(4)}
        scenes = [{'text': '甲方抵达。乙方安装。丙方培训。丁方调试。'}, {'text': '所有问题从此消失。'}]
        before = copy.deepcopy(scenes)
        response = {'sentences': [{'index': i, 'evidence_ids': [str(i)] if i < 4 else [], 'basis': '逐句核对。'}
                                  for i in range(5)]}
        with patch('core.editorial.chat_json', return_value=response), self.assertRaisesRegex(ValueError, '缺少本批原文支持'):
            editorial.rebind_news_evidence(scenes, excerpts, trim_unmatched_tail=True)
        self.assertEqual(scenes, before)

    def test_split_batch_resumes_after_review_outage_and_keeps_graphic_on_original_scene(self):
        import json
        from core.local_ai import LocalAIUnavailable

        sentences = ['工厂开始安装设备。', '工程师随后调整接口。', '现场另有操作培训。',
                     '调试安排在安装之后。', '厂房旁边留有通道。']
        excerpts = {str(i): {'source_id': 'r', 'quote': text} for i, text in enumerate(sentences)}
        reports = [{'id': 'r', 'summary': '\n'.join(sentences)}]
        draft = {'scenes': [
            {'title': '安排', 'text': ''.join(sentences[:4]), 'focus': '交代安装安排。',
             'evidence_ids': ['0', '1', '2'], 'media': '', 'motion': 'cut'},
            {'title': '通道', 'text': sentences[4], 'focus': '说明现场通道。',
             'evidence_ids': ['4'], 'media': '', 'motion': 'cut'}],
            'graphic': {'index': 1, 'style': 'scan'}}
        binding = {'sentences': [{'index': i, 'evidence_ids': [str(i)], 'basis': '对应原文。'} for i in range(5)]}
        titles = {'titles': [{'index': 0, 'title': '安装与培训'}, {'index': 1, 'title': '后续调试'}]}
        context = {'retrieved_evidence': True}
        schema = {'properties': {'graphic': {}}}
        with scratch_dir() as root:
            checkpoint = root / 'working.json'
            with patch('core.editorial.chat_json', side_effect=[draft, binding, titles, LocalAIUnavailable('offline')]), \
                    self.assertRaises(LocalAIUnavailable):
                editorial._write_batch(context, schema, reports, [], excerpts, 2, 100, checkpoint=checkpoint)
            pending = json.loads(checkpoint.read_text(encoding='utf-8'))['draft']
            self.assertEqual(pending['_source_scene_origins'], [0, 0, 1])
            with patch('core.editorial.chat_json', side_effect=[binding, {'issues': []}]) as resumed:
                result = editorial._write_batch(context, schema, reports, [], excerpts, 2, 100, checkpoint=checkpoint)
            self.assertEqual(resumed.call_count, 2)
            self.assertFalse(checkpoint.exists())
        self.assertEqual(''.join(s['text'] for s in result['scenes']), ''.join(sentences))
        self.assertEqual([s['motion'] for s in result['scenes']], ['cut', 'cut', 'scan'])

    def test_instruction_echo_and_empty_wrapup_require_rewriting_without_deleting_facts(self):
        evidence = [{'source_id': 'r', 'quote': '工厂正在安装机械手，完成安装后将进入调试阶段。'}]
        for line in ('来源支持事实。', '我们只讲有原文支持的事实。',
                     '不要输出多余的解释。', '这一变化值得持续关注。'):
            scene = {'text': '机械手目前还在安装，之后才会调试。' + line, 'evidence': evidence}
            before = copy.deepcopy(scene)
            with self.subTest(line=line):
                revisions = editorial._shots_to_rewrite([scene], [{'summary': evidence[0]['quote']}])
                self.assertEqual(len(revisions), 1)
                self.assertTrue(revisions[0]['narration_issues'])
                self.assertEqual(scene, before)

    def test_real_reporting_about_evidence_is_not_a_prompt_echo(self):
        quote = '研究者介绍，现有证据支持该解释，但还需要进一步观察。'
        scene = {'text': '据研究者介绍，目前的证据支持这一解释，还需要后续观察。',
                 'evidence': [{'source_id': 'r', 'quote': quote}]}
        self.assertEqual(editorial._shots_to_rewrite([scene], [{'summary': quote}]), [])

    def test_rewrites_isolate_each_shots_evidence_and_commit_only_after_all_succeed(self):
        scenes = [{'text': '通信终端原型机。'}, {'text': '机械手正在安装。'}]
        revisions = [{'index': i, 'text': scene['text'], 'evidence': [
            {'source_id': str(i), 'quote': scene['text']}]} for i, scene in enumerate(scenes)]
        before = copy.deepcopy(scenes)
        received = []

        def rewrite(_instruction, context, _schema):
            self.assertEqual(len(context['scenes']), 1)
            row = context['scenes'][0]
            received.append(row['evidence'][0]['source_id'])
            if row['index'] == 1:
                self.assertNotIn('原型机', str(context))
                raise RuntimeError('model disconnected')
            return {'scenes': [{'index': row['index'], 'focus': '从用途切入。', 'text': '这是通信终端的原型机。'}]}

        with patch('core.editorial.chat_json', side_effect=rewrite), self.assertRaises(RuntimeError):
            editorial._rewrite_shots(scenes, revisions)
        self.assertEqual(received, ['0', '1'])
        self.assertEqual(scenes, before)

    def test_echo_in_a_repair_is_rejected_without_overwriting_the_draft(self):
        scenes = [{'text': '机械手正在安装。'}]
        reply = {'scenes': [{'index': 0, 'focus': '说明进展。', 'text': '来源支持事实。'}]}
        with patch('core.editorial.chat_json', return_value=reply), self.assertRaisesRegex(ValueError, '口播'):
            editorial._rewrite_shots(scenes, [{'index': 0, 'evidence': []}])
        self.assertEqual(scenes[0]['text'], '机械手正在安装。')

    def test_news_prompt_is_short_and_does_not_include_recipe_instructions(self):
        excerpts = {'1': {'source_id': 'r', 'quote': '机械手目前处于安装阶段，之后才会开始调试。'}}
        draft = {'scenes': [
            {'focus': '说明当前阶段。', 'title': '安装', 'text': '目前正在安装机械手。', 'evidence_ids': ['1']},
            {'focus': '说明后续安排。', 'title': '调试', 'text': '调试要等安装完成之后才开始。', 'evidence_ids': ['1']}]}
        with patch('core.editorial.chat_json', side_effect=[draft, {'issues': []}]) as model, \
                patch('core.editorial.rebind_news_evidence'):
            editorial._write_batch({'retrieved_evidence': True, 'writing_only': True,
                                    'production_mode': 'weekly', 'period': {'start': '2026-11-09', 'end': '2026-11-15'}},
                                   {}, [{'id': 'r', 'summary': excerpts['1']['quote']}], [], excerpts, 2, 60)
        instruction = model.call_args_list[0].args[0]
        self.assertLess(len(instruction), 1500)
        self.assertNotIn('下锅', instruction)
        self.assertNotIn('调味', instruction)
        self.assertIn('因果', instruction)
        self.assertEqual(model.call_args_list[0].args[1]['period']['start'], '2026-11-09')
        review = model.call_args_list[-1]
        self.assertNotIn('下锅', review.args[0])
        self.assertNotIn('调味', review.args[0])
        self.assertLess(len(review.args[0]), 700)
        self.assertEqual(review.args[1]['period']['start'], '2026-11-09')

    def test_prompt_echo_is_rewritten_before_evidence_binding_and_keeps_the_other_shot(self):
        excerpts = {'1': {'source_id': 'r', 'quote': '机械手目前处于安装阶段，之后才会开始调试。'}}
        draft = {'scenes': [
            {'focus': '说明当前阶段。', 'title': '安装',
             'text': '目前正在安装机械手。来源支持事实。', 'evidence_ids': ['1']},
            {'focus': '说明后续安排。', 'title': '调试',
             'text': '调试要等安装完成之后才开始。', 'evidence_ids': ['1']}]}
        repair = {'scenes': [{'index': 0, 'focus': '直接交代安装进度。', 'text': '机械手还在安装中。'}]}

        def bind(scenes, _excerpts, **_options):
            self.assertEqual(scenes[0]['text'], repair['scenes'][0]['text'])
            self.assertEqual(scenes[1]['text'], draft['scenes'][1]['text'])

        with patch('core.editorial.chat_json', side_effect=[draft, repair, {'issues': []}]) as model, \
                patch('core.editorial.rebind_news_evidence', side_effect=bind):
            result = editorial._write_batch({'retrieved_evidence': True, 'writing_only': True}, {},
                                            [{'id': 'r', 'summary': excerpts['1']['quote']}], [], excerpts, 2, 60)
        self.assertEqual([row['text'] for row in result['scenes']],
                         [repair['scenes'][0]['text'], draft['scenes'][1]['text']])
        self.assertEqual(len(model.call_args_list[1].args[1]['scenes']), 1)

    def test_weekly_writer_receives_dates_and_preserves_actual_reference_provenance(self):
        quote = '行业此前开展过试验，目前报道的是工厂的安装进度。'
        reports = [{'id': 'r', 'title': '工厂进度', 'summary': quote * 100,
                    'published': '2020-05-02', 'source_type': 'reference'}]
        reference = {'title': '真实周报作品', 'url': 'https://www.bilibili.com/video/BVexample',
                     'evidence': '用户提供的作品原链接及摘要', 'notes': '围绕问题串联背景和新进展。'}
        item = {'editorial': {'reports': reports, 'production_mode': 'weekly',
                             'period': {'start': '2026-11-09', 'end': '2026-11-15'}},
                'editorial_plan': {'throughline': '从试验到工厂发生了哪些变化？'},
                'chapters': [{'story': reports[0], 'reports': reports, 'question': '安装有何进展？',
                              'source_excerpts': {'1': {'source_id': 'r', 'quote': quote}}}],
                'target_seconds': 720}
        result = {'source_ids': ['r'], 'scenes': [
            {'title': '试验', 'text': '此前有过行业试验。', 'media': '', 'zoom': False},
            {'title': '安装', 'text': '现在报道的是工厂的安装进度。', 'media': '', 'zoom': False}]}
        with scratch_dir() as root, patch('core.editorial._write_chapter_parts', return_value=result) as write:
            cut = editorial.write_batches(item, 'bilibili', root, lambda _: [reference], lambda *_: None,
                                          writing_only=True)
        context = write.call_args.args[0]
        self.assertEqual(context['period'], item['editorial']['period'])
        self.assertEqual(context['throughline'], item['editorial_plan']['throughline'])
        self.assertEqual(context['reports'][0]['published'], '2020-05-02')
        self.assertEqual(context['reports'][0]['source_type'], 'reference')
        self.assertEqual(cut['chapter_starts'][0]['style_reference'], reference)

    def test_weekly_planning_keeps_period_and_source_dates_and_invalidates_prior_week(self):
        reports = [{'id': str(i), 'title': title, 'summary': title + '的详细报道。', 'source': '原文',
                    'published': date, 'source_type': kind} for i, (title, date, kind) in enumerate((
                        ('终端发布', '2026-11-12', 'article'), ('工厂扩建', '2026-11-13', 'article'),
                        ('行业历史', '2020-05-02', 'reference')))]
        source = {'anchor': {'title': '科技周报'}, 'reports': reports, 'production_mode': 'weekly',
                  'period': {'start': '2026-11-09', 'end': '2026-11-15'}}
        plan = {'throughline': '产业从研发到应用遇到哪些问题？', 'chapters': [
            {'title': r['title'], 'question': r['title'] + '有什么依据？', 'source_ids': [r['id']]} for r in reports]}
        with scratch_dir() as root, patch('core.editorial._plan_weekly', return_value=plan) as model:
            editorial.plan_chapters(source, 'bilibili', root / 'plan.json')
            first = model.call_args
            self.assertEqual(first.args[0]['period'], source['period'])
            self.assertEqual(first.args[0]['reports'][-1]['source_type'], 'reference')
            source['period'] = {'start': '2026-11-16', 'end': '2026-11-22'}
            editorial.plan_chapters(source, 'bilibili', root / 'plan.json')
        self.assertEqual(model.call_count, 2)

    def test_weekly_planning_uses_ai_only_for_grouping_then_frames_source_derived_topics(self):
        reports = [{'id': str(i), 'title': name, 'summary': ('前文。' * 400) + name + '的独有事实。',
                    'published': date, 'source_type': kind} for i, (name, date, kind) in enumerate((
                        ('车市', '2026-11-12', 'article'), ('芯片', '2026-11-13', 'article'),
                        ('历史背景', '2020-05-02', 'reference')))]
        source = {'anchor': {'title': '经济周报'}, 'reports': reports,
                  'production_mode': 'weekly', 'period': {'start': '2026-11-10', 'end': '2026-11-16'}}
        groups = {'groups': [{'source_ids': [r['id']]} for r in reports]}
        with patch('core.editorial.chat_json', return_value=groups) as model:
            plan = editorial._plan_weekly(source, {r['id']: r for r in reports}, 3, 6)
        self.assertEqual(len(plan['chapters']), 3)
        grouping_schema = model.call_args_list[0].args[2]['properties']['groups']['items']['properties']
        self.assertEqual(set(grouping_schema), {'source_ids'})
        self.assertEqual(model.call_count, 1)
        self.assertEqual([c['title'] for c in plan['chapters']], [r['title'] for r in reports])
        self.assertTrue(all(c['question'].endswith('？') and not editorial._numbers(c['question'])
                            for c in plan['chapters']))
        self.assertNotIn('下锅', model.call_args.args[0])

    def test_weekly_grouping_rejects_duplicate_source_assignment_before_drafting(self):
        reports = {str(i): {'id': str(i), 'title': str(i), 'summary': '这篇报道有完整原文。'} for i in range(3)}
        groups = {'groups': [{'source_ids': ['0']}, {'source_ids': ['1']}, {'source_ids': ['0', '2']}]}
        with patch('core.editorial.chat_json', return_value=groups) as model, \
                self.assertRaisesRegex(RuntimeError, '分组'):
            editorial._plan_weekly({'anchor': {'title': '周报'}}, reports, 3, 6)
        self.assertEqual(model.call_count, 1)

    def test_weekly_title_uses_the_primary_report_and_keeps_its_source_identity(self):
        reports = [{'id': 'a', 'title': '工业数据-新华网', 'summary': '工业统计数据已经发布。'},
                   {'id': 'b', 'title': '消费信息-新华网', 'summary': '消费市场统计已经公布。'},
                   {'id': 'c', 'title': '台积电：' + '公开投资计划及相关安排' * 5, 'summary': '公司公开了投资计划。'},
                   {'id': 'd', 'title': '国际贸易情况的官方报道与统计指标' * 4, 'summary': '贸易统计数据已经公布。'}]
        source = {'anchor': {'title': '经济周报'}, 'reports': reports, 'production_mode': 'weekly',
                  'brief': {'focus_id': 'b', 'notes': [], 'sequence': []}}
        groups = {'groups': [{'source_ids': ['a', 'b']}, {'source_ids': ['c']}, {'source_ids': ['d']}]}
        with scratch_dir() as root, patch('core.editorial.chat_json', return_value=groups) as model:
            chapters, plan = editorial.plan_chapters(source, 'bilibili', root / 'plan.json')
        self.assertEqual(model.call_count, 1)
        self.assertEqual(chapters[0]['story']['title'], '消费信息')
        self.assertEqual(chapters[0]['story']['id'], 'b')
        self.assertEqual([r['id'] for r in chapters[0]['reports']], ['b', 'a'])
        self.assertEqual(chapters[1]['story']['title'], '台积电')
        self.assertTrue(chapters[2]['story']['title'].endswith('…'))
        self.assertTrue(all(len(c['story']['title']) <= 36 for c in chapters))
        self.assertTrue(all('公布了什么？' in c['question'] for c in chapters))
        self.assertFalse(editorial._numbers(plan['throughline']))

    def test_weekly_plan_cache_from_before_premise_checks_is_not_reused(self):
        import json

        reports = [{'id': str(i), 'title': name, 'summary': name + '已有公开报道。', 'source': '原文',
                    'published': '2026-11-16'} for i, name in enumerate(('消费', '投资', '出口'))]
        source = {'anchor': {'title': '经济周报'}, 'reports': reports, 'production_mode': 'weekly'}
        plan = {'throughline': '本周发生了什么变化？', 'chapters': [
            {'title': r['title'], 'question': r['title'] + '有什么进展？', 'source_ids': [r['id']]} for r in reports]}
        old = {**plan, 'brief_key': editorial._hash(['plan-groups-v3', source, 'bilibili', 720, None])}
        with scratch_dir() as root, patch('core.editorial._plan_weekly', return_value=plan) as model:
            path = root / 'plan.json'
            path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
            editorial.plan_chapters(source, 'bilibili', path)
            editorial.plan_chapters(source, 'bilibili', path)
        self.assertEqual(model.call_count, 1)

    def test_empty_actual_reference_search_does_not_fall_back_to_generic_pages(self):
        generic = {'title': '行业写作指南', 'url': 'https://example.com/guide',
                   'evidence': '指南', 'notes': '先提问题。'}
        calls = []

        def retrieve(*_args, **options):
            calls.append(options)
            return [] if options.get('actual_works') else [generic]

        with scratch_dir() as root, patch('core.copywriting.retrieve_cases', side_effect=retrieve), \
                patch('core.copywriting._generate', return_value=[
                    {'angle': '设备进展', 'title': '安装之后', 'script': '安装完成后开始调试。', 'caption': '设备进展'}]):
            batch = copywriting.generate_batch(root, '工厂设备', '设备正在安装。', ['bilibili'], 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(batch['results']['bilibili']['references'], [])
