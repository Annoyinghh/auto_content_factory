import unittest
from unittest.mock import patch

from core.local_ai import chat_json


class LocalAIResponseTests(unittest.TestCase):
    def response(self, payload, **options):
        with patch('core.local_ai.model_for', return_value='test-writer'), patch('requests.Session') as session:
            transport = session.return_value.__enter__.return_value
            transport.post.return_value.status_code = 200
            transport.post.return_value.json.return_value = payload
            result = chat_json('返回指定结构', {}, {'type': 'object'}, model='test-writer', **options)
            self.assertEqual(transport.post.call_count, 1)
            return result

    def test_truncated_json_identifies_output_budget_instead_of_generic_format_failure(self):
        payload = {'message': {'content': '{"chapters": [],'}, 'done_reason': 'length', 'eval_count': 600}
        with self.assertRaisesRegex(RuntimeError, r'test-writer.*长度上限.*600'):
            self.response(payload, max_tokens=600)

    def test_blank_final_content_distinguishes_exhausted_thinking_from_answer(self):
        payload = {'message': {'content': '', 'thinking': '{"private_reasoning": "not an answer"}'},
                   'done_reason': 'length', 'eval_count': 2048}
        with self.assertRaisesRegex(RuntimeError, r'空内容.*思考.*2048') as caught:
            self.response(payload, max_tokens=2048, reasoning=True)
        self.assertNotIn('private_reasoning', str(caught.exception))

    def test_malformed_json_reports_finish_reason_without_dumping_source_or_answer(self):
        payload = {'message': {'content': 'private-source-fragment'}, 'done_reason': 'stop', 'eval_count': 24}
        with self.assertRaisesRegex(RuntimeError, r'JSON.*stop.*24') as caught:
            self.response(payload)
        self.assertNotIn('private-source-fragment', str(caught.exception))

    def test_valid_json_is_accepted_even_if_the_backend_stopped_at_its_token_limit(self):
        result = self.response({'message': {'content': '{"ok": true}'}, 'done_reason': 'length', 'eval_count': 8})
        self.assertEqual(result, {'ok': True})
