Test results — VideoTool

## Folder storage total and destination creation — 2026-09-08

- All 67 automated tests passed with the real desktop-display checks enabled.
- DaVinci folder previews show a persistent batch banner with the ready-file count,
  combined estimated output including headroom, destination free space, and the
  estimated remaining space or shortfall. Selected-file details explicitly label
  their estimate as applying only to that file.
- The **Output folder** menu can choose an existing folder or create one inside a
  selected parent. Tests cover safe creation, invalid names, existing-name refusal,
  and the actual desktop menu workflow.
- Only temporary generated or simulated media was processed. User footage and the
  user's already-running conversion were not accessed, changed, or interrupted.

## Simplified desktop interface — 2026-09-06

- All 65 automated tests passed with the real desktop-display checks enabled.
- The initial window hides advanced DaVinci reference settings, empty details,
  progress, retry, receipt, and output controls until they apply. Preset-specific
  controls are shown only for the selected workflow.
- The review table now verifies and displays source size, duration, detected bit
  depth, output name, and color-coded status. Location naming shows guidance when
  empty and a live numbered example after a name is entered.
- One **Choose source** menu handles video and folder selection. Native TkDND was
  installed and the desktop test verifies the registered drop binding and source
  selection callback.
- Only temporary generated or simulated media was processed. User footage was
  not converted, changed, or removed.

## Processing receipts and measured timing — 2026-09-06

- All 64 automated tests passed, including the opt-in desktop-display checks.
- Successful GUI and CLI runs create a collision-safe Markdown receipt beside
  outputs. Tests cover source/output technical details, exact FFmpeg commands,
  FFmpeg version, per-file and batch timing, effective realtime speed, preset
  details, validation wording, batch counts, SQLite timing fields, and receipt
  recovery after reopening.
- The desktop test opens the latest receipt and copies the run summary through
  the new controls. Receipt-write failure is reported without changing a valid
  video conversion into a failure.
- Only temporary generated or simulated media was processed. User footage was
  not converted, changed, or removed.

## Cancel current conversion — 2026-09-06

- All 61 automated tests passed with the opt-in desktop-display checks enabled.
- The new **Cancel current conversion** control terminates active FFmpeg work and
  stops the batch before another file starts. **Stop after current file** retains
  its earlier behavior and lets the active conversion finish.
- Tests verify process termination, interrupted/unfinished accounting, retained
  partial output, no successful history record, no next-file start, AI two-pass
  cancellation before pass two, hover help, and the complete desktop interaction.
- Only temporary generated or simulated media was used. User footage was not
  converted, changed, or removed.

## Automatic source-depth format and storage planning — 2026-09-06

- All 57 automated tests passed with the opt-in desktop-display checks enabled.
- Automatic DaVinci preparation now uses DNxHR SQ for 8-bit sources and DNxHR
  HQX for 10-bit sources, with 16-bit PCM audio. Mixed folders are decided per file.
- Tests verify automatic SQ selection for 8-bit sources, automatic HQX selection
  for 10-bit sources, refusal of unknown/unsupported depths, and learning
  LB/SQ/HQ/HQX and PCM depth from a working reference,
  rejecting incompatible references, estimated-size calculations, insufficient-
  space refusal, format-specific saved history, and real generated 10-bit HQX conversion.
- A read-only check identified `1.MP4` as 10-bit HEVC, selected HQX, and estimated
  89.3 MB. Using the existing
  `1_davinci.mov` as a reference correctly detected HQX/24-bit PCM and estimated
  88.4 MB. Neither preview created an output or altered footage.
- A read-only preview using `/media/jer/ZX20/Tiernaboul/Timeline 1.mov` correctly
  detected DNxHR LB and estimated 18.2 MB for the same short source. No LB output
  was created; this confirms reference learning rather than Resolve acceptance.
- The real-window suite passed against the host display, including the full
  retry workflow, hover explanations, and AI Studio/YouTube preset switching.
- Existing files and user footage were not converted, altered, or removed.

Date: 2026-09-05

Command used:
```
python -m unittest discover -s tests -p 'test_*.py' -v
```

Summary:
- Ran 12 tests in 0.405s
- Result: OK (all tests passed)

Notes:
- Tests ran locally in the workspace and did not modify source files.

## Manual DaVinci Resolve test

Date reported: 2026-09-05

Result: PASS — the user tested a file converted by VideoTool and confirmed
that the converted file works in DaVinci Resolve.

This is a user-reported result for the tested file. The filename, Resolve
version, and separate picture/audio checks were not recorded.

## Batch conversion checks — 2026-09-05

