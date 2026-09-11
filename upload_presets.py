"""Local MP4 preparation for manual upload; no network or account integration."""
from dataclasses import dataclass, field
from fractions import Fraction
import math
from pathlib import Path
import shutil
import tempfile

import videotool

PRESETS = {'davinci': 'DaVinci editing copy', 'aistudio': 'AI Studio upload', 'youtube': 'YouTube upload'}


@dataclass
class UploadPlan:
    source: Path
    output: Path
    command: list
    first_pass: list
    duration: float
    max_bytes: int | None
    detail: str
    width: int
    height: int
    audio: bool
    estimated_bytes: int
    executed_commands: list = field(default_factory=list)


def size_target(value):
    try:
        mb = float(value)
    except (ValueError, TypeError):
        raise videotool.VideoToolError('Enter an AI Studio size target between 1 and 100000 MB.')
    if not math.isfinite(mb) or not 1 <= mb <= 100000:
        raise videotool.VideoToolError('Enter an AI Studio size target between 1 and 100000 MB.')
    return mb


def fit(width, height, longest):
    ratio = min(1, longest / max(width, height))
    return max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)


def plan(source, output, metadata, preset, target_mb=380):
    if preset not in ('aistudio', 'youtube'):
        raise videotool.VideoToolError('Unknown upload preset.')
    source = videotool.validate_source(source)
    output = videotool.validate_output(source, output)
    if output.suffix.lower() != '.mp4':
        raise videotool.VideoToolError('Upload output must be an .mp4 file.')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise videotool.VideoToolError('ffmpeg was not found. Install FFmpeg first.')
    video = videotool.select_video(metadata)
    try:
        duration = float(metadata.get('format', {}).get('duration', 0))
        rate = Fraction(video.get('avg_frame_rate', '0/1'))
        fps = float(rate)
        width, height = int(video['width']), int(video['height'])
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        raise videotool.VideoToolError('Cannot read duration, frame rate, or dimensions. Export a standard video from DaVinci.')
    if not math.isfinite(duration) or duration <= 0 or not 0 < fps <= 240 or min(width, height) < 2:
        raise videotool.VideoToolError('A valid duration, dimensions, and frame rate (up to 240 fps) are required.')
    if video.get('field_order') not in (None, 'unknown', 'progressive'):
        raise videotool.VideoToolError('This preset needs a progressive export. Deinterlace in DaVinci first.')
    unknown = (None, 'unknown', 'unspecified', 'reserved')
    for key in ('color_transfer', 'color_primaries', 'color_space'):
        if video.get(key) not in (*unknown, 'bt709'):
            raise videotool.VideoToolError('These presets need a finished SDR Rec.709 YUV export. Export SDR from DaVinci first; HDR/log conversion is not automatic.')
    if video.get('sample_aspect_ratio') not in (None, 'N/A', '0:1', '1:1'):
        raise videotool.VideoToolError('Export with square pixels from DaVinci before preparing an upload.')
    rotations = [s.get('rotation', 0) for s in video.get('side_data_list', [])]
    rotations.append(video.get('tags', {}).get('rotate', 0))
    try:
        rotated = any(float(value) % 360 for value in rotations)
    except (TypeError, ValueError):
        raise videotool.VideoToolError('Cannot read rotation metadata. Export upright video from DaVinci first.')
    if rotated:
        raise videotool.VideoToolError('Export upright video from DaVinci first; rotation metadata is not applied by these presets.')
    audios = [s for s in metadata['streams'] if s.get('codec_type') == 'audio']
    defaults = [s for s in audios if s.get('disposition', {}).get('default')]
    if len(audios) > 1:
        if len(defaults) != 1:
            raise videotool.VideoToolError('Export one mixed audio track from DaVinci, or mark one track as the default.')
        audios = defaults
    audio = audios[0] if audios else None
    channels = 1 if audio and audio.get('channels') == 1 else 2
    audio_rate = (128000 if channels == 1 else 384000) if preset == 'youtube' else 128000
    if not audio:
        audio_rate = 0
    target_mb = size_target(target_mb) if preset == 'aistudio' else 380
    max_bytes = int(target_mb * 1_000_000) if preset == 'aistudio' else None
    target_width, target_height = fit(width, height, max(width, height))
    video_rate = None
    if max_bytes:
        # Leave 5% for rate-control variation and MP4 overhead; verify the actual result.
        video_rate = min(8_000_000, int(max_bytes * 8 * .95 / duration) - audio_rate)
        for edge in (1920, 1280):
            target_width, target_height = fit(width, height, edge)
            minimum = max(500_000, int(target_width * target_height * fps * .05))
            if video_rate >= minimum:
                break
        else:
            raise videotool.VideoToolError(
                f'{target_mb:g} MB is too small for this duration at the preset’s minimum quality. '
                'Increase the target or export shorter sections from DaVinci. No output was created.')
        estimated_bytes = min(
            max_bytes, math.ceil((video_rate + audio_rate) * duration / 8 * 1.02))
    else:
        # CRF output depends heavily on picture detail and motion. This midpoint is
        # presented as approximate and exists only for storage planning.
        estimated_video_rate = min(35_000_000, max(
            1_000_000, target_width * target_height * fps * 0.14))
        estimated_bytes = math.ceil((estimated_video_rate + audio_rate) * duration / 8 * 1.03)
    command = [ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-n', '-noautorotate',
               '-i', str(source), '-map', f"0:{video['index']}"]
    if audio:
        command += ['-map', f"0:{audio['index']}"]
    command += ['-c:v', 'libx264', '-preset', 'slow', '-profile:v', 'high', '-pix_fmt', 'yuv420p',
                '-bf', '2', '-g', str(max(1, round(fps / 2))), '-keyint_min', str(max(1, round(fps / 2))),
                '-sc_threshold', '0', '-flags', '+cgop', '-x264-params', 'open-gop=0:cabac=1:b-adapt=0',
                '-r', str(rate), '-fps_mode', 'cfr',
                '-vf', f'scale={target_width}:{target_height}:flags=lanczos,setsar=1']
    for key, option in (('color_range', '-color_range'), ('color_space', '-colorspace'),
                        ('color_transfer', '-color_trc'), ('color_primaries', '-color_primaries')):
        if video.get(key) not in unknown:
            command += [option, video[key]]
    command += ['-b:v', str(video_rate)] if video_rate else ['-crf', '18']
    first_pass = command[:] if preset == 'aistudio' else []
    if audio:
        command += ['-c:a', 'aac', '-profile:a', 'aac_low', '-b:a', str(audio_rate), '-ar', '48000', '-ac', str(channels)]
    command += ['-map_metadata', '-1', '-sn', '-dn', '-movflags', '+faststart', '-use_editlist', '0',
                '-avoid_negative_ts', 'make_zero', '-f', 'mp4', str(output)]
    detail = (f'{PRESETS[preset]} · MP4 · H.264 High · 8-bit 4:2:0\n'
              f'{target_width} × {target_height} · {fps:.3f} fps constant; original export retained.\n')
    detail += f'AAC-LC {channels}-channel audio, 48 kHz; {audio_rate // 1000} kb/s.\n' if audio else 'No audio in source.\n'
    if audio and len([s for s in metadata['streams'] if s.get('codec_type') == 'audio']) > 1:
        detail += f"Only default audio stream {audio['index']} is included.\n"
    if audio and audio.get('channels', 2) > 2:
        detail += 'Surround audio is mixed down to stereo.\n'
    if max_bytes:
        detail += (f'Target: at most {target_mb:g} MB per file (decimal MB); two passes, {video_rate / 1e6:.2f} Mb/s video.\n'
                   f'Approximate output: {videotool.readable_size(estimated_bytes)}; the target remains the hard maximum.\n'
                   'This is your size target, not a verified AI Studio limit. Actual size is checked after encoding.\n')
    else:
        detail += (f'Quality-focused encoding (CRF 18); resolution retained apart from even-pixel rounding. No size cap.\n'
                   f'Approximate output: {videotool.readable_size(estimated_bytes)}; actual size may be much smaller or larger depending on motion and picture detail.\n')
    if any(video.get(key) in unknown for key in ('color_space', 'color_transfer', 'color_primaries')):
        detail += 'Some color tags are missing: use a finished SDR Rec.709 export and check the result.\n'
    detail += 'Lossy copy. No HDR/log tone mapping. Upload manually after checking picture and sound.'
    return UploadPlan(source, output, command, first_pass, duration, max_bytes, detail,
                      target_width, target_height, audio is not None, estimated_bytes)


