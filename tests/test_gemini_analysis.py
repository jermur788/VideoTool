from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import gemini_analysis
import videotool_gui


class FakeFiles:
    def __init__(self, states=('PROCESSING', 'ACTIVE')):
        self.states = list(states)
        self.uploaded = []
        self.gets = []

    def upload(self, file):
        self.uploaded.append(file)
        return SimpleNamespace(name='files/example', uri='gemini://example', state=self.states.pop(0))

    def get(self, name):
        self.gets.append(name)
        state = self.states.pop(0)
        return SimpleNamespace(name=name, uri='gemini://example', state=state)


class FakeModels:
    def __init__(self, text='A useful response.'):
        self.text = text
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text)


class GeminiAnalysisTests(unittest.TestCase):
    def test_secure_password_store_save_read_and_delete(self):
        values = {}
        fake = SimpleNamespace(
            set_password=lambda service, account, value: values.__setitem__((service, account), value),
            get_password=lambda service, account: values.get((service, account)),
            delete_password=lambda service, account: values.pop((service, account)),
        )
        with patch.dict(sys.modules, {'keyring': fake}):
            gemini_analysis.save_api_key(' saved-secret ')
            self.assertEqual(gemini_analysis.api_key(), 'saved-secret')
            self.assertTrue(gemini_analysis.delete_api_key())
            self.assertIsNone(gemini_analysis.api_key())

    def test_saved_key_rejects_shell_quotes_and_backslashes(self):
        with self.assertRaisesRegex(gemini_analysis.GeminiError, 'without quotation marks'):
            gemini_analysis.save_api_key('"copied-key"')
        with self.assertRaisesRegex(gemini_analysis.GeminiError, 'without spaces or backslashes'):
            gemini_analysis.save_api_key(r'copied\_key')

    def test_saved_key_takes_precedence_over_terminal_key(self):
        with patch.object(gemini_analysis, '_stored_api_key', return_value='saved-key'):
            self.assertEqual(gemini_analysis.api_key(), 'saved-key')

    def test_readiness_never_returns_the_key(self):
        secret = 'never-display-this-secret'
        with patch.object(gemini_analysis, 'sdk_present', return_value=True):
            ready, message = gemini_analysis.readiness({'GEMINI_API_KEY': secret})
        self.assertTrue(ready)
        self.assertNotIn(secret, message)
        self.assertEqual(message, 'Gemini is ready.')

    def test_missing_key_has_plain_setup_message(self):
        ready, message = gemini_analysis.readiness({})
        self.assertFalse(ready)
        self.assertEqual(message, 'Gemini API key not set up yet.')

    def test_api_errors_hide_the_configured_key(self):
        secret = 'a-secret-key-value'
        with patch.dict('os.environ', {'GEMINI_API_KEY': secret}, clear=False), \
                patch.object(gemini_analysis, '_stored_api_key', return_value=None):
            self.assertEqual(gemini_analysis._safe_error(RuntimeError(f'bad request {secret}')),
                             'bad request [hidden]')

    def test_invalid_key_error_has_plain_replacement_instruction(self):
        raw = RuntimeError("400 INVALID_ARGUMENT: API_KEY_INVALID: API key not valid")
        message = gemini_analysis._friendly_api_error(raw)
        self.assertIn('saved Gemini API key was rejected', message)
        self.assertIn('Save or replace key', message)
        self.assertNotIn('INVALID_ARGUMENT', message)

    def test_restricted_project_error_has_plain_access_instruction(self):
        raw = RuntimeError(
            '401 UNAUTHENTICATED: ACCESS_TOKEN_TYPE_UNSUPPORTED for FileService.CreateFile')
        message = gemini_analysis._friendly_api_error(raw)
        self.assertIn('API access is restricted', message)
        self.assertIn('billing', message)
        self.assertIn('active project', message)
        self.assertNotIn('ACCESS_TOKEN_TYPE_UNSUPPORTED', message)

    def test_upload_wait_and_analyze(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'prepared.mp4'
            video.write_bytes(b'video')
            client = SimpleNamespace(files=FakeFiles(), models=FakeModels('## Result'))
            stages = []
            result = gemini_analysis.analyze(video, 'Describe it', client=client,
                                             poll_interval=0,
                                             report=lambda *args: stages.append(args))
        self.assertEqual([stage for stage, _ in stages], ['Uploading', 'Processing', 'Analyzing'])
        self.assertEqual(result.text, '## Result')
        self.assertEqual(client.files.gets, ['files/example'])
        self.assertEqual(client.models.calls[0]['model'], gemini_analysis.DEFAULT_MODEL)
        self.assertEqual(client.models.calls[0]['contents'][1], 'Describe it')

    def test_supported_original_mov_can_be_uploaded_without_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'original.mov'
            video.write_bytes(b'video')
            client = SimpleNamespace(files=FakeFiles(('ACTIVE',)), models=FakeModels('Result'))
            result = gemini_analysis.analyze(video, 'Describe it', client=client)
        self.assertEqual(result.text, 'Result')
        self.assertEqual(client.files.uploaded, [str(video)])

    def test_processing_failure_does_not_generate(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'prepared.mp4'
            video.write_bytes(b'video')
            client = SimpleNamespace(files=FakeFiles(('FAILED',)), models=FakeModels())
            with self.assertRaisesRegex(gemini_analysis.GeminiError, 'rejected'):
                gemini_analysis.analyze(video, 'Describe it', client=client, poll_interval=0)
        self.assertEqual(client.models.calls, [])

    def test_empty_model_response_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'prepared.mp4'
            video.write_bytes(b'video')
            client = SimpleNamespace(files=FakeFiles(('ACTIVE',)), models=FakeModels('  '))
            with self.assertRaisesRegex(gemini_analysis.GeminiError, 'empty response'):
                gemini_analysis.analyze(video, 'Describe it', client=client)

    def test_cancel_during_processing_prevents_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'prepared.mp4'
            video.write_bytes(b'video')
            cancel = threading.Event()
            client = SimpleNamespace(files=FakeFiles(('PROCESSING', 'ACTIVE')), models=FakeModels())
            def report(stage, message):
                if stage == 'Processing':
                    cancel.set()
            with self.assertRaises(gemini_analysis.GeminiCancelled):
                gemini_analysis.analyze(video, 'Describe it', client=client, cancel=cancel,
                                        poll_interval=0, report=report)
        self.assertEqual(client.models.calls, [])

    def test_response_file_is_complete_and_collision_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            video = folder / 'prepared.mp4'
            video.write_bytes(b'video')
            result = gemini_analysis.AnalysisResult('Full answer', 'test-model', 'files/1', '', 1, 2, 3)
            now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
            first = gemini_analysis.write_response(folder / 'source.mov', video, 'My prompt', result,
                                                    folder / 'receipt.md', now)
            second = gemini_analysis.write_response(folder / 'source.mov', video, 'My prompt', result,
                                                     folder / 'receipt.md', now)
            text = first.read_text(encoding='utf-8')
        self.assertNotEqual(first, second)
        self.assertIn('My prompt', text)
        self.assertIn('Full answer', text)
        self.assertIn('test-model', text)
        self.assertTrue(second.name.endswith('-02.md'))

    def test_online_failure_keeps_local_conversion_successful(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            video = folder / 'prepared.mp4'
            video.write_bytes(b'video')
            item = videotool_gui.Conversion(folder / 'source.mov', video, [], preset='aistudio',
                                            completed=True, completed_this_run=True, outcome='Done')
            def fail(*args, **kwargs):
                raise gemini_analysis.GeminiError('quota reached')
            reports = []
            saved, failed = videotool_gui.analyze_completed(
                [item], 'My prompt', 'test-model', threading.Event(),
                lambda *args: reports.append(args), analyzer=fail)
        self.assertEqual(saved, [])
        self.assertEqual(failed, 1)
        self.assertTrue(item.completed)
        self.assertEqual(item.outcome, 'Done')
        self.assertEqual(item.gemini_error, 'quota reached')
        self.assertEqual(reports[-1][1], 'Done')

    def test_direct_upload_failure_says_original_was_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / 'original.mp4'
            video.write_bytes(b'video')
            item = videotool_gui.Conversion(
                video, video, [], preset='aistudio', completed=True,
                completed_this_run=True, outcome='Done', direct_upload=True)
            reports = []
            saved, failed = videotool_gui.analyze_completed(
                [item], 'My prompt', 'test-model', threading.Event(),
                lambda *args: reports.append(args),
                analyzer=lambda *args, **kwargs: (_ for _ in ()).throw(
                    gemini_analysis.GeminiError('bad key')))
        self.assertEqual((saved, failed), ([], 1))
        self.assertIn('Original video kept unchanged', reports[-1][2])
        self.assertNotIn('conversion succeeded', reports[-1][2].lower())

    def test_online_stage_refuses_more_than_one_video(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            items = []
            for number in (1, 2):
                output = folder / f'prepared-{number}.mp4'
                output.write_bytes(b'video')
                items.append(videotool_gui.Conversion(
                    folder / f'source-{number}.mov', output, [], preset='aistudio',
                    completed=True, completed_this_run=True, outcome='Done'))
            with self.assertRaisesRegex(gemini_analysis.GeminiError, 'one video at a time'):
                videotool_gui.analyze_completed(items, 'Prompt', 'model', threading.Event(),
                                                lambda *args: None)


if __name__ == '__main__':
    unittest.main()
