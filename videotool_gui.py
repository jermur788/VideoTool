"""A small desktop interface for VideoTool. Run with python3 videotool_gui.py."""

from dataclasses import dataclass, replace
from pathlib import Path
import queue
import math
import time
import re
import threading
import subprocess
import sys
import os
import sqlite3
import conversion_history
from tooltips import Tooltip

import videotool
import upload_presets


BUTTON_HINTS = {
    'Choose video': 'Choose a source video or finished DaVinci export for the selected preset.',
    'Choose folder': 'Choose a folder of source videos. Subfolders are not included.',
    'Choose reference': 'Choose a DNxHR MOV that you have already confirmed works in DaVinci.',
    'Use automatic': 'Choose DNxHR SQ for 8-bit footage and DNxHR HQX for 10-bit footage.',
    'Output folder': 'Choose an existing folder for the converted videos.',
    'Use originals’ folder': 'Save converted videos beside their source files.',
    'Preview': 'Check your footage and show the proposed output names. No videos are converted.',
    'Stop after current file': 'Let the current video finish, then stop. You can retry the unfinished files later.',
    'Convert ready files': 'Start converting the ready files shown in the preview. Completed files are kept.',
    'Preview retry': 'Prepare failed or unfinished files for review. Successful files are kept and partial files are not overwritten.',
    'Open output folder': 'Open the output folder in your file manager. If a row is selected, open that video’s output folder.',
}


@dataclass
class Conversion:
    source: Path
    output: Path
    command: list
    fingerprint: tuple = ()
    detail: str = ''
    error: str = ''
    duration: float = 0
    location: str = ""
    completed: bool = False
    outcome: str = "Ready"
    preset: str = "davinci"
    target_mb: float = 380
    upload_plan: object = None
    davinci_settings: object = None
    estimated_bytes: object = None
    available_bytes: object = None
    format_key: str = ''


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


def preview(source, folder=False, destination=None, location='', preset='davinci', target_mb=380,
            reference=None):
    """Capture the exact files and commands that the user will review."""
    if preset not in upload_presets.PRESETS:
        raise videotool.VideoToolError('Choose a preparation preset.')
    target_mb = upload_presets.size_target(target_mb) if preset == 'aistudio' else 380
    extension = '.mov' if preset == 'davinci' else '.mp4'
    location = location.strip()
    if location and (location in ('.', '..') or
                     any(c in '<>:"/\\|?*' or ord(c) < 32 for c in location)):
        raise videotool.VideoToolError('Enter a location name without slashes or special filename characters.')
    if folder:
        sources, target = videotool.discover_batch(source, destination, include_intermediates=preset != 'davinci')
        sources = [p for p in sources if not p.name.lower().endswith(('_aistudio.mp4', '_youtube.mp4'))]
        if location:
            # Do not feed this location's earlier numbered exports back into a batch.
            exported = re.compile(re.escape(location) + r'_\d{3,}' + re.escape(extension), re.IGNORECASE)
            sources = [p for p in sources if not exported.fullmatch(p.name)]
            if not sources:
                raise videotool.VideoToolError('No source videos remain after excluding this location’s numbered outputs.')
    else:
        sources = [videotool.validate_source(source)]
        target = Path(destination).expanduser().resolve() if destination else sources[0].parent
    settings = (videotool.davinci_settings_from_reference(reference)
                if preset == 'davinci' and reference else None)
    try:
        receipts = conversion_history.read(target)
        known_outputs = {r.get('output') for r in receipts
                         if ((preset == 'davinci' and r.get('preset', 'davinci') == 'davinci') or
                             (preset != 'davinci' and r.get('preset', 'davinci') != 'davinci'))}
        if folder and target == Path(source).expanduser().resolve():
            sources = [p for p in sources if p.name not in known_outputs]
        reserved = {p.name.casefold() for p in target.iterdir()}
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise videotool.VideoToolError(f'Cannot read output folder/history: {exc}') from exc
    items = []
    for source in sources:
        source_settings = settings
        source_metadata = None
        source_fingerprint = None
        if preset == 'davinci' and source_settings is None:
            try:
                source_fingerprint = fingerprint(source)
                source_metadata = videotool.inspect_video(source)
                source_settings = videotool.davinci_settings_for_source(source_metadata)
                if source_fingerprint != fingerprint(source):
                    raise videotool.VideoToolError('Source changed during inspection. Preview again.')
            except (videotool.VideoToolError, OSError):
                source_settings = None
                source_metadata = None
                source_fingerprint = None
        format_key = (f"{source_settings['encoder_profile']}:{source_settings['pixel_format']}:"
                      f"{source_settings['audio_codec']}" if source_settings else '')
        try:
            completed_output = conversion_history.matching(
                receipts, source, fingerprint(source), location, target, fingerprint, preset, target_mb,
                format_key)
        except OSError:
            completed_output = None
        if completed_output:
            items.append(Conversion(source, completed_output, [], completed=True,
                                    location=location, outcome='Done', preset=preset, target_mb=target_mb,
                                    davinci_settings=source_settings, format_key=format_key,
                                    detail='Already completed in the selected format; preserved.'))
            continue
        name = source.name if folder else source.stem
        output = allocate_output(target, location, reserved, extension) if location else target / (name + '_' + preset + extension)
        items.append(plan_item(source, output, location, preset, target_mb, source_settings,
                               source_metadata, source_fingerprint))
    return items


