"""Inspect videos and prepare DaVinci intermediates. Requires FFmpeg on PATH."""

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import tempfile


class VideoToolError(Exception):
    """An expected input or dependency error, suitable for display to the user."""


def validate_source(value):
    """Accept a local regular file and resolve links before using it."""
    try:
        source = Path(value).expanduser().resolve(strict=True)
        if not source.is_file():
            raise VideoToolError(f"Source is not a regular file: {source}")
        return source
    except (OSError, RuntimeError) as exc:
        raise VideoToolError(f"Cannot access source '{value}': {exc}") from exc


def validate_output(source, value):
    """Check a future output path without creating it or changing any files.

    Future conversions must also use FFmpeg's -n option to refuse overwrites
    at execution time; this preflight check alone cannot prevent races.
    """
    source = validate_source(source)
    try:
        candidate = Path(value).expanduser()
        # Reject even dangling symlinks; resolving them first hides their existence.
        if candidate.is_symlink():
            raise VideoToolError(f"Output is a symbolic link: {candidate}")
        output = candidate.resolve()
        if output == source:
            raise VideoToolError("Output must be different from the source.")
        if output.exists():
            raise VideoToolError(f"Output already exists; overwriting is refused: {output}")
        if not output.parent.is_dir():
            raise VideoToolError(f"Output folder does not exist: {output.parent}")
        return output
    except (OSError, RuntimeError) as exc:
        raise VideoToolError(f"Cannot use output '{value}': {exc}") from exc


def inspect_video(source):
    source = validate_source(source)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise VideoToolError("ffprobe was not found. Install FFmpeg and ensure ffprobe is on PATH.")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams",
             "-of", "json", str(source)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoToolError("Inspection timed out after 60 seconds.") from exc
    except OSError as exc:
        raise VideoToolError(f"Could not run ffprobe: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or "ffprobe could not read this file."
        raise VideoToolError(f"Inspection failed: {detail}")
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VideoToolError("ffprobe returned invalid JSON.") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("streams"), list):
        raise VideoToolError("ffprobe returned an unexpected metadata structure.")
    if not any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
               for s in metadata["streams"]):
        raise VideoToolError("No video stream was found in this file.")
    return metadata


def frame_rate(value):
    try:
        numerator, denominator = value.split("/")
        rate = float(numerator) / float(denominator)
        return f"{rate:.3f}" if math.isfinite(rate) and rate > 0 else "unknown"
    except (AttributeError, ValueError, ZeroDivisionError):
        return "unknown"


def print_summary(source, metadata):
    info = metadata.get("format", {})
    print(f"File: {source}")
    print(f"Container: {info.get('format_name', 'unknown')}")
    print(f"Duration (seconds): {info.get('duration', 'unknown')}")
    print(f"Size (bytes): {info.get('size', 'unknown')}")
    for stream in metadata["streams"]:
        kind = stream.get("codec_type", "unknown")
        print(f"\nStream {stream.get('index', '?')} ({kind}): {stream.get('codec_name', 'unknown')}")
        if kind == "video":
            print(f"  Dimensions: {stream.get('width', '?')} x {stream.get('height', '?')}")
            print(f"  Average frame rate: {frame_rate(stream.get('avg_frame_rate'))} fps")
            print(f"  Pixel format: {stream.get('pix_fmt', 'unknown')}")
            print(f"  Color: {stream.get('color_space', 'unknown')}; "
                  f"transfer: {stream.get('color_transfer', 'unknown')}; "
                  f"primaries: {stream.get('color_primaries', 'unknown')}")
        elif kind == "audio":
            print(f"  Sample rate: {stream.get('sample_rate', 'unknown')} Hz")
            print(f"  Channels: {stream.get('channels', 'unknown')}")


def select_video(metadata):
    videos = [s for s in metadata["streams"] if s.get("codec_type") == "video"
              and not any(s.get("disposition", {}).get(flag)
                          for flag in ("attached_pic", "timed_thumbnails", "still_image"))]
    defaults = [s for s in videos if s.get("disposition", {}).get("default")]
    candidates = defaults if len(defaults) == 1 else videos
    if len(candidates) != 1:
        raise VideoToolError("Cannot identify one primary video stream; refusing to guess.")
    return candidates[0]


