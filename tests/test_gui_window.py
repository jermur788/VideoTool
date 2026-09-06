"""Opt-in desktop workflow check: VIDEOTOOL_GUI_TESTS=1 python3 -m unittest discover -s tests."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import videotool
import videotool_gui as gui


@unittest.skipUnless(os.environ.get('VIDEOTOOL_GUI_TESTS') == '1', 'Desktop check is opt-in')
class DesktopWorkflowTest(unittest.TestCase):
    def test_native_drop_selects_a_video_when_tkdnd_is_available(self):
        import tkinter as tk
        from tkinter import ttk
        original_tk = tk.Tk
        probe = original_tk()
        try:
            probe.tk.call('package', 'require', 'tkdnd')
        except tk.TclError:
            probe.destroy()
            self.skipTest('TkDND is not installed')
        probe.destroy()
        errors, finished = [], []
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / 'dropped.mp4'
            clip.write_bytes(b'not inspected during source selection')
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            def create_root():
                root = original_tk()
                def drop():
                    try:
                        binding = root.tk.call('bind', root._w, '<<Drop:DND_Files>>')
                        self.assertIn('%D', binding)
                        root.tk.call(binding.split()[0], str(clip))
                        root.update_idletasks()
                        source = next(w for w in descendants(root) if isinstance(w, ttk.Entry) and
                                      w.get().startswith('Video: '))
                        self.assertEqual(source.get(), f'Video: {clip}')
                        finished.append(True)
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        root.destroy()
                root.after(100, drop)
                return root
            with patch('tkinter.Tk', create_root):
                gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)

    def test_retry_continue_and_open_output(self):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        original_tk = tk.Tk
        original_execute = videotool.execute_davinci
        errors = []
        opened = []
        opened_receipts = []
        finished = []
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clip = folder / 'a.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=size=256x144:rate=25:duration=0.12', '-c:v', 'mpeg4', str(clip)],
                           check=True, capture_output=True)
            (folder / 'b.mp4').write_bytes(clip.read_bytes())
            def execute(source, output, command, callback, cancel=None):
                if output.name == 'Dublin_002.mov':
                    output.write_bytes(b'partial retained')
                    raise videotool.VideoToolError('Simulated failure for retry check')
                original_execute(source, output, command, callback)
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            def create_root():
                root = original_tk()
                state = {'stage': 0, 'polls': 0}
                def check():
                    try:
                        widgets = list(descendants(root))
                        buttons = {str(w.cget('text')): w for w in widgets
                                   if isinstance(w, (ttk.Button, ttk.Menubutton))}
                        tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
                        rows = tree.get_children()
                        stage = state['stage']
                        if stage == 0:
                            self.assertEqual(set(buttons), set(gui.BUTTON_HINTS))
                            for label, button in buttons.items():
                                self.assertEqual(button.tooltip.text, gui.BUTTON_HINTS[label])
                            root.nametowidget(str(buttons['Choose source'].cget('menu'))).invoke(1)
                            entry = next(w for w in widgets if isinstance(w, ttk.Entry) and str(w.cget('state')) == 'normal')
                            entry.insert(0, 'Dublin')
                            buttons['Preview'].invoke()
                            state['stage'] = 1
                        elif stage == 1 and buttons['Convert ready files'].instate(['!disabled']):
                            self.assertEqual(len(rows), 2)
                            buttons['Convert ready files'].invoke()
                            state['stage'] = 2
                        elif stage == 2 and buttons['Preview retry'].instate(['!disabled']):
                            self.assertEqual([tree.set(row, 'status') for row in rows], ['Done', 'Failed'])
                            buttons['Preview retry'].invoke()
                            state['stage'] = 3
                        elif stage == 3 and buttons['Convert ready files'].instate(['!disabled']):
                            self.assertEqual([tree.set(row, 'output') for row in rows], ['Dublin_001.mov', 'Dublin_003.mov'])
                            self.assertEqual(tree.set('0', 'status'), 'Done')
                            buttons['Convert ready files'].invoke()
                            state['stage'] = 4
                        elif stage == 4 and buttons['Preview'].instate(['!disabled']):
                            self.assertEqual([tree.set(row, 'status') for row in rows], ['Done', 'Done'])
                            self.assertTrue(buttons['Preview retry'].instate(['disabled']))
                            self.assertTrue(buttons['View processing receipt'].instate(['!disabled']))
                            self.assertTrue(buttons['Copy summary'].instate(['!disabled']))
                            buttons['View processing receipt'].invoke()
                            buttons['Copy summary'].invoke()
                            self.assertIn('VideoTool run:', root.clipboard_get())
                            self.assertEqual(len(opened_receipts), 1)
                            buttons['Open output folder'].invoke()
                            self.assertEqual(opened, [folder])
                            self.assertEqual((folder / 'Dublin_002.mov').read_bytes(), b'partial retained')
                            (folder / 'c.mp4').write_bytes(clip.read_bytes())
                            buttons['Preview'].invoke()
                            state['stage'] = 5
                        elif stage == 5 and buttons['Convert ready files'].instate(['!disabled']):
                            self.assertEqual([tree.set(row, 'status') for row in rows], ['Done', 'Done', 'Ready'])
                            self.assertEqual(tree.set('2', 'output'), 'Dublin_004.mov')
                            finished.append(True)
                            root.destroy()
                            return
                        state['polls'] += 1
                        self.assertLess(state['polls'], 200, 'Desktop workflow timed out')
                        root.after(100, check)
                    except BaseException as exc:
                        errors.append(exc)
                        root.destroy()
                root.after(100, check)
                return root
            with patch('tkinter.Tk', create_root), patch.object(filedialog, 'askdirectory', return_value=str(folder)), \
                    patch.object(messagebox, 'showerror', side_effect=lambda *a, **k: errors.append(a)), \
                    patch('videotool.execute_davinci', side_effect=execute), \
                    patch('videotool_gui.open_processing_receipt',
                          side_effect=lambda path: opened_receipts.append(path)), \
                    patch('videotool_gui.open_output_folder', side_effect=lambda path: opened.append(path)):
                gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)

    def test_cancel_button_interrupts_current_file_and_keeps_partial(self):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        original_tk = tk.Tk
        errors, finished = [], []
        started = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clip = folder / 'cancel.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=size=256x144:rate=25:duration=0.12', '-c:v', 'mpeg4',
                            str(clip)], check=True, capture_output=True)
            output = folder / 'cancel_davinci.mov'
            def execute(source, target, command, callback, cancel=None):
                target.write_bytes(b'partial retained')
                started.set()
                if cancel is None or not cancel.wait(3):
                    raise AssertionError('Cancel signal was not delivered to the active conversion')
                raise videotool.ConversionCancelled('Conversion cancelled; partial output preserved.')
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            def create_root():
                root = original_tk()
                state = {'stage': 0, 'polls': 0}
                def check():
                    try:
                        widgets = list(descendants(root))
                        buttons = {str(w.cget('text')): w for w in widgets
                                   if isinstance(w, (ttk.Button, ttk.Menubutton))}
                        tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
                        if state['stage'] == 0:
                            root.nametowidget(str(buttons['Choose source'].cget('menu'))).invoke(0)
                            buttons['Preview'].invoke()
                            state['stage'] = 1
                        elif state['stage'] == 1 and buttons['Convert ready files'].instate(['!disabled']):
                            self.assertTrue(buttons['Cancel current conversion'].instate(['disabled']))
                            buttons['Convert ready files'].invoke()
                            state['stage'] = 2
                        elif state['stage'] == 2 and started.is_set():
                            self.assertTrue(buttons['Cancel current conversion'].instate(['!disabled']))
                            buttons['Cancel current conversion'].invoke()
                            state['stage'] = 3
                        elif state['stage'] == 3 and buttons['Preview retry'].instate(['!disabled']):
                            self.assertEqual(tree.set('0', 'status'), 'Interrupted')
                            self.assertEqual(output.read_bytes(), b'partial retained')
                            self.assertTrue(buttons['Cancel current conversion'].instate(['disabled']))
                            finished.append(True)
                            root.destroy()
                            return
                        state['polls'] += 1
                        self.assertLess(state['polls'], 100, 'Cancel workflow timed out')
                        root.after(100, check)
                    except BaseException as exc:
                        errors.append(exc)
                        root.destroy()
                root.after(100, check)
                return root
            with patch('tkinter.Tk', create_root), \
                    patch.object(filedialog, 'askopenfilename', return_value=str(clip)), \
                    patch.object(messagebox, 'showerror', side_effect=lambda *a, **k: errors.append(a)), \
                    patch('videotool.execute_davinci', side_effect=execute):
                gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)


@unittest.skipUnless(os.environ.get('VIDEOTOOL_GUI_TESTS') == '1', 'Desktop check is opt-in')
class TooltipTest(unittest.TestCase):
    def test_hover_dismiss_and_pending_cancellation(self):
        import tkinter as tk
        from tkinter import ttk
        from tooltips import Tooltip
        root = tk.Tk()
        self.addCleanup(root.destroy)
        button = ttk.Button(root, text='Disabled action', state='disabled')
        button.pack()
        tooltip = Tooltip(button, 'A helpful explanation.', delay=30)
        root.update()
        def wait():
            root.after(80, root.quit)
            root.mainloop()
        button.event_generate('<Enter>')
        wait()
        self.assertIsNotNone(tooltip.window)
        self.assertTrue(tooltip.window.winfo_ismapped())
        button.event_generate('<Leave>')
        self.assertIsNone(tooltip.window)
        button.event_generate('<Enter>')
        button.event_generate('<Leave>')
        wait()
        self.assertIsNone(tooltip.window)
        for event in ('<ButtonPress-1>', '<FocusOut>'):
            button.event_generate('<Enter>')
            wait()
            self.assertIsNotNone(tooltip.window)
            button.event_generate(event)
            self.assertIsNone(tooltip.window)
        button.event_generate('<Enter>')
        wait()
        root.focus_force()
        root.update()
        root.event_generate('<Escape>')
        self.assertIsNone(tooltip.window)
        button.event_generate('<Enter>')
        button.destroy()
        wait()
        self.assertIsNone(tooltip.window)
        self.assertIsNone(tooltip.pending)


@unittest.skipUnless(os.environ.get('VIDEOTOOL_GUI_TESTS') == '1', 'Desktop check is opt-in')
class UploadDesktopTest(unittest.TestCase):
    def test_switch_presets_preview_and_prepare_manual_uploads(self):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        original_tk = tk.Tk
        errors, finished = [], []
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clip = folder / 'finished.mov'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'testsrc2=size=320x180:rate=25:duration=2', '-f', 'lavfi', '-i',
                            'sine=sample_rate=48000:duration=2', '-c:v', 'mpeg4', '-c:a',
                            'pcm_s16le', str(clip)], check=True, capture_output=True)
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            def create_root():
                root = original_tk()
                state = {'stage': 0, 'polls': 0}
                def check():
                    try:
                        widgets = list(descendants(root))
                        buttons = {str(w.cget('text')): w for w in widgets
                                   if isinstance(w, (ttk.Button, ttk.Menubutton))}
                        tree = next(w for w in widgets if isinstance(w, ttk.Treeview))
                        choice = next(w for w in widgets if isinstance(w, ttk.Combobox))
                        size = next(w for w in widgets if isinstance(w, ttk.Entry) and str(w.cget('width')) == '10')
                        reference = next(w for w in widgets if isinstance(w, ttk.Entry) and
                                         w.get().startswith('Automatic: preserve'))
                        advanced = next(w for w in widgets if isinstance(w, ttk.Checkbutton) and
                                        str(w.cget('text')) == 'Advanced settings')
                        progress_panel = next(w for w in widgets if isinstance(w, ttk.LabelFrame) and
                                              str(w.cget('text')) == 'Progress')
                        details = next(w for w in widgets if isinstance(w, tk.Text))
                        stage = state['stage']
                        if stage == 0:
                            self.assertTrue(size.instate(['disabled']))
                            self.assertEqual(size.master.winfo_manager(), '', 'AI size should be hidden for DaVinci')
                            self.assertEqual(reference.master.winfo_manager(), '',
                                             'DaVinci reference should initially be hidden')
                            self.assertEqual(progress_panel.winfo_manager(), '')
                            self.assertFalse(details.winfo_ismapped())
                            self.assertEqual(buttons['Open output folder'].master.winfo_manager(), '')
                            advanced.invoke()
                            root.update_idletasks()
                            self.assertEqual(reference.master.winfo_manager(), 'grid',
                                             'Advanced DaVinci reference should be revealed on request')
                            root.nametowidget(str(buttons['Choose source'].cget('menu'))).invoke(0)
                            choice.set('AI Studio upload')
                            root.update_idletasks()
                            self.assertTrue(size.instate(['!disabled']))
                            self.assertEqual(size.master.winfo_manager(), 'grid',
                                             'AI size should be visible for AI Studio')
                            self.assertEqual(reference.master.master.winfo_manager(), '',
                                             'DaVinci reference should be hidden for AI Studio')
                            self.assertEqual(size.get(), '380')
                            size.delete(0, 'end')
                            size.insert(0, '1')
                            buttons['Preview'].invoke()
                            state['stage'] = 1
                        elif stage == 1 and buttons['Convert ready files'].instate(['!disabled']):
                            self.assertEqual(tree.set('0', 'output'), 'finished_aistudio.mp4')
                            self.assertNotEqual(tree.set('0', 'size'), '')
                            self.assertNotEqual(tree.set('0', 'duration'), '—')
                            self.assertNotEqual(tree.set('0', 'depth'), '—')
                            self.assertEqual(tree.item('0', 'tags'), ('Ready',))
                            self.assertEqual(progress_panel.winfo_manager(), 'grid')
                            self.assertTrue(details.winfo_ismapped())
                            self.assertEqual(buttons['Open output folder'].master.winfo_manager(), 'grid')
                            self.assertTrue(choice.instate(['readonly']))
                            buttons['Convert ready files'].invoke()
                            self.assertTrue(choice.instate(['disabled']))
                            state['stage'] = 2
                        elif stage == 2 and buttons['Preview'].instate(['!disabled']):
                            self.assertEqual(tree.set('0', 'status'), 'Done')
                            self.assertLessEqual((folder / 'finished_aistudio.mp4').stat().st_size, 1_000_000)
                            choice.set('YouTube upload')
                            root.update_idletasks()
                            self.assertTrue(size.instate(['disabled']))
                            self.assertEqual(size.master.winfo_manager(), '', 'AI size should be hidden for YouTube')
                            self.assertEqual(reference.master.master.winfo_manager(), '',
                                             'DaVinci reference should be hidden for YouTube')
                            self.assertTrue(buttons['Convert ready files'].instate(['disabled']))
                            buttons['Preview'].invoke()
                            state['stage'] = 3
                        elif stage == 3 and buttons['Convert ready files'].instate(['!disabled']):
                            self.assertEqual(tree.set('0', 'output'), 'finished_youtube.mp4')
                            buttons['Convert ready files'].invoke()
                            state['stage'] = 4
                        elif stage == 4 and buttons['Preview'].instate(['!disabled']):
                            self.assertEqual(tree.set('0', 'status'), 'Done')
                            self.assertTrue((folder / 'finished_youtube.mp4').is_file())
                            labels = [str(w.cget('text')) for w in widgets if isinstance(w, ttk.Label)]
                            self.assertTrue(any('Nothing has been uploaded' in label for label in labels))
                            self.assertTrue(buttons['Open output folder'].instate(['!disabled']))
                            finished.append(True)
                            root.destroy()
                            return
                        state['polls'] += 1
                        self.assertLess(state['polls'], 300, 'Upload desktop check timed out')
                        root.after(100, check)
                    except BaseException as exc:
                        errors.append(exc)
                        root.destroy()
                root.after(100, check)
                return root
            with patch('tkinter.Tk', create_root), patch.object(filedialog, 'askopenfilename', return_value=str(clip)), \
                    patch.object(messagebox, 'showerror', side_effect=lambda *a, **k: errors.append(a)):
                gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)
