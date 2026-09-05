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
