"""Creator-facing finished-video benchmark using one Gemini upload."""

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import tempfile
import time

import gemini_analysis


DEFAULT_PURPOSE = 'Publishing package'
PURPOSES = {
    'Publishing package': {
        'prompt': """Review this finished video and create a practical publishing package.

Provide:
- three accurate title options, with the strongest first;
- a ready-to-edit description based only on the video;
- concise chapters with timestamps;
- three thumbnail concepts and timestamped candidate frames;
- the strongest moments that could become Shorts, with timestamps and a brief hook.

Keep claims grounded in visible or spoken content. Mark uncertain details and
timestamps. Separate ready-to-use copy from suggestions. Format as readable Markdown.""",
        'focus': ('Ready-to-use titles and description', 'Useful chapters',
                  'Thumbnail concepts and candidate frames', 'Shorts opportunities'),
        'criteria': ('Title/description usefulness', 'Chapter usefulness',
                     'Thumbnail suggestions/candidate frames', 'Shorts suggestions'),
    },
    'Find Shorts': {
        'prompt': """Find the strongest short-form clips in this finished video.

Return a ranked list. For each suggestion give an exact start and end timestamp,
the hook, why the moment works, a suggested short title, and the minimum edit or
context needed. Include a timestamped thumbnail/candidate frame when useful.
Prioritize self-contained moments and do not invent spoken or visual details.
Mark uncertain boundaries or interpretations. Format as readable Markdown.""",
        'focus': ('Ranked timestamped clip candidates', 'Hooks and edit requirements',
                  'Short titles and candidate frames'),
        'criteria': ('Shorts suggestions', 'Thumbnail suggestions/candidate frames',
                     'Content recognition'),
    },
    'Review yoga sequence': {
        'prompt': """Review this finished yoga video as a sequence-analysis aid.

Create a chronological, timestamped list of poses, transitions, repeated sides,
and any clear instruction or theme. Note apparent omissions, uneven holds, abrupt
transitions, or places where the sequence may be unclear to a viewer. Distinguish
what is visible from what is inferred, use plain pose names, and mark uncertain
identifications. Do not make medical or safety claims. Format as readable Markdown.""",
        'focus': ('Chronological pose and transition map', 'Both-side and hold coverage',
                  'Visible sequence clarity and uncertainty'),
        'criteria': ('Pose/content recognition', 'Sequence completeness'),
    },
    'Check final video': {
        'prompt': """Quality-check this finished video before publishing.

Give a concise, timestamped review of pacing, structure, clarity, continuity,
audio/visual distractions, on-screen text, and the opening and ending. Separate
confirmed issues from optional improvements. Finish with a short prioritized fix
list and say clearly when no material issue is visible. Do not invent defects or
spoken details. Mark uncertainty. Format as readable Markdown.""",
        'focus': ('Confirmed final-video issues', 'Optional improvements',
                  'Prioritized fixes before publishing'),
        'criteria': ('Final-check completeness', 'Content recognition',
                     'Prioritized fix usefulness'),
    },
    'Generate chapters': {
        'prompt': """Create useful chapters for this finished video.

Return a clean timestamped chapter list with short, specific titles and one-line
descriptions. Cover the full video without excessive fragmentation, place chapter
changes at meaningful topic or scene boundaries, and include a brief note about
any uncertain boundary. Do not invent events or topics. Format as readable Markdown.""",
        'focus': ('Complete chapter coverage', 'Meaningful boundaries',
                  'Short specific titles and descriptions'),
        'criteria': ('Chapter usefulness', 'Chapter title usefulness',
                     'Content recognition'),
    },
}
CREATOR_PROMPT = PURPOSES[DEFAULT_PURPOSE]['prompt']
COMMON_CRITERIA = (
    'Timestamp accuracy', 'Completeness', 'Hallucinations/errors',
    'Creator correction', 'Repeatability',
)


def purpose_names():
    return tuple(PURPOSES)


def purpose_prompt(purpose=DEFAULT_PURPOSE):
    try:
        return PURPOSES[str(purpose)]['prompt']
    except KeyError as exc:
        raise gemini_analysis.GeminiError('Choose a supported creator analysis purpose.') from exc


class PromptDrafts:
    """Preserve one editable creator prompt per purpose."""

    def __init__(self, purpose=DEFAULT_PURPOSE):
        purpose_prompt(purpose)
        self.purpose = purpose
        self._drafts = {}

    def save(self, text):
        self._drafts[self.purpose] = str(text)

    def switch(self, current_text, purpose):
        purpose_prompt(purpose)
        self.save(current_text)
        self.purpose = purpose
        return self._drafts.get(purpose, purpose_prompt(purpose))

    def reset(self):
        text = purpose_prompt(self.purpose)
        self._drafts[self.purpose] = text
        return text


