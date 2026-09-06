import copy
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import videotool
import videotool_gui as gui
import upload_presets as upload


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'finished_davinci.mov'
        self.source.write_bytes(b'original export')
        self.metadata = {'format': {'duration': '400'}, 'streams': [
            {'index': 0, 'codec_type': 'video', 'width': 1920, 'height': 1080,
             'avg_frame_rate': '25/1', 'field_order': 'progressive', 'color_transfer': 'bt709',
             'color_primaries': 'bt709', 'color_space': 'bt709'},
            {'index': 1, 'codec_type': 'audio', 'channels': 2}]}

    def plan(self, preset='aistudio', target=380, metadata=None):
        with patch('videotool.shutil.which', return_value='ffmpeg'):
            return upload.plan(self.source, self.folder / 'prepared.mp4',
                               metadata or self.metadata, preset, target)

    def test_default_size_target_and_longer_than_five_minutes(self):
        result = self.plan()
        self.assertEqual(result.max_bytes, 380_000_000)
        self.assertEqual(result.duration, 400)
        self.assertTrue(result.first_pass)
        self.assertIn('not a verified AI Studio limit', result.detail)
        self.assertFalse(result.output.exists())
        self.assertNotIn('-t', result.command)
        self.assertNotIn('-fs', result.command)

    def test_too_small_target_is_blocked_before_encoding(self):
        with self.assertRaisesRegex(videotool.VideoToolError, 'too small'):
            self.plan(target=1)
        self.assertEqual(self.source.read_bytes(), b'original export')
        self.assertEqual(list(self.folder.iterdir()), [self.source])

    def test_invalid_targets(self):
        for target in ('NaN', 'inf', '', 'bad', 0, -1, 100001):
            with self.subTest(target=target), self.assertRaises(videotool.VideoToolError):
                self.plan(target=target)
        self.assertEqual(self.plan(target='380').max_bytes, 380_000_000)

    def test_ai_downscales_to_preserve_bitrate_floor(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['format']['duration'] = '60'
        result = self.plan(target=20, metadata=metadata)
        self.assertEqual((result.width, result.height), (1280, 720))
        self.assertIn('1280 × 720', result.detail)

    def test_youtube_quality_settings_and_no_size_cap(self):
        result = self.plan(preset='youtube', target='ignored')
        self.assertIsNone(result.max_bytes)
        self.assertFalse(result.first_pass)
        command = result.command
        for flag, value in [('-crf', '18'), ('-profile:v', 'high'), ('-pix_fmt', 'yuv420p'),
                            ('-c:a', 'aac'), ('-ar', '48000'), ('-movflags', '+faststart'),
                            ('-use_editlist', '0'), ('-bf', '2'), ('-ac', '2')]:
            self.assertEqual(command[command.index(flag) + 1], value)
        self.assertEqual((result.width, result.height), (1920, 1080))
        self.assertIn('-n', command)
        self.assertNotIn('-y', command)

    def test_hdr_interlace_rotation_and_ambiguous_audio_are_explicit(self):
        changes = [('color_transfer', 'smpte2084'), ('color_transfer', 'arib-std-b67'),
                   ('field_order', 'tt'), ('sample_aspect_ratio', '4:3'),
                   ('side_data_list', [{'rotation': 90}])]
        for key, value in changes:
            metadata = copy.deepcopy(self.metadata)
            metadata['streams'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(videotool.VideoToolError):
                self.plan(metadata=metadata)
        metadata = copy.deepcopy(self.metadata)
        metadata['streams'].append({'index': 2, 'codec_type': 'audio'})
        with self.assertRaisesRegex(videotool.VideoToolError, 'one mixed audio'):
            self.plan(metadata=metadata)
        metadata['streams'][1]['disposition'] = {'default': 1}
        self.assertIn('Only default audio stream 1', self.plan(metadata=metadata).detail)

    def test_silent_export_supported(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['streams'] = metadata['streams'][:1]
        result = self.plan(metadata=metadata)
        self.assertFalse(result.audio)
        self.assertNotIn('-c:a', result.command)

    def test_oversize_is_retained_and_not_completed_or_saved(self):
        metadata = copy.deepcopy(self.metadata)
        metadata['format']['duration'] = '2'
        with patch('videotool.inspect_video', return_value=metadata):
            item = gui.plan_item(self.source, self.folder / 'result.mp4', preset='aistudio', target_mb=1)
        self.assertFalse(item.error)
        def run(command, callback, cancel=None):
            if command[-1] != '-':
                Path(command[-1]).write_bytes(b'x' * 1_000_001)
            callback(1)
            return subprocess.CompletedProcess(command, 0, stderr='')
        events = []
        with patch('videotool.run_with_progress', side_effect=run), patch('conversion_history.save') as save:
            self.assertEqual(gui.convert([item], threading.Event(), lambda *event: events.append(event)), (0, 1, 0))
        self.assertFalse(item.completed)
        save.assert_not_called()
        self.assertIn('above your 1 MB target', events[-1][2])
        self.assertEqual(item.output.stat().st_size, 1_000_001)
        self.assertEqual(self.source.read_bytes(), b'original export')

    def test_existing_output_and_creation_between_passes_refused(self):
        result = self.plan()
        def first(command, callback, cancel=None):
            result.output.write_bytes(b'created elsewhere')
            return subprocess.CompletedProcess(command, 0, stderr='')
        with patch('videotool.run_with_progress', side_effect=first) as run:
            with self.assertRaisesRegex(videotool.VideoToolError, 'already exists'):
                upload.execute(result, lambda _: None)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result.output.read_bytes(), b'created elsewhere')

    def test_cancel_between_ai_passes_does_not_start_second_pass(self):
        result = self.plan()
        cancel = threading.Event()
        def first(command, callback, cancel_event=None):
            self.assertIs(cancel_event, cancel)
            cancel.set()
            return subprocess.CompletedProcess(command, 0, stderr='')
        with patch('videotool.run_with_progress', side_effect=first) as run:
            with self.assertRaisesRegex(videotool.ConversionCancelled, 'No second pass'):
                upload.execute(result, lambda _: None, cancel)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(result.output.exists())

    def test_upload_folder_accepts_davinci_intermediates_and_retry_keeps_preset(self):
        with patch('videotool.inspect_video', return_value=self.metadata):
            items = gui.preview(self.folder, folder=True, preset='youtube', location='Dublin')
            self.assertEqual(len(items), 1)
            items[0].output.write_bytes(b'partial')
            retry = gui.retry_preview(items)
        self.assertEqual(retry[0].preset, 'youtube')
        self.assertEqual(retry[0].output.name, 'Dublin_002.mp4')
        self.assertIsNotNone(retry[0].upload_plan)
        self.assertEqual(items[0].output.read_bytes(), b'partial')

    @unittest.skipUnless(videotool.shutil.which('ffmpeg') and videotool.shutil.which('ffprobe'), 'FFmpeg required')
    def test_actual_both_presets_size_validation_and_scoped_history(self):
        self.source.unlink()
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=320x180:rate=25:duration=2', '-f', 'lavfi', '-i',
                        'sine=sample_rate=48000:duration=2', '-c:v', 'mpeg4', '-c:a',
                        'pcm_s16le', str(self.source)], check=True, capture_output=True)
        original = self.source.read_bytes()
        for preset in ('aistudio', 'youtube'):
            items = gui.preview(self.source, preset=preset, target_mb=1)
            self.assertFalse(items[0].error)
            updates = []
            self.assertEqual(gui.convert(items, threading.Event(), lambda *a: None,
                                         lambda *a: updates.append(a)), (1, 0, 0))
            self.assertEqual(updates[-1], (100, 0, 100, 0))
            output = items[0].output
            if preset == 'aistudio':
                self.assertLessEqual(output.stat().st_size, 1_000_000)
            data = output.read_bytes()
            self.assertLess(data.index(b'moov'), data.index(b'mdat'))
            self.assertTrue(gui.preview(self.source, preset=preset, target_mb=1)[0].completed)
        self.assertEqual(self.source.read_bytes(), original)
        # The old 1 MB receipt cannot mark a different target complete.
        self.assertFalse(gui.preview(self.source, preset='aistudio', target_mb=2)[0].completed)
        self.assertFalse(gui.preview(self.source, preset='davinci')[0].completed)
