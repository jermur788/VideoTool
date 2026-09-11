import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import conversion_history
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
                                     'width': 256, 'height': 144, 'avg_frame_rate': '25/1',
                                     'pix_fmt': 'yuv420p'}]}

    def make_preview(self, **kwargs):
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            return gui.preview(self.source, **kwargs)

    def test_direct_gui_launch_switches_to_project_environment(self):
        expected_environment = Path(gui.__file__).resolve().parent / '.venv'
        expected_python = expected_environment / 'bin' / 'python'
        with patch.object(gui.Path, 'is_file', return_value=True), \
                patch.object(gui.sys, 'prefix', '/usr'), patch.object(gui.os, 'execv') as execute:
            gui.use_project_environment()
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0], str(expected_python))
        self.assertEqual(execute.call_args.args[1][0], str(expected_python))

    def test_project_environment_does_not_restart_itself(self):
        environment = Path(gui.__file__).resolve().parent / '.venv'
        with patch.object(gui.Path, 'is_file', return_value=True), \
                patch.object(gui.sys, 'prefix', str(environment)), patch.object(gui.os, 'execv') as execute:
            gui.use_project_environment()
        execute.assert_not_called()

    def test_preview_creates_no_output(self):
        with patch('videotool.subprocess.run') as run:
            items = self.make_preview()
        run.assert_not_called()
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].error)
        self.assertFalse(items[0].output.exists())
        self.assertEqual(items[0].output.name, 'clip_davinci.mov')

    def test_direct_gemini_preview_uses_original_without_conversion(self):
        with patch('videotool.inspect_video', return_value=self.metadata):
            item = gui.preview_direct_gemini(self.source)
        self.assertTrue(item.direct_upload)
        self.assertEqual(item.source, self.source)
        self.assertEqual(item.output, self.source)
        self.assertEqual(item.command, [])
        self.assertIn('No conversion will run', item.detail)

    def test_direct_gemini_preview_refuses_folder_and_unsupported_file(self):
        with self.assertRaisesRegex(Exception, 'one video file'):
            gui.preview_direct_gemini(self.folder, folder=True)
        unsupported = self.folder / 'notes.txt'
        unsupported.write_text('not a video')
        with self.assertRaisesRegex(Exception, 'does not support'):
            gui.preview_direct_gemini(unsupported)

    def test_location_names_continue_after_existing_numbers(self):
        (self.folder / 'second.mp4').write_bytes(b'second')
        existing = self.folder / 'Dublin_001.mov'
        existing.write_bytes(b'keep')
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            items = gui.preview(self.folder, folder=True, location=' Dublin ')
        self.assertEqual([item.source.name for item in items], ['clip.mp4', 'second.mp4'])
        self.assertEqual([item.output.name for item in items], ['Dublin_002.mov', 'Dublin_003.mov'])
        self.assertFalse(items[0].error)
        self.assertFalse(items[1].error)
        self.assertEqual(existing.read_bytes(), b'keep')
        self.assertFalse(items[1].output.exists())

    def folder_preview(self, location='Dublin'):
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            return gui.preview(self.folder, folder=True, location=location)

    def fake_success(self, source, output, command, callback):
        output.write_bytes(b'completed video')

    def test_saved_success_and_new_footage_after_reopen(self):
        first = self.folder_preview()
        with patch('videotool.execute_davinci', side_effect=self.fake_success):
            self.assertEqual(gui.convert(first, threading.Event(), lambda *args: None), (1, 0, 0))
        output_bytes = first[0].output.read_bytes()
        (self.folder / 'aaa-new.mp4').write_bytes(b'new footage sorts before old')
        with patch('videotool.subprocess.run') as run:
            later = self.folder_preview()
        run.assert_not_called()
        self.assertEqual([item.output.name for item in later], ['Dublin_002.mov', 'Dublin_001.mov'])
        self.assertTrue(later[1].completed)
        with patch('videotool.execute_davinci', side_effect=self.fake_success) as execute:
            self.assertEqual(gui.convert(later, threading.Event(), lambda *args: None), (1, 0, 0))
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(first[0].output.read_bytes(), output_bytes)

    def test_retry_partial_failure_preserves_success_and_unfinished_names(self):
        for name in ['second.mp4', 'third.mp4']:
            (self.folder / name).write_bytes(b'source')
        items = self.folder_preview()
        stop = threading.Event()
        def execute(source, output, command, callback):
            if source.name == 'second.mp4':
                output.write_bytes(b'partial')
                stop.set()
                raise videotool.VideoToolError('simulated failure')
            self.fake_success(source, output, command, callback)
        with patch('videotool.execute_davinci', side_effect=execute):
            self.assertEqual(gui.convert(items, stop, lambda *args: None), (1, 1, 1))
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            retry = gui.retry_preview(items)
        self.assertTrue(retry[0].completed)
        self.assertEqual(retry[1].output.name, 'Dublin_004.mov')
        self.assertEqual(retry[2].output.name, 'Dublin_003.mov')
        self.assertEqual(items[1].output.read_bytes(), b'partial')
        with patch('videotool.execute_davinci', side_effect=self.fake_success) as execute:
            self.assertEqual(gui.convert(retry, threading.Event(), lambda *args: None), (2, 0, 0))
        self.assertEqual(execute.call_count, 2)
        self.assertIn('3 completed · 0 failed/blocked · 0 unfinished', gui.completion_summary(retry))
        self.assertEqual(items[0].output.read_bytes(), b'completed video')
        self.assertEqual(items[1].output.read_bytes(), b'partial')

    def test_retry_default_partial_uses_unique_name_and_keeps_bytes(self):
        item = self.make_preview()[0]
        item.output.write_bytes(b'partial')
        with patch('videotool.inspect_video', return_value=self.metadata), patch('videotool.shutil.which', return_value='ffmpeg'):
            retried = gui.retry_preview([item])[0]
        self.assertFalse(retried.error)
        self.assertNotEqual(retried.output, item.output)
        self.assertTrue(retried.output.name.endswith('_davinci.mov'))
        self.assertFalse(retried.output.exists())
        self.assertEqual(item.output.read_bytes(), b'partial')

    def test_continuation_reserves_gaps_case_variants_and_links(self):
        (self.folder / 'DUBLIN_007.MOV').write_bytes(b'keep')
        (self.folder / 'Dublin_009.mov').symlink_to(self.folder / 'missing')
        items = self.folder_preview()
        self.assertEqual(items[0].output.name, 'Dublin_010.mov')
        items[0].output.write_bytes(b'appeared after preview')
        with patch('videotool.subprocess.Popen') as popen:
            self.assertEqual(gui.convert(items, threading.Event(), lambda *args: None), (0, 1, 0))
        popen.assert_not_called()
        self.assertEqual(items[0].output.read_bytes(), b'appeared after preview')

    def test_modified_output_is_not_treated_as_saved_success(self):
        items = self.folder_preview()
        with patch('videotool.execute_davinci', side_effect=self.fake_success):
            gui.convert(items, threading.Event(), lambda *args: None)
        items[0].output.write_bytes(b'changed output')
        later = self.folder_preview()
        self.assertFalse(later[0].completed)
        self.assertEqual(later[0].output.name, 'Dublin_002.mov')

    def test_history_write_failure_keeps_success_for_retry(self):
        items = self.folder_preview()
        with patch('videotool.execute_davinci', side_effect=self.fake_success), patch('conversion_history.save', side_effect=OSError('read only')):
            self.assertEqual(gui.convert(items, threading.Event(), lambda *args: None), (1, 0, 0))
        with patch('videotool.execute_davinci') as execute:
            self.assertEqual(gui.convert(gui.retry_preview(items), threading.Event(), lambda *args: None), (0, 0, 0))
        execute.assert_not_called()

    def test_success_writes_human_receipt_with_timing_and_history_link(self):
        metadata = {**self.metadata, 'format': {'duration': '10', 'size': '1000'},
                    'streams': [dict(self.metadata['streams'][0], codec_name='h264')]}
        output_metadata = {
            'format': {'duration': '10', 'size': '2000'},
            'streams': [
                {'index': 0, 'codec_type': 'video', 'codec_name': 'dnxhd',
                 'profile': 'DNXHR SQ', 'pix_fmt': 'yuv422p', 'width': 256,
                 'height': 144, 'avg_frame_rate': '25/1'},
                {'index': 1, 'codec_type': 'audio', 'codec_name': 'pcm_s16le',
                 'bits_per_sample': 16, 'sample_rate': '48000', 'channels': 2},
            ],
        }
        with patch('videotool.inspect_video', return_value=metadata), \
                patch('videotool.shutil.which', return_value='ffmpeg'):
            item = gui.preview(self.source)[0]
        receipts = []
        with patch('videotool.execute_davinci', side_effect=self.fake_success), \
                patch('videotool.inspect_video', return_value=output_metadata), \
                patch('processing_receipts.ffmpeg_version', return_value='ffmpeg test version'):
            result = gui.convert([item], threading.Event(), lambda *args: None,
                                 receipt_report=lambda *args: receipts.append(args))
        self.assertEqual(result, (1, 0, 0))
        receipt_path, summary, error = receipts[0]
        self.assertEqual(error, '')
        self.assertTrue(receipt_path.is_file())
        text = receipt_path.read_text(encoding='utf-8')
        for expected in ('# VideoTool processing receipt', 'DNXHR SQ', 'pcm_s16le',
                         'ffmpeg test version', 'Effective conversion speed:',
                         'Resolve compatibility has not been manually confirmed',
                         '-progress pipe:1', '- Attempted: 1', '- Completed: 1'):
            self.assertIn(expected, text)
        self.assertIn(str(receipt_path), summary)
        self.assertIsNotNone(item.elapsed_seconds)
        self.assertIsNotNone(item.batch_elapsed_seconds)
        saved = conversion_history.read(self.folder)[0]
        self.assertEqual(saved['processing_receipt'], receipt_path.name)
        self.assertIsNotNone(saved['elapsed_seconds'])
        with patch('videotool.inspect_video', return_value=metadata), \
                patch('videotool.shutil.which', return_value='ffmpeg'):
            reopened = gui.preview(self.source)[0]
        self.assertTrue(reopened.completed)
        self.assertEqual(reopened.receipt_path, receipt_path)

    def test_receipt_write_failure_does_not_change_video_success(self):
        item = self.make_preview()[0]
        reports = []
        with patch('videotool.execute_davinci', side_effect=self.fake_success), \
                patch('processing_receipts.write', side_effect=OSError('read only')):
            result = gui.convert([item], threading.Event(), lambda *args: None,
                                 receipt_report=lambda *args: reports.append(args))
        self.assertEqual(result, (1, 0, 0))
        self.assertTrue(item.completed)
        self.assertIsNone(reports[0][0])
        self.assertIn('could not be saved', reports[0][2])

    def test_completion_and_open_folder(self):
        items = [gui.Conversion(self.source, self.folder / 'a.mov', [], completed=True),
                 gui.Conversion(self.source, self.folder / 'b.mov', [], outcome='Failed'),
                 gui.Conversion(self.source, self.folder / 'c.mov', [], outcome='Not attempted')]
        self.assertIn('1 completed · 1 failed/blocked · 1 unfinished', gui.completion_summary(items))
        with patch('videotool_gui.subprocess.Popen') as popen, patch('videotool_gui.sys.platform', 'linux'):
            gui.open_output_folder(self.folder)
        self.assertEqual(popen.call_args.args[0], ['xdg-open', str(self.folder)])

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

    def test_review_output_size_is_clearly_approximate(self):
        item = gui.Conversion(self.source, self.folder / 'planned.mp4', [],
                              preset='youtube', estimated_bytes=12_300_000)
        self.assertEqual(gui.review_output_size(item), '~12.3 MB')
        item.direct_upload = True
        self.assertEqual(gui.review_output_size(item), 'No copy')

    def test_upload_preview_estimate_appears_in_details_and_batch_storage(self):
        metadata = {'format': {'duration': '60'}, 'streams': [
            {'index': 0, 'codec_type': 'video', 'width': 1920, 'height': 1080,
             'avg_frame_rate': '25/1', 'field_order': 'progressive',
             'color_transfer': 'bt709', 'color_primaries': 'bt709',
             'color_space': 'bt709'},
            {'index': 1, 'codec_type': 'audio', 'channels': 2},
        ]}
        with patch('videotool.inspect_video', return_value=metadata), \
                patch('videotool.shutil.which', return_value='ffmpeg'), \
                patch('videotool.shutil.disk_usage') as usage:
            usage.return_value.free = 2_000_000_000
            item = gui.preview(self.source, preset='youtube')[0]
        self.assertGreater(item.estimated_bytes, 0)
        self.assertIn('Approximate output:', item.detail)
        summary = gui.storage_summary([item])
        self.assertIn('Estimated batch output for all 1 ready file:', summary)
        self.assertIn('Approximate only', summary)

    def test_reference_settings_and_storage_are_shown(self):
        reference = self.folder / 'working.mov'
        reference.write_bytes(b'working')
        source_metadata = {**self.metadata, 'format': {'duration': '10'}}
        reference_metadata = {
            'format': {'duration': '10', 'size': '10000000'},
            'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'dnxhd',
                         'profile': 'DNXHR SQ', 'pix_fmt': 'yuv422p', 'width': 256,
                         'height': 144, 'avg_frame_rate': '25/1'},
                        {'index': 1, 'codec_type': 'audio', 'codec_name': 'pcm_s16le'}],
        }
        def inspect(path):
            return reference_metadata if Path(path) == reference else source_metadata
        with patch('videotool.inspect_video', side_effect=inspect), \
                patch('videotool.shutil.which', return_value='ffmpeg'), \
                patch('videotool.shutil.disk_usage') as usage:
            usage.return_value.free = 1_000_000_000
            item = gui.preview(self.source, reference=reference)[0]
        self.assertEqual(item.davinci_settings['name'], 'DNxHR SQ')
        self.assertIn('Learned from working reference', item.detail)
        self.assertIn('This file: estimated', item.detail)
        summary = gui.storage_summary([item])
        self.assertIn('Estimated batch output for all 1 ready file:', summary)
        self.assertIn('Destination free space:', summary)
        self.assertIn('Estimated space remaining after batch:', summary)

    def test_folder_storage_summary_is_explicit_about_total_and_shortfall(self):
        items = [gui.Conversion(self.source, self.folder / f'out-{number}.mov', [],
                                estimated_bytes=600, available_bytes=1000)
                 for number in range(2)]
        summary = gui.storage_summary(items)
        self.assertIn('Estimated batch output for all 2 ready files:', summary)
        self.assertIn('includes 10% headroom per file', summary)
        self.assertIn('Short by:', summary)
        self.assertIn('complete batch does not fit', summary)

    def test_make_output_folder_creates_one_safe_new_directory(self):
        created = gui.make_output_folder(self.folder, 'New exports')
        self.assertEqual(created, self.folder / 'New exports')
        self.assertTrue(created.is_dir())
        with self.assertRaises(FileExistsError):
            gui.make_output_folder(self.folder, 'New exports')
        for invalid in ('', '.', '..', 'nested/folder', 'bad:name'):
            with self.assertRaises(ValueError):
                gui.make_output_folder(self.folder, invalid)

    def test_automatic_preview_uses_hqx_for_10_bit_source(self):
        metadata = {**self.metadata, 'streams': [dict(self.metadata['streams'][0],
                                                     pix_fmt='yuv420p10le',
                                                     bits_per_raw_sample='10')]}
        with patch('videotool.inspect_video', return_value=metadata), \
                patch('videotool.shutil.which', return_value='ffmpeg'):
            item = gui.preview(self.source)[0]
        self.assertEqual(item.davinci_settings['name'], 'DNxHR HQX')
        self.assertIn('Automatic: source is 10-bit', item.detail)

    def test_automatic_folder_decides_each_source_separately(self):
        ten_bit_source = self.folder / 'ten-bit.mp4'
        ten_bit_source.write_bytes(b'original')
        ten_bit_metadata = {**self.metadata, 'streams': [dict(self.metadata['streams'][0],
                                                             pix_fmt='yuv420p10le')]}
        def inspect(path):
            return ten_bit_metadata if Path(path) == ten_bit_source else self.metadata
        with patch('videotool.inspect_video', side_effect=inspect), \
                patch('videotool.shutil.which', return_value='ffmpeg'):
            items = gui.preview(self.folder, folder=True)
        self.assertEqual({item.source.name: item.davinci_settings['name'] for item in items},
                         {'clip.mp4': 'DNxHR SQ', 'ten-bit.mp4': 'DNxHR HQX'})

    def test_saved_success_is_scoped_to_davinci_format(self):
        first = self.make_preview()
        with patch('videotool.execute_davinci', side_effect=self.fake_success):
            gui.convert(first, threading.Event(), lambda *args: None)
        reference = self.folder / 'hq-reference.mov'
        reference.write_bytes(b'reference')
        hq_metadata = {
            'format': {'duration': '10', 'size': '10000000'},
            'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'dnxhd',
                         'profile': 'DNXHR HQ', 'pix_fmt': 'yuv422p', 'width': 256,
                         'height': 144, 'avg_frame_rate': '25/1'}],
        }
        def inspect(path):
            return hq_metadata if Path(path) == reference else self.metadata
        with patch('videotool.inspect_video', side_effect=inspect), \
                patch('videotool.shutil.which', return_value='ffmpeg'):
            learned = gui.preview(self.source, reference=reference)
        self.assertFalse(learned[0].completed)
        self.assertIn('already exists', learned[0].error)

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

    def test_cancel_current_preserves_partial_and_stops_batch(self):
        (self.folder / 'second.mp4').write_bytes(b'second')
        with patch('videotool.inspect_video', return_value=self.metadata), \
                patch('videotool.shutil.which', return_value='ffmpeg'):
            items = gui.preview(self.folder, folder=True)
        cancel = threading.Event()
        events = []
        def execute(source, output, command, callback, cancel=None):
            output.write_bytes(b'partial retained')
            cancel.set()
            raise videotool.ConversionCancelled('Conversion cancelled; partial output preserved.')
        with patch('videotool.execute_davinci', side_effect=execute) as run, \
                patch('conversion_history.save') as save:
            result = gui.convert(items, threading.Event(), lambda *args: events.append(args),
                                 cancel=cancel)
        self.assertEqual(result, (0, 0, 2))
        self.assertEqual(run.call_count, 1)
        save.assert_not_called()
        self.assertEqual(items[0].outcome, 'Interrupted')
        self.assertEqual(items[1].outcome, 'Not attempted')
        self.assertEqual(items[0].output.read_bytes(), b'partial retained')
        self.assertEqual([event[1] for event in events],
                         ['Converting', 'Interrupted', 'Not attempted'])

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
        self.assertEqual(videotool.inspect_video(items[0].output)['streams'][0]['profile'], 'DNXHR SQ')
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