@dataclass(frozen=True)
class BenchmarkResult:
    report_path: Path | None
    baseline_path: Path | None
    creator_path: Path | None
    error: str
    upload_seconds: float | None
    processing_seconds: float | None
    baseline_seconds: float | None
    creator_seconds: float | None
    requests_completed: int


def preview_text(video, model, baseline_prompt, creator_prompt, purpose=DEFAULT_PURPOSE):
    """Describe the exact online work without creating files or clients."""
    video = Path(video)
    purpose_prompt(purpose)
    return (f'Creator benchmark · Purpose: {purpose} · Model: {model}\n'
            f'Upload: {video} once · Model requests: 2\n'
            f'Reports: beside {video}\n'
            'Network/API usage starts only after Run creator benchmark.\n'
            'This comparison tests the creator-review workflow and prompts; it does not '
            'test scene/frame extraction, contact sheets, local transcription, heavy AI '
            'dependencies, or raw-footage cataloguing.\n'
            f'Baseline prompt:\n{baseline_prompt}\n\n'
            f'Creator-review prompt:\n{creator_prompt}')


def _candidate_paths(video, now, number):
    stamp = now.strftime('%Y%m%d-%H%M%S')
    suffix = '' if number == 1 else f'-{number:02d}'
    base = f'VideoTool-Creator-Benchmark-{video.stem}-{stamp}{suffix}'
    return tuple(video.parent / f'{base}-{kind}.md'
                 for kind in ('baseline', 'creator-review', 'report'))


def _reserve_paths(video, now=None):
    """Exclusively reserve all names in a three-file result set."""
    now = now or datetime.now().astimezone()
    number = 1
    while True:
        paths = _candidate_paths(video, now, number)
        reserved = []
        try:
            for path in paths:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                reserved.append(path)
        except FileExistsError:
            for path in reserved:
                path.unlink(missing_ok=True)
            number += 1
            continue
        except OSError:
            for path in reserved:
                path.unlink(missing_ok=True)
            raise
        return paths


def _atomic_write(path, content):
    """Publish complete UTF-8 text without exposing a partially written file."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.videotool-benchmark-', delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_raw(path, title, video, model, prompt, response, purpose=None):
    purpose_line = f'- Analysis purpose: `{purpose}`\n' if purpose else ''
    _atomic_write(
        path,
        f'# {title}\n\n- Video: `{video}`\n- Model: `{model}`\n{purpose_line}\n'
        f'## Prompt\n\n{prompt}\n\n## Raw response\n\n{response}\n')


def _report_text(video, source_size, model, baseline_prompt, creator_prompt, purpose, paths,
                 uploaded, timings, requests_completed, error, started):
    baseline_path, creator_path, _report_path = paths
    elapsed = time.monotonic() - started
    status = 'Complete' if not error else 'Incomplete — successful responses were preserved'

    def measured(value):
        return f'{value:.2f} seconds' if value is not None else 'Not completed'

    details = PURPOSES[purpose]
    focus = '\n'.join(f'- {item}' for item in details['focus'])
    criteria = tuple(dict.fromkeys((*COMMON_CRITERIA, *details['criteria'])))
    rubric = '\n'.join(f'| {item} |  |  |  |' for item in criteria)
    return f"""# VideoTool creator-review benchmark

## Run status

- Status: {status}
- Finished video: `{video}`
- Analysis purpose: `{purpose}`
- Source size uploaded: {source_size} bytes
- Gemini model: `{model}`
- Uploads: {1 if uploaded else 0} completed; 1 planned
- Model requests: {requests_completed} completed; 2 planned
- Upload time: {measured(timings.get('upload'))}
- Gemini processing time: {measured(timings.get('processing'))}
- Baseline response time: {measured(timings.get('baseline'))}
- Creator-review response time: {measured(timings.get('creator'))}
- Total local workflow time: {elapsed:.2f} seconds
- Error: {error or 'None'}

This first comparison primarily tests the creator-review workflow and prompt. It
does not test scene/frame extraction, contact sheets, local transcription, heavy
AI dependencies, or raw-footage cataloguing.

## Preserved results

- Baseline response: {f'`{baseline_path.name}`' if baseline_path.exists() else 'Not saved'}
- Creator-review response: {f'`{creator_path.name}`' if creator_path.exists() else 'Not saved'}