def allocate_output(folder, location, reserved, extension='.mov'):
    pattern = re.compile(re.escape(location) + r'_(\d+)' + re.escape(extension), re.IGNORECASE)
    highest = max((int(match.group(1)) for name in reserved
                   if (match := pattern.fullmatch(name))), default=0)
    name = f'{location}_{highest + 1:03d}{extension}'
    reserved.add(name.casefold())
    return folder / name


def plan_item(source, output, location='', preset='davinci', target_mb=380, davinci_settings=None,
              metadata=None, source_fingerprint=None):
    item = Conversion(source, output, [], location=location, preset=preset, target_mb=target_mb)
    try:
        before = source_fingerprint or fingerprint(source)
        output = videotool.validate_output(source, output)
        metadata = metadata or videotool.inspect_video(source)
        if preset == 'davinci':
            item.davinci_settings = davinci_settings or videotool.davinci_settings_for_source(metadata)
            item.format_key = (f"{item.davinci_settings['encoder_profile']}:"
                               f"{item.davinci_settings['pixel_format']}:"
                               f"{item.davinci_settings['audio_codec']}")
            command = videotool.plan_davinci(source, output, metadata, item.davinci_settings)
            item.estimated_bytes = videotool.estimate_davinci_size(metadata, item.davinci_settings)
            item.available_bytes = videotool.available_space(output)
        else:
            item.upload_plan = upload_presets.plan(source, output, metadata, preset, target_mb)
            command = item.upload_plan.command
        if before != fingerprint(source):
            raise videotool.VideoToolError('Source changed during inspection. Preview again.')
        video = videotool.select_video(metadata)
        audio = sum(s.get('codec_type') == 'audio' for s in metadata['streams'])
        item.output = output
        item.command = command
        item.duration = duration_seconds(metadata)
        item.fingerprint = before
        if preset == 'davinci':
            settings = item.davinci_settings
            origin = (f"Learned from working reference: {settings['reference']}"
                      if settings.get('reference') else
                      f"Automatic: source is {videotool.source_bit_depth(metadata)}-bit")
            item.detail = (f"{video['width']} × {video['height']} · "
                           f"{videotool.frame_rate(video.get('avg_frame_rate'))} fps average\n"
                           f"{settings['name']} · {settings['bit_depth']}-bit 4:2:2 · "
                           f"{audio} audio stream(s), PCM {settings['audio_bits']}-bit\n"
                           f"{origin}\nEstimated output: {videotool.readable_size(item.estimated_bytes)} "
                           f"(10% headroom) · Free space: {videotool.readable_size(item.available_bytes)}\n"
                           'Source frame timing, resolution, and known color tags retained.')
        if item.upload_plan:
            item.detail = item.upload_plan.detail
    except (videotool.VideoToolError, OSError) as exc:
        item.error = str(exc)
    return item


