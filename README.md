# VideoTool

A small local Python command-line tool for inspecting videos and creating
DaVinci editing intermediates with FFmpeg. Source files are retained.

## Run it in VS Code

1. Open this VideoTool folder in VS Code.
2. Choose **Terminal → New Terminal**. The terminal should be in this folder.
3. Run the following, replacing the example path with your video's full path.
   Keep the quotes, especially if the path contains spaces:

   ```bash
   python3 videotool.py inspect "/full/path/to/video.mp4"
   ```

The summary shows the container, duration, file size, video codec, dimensions,
average frame rate, pixel format, color metadata, and audio information.
Missing metadata is shown as unknown. Metadata alone does not certify DaVinci
Resolve compatibility.

For the full metadata or help:

```bash
python3 videotool.py inspect "/full/path/to/video.mp4" --json
python3 videotool.py --help
python3 videotool.py inspect --help
```

Requirements: Python 3.10+ and FFmpeg with `ffprobe` on PATH. No Python packages,
virtual environment, or installation step is needed. Python 3.12.3 and FFmpeg
6.1.1 were available on this machine when this was created.

## Scope and safety

`inspect` reads a local regular file and runs
ffprobe with a 60-second timeout. Missing files, unreadable media, files without
video, and missing ffprobe produce an error and a nonzero exit code.

`validate_output()` refuses source
reuse, existing output files (including hard links), symbolic-link outputs, and
missing destination folders. FFmpeg also uses `-n` to refuse overwrites at
execution time. If a conversion fails or is interrupted, a partial output may
remain. Review it yourself; the tool will not delete or overwrite it.

## Prepare footage for DaVinci Resolve Free on Linux

Preview first; this command reads metadata and creates no output:

```bash
python3 videotool.py davinci "1.MP4"
```

It prints the exact source, proposed output, selected streams, tradeoffs, and
FFmpeg command. The default output is `1_davinci.mov` beside the original.
Use `--output "/full/path/new-name.mov"` to choose another new path in an existing
folder. Existing files are always refused.

After reviewing and approving that exact operation, adding `--execute` creates
the output. Running this yourself explicitly confirms the conversion:

```bash
python3 videotool.py davinci "1.MP4" --execute
```

When an assistant is operating this tool, it must present the exact source,
output, purpose, and significant tradeoffs and obtain your explicit confirmation
before executing a conversion on your media.

The MOV uses DNxHR HQX with 10-bit 4:2:2 video and uncompressed 24-bit PCM audio.
This avoids reducing the Action 4's 10-bit video to 8-bit, but is still a lossy
re-encode. Files will be much larger than camera HEVC; allow ample disk space.
Upsampling 4:2:0 to 4:2:2 adds no captured detail, and PCM cannot restore detail
already lost in AAC. Resolution, frame timing, and audio sample rate/channel
count are retained. No LUT, tone mapping, scaling, or automatic rotation is
applied; known source color tags are carried forward.

The tool selects the sole normal video stream, or a uniquely marked default
video stream. Ambiguous selections are refused. Attached images and thumbnails
are excluded; all audio streams are included. Data, subtitles, and timecode
tracks are omitted. Variable-frame-rate sources keep their frame timing; this
command does not normalize them for editing.

Codec choice is based on Blackmagic's [Resolve 20 supported codec list](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20_Supported_Codec_List.pdf),
Linux tables on pages 11 and 14: DNxHR MOV and PCM are supported; H.264/H.265
decode is Studio-only and AAC is unsupported. The published Linux table targets
Rocky Linux/CUDA; actual import on your installation still requires a picture
and sound check in Resolve. On 2026-09-05, the user confirmed that a file
converted by VideoTool works in DaVinci Resolve. See [test results](TEST_RESULTS.md)
for the recorded result and scope.
FFmpeg option reference: [official documentation](https://ffmpeg.org/ffmpeg.html).

YouTube and Gemini analysis derivatives remain future work.

## Convert a folder

Preview videos directly inside a folder:

```bash
python3 videotool.py batch "/full/path/to/footage"
```

After reviewing the plan, convert the ready files:

```bash
python3 videotool.py batch "/full/path/to/footage" --execute
```

Use `--output-dir "/full/path/to/exports"` with either command to select an
existing destination folder. By default, outputs are beside the sources.
Names include the original extension: `clip.mp4` becomes `clip.mp4_davinci.mov`,
so files with the same stem and different extensions have separate outputs.

The scan includes MP4, MOV, MKV, AVI, M4V, MTS, M2TS, WEBM, MPG, MPEG, and MXF
(case-insensitive). Subfolders, symbolic links, and names ending in
`_davinci.mov` are excluded. Each candidate is inspected before conversion.
Existing outputs are reported as failures and preserved; other ready files
continue. A failed or partial output is never deleted automatically.

Conversions run one at a time, with a file counter and final success/failure
counts. Ctrl+C during conversion stops the batch; remaining files are reported
as not attempted. Exit status is 0 for success, 1 for any failure, or 130 for
an interrupted conversion. Progress is per file, not a percentage within a file.
Allow ample disk space for the much larger DNxHR outputs.

## Check the code

Tests use temporary dummy files, simulated ffprobe responses, and a tiny generated
test clip when FFmpeg is installed. They never process your footage:

```bash
python3 -B -m unittest discover -s tests -v
```