DAVINCI_PROFILES = {
    'DNXHR LB': {'name': 'DNxHR LB', 'encoder_profile': 'dnxhr_lb',
                 'pixel_format': 'yuv422p', 'bit_depth': 8, 'estimate_bpp': 0.45},
    'DNXHR SQ': {'name': 'DNxHR SQ', 'encoder_profile': 'dnxhr_sq',
                 'pixel_format': 'yuv422p', 'bit_depth': 8, 'estimate_bpp': 1.45},
    'DNXHR HQ': {'name': 'DNxHR HQ', 'encoder_profile': 'dnxhr_hq',
                 'pixel_format': 'yuv422p', 'bit_depth': 8, 'estimate_bpp': 2.20},
    'DNXHR HQX': {'name': 'DNxHR HQX', 'encoder_profile': 'dnxhr_hqx',
                  'pixel_format': 'yuv422p10le', 'bit_depth': 10, 'estimate_bpp': 3.55},
}


def default_davinci_settings():
    """Return the 8-bit half of the automatic source-depth policy."""
    return {**DAVINCI_PROFILES['DNXHR SQ'], 'audio_codec': 'pcm_s16le',
            'audio_bits': 16, 'reference': None, 'reference_rate': None}


def source_bit_depth(metadata):
    """Identify supported 8- or 10-bit source video without silently reducing it."""
    video = select_video(metadata)
    raw = video.get('bits_per_raw_sample')
    try:
        reported = int(raw) if raw not in (None, '', '0', 0) else None
    except (TypeError, ValueError):
        reported = None
    pixel_format = str(video.get('pix_fmt') or '').lower()
    match = re.search(r'(?:p0?|gray)(9|10|12|14|16)(?:le|be)$', pixel_format)
    encoded = int(match.group(1)) if match else None
    if reported and encoded and reported != encoded:
        raise VideoToolError(f'Source bit-depth metadata conflicts: {reported}-bit data with '
                             f'pixel format {pixel_format}.')
    depth = reported or encoded
    if depth is None and (re.fullmatch(r'yuvj?\d+p', pixel_format) or
                          pixel_format in {'nv12', 'nv21', 'yuyv422', 'uyvy422', 'gbrp',
                                           'gray', 'gray8', 'rgb24', 'bgr24', 'rgba',
                                           'bgra', 'argb', 'abgr'}):
        depth = 8
    if depth not in (8, 10):
        detail = f'{depth}-bit' if depth else f'pixel format {pixel_format or "unknown"}'
        raise VideoToolError(f'Cannot safely choose an 8- or 10-bit DaVinci format for {detail}.')
    return depth


def davinci_settings_for_source(metadata):
    """Keep 8-bit sources 8-bit and 10-bit sources 10-bit."""
    profile = 'DNXHR HQX' if source_bit_depth(metadata) == 10 else 'DNXHR SQ'
    return {**DAVINCI_PROFILES[profile], 'audio_codec': 'pcm_s16le',
            'audio_bits': 16, 'reference': None, 'reference_rate': None,
            'automatic': True}


def davinci_settings_from_reference(reference):
    """Read a known-working DNxHR/PCM MOV and reproduce its useful codec choices."""
    reference = validate_source(reference)
    if reference.suffix.lower() != '.mov':
        raise VideoToolError('The working reference must be a .mov file.')
    metadata = inspect_video(reference)
    video = select_video(metadata)
    if video.get('codec_name') != 'dnxhd':
        raise VideoToolError('The working reference must contain DNxHR video.')
    profile_key = str(video.get('profile', '')).upper().replace('-', ' ').replace('_', ' ')
    profile_key = ' '.join(profile_key.split())
    settings = DAVINCI_PROFILES.get(profile_key)
    if not settings:
        raise VideoToolError(f"Unsupported DNxHR reference profile: {video.get('profile', 'unknown')}. "
                             "Supported profiles are LB, SQ, HQ, and HQX.")
    if video.get('pix_fmt') != settings['pixel_format']:
        raise VideoToolError(f"The reference uses unexpected pixel format {video.get('pix_fmt', 'unknown')} "
                             f"for {settings['name']}.")
    audio = [s for s in metadata['streams'] if s.get('codec_type') == 'audio']
    codecs = {s.get('codec_name') for s in audio}
    supported_audio = {'pcm_s16le': 16, 'pcm_s24le': 24, 'pcm_s32le': 32}
    if codecs and (len(codecs) != 1 or next(iter(codecs)) not in supported_audio):
        raise VideoToolError('The working reference must use one consistent PCM audio depth.')
    audio_codec = next(iter(codecs)) if codecs else 'pcm_s16le'
    duration = duration_value(metadata)
    try:
        size = int(metadata.get('format', {}).get('size', 0))
    except (TypeError, ValueError):
        size = 0
    rate = size * 8 / duration if size > 0 and duration else None
    return {**settings, 'audio_codec': audio_codec,
            'audio_bits': supported_audio[audio_codec], 'reference': reference,
            'reference_rate': rate, 'reference_width': video.get('width'),
            'reference_height': video.get('height'),
            'reference_fps': rate_value(video.get('avg_frame_rate'))}


