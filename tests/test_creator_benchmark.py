from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import creator_benchmark
import gemini_analysis
import videotool_gui


class CreatorBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.video = self.folder / 'finished.mp4'
        self.original = b'unchanged finished video bytes'
        self.video.write_bytes(self.original)
        self.now = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
        self.remote = object()

    def uploader(self, calls, cancel=None):
        def upload(video, **kwargs):
            calls.append(('upload', Path(video), kwargs.get('client')))
            if cancel:
                cancel()
            return gemini_analysis.UploadedVideo(self.remote, 'files/one', 'gemini://one', 1.25, 2.5)
        return upload

    def generator(self, calls, fail_second=False, cancel_first=None):
        def generate(remote, name, prompt, model, cancel, report, client, label):
            calls.append(('generate', remote, prompt, label))
            if len([call for call in calls if call[0] == 'generate']) == 1 and cancel_first:
                cancel_first()
            if fail_second and label == 'Creator review':
                raise gemini_analysis.GeminiError('second request failed')
            return f'raw response for {label}', 3.5
        return generate

    def test_preview_text_makes_no_client_call_and_creates_no_report(self):
        before = set(self.folder.iterdir())
        metadata = {'streams': [{'index': 0, 'codec_type': 'video', 'width': 1920,
                                 'height': 1080, 'avg_frame_rate': '25/1',
                                 'pix_fmt': 'yuv420p'}]}
        with patch('videotool.inspect_video', return_value=metadata), \
                patch.object(gemini_analysis, '_client') as client:
            item = videotool_gui.preview_direct_gemini(self.video)
            text = creator_benchmark.preview_text(
                item.source, 'test-model', 'baseline exact', 'creator exact',
                'Find Shorts')
        client.assert_not_called()
        self.assertEqual(set(self.folder.iterdir()), before)
        self.assertIn(str(self.video), text)
        self.assertIn('Model requests: 2', text)
        self.assertIn('Network/API usage starts only after Run creator benchmark', text)
        self.assertIn('Purpose: Find Shorts', text)
        self.assertIn('baseline exact', text)
        self.assertIn('creator exact', text)

    def test_one_upload_is_reused_for_two_sequential_requests_and_three_artifacts(self):
        calls = []
        result = creator_benchmark.run(
            self.video, 'baseline', 'creator', model='test-model',
            uploader=self.uploader(calls), generator=self.generator(calls), now=self.now)
        self.assertEqual([call[0] for call in calls], ['upload', 'generate', 'generate'])
        generated = [call for call in calls if call[0] == 'generate']
        self.assertIs(generated[0][1], self.remote)
        self.assertIs(generated[1][1], self.remote)
        self.assertEqual([call[3] for call in generated], ['Baseline analysis', 'Creator review'])
        self.assertEqual(result.requests_completed, 2)
        self.assertFalse(result.error)
        for path in (result.baseline_path, result.creator_path, result.report_path):
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())
        report = result.report_path.read_text(encoding='utf-8')
        self.assertIn('1 upload; 2 of 2 model requests completed', report)
        self.assertIn('| Timestamp accuracy |  |  |  |', report)

    def test_every_purpose_has_a_focused_prompt_report_rubric_and_persistence(self):
        combined = []
        for purpose in creator_benchmark.purpose_names():
            with self.subTest(purpose=purpose):
                folder = self.folder / purpose.replace(' ', '-')
                folder.mkdir()
                video = folder / 'finished.mp4'
                video.write_bytes(self.original)
                calls = []
                prompt = creator_benchmark.purpose_prompt(purpose)
                result = creator_benchmark.run(
                    video, 'baseline', prompt, model='test-model',
                    uploader=self.uploader(calls), generator=self.generator(calls),
                    now=self.now, purpose=purpose)
                creator = result.creator_path.read_text(encoding='utf-8')
                report = result.report_path.read_text(encoding='utf-8')
                self.assertIn(f'Analysis purpose: `{purpose}`', creator)
                self.assertIn(f'Analysis purpose: `{purpose}`', report)
                self.assertIn(prompt, creator)
                details = creator_benchmark.PURPOSES[purpose]
                for focus in details['focus']:
                    self.assertIn(f'- {focus}', report)
                for criterion in details['criteria']:
                    self.assertIn(f'| {criterion} |  |  |  |', report)
                combined.append(report)
        all_reports = '\n'.join(combined)
        for criterion in (
                'Timestamp accuracy', 'Completeness', 'Pose/content recognition',
                'Chapter usefulness', 'Title/description usefulness',
                'Thumbnail suggestions/candidate frames', 'Shorts suggestions',
                'Hallucinations/errors', 'Creator correction',
                'Hands-on time', 'Processing time', 'Upload friction', 'Repeatability'):
            self.assertIn(criterion, all_reports)

    def test_prompt_drafts_survive_purpose_changes_and_reset_only_current_purpose(self):
        drafts = creator_benchmark.PromptDrafts('Publishing package')
        shorts_default = drafts.switch('my publishing edits', 'Find Shorts')
        self.assertEqual(shorts_default, creator_benchmark.purpose_prompt('Find Shorts'))
        restored_publish = drafts.switch('my shorts edits', 'Publishing package')
        self.assertEqual(restored_publish, 'my publishing edits')
        self.assertEqual(drafts.reset(), creator_benchmark.purpose_prompt('Publishing package'))
        self.assertEqual(drafts.switch('new publishing edits', 'Find Shorts'), 'my shorts edits')
        with self.assertRaisesRegex(gemini_analysis.GeminiError, 'supported creator analysis purpose'):
            drafts.switch('keep this', 'Unknown purpose')

    def test_cancellation_before_upload_starts_no_online_stage(self):
        cancel = threading.Event()
        cancel.set()
        calls = []
        result = creator_benchmark.run(
            self.video, 'baseline', 'creator', cancel=cancel,
            uploader=self.uploader(calls), generator=self.generator(calls), now=self.now)
        self.assertEqual(calls, [])
        self.assertEqual(result.requests_completed, 0)
        self.assertIn('cancelled', result.error.lower())
        self.assertTrue(result.report_path.is_file())

    def test_cancellation_after_upload_prevents_both_requests(self):
        cancel = threading.Event()
        calls = []
        result = creator_benchmark.run(
            self.video, 'baseline', 'creator', cancel=cancel,
            uploader=self.uploader(calls, cancel.set), generator=self.generator(calls), now=self.now)
        self.assertEqual([call[0] for call in calls], ['upload'])
        self.assertEqual(result.requests_completed, 0)
        self.assertIn('cancelled', result.error.lower())

    def test_cancellation_after_baseline_preserves_it_and_prevents_request_two(self):
        cancel = threading.Event()
        calls = []
        result = creator_benchmark.run(
            self.video, 'baseline', 'creator', cancel=cancel,
            uploader=self.uploader(calls),
            generator=self.generator(calls, cancel_first=cancel.set), now=self.now)
        self.assertEqual([call[0] for call in calls], ['upload', 'generate'])
        self.assertEqual(result.requests_completed, 1)
        self.assertTrue(result.baseline_path.is_file())
        self.assertIsNone(result.creator_path)
        self.assertIn('cancelled', result.error.lower())

    def test_second_request_failure_preserves_baseline_and_incomplete_report(self):
        calls = []
        result = creator_benchmark.run(
            self.video, 'baseline', 'creator', uploader=self.uploader(calls),
            generator=self.generator(calls, fail_second=True), now=self.now)
        self.assertEqual(result.requests_completed, 1)
        self.assertTrue(result.baseline_path.is_file())
        self.assertIsNone(result.creator_path)
        report = result.report_path.read_text(encoding='utf-8')
        self.assertIn('Incomplete', report)
        self.assertIn('second request failed', report)
        self.assertIn('Baseline response:', report)

    def test_benchmark_error_redacts_configured_secret(self):
        secret = 'benchmark-secret-value'
        calls = []
        def fail(*args, **kwargs):
            raise gemini_analysis.GeminiError(f'request rejected: {secret}')
        with patch.dict(os.environ, {'GEMINI_API_KEY': secret}, clear=False), \
                patch.object(gemini_analysis, '_stored_api_key', return_value=None):
            result = creator_benchmark.run(
                self.video, 'baseline', 'creator', uploader=self.uploader(calls),
                generator=fail, now=self.now)
        self.assertNotIn(secret, result.error)
        self.assertNotIn(secret, result.report_path.read_text(encoding='utf-8'))
        self.assertIn('[hidden]', result.error)

    def test_same_timestamp_allocates_distinct_complete_artifact_sets(self):
        first_calls, second_calls = [], []
        first = creator_benchmark.run(
            self.video, 'baseline', 'creator', uploader=self.uploader(first_calls),
            generator=self.generator(first_calls), now=self.now)
        first_contents = {path: path.read_bytes() for path in
                          (first.baseline_path, first.creator_path, first.report_path)}
        second = creator_benchmark.run(
            self.video, 'baseline', 'creator', uploader=self.uploader(second_calls),
            generator=self.generator(second_calls), now=self.now)
        self.assertTrue(set(first_contents).isdisjoint(
            {second.baseline_path, second.creator_path, second.report_path}))
        self.assertTrue(all(path.is_file() for path in
                            (second.baseline_path, second.creator_path, second.report_path)))
        for path, content in first_contents.items():
            self.assertEqual(path.read_bytes(), content)

    def test_source_bytes_remain_unchanged(self):
        calls = []
        result = creator_benchmark.run(
            self.video, 'baseline', 'creator', uploader=self.uploader(calls),
            generator=self.generator(calls), now=self.now)
        self.assertFalse(result.error)
        self.assertEqual(self.video.read_bytes(), self.original)

    def test_report_write_failure_returns_structured_incomplete_result(self):
        calls = []
        real_write = creator_benchmark._atomic_write
        def fail_report(path, content):
            if path.name.endswith('-report.md'):
                raise OSError('report destination is unavailable')
            return real_write(path, content)
        with patch.object(creator_benchmark, '_atomic_write', side_effect=fail_report):
            result = creator_benchmark.run(
                self.video, 'baseline', 'creator', uploader=self.uploader(calls),
                generator=self.generator(calls), now=self.now)
        self.assertIsNone(result.report_path)
        self.assertIn('Benchmark report could not be saved', result.error)
        self.assertEqual(result.requests_completed, 2)
        self.assertTrue(result.baseline_path.is_file())
        self.assertTrue(result.creator_path.is_file())

    def test_gui_benchmark_action_cancel_and_summary_labels_are_truthful(self):
        self.assertEqual(videotool_gui.gemini_action_text(True), 'Run creator benchmark')
        self.assertEqual(videotool_gui.interrupt_controls(True),
                         ('Gemini benchmark controls', 'Cancel Gemini benchmark', False))
        cancel = videotool_gui.cancel_status_text(True)
        self.assertIn('local video stays unchanged', cancel)
        self.assertIn('request already in progress must finish', cancel)
        self.assertNotIn('partial output', cancel)
        item = videotool_gui.Conversion(
            self.video, self.video, [], preset='aistudio', completed=True,
            completed_this_run=True, outcome='Done', direct_upload=True,
            creator_benchmark=True, gemini_response_path=self.folder / 'report.md',
            gemini_status='Saved')
        summary = videotool_gui.completion_summary([item])
        self.assertIn('Creator benchmark complete', summary)
        self.assertIn('Benchmark report: 1 saved locally', summary)
        self.assertNotIn('converted', summary.lower())


if __name__ == '__main__':
    unittest.main()
