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
import gemini_analysis
import processing_receipts
from tooltips import Tooltip

import videotool
import upload_presets
import user_settings


def use_project_environment():
    """Restart a directly launched GUI with the project's optional packages."""
    environment = Path(__file__).resolve().parent / '.venv'
    python = environment / 'bin' / 'python'
    if python.is_file() and Path(sys.prefix) != environment:
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


BUTTON_HINTS = {
    'Choose source': 'Choose one video file or a folder of videos. Folder subdirectories are not included.',
    'Starting folder…': 'Choose which folder or drive the source picker opens in each time.',
    'Choose reference': 'Choose a DNxHR MOV that you have already confirmed works in DaVinci.',
    'Use automatic': 'Choose DNxHR SQ for 8-bit footage and DNxHR HQX for 10-bit footage.',
    'Output folder': 'Choose an existing output folder or create a new one.',
    'Use originals’ folder': 'Save converted videos beside their source files.',
    'Preview': 'Check your footage and show the proposed output names. No videos are converted.',
    'Stop after current file': 'Let the current video finish, then stop. You can retry the unfinished files later.',
    'Cancel current conversion': 'Terminate the active conversion and stop the batch. Any partial output is kept and will not be overwritten.',
    'Convert ready files': 'Start converting the ready files shown in the preview. Completed files are kept.',
    'Preview retry': 'Prepare failed or unfinished files for review. Successful files are kept and partial files are not overwritten.',
    'Open output folder': 'Open the output folder in your file manager. If a row is selected, open that video’s output folder.',
    'View processing receipt': 'Open the Markdown record for the latest completed conversion run.',
    'Copy summary': 'Copy the latest run counts, elapsed time, and receipt location.',
    'Restore default prompt': 'Replace the prompt with VideoTool’s default video-summary prompt.',
    'View Gemini response': 'Open the latest Gemini response saved as Markdown beside the prepared video.',
    'Gemini key…': 'Save, replace, or remove the Gemini API key in your system password store.',
    'Upload original to Gemini': 'Upload the selected video to Gemini without converting or changing it.',
    'Copy error/details': 'Copy the selected video’s full status and error details to the clipboard.',
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
    gemini_requested: bool = False
    gemini_prompt: str = ''
    gemini_model: str = ''
    gemini_status: str = ''
    gemini_error: str = ''
    gemini_response_path: object = None
    gemini_remote_name: str = ''
    direct_upload: bool = False

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


def review_output_size(item):
    if item.direct_upload:
        return 'No copy'
    if item.completed:
        try:
            return videotool.readable_size(item.output.stat().st_size)
        except OSError:
            return '—'
    return f'~{videotool.readable_size(item.estimated_bytes)}' if item.estimated_bytes else '—'


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
            item.estimated_bytes = item.upload_plan.estimated_bytes
            item.available_bytes = videotool.available_space(output)
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
                           f"{origin}\nThis file: estimated {videotool.readable_size(item.estimated_bytes)} "
                           f"(10% headroom) · Destination free space at preview: "
                           f"{videotool.readable_size(item.available_bytes)}\n"
                           'Source frame timing, resolution, and known color tags retained.')
        if item.upload_plan:
            item.detail = item.upload_plan.detail
    except (videotool.VideoToolError, OSError) as exc:
        item.error = str(exc)
    return item