def rate_value(value):
    try:
        numerator, denominator = value.split('/')
        rate = float(numerator) / float(denominator)
        return rate if math.isfinite(rate) and rate > 0 else None
    except (AttributeError, ValueError, ZeroDivisionError):
        return None


def duration_value(metadata):
    try:
        value = float(metadata.get('format', {}).get('duration', 0))
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def estimate_davinci_size(metadata, settings=None):
    """Return a cautious byte estimate, or None when duration/rate is unavailable."""
    settings = settings or davinci_settings_for_source(metadata)
    video = select_video(metadata)
    duration = duration_value(metadata)
    fps = rate_value(video.get('avg_frame_rate'))
    width, height = video.get('width'), video.get('height')
    if not duration or not fps or not isinstance(width, int) or not isinstance(height, int):
        return None
    reference_rate = settings.get('reference_rate')
    reference_pixels = (settings.get('reference_width') or 0) * (settings.get('reference_height') or 0)
    reference_fps = settings.get('reference_fps')
    if reference_rate and reference_pixels and reference_fps:
        video_rate = reference_rate * (width * height * fps) / (reference_pixels * reference_fps)
    else:
        video_rate = width * height * fps * settings['estimate_bpp']
        audio = [s for s in metadata['streams'] if s.get('codec_type') == 'audio']
        video_rate += sum(int(s.get('sample_rate', 48000)) * int(s.get('channels', 2)) *
                          settings['audio_bits'] for s in audio)
    # Container variation and the fact that this is a planning estimate need headroom.
    return math.ceil(video_rate * duration / 8 * 1.10)


def readable_size(value):
    if value is None:
        return 'unavailable'
    units = ('bytes', 'KB', 'MB', 'GB', 'TB')
    amount = float(value)
    for unit in units:
        if amount < 1000 or unit == units[-1]:
            return f'{amount:.1f} {unit}' if unit != 'bytes' else f'{int(amount)} bytes'
        amount /= 1000


def available_space(output):
    try:
        return shutil.disk_usage(Path(output).parent).free
    except OSError as exc:
        raise VideoToolError(f'Cannot check free space for {Path(output).parent}: {exc}') from exc


def require_space(output, estimated_bytes):
    free = available_space(output)
    if estimated_bytes is not None and estimated_bytes > free:
        raise VideoToolError(f'Estimated output {readable_size(estimated_bytes)} exceeds available space '
                             f'{readable_size(free)} in {Path(output).parent}.')
    return free


