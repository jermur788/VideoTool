"""Human-readable Markdown records for completed VideoTool runs."""
from dataclasses import dataclass, field
from datetime import datetime
import math
from pathlib import Path
import shlex
import shutil
import subprocess


@dataclass
class Record:
    source: Path
    output: Path
    command: list = field(default_factory=list)
    preset: str = 'davinci'
    outcome: str = 'Ready'
    error: str = ''
    completed: bool = False
    attempted_this_run: bool = False
    completed_this_run: bool = False
    duration: float = 0
    elapsed_seconds: float | None = None
    batch_elapsed_seconds: float | None = None
    source_metadata: object = None
    output_metadata: object = None
    executed_commands: list = field(default_factory=list)
    validation_result: str = ''
    davinci_settings: object = None
    upload_plan: object = None
    target_mb: float = 380


def readable_bytes(value):
    if value is None:
        return 'unavailable'
    amount = float(value)
    for unit in ('bytes', 'KB', 'MB', 'GB', 'TB'):
        if amount < 1000 or unit == 'TB':
            return f'{int(amount)} bytes' if unit == 'bytes' else f'{amount:.2f} {unit}'
        amount /= 1000


def readable_seconds(value):
    if value is None or not math.isfinite(value):
        return 'unavailable'
    return f'{value:.2f} seconds'