def preview_direct_gemini(source, folder=False):
    """Review one original video for Gemini without planning a conversion."""
    if folder:
        raise gemini_analysis.GeminiError(
            'Direct Gemini upload accepts one video file. Choose a video instead of a folder.')
    candidate = Path(source).expanduser()
    if candidate.is_symlink():
        raise gemini_analysis.GeminiError('Choose a regular video file, not a symbolic link.')
    try:
        source = candidate.resolve(strict=True)
    except OSError as exc:
        raise gemini_analysis.GeminiError(f'Cannot open the selected video: {exc}') from exc
    if not source.is_file():
        raise gemini_analysis.GeminiError('Choose one available video file.')
    if source.suffix.lower() not in gemini_analysis.SUPPORTED_VIDEO_SUFFIXES:
        raise gemini_analysis.GeminiError('Gemini does not support this video file format.')
    try:
        before = fingerprint(source)
        metadata = videotool.inspect_video(source)
        video = videotool.select_video(metadata)
        if before != fingerprint(source):
            raise gemini_analysis.GeminiError('Source changed during inspection. Preview again.')
    except (videotool.VideoToolError, OSError) as exc:
        raise gemini_analysis.GeminiError(f'Cannot inspect the selected video: {exc}') from exc
    size = videotool.readable_size(source.stat().st_size)
    detail = (f'{video["width"]} × {video["height"]} · '
              f'{videotool.frame_rate(video.get("avg_frame_rate"))} fps average\n'
              f'Original file: {size} · No conversion will run.\n'
              'This exact file will be uploaded to Gemini and will remain unchanged.')
    return Conversion(source, source, [], fingerprint=before, detail=detail,
                      duration=duration_seconds(metadata), preset='aistudio',
                      source_metadata=metadata, direct_upload=True)


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
    if items and all(item.direct_upload for item in items):
        analyzed = sum(bool(item.gemini_response_path) for item in items)
        online_failed = sum(item.gemini_status == 'Failed' for item in items)
        online_cancelled = sum(item.gemini_status == 'Cancelled' for item in items)
        title = ('Gemini analysis complete' if analyzed and not online_failed and not online_cancelled
                 else 'Gemini analysis needs attention')
        text = (f'{title}: original video kept unchanged.\nFile: {items[0].source}\n'
                f'Gemini: {analyzed} response(s) saved locally · {online_failed} failed · '
                f'{online_cancelled} cancelled.')
        if online_failed or online_cancelled:
            text += '\nSelect the video and use Copy error/details for the full error.'
        return text
    completed = sum(item.completed for item in items)
    failed = sum(not item.completed and (bool(item.error) or item.outcome == 'Failed') for item in items)
    remaining = len(items) - completed - failed
    title = 'Conversion complete' if not failed and not remaining else 'Conversion needs attention'
    folders = sorted({str(item.output.parent) for item in items})
    summary = f'{title}: {completed} completed · {failed} failed/blocked · {remaining} unfinished.\nOutput: ' + ', '.join(folders)
    analyzed = sum(bool(item.gemini_response_path) for item in items)
    online_failed = sum(item.gemini_status == 'Failed' for item in items)
    online_cancelled = sum(item.gemini_status == 'Cancelled' for item in items)
    if analyzed or online_failed or online_cancelled:
        summary += (f'\nGemini: {analyzed} response(s) saved locally · {online_failed} failed · '
                    f'{online_cancelled} cancelled.')
        if online_failed or online_cancelled:
            summary += '\nSelect the video and use Copy error/details for the full error.'
    elif any(item.preset != 'davinci' for item in items):
        summary += '\nCompleted files are for manual upload. Nothing has been uploaded.'
    return summary


def storage_summary(items):
    ready = [item for item in items if not item.error and not item.completed and not item.direct_upload]
    if not ready:
        return ''
    estimates = [item.estimated_bytes for item in ready]
    estimated = sum(estimates) if all(value is not None for value in estimates) else None
    free = min((item.available_bytes for item in ready if item.available_bytes is not None), default=None)
    noun = 'file' if len(ready) == 1 else 'files'
    davinci_only = all(item.preset == 'davinci' for item in ready)
    qualifier = ' (includes 10% headroom per file)' if davinci_only else ''
    text = (f'Estimated batch output for all {len(ready)} ready {noun}: {videotool.readable_size(estimated)}'
            f'{qualifier} · Destination free space: {videotool.readable_size(free)}')
    if estimated is not None and free is not None and estimated > free:
        text += (f' · Short by: {videotool.readable_size(estimated - free)}. '
                 'The complete batch does not fit in the currently available space.')
    elif estimated is not None and free is not None:
        text += f' · Estimated space remaining after batch: {videotool.readable_size(free - estimated)}.'
    else:
        text += '.'
    if not davinci_only:
        text += ' Approximate only; encoded sizes can vary.'
    return text


def make_output_folder(parent, name):
    parent = Path(parent).expanduser().resolve(strict=True)
    if not parent.is_dir():
        raise OSError('Choose an existing parent folder.')
    name = str(name).strip()
    if (not name or name in ('.', '..') or
            any(c in '<>:"/\\|?*' or ord(c) < 32 for c in name)):
        raise ValueError('Enter one folder name without slashes or special filename characters.')
    target = parent / name
    target.mkdir()
    return target.resolve(strict=True)


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


