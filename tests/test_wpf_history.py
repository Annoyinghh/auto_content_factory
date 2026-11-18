import copy
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from core.wpf_history import creation_list, creation_state, open_creation
from core.wpf_workspace import workspace_stage
from core.works_library import load_works, video_work_id


class CreationHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'daily/2026-11-11/edition.json'
        self.path.parent.mkdir(parents=True)
        self.old = {'id': 'previous', 'story': {'title': '上一份创作'}, 'edit': {'scenes': [{'text': '已保存口播'}]},
                    'edit_revision': 'saved-revision', 'package': {'video': 'kept-video.mp4'},
                    'suggestion': {'scenes': [{'text': '保留候选'}]}, 'suggestion_revision': 'candidate-revision',
                    'media': [{'page_url': 'source-original', 'file': 'original.mp4'}],
                    'preview': {'video': 'opening.mp4'}, 'full_preview': {'video': 'full.mp4'},
                    'full_preview_revision': 'saved-revision', 'release_dir': 'kept-release-directory'}
        self.current = {'id': 'current', 'story': {'title': '当前创作'}, 'edit': {'scenes': [{'text': '正在制作'}]}, 'edit_revision': 'live-revision'}
        self.edition = {'items': {'bilibili': self.current, 'douyin': copy.deepcopy(self.current)},
                        'previous_items': {'bilibili': [self.old]}}
        self.write()

    def write(self):
        self.path.write_text(json.dumps(self.edition, ensure_ascii=False), encoding='utf-8')

    def row(self):
        return next(row for row in creation_list(self.root)['rows'] if row['platform'] == 'bilibili' and not row['active'])

    def open(self, row):
        return open_creation(self.root, row['day'], row['platform'], row)

    def test_restore_preserves_both_creations_and_other_platform(self):
        self.assertTrue(self.open(self.row())['restored'])
        result = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(result['items']['bilibili'], self.old)
        self.assertEqual(result['items']['douyin'], self.current)
        self.assertEqual(result['previous_items']['bilibili'], [self.current])
        self.open(self.row())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), self.edition)

    def test_cleared_export_is_not_offered_as_a_creation_but_real_draft_remains(self):
        video = self.path.parent / 'legacy.mp4'
        video.write_bytes(b'fixture')
        self.edition['items']['xiaohongshu'] = {'id': 'legacy', 'story': {'title': '已清理旧成片'},
                                               'package': {'video': str(video)}}
        self.write()
        work = next(w for a in load_works(self.root) for w in a.works if w.title == '已清理旧成片')
        self.assertEqual(work.id, video_work_id(self.root, str(video)))
        recycle = self.root / '.works-recycle'
        recycle.mkdir()
        (recycle / 'cleared.json').write_text(json.dumps([work.id]), encoding='utf-8')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        rows = creation_list(self.root)['rows']
        self.assertNotIn('legacy', [r['id'] for r in rows])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.edition['items']['xiaohongshu']['edit'] = {'scenes': [{'text': '新保存的修改'}]}
        self.write()
        row = next(r for r in creation_list(self.root)['rows'] if r['id'] == 'legacy')
        self.assertEqual(row['artifact_state'], '仅有草稿')

    def test_recycled_export_is_hidden_and_restoring_marker_restores_listing(self):
        video = self.path.parent / 'export.mp4'
        video.write_bytes(b'fixture')
        self.edition['items']['xiaohongshu'] = {'id': 'export', 'story': {'title': '已导出的作品'},
                                               'package': {'video': str(video)}}
        self.write()
        recycle = self.root / '.works-recycle'
        recycle.mkdir()
        marker = recycle / video_work_id(self.root, str(video))
        marker.write_text('recycled', encoding='utf-8')
        self.assertNotIn('export', [r['id'] for r in creation_list(self.root)['rows']])
        marker.unlink()
        row = next(r for r in creation_list(self.root)['rows'] if r['id'] == 'export')
        self.assertEqual(row['artifact_state'], '已有成片')

    def test_cleared_export_stays_hidden_when_video_is_missing_or_output_tree_moves(self):
        video = self.path.parent / 'xiaohongshu/removed.mp4'
        item = {'id': 'removed', 'story': {'title': '已清理且视频已移除'}, 'package': {'video': str(video)}}
        self.edition['items']['xiaohongshu'] = item
        self.write()
        recycle = self.root / '.works-recycle'
        recycle.mkdir()
        (recycle / 'cleared.json').write_text(json.dumps([video_work_id(self.root, str(video))]), encoding='utf-8')
        before = self.path.read_bytes()
        self.assertNotIn('removed', [r['id'] for r in creation_list(self.root)['rows']])
        self.assertEqual(self.path.read_bytes(), before)
        item['package']['video'] = 'Z:/old-location/output/daily/2026-11-11/xiaohongshu/removed.mp4'
        self.write()
        self.assertNotIn('removed', [r['id'] for r in creation_list(self.root)['rows']])
        item['edit'] = {'scenes': [{'text': '保留真实草稿'}]}
        self.write()
        self.assertIn('removed', [r['id'] for r in creation_list(self.root)['rows']])
        del item['edit']
        (recycle / 'cleared.json').write_text('[]', encoding='utf-8')
        self.write()
        row = next(r for r in creation_list(self.root)['rows'] if r['id'] == 'removed')
        self.assertEqual(row['artifact_state'], '视频文件缺失')

    def test_both_preview_versions_and_unrendered_drafts_are_distinguished(self):
        for name, item in [('new.mp4', self.current), ('old.mp4', self.old)]:
            video = self.path.parent / name
            video.write_bytes(b'fixture')
            item['full_preview'] = {'video': str(video)}
        self.write()
        rows = creation_list(self.root)['rows']
        versions = [r for r in rows if r['platform'] == 'bilibili']
        self.assertEqual(len(versions), 2)
        self.assertEqual({r['artifact_state'] for r in versions}, {'已有试剪'})
        self.assertEqual({r['active'] for r in versions}, {True, False})
        self.assertEqual(next(r for r in rows if r['platform'] == 'douyin')['artifact_state'], '仅有草稿')

    def test_stale_selection_cannot_restore_a_cleared_legacy_export(self):
        video = self.path.parent / 'legacy.mp4'
        video.write_bytes(b'fixture')
        self.old.clear()
        self.old.update({'id': 'legacy', 'story': {'title': '被清理的旧成片'}, 'package': {'video': str(video)}})
        self.write()
        row = self.row()
        identifier = video_work_id(self.root, str(video))
        workspace_stage('workspace_work_recycle', '2026-11-11', self.root, None, {'id': identifier, 'restore': False})
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '已从作品库移除'):
            self.open(row)
        self.assertEqual(self.path.read_bytes(), before)
        workspace_stage('workspace_work_empty', '2026-11-11', self.root, None, {'ids': [identifier]})
        video.unlink()
        with self.assertRaisesRegex(ValueError, '已从作品库移除'):
            self.open(row)
        self.assertEqual(self.path.read_bytes(), before)
        self.old['edit'] = {'scenes': [{'text': '仍可编辑的真实草稿'}]}
        self.write()
        self.assertTrue(self.open(self.row())['restored'])

    def test_restore_holds_recycle_lock_until_edition_commit(self):
        from core.workflow import _edit_edition
        lockfile = self.root / '.works-recycle/.writes.sqlite3'
        def checked_edit(path, change):
            def checked_change(edition):
                connection = sqlite3.connect(lockfile, timeout=0)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        connection.execute('BEGIN IMMEDIATE')
                finally:
                    connection.close()
                change(edition)
            return _edit_edition(path, checked_change)
        with patch('core.wpf_history._edit_edition', checked_edit):
            self.assertTrue(self.open(self.row())['restored'])
        connection = sqlite3.connect(lockfile, timeout=0)
        try:
            connection.execute('BEGIN IMMEDIATE')
            connection.rollback()
        finally:
            connection.close()

    def test_missing_old_preview_does_not_revive_a_cleared_existing_export(self):
        video = self.path.parent / 'legacy.mp4'
        video.write_bytes(b'fixture')
        self.old.clear()
        self.old.update({'id': 'legacy', 'story': {'title': '旧成片'}, 'package': {'video': str(video)},
                         'preview': {'video': str(self.path.parent / 'missing-preview.mp4')}})
        self.write()
        row = self.row()
        identifier = video_work_id(self.root, str(video))
        workspace_stage('workspace_work_recycle', '2026-11-11', self.root, None, {'id': identifier, 'restore': False})
        workspace_stage('workspace_work_empty', '2026-11-11', self.root, None, {'ids': [identifier]})
        before = self.path.read_bytes()
        self.assertNotIn('legacy', [r['id'] for r in creation_list(self.root)['rows']])
        with self.assertRaisesRegex(ValueError, '已从作品库移除'):
            self.open(row)
        self.assertEqual(self.path.read_bytes(), before)

    def test_generation_lock_prevents_switch_and_preserves_every_byte(self):
        row = self.row()
        directory = self.path.parent / 'bilibili'
        directory.mkdir()
        for name in ('ai', 'render', 'media', 'import_media', 'preview', 'selection'):
            with self.subTest(stage=name):
                lockpath = self.path.parent / 'bilibili-selection.sqlite3' if name == 'selection' else directory / (name + '.sqlite3')
                connection = sqlite3.connect(lockpath)
                try:
                    connection.execute('BEGIN IMMEDIATE')
                    before = self.path.read_bytes()
                    self.assertTrue(creation_state(self.root, row['day'], 'bilibili')['busy'])
                    with self.assertRaisesRegex(RuntimeError, '后台处理'):
                        self.open(row)
                    self.assertEqual(self.path.read_bytes(), before)
                finally:
                    connection.close()
        self.assertFalse(creation_state(self.root, row['day'], 'bilibili')['busy'])
        self.open(row)

    def test_stale_current_and_historical_snapshot_cannot_overwrite(self):
        row = self.row()
        self.edition['items']['bilibili']['edit_revision'] = 'newer'
        self.write()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '当前工作稿已变化'):
            self.open(row)
        self.assertEqual(self.path.read_bytes(), before)
        row = self.row()
        self.edition['previous_items']['bilibili'][0]['edit_revision'] = 'changed'
        self.write()
        with self.assertRaisesRegex(ValueError, '历史创作已变化'):
            self.open(row)

    def test_list_and_state_are_read_only_and_current_view_allowed_while_busy(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        rows = workspace_stage('workspace_creation_list', '2026-11-11', self.root, None, {})['rows']
        state = workspace_stage('workspace_creation_state', '2026-11-11', self.root, 'bilibili', {})
        self.assertEqual(len(rows), 3)
        self.assertEqual(state['item'], self.current)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        row = next(r for r in rows if r['platform'] == 'bilibili' and r['active'])
        connection = sqlite3.connect(self.path.parent / 'bilibili-selection.sqlite3')
        try:
            connection.execute('BEGIN IMMEDIATE')
            self.assertFalse(workspace_stage('workspace_creation_open', row['day'], self.root, row['platform'], row)['restored'])
        finally:
            connection.close()

    def test_bad_day_and_platform_do_not_select_another_path(self):
        row = self.row()
        with self.assertRaises(ValueError):
            open_creation(self.root, '../2026-11-11', 'bilibili', row)
        with self.assertRaises(ValueError):
            open_creation(self.root, row['day'], '../bilibili', row)

    def test_open_live_creation_after_progress_change_never_restores_old_snapshot(self):
        row = next(row for row in creation_list(self.root)['rows'] if row['platform'] == 'bilibili' and row['active'])
        self.edition['items']['bilibili']['editorial_progress'] = '后台仍在生成'
        self.write()
        before = self.path.read_bytes()
        self.assertFalse(self.open(row)['restored'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_lock_does_not_hide_other_platform_or_day(self):
        (self.path.parent / 'douyin').mkdir()
        (self.path.parent / 'douyin/ai.sqlite3').write_bytes(b'not-a-database' * 100)
        result = creation_list(self.root)
        self.assertTrue(result['errors'])
        self.assertEqual(len([row for row in result['rows'] if row['platform'] == 'bilibili']), 2)

    def test_unknown_history_key_preserves_every_record(self):
        row = self.row()
        row['key'] = 'missing'
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '历史创作已变化'):
            self.open(row)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
