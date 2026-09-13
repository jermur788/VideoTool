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
import frame_benchmark
import gemini_analysis


@unittest.skipUnless(os.environ.get('VIDEOTOOL_GUI_TESTS') == '1', 'Desktop check is opt-in')
class DesktopWorkflowTest(unittest.TestCase):
    def test_small_window_keeps_actions_visible_and_page_scrollable(self):
        import tkinter as tk
        from tkinter import ttk
        original_tk = tk.Tk
        errors, finished = [], []

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def create_root():
            root = original_tk()

            def check():
                try:
                    root.geometry('800x600')
                    root.update_idletasks()
                    widgets = list(descendants(root))
                    preview = next(w for w in widgets if isinstance(w, ttk.Button) and
                                   str(w.cget('text')) == 'Preview')
                    page_scrollbar = next(w for w in widgets if
                                          getattr(w, 'videotool_page_scrollbar', False))
                    panels = {str(w.cget('text')): w for w in widgets
                              if isinstance(w, ttk.LabelFrame)}
                    self.assertTrue(page_scrollbar.winfo_ismapped())
                    first, last = page_scrollbar.get()
                    self.assertLess(last, 1.0)
                    self.assertLessEqual(preview.winfo_rooty() + preview.winfo_height(),
                                         root.winfo_rooty() + root.winfo_height())
                    self.assertEqual(panels['1  Choose a format'].winfo_rootx(),
                                     panels['2  Choose source and destination'].winfo_rootx())
                    self.assertLess(panels['1  Choose a format'].winfo_rooty(),
                                    panels['2  Choose source and destination'].winfo_rooty())
                    root.event_generate('<Button-5>')
                    root.update_idletasks()
                    self.assertGreater(page_scrollbar.get()[0], first)
                    finished.append(True)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    root.destroy()

            root.after(150, check)
            return root

        with patch('tkinter.Tk', create_root):
            gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)

    def test_resumable_analysis_is_prominent_after_preview(self):
        import tkinter as tk
        from tkinter import ttk, filedialog
        original_tk = tk.Tk
        errors, finished = [], []
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clip = folder / 'resume.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=size=256x144:rate=25:duration=0.12', '-c:v', 'mpeg4',
                            str(clip)], check=True, capture_output=True)
            def extractor(video, target, cancel=None):
                path = Path(target) / 'frame-0001.jpg'
                path.write_bytes(b'frame')
                return frame_benchmark.ExtractedFrames((path,), (0.0,), 2.0, 0.1)
            def contact(extracted, output, cancel=None):
                Path(output).write_bytes(b'contact')
            upload = lambda *args, **kwargs: gemini_analysis.UploadedVideo(
                object(), 'files/test', 'gemini://test', 0.1, 0.1)
            native = lambda *args, **kwargs: ('native result', 0.1)
            fail = lambda *args, **kwargs: (_ for _ in ()).throw(
                gemini_analysis.GeminiError('temporary frame failure'))
            partial = frame_benchmark.run(
                clip, gemini_analysis.DEFAULT_PROMPT, extractor=extractor,
                contact_writer=contact, uploader=upload, native_generator=native,
                frame_generator=fail)
            self.assertEqual(partial.requests_completed, 1)
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
                        if state['stage'] == 0:
                            choice = next(w for w in widgets if isinstance(w, ttk.Combobox))
                            choice.set('AI Studio upload')
                            frame_toggle = next(
                                w for w in widgets if isinstance(w, ttk.Checkbutton) and
                                str(w.cget('text')).startswith('Combine native video'))
                            frame_toggle.invoke()
                            source_button = next(
                                w for w in widgets if isinstance(w, ttk.Menubutton) and
                                str(w.cget('text')) == 'Choose source')
                            root.nametowidget(str(source_button.cget('menu'))).invoke(0)
                            preview = next(w for w in widgets if isinstance(w, ttk.Button) and
                                           str(w.cget('text')) == 'Preview')
                            preview.invoke()
                            state['stage'] = 1
                        else:
                            labels = [str(w.cget('text')) for w in widgets
                                      if isinstance(w, ttk.Label)]
                            if any(text.startswith('Resume available ·') for text in labels):
                                self.assertIn(
                                    'Resume available. Review the saved-stage details, then run combined analysis.',
                                    labels)
                                self.assertTrue(any(
                                    isinstance(w, ttk.Button) and
                                    str(w.cget('text')) == 'Run combined analysis' and
                                    w.instate(['!disabled']) for w in widgets))
                                finished.append(True)
                                root.destroy()
                                return
                        state['polls'] += 1
                        self.assertLess(state['polls'], 100, 'Resume preview timed out')
                        root.after(100, check)
                    except BaseException as exc:
                        errors.append(exc)
                        root.destroy()
                root.after(100, check)
                return root
            with patch('tkinter.Tk', create_root), \
                    patch.object(filedialog, 'askopenfilename', return_value=str(clip)):
                gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)

    def test_missing_gemini_key_reports_that_no_upload_started(self):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        original_tk = tk.Tk
        errors, finished = [], []
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / 'direct.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=size=256x144:rate=25:duration=0.12', '-c:v', 'mpeg4',
                            str(clip)], check=True, capture_output=True)
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
                        if state['stage'] == 0:
                            root.geometry('1200x900')
                            choice = next(w for w in widgets if isinstance(w, ttk.Combobox))
                            choice.set('AI Studio upload')
                            direct = next(w for w in widgets if isinstance(w, ttk.Checkbutton) and
                                          str(w.cget('text')).startswith('Upload original directly'))
                            direct.invoke()
                            root.nametowidget(str(buttons['Choose source'].cget('menu'))).invoke(0)
                            buttons['Preview'].invoke()
                            state['stage'] = 1
                        elif state['stage'] == 1 and buttons['Upload original to Gemini'].instate(['!disabled']):
                            self.assertLessEqual(
                                buttons['Copy error/details'].winfo_rooty() +
                                buttons['Copy error/details'].winfo_height(),
                                root.winfo_rooty() + root.winfo_height())
                            buttons['Upload original to Gemini'].invoke()
                            labels = [str(w.cget('text')) for w in widgets if isinstance(w, ttk.Label)]
                            self.assertIn('Current file: Gemini did not start', labels)
                            self.assertIn('Whole batch: no upload started', labels)
                            self.assertTrue(any(text.startswith('Gemini did not start.') for text in labels))
                            self.assertEqual(len(messagebox.showerror.call_args_list), 1)
                            finished.append(True)
                            root.destroy()
                            return
                        state['polls'] += 1
                        self.assertLess(state['polls'], 100, 'Readiness workflow timed out')
                        root.after(100, check)
                    except BaseException as exc:
                        errors.append(exc)
                        root.destroy()
                root.after(100, check)
                return root
            with patch('tkinter.Tk', create_root), \
                    patch.object(filedialog, 'askopenfilename', return_value=str(clip)), \
                    patch.object(messagebox, 'showerror'), \
                    patch('videotool_gui.gemini_analysis.readiness',
                          return_value=(False, 'Gemini API key not set up yet.')):
                gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)

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
        from tkinter import ttk, filedialog, messagebox, simpledialog
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
                            self.assertTrue(set(buttons).issubset(set(gui.BUTTON_HINTS)))
                            for label, button in buttons.items():
                                self.assertEqual(button.tooltip.text, gui.BUTTON_HINTS[label])
                            output_menu = root.nametowidget(str(buttons['Output folder'].cget('menu')))
                            self.assertEqual(output_menu.entrycget(0, 'label'), 'Choose existing folder')
                            self.assertEqual(output_menu.entrycget(1, 'label'), 'Create new folder')
                            output_menu.invoke(1)
                            self.assertTrue((folder / 'New exports').is_dir())
                            buttons['Use originals’ folder'].invoke()
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
                            tree.selection_set('1')
                            buttons['Copy error/details'].invoke()
                            self.assertIn('Simulated failure for retry check', root.clipboard_get())
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
                    patch.object(simpledialog, 'askstring', return_value='New exports'), \
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
        from tkinter import ttk, filedialog, messagebox, simpledialog
        original_tk = tk.Tk
        errors, finished, saved_keys = [], [], []
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
                        details = next(w for w in widgets if isinstance(w, tk.Text) and
                                       str(w.cget('state')) == 'disabled')
                        prompts = [w for w in widgets if isinstance(w, tk.Text) and
                                   str(w.cget('height')) == '4']
                        prompt, creator_prompt = prompts
                        gemini_toggle = next(w for w in widgets if isinstance(w, ttk.Checkbutton) and
                                             str(w.cget('text')) == 'Analyze converted video with Gemini')
                        direct_toggle = next(w for w in widgets if isinstance(w, ttk.Checkbutton) and
                                             str(w.cget('text')) == 'Upload original directly to Gemini (skip conversion)')
                        creator_toggle = next(w for w in widgets if isinstance(w, ttk.Checkbutton) and
                                              str(w.cget('text')).startswith('Compare standard summary'))
                        frame_toggle = next(w for w in widgets if isinstance(w, ttk.Checkbutton) and
                                            str(w.cget('text')).startswith('Combine native video'))
                        stage = state['stage']
                        if stage == 0:
                            root.geometry('1200x900')
                            root.update_idletasks()
                            page_scrollbar = next(w for w in widgets if
                                                  getattr(w, 'videotool_page_scrollbar', False))
                            self.assertTrue(page_scrollbar.winfo_ismapped(),
                                            'The page scrollbar should remain available')
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
                            self.assertEqual(filedialog.askopenfilename.call_args.kwargs['initialdir'],
                                             str(folder))
                            choice.set('AI Studio upload')
                            root.update_idletasks()
                            self.assertTrue(size.instate(['!disabled']))
                            self.assertEqual(size.master.winfo_manager(), 'grid',
                                             'AI size should be visible for AI Studio')
                            self.assertEqual(gemini_toggle.master.winfo_manager(), 'grid')
                            key_menu = root.nametowidget(str(buttons['Gemini key…'].cget('menu')))
                            self.assertEqual(key_menu.entrycget(0, 'label'), 'Save or replace key')
                            self.assertEqual(key_menu.entrycget(1, 'label'), 'Remove saved key')
                            key_menu.invoke(0)
                            self.assertEqual(saved_keys, ['temporary-test-key'])
                            self.assertFalse(prompt.winfo_ismapped())
                            gemini_toggle.invoke()
                            root.update_idletasks()
                            self.assertTrue(prompt.winfo_ismapped(), 'main prompt should open for converted analysis')
                            self.assertTrue(direct_toggle.instate(['!disabled']))
                            prompt.delete('1.0', 'end')
                            prompt.insert('1.0', 'A replacement prompt')
                            buttons['Restore default prompt'].invoke()
                            self.assertIn('chronological summary', prompt.get('1.0', 'end'))
                            gemini_toggle.invoke()
                            root.update_idletasks()
                            self.assertFalse(prompt.winfo_ismapped())
                            direct_toggle.invoke()
                            root.update_idletasks()
                            self.assertTrue(prompt.winfo_ismapped(), 'main prompt should open for direct upload')
                            self.assertFalse(gemini_toggle.instate(['selected']))
                            self.assertEqual(size.master.winfo_manager(), '')
                            self.assertEqual(buttons['Upload original to Gemini'].winfo_manager(), 'grid')
                            self.assertEqual(buttons['Convert ready files'].winfo_manager(), '')
                            self.assertEqual(buttons['Output folder'].winfo_manager(), '')
                            direct_toggle.invoke()
                            root.update_idletasks()
                            self.assertEqual(size.master.winfo_manager(), 'grid')
                            self.assertFalse(prompt.winfo_ismapped())
                            self.assertEqual(buttons['Convert ready files'].winfo_manager(), 'grid')
                            creator_toggle.invoke()
                            root.update_idletasks()
                            prompt_tabs = next(w for w in widgets if isinstance(w, ttk.Notebook))
                            self.assertEqual([prompt_tabs.tab(tab, 'text') for tab in prompt_tabs.tabs()],
                                             ['Standard summary', 'Creator prompt'])
                            prompt_tabs.select(prompt_tabs.tabs()[1])
                            root.update_idletasks()
                            self.assertFalse(prompt.winfo_ismapped())
                            self.assertTrue(creator_prompt.winfo_ismapped())
                            creator_toggle.invoke()
                            root.update_idletasks()
                            self.assertFalse(prompt_tabs.winfo_ismapped())
                            frame_toggle.invoke()
                            root.update_idletasks()
                            self.assertTrue(prompt.winfo_ismapped())
                            self.assertEqual(buttons['Upload original to Gemini'].cget('text'),
                                             'Run combined analysis')
                            self.assertFalse(creator_toggle.instate(['selected']))
                            frame_toggle.invoke()
                            root.update_idletasks()
                            self.assertEqual(reference.master.master.winfo_manager(), '',
                                             'DaVinci reference should be hidden for AI Studio')
                            self.assertEqual(size.get(), '380')
                            size.delete(0, 'end')
                            size.insert(0, '1')
                            buttons['Preview'].invoke()
                            state['stage'] = 1
                        elif stage == 1 and buttons['Convert ready files'].instate(['!disabled']):
                            self.assertEqual(tree.set('0', 'output'), 'finished_aistudio.mp4')
                            self.assertTrue(tree.set('0', 'estimate').startswith('~'), 'estimate should be shown')
                            self.assertNotEqual(tree.set('0', 'size'), '')
                            self.assertNotEqual(tree.set('0', 'duration'), '—')
                            self.assertNotEqual(tree.set('0', 'depth'), '—')
                            self.assertEqual(tree.item('0', 'tags'), ('Ready',))
                            self.assertEqual(progress_panel.winfo_manager(), 'grid')
                            self.assertTrue(details.winfo_ismapped(), 'selected-row details should be visible')
                            self.assertEqual(buttons['Open output folder'].master.winfo_manager(), 'grid')
                            self.assertTrue(choice.instate(['readonly']), 'preset should be selectable after preview')
                            buttons['Convert ready files'].invoke()
                            self.assertTrue(choice.instate(['disabled']), 'preset should lock during conversion')
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
                            self.assertTrue(tree.set('0', 'estimate').startswith('~'))
                            buttons['Convert ready files'].invoke()
                            state['stage'] = 4
                        elif stage == 4 and buttons['Preview'].instate(['!disabled']):
                            self.assertEqual(tree.set('0', 'status'), 'Done')
                            self.assertTrue((folder / 'finished_youtube.mp4').is_file())
                            self.assertLessEqual(
                                buttons['Open output folder'].winfo_rooty() +
                                buttons['Open output folder'].winfo_height(),
                                root.winfo_rooty() + root.winfo_height())
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
                        errors.append(AssertionError(f'upload desktop stage {state["stage"]}: {exc}'))
                        root.destroy()
                root.after(100, check)
                return root
            with patch('tkinter.Tk', create_root), \
                    patch('videotool_gui.user_settings.default_source_folder', return_value=folder), \
                    patch.object(filedialog, 'askopenfilename', return_value=str(clip)), \
                    patch.object(simpledialog, 'askstring', return_value='temporary-test-key'), \
                    patch.object(messagebox, 'showerror', side_effect=lambda *a, **k: errors.append(a)), \
                    patch('videotool_gui.gemini_analysis.save_api_key',
                          side_effect=lambda value: saved_keys.append(value)):
                gui.main()
        self.assertEqual(errors, [])
        self.assertTrue(finished)