def plan_davinci(source, output, metadata, settings=None):
    """Build an argument list only: no output creation and no conversion."""
    source = validate_source(source)
    output = validate_output(source, output)
    if output.suffix.lower() != ".mov":
        raise VideoToolError("DaVinci output must have a .mov extension.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VideoToolError("ffmpeg was not found. Install FFmpeg and ensure it is on PATH.")
    settings = settings or davinci_settings_for_source(metadata)
    video = select_video(metadata)
    width, height = video.get("width", 0), video.get("height", 0)
    if width < 256 or height < 120 or width % 2 or height % 2:
        raise VideoToolError("DNxHR requires even dimensions of at least 256 x 120; no resizing is automatic.")
    audio = [s for s in metadata["streams"] if s.get("codec_type") == "audio"]
    command = [ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-n", "-noautorotate", "-i", str(source),
               "-map", f"0:{video['index']}"]
    for stream in audio:
        command += ["-map", f"0:{stream['index']}"]
    command += ["-c:v", "dnxhd", "-profile:v", settings['encoder_profile'],
                "-pix_fmt", settings['pixel_format'],
                "-fps_mode", "passthrough"]
    # Carry the known source tags; never assume Rec.709 or apply a LUT/tone map.
    for key, option in (("color_range", "-color_range"), ("color_space", "-colorspace"),
                        ("color_transfer", "-color_trc"), ("color_primaries", "-color_primaries")):
        value = video.get(key)
        if value and value not in ("unknown", "unspecified", "reserved"):
            command += [option, value]
    if audio:
        command += ["-c:a", settings['audio_codec']]
    command += ["-sn", "-dn", "-write_tmcd", "0", "-f", "mov", str(output)]
    return command


def print_plan(source, output, metadata, command, settings=None):
    settings = settings or davinci_settings_for_source(metadata)
    video = select_video(metadata)
    print(f"Source: {source}\nOutput: {output}")
    print("Purpose: create an editing intermediate for DaVinci Resolve Free on Linux.")
    print(f"Video stream {video['index']}: {settings['name']}, {settings['bit_depth']}-bit 4:2:2; "
          f"{video['width']} x {video['height']}; source frame timing retained "
          f"(average {frame_rate(video.get('avg_frame_rate'))} fps).")
    audio = [str(s['index']) for s in metadata['streams'] if s.get('codec_type') == 'audio']
    print(f"Audio: PCM {settings['audio_bits']}-bit; streams {', '.join(audio)}; source rate/channels retained."
          if audio else "Audio: none in source.")
    print(f"Settings: learned from {settings['reference']}"
          if settings.get('reference') else
          f"Settings: automatic source-depth choice ({source_bit_depth(metadata)}-bit source).")
    estimate = estimate_davinci_size(metadata, settings)
    free = available_space(output)
    print(f"Estimated output size: {readable_size(estimate)} (includes 10% planning headroom).")
    print(f"Available space: {readable_size(free)} in {Path(output).parent}.")
    print("Tradeoffs: much larger files; lossy video re-encode. 4:2:0 to 4:2:2 adds no source detail.")
    print("No scaling, LUT, tone mapping, or deliberate color-space change. Known color tags retained.")
    print("Preview images, subtitles, and data/timecode tracks are omitted. Source is retained.")
    print("Overwrites are refused. Resolve picture/sound import still needs a manual check.")
    print("\nFFmpeg command:\n" + shlex.join(command), flush=True)


def run_with_progress(command, progress):
    """Drain FFmpeg's machine-readable progress while keeping errors off that pipe."""
    with tempfile.TemporaryFile(mode='w+', encoding='utf-8', errors='replace') as errors:
        with subprocess.Popen(command[:1] + ['-progress', 'pipe:1', '-nostats'] + command[1:],
                              stdout=subprocess.PIPE, stderr=errors, text=True,
                              encoding='utf-8', errors='replace') as process:
            try:
                for line in process.stdout:
                    key, _, value = line.strip().partition('=')
                    if key == 'out_time_us':
                        try:
                            seconds = float(value) / 1_000_000
                        except ValueError:
                            continue
                        if math.isfinite(seconds):
                            progress(max(0, seconds))
                code = process.wait()
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        errors.seek(0)
        return subprocess.CompletedProcess(command, code, stderr=errors.read())


def execute_davinci(source, output, command, progress=None, estimated_bytes=None):
    # Recheck immediately before launch; -n also refuses an output created since planning.
    validate_output(source, output)
    require_space(output, estimated_bytes)
    print("Converting; FFmpeg errors will be shown when it finishes.", flush=True)
    try:
        if progress is None:
            result = subprocess.run(command, check=False, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace")
        else:
            result = run_with_progress(command, progress)
    except OSError as exc:
        raise VideoToolError(f"Could not run FFmpeg: {exc}") from exc
    except KeyboardInterrupt as exc:
        raise VideoToolError(f"Conversion interrupted. A partial output may remain at {output}; it will not be overwritten.") from exc
    # Some FFmpeg versions return zero even when -n refuses an existing output.
    # At log level 'error', stderr is itself a failure signal, including decode errors.
    if result.returncode or result.stderr:
        raise VideoToolError(f"FFmpeg did not complete cleanly (exit {result.returncode}): "
                             f"{(result.stderr or '').strip()}\nAn existing or partial output may remain at "
                             f"{output}; it will not be overwritten.")
    print(f"Created: {output}\nNext: check picture and sound in DaVinci Resolve.")


VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.m4v', '.mts', '.m2ts', '.webm', '.mpg', '.mpeg', '.mxf'}


def discover_batch(folder, output_dir=None, include_intermediates=False):
    """Return a stable list of candidates and an existing destination folder."""
    try:
        folder = Path(folder).expanduser().resolve(strict=True)
        destination = Path(output_dir).expanduser().resolve(strict=True) if output_dir else folder
        if not folder.is_dir() or not destination.is_dir():
            raise VideoToolError('Source and output must be existing folders.')
        sources = sorted((p for p in folder.iterdir()
                          if p.is_file() and not p.is_symlink()
                          and p.suffix.lower() in VIDEO_EXTENSIONS
                          and (include_intermediates or not p.name.lower().endswith('_davinci.mov'))),
                         key=lambda p: p.name)
    except (OSError, RuntimeError) as exc:
        raise VideoToolError(f'Cannot read batch folder: {exc}') from exc
    if not sources:
        raise VideoToolError('No matching video files found in this folder (subfolders are not scanned).')
    return sources, destination


def batch_davinci(folder, output_dir=None, execute=False, reference=None):
    """Preview all candidates before sequential conversion; never overwrite."""
    sources, destination = discover_batch(folder, output_dir)
    reference_settings = davinci_settings_from_reference(reference) if reference else None
    plans = []
    failed = 0
    for index, source in enumerate(sources, 1):
        print(f'\nPlanning [{index}/{len(sources)}]: {source.name}', flush=True)
        # Include the original extension so clip.mp4 and clip.mov cannot collide.
        output = destination / (source.name + '_davinci.mov')
        try:
            validate_output(source, output)
            metadata = inspect_video(source)
            settings = reference_settings or davinci_settings_for_source(metadata)
            command = plan_davinci(source, output, metadata, settings)
            print_plan(source, output, metadata, command, settings)
            plans.append((source, output, command, estimate_davinci_size(metadata, settings)))
        except VideoToolError as exc:
            failed += 1
            print(f'FAILED: {source.name}: {exc}', flush=True)
    print(f'\nBatch preview: {len(plans)} ready, {failed} failed.')
    estimates = [plan[3] for plan in plans]
    total_estimate = sum(estimates) if all(value is not None for value in estimates) else None
    if plans:
        print(f'Estimated batch output: {readable_size(total_estimate)}; '
              f'available: {readable_size(available_space(plans[0][1]))}.')
    if not execute:
        print('Preview only: no output created. Review before rerunning with --execute.')
        return 1 if failed else 0
    completed = 0
    interrupted = False
    attempted = 0
    for index, (source, output, command, estimated_bytes) in enumerate(plans, 1):
        print(f'\nConverting [{index}/{len(plans)}]: {source.name}', flush=True)
        attempted += 1
        try:
            execute_davinci(source, output, command, estimated_bytes=estimated_bytes)
            completed += 1
        except (VideoToolError, KeyboardInterrupt) as exc:
            failed += 1
            print(f'FAILED: {source.name}: {exc}', flush=True)
            if isinstance(exc, KeyboardInterrupt) or isinstance(exc.__cause__, KeyboardInterrupt):
                interrupted = True
                break
    print(f'\nBatch results: {completed} succeeded, {failed} failed, '
          f'{len(plans) - attempted} not attempted.')
    return 130 if interrupted else (1 if failed else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Read video metadata without modifying the file")
    inspect.add_argument("source", help="Path to a local video file")
    inspect.add_argument("--json", action="store_true", help="Print full ffprobe metadata as JSON")
    davinci = commands.add_parser("davinci", help="Preview a DNxHR / PCM MOV conversion")
    davinci.add_argument("source", help="Path to a local video file")
    davinci.add_argument("--output", help="New .mov path (default: SOURCE_davinci.mov beside source)")
    davinci.add_argument("--execute", action="store_true",
                         help="Confirm the reviewed plan and actually create the output")
    davinci.add_argument("--reference", help="Known-working DNxHR/PCM .mov whose format should be reused")
    batch = commands.add_parser('batch', help='Preview DaVinci conversions for videos in a folder')
    batch.add_argument('source', help='Folder to scan (no subfolders)')
    batch.add_argument('--output-dir', help='Existing output folder (default: source folder)')
    batch.add_argument('--execute', action='store_true', help='Confirm and convert all ready files')
    batch.add_argument('--reference', help='Known-working DNxHR/PCM .mov whose format should be reused')
    args = parser.parse_args(argv)
    try:
        if args.command == 'batch':
            return batch_davinci(args.source, args.output_dir, args.execute, args.reference)
        source = validate_source(args.source)
        metadata = inspect_video(source)
        if args.command == "davinci":
            output = validate_output(source, args.output or source.with_name(source.stem + "_davinci.mov"))
            settings = (davinci_settings_from_reference(args.reference) if args.reference else
                        davinci_settings_for_source(metadata))
            command = plan_davinci(source, output, metadata, settings)
            estimate = estimate_davinci_size(metadata, settings)
            print_plan(source, output, metadata, command, settings)
            if args.execute:
                execute_davinci(source, output, command, estimated_bytes=estimate)
            else:
                print("\nPreview only: no output created. Review this plan before rerunning with --execute.")
        elif args.json:
            print(json.dumps(metadata, indent=2, ensure_ascii=False))
        else:
            print_summary(source, metadata)
        return 0
    except VideoToolError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
