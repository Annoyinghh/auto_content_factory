import unittest
from unittest.mock import patch

from core.editorial import _write_chapter_parts
from test_daily import scratch_dir


class LargeChapterBatchTests(unittest.TestCase):
    def test_news_writer_cannot_read_future_batches_through_the_chapter_context(self):
        excerpts={str(i):{'source_id':'r','quote':f'独立事实{i}仅供对应批次使用。'} for i in range(6)}
        context={'position':'中段','writing_only':True,'retrieved_evidence':True,'chapter_excerpts':excerpts}
        def write(part,_schema,_reports,_visuals,quotes,*_args,**_options):
            self.assertTrue(set(part.get('chapter_excerpts',{})) <= set(quotes),
                            'Future-batch facts bypass the scoped excerpt list')
            return {'source_ids':['r'],'scenes':[{'text':'本批事实已经完成核对。'}]}
        with scratch_dir() as root,patch('core.editorial._write_batch',side_effect=write):
            _write_chapter_parts(context,[],[],excerpts,6,500,root/'chapter.json',short=False)

    def test_long_chapter_reaches_all_six_batches_and_reuses_verified_parts(self):
        excerpts = {str(i): {'source_id': 'r', 'quote': f'这是第{i}项已确认的独立事实。'} for i in range(12)}
        context = {'position': '片头', 'writing_only': True, 'retrieved_evidence': True}
        sections = []
        def write(part, _schema, _reports, _visuals, quotes, *_args, **_options):
            sections.append(part['position'])
            return {'source_ids': ['r'], 'scenes': [
                {'text': '批次事实已经完成核对。', 'evidence': [next(iter(quotes.values()))]}]}
        with scratch_dir() as root:
            with patch('core.editorial._write_batch', side_effect=write):
                first = _write_chapter_parts(context, [], [], excerpts, 18, 1400, root/'chapter.json', short=False)
            with patch('core.editorial._write_batch', side_effect=AssertionError('Verified part was regenerated')):
                self.assertEqual(_write_chapter_parts(context, [], [], excerpts, 18, 1400, root/'chapter.json', short=False), first)
        self.assertEqual(sections, ['片头', '中段', '中段', '中段', '中段', '中段'])
        self.assertEqual(len(first['scenes']), 6)
