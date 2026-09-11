import unittest
from unittest.mock import patch

import health_checks
import videotool_gui
from videotool_version import __version__


class HealthCheckTests(unittest.TestCase):
    def test_required_and_optional_readiness_are_plain_language(self):
        def importer(name):
            if name == 'tkinter':
                raise ModuleNotFoundError(name)
        with patch('health_checks.gemini_analysis.sdk_present', return_value=False), \
                patch('health_checks.gemini_analysis.api_key_present', return_value=False):
            checks = health_checks.checks(
                which=lambda name: '/usr/bin/ffmpeg' if name == 'ffmpeg' else None,
                importer=importer)
        report = health_checks.report(checks)
        self.assertIn('Python: Ready', report)
        self.assertIn('Desktop window: Needs attention', report)
        self.assertIn('ffmpeg: Ready', report)
        self.assertIn('ffprobe: Needs attention', report)
        self.assertIn('Gemini: Optional', report)

    def test_about_text_has_version_and_does_not_expose_a_key(self):
        checks = [health_checks.Check('Gemini', True, 'Client installed and API key saved', True)]
        text = videotool_gui.about_text(checks)
        self.assertIn(f'Installed/app version: {__version__}', text)
        self.assertIn('Gemini: Ready', text)
        self.assertIn('never display your API key', text)


if __name__ == '__main__':
    unittest.main()
