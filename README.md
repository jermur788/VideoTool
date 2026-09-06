# VideoTool

A small local Python command-line tool for inspecting videos and creating
DaVinci editing intermediates with FFmpeg, with an optional desktop window.
Source files are retained.

The proposed Gemini analysis and Google Drive delivery workflow is documented in
[NEXT_VERSION.md](NEXT_VERSION.md).

## Desktop window

Hover over any button for a short explanation, including buttons that are currently
disabled. Hints disappear when you move away, click, press Escape, or change focus.

On Linux Mint/Ubuntu, install the window library once:

```bash
sudo apt install python3-tk
```

Open the interface from the VideoTool folder:

```bash
python3 videotool_gui.py
```

1. Choose a preparation preset, then use **Choose source** to select one video or a folder.
   For upload presets, choose finished DaVinci exports. DaVinci copies automatically preserve
   source bit depth. Open **Advanced settings** only when you want to use a DNxHR MOV that you
   have already confirmed works in Resolve as a format reference.
2. Optionally choose an existing output folder; otherwise copies go beside the originals.
3. Optionally enter a location under **Name files by location**, such as `Dublin`, for names like
   `Dublin_001.mov`, `Dublin_002.mov`. Leave it blank to keep the default names.
4. Click **Preview**. The list shows source size, duration, detected bit depth, output name,
   and color-coded status. Select a row to reveal its full paths, format, or error.
5. Review the ready files and click **Convert ready files** to confirm and start. Progress,
   retry, receipt, and output-folder controls appear when they are useful.

When the desktop Tcl/Tk installation provides TkDND, a video or folder can also be dropped
onto the window. **Choose source** remains available on systems without that optional support.

Location numbering continues after the highest existing number in the output
folder: if `Dublin_007.mov` exists, new files start at `Dublin_008.mov`. Gaps are
not reused. Existing files, folders, and links reserve their names, including
case variants. New sources are numbered in filename sort order. Changing the
location requires a new preview. Existing numbered exports for that location
are excluded from the folder scan. Spaces and accented letters are supported;
path separators and special filename characters are refused.

Successful conversions made by this version are recorded in a small hidden
`.videotool-history.sqlite3` file in the output folder. When the original source,
location, and recorded output still match, a later preview shows **Done** and
skips conversion. This works after closing the app and when adding footage to
an existing source folder. Keep the history file with the output folder. If
saving history fails, the completed row shows a warning; the open window still
remembers its successes.

Each run that completes at least one video also creates a human-readable Markdown
file named `VideoTool-processing-YYYYMMDD-HHMMSS.md` in the output folder. It
records the source and output paths and sizes, video and audio formats, preset
parameters, exact FFmpeg command or two-pass commands, FFmpeg version, source
duration, per-file elapsed time, effective speed relative to realtime, validation
scope, and batch totals. **View processing receipt** opens the latest record;
**Copy summary** copies the latest run counts, elapsed time, and receipt location.
The command-line `davinci --execute` and `batch --execute` workflows create the
same receipt format.

Receipt timing measures local encoding time for each file. Batch timing covers
the attempted run. The SQLite history also stores source/output sizes, duration,
elapsed time, conversion speed, and the associated receipt name for newly
completed files. Existing history databases need no migration; older entries
without these fields remain readable.

The receipt distinguishes local validation from external testing. “Completed”
means FFmpeg finished and VideoTool performed its available local checks. It does
not mean DaVinci Resolve successfully imported the file, or that AI Studio or
YouTube accepted an upload. If the Markdown file cannot be written, completed
video remains successful and the interface reports the receipt error.

Older exports without a history record reserve their numbers, but the app cannot
reliably identify which source created them. When adding footage to those older
exports, select only the new footage or put new sources in their own folder.
Use a separate output folder when working with exports from several locations.

The window stays responsive while inspecting or converting. It shows each file's
status and a final results summary. **Stop after current file** lets the active
conversion finish, then leaves the remaining files unprocessed. **Cancel current
conversion** terminates the active FFmpeg process and also stops the batch. The
current file is marked **Interrupted**, is never recorded as successful, and any
partial output remains in place. Files later in the batch do not start. During an
AI Studio two-pass conversion, cancelling during or after the first pass prevents
the second pass from starting; because pass one does not create the target file,
there may be no partial output in that case. Close the window after work finishes.

