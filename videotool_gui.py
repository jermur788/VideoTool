"""A small desktop interface for VideoTool. Run with python3 videotool_gui.py."""

from dataclasses import dataclass
from pathlib import Path
import queue
import math
import time
import re
import threading

import videotool


@dataclass
class Conversion:
    source: Path
    output: Path
    command: list
    fingerprint: tuple = ()
    detail: str = ''
    error: str = ''
    duration: float = 0


def duration_seconds(metadata):
    try:
        value = float(metadata.get('format', {}).get('duration', 0))
        return value if math.isfinite(value) and value > 0 else 0
    except (TypeError, ValueError):
        return 0


def remaining_text(seconds):
    if seconds is None or not math.isfinite(seconds):
        return 'estimating…'
    seconds = max(0, math.ceil(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'about {hours}h {minutes:02d}m left' if hours else f'about {minutes}m {seconds:02d}s left'


def estimate(processed, duration, elapsed, complete=False):
    if duration <= 0:
        return None, None
    fraction = min(max(processed / duration, 0), 1)
    percent = 100 if complete else min(fraction * 100, 99)
    eta = elapsed * (1 - fraction) / fraction if fraction > 0 and elapsed >= 2 else None
    return percent, (0 if complete else eta)


def fingerprint(source):
    stat = source.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def preview(source, folder=False, destination=None, location=''):
    """Capture the exact files and commands that the user will review."""
    location = location.strip()
    if location and (location in ('.', '..') or
                     any(c in '<>:"/\\|?*' or ord(c) < 32 for c in location)):
        raise videotool.VideoToolError('Enter a location name without slashes or special filename characters.')
    if folder:
        sources, target = videotool.discover_batch(source, destination)
        if location:
            # Do not feed this location's earlier numbered exports back into a batch.
            exported = re.compile(re.escape(location) + r'_\d{3,}\.mov', re.IGNORECASE)
            sources = [p for p in sources if not exported.fullmatch(p.name)]
            if not sources:
                raise videotool.VideoToolError('No source videos remain after excluding this location’s numbered outputs.')
    else:
        sources = [videotool.validate_source(source)]
        target = Path(destination).expanduser().resolve() if destination else sources[0].parent
    items = []
    for number, source in enumerate(sources, 1):
        name = source.name if folder else source.stem
        output = target / (f'{location}_{number:03d}.mov' if location else name + '_davinci.mov')
        item = Conversion(source, output, [])
        try:
            before = fingerprint(source)
            output = videotool.validate_output(source, output)
            metadata = videotool.inspect_video(source)
            command = videotool.plan_davinci(source, output, metadata)
            if before != fingerprint(source):
                raise videotool.VideoToolError('Source changed during inspection. Preview again.')
            video = videotool.select_video(metadata)
            audio = sum(s.get('codec_type') == 'audio' for s in metadata['streams'])
            item.output = output
            item.command = command
            item.duration = duration_seconds(metadata)
            item.fingerprint = before
            item.detail = (f"{video['width']} × {video['height']} · "
                           f"{videotool.frame_rate(video.get('avg_frame_rate'))} fps average\n"
                           f"DNxHR HQX · 10-bit 4:2:2 · {audio} audio stream(s), PCM 24-bit\n"
                           'Source frame timing, resolution, and known color tags retained.')
        except (videotool.VideoToolError, OSError) as exc:
            item.error = str(exc)
        items.append(item)
    return items


def convert(items, stop, report, report_progress=None):
    """Run only the reviewed snapshot; stop requests take effect between files."""
    succeeded = 0
    failed = sum(bool(item.error) for item in items)
    ready = [(index, item) for index, item in enumerate(items) if not item.error]
    attempted = 0
    total_duration = sum(item.duration for _, item in ready) if all(item.duration > 0 for _, item in ready) else 0
    completed_duration = 0
    batch_start = time.monotonic()
    conversion_failed = False
    for index, item in ready:
        if stop.is_set():
            break
        attempted += 1
        file_start = time.monotonic()
        processed = 0
        def update(seconds, complete=False):
            nonlocal processed
            processed = max(processed, seconds)
            now = time.monotonic()
            file_percent, file_eta = estimate(processed, item.duration, now - file_start, complete)
            batch_percent, batch_eta = estimate(completed_duration + min(processed, item.duration), total_duration,
                                                 now - batch_start, complete and attempted == len(ready) and not conversion_failed)
            if report_progress:
                report_progress(file_percent, file_eta, batch_percent, None if conversion_failed else batch_eta)
        update(0)
        report(index, 'Converting', f'Converting {attempted} of {len(ready)}: {item.source.name}')
        try:
            if fingerprint(item.source) != item.fingerprint:
                raise videotool.VideoToolError('Source changed since preview. Preview again.')
            videotool.execute_davinci(item.source, item.output, item.command, update)
            update(item.duration, complete=True)
            completed_duration += item.duration
            succeeded += 1
            report(index, 'Done', str(item.output))
        except (videotool.VideoToolError, OSError) as exc:
            failed += 1
            conversion_failed = True
            report(index, 'Failed', str(exc))
    for index, item in ready[attempted:]:
        report(index, 'Not attempted', 'Stopped before this file started.')
    return succeeded, failed, len(ready) - attempted


def main():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        from tkinter.scrolledtext import ScrolledText
    except ImportError:
        print('The window interface needs Tkinter. On Linux Mint/Ubuntu, run:\n'
              '  sudo apt install python3-tk\nThen run: python3 videotool_gui.py')
        return 1
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f'Could not open a desktop window: {exc}')
        return 1
    root.title('VideoTool — Prepare for DaVinci')
    root.geometry('1000x820')
    root.minsize(780, 740)
    root.configure(background='#f3f5f7')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TFrame', background='#f3f5f7')
    style.configure('TLabel', background='#f3f5f7', font=('Sans', 11))
    style.configure('Title.TLabel', font=('Sans', 23, 'bold'))
    style.configure('TButton', font=('Sans', 11), padding=(12, 8))
    style.configure('Treeview', font=('Sans', 10), rowheight=30)
    style.configure('Treeview.Heading', font=('Sans', 10, 'bold'))
    style.configure('Accent.TButton', background='#16665b', foreground='white')
    body = ttk.Frame(root, padding=24)
    body.pack(fill='both', expand=True)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(5, weight=1)
    ttk.Label(body, text='Prepare footage for DaVinci', style='Title.TLabel').grid(sticky='w')
    ttk.Label(body, text='Choose footage → Preview → Convert').grid(row=1, sticky='w', pady=(6, 18))
    source = tk.StringVar(value='Choose a video or a folder to begin')
    destination = tk.StringVar(value='Save beside the original files')
    location = tk.StringVar()
    status = tk.StringVar(value='Ready to choose footage.')
    state = {'source': None, 'folder': False, 'destination': None, 'items': [], 'busy': False,
             'messages': {}}
    events = queue.Queue()
    stop = threading.Event()
    controls = []

    def invalidate():
        state['items'] = []
        state['messages'] = {}
        tree.delete(*tree.get_children())
        show_details()
        file_progress_text.set('Current file: waiting')
        batch_progress_text.set('Whole batch: waiting')
        progress['value'] = 0
        batch_progress['value'] = 0
        start_button.configure(state='disabled')
        status.set('Preview your selection before converting.')

    def choose_source(folder):
        selected = filedialog.askdirectory(parent=root, title='Choose footage folder') if folder else filedialog.askopenfilename(parent=root, title='Choose video')
        if selected:
            state.update(source=selected, folder=folder)
            source.set(('Folder: ' if folder else 'Video: ') + selected)
            invalidate()

    def choose_destination():
        selected = filedialog.askdirectory(parent=root, title='Choose output folder')
        if selected:
            state['destination'] = selected
            destination.set(selected)
            invalidate()

    def reset_destination():
        state['destination'] = None
        destination.set('Save beside the original files')
        invalidate()

    selection = ttk.Frame(body)
    selection.grid(row=2, sticky='ew')
    selection.columnconfigure(0, weight=1)
    ttk.Entry(selection, textvariable=source, state='readonly').grid(row=0, column=0, sticky='ew', padx=(0, 8))
    for column, (label, callback) in enumerate([('Choose video', lambda: choose_source(False)), ('Choose folder', lambda: choose_source(True))], 1):
        button = ttk.Button(selection, text=label, command=callback)
        button.grid(row=0, column=column, padx=4)
        controls.append(button)
    naming = ttk.Frame(selection)
    naming.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(4, 10))
    ttk.Label(naming, text='Location name (optional)').pack(side='left', padx=(0, 10))
    location_entry = ttk.Entry(naming, textvariable=location, width=25)
    location_entry.pack(side='left', padx=(0, 10))
    controls.append(location_entry)
    ttk.Label(naming, text='Dublin → Dublin_001.mov, Dublin_002.mov').pack(side='left')
    ttk.Entry(selection, textvariable=destination, state='readonly').grid(row=1, column=0, sticky='ew', padx=(0, 8), pady=8)
    for column, (label, callback) in enumerate([('Output folder', choose_destination), ('Use originals’ folder', reset_destination)], 1):
        button = ttk.Button(selection, text=label, command=callback)
        button.grid(row=1, column=column, padx=4)
        controls.append(button)
    ttk.Label(body, text='Folder mode scans this folder only. Existing outputs are preserved.').grid(row=3, sticky='w', pady=(0, 12))
    ttk.Label(body, text='Editing copies are much larger and use lossy DNxHR video with PCM audio.\n'
              'Originals are kept. No resizing, LUT, or tone mapping; subtitles and timecode are omitted.').grid(row=4, sticky='w', pady=(0, 14))
    table_frame = ttk.Frame(body)
    table_frame.grid(row=5, sticky='nsew')
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(table_frame, columns=('source', 'output', 'status'), show='headings', selectmode='browse')
    for key, title, width in [('source', 'Source video', 270), ('output', 'Output file', 340), ('status', 'Status', 120)]:
        tree.heading(key, text=title)
        tree.column(key, width=width, minwidth=100)
    tree.grid(row=0, column=0, sticky='nsew')
    scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky='ns')
    tree.configure(yscrollcommand=scrollbar.set)
    details = ScrolledText(body, height=6, wrap='word', font=('Sans', 10), relief='flat', padx=10, pady=8)
    details.grid(row=6, sticky='ew', pady=10)
    details.configure(state='disabled')

    def show_details(event=None):
        selected = tree.selection()
        text = ''
        if selected:
            index = int(selected[0])
            item = state['items'][index]
            text = f'Source: {item.source}\nOutput: {item.output}\n' + (item.error or item.detail)
            if index in state['messages']:
                text += '\n' + state['messages'][index]
        details.configure(state='normal')
        details.delete('1.0', 'end')
        details.insert('end', text)
        details.configure(state='disabled')
    tree.bind('<<TreeviewSelect>>', show_details)
    progress_frame = ttk.Frame(body)
    progress_frame.grid(row=7, sticky='ew')
    progress_frame.columnconfigure(0, weight=1)
    file_progress_text = tk.StringVar(value='Current file: waiting')
    batch_progress_text = tk.StringVar(value='Whole batch: waiting')
    ttk.Label(progress_frame, textvariable=file_progress_text).grid(row=0, sticky='w')
    progress = ttk.Progressbar(progress_frame, mode='indeterminate')
    progress.grid(row=1, sticky='ew', pady=(3, 6))
    ttk.Label(progress_frame, textvariable=batch_progress_text).grid(row=2, sticky='w')
    batch_progress = ttk.Progressbar(progress_frame, mode='determinate')
    batch_progress.grid(row=3, sticky='ew', pady=(3, 0))
    ttk.Label(body, textvariable=status, wraplength=880).grid(row=8, sticky='w', pady=10)
    actions = ttk.Frame(body)
    actions.grid(row=9, sticky='e')

    def busy(value):
        state['busy'] = value
        for button in controls:
            button.configure(state='disabled' if value else 'normal')
        start_button.configure(state='disabled')
        if value:
            progress.start(12)
        else:
            progress.stop()
            stop_button.configure(state='disabled')

    def do_preview():
        if not state['source']:
            messagebox.showinfo('Choose footage', 'Choose a video or folder first.', parent=root)
            return
        invalidate()
        busy(True)
        progress.configure(mode='indeterminate')
        file_progress_text.set('Current file: inspecting footage')
        batch_progress_text.set('Whole batch: waiting')
        batch_progress['value'] = 0
        status.set('Inspecting footage…')
        args = (state['source'], state['folder'], state['destination'], location.get())
        def worker():
            try:
                events.put(('preview', preview(*args)))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def do_convert():
        busy(True)
        progress.stop()
        progress.configure(mode='determinate', value=0)
        batch_progress['value'] = 0
        stop.clear()
        stop_button.configure(state='normal')
        status.set('Starting conversion…')
        items = tuple(state['items'])
        def worker():
            try:
                result = convert(items, stop, lambda *data: events.put(('progress', data)),
                                 lambda *data: events.put(('percent', data)))
                events.put(('done', result))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def request_stop():
        stop.set()
        stop_button.configure(state='disabled')
        status.set('Stopping after the current file finishes…')

    preview_button = ttk.Button(actions, text='Preview', command=do_preview)
    preview_button.pack(side='left', padx=5)
    controls.append(preview_button)
    stop_button = ttk.Button(actions, text='Stop after current file', command=request_stop, state='disabled')
    stop_button.pack(side='left', padx=5)
    start_button = ttk.Button(actions, text='Convert ready files', style='Accent.TButton', command=do_convert, state='disabled')
    start_button.pack(side='left', padx=5)

    def poll():
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == 'preview':
                    state['items'] = data
                    for index, item in enumerate(data):
                        tree.insert('', 'end', iid=str(index), values=(item.source.name, item.output.name, 'Blocked' if item.error else 'Ready'))
                    ready = sum(not item.error for item in data)
                    busy(False)
                    file_progress_text.set('Current file: ready to convert' if ready else 'Current file: no ready files')
                    status.set(f'{ready} ready · {len(data) - ready} blocked. Review the list, then convert ready files.')
                    if ready:
                        start_button.configure(state='normal')
                    if data:
                        tree.selection_set('0')
                        show_details()
                elif kind == 'progress':
                    index, label, message = data
                    tree.set(str(index), 'status', label)
                    state['messages'][index] = message
                    if label == 'Converting' and not stop.is_set():
                        status.set(message)
                    elif label == 'Failed':
                        file_progress_text.set('Current file: failed — select the row for details')
                    show_details()
                elif kind == 'percent':
                    file_percent, file_eta, batch_percent, batch_eta = data
                    for bar, label, title, percent, eta in (
                            (progress, file_progress_text, 'Current file', file_percent, file_eta),
                            (batch_progress, batch_progress_text, 'Whole batch', batch_percent, batch_eta)):
                        bar['value'] = percent if percent is not None else 0
                        amount = f'{percent:.0f}%' if percent is not None else 'duration unknown'
                        label.set(f'{title}: {amount} · {remaining_text(eta)}')
                elif kind == 'done':
                    busy(False)
                    succeeded, failed, remaining = data
                    if failed or remaining:
                        batch_progress_text.set('Whole batch: ended with failed, blocked, or unprocessed files')
                    status.set(f'{succeeded} succeeded · {failed} failed/blocked · {remaining} not attempted. Check picture and sound in DaVinci.')
                elif kind == 'error':
                    busy(False)
                    file_progress_text.set('Current file: operation failed')
                    batch_progress_text.set('Whole batch: stopped')
                    status.set('Unable to complete this operation. Preview again to retry.')
                    messagebox.showerror('VideoTool', data, parent=root)
        except queue.Empty:
            pass
        root.after(100, poll)

    def close():
        if state['busy']:
            messagebox.showinfo('Work in progress', 'Wait for the operation to finish before closing. During conversion, you can stop after the current file.', parent=root)
        else:
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    location.trace_add('write', lambda *args: invalidate())
    poll()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