def analyze_completed(items, prompt, model, cancel, report, receipt_path=None, analyzer=None,
                      response_writer=None):
    """Analyze newly prepared AI Studio files without changing conversion outcomes."""
    analyzer = analyzer or gemini_analysis.analyze
    response_writer = response_writer or gemini_analysis.write_response
    candidates = [(index, item) for index, item in enumerate(items)
                  if item.preset == 'aistudio' and item.completed_this_run]
    if len(candidates) > 1:
        raise gemini_analysis.GeminiError(
            'Gemini analysis accepts one video at a time. Choose one video and preview again.')
    saved = []
    failed = 0
    for position, (index, item) in enumerate(candidates, 1):
        if cancel.is_set():
            item.gemini_status = 'Cancelled'
            item.gemini_error = 'Gemini analysis cancelled. The converted video was kept.'
            report(index, 'Done', item.gemini_error)
            failed += 1
            break
        item.gemini_requested = True
        item.gemini_prompt = prompt
        item.gemini_model = model

        def stage(label, message):
            item.gemini_status = label
            report(index, label, f'{message} ({position} of {len(candidates)})')

        try:
            result = analyzer(item.output, prompt, model=model, cancel=cancel, report=stage)
            path = response_writer(item.source, item.output, prompt, result, receipt_path)
            item.gemini_status = 'Saved'
            item.gemini_remote_name = result.remote_name
            item.gemini_response_path = path
            saved.append(path)
            report(index, 'Done', f'Converted video: {item.output}\nGemini response: {path}')
        except gemini_analysis.GeminiCancelled as exc:
            item.gemini_status = 'Cancelled'
            item.gemini_error = str(exc)
            failed += 1
            report(index, 'Done', str(exc))
            break
        except (gemini_analysis.GeminiError, OSError) as exc:
            item.gemini_status = 'Failed'
            item.gemini_error = str(exc)
            failed += 1
            if item.direct_upload:
                report(index, 'Done', f'Original video kept unchanged. Gemini failed: {exc}')
            else:
                report(index, 'Done', f'Video conversion succeeded. Gemini failed: {exc}')
    return saved, failed


