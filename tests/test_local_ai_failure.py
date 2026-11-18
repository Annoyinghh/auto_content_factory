import unittest
from unittest.mock import patch

import requests

from core.local_ai import LocalAIUnavailable,chat_json


class LocalAIFailureTests(unittest.TestCase):
    def test_service_timeout_is_distinct_from_a_bad_scene_response(self):
        with patch('core.local_ai.model_for',return_value='vision'),patch('requests.Session') as session:
            session.return_value.__enter__.return_value.post.side_effect=requests.Timeout('unresponsive')
            with self.assertRaises(LocalAIUnavailable):
                chat_json('只描述画面',{}, {'type':'object'},model='vision')