def retry_preview(items):
    """Preserve successes; rebuild unfinished plans and retain partial outputs."""
    reserved = {}
    for item in items:
        folder = item.output.parent
        if folder not in reserved:
            reserved[folder] = {p.name.casefold() for p in folder.iterdir()}
        reserved[folder].add(item.output.name.casefold())
    result = []
    for item in items:
        if item.completed:
            result.append(replace(item))
            continue
        output = item.output
        if output.exists() or output.is_symlink():
            if item.location:
                output = allocate_output(output.parent, item.location, reserved[output.parent], output.suffix)
            else:
                number = 1
                while True:
                    name = f'{item.output.stem}_retry_{number:03d}_{item.preset}{item.output.suffix}'
                    if name.casefold() not in reserved[output.parent]:
                        reserved[output.parent].add(name.casefold())
                        output = output.parent / name
                        break
                    number += 1
        result.append(plan_item(item.source, output, item.location, item.preset, item.target_mb,
                                item.davinci_settings))
    return result


def completion_summary(items):
    completed = sum(item.completed for item in items)
    failed = sum(not item.completed and (bool(item.error) or item.outcome == 'Failed') for item in items)
    remaining = len(items) - completed - failed
    title = 'Conversion complete' if not failed and not remaining else 'Conversion needs attention'
    folders = sorted({str(item.output.parent) for item in items})
    summary = f'{title}: {completed} completed · {failed} failed/blocked · {remaining} unfinished.\nOutput: ' + ', '.join(folders)
    if any(item.preset != 'davinci' for item in items):
        summary += '\nCompleted files are for manual upload. Nothing has been uploaded.'
    return summary


def storage_summary(items):
    ready = [item for item in items if not item.error and not item.completed and item.preset == 'davinci']
    if not ready:
        return ''
    estimates = [item.estimated_bytes for item in ready]
    estimated = sum(estimates) if all(value is not None for value in estimates) else None
    free = min((item.available_bytes for item in ready if item.available_bytes is not None), default=None)
    text = f' Estimated output: {videotool.readable_size(estimated)}; free space: {videotool.readable_size(free)}.'
    if estimated is not None and free is not None and estimated > free:
        text += ' Estimated output exceeds available space; conversion will be refused.'
    return text