def main():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk
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
    subtitle = tk.StringVar(value='Choose a format and footage, review the files, then convert.')
    ttk.Label(body, textvariable=subtitle, style='Subtitle.TLabel').grid(
        row=1, sticky='w', pady=(3, 14))
    source = tk.StringVar(value='Choose a video or a folder to begin')
    destination = tk.StringVar(value='Save beside the original files')
    reference_text = tk.StringVar(value='Automatic: preserve 8-bit or 10-bit')
    location = tk.StringVar()
    preset_name = tk.StringVar(value=upload_presets.PRESETS['davinci'])
    target_size = tk.StringVar(value='380')
    advanced_reference = tk.BooleanVar(value=False)
    analyze_with_gemini = tk.BooleanVar(value=False)
    direct_gemini_upload = tk.BooleanVar(value=False)
    gemini_readiness = tk.StringVar()
    preset_note = tk.StringVar()
    naming_note = tk.StringVar()
    configured_source_folder = user_settings.default_source_folder()
    def regular_source_note():
        note = 'Folder mode scans one folder. Existing outputs are preserved.'
        if configured_source_folder:
            note += f' File picker starts in: {configured_source_folder}'
        return note
    normal_source_note = regular_source_note()
    source_note = tk.StringVar(value=normal_source_note)
    storage_text = tk.StringVar()
    status = tk.StringVar(value='Ready to choose footage.')
    state = {'source': None, 'folder': False, 'destination': None, 'items': [], 'busy': False,
             'messages': {}, 'reference': None, 'receipt': None, 'run_summary': '',
             'receipt_error': '', 'gemini_response': None}
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
        state['gemini_response'] = None
        tree.delete(*tree.get_children())
        show_details()
        file_progress_text.set('Current file: waiting')
        batch_progress_text.set('Whole batch: waiting')
        progress['value'] = 0
        batch_progress['value'] = 0
        storage_text.set('')
        storage_banner.grid_remove()
        progress_frame.grid_remove()
        start_button.configure(state='disabled')
        direct_button.configure(state='disabled')
        if selected_preset() == 'aistudio' and direct_gemini_upload.get():
            start_button.grid_remove()
            direct_button.grid()
        else:
            direct_button.grid_remove()
            start_button.grid()
        retry_button.configure(state='disabled')
        retry_button.pack_forget()
        open_button.configure(state='disabled')
        result_actions.grid_remove()
        view_receipt_button.grid_remove()
        copy_summary_button.grid_remove()
        copy_details_button.grid_remove()
        open_button.grid_remove()
        view_gemini_button.grid_remove()
        view_receipt_button.configure(state='disabled')
        copy_summary_button.configure(state='disabled')
        copy_details_button.configure(state='disabled')
        view_gemini_button.configure(state='disabled')
        status.set('Preview this video before uploading it to Gemini.'
                   if selected_preset() == 'aistudio' and direct_gemini_upload.get()
                   else 'Preview your selection before converting.')

    def set_source(selected, folder):
        selected = str(selected)
        state.update(source=selected, folder=folder)
        source.set(('Folder: ' if folder else 'Video: ') + selected)
        invalidate()

    def choose_source(folder):
        options = {'parent': root, 'title': 'Choose footage folder' if folder else 'Choose video'}
        if configured_source_folder and configured_source_folder.is_dir():
            options['initialdir'] = str(configured_source_folder)
        selected = filedialog.askdirectory(**options) if folder else filedialog.askopenfilename(**options)
        if selected:
            set_source(selected, folder)

    def choose_starting_folder():
        nonlocal configured_source_folder, normal_source_note
        options = {'parent': root, 'title': 'Choose the folder source selection should open in'}
        if configured_source_folder and configured_source_folder.is_dir():
            options['initialdir'] = str(configured_source_folder)
        selected = filedialog.askdirectory(**options)
        if not selected:
            return
        try:
            configured_source_folder = user_settings.save_default_source_folder(selected)
        except ValueError as exc:
            messagebox.showerror('Could not save starting folder', str(exc), parent=root)
            return
        normal_source_note = regular_source_note()
        if not (selected_preset() == 'aistudio' and direct_gemini_upload.get()):
            source_note.set(normal_source_note)
        status.set(f'Source picker will start in: {configured_source_folder}')

    def reset_starting_folder():
        nonlocal configured_source_folder, normal_source_note
        try:
            user_settings.clear_default_source_folder()
        except ValueError as exc:
            messagebox.showerror('Could not reset starting folder', str(exc), parent=root)
            return
        configured_source_folder = None
        normal_source_note = regular_source_note()
        if not (selected_preset() == 'aistudio' and direct_gemini_upload.get()):
            source_note.set(normal_source_note)
        status.set('Source picker will use the normal system starting folder.')

    def choose_destination():
        selected = filedialog.askdirectory(parent=root, title='Choose output folder')
        if selected:
            state['destination'] = selected
            destination.set(selected)
            invalidate()

    def create_destination():
        parent = filedialog.askdirectory(parent=root, title='Choose where to create the output folder')
        if not parent:
            return
        name = simpledialog.askstring('Create output folder', 'New folder name:', parent=root)
        if name is None:
            return
        try:
            selected = make_output_folder(parent, name)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Cannot create output folder', str(exc), parent=root)
            return
        state['destination'] = str(selected)
        destination.set(str(selected))
        invalidate()
        status.set(f'Created output folder: {selected}')

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
    starting_folder_button = ttk.Menubutton(selection, text='Starting folder…')
    starting_folder_button.grid(row=1, column=2, sticky='w', padx=(6, 0), pady=(6, 0))
    starting_folder_menu = tk.Menu(starting_folder_button, tearoff=False)
    starting_folder_menu.add_command(label='Choose starting folder', command=choose_starting_folder)
    starting_folder_menu.add_command(label='Use normal system folder', command=reset_starting_folder)
    starting_folder_button.configure(menu=starting_folder_menu)
    controls.append(starting_folder_button)
    destination_label = ttk.Label(selection, text='Destination', style='Field.TLabel')
    destination_label.grid(row=2, column=0, sticky='w', padx=(0, 12), pady=(8, 0))
    destination_entry = ttk.Entry(selection, textvariable=destination, state='readonly')
    destination_entry.grid(row=2, column=1, columnspan=2, sticky='ew', pady=(8, 0))
    output_button = ttk.Menubutton(selection, text='Output folder')
    output_button.grid(row=3, column=1, sticky='w', pady=(6, 0))
    output_menu = tk.Menu(output_button, tearoff=False)
    output_menu.add_command(label='Choose existing folder', command=choose_destination)
    output_menu.add_command(label='Create new folder', command=create_destination)
    output_button.configure(menu=output_menu)
    controls.append(output_button)
    originals_button = ttk.Button(selection, text='Use originals’ folder', command=reset_destination)
    originals_button.grid(row=3, column=2, sticky='w', padx=(4, 0), pady=(6, 0))
    controls.append(originals_button)
    naming = ttk.Frame(selection)
    naming.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(8, 0))
    naming.columnconfigure(1, weight=1)
    ttk.Label(naming, text='Name files by location', style='Field.TLabel').grid(row=0, column=0,
                                                                                sticky='w', padx=(0, 12))
    location_entry = ttk.Entry(naming, textvariable=location, width=25)
    location_entry.grid(row=0, column=1, sticky='ew')
    controls.append(location_entry)
    destination_controls = [output_button, originals_button, location_entry]
    ttk.Label(naming, textvariable=naming_note, style='Muted.TLabel').grid(row=1, column=1,
                                                                          sticky='w', pady=(3, 0))
    ttk.Label(selection, textvariable=source_note, style='Muted.TLabel', wraplength=450).grid(
        row=5, column=0, columnspan=3, sticky='w', pady=(7, 0))

    gemini_options = ttk.LabelFrame(setup, text='Gemini analysis', style='Section.TLabelframe',
                                    padding=(14, 10))
    gemini_options.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
    gemini_options.columnconfigure(0, weight=1)
    gemini_toggle = ttk.Checkbutton(gemini_options, text='Analyze converted video with Gemini',
                                    variable=analyze_with_gemini)
    gemini_toggle.grid(row=0, column=0, sticky='w')
    controls.append(gemini_toggle)
    Tooltip(gemini_toggle, 'Upload the reviewed video to Gemini and send it with the prompt below.')
    gemini_key_button = ttk.Menubutton(gemini_options, text='Gemini key…')
    gemini_key_button.grid(row=0, column=1, sticky='e', padx=(12, 0))
    controls.append(gemini_key_button)
    gemini_key_menu = tk.Menu(gemini_key_button, tearoff=False)
    gemini_key_button.configure(menu=gemini_key_menu)
    ttk.Label(gemini_options, textvariable=gemini_readiness, style='Muted.TLabel').grid(
        row=1, column=0, columnspan=2, sticky='w', pady=(5, 0))
    direct_upload_toggle = ttk.Checkbutton(
        gemini_options, text='Upload original directly to Gemini (skip conversion)',
        variable=direct_gemini_upload)
    direct_upload_toggle.grid(row=2, column=0, columnspan=2, sticky='w', pady=(7, 0))
    controls.append(direct_upload_toggle)
    Tooltip(direct_upload_toggle,
            'Send the selected original video to Gemini exactly as it is, without running FFmpeg.')
    prompt_options = ttk.Frame(gemini_options)
    prompt_options.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
    prompt_options.columnconfigure(0, weight=1)
    ttk.Label(prompt_options, text='Prompt sent with this video', style='Field.TLabel').grid(
        row=0, column=0, sticky='w')
    restore_prompt_button = ttk.Button(prompt_options, text='Restore default prompt')
    restore_prompt_button.grid(row=0, column=1, sticky='e', padx=(10, 0))
    controls.append(restore_prompt_button)
    prompt_text = ScrolledText(prompt_options, height=6, wrap='word', font=('Sans', 10),
                               borderwidth=1, padx=10, pady=8)
    prompt_text.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(6, 0))
    prompt_text.insert('1.0', gemini_analysis.DEFAULT_PROMPT)

    def current_prompt():
        return prompt_text.get('1.0', 'end-1c').strip()

    def restore_default_prompt():
        prompt_text.delete('1.0', 'end')
        prompt_text.insert('1.0', gemini_analysis.DEFAULT_PROMPT)
        invalidate()

    restore_prompt_button.configure(command=restore_default_prompt)
    prompt_text.bind('<FocusOut>', lambda event: invalidate(), add='+')

    def show_gemini_prompt(*args):
        online = analyze_with_gemini.get() or direct_gemini_upload.get()
        direct = selected_preset() == 'aistudio' and direct_gemini_upload.get()
        if selected_preset() == 'aistudio' and online:
            prompt_options.grid()
        else:
            prompt_options.grid_remove()
        if direct:
            target_options.grid_remove()
            preset_note.set('Upload one selected original video directly to Gemini. No conversion will run.\n'
                            'The Gemini response will be saved beside the original.')
            selection.configure(text='2  Choose one video')
            for widget in (destination_label, destination_entry, output_button, originals_button, naming):
                widget.grid_remove()
            source_menu.entryconfigure(1, state='disabled')
            source_note.set('Choose or drop one video. The original will remain unchanged.')
            subtitle.set('Choose one video, review the prompt, then upload it to Gemini.')
            tree.heading('output', text='Upload file')
        elif selected_preset() == 'aistudio':
            target_options.grid()
            preset_note.set('Finished SDR exports → smaller MP4 copies, up to 1920 pixels on the longest edge.\n'
                            'Optionally upload one result to Gemini with your prompt after conversion.')
        if not direct:
            selection.configure(text='2  Choose source and destination')
            destination_label.grid()
            destination_entry.grid()
            output_button.grid()
            originals_button.grid()
            naming.grid()
            source_menu.entryconfigure(1, state='normal')
            source_note.set(normal_source_note)
            subtitle.set('Choose a format and footage, review the files, then convert.')
            tree.heading('output', text='Output file')
        _ready, readiness_text = gemini_analysis.readiness()
        gemini_readiness.set(readiness_text)
        if not state['busy']:
            restore_prompt_button.configure(state='normal' if online else 'disabled')
            direct_upload_toggle.configure(state='normal')
            direct = selected_preset() == 'aistudio' and direct_gemini_upload.get()
            for widget in destination_controls:
                widget.configure(state='disabled' if direct else 'normal')

    def save_gemini_key():
        value = simpledialog.askstring(
            'Set up Gemini', 'Paste your new Gemini API key:', parent=root, show='*')
        if value is None:
            return
        try:
            gemini_analysis.save_api_key(value)
        except gemini_analysis.GeminiError as exc:
            messagebox.showerror('Could not save Gemini key', str(exc), parent=root)
            return
        show_gemini_prompt()
        status.set('Gemini key saved securely. You will not need to enter it each time.')

    def remove_gemini_key():
        try:
            removed = gemini_analysis.delete_api_key()
        except gemini_analysis.GeminiError as exc:
            messagebox.showerror('Could not remove Gemini key', str(exc), parent=root)
            return
        show_gemini_prompt()
        status.set('Saved Gemini key removed.' if removed else 'No saved Gemini key was found.')

    gemini_key_menu.add_command(label='Save or replace key', command=save_gemini_key)
    gemini_key_menu.add_command(label='Remove saved key', command=remove_gemini_key)

    def gemini_option_changed(*args):
        if analyze_with_gemini.get() and direct_gemini_upload.get():
            direct_gemini_upload.set(False)
        show_gemini_prompt()
        invalidate()

    def direct_upload_changed(*args):
        if direct_gemini_upload.get() and analyze_with_gemini.get():
            analyze_with_gemini.set(False)
        show_gemini_prompt()
        invalidate()

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
            if direct_gemini_upload.get() and candidate.is_dir():
                raise OSError('Direct Gemini upload accepts one video file, not a folder.')
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
        normal_source_note = ('Drop a video or folder onto this window, or use Choose source. '
                              'Folder subdirectories are not included.')
        source_note.set(normal_source_note)
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
            'aistudio': 'Finished SDR exports → smaller MP4 copies, up to 1920 pixels on the longest edge.\nOptionally upload one result to Gemini with your prompt after conversion.',
            'youtube': 'Finished SDR exports → high-quality H.264/AAC MP4 copies at source resolution.\nNo size cap. Check picture and sound, then upload manually to YouTube.',
        }
        preset_note.set(notes[mode])
        update_naming_note()
        if mode == 'aistudio':
            davinci_options.grid_remove()
            target_options.grid()
            gemini_options.grid()
        elif mode == 'davinci':
            target_options.grid_remove()
            davinci_options.grid()
            show_advanced_reference()
        else:
            target_options.grid_remove()
            davinci_options.grid_remove()
        if mode != 'aistudio':
            gemini_options.grid_remove()
        size_entry.configure(state='normal' if mode == 'aistudio' and not state['busy'] else 'disabled')
        for widget in reference_controls:
            widget.configure(state=('readonly' if widget is reference_entry else 'normal')
                             if mode == 'davinci' and not state['busy'] else 'disabled')
        show_gemini_prompt()
        invalidate()

    def selected_preset():
        return next(key for key, label in upload_presets.PRESETS.items() if label == preset_name.get())

    review = ttk.LabelFrame(body, text='3  Review files', style='Section.TLabelframe', padding=(10, 8))
    review.grid(row=3, sticky='nsew', pady=(0, 10))
    review.columnconfigure(0, weight=1)
    review.rowconfigure(1, weight=1)
    storage_banner = ttk.Label(review, textvariable=storage_text, style='Status.TLabel',
                               justify='left', wraplength=970)
    storage_banner.grid(row=0, sticky='ew', pady=(0, 8))
    storage_banner.grid_remove()
    table_frame = ttk.Frame(review)
    table_frame.grid(row=1, sticky='nsew')
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    tree = ttk.Treeview(table_frame,
                        columns=('source', 'size', 'duration', 'depth', 'estimate', 'output', 'status'),
                        show='headings', selectmode='browse')
    for key, title, width in [('source', 'Source video', 225), ('size', 'Size', 85),
                              ('duration', 'Duration', 80), ('depth', 'Bit depth', 75),
                              ('estimate', 'Approx. output', 105),
                              ('output', 'Output file', 210), ('status', 'Status', 100)]:
        tree.heading(key, text=title)
        tree.column(key, width=width, minwidth=65,
                    stretch=key in ('source', 'output'))
    tree.tag_configure('Ready', foreground='#16665b')
    tree.tag_configure('Done', foreground='#25723b')
    tree.tag_configure('Converting', foreground='#1f5f99')
    tree.tag_configure('Uploading', foreground='#1f5f99')
    tree.tag_configure('Processing', foreground='#1f5f99')
    tree.tag_configure('Analyzing', foreground='#1f5f99')
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
    details.grid(row=2, sticky='ew', pady=(8, 0))
    details.configure(state='disabled')

    def details_text(index):
        item = state['items'][index]
        text = f'Source: {item.source}\nOutput/upload file: {item.output}\n' + (item.error or item.detail)
        if item.gemini_model:
            text += f'\nGemini model: {item.gemini_model}'
        if index in state['messages']:
            text += '\n' + state['messages'][index]
        if item.gemini_error and item.gemini_error not in text:
            text += f'\nGemini error: {item.gemini_error}'
        return text

    def show_details(event=None):
        selected = tree.selection()
        text = ''
        if selected:
            details.grid()
            text = details_text(int(selected[0]))
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
        direct = selected_preset() == 'aistudio' and direct_gemini_upload.get()
        size_entry.configure(state='normal' if not value and selected_preset() == 'aistudio' and not direct else 'disabled')
        for widget in destination_controls:
            widget.configure(state='disabled' if value or direct else 'normal')
        for widget in reference_controls:
            widget.configure(state=('readonly' if widget is reference_entry else 'normal')
                             if not value and selected_preset() == 'davinci' else 'disabled')
        prompt_text.configure(state='disabled' if value else 'normal')
        online = analyze_with_gemini.get() or direct_gemini_upload.get()
        restore_prompt_button.configure(state='disabled' if value or not online else 'normal')
        direct_upload_toggle.configure(state='disabled' if value else 'normal')
        start_button.configure(state='disabled')
        direct_button.configure(state='disabled')
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
            copy_details_button.configure(state='normal' if state['items'] else 'disabled')
            view_gemini_button.configure(state='normal' if state['gemini_response'] else 'disabled')

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
        online = (analyze_with_gemini.get() or direct_gemini_upload.get()) and selected_preset() == 'aistudio'
        direct = online and direct_gemini_upload.get()
        reviewed_prompt = current_prompt()
        def worker():
            try:
                items = [preview_direct_gemini(args[0], args[1])] if direct else preview(*args)
                if online:
                    ready_items = [item for item in items if not item.error and not item.completed]
                    if len(ready_items) != 1:
                        raise gemini_analysis.GeminiError(
                            'Gemini analysis accepts one ready video at a time. Choose one video and preview again.')
                    for item in ready_items:
                        item.gemini_requested = True
                        item.gemini_prompt = reviewed_prompt
                        item.gemini_model = gemini_analysis.DEFAULT_MODEL
                        timing = 'without conversion' if direct else 'after conversion'
                        item.detail += (f'\nGemini upload enabled for this video {timing} · Model: '
                                        f'{gemini_analysis.DEFAULT_MODEL}\nPrompt:\n{reviewed_prompt}')
                events.put(('preview', items))
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

    def view_gemini_response():
        try:
            open_processing_receipt(state['gemini_response'])
        except (OSError, TypeError) as exc:
            messagebox.showerror('Cannot open Gemini response', str(exc), parent=root)

    def copy_summary():
        if not state['run_summary']:
            return
        root.clipboard_clear()
        root.clipboard_append(state['run_summary'])
        root.update_idletasks()
        status.set('Processing summary copied to the clipboard.')

    def copy_details():
        selected = tree.selection()
        if not selected:
            return
        text = details_text(int(selected[0]))
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update_idletasks()
        status.set('Full details copied to the clipboard.')

    def do_convert():
        online = (analyze_with_gemini.get() or direct_gemini_upload.get()) and selected_preset() == 'aistudio'
        direct = bool(state['items'] and len(state['items']) == 1 and state['items'][0].direct_upload)
        reviewed_prompt = current_prompt()
        if online and not reviewed_prompt:
            messagebox.showerror('Enter a Gemini prompt',
                                 'Paste or type the prompt to send with each video.', parent=root)
            return
        if online:
            ready, readiness_text = gemini_analysis.readiness()
            if not ready:
                messagebox.showerror('Gemini is not ready', readiness_text, parent=root)
                return
        busy(True)
        progress.stop()
        progress.configure(mode='determinate', value=0)
        batch_progress['value'] = 0
        stop.clear()
        cancel.clear()
        state['receipt'] = None
        state['run_summary'] = ''
        state['receipt_error'] = ''
        state['gemini_response'] = None
        interrupt_actions.grid()
        stop_button.configure(state='normal')
        cancel_button.configure(state='normal')
        status.set('Starting Gemini upload…' if direct else 'Starting conversion…')
        items = tuple(state['items'])
        def worker():
            try:
                receipt_holder = {'path': None}
                def receipt_ready(*data):
                    receipt_holder['path'] = data[0]
                    events.put(('receipt', data))
                if direct:
                    item = items[0]
                    if fingerprint(item.source) != item.fingerprint:
                        raise gemini_analysis.GeminiError('Source changed since preview. Preview again.')
                    item.attempted_this_run = True
                    item.completed_this_run = True
                    item.completed = True
                    item.outcome = 'Done'
                    result = (0, 0, 0)
                else:
                    result = convert(items, stop, lambda *data: events.put(('progress', data)),
                                     lambda *data: events.put(('percent', data)), cancel,
                                     receipt_ready)
                if online and any(item.completed_this_run for item in items):
                    saved, online_failed = analyze_completed(
                        items, reviewed_prompt, gemini_analysis.DEFAULT_MODEL, cancel,
                        lambda *data: events.put(('progress', data)), receipt_holder['path'])
                    events.put(('gemini', (saved, online_failed)))
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
        status.set('Cancelling the current operation… Converted videos and any partial output will be kept.')

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
    direct_button = ttk.Button(actions, text='Upload original to Gemini', style='Accent.TButton',
                               command=do_convert, state='disabled')
    direct_button.grid(row=0, column=2, sticky='e', padx=(10, 0))
    direct_button.grid_remove()

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
    copy_details_button = ttk.Button(result_actions, text='Copy error/details',
                                     command=copy_details, state='disabled')
    copy_details_button.grid(row=0, column=2, padx=5)
    view_gemini_button = ttk.Button(result_actions, text='View Gemini response',
                                    command=view_gemini_response, state='disabled')
    view_gemini_button.grid(row=0, column=3, padx=5)
    open_button = ttk.Button(result_actions, text='Open output folder', command=open_folder,
                             state='disabled')
    open_button.grid(row=0, column=4, padx=5)
    retry_button.pack_forget()
    result_actions.grid_remove()
    view_receipt_button.grid_remove()
    copy_summary_button.grid_remove()
    copy_details_button.grid_remove()
    view_gemini_button.grid_remove()
    open_button.grid_remove()

    for button in [*controls, start_button, direct_button, stop_button, cancel_button, retry_button,
                   view_receipt_button, copy_summary_button, copy_details_button,
                   view_gemini_button, open_button]:
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
                                            review_output_size(item), item.output.name, row_status),
                                    tags=(row_status,))
                    ready = sum(not item.error and not item.completed for item in data)
                    completed = sum(item.completed for item in data)
                    batch_storage = storage_summary(data)
                    storage_text.set(batch_storage)
                    if batch_storage:
                        storage_banner.grid()
                    else:
                        storage_banner.grid_remove()
                    busy(False)
                    file_progress_text.set('Current file: ready to convert' if ready else 'Current file: no ready files')
                    status.set(f'{ready} ready · {len(data) - ready - completed} blocked · {completed} already completed.'
                               + (' Review the file, then upload it.' if data and data[0].direct_upload else
                                  ' Review the list, then convert ready files.'))
                    if ready:
                        if data[0].direct_upload:
                            start_button.grid_remove()
                            direct_button.grid()
                            direct_button.configure(state='normal')
                            file_progress_text.set('Current file: ready to upload without conversion')
                        else:
                            direct_button.grid_remove()
                            start_button.grid()
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
                        copy_details_button.configure(state='normal')
                        result_actions.grid()
                        open_button.grid()
                        copy_details_button.grid()
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
                    elif label in ('Uploading', 'Processing', 'Analyzing'):
                        progress.configure(mode='indeterminate')
                        progress.start(12)
                        stop_button.configure(state='disabled')
                        file_progress_text.set(f'Current file: {label.lower()} with Gemini')
                        status.set(message)
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
                    copy_details_button.configure(state='normal')
                    result_actions.grid()
                    open_button.grid()
                    copy_details_button.grid()
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
                elif kind == 'gemini':
                    saved, online_failed = data
                    if saved:
                        state['gemini_response'] = saved[-1]
                        view_gemini_button.configure(state='normal')
                        result_actions.grid()
                        view_gemini_button.grid()
                elif kind == 'error':
                    busy(False)
                    file_progress_text.set('Current file: operation failed')
                    batch_progress_text.set('Whole batch: stopped')
                    status.set('Unable to complete this operation. Preview retry to keep completed files.')
                    if state['items']:
                        retry_button.configure(state='normal')
                        retry_button.pack(side='left', padx=5)
                        open_button.configure(state='normal')
                        copy_details_button.configure(state='normal')
                        result_actions.grid()
                        open_button.grid()
                        copy_details_button.grid()
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
    analyze_with_gemini.trace_add('write', gemini_option_changed)
    direct_gemini_upload.trace_add('write', direct_upload_changed)
    preset_name.trace_add('write', mode_changed)
    target_size.trace_add('write', lambda *args: invalidate())
    mode_changed()
    poll()
    root.mainloop()
    return 0


if __name__ == '__main__':
    use_project_environment()
    raise SystemExit(main())
