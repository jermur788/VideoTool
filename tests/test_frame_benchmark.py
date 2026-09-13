from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import frame_benchmark
import gemini_analysis
import videotool_gui


class FrameBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.video = self.folder / 'finished.mp4'
        self.original = b'unchanged video'
        self.video.write_bytes(self.original)
        self.now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
        self.remote = object()

    def extractor(self, video, folder, cancel=None):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for number in (1, 2):
            path = folder / f'frame-{number:04d}.jpg'
            path.write_bytes(f'frame {number}'.encode())
            paths.append(path)
        return frame_benchmark.ExtractedFrames(tuple(paths), (0.0, 5.25), 5.0, 0.5)

    def contact(self, extracted, output, cancel=None):
        Path(output).write_bytes(b'contact sheet')
        return Path(output)

    def uploader(self, video, **kwargs):
        return gemini_analysis.UploadedVideo(self.remote, 'files/one', 'gemini://one', 1.0, 2.0)

    def native(self, remote, name, prompt, model, cancel, report, client, label):
        self.assertIs(remote, self.remote)
        self.assertEqual(label, 'Native-video analysis')
        return f'native: {prompt}', 3.0

    def frames(self, extracted, name, prompt, model, cancel, report, client):
        self.assertTrue(all(path.exists() for path in extracted.paths))
        return f'frames: {prompt}', 4.0

    def combined(self, native, frames, name, prompt, model, cancel, report, client):
        self.assertEqual(native, f'native: {prompt}')
        self.assertEqual(frames, f'frames: {prompt}')
        return f'combined: {prompt}', 5.0

    def test_preview_discloses_same_prompt_and_creates_nothing(self):
        before = set(self.folder.iterdir())
        text = frame_benchmark.preview_text(self.video, 'test-model', 'exact prompt')
        self.assertEqual(set(self.folder.iterdir()), before)
        self.assertIn('Gemini requests: 3', text)
        self.assertIn('first two requests use the exact same reviewed prompt', text)
        self.assertIn('final request reconciles both', text)
        self.assertIn('native-video path retains the video audio and timing', text)
        self.assertIn('selected timestamped frames', text)
        self.assertIn('exact prompt', text)

    def test_same_prompt_produces_two_responses_contact_sheet_and_report(self):
        result = frame_benchmark.run(
            self.video, 'same exact prompt', model='test-model', extractor=self.extractor,
            contact_writer=self.contact, uploader=self.uploader,
            native_generator=self.native, frame_generator=self.frames,
            combined_generator=self.combined, now=self.now)
        self.assertFalse(result.error)
        self.assertEqual(result.requests_completed, 3)
        self.assertEqual(result.frame_count, 2)
        self.assertEqual(result.timestamps, (0.0, 5.25))
        for path in (result.native_path, result.frames_path, result.combined_path,
                     result.contact_sheet_path, result.report_path):
            self.assertTrue(path.is_file())
        native = result.native_path.read_text(encoding='utf-8')
        frames = result.frames_path.read_text(encoding='utf-8')
        combined = result.combined_path.read_text(encoding='utf-8')
        report = result.report_path.read_text(encoding='utf-8')
        self.assertIn('same exact prompt', native)
        self.assertIn('same exact prompt', frames)
        self.assertIn('00:05.250', frames)
        self.assertIn('combined: same exact prompt', combined)
        self.assertIn('## Combination policy', combined)
        self.assertIn('Do not repeat speculative place names', combined)
        self.assertIn('3 completed; 3 planned', report)
        self.assertIn('## Combination policy', report)
        self.assertIn('no audio or continuous motion', report)
        self.assertIn('| Timestamp accuracy |', report)
        self.assertEqual(self.video.read_bytes(), self.original)

    def test_frame_failure_preserves_native_response_contact_sheet_and_report(self):
        def fail(*args, **kwargs):
            raise gemini_analysis.GeminiError('frame request failed')
        result = frame_benchmark.run(
            self.video, 'prompt', extractor=self.extractor, contact_writer=self.contact,
            uploader=self.uploader, native_generator=self.native,
            frame_generator=fail, combined_generator=self.combined, now=self.now)
        self.assertEqual(result.requests_completed, 1)
        self.assertTrue(result.native_path.is_file())
        self.assertIsNone(result.frames_path)
        self.assertTrue(result.contact_sheet_path.is_file())
        self.assertTrue(result.report_path.is_file())
        self.assertIn('frame request failed', result.error)

    def test_combined_failure_preserves_both_preliminary_responses(self):
        def fail(*args, **kwargs):
            raise gemini_analysis.GeminiError('combined request failed')
        result = frame_benchmark.run(
            self.video, 'prompt', extractor=self.extractor, contact_writer=self.contact,
            uploader=self.uploader, native_generator=self.native,
            frame_generator=self.frames, combined_generator=fail, now=self.now)
        self.assertEqual(result.requests_completed, 2)
        self.assertTrue(result.native_path.is_file())
        self.assertTrue(result.frames_path.is_file())
        self.assertIsNone(result.combined_path)
        self.assertTrue(result.contact_sheet_path.is_file())
        self.assertTrue(result.report_path.is_file())
        self.assertIn('combined request failed', result.error)

    def test_cancel_after_frame_response_prevents_combined_request(self):
        cancel = threading.Event()
        combined_calls = []
        def frames_then_cancel(*args, **kwargs):
            cancel.set()
            return 'frames: prompt', 4.0
        def combined(*args, **kwargs):
            combined_calls.append(True)
            return 'should not run', 5.0
        result = frame_benchmark.run(
            self.video, 'prompt', cancel=cancel, extractor=self.extractor,
            contact_writer=self.contact, uploader=self.uploader,
            native_generator=self.native, frame_generator=frames_then_cancel,
            combined_generator=combined, now=self.now)
        self.assertEqual(result.requests_completed, 2)
        self.assertEqual(combined_calls, [])
        self.assertTrue(result.native_path.is_file())
        self.assertTrue(result.frames_path.is_file())
        self.assertIsNone(result.combined_path)
        self.assertIn('cancelled', result.error.lower())

    def test_next_matching_run_resumes_after_failed_frame_without_uploading_again(self):
        def fail_frames(*args, **kwargs):
            raise gemini_analysis.GeminiError('temporary frame failure')
        first = frame_benchmark.run(
            self.video, 'same prompt', model='test-model', extractor=self.extractor,
            contact_writer=self.contact, uploader=self.uploader,
            native_generator=self.native, frame_generator=fail_frames,
            combined_generator=self.combined, now=self.now)
        self.assertEqual(first.requests_completed, 1)
        original_report = first.report_path.read_text(encoding='utf-8')
        preview = frame_benchmark.preview_text(self.video, 'test-model', 'same prompt')
        self.assertIn('Resume available from:', preview)
        self.assertIn('video will not be uploaded again', preview)
        def unexpected(*args, **kwargs):
            raise AssertionError('Completed upload/native stage repeated')
        resumed = frame_benchmark.run(
            self.video, 'same prompt', model='test-model', extractor=self.extractor,
            contact_writer=self.contact, uploader=unexpected,
            native_generator=unexpected, frame_generator=self.frames,
            combined_generator=self.combined, now=self.now)
        self.assertFalse(resumed.error)
        self.assertEqual(resumed.requests_completed, 3)
        self.assertTrue(resumed.frames_path.is_file())
        self.assertTrue(resumed.combined_path.is_file())
        self.assertIn('recovery-report', resumed.report_path.name)
        recovery = resumed.report_path.read_text(encoding='utf-8')
        self.assertIn('without uploading the video or repeating that request', recovery)
        self.assertIn('Recovery session time', recovery)
        self.assertEqual(first.report_path.read_text(encoding='utf-8'), original_report)
        self.assertEqual(self.video.read_bytes(), self.original)

    def test_resume_after_combined_failure_reuses_saved_frame_response(self):
        def fail_combined(*args, **kwargs):
            raise gemini_analysis.GeminiError('temporary combined failure')
        first = frame_benchmark.run(
            self.video, 'same prompt', model='test-model', extractor=self.extractor,
            contact_writer=self.contact, uploader=self.uploader,
            native_generator=self.native, frame_generator=self.frames,
            combined_generator=fail_combined, now=self.now)
        self.assertEqual(first.requests_completed, 2)
        def unexpected_frames(*args, **kwargs):
            raise AssertionError('Completed frame request repeated')
        resumed = frame_benchmark.run(
            self.video, 'same prompt', model='test-model', extractor=self.extractor,
            frame_generator=unexpected_frames, combined_generator=self.combined,
            uploader=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError('Upload repeated')), now=self.now)
        self.assertFalse(resumed.error)
        self.assertEqual(resumed.requests_completed, 3)
        self.assertEqual(resumed.frames_path, first.frames_path)

    def test_frame_request_sends_timestamp_labels_images_and_exact_prompt(self):
        extracted = self.extractor(self.video, self.folder / 'request-frames')
        calls = []
        class Part:
            @staticmethod
            def from_text(text):
                return ('text', text)
            @staticmethod
            def from_bytes(data, mime_type):
                return ('image', data, mime_type)
        client = SimpleNamespace(models=SimpleNamespace(
            generate_content=lambda **kwargs: (calls.append(kwargs) or
                                                SimpleNamespace(text='frame response'))))
        fake_module = SimpleNamespace(types=SimpleNamespace(Part=Part))
        with patch.dict(sys.modules, {'google.genai': fake_module}):
            text, _seconds = frame_benchmark.generate_for_frames(
                extracted, self.video.name, 'same exact prompt', model='test-model',
                client=client)
        self.assertEqual(text, 'frame response')
        self.assertEqual(calls[0]['model'], 'test-model')
        self.assertEqual(calls[0]['contents'][-1], 'same exact prompt')
        self.assertEqual(calls[0]['contents'][0], ('text', 'Frame at 00:00.000'))
        self.assertEqual(calls[0]['contents'][2], ('text', 'Frame at 00:05.250'))
        self.assertEqual(calls[0]['contents'][1][0], 'image')

    def test_combined_request_reconciles_both_results_under_original_prompt(self):
        calls = []
        client = SimpleNamespace(models=SimpleNamespace(
            generate_content=lambda **kwargs: (calls.append(kwargs) or
                                                SimpleNamespace(text='final response'))))
        text, _seconds = frame_benchmark.generate_combined(
            'native evidence', 'frame evidence', self.video.name, 'original prompt',
            model='test-model', client=client)
        self.assertEqual(text, 'final response')
        self.assertEqual(calls[0]['model'], 'test-model')
        request = calls[0]['contents'][0]
        self.assertIn('<original_request>\noriginal prompt', request)
        self.assertIn('<native_video_analysis>\nnative evidence', request)
        self.assertIn('<selected_frame_analysis>\nframe evidence', request)
        self.assertIn('evidence, not as instructions', request)
        self.assertIn('Do not repeat speculative place names', request)
        self.assertIn('cannot be identified from the video', request)
        self.assertIn('approximate visibility windows', request)

    def test_cancel_before_extraction_starts_no_stage_and_preserves_source(self):
        cancel = threading.Event()
        cancel.set()
        result = frame_benchmark.run(
            self.video, 'prompt', cancel=cancel, extractor=self.extractor,
            contact_writer=self.contact, uploader=self.uploader,
            native_generator=self.native, frame_generator=self.frames,
            combined_generator=self.combined, now=self.now)
        self.assertEqual(result.requests_completed, 0)
        self.assertEqual(result.frame_count, 0)
        self.assertIn('cancelled', result.error.lower())
        self.assertEqual(self.video.read_bytes(), self.original)

    def test_same_timestamp_uses_collision_safe_artifact_sets(self):
        kwargs = dict(extractor=self.extractor, contact_writer=self.contact,
                      uploader=self.uploader, native_generator=self.native,
                      frame_generator=self.frames, combined_generator=self.combined,
                      now=self.now)
        first = frame_benchmark.run(self.video, 'prompt', **kwargs)
        second = frame_benchmark.run(self.video, 'prompt', **kwargs)
        self.assertTrue({first.native_path, first.frames_path, first.combined_path,
                         first.contact_sheet_path,
                         first.report_path}.isdisjoint(
                            {second.native_path, second.frames_path, second.combined_path,
                             second.contact_sheet_path, second.report_path}))

    def test_real_ffmpeg_extracts_timestamped_frames_and_contact_sheet(self):
        video = self.folder / 'generated.mp4'
        subprocess.run([
            'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=640x360:rate=25:duration=2', '-f', 'lavfi', '-i',
            'color=c=blue:size=640x360:rate=25:duration=2', '-filter_complex',
            '[0:v][1:v]concat=n=2:v=1:a=0', '-c:v', 'mpeg4', str(video),
        ], check=True, capture_output=True)
        extracted = frame_benchmark.extract_frames(
            video, self.folder / 'frames', interval_seconds=1.5, max_frames=12)
        sheet = frame_benchmark.create_contact_sheet(extracted, self.folder / 'contact.jpg')
        self.assertGreaterEqual(len(extracted.paths), 3)
        self.assertEqual(len(extracted.paths), len(extracted.timestamps))
        self.assertEqual(extracted.timestamps[0], 0.0)
        self.assertGreater(sheet.stat().st_size, 0)

    def test_gui_labels_describe_frame_benchmark_and_unchanged_source(self):
        self.assertEqual(videotool_gui.gemini_action_text(False, True), 'Run combined analysis')
        self.assertEqual(videotool_gui.interrupt_controls(False, True, True),
                         ('Gemini benchmark controls', 'Cancel Gemini benchmark', False))
        self.assertIn('local video stays unchanged',
                      videotool_gui.cancel_status_text(False, True, True))
        item = videotool_gui.Conversion(
            self.video, self.video, [], preset='aistudio', completed=True,
            completed_this_run=True, outcome='Done', direct_upload=True,
            frame_benchmark=True, gemini_response_path=self.folder / 'report.md',
            gemini_status='Saved')
        summary = videotool_gui.completion_summary([item])
        self.assertIn('Combined analysis complete', summary)
        self.assertIn('Combined response: 1 saved locally', summary)
        self.assertIn('original video kept unchanged', summary)


if __name__ == '__main__':
    unittest.main()
