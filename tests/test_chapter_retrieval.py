import unittest
from unittest.mock import patch

from core import editorial
from test_daily import scratch_dir


class ChapterRetrievalTests(unittest.TestCase):
    def test_source_credits_and_headings_are_not_retrieved_as_facts(self):
        facts = [
            '屏幕上，一座自动化立体仓库逐渐成形。',
            '记者在现场体验智能看护与居家适老化产品。',
            '新华社记者在现场采访。',
            '新华社记者拍摄现场。',
            '新华社记者提问，多方回应。',
            '图为三菱电机在进博会上展示的工业AI平台。',
        ]
        metadata = [
            '在进博会上感受AI产业新脉动',
            '新华社上海11月8日电 题：在进博会上感受AI产业新脉动',
            '新华社上海11月8日电',
            '新华社记者高亢、孙青、胡洁菲',
            '新华社记者高亢、孙青、胡洁菲。',
            '记者：张梦洁、丁汀。',
            '记者：张梦洁，丁汀',
            '记者：张梦洁 丁汀 狄春 孙青 陈杰 李海伟 张璟怡',
            '记者 张梦洁 丁汀 狄春 孙青',
            '统筹：李杰 李佳琳 王冰笛',
            '新华社音视频部制作',
            '综合新华社记者报道',
            '新华社记者高亢 摄',
        ]
        report = {'id': 'r', 'title': '在进博会上感受AI产业新脉动-新华网',
                  'summary': '\n'.join([*metadata, *facts])}
        chapter = {'story': report, 'question': '现场展示了什么？', 'reports': [report]}
        with scratch_dir() as root, patch('core.editorial.scope_excerpts',
                side_effect=lambda _c, _q, _b, excerpts, **_kw: excerpts):
            result = editorial.retrieve_chapter_evidence({'reports': [report]}, [chapter], root)
        self.assertEqual([row['quote'] for row in result[0]['source_excerpts'].values()], facts)

    def test_a_credit_only_report_cannot_supply_a_news_chapter(self):
        report = {'id': 'r', 'title': '报道署名',
                  'summary': '新华社记者高亢、孙青、胡洁菲\n新华社音视频部制作\n统筹：李杰 李佳琳 王冰笛'}
        chapter = {'story': report, 'question': '现场展示了什么？', 'reports': [report]}
        with scratch_dir() as root, patch('core.editorial.scope_excerpts',
                side_effect=AssertionError('Credit-only sources reached the model')):
            with self.assertRaisesRegex(ValueError, '事实段落'):
                editorial.retrieve_chapter_evidence({'reports': [report]}, [chapter], root)

    def test_writer_filters_credits_from_legacy_and_prepared_excerpts(self):
        fact = '记者在现场体验智能看护与居家适老化产品。'
        credit = '新华社记者高亢、孙青、胡洁菲'
        report = {'id': 'r', 'title': '智能看护体验',
                  'summary': credit + '\n新华社音视频部制作\n' + fact * 90}
        chapter = {'story': report, 'question': '记者体验了什么？', 'reports': [report]}
        prepared = {'credit': {'source_id': 'r', 'quote': credit},
                    'fact': {'source_id': 'r', 'quote': fact}}
        received = []
        def write(_context, _reports, _visuals, excerpts, *_args, **_kwargs):
            received.append(excerpts)
            return {'source_ids': ['r'], 'scenes': [
                {'title': '现场体验', 'text': '报道记者体验了智能看护产品。', 'media': '', 'zoom': False},
                {'title': '居家场景', 'text': '这次体验还包括居家适老化产品。', 'media': '', 'zoom': False}]}
        for selection in (None, prepared):
            selected = {**chapter, **({'source_excerpts': selection} if selection is not None else {})}
            with self.subTest(prepared=selection is not None), scratch_dir() as root, \
                    patch('core.editorial._write_chapter_parts', side_effect=write):
                editorial.write_batches({'editorial': {'reports': [report]}, 'editorial_plan': {},
                    'chapters': [selected], 'target_seconds': 720}, 'bilibili', root,
                    lambda _: [], lambda *_: None, writing_only=True)
                self.assertTrue(received[-1])
                self.assertEqual({row['quote'] for row in received[-1].values()}, {fact})
        self.assertEqual(prepared['credit'], {'source_id': 'r', 'quote': credit})

    def test_cached_chapter_can_repair_a_stale_headline_without_rewriting_narration(self):
        quote='旅居车配备电动轮椅，方便乘客上下车。'
        retrieved={'late':{'source_id':'background','quote':quote}}
        chapter={**self.chapters[0],'reports':[self.background],'source_excerpts':retrieved}
        clean={'source_ids':['background'],'scenes':[
            {'title':'480辆旅居车','text':'这辆车的电动轮椅方便乘客进出。','media':'','motion':'cut','zoom':False},
            {'title':'电动轮椅','text':'乘客可以坐着上下车。','media':'','motion':'cut','zoom':False}]}
        item={'editorial':self.inputs,'editorial_plan':{},'chapters':[chapter],'target_seconds':720}
        with scratch_dir() as root:
            with patch('core.editorial._write_chapter_parts',return_value=clean), \
                    patch('core.editorial.chat_json',return_value={'titles':[{'index':0,'title':'坐着进出车厢'}]}):
                first=editorial.write_batches(item,'bilibili',root,lambda _:[],lambda *_:None,writing_only=True)
            self.assertEqual(first['scenes'][0]['title'],'坐着进出车厢')
            self.assertEqual(first['scenes'][0]['text'],clean['scenes'][0]['text'])
            with patch('core.editorial._write_chapter_parts',side_effect=AssertionError('Narration was regenerated')), \
                    patch('core.editorial.chat_json',side_effect=AssertionError('Title repair was not saved')):
                second=editorial.write_batches(item,'bilibili',root,lambda _:[],lambda *_:None,writing_only=True)
            self.assertEqual(second,first)

    def setUp(self):
        self.brief = {'title': '展会观察', 'summary': '记者在展会上看到了适老化产品。', 'id': 'brief'}
        self.background = {'title': '展品与生活', 'id': 'background',
                           'summary': '展会展示工业设备与制造流程。' * 120 + '\n旅居车配备电动轮椅，方便乘客上下车。'}
        self.inputs = {'anchor': {'title': '展会观察'}, 'reports': [self.brief, self.background],
                       'brief': {'notes': ['关注适老生活'], 'focus_id': 'brief', 'sequence': []}}
        self.chapters = [{'story': {**self.brief, 'title': '适老生活'}, 'question': '怎样方便乘客上下车？',
                          'reports': [self.brief]}]

    def select(self, _chapter, _question, _brief, excerpts, **_kwargs):
        return {key: value for key, value in excerpts.items() if '电动轮椅' in value['quote']}

    def test_chapter_retrieves_later_relevant_passages_from_all_selected_sources(self):
        with scratch_dir() as root, patch('core.editorial.scope_excerpts', side_effect=self.select):
            result = editorial.retrieve_chapter_evidence(self.inputs, self.chapters, root)
        self.assertEqual(result[0]['source_excerpts'], {
            next(iter(result[0]['source_excerpts'])): {'source_id': 'background', 'quote': '旅居车配备电动轮椅，方便乘客上下车。'}})
        self.assertIn('background', [row['id'] for row in result[0]['reports']])
        self.assertNotIn('制造流程', str(result[0]['source_excerpts']))

    def test_retrieval_cannot_introduce_a_passage_outside_the_confirmed_originals(self):
        with scratch_dir() as root, patch('core.editorial.scope_excerpts', return_value={
                'fabricated': {'source_id': 'background', 'quote': '自行补充的产品疗效。'}}):
            with self.assertRaisesRegex(ValueError, '原文'):
                editorial.retrieve_chapter_evidence(self.inputs, self.chapters, root)

    def test_same_retrieval_is_reused_but_source_changes_invalidate_it(self):
        with scratch_dir() as root:
            with patch('core.editorial.scope_excerpts', side_effect=self.select):
                first = editorial.retrieve_chapter_evidence(self.inputs, self.chapters, root)
            with patch('core.editorial.scope_excerpts', side_effect=AssertionError('Cached retrieval called the model')):
                self.assertEqual(editorial.retrieve_chapter_evidence(self.inputs, self.chapters, root), first)
            changed = {**self.inputs, 'reports': [self.brief, {**self.background,
                'summary': '旅居车配备电动轮椅，乘客可以坐着上下车。'}]}
            with patch('core.editorial.scope_excerpts', side_effect=self.select):
                result = editorial.retrieve_chapter_evidence(changed, self.chapters, root)
            self.assertIn('乘客可以坐着上下车', str(result[0]['source_excerpts']))

    def test_later_chapter_cannot_repeat_previously_assigned_passages(self):
        chapters = [self.chapters[0], {**self.chapters[0], 'question': '补充剩余证据'}]
        def select(_chapter, question, _brief, excerpts, **_kwargs):
            return (self.select(_chapter, question, _brief, excerpts) if question != '补充剩余证据'
                    else excerpts)
        with scratch_dir() as root, patch('core.editorial.scope_excerpts', side_effect=select):
            result = editorial.retrieve_chapter_evidence(self.inputs, chapters, root)
        self.assertFalse(set(result[0]['source_excerpts']) & set(result[1]['source_excerpts']))
        self.assertNotIn('电动轮椅', str(result[1]['source_excerpts']))

    def test_writer_uses_retrieved_passages_instead_of_restarting_at_the_article_beginning(self):
        quote = '旅居车配备电动轮椅，方便乘客上下车。'
        retrieved = {'late': {'source_id': 'background', 'quote': quote}}
        chapter = {**self.chapters[0], 'reports': [self.background], 'source_excerpts': retrieved}
        result = {'source_ids': ['background'], 'scenes': [
            {'title': '上下车', 'text': '这辆车让乘客坐着进出车厢。', 'media': '', 'motion': 'cut', 'zoom': False,
             'evidence': [retrieved['late']]},
            {'title': '辅助装置', 'text': '原文说的是电动轮椅辅助进出车辆的场景。', 'media': '', 'motion': 'cut', 'zoom': False,
             'evidence': [retrieved['late']]}]}
        received = []
        def writer(_context, _reports, _visuals, excerpts, *_args, **_kwargs):
            received.append(excerpts)
            return result
        with scratch_dir() as root, patch('core.editorial._write_chapter_parts', side_effect=writer), \
                patch('core.editorial.scope_excerpts', side_effect=lambda _title, _question, _brief, excerpts: excerpts):
            editorial.write_batches({'editorial': self.inputs, 'editorial_plan': {}, 'chapters': [chapter],
                                      'target_seconds': 720}, 'bilibili', root, lambda _: [], lambda *_: None,
                                     writing_only=True)
        self.assertEqual(received, [retrieved])

    def test_short_news_does_not_get_the_same_padding_budget_as_detailed_background(self):
        short_quote = '旅居车配备电动轮椅，方便乘客上下车。'
        details = ['展会上演示智能仓储的规划流程，参观者可以先观察货架的位置，再看设备怎样运行，平台展示了设计与验证的衔接过程。',
                   '报道把需求反馈、软件研发、本地制造和方案验证放在一起介绍，记录参与企业如何协作，没有提供所有产品的性能结论。',
                   '工作人员从场景需求出发，先介绍现有流程，再展示软件的设计界面，最后解释测试结果怎样反馈给参与开发的团队。']
        report = {**self.background, 'summary': self.background['summary']+'\n'+'\n'.join(details)}
        chapters = [{**self.chapters[0], 'story': {'title': '简讯'}, 'reports': [report],
                     'source_excerpts': {'short': {'source_id': 'background', 'quote': short_quote}}},
                    {**self.chapters[0], 'story': {'title': '背景'}, 'reports': [report],
                     'source_excerpts': {str(i): {'source_id': 'background', 'quote': text} for i, text in enumerate(details)}}]
        budgets = []
        def writer(context, _reports, _visuals, excerpts, count, target, *_args, **_kwargs):
            budgets.append((count, target))
            return {'source_ids': ['background'], 'scenes': [
                {'title': context['chapter'], 'text': context['chapter']+'里的第一项事实已核对。', 'media': '', 'motion': 'cut', 'zoom': False},
                {'title': context['chapter'], 'text': context['chapter']+'里的下一项事实来自原文。', 'media': '', 'motion': 'cut', 'zoom': False}]}
        with scratch_dir() as root, patch('core.editorial._write_chapter_parts', side_effect=writer):
            editorial.write_batches({'editorial': {**self.inputs, 'reports': [report]}, 'editorial_plan': {},
                'chapters': chapters, 'target_seconds': 720}, 'bilibili', root, lambda _: [], lambda *_: None,
                writing_only=True)
        self.assertLess(budgets[0][0], budgets[1][0])
        self.assertLess(budgets[0][1], budgets[1][1])

    def test_retrieved_facts_are_divided_between_batches_even_with_a_user_brief(self):
        excerpts = {str(i): {'source_id': 'r', 'quote': f'原文第{i}个独立事实用于本章。'} for i in range(6)}
        groups = []
        def write(_context, _schema, _reports, _visuals, selected, *_args, **_options):
            groups.append(set(selected))
            return {'scenes': [{'text': '本批事实已核对。'}], 'source_ids': ['r']}
        with scratch_dir() as root, patch('core.editorial._write_batch', side_effect=write):
            editorial._write_chapter_parts({'position': '中段', 'writing_only': True,
                'brief': {'notes': ['解释已选事实']}, 'retrieved_evidence': True}, [], [], excerpts, 9, 900,
                root/'chapter.json', short=False)
        self.assertEqual(len(groups), 3)
        self.assertEqual(set.union(*groups), set(excerpts))
        self.assertFalse(groups[0] & groups[1] or groups[1] & groups[2] or groups[0] & groups[2])

    def test_sparse_chapter_does_not_treat_previous_narration_as_its_own_evidence(self):
        first = {'1': {'source_id': 'background', 'quote': '多家机构表达了订购意愿。'}}
        second = {'2': {'source_id': 'background', 'quote': '记者体验智能看护与居家适老化产品。'}}
        report = {**self.background, 'summary': self.background['summary']+'\n'+first['1']['quote']+'\n'+second['2']['quote']}
        chapters = [{**self.chapters[0], 'story': {'title': title}, 'reports': [report], 'source_excerpts': passages}
                    for title, passages in (('机构反馈', first), ('居家场景', second))]
        contexts = []
        def write(context, _reports, _visuals, _excerpts, *_args, **_kwargs):
            contexts.append(context)
            text = '多家机构已经表达了订购意愿。' if len(contexts)==1 else '记者体验的是居家适老化产品。'
            return {'source_ids': ['background'], 'scenes': [
                {'title': context['chapter'], 'text': text, 'media': '', 'motion': 'cut', 'zoom': False},
                {'title': context['chapter'], 'text': context['chapter']+'的相关事实来自所选报道。', 'media': '', 'motion': 'cut', 'zoom': False}]}
        with scratch_dir() as root, patch('core.editorial._write_chapter_parts', side_effect=write):
            editorial.write_batches({'editorial': {**self.inputs, 'reports': [report]}, 'editorial_plan': {},
                'chapters': chapters, 'target_seconds': 720}, 'bilibili', root, lambda _: [], lambda *_: None,
                writing_only=True)
        self.assertNotIn('订购意愿', contexts[1]['earlier_narration'])
        self.assertNotIn('订购意愿', contexts[1]['previous_voice'])

    def test_multitopic_semicolon_sentence_does_not_leak_unrelated_objects_into_a_chapter(self):
        report={**self.background,'summary':'绿电算力舱展示电力与算力的匹配；适老化旅居车配备电动轮椅方便上下车；医疗设备关注精细操作。'}
        inputs={**self.inputs,'reports':[report]}
        with scratch_dir() as root,patch('core.editorial.scope_excerpts',side_effect=self.select):
            result=editorial.retrieve_chapter_evidence(inputs,self.chapters,root)
        text=''.join(r['quote'] for r in result[0]['source_excerpts'].values())
        self.assertIn('电动轮椅',text)
        self.assertNotIn('绿电',text)
        self.assertNotIn('医疗',text)


if __name__ == '__main__':
    unittest.main()
