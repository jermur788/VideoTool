import contextlib
import io
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

import videotool


class VideoToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / "source clip.mp4"
        self.source.write_bytes(b"unchanged dummy source")

    def test_source_errors(self):
        for source in (self.folder / "missing", self.folder):
            with self.subTest(source=source), self.assertRaises(videotool.VideoToolError):
                videotool.validate_source(source)

    def test_output_safety(self):
        existing = self.folder / "existing.mp4"
        existing.write_bytes(b"keep")
        alias = self.folder / "alias.mp4"
        alias.hardlink_to(self.source)
        link = self.folder / "link.mp4"
        link.symlink_to(self.source)
        dangling = self.folder / "dangling.mp4"
        dangling.symlink_to(self.folder / "absent")
        for target in (self.source, existing, alias, link, dangling,
                       self.folder / "missing" / "out.mp4"):
            with self.subTest(target=target), self.assertRaises(videotool.VideoToolError):
                videotool.validate_output(self.source, target)
        output = self.folder / "new.mp4"
        self.assertEqual(videotool.validate_output(self.source, output), output)
        self.assertFalse(output.exists())
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(self.source.read_bytes(), b"unchanged dummy source")

    @patch("videotool.shutil.which", return_value="/usr/bin/ffprobe")
    @patch("videotool.subprocess.run")
    def test_inspection_json_and_safe_invocation(self, run, which):
        metadata = {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(metadata), "")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(videotool.main(["inspect", str(self.source), "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue()), metadata)
        self.assertEqual(run.call_args.args[0][-1], str(self.source))
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(self.source.read_bytes(), b"unchanged dummy source")

    @patch("videotool.shutil.which", return_value=None)
    def test_missing_dependency(self, which):
        with self.assertRaisesRegex(videotool.VideoToolError, "not found"):
            videotool.inspect_video(self.source)

    @patch("videotool.shutil.which", return_value="ffprobe")
    @patch("videotool.subprocess.run")
    def test_probe_failures(self, run, which):
        for result in (
            subprocess.CompletedProcess([], 1, "", "Invalid data"),
            subprocess.CompletedProcess([], 0, "not JSON", ""),
            subprocess.CompletedProcess([], 0, "[]", ""),
            subprocess.CompletedProcess([], 0, '{"streams": []}', ""),
            subprocess.CompletedProcess([], 0, '{"streams": [{"codec_type": "video", "disposition": {"attached_pic": 1}}]}', ""),
        ):
            run.return_value = result
            with self.subTest(result=result), self.assertRaises(videotool.VideoToolError):
                videotool.inspect_video(self.source)
        run.side_effect = subprocess.TimeoutExpired("ffprobe", 60)
        with self.assertRaisesRegex(videotool.VideoToolError, "timed out"):
            videotool.inspect_video(self.source)

    def test_cli_error(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(videotool.main(["inspect", str(self.folder / "missing")]), 1)
        self.assertIn("Error:", errors.getvalue())

    def test_frame_rate(self):
        self.assertEqual(videotool.frame_rate("30000/1001"), "29.970")
        for value in (None, "0/0", "bad", "0/1"):
            self.assertEqual(videotool.frame_rate(value), "unknown")

    def metadata(self):
        return {"streams": [
            {"index": 0, "codec_type": "video", "width": 3840, "height": 2160,
             "avg_frame_rate": "25/1", "pix_fmt": "yuv420p", "color_space": "bt709",
             "disposition": {"default": 1}},
            {"index": 1, "codec_type": "audio"},
            {"index": 2, "codec_type": "data"},
            {"index": 5, "codec_type": "video", "disposition": {"attached_pic": 1}},
        ]}

    @patch("videotool.shutil.which", return_value="ffmpeg")
    def test_conversion_plan_and_selection(self, which):
        output = self.folder / "new.mov"
        metadata = self.metadata()
        command = videotool.plan_davinci(self.source, output, metadata)
        self.assertEqual([command[i + 1] for i, arg in enumerate(command) if arg == "-map"], ["0:0", "0:1"])
        self.assertIn("-n", command)
        self.assertNotIn("-y", command)
        self.assertIn("dnxhr_sq", command)
        self.assertIn("yuv422p", command)
        self.assertIn("pcm_s16le", command)
        self.assertNotIn("-r", command)
        self.assertNotIn("-vf", command)
        self.assertFalse(output.exists())
        metadata['streams'].append({"index": 6, "codec_type": "video"})
        self.assertEqual(videotool.select_video(metadata)['index'], 0)
        metadata['streams'][0]['disposition'] = {}
        with self.assertRaises(videotool.VideoToolError):
            videotool.select_video(metadata)

    @patch("videotool.shutil.which", return_value="ffmpeg")
    def test_automatic_format_preserves_source_bit_depth(self, which):
        eight_bit = self.metadata()
        ten_bit = self.metadata()
        ten_bit['streams'][0].update(pix_fmt='yuv420p10le', bits_per_raw_sample='10')
        eight_command = videotool.plan_davinci(
            self.source, self.folder / 'eight.mov', eight_bit)
        ten_command = videotool.plan_davinci(
            self.source, self.folder / 'ten.mov', ten_bit)
        self.assertIn('dnxhr_sq', eight_command)
        self.assertIn('yuv422p', eight_command)
        self.assertIn('dnxhr_hqx', ten_command)
        self.assertIn('yuv422p10le', ten_command)
        self.assertEqual(videotool.source_bit_depth(eight_bit), 8)
        self.assertEqual(videotool.source_bit_depth(ten_bit), 10)

    def test_automatic_format_refuses_unknown_or_unsupported_depth(self):
        for pixel_format, reported in [('unknown', None), ('yuv420p12le', '12')]:
            metadata = self.metadata()
            metadata['streams'][0]['pix_fmt'] = pixel_format
            metadata['streams'][0]['bits_per_raw_sample'] = reported
            with self.subTest(pixel_format=pixel_format), \
                    self.assertRaisesRegex(videotool.VideoToolError, 'Cannot safely choose'):
                videotool.davinci_settings_for_source(metadata)

    @patch("videotool.shutil.which", return_value="ffmpeg")
    def test_conversion_errors_and_silent_source(self, which):
        with self.assertRaises(videotool.VideoToolError):
            videotool.plan_davinci(self.source, self.folder / "bad.mp4", self.metadata())
        metadata = self.metadata()
        metadata['streams'] = [metadata['streams'][0]]
        command = videotool.plan_davinci(self.source, self.folder / "new.mov", metadata)
        self.assertNotIn("-c:a", command)
        metadata['streams'][0]['width'] = 123
        with self.assertRaises(videotool.VideoToolError):
            videotool.plan_davinci(self.source, self.folder / "new.mov", metadata)
        which.return_value = None
        with self.assertRaisesRegex(videotool.VideoToolError, "ffmpeg was not found"):
            videotool.plan_davinci(self.source, self.folder / "new.mov", self.metadata())

    @patch("videotool.inspect_video")
    @patch("videotool.shutil.which", return_value="ffmpeg")
    @patch("videotool.subprocess.run")
    def test_preview_never_executes(self, run, which, inspect):
        inspect.return_value = self.metadata()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(videotool.main(["davinci", str(self.source)]), 0)
        run.assert_not_called()
        self.assertFalse(self.source.with_name("source clip_davinci.mov").exists())

    @patch('videotool.inspect_video')
    @patch('videotool.shutil.which', return_value='ffmpeg')
    @patch('videotool.execute_davinci')
    def test_cli_execution_writes_processing_receipt(self, execute, which, inspect):
        source_metadata = self.metadata()
        source_metadata['format'] = {'duration': '10', 'size': '1000'}
        output_metadata = {'format': {'duration': '10', 'size': '2000'}, 'streams': [
            {'index': 0, 'codec_type': 'video', 'codec_name': 'dnxhd',
             'profile': 'DNXHR SQ', 'pix_fmt': 'yuv422p', 'width': 3840,
             'height': 2160, 'avg_frame_rate': '25/1'}]}
        inspect.side_effect = [source_metadata, output_metadata]
        output = self.folder / 'cli.mov'
        execute.side_effect = lambda source, target, command, **kwargs: target.write_bytes(b'output')
        with patch('processing_receipts.ffmpeg_version', return_value='ffmpeg test version'), \
                contextlib.redirect_stdout(io.StringIO()):
            result = videotool.main(['davinci', str(self.source), '--output', str(output), '--execute'])
        self.assertEqual(result, 0)
        receipts = list(self.folder.glob('VideoTool-processing-*.md'))
        self.assertEqual(len(receipts), 1)
        text = receipts[0].read_text(encoding='utf-8')
        self.assertIn('Batch elapsed time:', text)
        self.assertIn('FFmpeg: ffmpeg test version', text)
        self.assertIn(str(output), text)

    @patch("videotool.subprocess.run")
    def test_execution_rechecks_and_reports_failure(self, run):
        output = self.folder / "new.mov"
        output.write_bytes(b"keep")
        with self.assertRaises(videotool.VideoToolError):
            videotool.execute_davinci(self.source, output, ["ffmpeg"])
        run.assert_not_called()
        self.assertEqual(output.read_bytes(), b"keep")
        run.return_value = subprocess.CompletedProcess([], 1)
        with self.assertRaisesRegex(videotool.VideoToolError, "partial output"):
            videotool.execute_davinci(self.source, self.folder / "other.mov", ["ffmpeg"])
        run.return_value = subprocess.CompletedProcess([], 0, stderr='File already exists. Exiting.')
        with self.assertRaisesRegex(videotool.VideoToolError, "did not complete cleanly"):
            videotool.execute_davinci(self.source, self.folder / "other.mov", ["ffmpeg"])

    def test_progress_runner_terminates_active_process_on_cancel(self):
        class Process:
            def __init__(self):
                self.stdout = iter(())
                self.terminated = threading.Event()
                self.killed = False
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def poll(self):
                return 255 if self.terminated.is_set() else None
            def terminate(self):
                self.terminated.set()
            def kill(self):
                self.killed = True
                self.terminated.set()
            def wait(self, timeout=None):
                if not self.terminated.wait(timeout or 2):
                    raise subprocess.TimeoutExpired('ffmpeg', timeout)
                return 255
        process = Process()
        cancel = threading.Event()
        cancel.set()
        with patch('videotool.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(videotool.ConversionCancelled, 'partial output'):
                videotool.run_with_progress(['ffmpeg'], lambda _: None, cancel)
        self.assertTrue(process.terminated.is_set())
        self.assertFalse(process.killed)

    @patch("videotool.inspect_video")
    @patch("videotool.shutil.which", return_value="ffmpeg")
    @patch("videotool.execute_davinci")
    def test_batch_preview_selection_and_names(self, execute, which, inspect):
        inspect.return_value = self.metadata()
        for name in ('clip.MP4', 'clip.mov', 'old_davinci.mov', 'notes.txt'):
            (self.folder / name).write_bytes(b'keep')
        (self.folder / 'nested').mkdir()
        (self.folder / 'nested' / 'hidden.mp4').write_bytes(b'keep')
        (self.folder / 'alias.mp4').symlink_to(self.source)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(videotool.main(['batch', str(self.folder)]), 0)
        execute.assert_not_called()
        self.assertEqual(inspect.call_count, 3)
        self.assertIn('clip.MP4_davinci.mov', output.getvalue())
        self.assertIn('clip.mov_davinci.mov', output.getvalue())
        self.assertFalse((self.folder / 'clip.mov_davinci.mov').exists())

    @patch("videotool.inspect_video")
    @patch("videotool.shutil.which", return_value="ffmpeg")
    @patch("videotool.execute_davinci")
    def test_batch_failures_continue_and_existing_output_is_preserved(self, execute, which, inspect):
        inspect.return_value = self.metadata()
        for name in ('a.mp4', 'b.mp4'):
            (self.folder / name).write_bytes(b'keep')
        existing = self.folder / (self.source.name + '_davinci.mov')
        existing.write_bytes(b'previous result')
        execute.side_effect = [videotool.VideoToolError('failed conversion'), None]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(videotool.main(['batch', str(self.folder), '--execute']), 1)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(existing.read_bytes(), b'previous result')
        self.assertIn('1 succeeded, 2 failed, 0 not attempted', output.getvalue())

    @patch("videotool.inspect_video")
    @patch("videotool.shutil.which", return_value="ffmpeg")
    @patch("videotool.execute_davinci")
    def test_batch_interrupt_stops_and_output_folder(self, execute, which, inspect):
        inspect.return_value = self.metadata()
        (self.folder / 'second.mp4').write_bytes(b'keep')
        destination = self.folder / 'outputs'
        destination.mkdir()
        error = videotool.VideoToolError('interrupted')
        error.__cause__ = KeyboardInterrupt()
        execute.side_effect = error
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(videotool.main(['batch', str(self.folder), '--output-dir',
                                            str(destination), '--execute']), 130)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.args[1].parent, destination)
        self.assertIn('0 succeeded, 1 failed, 1 not attempted', output.getvalue())

    def test_batch_invalid_and_empty_folders(self):
        empty = self.folder / 'empty'
        empty.mkdir()
        for folder in (empty, self.source, self.folder / 'missing'):
            with self.subTest(folder=folder), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(videotool.main(['batch', str(folder)]), 1)

    @patch('videotool.inspect_video')
    def test_working_reference_reproduces_profile_and_pcm_depth(self, inspect):
        reference = self.folder / 'known-good.mov'
        reference.write_bytes(b'reference')
        inspect.return_value = {
            'format': {'duration': '10', 'size': '100000000'},
            'streams': [
                {'index': 0, 'codec_type': 'video', 'codec_name': 'dnxhd',
                 'profile': 'DNXHR HQ', 'pix_fmt': 'yuv422p', 'width': 3840,
                 'height': 2160, 'avg_frame_rate': '25/1'},
                {'index': 1, 'codec_type': 'audio', 'codec_name': 'pcm_s24le'},
            ],
        }
        settings = videotool.davinci_settings_from_reference(reference)
        self.assertEqual((settings['name'], settings['encoder_profile'], settings['audio_codec']),
                         ('DNxHR HQ', 'dnxhr_hq', 'pcm_s24le'))
        with patch('videotool.shutil.which', return_value='ffmpeg'):
            command = videotool.plan_davinci(self.source, self.folder / 'learned.mov',
                                             self.metadata(), settings)
        self.assertIn('dnxhr_hq', command)
        self.assertIn('pcm_s24le', command)

        inspect.return_value['streams'][0].update(profile='DNXHR LB', pix_fmt='yuv422p')
        settings = videotool.davinci_settings_from_reference(reference)
        self.assertEqual((settings['name'], settings['encoder_profile']), ('DNxHR LB', 'dnxhr_lb'))

    @patch('videotool.inspect_video')
    def test_reference_rejects_non_dnxhr_and_non_pcm(self, inspect):
        reference = self.folder / 'reference.mov'
        reference.write_bytes(b'reference')
        inspect.return_value = {'streams': [
            {'index': 0, 'codec_type': 'video', 'codec_name': 'h264',
             'profile': 'High', 'pix_fmt': 'yuv420p'},
        ]}
        with self.assertRaisesRegex(videotool.VideoToolError, 'DNxHR'):
            videotool.davinci_settings_from_reference(reference)
        inspect.return_value = {'streams': [
            {'index': 0, 'codec_type': 'video', 'codec_name': 'dnxhd',
             'profile': 'DNXHR SQ', 'pix_fmt': 'yuv422p'},
            {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac'},
        ]}
        with self.assertRaisesRegex(videotool.VideoToolError, 'PCM'):
            videotool.davinci_settings_from_reference(reference)

    def test_storage_estimate_and_insufficient_space(self):
        metadata = self.metadata()
        metadata['format'] = {'duration': '60'}
        metadata['streams'][0]['avg_frame_rate'] = '25/1'
        estimate = videotool.estimate_davinci_size(metadata)
        self.assertGreater(estimate, 0)
        with patch('videotool.shutil.disk_usage') as usage:
            usage.return_value.free = estimate - 1
            with self.assertRaisesRegex(videotool.VideoToolError, 'exceeds available space'):
                videotool.require_space(self.folder / 'output.mov', estimate)
            usage.return_value.free = estimate
            self.assertEqual(videotool.require_space(self.folder / 'output.mov', estimate), estimate)

    def test_reference_rate_drives_estimate(self):
        metadata = self.metadata()
        metadata['format'] = {'duration': '20'}
        settings = videotool.default_davinci_settings()
        settings.update(reference_rate=80_000_000, reference_width=3840,
                        reference_height=2160, reference_fps=25)
        # Same dimensions/frame rate: 80 Mb/s for 20 seconds, plus 10% headroom.
        self.assertAlmostEqual(videotool.estimate_davinci_size(metadata, settings),
                               220_000_000, delta=1)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_synthetic_conversion_and_ffmpeg_overwrite_refusal(self):
        # Only generated test media in the temporary test directory is processed.
        source = self.folder / 'synthetic.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n',
                        '-f', 'lavfi', '-i', 'color=size=256x144:rate=25:duration=0.12',
                        '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=0.12',
                        '-c:v', 'ffv1', '-pix_fmt', 'yuv420p10le', '-c:a', 'pcm_s16le', str(source)],
                       check=True, capture_output=True)
        original = source.read_bytes()
        output = self.folder / 'synthetic_davinci.mov'
        command = videotool.plan_davinci(source, output, videotool.inspect_video(source))
        subprocess.run(command, check=True, capture_output=True)
        streams = videotool.inspect_video(output)['streams']
        video, audio = streams
        self.assertEqual((video['codec_name'], video['profile'], video['pix_fmt']),
                         ('dnxhd', 'DNXHR HQX', 'yuv422p10le'))
        self.assertEqual((video['width'], video['height'], video['avg_frame_rate']), (256, 144, '25/1'))
        self.assertEqual((audio['codec_name'], audio['sample_rate']), ('pcm_s16le', '48000'))
        before = output.read_bytes()
        refused = subprocess.run(command, capture_output=True)
        self.assertIn(b'already exists', refused.stderr)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
