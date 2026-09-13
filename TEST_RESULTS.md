Test results — VideoTool

## Prominent resume preview — 2026-09-13

- Version 0.11.1 shows **Resume available** in the main preview banner and marks the
  file ready to resume from saved results.
- A graphical desktop test creates a safe mocked partial run, previews the matching
  generated video, and verifies that the banner, status, and enabled combined action
  are visible in the compact window.
- The complete suite, including all seven desktop checks, passed: 132 tests in
  15.026 seconds.
- No live Gemini request or user video is used by the check.

## Resumable combined analysis — 2026-09-13

- Version 0.11.0 detects a compatible incomplete combined-analysis report during
  Preview when the video, model, prompt, and source size still match.
- Recovery tests verify that a failed selected-frame stage reuses the native result
  without uploading or repeating native analysis, while a failed final stage also
  reuses the completed frame response. Temporary frames are rebuilt locally.
- Successful recovery saves the missing artifacts and a separate recovery report;
  the original incomplete report and source video remain unchanged.
- The interface distinguishes **View combined response** from **View recovery
  report** when recovery is still incomplete.
- The complete suite, including all six desktop checks, passed: 131 tests in
  14.375 seconds.
- No live Gemini request or user video is used by these checks.

## Combined-response evidence policy — 2026-09-13

- Version 0.10.2 tells the final reconciliation stage to omit unsupported locations,
  identities, causes, and intentions; preserve only supported details; and state
  unresolved conflicts as uncertainties.
- Sampled-frame timestamps are explicitly treated as approximate visibility windows.
  Tests verify that the policy reaches Gemini and is preserved in both final and
  comparison artifacts.
- The complete suite, including all six desktop checks, passed: 129 tests in
  14.134 seconds.
- No live Gemini request or user video is used by these checks.

## Temporary Gemini high-demand recovery — 2026-09-12

- Version 0.10.1 recognizes Gemini 503, unavailable, and high-demand generation
  failures and retries the affected request after bounded delays.
- Tests verify recovery after two temporary failures, no retry for permanent
  invalid-request errors, and a short plain-language message after retries are
  exhausted. The API key and raw Google error payload remain hidden.
- The complete suite, including all six desktop checks, passed: 129 tests in
  14.638 seconds.
- No live Gemini request is made by these checks.

## Combined native-video and selected-frame analysis — 2026-09-12

- Version 0.10.0 adds a third, separately cancellable Gemini request that combines
  the preserved native-video and selected-frame findings into the final response.
- The synthesis instruction treats preliminary output as evidence, follows the
  user's original prompt, prefers native-video evidence for audio and motion, and
  uses frames for visible text, visual detail, and explicit timestamps.
- The final response has its own collision-safe Markdown file. If synthesis fails,
  VideoTool retains both preliminary responses, the contact sheet, and the report.
- The complete suite, including all six desktop checks, passed: 126 tests in
  14.272 seconds.
- No live Gemini request or user video is used by the automated checks.

## Native-video versus selected-frame benchmark — 2026-09-12

- Version 0.9.0 adds an explicit desktop mode that compares one native-video
  Gemini response with one response from locally selected timestamped frames,
  using the same model and exact reviewed prompt.
- Tests cover preview without side effects, source preservation, collision-safe
  artifacts, partial-result preservation, timestamp and image request contents,
  cancellation, truthful interface wording, and real FFmpeg extraction plus
  contact-sheet creation from generated temporary media.
- Desktop checks verify that the new option is mutually exclusive with the other
  Gemini modes and that its action remains reachable in the compact window.
- The complete suite, including all six graphical desktop checks, passed: 123
  tests in 14.163 seconds.
- No live Gemini request or user video is used by the automated checks.

## Benchmark baseline definition — 2026-09-12

- The interface now calls the first result **Standard summary (baseline)** and
  explains that it is VideoTool's normal chronological summary from the same video
  and model, used as the reference for the creator-focused response.
- The definition is included before a run in Preview and afterward in the saved
  comparison report. Raw response titles, timings, prompt headings, and rating
  columns use the same wording.

## Persistent Gemini key and truthful start status — 2026-09-12

- Saving a Gemini key now writes it atomically to an owner-only per-user
  credential file outside the managed application and mirrors it to the system
  password store when that service is available. Updating or uninstalling the
  application preserves the credential; **Remove saved key** removes both copies.
- Tests cover file permissions, round-trip persistence when the password-store
  backend is unavailable, deletion, key redaction, and existing environment-key
  compatibility. No real credential is read, displayed, or sent.
- If readiness fails, the interface now says **Gemini did not start**, resets both
  progress labels, and states that no upload began instead of leaving an older
  analysis message visible.

## Compact desktop results — 2026-09-12

- Format, source, and Gemini settings now share one responsive row. The prompt
  editors use tabs, the review table is shorter, and the details box remains
  independently scrollable.
- The review, progress, primary action, and result buttons stay in the main window
  without requiring a whole-page scrollbar.
- The non-live suite passes, and opt-in desktop checks verify the compact
  1200 x 900 layout and confirm that Gemini and completion actions stay within the window.
  No Gemini request or user media is used by these checks.

