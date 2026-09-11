"""Optional Gemini video analysis for VideoTool.

The Google SDK is imported only when online analysis is requested so every local
VideoTool workflow continues to work without an API key or extra package.
"""

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import time


DEFAULT_MODEL = os.environ.get('VIDEOTOOL_GEMINI_MODEL', 'gemini-3.8-flash')
KEYRING_SERVICE = 'VideoTool Gemini'
KEYRING_ACCOUNT = 'api-key'
SUPPORTED_VIDEO_SUFFIXES = frozenset({
    '.mp4', '.mpeg', '.mpg', '.mov', '.avi', '.flv', '.webm', '.wmv', '.3gp', '.3gpp',
})
DEFAULT_PROMPT = """Analyze this video and write a clear chronological summary.

Include:
- the main events and topics;
- useful timestamps;
- important visual and spoken details;
- anything uncertain or difficult to verify.

Format the response as readable Markdown."""


class GeminiError(Exception):
    """A plain-language online analysis error."""


class GeminiCancelled(GeminiError):
    """The user cancelled before the next online stage."""


@dataclass(frozen=True)
class AnalysisResult:
    text: str
    model: str
    remote_name: str
    remote_uri: str
    upload_seconds: float
    processing_seconds: float
    analysis_seconds: float


def _stored_api_key():
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        return None


def api_key(environ=None):
    """Read the secure saved key first, with terminal variables as a fallback."""
    if environ is None:
        saved = _stored_api_key()
        if saved:
            return saved
        environ = os.environ
    return environ.get('GOOGLE_API_KEY') or environ.get('GEMINI_API_KEY')


def api_key_present(environ=None):
    """Report key availability without returning or retaining the credential."""
    return bool(api_key(environ))


def save_api_key(value):
    value = str(value).strip()
    if not value:
        raise GeminiError('Paste a Gemini API key before saving.')
    if value[:1] in ('"', "'") or value[-1:] in ('"', "'"):
        raise GeminiError('Paste the Gemini API key without quotation marks.')
    if '\\' in value or any(character.isspace() for character in value):
        raise GeminiError('Paste the Gemini API key exactly as shown in Google AI Studio, without spaces or backslashes.')
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)
        if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) != value:
            raise GeminiError('The password store did not return the saved Gemini key.')
    except GeminiError:
        raise
    except Exception as exc:
        raise GeminiError(f'The system password store could not save the Gemini key: {exc}') from exc


def delete_api_key():
    try:
        import keyring
        if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) is None:
            return False
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return True
    except Exception as exc:
        raise GeminiError(f'The system password store could not remove the Gemini key: {exc}') from exc


def sdk_present():
    try:
        from google import genai  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        return False
    return True


def readiness(environ=None):
    if not api_key_present(environ):
        return False, 'Gemini API key not set up yet.'
    if not sdk_present():
        return False, 'Gemini support is not installed. Install the optional Gemini package, then restart VideoTool.'
    return True, 'Gemini is ready.'


def _client():
    key = api_key()
    if not key:
        raise GeminiError('Gemini API key not set up yet.')
    try:
        from google import genai
    except (ImportError, ModuleNotFoundError) as exc:
        raise GeminiError(
            'Gemini support is not installed. Run: python3 -m pip install -r requirements-gemini.txt'
        ) from exc
    return genai.Client(api_key=key)


def _state_name(remote):
    state = getattr(remote, 'state', '')
    name = getattr(state, 'name', state)
    return str(name).rsplit('.', 1)[-1].upper()


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise GeminiCancelled('Gemini analysis cancelled. The converted video was kept.')


def _safe_error(exc):
    message = str(exc)
    saved = _stored_api_key()
    if saved:
        message = message.replace(saved, '[hidden]')
    for name in ('GEMINI_API_KEY', 'GOOGLE_API_KEY'):
        secret = os.environ.get(name)
        if secret:
            message = message.replace(secret, '[hidden]')
    return message


def _friendly_api_error(exc):
    """Turn common Google API failures into useful text while hiding credentials."""
    message = _safe_error(exc)
    upper = message.upper()
    if 'API_KEY_INVALID' in upper or 'API KEY NOT VALID' in upper:
        return ('The saved Gemini API key was rejected. Open Gemini key… → '
                'Save or replace key, then paste a new key exactly as shown in Google AI Studio.')
    if 'ACCESS_TOKEN_TYPE_UNSUPPORTED' in upper or '401 UNAUTHENTICATED' in upper:
        return ('Google did not accept this key for Gemini file uploads. In Google AI Studio, '
                'resolve any “API access is restricted” notice for the key’s project, including '
                'billing if Google requests it, or create a key in an active project. Then save '
                'that key through Gemini key… → Save or replace key.')
    return message


def _wait(cancel, seconds):
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        _check_cancel(cancel)