## Purpose focus

{focus}

## Human rating rubric

Fill these in after reading both responses. Leave a score blank when it cannot be judged.

| Criterion | Baseline (1–5) | Creator review (1–5) | Notes |
|---|---:|---:|---|
{rubric}

## Operational observations

| Criterion | Measured result | Human notes |
|---|---|---|
| Processing time | {elapsed:.2f} seconds total |  |
| Upload size/time | {source_size} bytes / {measured(timings.get('upload'))} |  |
| API requests | 1 upload; {requests_completed} of 2 model requests completed |  |
| Hands-on time | Not measured automatically |  |
| Upload friction | Not measured automatically |  |
| Repeatability | Requires another approved run |  |

## Baseline prompt

{baseline_prompt}

## Creator-review prompt

{creator_prompt}
"""


def _append_error(current, error):
    message = gemini_analysis._safe_error(error)
    return f'{current}; {message}' if current else message


def run(video, baseline_prompt, creator_prompt, model=gemini_analysis.DEFAULT_MODEL,
        cancel=None, report=None, client=None, uploader=None, generator=None, now=None,
        purpose=DEFAULT_PURPOSE):
    """Run one upload and two analyses, preserving every completed response."""
    candidate = Path(video).expanduser()
    if candidate.is_symlink():
        raise gemini_analysis.GeminiError('Choose a regular finished video file.')
    video = candidate.resolve(strict=True)
    if not video.is_file() or video.suffix.lower() not in gemini_analysis.SUPPORTED_VIDEO_SUFFIXES:
        raise gemini_analysis.GeminiError('Choose one supported finished video file.')
    purpose_prompt(purpose)
    baseline_prompt, creator_prompt = str(baseline_prompt).strip(), str(creator_prompt).strip()
    if not baseline_prompt or not creator_prompt:
        raise gemini_analysis.GeminiError('Review both benchmark prompts before starting.')
    before = video.stat()
    source_size = before.st_size
    started = time.monotonic()
    timings = {'upload': None, 'processing': None, 'baseline': None, 'creator': None}
    try:
        paths = _reserve_paths(video, now)
    except OSError as exc:
        return BenchmarkResult(None, None, None, _append_error('', exc), None, None,
                               None, None, 0)
    report = report or (lambda stage, message: None)
    uploader = uploader or gemini_analysis.upload_video
    generator = generator or gemini_analysis.generate_for_file
    uploaded = None
    completed = 0
    baseline_saved = False
    creator_saved = False
    error = ''
    try:
        gemini_analysis._check_cancel(cancel)
        uploaded = uploader(video, cancel=cancel, report=report, client=client)
        timings.update(upload=uploaded.upload_seconds, processing=uploaded.processing_seconds)
        gemini_analysis._check_cancel(cancel)
        text, timings['baseline'] = generator(
            uploaded.remote, video.name, baseline_prompt, model, cancel, report, client,
            label='Baseline analysis')
        completed = 1
        _write_raw(paths[0], 'Baseline Gemini response', video, model, baseline_prompt, text)
        baseline_saved = True
        gemini_analysis._check_cancel(cancel)
        text, timings['creator'] = generator(
            uploaded.remote, video.name, creator_prompt, model, cancel, report, client,
            label='Creator review')
        completed = 2
        _write_raw(paths[1], 'Creator-review Gemini response', video, model, creator_prompt, text,
                   purpose)
        creator_saved = True
    except (gemini_analysis.GeminiError, OSError) as exc:
        error = _append_error(error, exc)
    if not baseline_saved:
        paths[0].unlink(missing_ok=True)
    if not creator_saved:
        paths[1].unlink(missing_ok=True)
    try:
        after = video.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != \
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            error = _append_error(error, 'Source changed during the benchmark.')
    except OSError as exc:
        error = _append_error(error, f'Source could not be verified after the benchmark: {exc}')
    try:
        content = _report_text(video, source_size, model, baseline_prompt, creator_prompt, purpose,
                               paths, uploaded, timings, completed, error, started)
        _atomic_write(paths[2], content)
        report_path = paths[2]
    except OSError as exc:
        error = _append_error(error, f'Benchmark report could not be saved: {exc}')
        paths[2].unlink(missing_ok=True)
        report_path = None
    return BenchmarkResult(report_path, paths[0] if paths[0].exists() else None,
                           paths[1] if paths[1].exists() else None, error,
                           timings['upload'], timings['processing'], timings['baseline'],
                           timings['creator'], completed)
