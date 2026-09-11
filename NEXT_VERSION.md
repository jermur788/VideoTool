# Next version: Gemini analysis and Google Drive delivery

## Current progress — 2026-09-08

The first online stage is implemented. VideoTool can optionally upload one newly
prepared AI Studio MP4 to Gemini, wait for file processing, send an editable or
restored default prompt, and save the full response locally as Markdown. API-key
and SDK readiness are shown, and one-time setup stores the key in the operating
system password store. Cancellation prevents
later stages, and online failures do not change a successful local conversion.
The same interface can upload one supported original video directly, without
running a conversion, and saves its response beside the original.

The version 0.8.0 creator-review benchmark is implemented for one finished video.
Its purpose selector offers Publishing package, Find Shorts, Review yoga sequence,
Check final video, and Generate chapters. Each purpose supplies a focused editable
prompt and report rubric, while per-purpose drafts prevent edits from being lost
when switching. Preview discloses the purpose, one upload, two exact prompts, model,
source, report location, and network boundary. The upload is reused for the baseline
and creator-review requests; both raw responses and the purpose are preserved.
Subjective ratings remain blank and measured operational values are identified.
It remains a prompt/workflow test rather than scene/frame extraction, contact
sheets, transcription, heavy AI dependencies, or raw-footage cataloguing.
Cancellation prevents the next request, although an SDK request already in flight
cannot be aborted. Remote Gemini cleanup and live testing remain future work; the
current user project is Restricted and no live request has been made. The intended
manual sequence is V2 yoga first and A01 Kinsale Bay later; neither has been inspected
or processed during implementation.

Desktop packaging is also implemented at version 0.7.0: a user-level installer
adds an application-menu launcher and scalable icon, the About panel reports the
version and local readiness, updates replace only a recognized managed install,
and uninstall preserves preferences unless their removal is explicitly requested.

Google Drive OAuth, native Google Doc creation, Drive-only retry, online metadata
in receipts/history, and live-account testing remain for the next stage.

## Goal

After VideoTool prepares an AI Studio/Gemini-compatible MP4, it should optionally
upload that video to the Gemini API, send it with a prompt, and save the response
as a native Google Doc in a dedicated Google Drive folder.

The existing local-only preparation workflow must remain available. Network or
Google service failures must never invalidate or delete a successfully converted
video.

## Intended desktop workflow

1. The user selects **AI Studio upload** and previews the conversion as today.
2. An **Analyze with Gemini after conversion** option enables the online workflow.
3. A multiline prompt field starts with a documented default prompt. The user can
   edit or replace it before conversion and restore the default with one action.
4. VideoTool converts and validates the local MP4.
5. VideoTool uploads the MP4 with the Gemini Files API and waits until its state is
   ready for prompting.
6. VideoTool sends the uploaded video and the reviewed prompt to a configured
   Gemini model.
7. The complete response is saved locally first so a later Drive failure cannot
   lose it.
8. VideoTool creates a Google Doc in **My Drive / VideoTool Gemini Results** and
   offers an **Open Google Doc** action.

Online analysis should be explicitly enabled for a run. Preview must show which
files will be uploaded, the chosen model, the prompt, and the destination folder
before any network request starts.

## Authentication and configuration

- Read the Gemini credential from `GEMINI_API_KEY` or `GOOGLE_API_KEY`. Never write
  the key into source control, logs, receipts, history, error messages, or UI state.
- Use Google's installed-desktop OAuth flow for Google Drive. Store refreshable
  user credentials outside the repository with owner-only permissions.
- Request the narrowest practical Drive permission. The first version should
  create and manage its own **VideoTool Gemini Results** folder instead of browsing
  the user's entire Drive.
- Keep OAuth client configuration and user tokens out of Git. Add their possible
  filenames to `.gitignore` before implementation.
- Keep the Gemini model and default prompt in normal application configuration so
  they can be changed without rewriting the upload workflow.

