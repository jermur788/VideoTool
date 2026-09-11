import json
from pathlib import Path
import tempfile
import unittest

import user_settings


class UserSettingsTests(unittest.TestCase):
    def test_default_path_follows_xdg_or_home(self):
        self.assertEqual(
            user_settings.settings_path({'XDG_CONFIG_HOME': '/tmp/config'}),
            Path('/tmp/config/videotool/settings.json'))
        self.assertEqual(
            user_settings.settings_path({}, home='/tmp/home'),
            Path('/tmp/home/.config/videotool/settings.json'))

    def test_starting_folder_round_trip_and_reset_preserve_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'footage'
            folder.mkdir()
            settings = root / 'config' / 'settings.json'
            settings.parent.mkdir()
            settings.write_text(json.dumps({'future_setting': True}), encoding='utf-8')
            self.assertEqual(user_settings.save_default_source_folder(folder, settings), folder)
            self.assertEqual(user_settings.default_source_folder(settings), folder)
            self.assertTrue(user_settings.clear_default_source_folder(settings))
            self.assertIsNone(user_settings.default_source_folder(settings))
            self.assertTrue(json.loads(settings.read_text())['future_setting'])

    def test_missing_or_malformed_starting_folder_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / 'settings.json'
            settings.write_text('{not json', encoding='utf-8')
            self.assertIsNone(user_settings.default_source_folder(settings))
            settings.write_text(json.dumps({'default_source_folder': str(root / 'missing')}),
                                encoding='utf-8')
            self.assertIsNone(user_settings.default_source_folder(settings))
            with self.assertRaisesRegex(ValueError, 'not available'):
                user_settings.save_default_source_folder(root / 'missing', settings)


if __name__ == '__main__':
    unittest.main()
