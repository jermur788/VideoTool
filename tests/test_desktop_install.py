import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import desktop_install
from videotool_version import __version__


class DesktopInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'Home With Spaces'
        self.environ = {
            'XDG_DATA_HOME': str(self.home / '.local' / 'share data'),
            'XDG_CONFIG_HOME': str(self.home / '.config data'),
            'VIDEOTOOL_BIN_DIR': str(self.home / '.local' / 'bin files'),
        }
        self.paths = desktop_install.destinations(self.environ, self.home)
        self.source = Path(desktop_install.__file__).resolve().parent

    def test_install_update_and_uninstall_are_scoped(self):
        installed = desktop_install.install(self.source, self.paths)
        self.assertTrue(installed['launcher'].is_file())
        self.assertTrue(installed['launcher'].stat().st_mode & 0o111)
        self.assertTrue(installed['desktop'].is_file())
        self.assertTrue(installed['icon'].is_file())
        desktop = installed['desktop'].read_text(encoding='utf-8')
        self.assertIn('Name=VideoTool', desktop)
        self.assertIn(f'Exec="{installed["launcher"]}"', desktop)
        launcher = installed['launcher'].read_text(encoding='utf-8')
        self.assertIn("APP_DIR='", launcher)
        manifest = json.loads((installed['app'] / desktop_install.MANIFEST).read_text())
        self.assertEqual(manifest['application'], 'VideoTool')
        self.assertIn('creator_benchmark.py', manifest['app_files'])
        self.assertTrue((installed['app'] / 'creator_benchmark.py').is_file())
        imported = subprocess.run(
            [sys.executable, '-B', '-c',
             'import creator_benchmark, videotool_gui; print(videotool_gui.__version__)'],
            cwd=installed['app'], text=True, capture_output=True, check=True)
        self.assertEqual(imported.stdout.strip(), __version__)
        health = subprocess.run([str(installed['launcher']), '--health'], text=True,
                                capture_output=True, check=True)
        self.assertIn(f'VideoTool {__version__}', health.stdout)

        (installed['app'] / 'README.md').write_text('old installed copy', encoding='utf-8')
        unrelated = installed['app'] / 'my-note.txt'
        unrelated.write_text('preserve me', encoding='utf-8')
        desktop_install.install(self.source, self.paths, update=True)
        self.assertNotEqual((installed['app'] / 'README.md').read_text(), 'old installed copy')

        config = Path(self.environ['XDG_CONFIG_HOME']) / 'videotool' / 'settings.json'
        config.parent.mkdir(parents=True)
        config.write_text('{}', encoding='utf-8')
        desktop_install.uninstall(self.paths, environ=self.environ, home=self.home)
        self.assertTrue(config.is_file())
        self.assertTrue(unrelated.is_file())
        self.assertFalse(installed['launcher'].exists())
        self.assertFalse(installed['desktop'].exists())
        self.assertFalse(installed['icon'].exists())

    def test_first_install_refuses_unmanaged_collision(self):
        self.paths['launcher'].parent.mkdir(parents=True)
        self.paths['launcher'].write_text('someone else', encoding='utf-8')
        with self.assertRaisesRegex(desktop_install.InstallError, 'unmanaged'):
            desktop_install.install(self.source, self.paths)
        self.assertEqual(self.paths['launcher'].read_text(), 'someone else')

    def test_remove_settings_requires_explicit_choice(self):
        desktop_install.install(self.source, self.paths)
        config = Path(self.environ['XDG_CONFIG_HOME']) / 'videotool'
        config.mkdir(parents=True)
        (config / 'settings.json').write_text('{}', encoding='utf-8')
        desktop_install.uninstall(self.paths, remove_settings=True,
                                  environ=self.environ, home=self.home)
        self.assertFalse(config.exists())

    def test_failed_optional_setup_keeps_base_install_managed(self):
        environment = self.paths['app'] / '.venv'
        def fail(command, check):
            environment.mkdir(parents=True, exist_ok=True)
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaisesRegex(desktop_install.InstallError,
                                    'local conversion features are still installed'):
            desktop_install.install(self.source, self.paths, with_gemini=True, runner=fail)
        self.assertTrue((self.paths['app'] / desktop_install.MANIFEST).is_file())
        self.assertFalse(environment.exists())
        desktop_install.install(self.source, self.paths, update=True)


if __name__ == '__main__':
    unittest.main()
