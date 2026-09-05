import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import videotool
import videotool_gui as gui


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'clip.mp4'
        self.source.write_bytes(b'original')
        self.metadata = {'streams': [{'index': 0, 'codec_type': 'video',
                                     'width': 256, 'height': 144, 'avg_frame_rate': '25/1'}]}

    def make_preview(self, **kwargs):
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            return gui.preview(self.source, **kwargs)

    def test_preview_creates_no_output(self):
        with patch('videotool.subprocess.run') as run:
            items = self.make_preview()
        run.assert_not_called()
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].error)
        self.assertFalse(items[0].output.exists())
        self.assertEqual(items[0].output.name, 'clip_davinci.mov')

    def test_location_names_are_sequential_and_keep_blocked_numbers(self):
        (self.folder / 'second.mp4').write_bytes(b'second')
        existing = self.folder / 'Dublin_001.mov'
        existing.write_bytes(b'keep')
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            items = gui.preview(self.folder, folder=True, location=' Dublin ')
        self.assertEqual([item.source.name for item in items], ['clip.mp4', 'second.mp4'])
        self.assertEqual([item.output.name for item in items], ['Dublin_001.mov', 'Dublin_002.mov'])
        self.assertTrue(items[0].error)
        self.assertFalse(items[1].error)
        self.assertEqual(existing.read_bytes(), b'keep')
        self.assertFalse(items[1].output.exists())

    def test_location_unicode_spaces_and_invalid_paths(self):
        item = self.make_preview(location='Dún Laoghaire')[0]
        self.assertEqual(item.output.name, 'Dún Laoghaire_001.mov')
        for location in ('../escape', 'bad/name', 'bad\\name', '..', 'bad:name', 'bad\nname'):
            with self.subTest(location=location), self.assertRaises(videotool.VideoToolError):
                self.make_preview(location=location)

    def test_location_change_and_blank_restore_names(self):
        self.assertEqual(self.make_preview(location='Cork')[0].output.name, 'Cork_001.mov')
        self.assertEqual(self.make_preview(location='Galway')[0].output.name, 'Galway_001.mov')
        self.assertEqual(self.make_preview(location='  ')[0].output.name, 'clip_davinci.mov')

    def test_progress_estimates_and_unknown_duration(self):
        self.assertEqual(gui.estimate(25, 100, 10), (25, 30))
        self.assertEqual(gui.estimate(100, 100, 10), (99, 0))
        self.assertEqual(gui.estimate(100, 100, 10, complete=True), (100, 0))
        self.assertEqual(gui.estimate(0, 0, 10), (None, None))
        self.assertEqual(gui.estimate(1, 100, 1), (1, None))
        for value in ('NaN', 'inf', '-1', None, 'bad'):
            self.assertEqual(gui.duration_seconds({'format': {'duration': value}}), 0)

    def test_batch_progress_is_duration_weighted(self):
        first = self.make_preview()[0]
        second = self.make_preview()[0]
        first.duration, second.duration = 10, 30
        updates = []
        durations = iter([10, 30])
        def execute(source, output, command, callback):
            callback(next(durations) / 2)
        with patch('videotool.execute_davinci', side_effect=execute):
            result = gui.convert([first, second], threading.Event(), lambda *args: None,
                                 lambda *args: updates.append(args))
        self.assertEqual(result, (2, 0, 0))
        self.assertEqual([event[2] for event in updates], [0, 12.5, 25, 25, 62.5, 100])
        self.assertEqual(updates[-1], (100, 0, 100, 0))

    def test_changed_source_and_existing_output_are_blocked(self):
        items = self.make_preview()
        self.source.write_bytes(b'changed after preview')
        events = []
        with patch('videotool.execute_davinci') as execute:
            result = gui.convert(items, threading.Event(), lambda *args: events.append(args))
        execute.assert_not_called()
        self.assertEqual(result, (0, 1, 0))
        self.assertIn('changed since preview', events[-1][2])
        items = self.make_preview()
        items[0].output.write_bytes(b'existing output')
        with patch('videotool.subprocess.run') as run:
            self.assertEqual(gui.convert(items, threading.Event(), lambda *args: None), (0, 1, 0))
        run.assert_not_called()
        self.assertEqual(items[0].output.read_bytes(), b'existing output')

    def test_stop_after_current_and_snapshot(self):
        (self.folder / 'second.mp4').write_bytes(b'second')
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            items = gui.preview(self.folder, folder=True)
        (self.folder / 'late.mp4').write_bytes(b'added after preview')
        stop = threading.Event()
        with patch('videotool.execute_davinci', side_effect=lambda *args: stop.set()) as execute:
            result = gui.convert(items, stop, lambda *args: None)
        self.assertEqual(result, (1, 0, 1))
        self.assertEqual(execute.call_count, 1)

    def test_failure_continues_and_blocked_counts(self):
        item = self.make_preview()[0]
        blocked = gui.Conversion(self.source, self.folder / 'existing.mov', [], error='Existing output')
        with patch('videotool.execute_davinci', side_effect=[videotool.VideoToolError('failed'), None]):
            result = gui.convert([item, item, blocked], threading.Event(), lambda *args: None)
        self.assertEqual(result, (1, 2, 0))

    @unittest.skipUnless(videotool.shutil.which('ffmpeg') and videotool.shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_preview_and_conversion(self):
        self.source.unlink()
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=size=256x144:rate=25:duration=0.12', '-c:v', 'mpeg4',
                        str(self.source)], check=True, capture_output=True)
        original = self.source.read_bytes()
        items = gui.preview(self.source)
        self.assertFalse(items[0].error)
        updates = []
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gui.convert(items, threading.Event(), lambda *args: None,
                                         lambda *args: updates.append(args)), (1, 0, 0))
        self.assertGreater(len(updates), 2)
        self.assertEqual(updates[-1], (100, 0, 100, 0))
        self.assertEqual(videotool.inspect_video(items[0].output)['streams'][0]['profile'], 'DNXHR HQX')
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