## Saved Google Doc

Use a collision-safe title such as `Dublin_001 - Gemini response - 2026-09-06`.
The document should contain:

- source and prepared-video filenames;
- conversion receipt name;
- date and local timezone;
- Gemini model;
- exact prompt sent;
- full Gemini response;
- upload, Gemini processing, generation, and Drive-save timings;
- a short validation statement explaining which stages completed.

Save the same content locally as Markdown before creating the Google Doc. Record
the local response path, Drive file ID and web link, Gemini file name, and stage
outcomes in the processing receipt and conversion history. Do not store secrets.

## Execution and recovery

Model the online work as distinct stages: `Uploading`, `Processing`, `Analyzing`,
and `Saving to Drive`. The existing cancel control should stop local waiting and
prevent later stages. If an API request is already in flight, finish handling its
response safely and do not start another stage after cancellation.

- Retry transient network errors and rate limits with bounded exponential backoff.
- Give authentication, quota, billing, rejected-file, and service errors separate
  plain-language messages.
- Preserve Gemini's response locally if Drive saving fails and provide a retry that
  uploads the saved response without rerunning Gemini.
- Reuse a still-valid Gemini Files API upload when safely identified; avoid
  accidental duplicate analysis requests.
- Treat remote file deletion as a separate, explicit cleanup policy. Gemini Files
  API uploads currently expire automatically, but the implementation must verify
  current API behavior rather than depend on a hard-coded retention period.
- Never mark a local conversion failed solely because Gemini or Drive failed.

## Implementation outline

1. Add isolated Gemini and Drive client modules with small interfaces that can be
   replaced by fakes in tests.
2. Add dependency installation and startup checks for Google's supported Python
   clients.
3. Add secure Gemini-key detection and Drive OAuth setup/status UI.
4. Add prompt editing, default restoration, online opt-in, and preview disclosure.
5. Add the staged background workflow with progress, cancellation, bounded retries,
   and local response persistence.
6. Add Google Doc creation in the dedicated Drive folder and an open-document action.
7. Extend receipts and history without breaking existing databases.
8. Add mocked API tests, cancellation and recovery tests, and an opt-in live test
   that uses a small generated video and a test Drive folder.

## Acceptance criteria

- Local-only conversion behaves exactly as it did before the integration.
- No upload or prompt occurs before the user enables online analysis and reviews
  the preview.
- The default prompt can be edited or restored before execution.
- A successful run produces both a local Markdown response and a native Google Doc
  in the dedicated folder.
- Drive failure after Gemini succeeds retains the local response and can be retried
  without another Gemini request.
- Cancellation prevents unstarted online stages and leaves an honest receipt.
- Credentials and tokens cannot appear in Git, receipts, history, copied summaries,
  or displayed command output.
- Tests cover success, authentication failure, rejected video, processing failure,
  rate limiting, cancellation at every stage, Drive failure, retry, duplicate
  prevention, and safe reopening from history.

## Decisions made for the Gemini stage

- The default prompt asks for a chronological Markdown summary with timestamps,
  important visual and spoken details, and uncertainties.
- The default model is `gemini-3.8-flash`, with an environment override.
- Gemini analysis accepts one video per run and produces one response.

## Setup decisions needed for Google Drive delivery

- Google Cloud project and installed-desktop OAuth client.

Official implementation references:

- Gemini video input: <https://ai.google.dev/gemini-api/docs/video-understanding>
- Gemini Files API: <https://ai.google.dev/gemini-api/docs/files>
- Gemini API keys: <https://ai.google.dev/gemini-api/docs/api-key>
- Google Drive desktop OAuth: <https://developers.google.com/workspace/drive/api/quickstart/python>
- Creating files in a Drive folder: <https://developers.google.com/workspace/drive/api/guides/folder>
- Creating Google Docs: <https://developers.google.com/workspace/docs/api/how-tos/documents>
