Test results — VideoTool

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