After a failure, stop, or cancellation, click **Preview retry**, review the new list, then
**Convert ready files**. Completed files remain **Done** and are not converted
again. Unfinished files keep their planned names when available. A partial or
existing output is retained; the retry gets the next location number, or a
unique `_retry_001_davinci.mov` suffix when no location is entered. No existing
output is deleted or overwritten. The retry preview inspects sources again,
so repaired inputs can be retried.

The completion summary shows completed, failed/blocked, and unfinished counts
across the list, including prior successes, and the destination path. **Open
output folder** opens that folder in the desktop file manager (or the selected
row's output folder if there is more than one). Progress percentages during a
retry describe only the remaining ready files.

Only files captured in the preview are converted. Changing the selection requires
a new preview, and sources changed since inspection are refused. Existing outputs
are preserved and shown as blocked. Conversion failures do not stop other ready files.
Two progress bars show the current file and the whole batch, with estimated time
remaining based on measured conversion speed. Batch progress is weighted by video
duration across ready files. Estimates appear after initial progress and can change
with clip resolution, codec, or system load. If duration metadata is unavailable,
the corresponding percentage and estimate are unavailable. A file reaches 100%
only after FFmpeg finishes successfully; failures remain visible in the results.
Single-file names use `clip_davinci.mov`; folder mode uses `clip.mp4_davinci.mov`.

Python 3.10+, FFmpeg/ffprobe, Tkinter, and a graphical desktop are required.
The command-line tool still works without Tkinter.

## Prepare finished exports for manual upload

Use the **Prepare for** selector in the desktop window:

| Preset | Result |
| --- | --- |
| DaVinci editing copy | DNxHR/PCM MOV that keeps 8-bit sources at 8-bit and 10-bit sources at 10-bit, or settings learned from a working reference. |
| AI Studio upload | A smaller H.264/AAC MP4, using an adjustable maximum target per file. |
| YouTube upload | A quality-focused H.264/AAC MP4 at source resolution, with no file-size cap. |

Choose the finished export, select the preset, and click **Preview** to see the
output name, dimensions, audio handling, and tradeoffs. Then convert the ready
files. The original exports are retained. Open the output folder and upload
completed files yourself; VideoTool does not connect to accounts or upload files.
The upload presets are desktop features; the existing command-line commands
continue to prepare editing copies.

### AI Studio size target

The default **380 MB** is a user workflow target chosen to leave headroom under
the user's observed possible 400 MB upload cap. It is **not a verified universal
AI Studio limit**. There is no five-minute cutoff. The target is per output file,
uses decimal MB (1 MB = 1,000,000 bytes), and accepts values from 1 to 100000 MB.

Encoding uses two passes with a bitrate budget leaving 5% for overhead and
variation. The longest edge is limited to 1920 pixels, or 1280 if the available
bitrate is too low; videos are never upscaled. Frame rate is retained as the
source average at constant frame rate. The preview shows the chosen dimensions
and bitrate. Video bitrate is capped at 8 Mb/s for this analysis-oriented copy.

A preset guard refuses targets below the greater of 500 kb/s or 0.05 bits per
pixel per frame at the smallest chosen dimensions. This is a practical quality
floor, not a guarantee of visual quality. Increase the size target or export
shorter sections from DaVinci if it is refused.

The bitrate calculation is an estimate. **Actual size is checked after encoding**,
along with codec, dimensions, audio, and duration. A result above the target is
preserved but marked failed, not ready, and is not saved as a successful conversion.
No output is silently truncated to meet the target. Retry uses a new name if the
old output exists. AI Studio's acceptance still needs a manual upload check.

### YouTube quality preset

The preset follows YouTube's [official upload encoding guidance](https://support.google.com/youtube/answer/1722171?hl=en),
checked on 2026-09-06: fast-start MP4, H.264 High Profile with 4:2:0 video, progressive
frames, closed GOPs, and AAC-LC audio at 48 kHz. It uses quality-based variable
bitrate (CRF 18, slow preset), retaining resolution except even-pixel rounding and
the source average frame rate. CRF 18 is VideoTool's quality choice, not a YouTube
requirement. Stereo audio uses 384 kb/s; mono uses 128 kb/s. There is no size cap.

### Supported finished exports and existing workflows

These upload presets expect progressive, square-pixel SDR Rec.709 YUV exports.
Known HDR, other tagged color spaces, interlacing, and rotation metadata are refused
with instructions to prepare a suitable finished export in DaVinci. Missing color
tags are called out in the preview: confirm that the source is a finished SDR export.
No HDR/log tone mapping is performed. Upload copies are 8-bit, lossy derivatives.

One audio track is included (the sole track or a uniquely marked default).
Surround audio is mixed to stereo, as disclosed in the preview. Silent exports
are supported. Subtitle, data, and other audio tracks are omitted.

Upload presets can scan incoming `_davinci.mov` files. They exclude their own
`_aistudio.mp4` and `_youtube.mp4` outputs and recognised upload receipts from folder
scans. Use a folder of finished exports, preferably with a separate destination.
Default names include the preset, such as `Finished_aistudio.mp4` and
`Finished_youtube.mp4`. Location naming uses sequential `.mp4` names and continues
past existing numbers. Retry, progress, completion summaries, and hover hints work
for all presets. AI Studio progress covers both passes. Saved successes are scoped
to the preset and AI size target, so one preparation cannot stand in for another.

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

The automatic MOV format checks the source video depth. It uses **DNxHR SQ with
8-bit 4:2:2 video** for 8-bit footage and **DNxHR HQX with 10-bit 4:2:2 video**
for 10-bit footage. Audio uses uncompressed 16-bit PCM. A mixed folder is decided
one file at a time. Unknown, conflicting, and unsupported source depths are
refused instead of being silently reduced. These editing copies remain much
larger than camera HEVC, so keep the original camera files as masters. Upsampling
4:2:0 to 4:2:2 adds no captured detail, and PCM cannot restore detail already
lost in AAC.

Use **Choose reference** in the window when you have a DNxHR MOV already verified
in Resolve. VideoTool reads that file and reuses its DNxHR profile (LB, SQ, HQ, or
HQX), pixel depth, and PCM depth. Choosing a reference explicitly overrides the
automatic source-depth choice. It rejects non-DNxHR video, non-PCM audio,
unexpected pixel formats, and ambiguous primary video. A reference is a format
example only; its resolution, frame rate, and channel layout are not imposed on
new footage. Selecting an HQX reference will deliberately produce large HQX files;
selecting LB makes much smaller files with lower editing-copy quality.

Preview shows a cautious output-size estimate with 10% headroom and the free
space in the destination. Estimates based on a reference use its measured data
rate, scaled for the new resolution and frame rate. Automatic SQ/HQX estimates
use profile-rate approximations. Conversion checks free space again immediately
before starting and refuses a file when its estimate exceeds the available space.
The estimate is planning guidance rather than an exact output-size guarantee.

Resolution, frame timing, and audio sample rate/channel count are retained. No
LUT, tone mapping, scaling, or automatic rotation is applied; known source color
tags are carried forward.

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

The desktop window also provides the YouTube and AI Studio preparation workflows
described above.

## Convert a folder

Preview videos directly inside a folder:

```bash
python3 videotool.py batch "/full/path/to/footage"
```

To learn from a previously verified DNxHR/PCM file, add `--reference` during
both preview and execution:

```bash
python3 videotool.py davinci "1.MP4" --reference "/path/to/working_DNxHR_SQ.mov"
python3 videotool.py batch "/full/path/to/footage" --reference "/path/to/working_DNxHR_SQ.mov"
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
Preview reports estimated batch output and available space. Allow ample disk
space for the much larger DNxHR outputs.

## Check the code

Tests use temporary dummy files, simulated ffprobe responses, and a tiny generated
test clip when FFmpeg is installed. They never process your footage:

```bash
python3 -B -m unittest discover -s tests -v
```