- All 16 automated tests passed, including preview-only behavior, file selection,
  distinct output names, existing-output preservation, continuing after failures,
  interruption, destination selection, and invalid/empty folders.
- A separate two-file batch using temporary generated video completed successfully;
  both outputs were verified as DNxHR HQX with ffprobe.
- These checks did not convert user footage. Batch outputs have not yet been
  manually tested in DaVinci Resolve.

## Desktop interface checks — 2026-09-05

- All 21 automated tests passed. New checks cover preview-only behavior, source
  changes after preview, existing-output protection, stopping between files,
  preserving the reviewed file list, and continuing after failures.
- A generated clip passed the interface's preview/conversion workflow and its
  output was verified as DNxHR HQX. Original bytes were preserved.
- The actual desktop window was exercised with a temporary generated clip:
  choose video, preview, convert, and preview again to verify overwrite blocking.
  The window layout was captured and visually checked.
- Tkinter and its BLT dependency were extracted into temporary storage for these
  window checks. System installation still requires `sudo apt install python3-tk`.
- User footage was not converted; manual DaVinci testing of interface-created
  outputs remains outstanding.

## Location-based output names — 2026-09-05

- All 24 automated tests passed, including sequential location names, preserving
  blocked numbers, excluding that location's previous numbered exports, accented
  names, invalid path characters, and reverting to default names.
- The actual window was tested with the location `Dublin`: preview showed
  `Dublin_001.mov`, conversion of a generated clip succeeded, and a second
  preview blocked the existing output. The layout was visually checked.
- No user footage or running conversion was changed by these checks.

## Conversion percentages and remaining time — 2026-09-05

- All 26 automated tests passed. Checks include duration-weighted batch progress,
  time estimates, unknown/invalid duration, and withholding 100% until completion.
- Real FFmpeg progress was received while converting a generated clip; current-file
  and whole-batch progress both reached 100% on successful completion.
- The desktop workflow was exercised again with generated footage and the two
  progress displays were visually checked. Existing user conversions were untouched.

## User acceptance of desktop updates — 2026-09-05

The user confirmed that the updated program works as expected after the interface,
location-based naming, and conversion-progress updates. This records the user's
overall acceptance; individual file details and a new DaVinci import check were
not supplied.


## Retry, continued numbering, and completion summary — 2026-09-06

- All 34 tests passed with the optional desktop workflow enabled.
- Checks cover preserved successful outputs, retained partial files, unique retry
  names, saved success recognition after reopening, new footage sorted before
  older footage, gaps/case variants/symbolic-link collisions, and output creation
  after preview. Modified outputs are not falsely treated as recorded successes.
- A real window workflow used temporary generated videos: one conversion succeeded,
  one simulated failure retained a partial output, retry converted only the failed
  file, and new footage continued at the next available number. Completion counts
  and the output-folder button were checked; the file-manager launch was mocked.
- User footage and existing conversions were not processed. These changes have
  not been pushed to GitHub.

Run the optional window check from a graphical desktop:

```bash
VIDEOTOOL_GUI_TESTS=1 python3 -B -m unittest discover -s tests -v
```


## Button hover explanations — 2026-09-06

- Added plain-language hover hints to all nine desktop buttons, including retry
  and Open output folder. Disabled buttons also display their explanations.
- All 35 tests passed with desktop checks enabled. Coverage verifies every button
  has its intended explanation, delayed display, dismissal on leaving/clicking/
  Escape/focus change, and cancellation when leaving or destroying a button.
- Existing retry, numbering, and conversion workflow checks continue to pass.


## Manual upload preparation — 2026-09-06

- All 47 tests passed with optional desktop checks enabled.
- Actual generated exports were prepared with both presets. AI Studio's two-pass
  test output was under its chosen 1 MB target; both outputs were checked for
  H.264/AAC, dimensions, duration, 48 kHz audio, and fast-start MP4 ordering.
- Tests cover the default 380 MB target, more-than-five-minute input planning,
  invalid/too-small targets, downscaling decisions, oversize failure preservation,
  source retention, creation of an output between passes, unsupported HDR/interlace/
  rotation, ambiguous audio, silent exports, preset-specific retry, and history scope.
- The real window test switched presets, edited the target, previewed, encoded,
  and checked manual-upload completion messaging. Earlier desktop features passed
  their regression checks. No account connection or upload was performed.
- YouTube guidance was checked at https://support.google.com/youtube/answer/1722171?hl=en.
  The 380 MB AI Studio target is a user preference, not a verified platform limit.
- Only temporary generated footage was processed. Manual playback and platform
  upload acceptance remain to be checked by the user. Changes have not been pushed.
