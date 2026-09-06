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
from urllib.parse import unquote, urlparse
import conversion_history
import processing_receipts
from tooltips import Tooltip

import videotool
import upload_presets


BUTTON_HINTS = {
    'Choose source': 'Choose one video file or a folder of videos. Folder subdirectories are not included.',
    'Choose reference': 'Choose a DNxHR MOV that you have already confirmed works in DaVinci.',
    'Use automatic': 'Choose DNxHR SQ for 8-bit footage and DNxHR HQX for 10-bit footage.',
    'Output folder': 'Choose an existing folder for the converted videos.',
    'Use originals’ folder': 'Save converted videos beside their source files.',
    'Preview': 'Check your footage and show the proposed output names. No videos are converted.',
    'Stop after current file': 'Let the current video finish, then stop. You can retry the unfinished files later.',
    'Cancel current conversion': 'Terminate the active conversion and stop the batch. Any partial output is kept and will not be overwritten.',
    'Convert ready files': 'Start converting the ready files shown in the preview. Completed files are kept.',
    'Preview retry': 'Prepare failed or unfinished files for review. Successful files are kept and partial files are not overwritten.',
    'Open output folder': 'Open the output folder in your file manager. If a row is selected, open that video’s output folder.',
    'View processing receipt': 'Open the Markdown record for the latest completed conversion run.',
    'Copy summary': 'Copy the latest run counts, elapsed time, and receipt location.',
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
    source_metadata: object = None
    output_metadata: object = None
    elapsed_seconds: object = None
    batch_elapsed_seconds: object = None
    executed_commands: list = None
    validation_result: str = ''
    receipt_path: object = None
    attempted_this_run: bool = False
    completed_this_run: bool = False

    def __post_init__(self):
        if self.executed_commands is None:
            self.executed_commands = []


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


def review_duration(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return '—'
    if not seconds or not math.isfinite(seconds):
        return '—'
    seconds = max(0, round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'


def review_bit_depth(item):
    if item.source_metadata:
        try:
            return f'{videotool.source_bit_depth(item.source_metadata)}-bit'
        except videotool.VideoToolError:
            pass
    if item.davinci_settings and item.davinci_settings.get('bit_depth'):
        return f"{item.davinci_settings['bit_depth']}-bit"
    return '—'


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
            matched = conversion_history.matching_record(
                receipts, source, fingerprint(source), location, target, fingerprint, preset, target_mb,
                format_key)
        except OSError:
            matched = None
        if matched:
            completed_output, record = matched
            receipt_name = record.get('processing_receipt')
            receipt_path = target / receipt_name if receipt_name and Path(receipt_name).name == receipt_name else None
            if receipt_path and (receipt_path.is_symlink() or not receipt_path.is_file()):
                receipt_path = None
            items.append(Conversion(source, completed_output, [], completed=True,
                                    location=location, outcome='Done', preset=preset, target_mb=target_mb,
                                    davinci_settings=source_settings, format_key=format_key,
                                    receipt_path=receipt_path,
                                    elapsed_seconds=record.get('elapsed_seconds'),
                                    duration=record.get('duration_seconds') or 0,
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
        item.source_metadata = metadata
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


def open_processing_receipt(path):
    candidate = Path(path)
    if candidate.is_symlink():
        raise OSError('The processing receipt is a symbolic link.')
    path = candidate.resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != '.md':
        raise OSError('The processing receipt is not an available Markdown file.')
    if sys.platform == 'win32':
        os.startfile(str(path))
    else:
        subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def convert(items, stop, report, report_progress=None, cancel=None, receipt_report=None):
    """Run only the reviewed snapshot; stop requests take effect between files."""
    succeeded = 0
    failed = sum(bool(item.error) for item in items)
    ready = [(index, item) for index, item in enumerate(items) if not item.error and not item.completed]
    attempted = 0
    total_duration = sum(item.duration for _, item in ready) if all(item.duration > 0 for _, item in ready) else 0
    completed_duration = 0
    batch_start = time.monotonic()
    conversion_failed = False
    interrupted = 0
    for item in items:
        item.attempted_this_run = False
        item.completed_this_run = False
        item.batch_elapsed_seconds = None
    for index, item in ready:
        if stop.is_set() or (cancel is not None and cancel.is_set()):
            break
        attempted += 1
        item.attempted_this_run = True
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
                item.executed_commands = [videotool.command_with_progress(item.command)]
                if cancel is None:
                    videotool.execute_davinci(item.source, item.output, item.command, update)
                else:
                    videotool.execute_davinci(item.source, item.output, item.command, update,
                                              cancel=cancel)
            else:
                upload_message = (upload_presets.execute(item.upload_plan, update) if cancel is None else
                                  upload_presets.execute(item.upload_plan, update, cancel))
            item.elapsed_seconds = time.monotonic() - file_start
            try:
                item.output_metadata = videotool.inspect_video(item.output)
                item.validation_result = (
                    'FFmpeg completed cleanly and output metadata is readable. '
                    'Resolve compatibility has not been manually confirmed.'
                    if item.preset == 'davinci' else
                    'Codec, pixel format, dimensions, audio, duration, and requested size checks passed. '
                    'External upload acceptance has not been tested.')
            except (videotool.VideoToolError, OSError) as exc:
                item.validation_result = f'Encoding completed; output metadata inspection unavailable: {exc}'
            if item.preset != 'davinci':
                item.executed_commands = list(item.upload_plan.executed_commands)
            update(item.duration, complete=True)
            completed_duration += item.duration
            succeeded += 1
            item.completed = True
            item.completed_this_run = True
            item.outcome = 'Done'
            message = str(item.output) + ('\n' + upload_message if upload_message else '')
            try:
                conversion_history.save(item, fingerprint(item.output))
            except (OSError, sqlite3.Error) as exc:
                message += f'\nCompleted, but history could not be saved: {exc}. Keep this window open to retry other files.'
            report(index, 'Done', message)
        except videotool.ConversionCancelled as exc:
            item.elapsed_seconds = time.monotonic() - file_start
            if item.preset != 'davinci':
                item.executed_commands = list(item.upload_plan.executed_commands)
            interrupted = 1
            conversion_failed = True
            item.outcome = 'Interrupted'
            report(index, 'Interrupted', str(exc))
            break
        except (videotool.VideoToolError, OSError) as exc:
            item.elapsed_seconds = time.monotonic() - file_start
            if item.preset != 'davinci':
                item.executed_commands = list(item.upload_plan.executed_commands)
            failed += 1
            conversion_failed = True
            item.outcome = 'Failed'
            report(index, 'Failed', str(exc))
    for index, item in ready[attempted:]:
        item.outcome = 'Not attempted'
        report(index, 'Not attempted', 'Stopped before this file started.')
    batch_finished = time.monotonic()
    for item in items:
        item.batch_elapsed_seconds = batch_finished - batch_start
    try:
        receipt_path = processing_receipts.write(items, batch_start, batch_finished)
    except OSError as exc:
        if receipt_report:
            receipt_report(None, processing_receipts.summary(items),
                           f'Videos completed, but the processing receipt could not be saved: {exc}')
    else:
        if receipt_path:
            history_errors = []
            for item in items:
                item.receipt_path = receipt_path
                if item.completed_this_run:
                    try:
                        conversion_history.save(item, fingerprint(item.output))
                    except (OSError, sqlite3.Error) as exc:
                        history_errors.append(str(exc))
            warning = (f'Processing receipt saved, but its history link could not be saved: '
                       f'{history_errors[0]}' if history_errors else '')
            if receipt_report:
                receipt_report(receipt_path, processing_receipts.summary(items, receipt_path), warning)
    return succeeded, failed, len(ready) - attempted + interrupted


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
    root.geometry('1080x960')
    root.minsize(860, 720)
    root.configure(background='#f3f5f7')
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('TFrame', background='#f3f5f7')
    style.configure('TLabel', background='#f3f5f7', font=('Sans', 11))
    style.configure('Title.TLabel', font=('Sans', 24, 'bold'), foreground='#17212b')
    style.configure('Subtitle.TLabel', foreground='#59636e')
    style.configure('Section.TLabelframe', background='#f3f5f7', borderwidth=1, relief='solid')
    style.configure('Section.TLabelframe.Label', background='#f3f5f7', foreground='#17212b',
                    font=('Sans', 11, 'bold'))
    style.configure('Field.TLabel', foreground='#36414c', font=('Sans', 10, 'bold'))
    style.configure('Muted.TLabel', foreground='#64707c', font=('Sans', 10))
    style.configure('Status.TLabel', background='#e7eef2', foreground='#263540',
                    font=('Sans', 10), padding=(12, 9))
    style.configure('TButton', font=('Sans', 11), padding=(12, 8))
    style.configure('Treeview', font=('Sans', 10), rowheight=30)
    style.configure('Treeview.Heading', font=('Sans', 10, 'bold'))
    style.configure('Accent.TButton', background='#16665b', foreground='white')
    style.map('Accent.TButton', background=[('active', '#0f574e'), ('disabled', '#9aafab')])
    style.configure('Danger.TButton', foreground='#9c2f2f')
    body = ttk.Frame(root, padding=(22, 18))
    body.pack(fill='both', expand=True)
    body.columnconfigure(0, weight=1)
    body.rowconfigure(3, weight=1)
    ttk.Label(body, text='Prepare your videos', style='Title.TLabel').grid(sticky='w')
    ttk.Label(body, text='Choose a format and footage, review the files, then convert.',
              style='Subtitle.TLabel').grid(row=1, sticky='w', pady=(3, 14))
    source = tk.StringVar(value='Choose a video or a folder to begin')
    destination = tk.StringVar(value='Save beside the original files')
    reference_text = tk.StringVar(value='Automatic: preserve 8-bit or 10-bit')
    location = tk.StringVar()
    preset_name = tk.StringVar(value=upload_presets.PRESETS['davinci'])
    target_size = tk.StringVar(value='380')
    advanced_reference = tk.BooleanVar(value=False)
    preset_note = tk.StringVar()
    naming_note = tk.StringVar()
    source_note = tk.StringVar(value='Folder mode scans one folder. Existing outputs are preserved.')
    status = tk.StringVar(value='Ready to choose footage.')
    state = {'source': None, 'folder': False, 'destination': None, 'items': [], 'busy': False,
             'messages': {}, 'reference': None, 'receipt': None, 'run_summary': '',
             'receipt_error': ''}
    events = queue.Queue()
    stop = threading.Event()
    cancel = threading.Event()
    controls = []

    def invalidate():
        state['items'] = []
        state['messages'] = {}
        state['receipt'] = None
        state['run_summary'] = ''
        state['receipt_error'] = ''
        tree.delete(*tree.get_children())
        show_details()
        file_progress_text.set('Current file: waiting')
        batch_progress_text.set('Whole batch: waiting')
        progress['value'] = 0
        batch_progress['value'] = 0
        progress_frame.grid_remove()
        start_button.configure(state='disabled')
        retry_button.configure(state='disabled')
        retry_button.pack_forget()
        open_button.configure(state='disabled')
        result_actions.grid_remove()
        view_receipt_button.grid_remove()
        copy_summary_button.grid_remove()
        open_button.grid_remove()
        view_receipt_button.configure(state='disabled')
        copy_summary_button.configure(state='disabled')
        status.set('Preview your selection before converting.')

    def set_source(selected, folder):
        selected = str(selected)
        state.update(source=selected, folder=folder)
        source.set(('Folder: ' if folder else 'Video: ') + selected)
        invalidate()

    def choose_source(folder):
        selected = filedialog.askdirectory(parent=root, title='Choose footage folder') if folder else filedialog.askopenfilename(parent=root, title='Choose video')
        if selected:
            set_source(selected, folder)

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

    setup = ttk.Frame(body)
    setup.grid(row=2, sticky='ew', pady=(0, 10))
    setup.columnconfigure(0, weight=1, uniform='setup')
    setup.columnconfigure(1, weight=1, uniform='setup')

    workflow = ttk.LabelFrame(setup, text='1  Choose a format', style='Section.TLabelframe',
                              padding=(14, 10))
    workflow.grid(row=0, column=0, sticky='nsew', padx=(0, 5))
    workflow.columnconfigure(1, weight=1)
    ttk.Label(workflow, text='Prepare for', style='Field.TLabel').grid(row=0, column=0, sticky='w',
                                                                      padx=(0, 12))
    preset_choice = ttk.Combobox(workflow, textvariable=preset_name,
                                 values=list(upload_presets.PRESETS.values()), state='readonly', width=25)
    preset_choice.grid(row=0, column=1, sticky='w')
    controls.append(preset_choice)
    Tooltip(preset_choice, 'Choose an editing copy, or an MP4 copy of a finished SDR export for manual upload.')

    option_area = ttk.Frame(workflow)
    option_area.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(9, 0))
    option_area.columnconfigure(1, weight=1)
    target_options = ttk.Frame(option_area)
    target_options.grid(row=0, column=0, sticky='ew')
    ttk.Label(target_options, text='Maximum file size', style='Field.TLabel').pack(side='left', padx=(0, 12))
    size_entry = ttk.Entry(target_options, textvariable=target_size, width=10)
    size_entry.pack(side='left')
    ttk.Label(target_options, text='MB per video', style='Muted.TLabel').pack(side='left', padx=(7, 0))
    controls.append(size_entry)
    Tooltip(size_entry, 'Maximum target per file in decimal MB (1–100000). Default 380 MB is your workflow target, not a verified AI Studio limit. Actual size is checked.')

    davinci_options = ttk.Frame(option_area)
    davinci_options.grid(row=0, column=0, sticky='ew')
    davinci_options.columnconfigure(0, weight=1)
    automatic_row = ttk.Frame(davinci_options)
    automatic_row.grid(row=0, column=0, sticky='ew')
    ttk.Label(automatic_row, text='Automatic: preserve source bit depth',
              style='Field.TLabel').pack(side='left')
    advanced_toggle = ttk.Checkbutton(automatic_row, text='Advanced settings',
                                      variable=advanced_reference)
    advanced_toggle.pack(side='right', padx=(12, 0))
    controls.append(advanced_toggle)
    Tooltip(advanced_toggle, 'Use settings learned from a DNxHR MOV that already works in DaVinci.')

    reference_options = ttk.Frame(davinci_options)
    reference_options.grid(row=1, column=0, sticky='ew', pady=(8, 0))
    reference_options.columnconfigure(1, weight=1)
    ttk.Label(reference_options, text='DaVinci format', style='Field.TLabel').grid(row=0, column=0,
                                                                                  padx=(0, 12))
    reference_entry = ttk.Entry(reference_options, textvariable=reference_text, state='readonly')
    reference_entry.grid(row=0, column=1, columnspan=2, sticky='ew')
    reference_button = ttk.Button(reference_options, text='Choose reference', command=choose_reference)
    reference_button.grid(row=1, column=1, sticky='w', pady=(6, 0))
    default_button = ttk.Button(reference_options, text='Use automatic', command=reset_reference)
    default_button.grid(row=1, column=2, sticky='w', padx=(6, 0), pady=(6, 0))
    controls.extend([reference_button, default_button])
    reference_controls = [reference_entry, reference_button, default_button]
    ttk.Label(workflow, textvariable=preset_note, style='Muted.TLabel', justify='left',
              wraplength=450).grid(
        row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))

    selection = ttk.LabelFrame(setup, text='2  Choose source and destination',
                               style='Section.TLabelframe', padding=(14, 10))
    selection.grid(row=0, column=1, sticky='nsew', padx=(5, 0))
    selection.columnconfigure(1, weight=1)
    ttk.Label(selection, text='Source', style='Field.TLabel').grid(row=0, column=0, sticky='w', padx=(0, 12))
    ttk.Entry(selection, textvariable=source, state='readonly').grid(row=0, column=1, columnspan=2,
                                                                     sticky='ew')
    source_button = ttk.Menubutton(selection, text='Choose source')
    source_button.grid(row=1, column=1, sticky='w', pady=(6, 0))
    source_menu = tk.Menu(source_button, tearoff=False)
    source_menu.add_command(label='Video file', command=lambda: choose_source(False))
    source_menu.add_command(label='Folder', command=lambda: choose_source(True))
    source_button.configure(menu=source_menu)
    controls.append(source_button)
    ttk.Label(selection, text='Destination', style='Field.TLabel').grid(row=2, column=0, sticky='w',
                                                                        padx=(0, 12), pady=(8, 0))
    ttk.Entry(selection, textvariable=destination, state='readonly').grid(row=2, column=1, columnspan=2,
                                                                          sticky='ew', pady=(8, 0))
    for column, (label, callback) in enumerate([('Output folder', choose_destination), ('Use originals’ folder', reset_destination)], 1):
        button = ttk.Button(selection, text=label, command=callback)
        button.grid(row=3, column=column, sticky='w', padx=(0 if column == 1 else 4, 0),
                    pady=(6, 0))
        controls.append(button)
    naming = ttk.Frame(selection)
    naming.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(8, 0))
    naming.columnconfigure(1, weight=1)
    ttk.Label(naming, text='Name files by location', style='Field.TLabel').grid(row=0, column=0,
                                                                                sticky='w', padx=(0, 12))
    location_entry = ttk.Entry(naming, textvariable=location, width=25)
    location_entry.grid(row=0, column=1, sticky='ew')
    controls.append(location_entry)
    ttk.Label(naming, textvariable=naming_note, style='Muted.TLabel').grid(row=1, column=1,
                                                                          sticky='w', pady=(3, 0))
    ttk.Label(selection, textvariable=source_note, style='Muted.TLabel', wraplength=450).grid(
        row=5, column=0, columnspan=3, sticky='w', pady=(7, 0))

    def receive_drop(data):
        try:
            candidates = root.tk.splitlist(data)
            if not candidates:
                return 'refuse_drop'
            raw = candidates[0]
            parsed = urlparse(raw)
            if parsed.scheme == 'file':
                raw = unquote(parsed.path)
            candidate = Path(raw).expanduser().resolve(strict=True)
            if not candidate.is_file() and not candidate.is_dir():
                raise OSError('Drop one video file or one folder.')
            set_source(candidate, candidate.is_dir())
            if len(candidates) > 1:
                status.set('Using the first dropped item. VideoTool accepts one file or one folder at a time.')
            return 'copy'
        except (OSError, ValueError) as exc:
            messagebox.showerror('Cannot use dropped source', str(exc), parent=root)
            return 'refuse_drop'

    try:
        root.tk.call('package', 'require', 'tkdnd')
        root.tk.call('tkdnd::drop_target', 'register', root._w, 'DND_Files')
        drop_command = root.register(receive_drop)
        root.tk.call('bind', root._w, '<<Drop:DND_Files>>', f'{drop_command} %D')
        source_note.set('Drop a video or folder onto this window, or use Choose source. '
                        'Folder subdirectories are not included.')
    except tk.TclError:
        pass

    def update_naming_note():
        name = location.get().strip()
        extension = 'mov' if selected_preset() == 'davinci' else 'mp4'
        naming_note.set(f'{name} → {name}_001.{extension}' if name else
                        'Optional — leave blank to use the source filename.')

    def show_advanced_reference(*args):
        if selected_preset() == 'davinci' and advanced_reference.get():
            reference_options.grid()
        else:
            reference_options.grid_remove()

    def mode_changed(*args):
        mode = selected_preset()
        notes = {
            'davinci': 'Keeps 8-bit footage at 8-bit with DNxHR SQ and 10-bit footage at 10-bit with HQX.\nPreview shows estimated output and free space. Originals are kept.',
            'aistudio': 'Finished SDR exports → smaller MP4 copies, up to 1920 pixels on the longest edge.\nYour size target is checked after encoding. No five-minute rule. Upload manually to AI Studio.',
            'youtube': 'Finished SDR exports → high-quality H.264/AAC MP4 copies at source resolution.\nNo size cap. Check picture and sound, then upload manually to YouTube.',
        }
        preset_note.set(notes[mode])
        update_naming_note()
        if mode == 'aistudio':
            davinci_options.grid_remove()
            target_options.grid()
        elif mode == 'davinci':
            target_options.grid_remove()
            davinci_options.grid()
            show_advanced_reference()
        else:
            target_options.grid_remove()
            davinci_options.grid_remove()
        size_entry.configure(state='normal' if mode == 'aistudio' and not state['busy'] else 'disabled')
        for widget in reference_controls:
            widget.configure(state=('readonly' if widget is reference_entry else 'normal')
                             if mode == 'davinci' and not state['busy'] else 'disabled')
        invalidate()

    def selected_preset():
        return next(key for key, label in upload_presets.PRESETS.items() if label == preset_name.get())

    review = ttk.LabelFrame(body, text='3  Review files', style='Section.TLabelframe', padding=(10, 8))
    review.grid(row=3, sticky='nsew', pady=(0, 10))
    review.columnconfigure(0, weight=1)
    review.rowconfigure(0, weight=1)
    table_frame = ttk.Frame(review)
    table_frame.grid(row=0, sticky='nsew')
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(table_frame,
                        columns=('source', 'size', 'duration', 'depth', 'output', 'status'),
                        show='headings', selectmode='browse')
    for key, title, width in [('source', 'Source video', 225), ('size', 'Size', 85),
                              ('duration', 'Duration', 80), ('depth', 'Bit depth', 75),
                              ('output', 'Output file', 275), ('status', 'Status', 100)]:
        tree.heading(key, text=title)
        tree.column(key, width=width, minwidth=65,
                    stretch=key in ('source', 'output'))
    tree.tag_configure('Ready', foreground='#16665b')
    tree.tag_configure('Done', foreground='#25723b')
    tree.tag_configure('Converting', foreground='#1f5f99')
    tree.tag_configure('Blocked', foreground='#9c5a16')
    tree.tag_configure('Failed', foreground='#a03030')
    tree.tag_configure('Interrupted', foreground='#a03030')
    tree.tag_configure('Not attempted', foreground='#68737d')
    tree.grid(row=0, column=0, sticky='nsew')
    scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky='ns')
    tree.configure(yscrollcommand=scrollbar.set)
    details = ScrolledText(review, height=4, wrap='word', font=('Sans', 10), relief='flat',
                           borderwidth=1, padx=10, pady=8)
    details.grid(row=1, sticky='ew', pady=(8, 0))
    details.configure(state='disabled')

    def show_details(event=None):
        selected = tree.selection()
        text = ''
        if selected:
            details.grid()
            index = int(selected[0])
            item = state['items'][index]
            text = f'Source: {item.source}\nOutput: {item.output}\n' + (item.error or item.detail)
            if index in state['messages']:
                text += '\n' + state['messages'][index]
        else:
            details.grid_remove()
        details.configure(state='normal')
        details.delete('1.0', 'end')
        details.insert('end', text)
        details.configure(state='disabled')
    tree.bind('<<TreeviewSelect>>', show_details)
    progress_frame = ttk.LabelFrame(body, text='Progress', style='Section.TLabelframe', padding=(12, 8))
    progress_frame.grid(row=4, sticky='ew')
    progress_frame.columnconfigure(0, weight=1)
    file_progress_text = tk.StringVar(value='Current file: waiting')
    batch_progress_text = tk.StringVar(value='Whole batch: waiting')
    ttk.Label(progress_frame, textvariable=file_progress_text).grid(row=0, sticky='w')
    progress = ttk.Progressbar(progress_frame, mode='indeterminate')
    progress.grid(row=1, sticky='ew', pady=(3, 6))
    ttk.Label(progress_frame, textvariable=batch_progress_text).grid(row=2, sticky='w')
    batch_progress = ttk.Progressbar(progress_frame, mode='determinate')
    batch_progress.grid(row=3, sticky='ew', pady=(3, 0))
    ttk.Label(body, textvariable=status, style='Status.TLabel', wraplength=960,
              justify='left').grid(row=5, sticky='ew', pady=(10, 8))
    actions = ttk.Frame(body)
    actions.grid(row=6, sticky='ew')
    actions.columnconfigure(1, weight=1)

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
            interrupt_actions.grid_remove()
            stop_button.configure(state='disabled')
            cancel_button.configure(state='disabled')
            view_receipt_button.configure(state='normal' if state['receipt'] else 'disabled')
            copy_summary_button.configure(state='normal' if state['run_summary'] else 'disabled')

    def do_preview():
        if not state['source']:
            messagebox.showinfo('Choose footage', 'Choose a video or folder first.', parent=root)
            return
        invalidate()
        busy(True)
        progress_frame.grid()
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

    def view_receipt():
        try:
            open_processing_receipt(state['receipt'])
        except (OSError, TypeError) as exc:
            messagebox.showerror('Cannot open processing receipt', str(exc), parent=root)

    def copy_summary():
        if not state['run_summary']:
            return
        root.clipboard_clear()
        root.clipboard_append(state['run_summary'])
        root.update_idletasks()
        status.set('Processing summary copied to the clipboard.')

    def do_convert():
        busy(True)
        progress.stop()
        progress.configure(mode='determinate', value=0)
        batch_progress['value'] = 0
        stop.clear()
        cancel.clear()
        state['receipt'] = None
        state['run_summary'] = ''
        state['receipt_error'] = ''
        interrupt_actions.grid()
        stop_button.configure(state='normal')
        cancel_button.configure(state='normal')
        status.set('Starting conversion…')
        items = tuple(state['items'])
        def worker():
            try:
                result = convert(items, stop, lambda *data: events.put(('progress', data)),
                                 lambda *data: events.put(('percent', data)), cancel,
                                 lambda *data: events.put(('receipt', data)))
                events.put(('done', result))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def request_stop():
        stop.set()
        stop_button.configure(state='disabled')
        status.set('Stopping after the current file finishes…')

    def request_cancel():
        cancel.set()
        stop.set()
        cancel_button.configure(state='disabled')
        stop_button.configure(state='disabled')
        status.set('Cancelling the current conversion… Any partial output will be kept.')

    review_actions = ttk.Frame(actions)
    review_actions.grid(row=0, column=0, sticky='w')
    preview_button = ttk.Button(review_actions, text='Preview', command=do_preview)
    preview_button.pack(side='left', padx=(0, 5))
    controls.append(preview_button)
    retry_button = ttk.Button(review_actions, text='Preview retry', command=do_retry, state='disabled')
    retry_button.pack(side='left', padx=5)
    start_button = ttk.Button(actions, text='Convert ready files', style='Accent.TButton',
                              command=do_convert, state='disabled')
    start_button.grid(row=0, column=2, sticky='e', padx=(10, 0))

    interrupt_actions = ttk.Frame(actions)
    interrupt_actions.grid(row=1, column=0, columnspan=3, sticky='e', pady=(7, 0))
    ttk.Label(interrupt_actions, text='Conversion controls', style='Muted.TLabel').pack(side='left',
                                                                                       padx=(0, 8))
    stop_button = ttk.Button(interrupt_actions, text='Stop after current file', command=request_stop,
                             state='disabled')
    stop_button.pack(side='left', padx=5)
    cancel_button = ttk.Button(interrupt_actions, text='Cancel current conversion',
                               style='Danger.TButton', command=request_cancel, state='disabled')
    cancel_button.pack(side='left', padx=(5, 0))
    interrupt_actions.grid_remove()

    result_actions = ttk.Frame(body)
    result_actions.grid(row=7, sticky='e', pady=(8, 0))
    view_receipt_button = ttk.Button(result_actions, text='View processing receipt',
                                     command=view_receipt, state='disabled')
    view_receipt_button.grid(row=0, column=0, padx=5)
    copy_summary_button = ttk.Button(result_actions, text='Copy summary',
                                     command=copy_summary, state='disabled')
    copy_summary_button.grid(row=0, column=1, padx=5)
    open_button = ttk.Button(result_actions, text='Open output folder', command=open_folder,
                             state='disabled')
    open_button.grid(row=0, column=2, padx=5)
    retry_button.pack_forget()
    result_actions.grid_remove()
    view_receipt_button.grid_remove()
    copy_summary_button.grid_remove()
    open_button.grid_remove()

    for button in [*controls, start_button, stop_button, cancel_button, retry_button,
                   view_receipt_button, copy_summary_button, open_button]:
        if isinstance(button, (ttk.Button, ttk.Menubutton)):
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
                        row_status = 'Done' if item.completed else ('Blocked' if item.error else 'Ready')
                        try:
                            source_size = videotool.readable_size(item.source.stat().st_size)
                        except OSError:
                            source_size = '—'
                        tree.insert('', 'end', iid=str(index),
                                    values=(item.source.name, source_size,
                                            review_duration(item.duration), review_bit_depth(item),
                                            item.output.name, row_status), tags=(row_status,))
                    ready = sum(not item.error and not item.completed for item in data)
                    completed = sum(item.completed for item in data)
                    busy(False)
                    file_progress_text.set('Current file: ready to convert' if ready else 'Current file: no ready files')
                    status.set(f'{ready} ready · {len(data) - ready - completed} blocked · {completed} already completed.'
                               + storage_summary(data) + ' Review the list, then convert ready files.')
                    if ready:
                        start_button.configure(state='normal')
                    if data:
                        receipt_paths = {item.receipt_path for item in data if item.receipt_path}
                        previous_receipt = (max(receipt_paths, key=lambda path: path.name)
                                            if receipt_paths else None)
                        if previous_receipt:
                            state['receipt'] = previous_receipt
                            state['run_summary'] = f'Previous VideoTool processing receipt: {previous_receipt}'
                            view_receipt_button.configure(state='normal')
                            copy_summary_button.configure(state='normal')
                            view_receipt_button.grid()
                            copy_summary_button.grid()
                        open_button.configure(state='normal')
                        result_actions.grid()
                        open_button.grid()
                        if any(not item.completed for item in data):
                            retry_button.configure(state='normal')
                            retry_button.pack(side='left', padx=5)
                        else:
                            retry_button.configure(state='disabled')
                            retry_button.pack_forget()
                        tree.selection_set('0')
                        show_details()
                elif kind == 'progress':
                    index, label, message = data
                    tree.set(str(index), 'status', label)
                    tree.item(str(index), tags=(label,))
                    state['messages'][index] = message
                    if label == 'Converting' and not stop.is_set():
                        status.set(message)
                    elif label == 'Failed':
                        file_progress_text.set('Current file: failed — select the row for details')
                    elif label == 'Interrupted':
                        file_progress_text.set('Current file: cancelled — partial output kept if created')
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
                    message = completion_summary(state['items'])
                    if state['receipt']:
                        message += f'\nProcessing receipt: {state["receipt"]}'
                    if state['receipt_error']:
                        message += f'\n{state["receipt_error"]}'
                    status.set(message)
                    open_button.configure(state='normal')
                    result_actions.grid()
                    open_button.grid()
                    if any(not item.completed for item in state['items']):
                        retry_button.configure(state='normal')
                        retry_button.pack(side='left', padx=5)
                    else:
                        retry_button.configure(state='disabled')
                        retry_button.pack_forget()
                elif kind == 'receipt':
                    receipt_path, run_summary, receipt_error = data
                    state['receipt'] = receipt_path
                    state['run_summary'] = run_summary
                    state['receipt_error'] = receipt_error
                    view_receipt_button.configure(state='normal' if receipt_path else 'disabled')
                    copy_summary_button.configure(state='normal' if run_summary else 'disabled')
                    if receipt_path or run_summary:
                        result_actions.grid()
                    if receipt_path:
                        view_receipt_button.grid()
                    if run_summary:
                        copy_summary_button.grid()
                elif kind == 'error':
                    busy(False)
                    file_progress_text.set('Current file: operation failed')
                    batch_progress_text.set('Whole batch: stopped')
                    status.set('Unable to complete this operation. Preview retry to keep completed files.')
                    if state['items']:
                        retry_button.configure(state='normal')
                        retry_button.pack(side='left', padx=5)
                        open_button.configure(state='normal')
                        result_actions.grid()
                        open_button.grid()
                    messagebox.showerror('VideoTool', data, parent=root)
        except queue.Empty:
            pass
        state['poll_timer'] = root.after(100, poll)

    def close():
        if state['busy']:
            messagebox.showinfo('Work in progress', 'Wait for the operation to finish, stop after the current file, or cancel the current conversion.', parent=root)
        else:
            root.destroy()
    def cleanup(event):
        if event.widget == root and state.get('poll_timer') is not None:
            root.after_cancel(state.pop('poll_timer'))

    root.bind('<Destroy>', cleanup, add='+')
    root.protocol('WM_DELETE_WINDOW', close)
    def location_changed(*args):
        update_naming_note()
        invalidate()
    location.trace_add('write', location_changed)
    advanced_reference.trace_add('write', show_advanced_reference)
    preset_name.trace_add('write', mode_changed)
    target_size.trace_add('write', lambda *args: invalidate())
    mode_changed()
    poll()
    root.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
