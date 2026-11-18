import unittest


class NoteProcedureTests(unittest.TestCase):
    def test_selected_source_ids_reach_the_photo_collector_without_a_new_search(self):
        import datetime as dt
        from unittest.mock import patch
        from core.workflow import run_stage
        from core.daily_news import story_key
        from test_daily import scratch_dir
        article = {'title':'肠粉步骤', 'url':'https://example.com/rice-roll', 'published':'2026-11-10',
                   'summary':'肠粉先切块再蒸，详情在图中。'}
        selected = [story_key(article)]
        with scratch_dir() as root, patch('core.intake.read_intake', return_value={'articles':[article]}), \
                patch('core.copywriting.collect_topic_cases') as search, \
                patch('core.copywriting.retrieve_cases', return_value=[]), \
                patch('core.note_images.collect_source_note_photos', side_effect=ValueError('需补选图片')) as collect:
            batch = run_stage('xiaohongshu_notes', dt.date.today(), root, payload={
                'topic':'肠粉教程', 'facts':'', 'count':2, 'keyword_topic':True,
                'photo_first':True, 'source_ids':selected})
        search.assert_not_called()
        self.assertEqual(collect.call_args.args[-1], selected)
        self.assertEqual(collect.call_args.kwargs, {'allow_related':False})
        self.assertEqual(batch['errors']['xiaohongshu'], '需补选图片')

    def test_image_steps_keep_cut_before_steam_and_preserve_the_original_amounts(self):
        from core.note_procedure import note_procedure
        photo={'source_report_id':'rice-roll','source_summary':'原文说具体教程在图里。','photo_role':'process',
               'source_image_text':'肠粉斜着切块\n水开后上锅蒸3分钟\n热油炒香蒜末辣椒圈\n+2勺生抽，半勺老抽\n适量糖和盐拌匀\n淋在肠粉上就可以啦','path':'steps.jpg'}
        plan=note_procedure([photo])
        self.assertEqual([r['text'] for r in plan['steps']],photo['source_image_text'].splitlines())
        self.assertTrue(all(r['source_report_id']=='rice-roll' for r in plan['steps']))
        self.assertTrue(all(r['field']=='image_text' for r in plan['steps']))
        self.assertFalse(plan['conflicts'])

    def test_conflicting_body_and_image_amounts_require_another_source(self):
        from core.note_procedure import note_procedure
        photo={'source_summary':'1️⃣加入2勺老抽、2勺蚝油。\n2️⃣搅拌后淋在菜上。',
               'photo_role':'process','source_image_text':'加入2勺老抽、1勺蚝油。'}
        self.assertEqual(note_procedure([photo])['conflicts'],['蚝油'])

    def test_generated_intro_cannot_change_the_sourced_action_order(self):
        from unittest.mock import patch
        from core.copywriting import _generate
        photo={'title':'切好的肠粉与调制料汁过程','photo_role':'process','source_report_id':'rice-roll',
               'source_summary':'肠粉配辣椒酱油，具体步骤在图中。',
               'source_image_text':'肠粉斜切成块\n水开后上锅蒸3分钟\n热油炒香蒜末辣椒圈\n加入2勺生抽和半勺老抽\n拌匀后淋在肠粉上'}
        intro='不想在晚饭上花太多心思的时候，肠粉配一碗辣椒酱油，就是一份简单又有滋味的小满足。'
        good={'title':'肠粉配辣椒酱油','script':intro,'caption':'一碗热乎的小满足 #家常菜 #肠粉'}
        with patch('core.copywriting.chat_json',side_effect=[{**good,'script':'五分钟就能搞定。'+intro},
                {**good,'script':'适合不想开火的时候。'+intro},good]) as writer, \
                patch('core.copywriting._photo_copy_issues',return_value=''):
            draft,=_generate('xiaohongshu','美食教程','',[],1,[],0,illustrated=True,photo_sets=[[(None,photo)]])
        self.assertEqual(writer.call_count,3)
        self.assertTrue(draft['script'].startswith(intro))
        self.assertLess(draft['script'].index('肠粉斜切'),draft['script'].index('上锅蒸3分钟'))
        self.assertEqual(draft['script'].count('蒸'),1)
        self.assertEqual(len(draft['procedure_sources']),5)