def video_stream(metadata):
    if not metadata:
        return {}
    videos = [s for s in metadata.get('streams', []) if s.get('codec_type') == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    defaults = [s for s in videos if s.get('disposition', {}).get('default')]
    return defaults[0] if len(defaults) == 1 else (videos[0] if videos else {})


def bit_depth(stream):
    value = stream.get('bits_per_raw_sample')
    try:
        if int(value or 0) > 0:
            return f'{int(value)}-bit'
    except (TypeError, ValueError):
        pass
    pixel_format = str(stream.get('pix_fmt') or '')
    for depth in (16, 14, 12, 10, 9):
        if f'{depth}le' in pixel_format or f'{depth}be' in pixel_format:
            return f'{depth}-bit'
    return '8-bit' if pixel_format else 'unknown'


def audio_description(metadata):
    audio = [s for s in (metadata or {}).get('streams', []) if s.get('codec_type') == 'audio']
    if not audio:
        return 'none'
    result = []
    for stream in audio:
        codec = stream.get('codec_name', 'unknown')
        bits = stream.get('bits_per_raw_sample') or stream.get('bits_per_sample')
        if not bits and codec.startswith('pcm_s'):
            digits = ''.join(c for c in codec[5:] if c.isdigit())
            bits = digits or None
        depth = f'{bits}-bit' if bits else 'depth unknown'
        rate = f"{stream.get('sample_rate')} Hz" if stream.get('sample_rate') else 'rate unknown'
        channels = f"{stream.get('channels')} channel(s)" if stream.get('channels') else 'channels unknown'
        result.append(f'{codec}, {depth}, {rate}, {channels}')
    return '; '.join(result)


def stat_size(path):
    try:
        return path.stat().st_size
    except OSError:
        return None


def ffmpeg_version(items):
    executable = next((item.command[0] for item in items if item.command), shutil.which('ffmpeg'))
    if not executable:
        return 'unavailable'
    try:
        result = subprocess.run([executable, '-version'], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=10, check=False)
        return result.stdout.splitlines()[0].strip() if result.stdout else 'unavailable'
    except (OSError, subprocess.TimeoutExpired):
        return 'unavailable'


def preset_parameters(item):
    if item.preset == 'davinci':
        settings = item.davinci_settings or {}
        origin = f"reference `{settings.get('reference')}`" if settings.get('reference') else 'automatic source-depth choice'
        return (f"{settings.get('name', 'DNxHR')}, {settings.get('bit_depth', 'unknown')}-bit; "
                f"PCM {settings.get('audio_bits', 'unknown')}-bit; {origin}")
    plan = item.upload_plan
    if plan is None:
        return 'unavailable because planning did not complete'
    if item.preset == 'aistudio':
        return (f'{item.target_mb:g} MB workflow target; {plan.width} × {plan.height}; '
                f"{'two-pass target-size encoding' if plan.first_pass else 'single pass'}")
    return f'{plan.width} × {plan.height}; CRF 18; slow preset; no size cap'


def validation(item):
    if item.validation_result:
        return item.validation_result
    if item.outcome == 'Done':
        return 'Conversion completed, but detailed validation was unavailable.'
    return item.error or item.outcome


def _path(folder, now):
    stem = now.strftime('VideoTool-processing-%Y%m%d-%H%M%S')
    for number in range(1000):
        suffix = '' if number == 0 else f'-{number:03d}'
        candidate = folder / f'{stem}{suffix}.md'
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise OSError('Could not allocate a unique processing receipt name.')


def build(items, started_at, finished_at):
    attempted = [item for item in items if item.attempted_this_run]
    completed = [item for item in attempted if item.completed_this_run]
    failed = [item for item in items if item.outcome == 'Failed' or item.error]
    interrupted = [item for item in attempted if item.outcome == 'Interrupted']
    unfinished = [item for item in items if not item.completed and
                  not (item.outcome == 'Failed' or item.error)]
    folders = sorted({str(item.output.parent) for item in items})
    source_total = sum(filter(None, (stat_size(item.source) for item in attempted)))
    output_total = sum(filter(None, (stat_size(item.output) for item in completed)))
    elapsed = max(0.0, finished_at - started_at)
    now = datetime.now().astimezone()
    lines = [
        '# VideoTool processing receipt', '',
        f'- Created: {now.isoformat(timespec="seconds")}',
        f'- Destination: {", ".join(folders)}',
        f'- Attempted: {len(attempted)}',
        f'- Completed: {len(completed)}',
        f'- Failed: {len(failed)}',
        f'- Interrupted: {len(interrupted)}',
        f'- Unfinished (includes interrupted): {len(unfinished)}',
        f'- Batch elapsed time: {readable_seconds(elapsed)}',
        f'- Total attempted source size: {readable_bytes(source_total)}',
        f'- Total completed output size: {readable_bytes(output_total)}',
        f'- FFmpeg: {ffmpeg_version(items)}', '',
        'A completed conversion means VideoTool finished its local encoding and validation checks. '
        'It does not mean the file was confirmed in DaVinci Resolve or accepted by an upload service.',
    ]
    for item in items:
        source_video = video_stream(item.source_metadata)
        output_video = video_stream(item.output_metadata)
        speed = (item.duration / item.elapsed_seconds
                 if item.duration > 0 and item.elapsed_seconds and item.elapsed_seconds > 0 else None)
        lines += [
            '', f'## {item.output.name}', '',
            f'- Status: {item.outcome}',
            f'- Preset: {item.preset}',
            f'- Preset parameters: {preset_parameters(item)}',
            f'- Source: `{markdown_path(item.source)}`',
            f'- Output: `{markdown_path(item.output)}`',
            f'- Source size: {readable_bytes(stat_size(item.source))}',
            f'- Output size: {readable_bytes(stat_size(item.output))}',
            f"- Source video: {source_video.get('codec_name', 'unknown')}; "
            f"{source_video.get('width', '?')} × {source_video.get('height', '?')}; "
            f"{source_video.get('avg_frame_rate', 'unknown')} fps; "
            f"{source_video.get('pix_fmt', 'unknown')} ({bit_depth(source_video)})",
            f'- Source audio: {audio_description(item.source_metadata)}',
            f"- Output video: {output_video.get('codec_name', 'unavailable')}; "
            f"{output_video.get('profile', 'profile unavailable')}; "
            f"{output_video.get('width', '?')} × {output_video.get('height', '?')}; "
            f"{output_video.get('avg_frame_rate', 'unknown')} fps; "
            f"{output_video.get('pix_fmt', 'pixel format unavailable')} ({bit_depth(output_video)})",
            f'- Output audio: {audio_description(item.output_metadata)}',
            f'- Source duration: {readable_seconds(item.duration if item.duration > 0 else None)}',
            f'- Conversion elapsed time: {readable_seconds(item.elapsed_seconds)}',
            f"- Effective conversion speed: {speed:.2f}× realtime" if speed is not None else
            '- Effective conversion speed: unavailable',
            f'- Validation: {validation(item)}',
            '- FFmpeg command(s) actually executed:',
        ]
        if item.executed_commands:
            for command in item.executed_commands:
                lines += ['', '    ' + shlex.join(command)]
        else:
            lines += ['', '    No FFmpeg command was executed for this item.']
    return '\n'.join(lines) + '\n'


def write(items, started_at, finished_at):
    if not any(item.completed_this_run for item in items):
        return None
    folder = items[0].output.parent
    if any(item.output.parent != folder for item in items):
        raise OSError('A single receipt requires one destination folder.')
    path = _path(folder, datetime.now().astimezone())
    with path.open('x', encoding='utf-8') as handle:
        handle.write(build(items, started_at, finished_at))
    return path


def markdown_path(path):
    return str(path).replace('`', '\\`').replace('\n', ' ')


def summary(items, receipt_path=None):
    completed = sum(item.completed_this_run for item in items)
    failed = sum(bool(item.error) or (item.attempted_this_run and item.outcome == 'Failed')
                 for item in items)
    interrupted = sum(item.attempted_this_run and item.outcome == 'Interrupted' for item in items)
    unfinished = sum(not item.completed and item.outcome not in ('Failed',) for item in items)
    elapsed = max((item.batch_elapsed_seconds or 0 for item in items), default=0)
    text = (f'VideoTool run: {completed} completed, {failed} failed, '
            f'{interrupted} interrupted, {unfinished} unfinished including interrupted; '
            f'elapsed {elapsed:.2f} seconds.')
    if receipt_path:
        text += f' Receipt: {receipt_path}'
    return text
