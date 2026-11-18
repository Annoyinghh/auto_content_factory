import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from studio_preferences import apply_export_preferences, read_video_resolution


class StudioPreferencesTest(unittest.TestCase):
    def test_missing_corrupt_and_invalid_settings_use_1080(self):
        with tempfile.TemporaryDirectory(prefix="acf-settings-") as folder:
            root = Path(folder)
            self.assertEqual(read_video_resolution(root), 1080)
            for value in ['{', 'null', '[]', '{"VideoResolution":true}',
                          '{"VideoResolution":"720"}', '{"VideoResolution":4320}', ' ' * 65537]:
                (root / "studio-settings.json").write_text(value, encoding="utf-8")
                self.assertEqual(read_video_resolution(root), 1080)

    def test_export_sizes_keep_orientation_and_scale_subtitles(self):
        original = copy.deepcopy(config.PLATFORM_PRESETS)
        with tempfile.TemporaryDirectory(prefix="acf-settings-") as folder:
            root = Path(folder)
            for resolution in (720, 1080, 1440):
                presets = copy.deepcopy(original)
                (root / "studio-settings.json").write_text(json.dumps({"VideoResolution": resolution}), encoding="utf-8")
                with patch.object(config, "DATA_DIR", root), patch.object(config, "PLATFORM_PRESETS", presets):
                    apply_export_preferences()
                for name, preset in presets.items():
                    size = (resolution * 16 // 9, resolution) if name == "bilibili" else (resolution, resolution * 16 // 9)
                    self.assertEqual((preset['width'], preset['height']), size)
                    self.assertEqual(preset['fps'], 30)
                    self.assertEqual(preset['sub_fontsize'], round(original[name]['sub_fontsize'] * resolution / 1080))
                    for key in ('cover_width', 'cover_height', 'voice', 'rate'):
                        self.assertEqual(preset.get(key), original[name].get(key))

    def test_resolution_changes_invalidate_existing_render_cache(self):
        from core.workflow import _render_key

        case = {"id": "settings-test", "scenes": [{"text": "unchanged narration"}]}
        baseline = _render_key(case)
        presets = copy.deepcopy(config.PLATFORM_PRESETS)
        presets['douyin'].update(width=720, height=1280)
        with patch.object(config, "PLATFORM_PRESETS", presets):
            self.assertNotEqual(_render_key(case), baseline)
            self.assertEqual(_render_key(case), _render_key(case))

    def test_worker_reads_preferences_before_dispatch(self):
        import studio_entry

        calls = []
        with patch('sys.argv', ['studio', '--worker', 'request.json', 'result.json']), \
             patch('studio_preferences.apply_export_preferences', side_effect=lambda: calls.append('preferences')), \
             patch('core.desktop_jobs.worker_main', side_effect=lambda *args: calls.append('worker') or 0):
            self.assertEqual(studio_entry.run_command(), 0)
        self.assertEqual(calls, ['preferences', 'worker'])


if __name__ == '__main__':
    unittest.main()