## Creator-review finished-video benchmark — 2026-09-11

- The full 112-test non-live suite completed successfully. Five optional
  display-dependent checks were skipped; all 13 dedicated creator-benchmark tests
  passed.
- Mocked benchmark tests verify that preview creates no report or Gemini client,
  one upload object is reused for two sequential requests, cancellation is checked
  before each stage, and cancellation after the baseline prevents request two.
- Partial second-request failure preserves the baseline and an incomplete report.
  Tests also cover configured-key redaction, atomic collision-safe three-file
  result sets, unchanged source bytes, structured report-write failure, and
  truthful benchmark action, cancellation, summary, and Stop-control behavior.
- All five creator purposes have distinct focused prompts, output sections, and
  relevant blank rubric criteria. Tests verify purpose persistence in preview, raw
  creator output, and reports, plus per-purpose draft preservation and reset behavior.
- The isolated desktop installer test confirms `creator_benchmark.py` is copied,
  imports with the installed GUI, and the generated launcher reports healthy
  version 0.8.0 state. It used temporary XDG and executable directories only.
- No live Gemini request was made. No user media, real user installation,
  credential, running app, commit, or remote state was accessed or changed.

## User-level desktop installation — 2026-09-11

- The full 99-test suite completed successfully. Five optional display-dependent
  checks were skipped because the run did not open the live desktop interface.
- Installer tests used isolated temporary XDG data, configuration, and executable
  directories, including paths containing spaces. They cover first installation,
  managed update, unmanaged-collision refusal, uninstall, preference preservation,
  explicit preference removal, and recovery from optional Gemini setup failure.
- A separate isolated end-to-end check installed the app, validated the generated
  desktop entry, launched `videotool --health`, updated the managed copy, and
  uninstalled it. The SVG icon parsed successfully. Nothing was installed under
  the user's actual home directories.
- The About/readiness checks cover the installed/app version, Python, Tkinter,
  FFmpeg, ffprobe, and optional Gemini client/key state without revealing a key.
- No user media, saved credentials, conversion output, history, receipt, cache,
  or running process was accessed or changed.

## Starting folder and preview size estimates — 2026-09-11

- The full 93-test regression suite completed successfully. Five optional
  display-dependent checks were skipped because this run did not open the desktop
  interface; the three temporary-media integration checks passed.
- The source picker can now start in a user-selected folder or mounted drive on
  future launches. Settings tests cover the normal per-user location, save/load,
  reset, malformed data, unavailable folders, and preservation of future settings.
- Preview now has an **Approx. output** column for DaVinci, AI Studio, and YouTube
  preparation. AI Studio uses the planned bitrate, duration, and audio while
  respecting the chosen maximum. YouTube uses a storage-planning heuristic based
  on output resolution, frame rate, duration, and audio, and warns that CRF output
  can be much smaller or larger.
- Upload-preset folder previews now include their approximate batch total and
  destination free space. Direct Gemini uploads say **No copy** because no local
  output is created.
- No user footage, existing output, or running process was accessed or changed.

## Gemini upload, prompt, and secure key setup — 2026-09-09

- All 88 automated tests passed with the five real desktop-display checks enabled.
  The window checks cover the secure-key menu, revealing the prompt, replacing its
  text, and restoring the default.
- Mocked API tests cover upload, processing-state polling, prompt submission,
  response persistence, empty responses, processing failure, cancellation,
  credential redaction, and preserving a successful local conversion when the
  online stage fails.
- The UI now saves, replaces, or removes the API key through the operating system
  password store. Tests verify round-trip storage, deletion, and that a securely
  saved key takes precedence over a temporary terminal key.
- The installed keyring client selected Cinnamon's Secret Service backend during
  a read-only host check. No credential was read or written during that check.
- Gemini analysis is restricted to one ready video per run. Multi-video folder
  conversion remains available with online analysis turned off.
- Direct Gemini upload previews and uploads one supported original video without
  running FFmpeg. Tests verify unchanged source selection, unsupported-format and
  folder refusal, MOV upload, and the desktop toggle hiding conversion-only options.
- The selected row's complete error and technical context can be copied with
  **Copy error/details**. The desktop failure workflow verifies the clipboard text.
- An invalid Google API key now produces a short replacement instruction instead
  of the raw Google error payload. Key entry also rejects quotation marks,
  whitespace, and backslashes that may be copied accidentally from a shell example.
- A restricted-project 401 now directs the user to resolve the project access or
  billing notice in Google AI Studio, or use a key from an active project.
- A failed direct upload now says that the original video was kept unchanged and
  no longer claims that a conversion succeeded. Repeated error text was removed
  from the copyable details.
- Google's `google-genai` client 2.22.0 was installed successfully in the ignored
  `.venv` environment and its `Client` entry point was verified. No Gemini API key
  was present, so no live upload or billable model request was made.
- Directly launching `python3 videotool_gui.py` now restarts with the isolated
  VideoTool environment automatically, preventing a false “support is not
  installed” message when the package exists there.
- Only temporary dummy or generated media was used. User footage and the user's
  running application were not accessed or interrupted.

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