def open_output_folder(folder):
    folder = Path(folder).resolve(strict=True)
    if not folder.is_dir():
        raise OSError('Output folder is no longer available.')
    if sys.platform == 'win32':
        os.startfile(str(folder))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(folder)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def convert(items, stop, report, report_progress=None):
    """Run only the reviewed snapshot; stop requests take effect between files."""
    succeeded = 0
    failed = sum(bool(item.error) for item in items)
    ready = [(index, item) for index, item in enumerate(items) if not item.error and not item.completed]
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
            upload_message = ''
            if item.preset == 'davinci':
                videotool.require_space(item.output, item.estimated_bytes)
                videotool.execute_davinci(item.source, item.output, item.command, update)
            else:
                upload_message = upload_presets.execute(item.upload_plan, update)
            update(item.duration, complete=True)
            completed_duration += item.duration
            succeeded += 1
            item.completed = True
            item.outcome = 'Done'
            message = str(item.output) + ('\n' + upload_message if upload_message else '')
            try:
                conversion_history.save(item, fingerprint(item.output))
            except (OSError, sqlite3.Error) as exc:
                message += f'\nCompleted, but history could not be saved: {exc}. Keep this window open to retry other files.'
            report(index, 'Done', message)
        except (videotool.VideoToolError, OSError) as exc:
            failed += 1
            conversion_failed = True
            item.outcome = 'Failed'
            report(index, 'Failed', str(exc))
    for index, item in ready[attempted:]:
        item.outcome = 'Not attempted'
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
    root.title('VideoTool — Prepare videos')
    root.geometry('1100x960')
    root.minsize(1000, 900)
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
    ttk.Label(body, text='Prepare your videos', style='Title.TLabel').grid(sticky='w')
    ttk.Label(body, text='Choose footage → Preview → Convert').grid(row=1, sticky='w', pady=(6, 18))
    source = tk.StringVar(value='Choose a video or a folder to begin')
    destination = tk.StringVar(value='Save beside the original files')
    reference_text = tk.StringVar(value='Automatic: preserve 8-bit or 10-bit')
    location = tk.StringVar()
    preset_name = tk.StringVar(value=upload_presets.PRESETS['davinci'])
    target_size = tk.StringVar(value='380')
    preset_note = tk.StringVar()
    naming_note = tk.StringVar()
    status = tk.StringVar(value='Ready to choose footage.')
    state = {'source': None, 'folder': False, 'destination': None, 'items': [], 'busy': False,
             'messages': {}, 'reference': None}
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
        retry_button.configure(state='disabled')
        open_button.configure(state='disabled')
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

    def choose_reference():
        selected = filedialog.askopenfilename(parent=root, title='Choose a working DNxHR MOV',
                                              filetypes=[('MOV video', '*.mov'), ('All files', '*')])
        if selected:
            state['reference'] = selected
            reference_text.set(selected)
            invalidate()

    def reset_reference():
        state['reference'] = None
        reference_text.set('Automatic: preserve 8-bit or 10-bit')
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
    ttk.Label(naming, textvariable=naming_note).pack(side='left')
    ttk.Entry(selection, textvariable=destination, state='readonly').grid(row=1, column=0, sticky='ew', padx=(0, 8), pady=8)
    for column, (label, callback) in enumerate([('Output folder', choose_destination), ('Use originals’ folder', reset_destination)], 1):
        button = ttk.Button(selection, text=label, command=callback)
        button.grid(row=1, column=column, padx=4)
        controls.append(button)
    preset_row = ttk.Frame(selection)
    preset_row.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(0, 8))
    ttk.Label(preset_row, text='Prepare for').pack(side='left', padx=(0, 10))
    preset_choice = ttk.Combobox(preset_row, textvariable=preset_name, values=list(upload_presets.PRESETS.values()), state='readonly', width=25)
    preset_choice.pack(side='left', padx=(0, 16))
    ttk.Label(preset_row, text='AI Studio target (MB)').pack(side='left', padx=(0, 10))
    size_entry = ttk.Entry(preset_row, textvariable=target_size, width=10)
    size_entry.pack(side='left')
    controls.extend([preset_choice, size_entry])
    Tooltip(preset_choice, 'Choose an editing copy, or an MP4 copy of a finished SDR export for manual upload.')
    Tooltip(size_entry, 'Maximum target per file in decimal MB (1–100000). Default 380 MB is your workflow target, not a verified AI Studio limit. Actual size is checked.')
    reference_row = ttk.Frame(selection)
    reference_row.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(0, 8))
    reference_row.columnconfigure(0, weight=1)
    reference_entry = ttk.Entry(reference_row, textvariable=reference_text, state='readonly')
    reference_entry.grid(row=0, column=0, sticky='ew', padx=(0, 8))
    reference_button = ttk.Button(reference_row, text='Choose reference', command=choose_reference)
    reference_button.grid(row=0, column=1, padx=4)
    default_button = ttk.Button(reference_row, text='Use automatic', command=reset_reference)
    default_button.grid(row=0, column=2, padx=4)
    controls.extend([reference_button, default_button])
    reference_controls = [reference_entry, reference_button, default_button]
    def mode_changed(*args):
        mode = selected_preset()
        notes = {
            'davinci': 'Keeps 8-bit footage at 8-bit with DNxHR SQ and 10-bit footage at 10-bit with HQX.\nPreview shows estimated output and free space. Originals are kept.',
            'aistudio': 'Finished SDR exports → smaller MP4 copies, up to 1920 pixels on the longest edge.\nYour size target is checked after encoding. No five-minute rule. Upload manually to AI Studio.',
            'youtube': 'Finished SDR exports → high-quality H.264/AAC MP4 copies at source resolution.\nNo size cap. Check picture and sound, then upload manually to YouTube.',
        }
        preset_note.set(notes[mode])
        extension = 'mov' if mode == 'davinci' else 'mp4'
        naming_note.set(f'Dublin → Dublin_001.{extension}, Dublin_002.{extension}')
        size_entry.configure(state='normal' if mode == 'aistudio' and not state['busy'] else 'disabled')
        for widget in reference_controls:
            widget.configure(state=('readonly' if widget is reference_entry else 'normal')
                             if mode == 'davinci' and not state['busy'] else 'disabled')
        invalidate()

    def selected_preset():
        return next(key for key, label in upload_presets.PRESETS.items() if label == preset_name.get())

    ttk.Label(body, text='Folder mode scans this folder only. Existing outputs are preserved.').grid(row=3, sticky='w', pady=(0, 12))
    ttk.Label(body, textvariable=preset_note).grid(row=4, sticky='w', pady=(0, 14))
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
            button.configure(state='disabled' if value else ('readonly' if isinstance(button, ttk.Combobox) else 'normal'))
        size_entry.configure(state='normal' if not value and selected_preset() == 'aistudio' else 'disabled')
        for widget in reference_controls:
            widget.configure(state=('readonly' if widget is reference_entry else 'normal')
                             if not value and selected_preset() == 'davinci' else 'disabled')
        start_button.configure(state='disabled')
        retry_button.configure(state='disabled')
        open_button.configure(state='disabled')
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
        args = (state['source'], state['folder'], state['destination'], location.get(), selected_preset(),
                target_size.get(), state['reference'])
        def worker():
            try:
                events.put(('preview', preview(*args)))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def do_retry():
        busy(True)
        status.set('Preparing unfinished files for review… Successful files will be kept.')
        items = tuple(state['items'])
        def worker():
            try:
                events.put(('preview', retry_preview(items)))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def open_folder():
        selected = tree.selection()
        item = state['items'][int(selected[0])] if selected else state['items'][0]
        try:
            open_output_folder(item.output.parent)
        except OSError as exc:
            messagebox.showerror('Cannot open output folder', str(exc), parent=root)

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

    retry_button = ttk.Button(actions, text='Preview retry', command=do_retry, state='disabled')
    retry_button.pack(side='left', padx=5)
    open_button = ttk.Button(body, text='Open output folder', command=open_folder, state='disabled')
    open_button.grid(row=10, sticky='e', pady=(8, 0))

    for button in [*controls, start_button, stop_button, retry_button, open_button]:
        if isinstance(button, ttk.Button):
            button.tooltip = Tooltip(button, BUTTON_HINTS[str(button.cget('text'))])

    def poll():
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == 'preview':
                    state['items'] = data
                    tree.delete(*tree.get_children())
                    state['messages'] = {}
                    for index, item in enumerate(data):
                        tree.insert('', 'end', iid=str(index), values=(item.source.name, item.output.name, 'Done' if item.completed else ('Blocked' if item.error else 'Ready')))
                    ready = sum(not item.error and not item.completed for item in data)
                    completed = sum(item.completed for item in data)
                    busy(False)
                    file_progress_text.set('Current file: ready to convert' if ready else 'Current file: no ready files')
                    status.set(f'{ready} ready · {len(data) - ready - completed} blocked · {completed} already completed.'
                               + storage_summary(data) + ' Review the list, then convert ready files.')
                    if ready:
                        start_button.configure(state='normal')
                    if data:
                        open_button.configure(state='normal')
                        retry_button.configure(state='normal' if any(not item.completed for item in data) else 'disabled')
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
                    status.set(completion_summary(state['items']))
                    open_button.configure(state='normal')
                    retry_button.configure(state='normal' if any(not item.completed for item in state['items']) else 'disabled')
                elif kind == 'error':
                    busy(False)
                    file_progress_text.set('Current file: operation failed')
                    batch_progress_text.set('Whole batch: stopped')
                    status.set('Unable to complete this operation. Preview retry to keep completed files.')
                    if state['items']:
                        retry_button.configure(state='normal')
                        open_button.configure(state='normal')
                    messagebox.showerror('VideoTool', data, parent=root)
        except queue.Empty:
            pass
        state['poll_timer'] = root.after(100, poll)

    def close():
        if state['busy']:
            messagebox.showinfo('Work in progress', 'Wait for the operation to finish before closing. During conversion, you can stop after the current file.', parent=root)
        else:
            root.destroy()
    def cleanup(event):
        if event.widget == root and state.get('poll_timer') is not None:
            root.after_cancel(state.pop('poll_timer'))

    root.bind('<Destroy>', cleanup, add='+')
    root.protocol('WM_DELETE_WINDOW', close)
    location.trace_add('write', lambda *args: invalidate())
    preset_name.trace_add('write', mode_changed)
    target_size.trace_add('write', lambda *args: invalidate())
    mode_changed()
    poll()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