def analyze(video, prompt, model=DEFAULT_MODEL, cancel=None, report=None, client=None,
            poll_interval=5, processing_timeout=1800):
    """Upload one MP4, wait for processing, and submit the reviewed prompt."""
    candidate = Path(video).expanduser()
    if candidate.is_symlink():
        raise GeminiError('The prepared video is not an available regular file.')
    video = candidate.resolve(strict=True)
    if not video.is_file():
        raise GeminiError('The prepared video is not an available regular file.')
    if video.suffix.lower() not in SUPPORTED_VIDEO_SUFFIXES:
        raise GeminiError('Gemini does not support this video file format.')
    prompt = str(prompt).strip()
    if not prompt:
        raise GeminiError('Enter a prompt before starting Gemini analysis.')
    model = str(model).strip()
    if not model:
        raise GeminiError('A Gemini model is required.')
    client = client or _client()
    report = report or (lambda stage, message: None)

    _check_cancel(cancel)
    report('Uploading', f'Uploading {video.name} to Gemini…')
    started = time.monotonic()
    try:
        remote = client.files.upload(file=str(video))
    except Exception as exc:
        raise GeminiError(f'Gemini could not upload {video.name}: {_friendly_api_error(exc)}') from exc
    upload_seconds = time.monotonic() - started

    _check_cancel(cancel)
    report('Processing', f'Gemini is processing {video.name}…')
    processing_started = time.monotonic()
    while _state_name(remote) not in ('ACTIVE', 'READY'):
        state = _state_name(remote)
        if state in ('FAILED', 'ERROR', 'CANCELLED'):
            detail = getattr(remote, 'error', None)
            raise GeminiError(f'Gemini rejected or could not process {video.name}' +
                              (f': {detail}' if detail else '.'))
        if time.monotonic() - processing_started >= processing_timeout:
            raise GeminiError(f'Gemini did not finish processing {video.name} within the time limit.')
        _wait(cancel, poll_interval)
        _check_cancel(cancel)
        try:
            remote = client.files.get(name=remote.name)
        except Exception as exc:
            raise GeminiError(
                f'Gemini could not check the processing status for {video.name}: '
                f'{_friendly_api_error(exc)}') from exc
    processing_seconds = time.monotonic() - processing_started

    _check_cancel(cancel)
    report('Analyzing', f'Gemini is analyzing {video.name} with the reviewed prompt…')
    analysis_started = time.monotonic()
    try:
        response = client.models.generate_content(model=model, contents=[remote, prompt])
        response_text = str(getattr(response, 'text', '') or '').strip()
    except Exception as exc:
        raise GeminiError(f'Gemini could not analyze {video.name}: {_friendly_api_error(exc)}') from exc
    if not response_text:
        raise GeminiError(f'Gemini returned an empty response for {video.name}.')
    return AnalysisResult(
        text=response_text,
        model=model,
        remote_name=str(getattr(remote, 'name', '') or ''),
        remote_uri=str(getattr(remote, 'uri', '') or ''),
        upload_seconds=upload_seconds,
        processing_seconds=processing_seconds,
        analysis_seconds=time.monotonic() - analysis_started,
    )


def write_response(source, video, prompt, result, receipt_path=None, now=None):
    """Save a Gemini response locally before any later cloud-delivery stage."""
    source = Path(source)
    candidate_video = Path(video).expanduser()
    if candidate_video.is_symlink():
        raise OSError('The prepared video is not an available regular file.')
    video = candidate_video.resolve(strict=True)
    if not video.is_file():
        raise OSError('The prepared video is not an available regular file.')
    now = now or datetime.now().astimezone()
    timestamp = now.strftime('%Y%m%d-%H%M%S')
    base = video.parent / f'VideoTool-Gemini-{video.stem}-{timestamp}.md'
    candidate = base
    number = 2
    while candidate.exists() or candidate.is_symlink():
        candidate = base.with_name(f'{base.stem}-{number:02d}{base.suffix}')
        number += 1
    receipt = Path(receipt_path).name if receipt_path else 'Not available'
    content = (
        '# VideoTool Gemini response\n\n'
        f'- Source video: `{source.name}`\n'
        f'- Prepared video: `{video.name}`\n'
        f'- Processing receipt: `{receipt}`\n'
        f'- Created: {now.isoformat(timespec="seconds")}\n'
        f'- Gemini model: `{result.model}`\n'
        f'- Gemini file: `{result.remote_name or "Not reported"}`\n'
        f'- Upload time: {result.upload_seconds:.1f} seconds\n'
        f'- Processing time: {result.processing_seconds:.1f} seconds\n'
        f'- Analysis time: {result.analysis_seconds:.1f} seconds\n\n'
        '## Prompt sent\n\n'
        f'{str(prompt).strip()}\n\n'
        '## Gemini response\n\n'
        f'{result.text.strip()}\n\n'
        '## Validation\n\n'
        'The prepared MP4 was uploaded, Gemini reported it ready, and a non-empty response was saved locally.\n'
    )
    with candidate.open('x', encoding='utf-8') as handle:
        handle.write(content)
    return candidate