def execute(plan, progress, cancel=None):
    videotool.validate_output(plan.source, plan.output)
    before = plan.source.stat()
    plan.executed_commands.clear()
    def run(command, index, passes):
        plan.executed_commands.append(videotool.command_with_progress(command))
        result = videotool.run_with_progress(command, lambda seconds: progress(
            (index * plan.duration + min(seconds, plan.duration)) / passes), cancel)
        if result.returncode or result.stderr:
            raise videotool.VideoToolError(f'Upload preparation failed: {(result.stderr or "FFmpeg failed").strip()}. '
                                          f'A partial output may remain at {plan.output}; it is preserved.')
    with tempfile.TemporaryDirectory(prefix='videotool-pass-') as temp:
        if plan.first_pass:
            log = str(Path(temp) / 'encode')
            run(plan.first_pass + ['-pass', '1', '-passlogfile', log, '-an', '-f', 'null', '-'], 0, 2)
            if cancel is not None and cancel.is_set():
                raise videotool.ConversionCancelled(
                    'Conversion cancelled after the first pass. No second pass was started.')
            videotool.validate_output(plan.source, plan.output)
            run(plan.command[:-1] + ['-pass', '2', '-passlogfile', log, str(plan.output)], 1, 2)
        else:
            run(plan.command, 0, 1)
    after = plan.source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise videotool.VideoToolError('Source changed during encoding. Output is retained but not marked complete.')
    actual = plan.output.stat().st_size
    if plan.max_bytes is not None and actual > plan.max_bytes:
        raise videotool.VideoToolError(f'Output is {actual / 1e6:.2f} MB, above your {plan.max_bytes / 1e6:g} MB target. '
                                      'It is retained but not marked ready. Increase the target or export shorter sections, then preview again.')
    metadata = videotool.inspect_video(plan.output)
    video = videotool.select_video(metadata)
    audio = [s for s in metadata['streams'] if s.get('codec_type') == 'audio']
    try:
        duration = float(metadata.get('format', {}).get('duration', 0))
    except (ValueError, TypeError):
        duration = 0
    if (actual <= 0 or video.get('codec_name') != 'h264' or video.get('pix_fmt') != 'yuv420p' or
            (video.get('width'), video.get('height')) != (plan.width, plan.height) or
            bool(audio) != plan.audio or any(s.get('codec_name') != 'aac' or s.get('sample_rate') != '48000' or s.get('channels') not in (1, 2) for s in audio) or
            not math.isfinite(duration) or abs(duration - plan.duration) > 1):
        raise videotool.VideoToolError('Output validation failed. File is retained but not marked ready for upload.')
    return f'Prepared for manual upload · actual size {actual / 1e6:.2f} MB. Nothing has been uploaded.'
