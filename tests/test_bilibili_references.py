import json
import unittest
from unittest.mock import patch

from core.copywriting import _public_work, collect_topic_cases


class BilibiliReferenceTests(unittest.TestCase):
    bvid = 'BV111BeBPEaD'
    url = 'https://www.bilibili.com/video/' + bvid
    description = '这一周的经济新闻提出了哪些问题？先梳理变化，再逐项解释背景和已知限制。'

    def response(self, **changes):
        data = {'bvid': self.bvid, 'title': '一周经济新闻', 'desc': self.description,
                'duration': 720, 'pubdate': 1766977200}
        data.update(changes)
        return json.dumps({'code': 0, 'data': data}).encode()

    def test_unreadable_html_uses_verified_public_description_only(self):
        with patch('core.copywriting._read_public', side_effect=[ValueError('HTTP 412'), self.response()]) as read:
            work = _public_work('bilibili', self.url)
        self.assertEqual(work['url'], self.url)
        self.assertEqual(work['excerpt'], self.description)
        self.assertIn('未读取视频口播', work['evidence'])
        self.assertIn('热度未核验', work['evidence'])
        self.assertEqual(read.call_args.args[0], 'https://api.bilibili.com/x/web-interface/view')
        self.assertEqual(read.call_args.kwargs['params'], {'bvid': self.bvid})
        self.assertIn('User-Agent', read.call_args.kwargs['headers'])

    def test_metadata_fallback_rejects_wrong_work_and_unusable_fields(self):
        for changes in ({'bvid': 'BV1UJVM6BE7h'}, {'title': ''}, {'desc': '-'},
                        {'desc': None}, {'duration': 0}, {'duration': True}, {'duration': '720'}):
            with self.subTest(changes=changes), patch('core.copywriting._read_public',
                    side_effect=[ValueError('HTTP 412'), self.response(**changes)]):
                with self.assertRaises(ValueError):
                    _public_work('bilibili', self.url)

    def test_nonzero_api_code_and_invalid_json_are_not_reference_material(self):
        for body in (b'<html>verification</html>', b'{"code":-412,"data":{}}', b'[]'):
            with self.subTest(body=body), patch('core.copywriting._read_public',
                    side_effect=[ValueError('HTTP 412'), body]):
                with self.assertRaises(ValueError):
                    _public_work('bilibili', self.url)

    def test_readable_html_needs_no_api_request(self):
        page = ('<meta property="og:title" content="一周经济新闻">'
                '<meta property="og:description" content="' + self.description + '">').encode()
        with patch('core.copywriting._read_public', return_value=page) as read:
            self.assertEqual(_public_work('bilibili', self.url)['excerpt'], self.description)
        read.assert_called_once()

    def test_search_fallback_uses_only_valid_bvids_then_rehydrates_description(self):
        rss = b'<rss><channel><item><link>https://news.example.com/article</link></item></channel></rss>'
        search = json.dumps({'code': 0, 'data': {'result': [
            {'bvid': '', 'arcurl': 'https://evil.test/video'},
            {'bvid': '../escape'}, {'bvid': self.bvid, 'description': 'Do not trust a search snippet'},
            {'bvid': self.bvid},
        ]}}).encode()
        work = {'title': '未来一周重大经济事件', 'url': self.url,
                'excerpt': self.description, 'evidence': '官方作品简介 · 热度未核验'}
        with patch('core.intake.read_intake', return_value={'ranks': {}}), \
                patch('core.copywriting.list_cases', return_value=[]), \
                patch('core.copywriting._read_public', side_effect=[rss, search]) as read, \
                patch('core.copywriting._public_work', return_value=work) as fetch, \
                patch('core.copywriting.analyze_case', return_value={'keywords': '经济', 'notes': '问题引入'}) as analyze, \
                patch('core.copywriting.add_case'):
            result = collect_topic_cases('.', 'bilibili',
                {'topic': '10月国民经济运行总体平稳向新向优', 'search_query': '经济 一周'})
        self.assertEqual(result['saved'], [work['title']])
        fetch.assert_called_once_with('bilibili', self.url)
        self.assertEqual(analyze.call_args.args[1], self.description)
        self.assertEqual(read.call_args.kwargs['params']['keyword'], '经济 一周')

    def test_explicit_link_does_not_trigger_search(self):
        with patch('core.copywriting.list_cases', return_value=[]), \
                patch('core.copywriting._read_public', side_effect=[ValueError('HTTP 412'), self.response()]) as read, \
                patch('core.copywriting.analyze_case', return_value={'keywords': '经济', 'notes': '问题引入'}), \
                patch('core.copywriting.add_case'):
            result = collect_topic_cases('.', 'bilibili', {'topic': '经济', 'url': self.url})
        self.assertEqual(result['saved'], ['一周经济新闻'])
        self.assertFalse(any('search' in call.args[0] for call in read.call_args_list))


if __name__ == '__main__':
    unittest.main()
