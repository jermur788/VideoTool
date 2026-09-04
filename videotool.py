"""Inspect videos and prepare DaVinci intermediates. Requires FFmpeg on PATH."""

import argparse
import json
import math
from pathlib import Path
import shutil
import shlex
import subprocess
import sys


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


def plan_davinci(source, output, metadata):
    """Build an argument list only: no output creation and no conversion."""
    source = validate_source(source)
    output = validate_output(source, output)
    if output.suffix.lower() != ".mov":
        raise VideoToolError("DaVinci output must have a .mov extension.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VideoToolError("ffmpeg was not found. Install FFmpeg and ensure it is on PATH.")
    video = select_video(metadata)
    width, height = video.get("width", 0), video.get("height", 0)
    if width < 256 or height < 120 or width % 2 or height % 2:
        raise VideoToolError("DNxHR requires even dimensions of at least 256 x 120; no resizing is automatic.")
    audio = [s for s in metadata["streams"] if s.get("codec_type") == "audio"]
    command = [ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-n", "-noautorotate", "-i", str(source),
               "-map", f"0:{video['index']}"]
    for stream in audio:
        command += ["-map", f"0:{stream['index']}"]
    command += ["-c:v", "dnxhd", "-profile:v", "dnxhr_hqx", "-pix_fmt", "yuv422p10le",
                "-fps_mode", "passthrough"]
    # Carry the known source tags; never assume Rec.709 or apply a LUT/tone map.
    for key, option in (("color_range", "-color_range"), ("color_space", "-colorspace"),
                        ("color_transfer", "-color_trc"), ("color_primaries", "-color_primaries")):
        value = video.get(key)
        if value and value not in ("unknown", "unspecified", "reserved"):
            command += [option, value]
    if audio:
        command += ["-c:a", "pcm_s24le"]
    command += ["-sn", "-dn", "-write_tmcd", "0", "-f", "mov", str(output)]
    return command


def print_plan(source, output, metadata, command):
    video = select_video(metadata)
    print(f"Source: {source}\nOutput: {output}")
    print("Purpose: create an editing intermediate for DaVinci Resolve Free on Linux.")
    print(f"Video stream {video['index']}: DNxHR HQX, 10-bit 4:2:2; "
          f"{video['width']} x {video['height']}; source frame timing retained "
          f"(average {frame_rate(video.get('avg_frame_rate'))} fps).")
    audio = [str(s['index']) for s in metadata['streams'] if s.get('codec_type') == 'audio']
    print(f"Audio: PCM 24-bit; streams {', '.join(audio)}; source rate/channels retained."
          if audio else "Audio: none in source.")
    print("Tradeoffs: much larger files; lossy video re-encode. 4:2:0 to 4:2:2 adds no source detail.")
    print("No scaling, LUT, tone mapping, or deliberate color-space change. Known color tags retained.")
    print("Preview images, subtitles, and data/timecode tracks are omitted. Source is retained.")
    print("Overwrites are refused. Resolve picture/sound import still needs a manual check.")
    print("\nFFmpeg command:\n" + shlex.join(command), flush=True)


def execute_davinci(source, output, command):
    # Recheck immediately before launch; -n also refuses an output created since planning.
    validate_output(source, output)
    print("Converting; FFmpeg errors will be shown when it finishes.", flush=True)
    try:
        result = subprocess.run(command, check=False, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Read video metadata without modifying the file")
    inspect.add_argument("source", help="Path to a local video file")
    inspect.add_argument("--json", action="store_true", help="Print full ffprobe metadata as JSON")
    davinci = commands.add_parser("davinci", help="Preview a DNxHR HQX / PCM MOV conversion")
    davinci.add_argument("source", help="Path to a local video file")
    davinci.add_argument("--output", help="New .mov path (default: SOURCE_davinci.mov beside source)")
    davinci.add_argument("--execute", action="store_true",
                         help="Confirm the reviewed plan and actually create the output")
    args = parser.parse_args(argv)
    try:
        source = validate_source(args.source)
        metadata = inspect_video(source)
        if args.command == "davinci":
            output = validate_output(source, args.output or source.with_name(source.stem + "_davinci.mov"))
            command = plan_davinci(source, output, metadata)
            print_plan(source, output, metadata, command)
            if args.execute:
                execute_davinci(source, output, command)
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
