import contextlib
import io
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
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
             "avg_frame_rate": "25/1", "color_space": "bt709", "disposition": {"default": 1}},
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
        self.assertIn("yuv422p10le", command)
        self.assertIn("pcm_s24le", command)
        self.assertNotIn("-r", command)
        self.assertNotIn("-vf", command)
        self.assertFalse(output.exists())
        metadata['streams'].append({"index": 6, "codec_type": "video"})
        self.assertEqual(videotool.select_video(metadata)['index'], 0)
        metadata['streams'][0]['disposition'] = {}
        with self.assertRaises(videotool.VideoToolError):
            videotool.select_video(metadata)

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
        self.assertEqual((audio['codec_name'], audio['sample_rate']), ('pcm_s24le', '48000'))
        before = output.read_bytes()
        refused = subprocess.run(command, capture_output=True)
        self.assertIn(b'already exists', refused.stderr)
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
