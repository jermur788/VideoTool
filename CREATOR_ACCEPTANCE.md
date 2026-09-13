# VideoTool creator acceptance review

Date: 2026-09-13  
Review scope: validation only  
Creator-analysis implementation under review: 0.11.1  
Installed application observed during review: 0.12.1

## Decision

**Conditionally accept VideoTool for creator use.**

The creator-review publishing package provides materially more practical creator
value than the previous generic Gemini summary. The combined native-video and
selected-frame workflow improves evidence preservation, grounding, and recovery,
but its three Gemini requests make it too quota-sensitive to replace the simpler
workflow as the default.

Use the creator-review workflow for publishing support. Keep combined analysis as
an optional quality-control path for footage where visual precision justifies the
extra time and quota.

## Evidence reviewed

- Completed creator-review benchmark for `Ambient Short.mov`.
- Baseline and creator-review responses from the same upload and model.
- Completed native-video, selected-frame, and combined results for
  `Ambient Short_youtube.mp4`.
- Timestamped contact sheet containing 18 frames at roughly two-second intervals.
- A second Kinsale run and its recovery record.
- Incomplete and resumed `Short1_Instagram.mp4` runs.
- Recorded upload, processing, request, retry, and recovery timings.
- Live Google Drive delivery of an existing completed response on 2026-09-13.

## Previous Gemini summary versus creator-review package

| Criterion | Baseline | Creator review | Finding |
|---|---:|---:|---|
| Timestamp accuracy | 4/5 | 4/5 | Both align closely with the sampled footage. |
| Completeness for stated purpose | 4/5 | 5/5 | The creator response supplies every requested publishing component. |
| Grounding / hallucination control | 3/5 | 4/5 | The baseline speculates about Cornwall, Devon, or Ireland. The creator response mostly stays with visible content. |
| Creator correction burden | 2/5 | 4/5 | The baseline still requires creating all publishing copy. The creator package is ready to edit. |
| Repeatability | 3/5 | 3/5 | Inputs are recorded exactly, but only one matched creator-review benchmark is available. |
| Title and description usefulness | N/A | 4/5 | Three usable titles and a concise description were produced. |
| Chapter usefulness | N/A | 4/5 | Chapters follow the visible scene changes and are close to the sampled timing. |
| Thumbnail suggestions | N/A | 4/5 | Suggested frames at 00:01, 00:07, and 00:28 correspond to strong visible compositions. |
| Shorts suggestions | N/A | 4/5 | Suggested segments are concrete and usable for a short vertical source. |

The creator-review response passes the practical-value test. It converts the same
video upload into usable titles, description, chapters, thumbnail candidates, and
clip ideas. Its remaining burden is normal editorial review rather than rebuilding
the output from a generic summary.

## Native video, selected frames, and combined response

| Criterion | Native video | Selected frames | Combined | Finding |
|---|---:|---:|---:|---|
| Timestamp accuracy | 4/5 | 4/5 | 4/5 | All three track the main cuts closely; sampled timings are correctly marked approximate in the later combined result. |
| Scene and event completeness | 4/5 | 4/5 | 4/5 | The main beach, overlook, rocky walk, text overlays, and final viewpoint are retained. |
| On-screen text recognition | 5/5 | 5/5 | 5/5 | All three visible text overlays are captured. |
| Audio awareness | 4/5 | N/A | 4/5 | The frame result correctly says it cannot verify audio; native evidence supplies the audio finding. |
| Grounding / hallucination control | 3/5 | 4/5 | 5/5 | Native analysis suggests possible regions. The later combined result removes candidate locations and states that the location is unknown. |
| Creator correction burden | 3/5 | 4/5 | 4/5 | Combining resolves the main evidence conflict without demanding extensive correction. |
| Repeatability | 3/5 | 3/5 | 3/5 | Outputs are semantically consistent, but model wording and completion reliability vary. |

The combined path improves grounding and leaves a useful audit trail: exact prompt,
model, timestamps, contact sheet, preliminary responses, final response, timings,
and recovery reports are preserved. It also successfully reused completed stages
after failure instead of uploading the same video again.

## Operational acceptance

| Area | Assessment |
|---|---|
| Integration | Pass. Selection, prompt, upload, local saving, resume, and optional Drive delivery are part of one desktop flow. A live existing-response delivery created a native Google Doc and local receipts in 4.1 seconds without calling Gemini. |
| Active creator time | Conditional pass. A successful Kinsale combined run took 56.78 seconds with little active work, but failures required reopening and retrying. |
| Repeatability and auditability | Pass. Exact inputs and intermediate evidence are saved, and matching incomplete runs can resume. |
| Privacy | No material improvement over the old Gemini upload. Combined analysis sends the native video and selected frames to Gemini, although extraction and artifact storage are local. |
| Reliability | Conditional. The free-tier project limit of 20 requests per day stopped the Short1 final combination after earlier temporary-busy retries. |
| Correction burden | Pass on completed Kinsale output. The later combination policy removed unsupported location suggestions and retained uncertainty language. |

## Acceptance limits

- Creator-review publishing packages are accepted for routine use with normal human
  review before publishing.
- Combined analysis is accepted as an optional verification workflow.
- Combined analysis is not accepted as the default replacement for a single Gemini
  analysis while each run requires three model requests and free-tier quota is the
  active constraint.
- Google Drive delivery is accepted. A completed local response was saved as one
  native Google Doc in `VideoTool Gemini Results`, and both readable and
  machine-readable receipts were written locally.
- One additional creator-reviewed publishing-package run on different footage is
  needed to raise repeatability from provisional to demonstrated.

## Next acceptance action

After the Gemini quota resets, resume `Short1_Instagram.mp4`. VideoTool should reuse
the saved native-video and selected-frame responses and make only the final combining
request. With **Save final response to Google Drive** enabled, verify that the local
combined Markdown is preserved and its live-run Google Doc is created without a
duplicate video upload.
