"""Optional Gemini video analysis for VideoTool.

The Google SDK is imported only when online analysis is requested so every local
VideoTool workflow continues to work without an API key or extra package.
"""

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import stat
import tempfile
import time


DEFAULT_MODEL = os.environ.get('VIDEOTOOL_GEMINI_MODEL', 'gemini-3.8-flash')
KEYRING_SERVICE = 'VideoTool Gemini'
KEYRING_ACCOUNT = 'api-key'
CREDENTIAL_FILENAME = 'gemini-api-key'
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


@dataclass(frozen=True)
class UploadedVideo:
    remote: object
    remote_name: str
    remote_uri: str
    upload_seconds: float
    processing_seconds: float


def credential_path(environ=None, home=None):
    """Return the per-user credential path outside the managed application."""
    environ = os.environ if environ is None else environ
    base = environ.get('XDG_CONFIG_HOME')
    if base:
        return Path(base).expanduser() / 'videotool' / CREDENTIAL_FILENAME
    home = Path.home() if home is None else Path(home)
    return home / '.config' / 'videotool' / CREDENTIAL_FILENAME


def _file_api_key(path=None):
    target = Path(path or credential_path())
    try:
        info = target.lstat()
        if target.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            return None
        value = target.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return value or None


def _save_file_api_key(value, path=None):
    target = Path(path or credential_path())
    temporary_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.parent.is_symlink() or (target.exists() and target.is_symlink()):
            raise OSError('the private credential location is a symbolic link')
        target.parent.chmod(0o700)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent,
                                         prefix='.gemini-key-', delete=False) as temporary:
            temporary.write(value)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, target)
        target.chmod(0o600)
    except OSError as exc:
        if temporary_path:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise GeminiError(f'VideoTool could not save its private Gemini credential: {exc}') from exc


def _stored_api_key():
    saved = _file_api_key()
    if saved:
        return saved
    try:
        import keyring
        saved = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        return None
    if saved:
        try:
            _save_file_api_key(saved)
        except GeminiError:
            pass
    return saved


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
    _save_file_api_key(value)
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, value)
    except Exception:
        pass


def delete_api_key():
    target = credential_path()
    removed = False
    try:
        import keyring
        if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) is not None:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
            removed = True
    except Exception:
        pass
    try:
        if target.exists() or target.is_symlink():
            target.unlink()
            removed = True
    except OSError as exc:
        raise GeminiError(f'VideoTool could not remove its private Gemini credential: {exc}') from exc
    return removed


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
        raise GeminiCancelled('Gemini analysis cancelled. The local video was kept unchanged.')


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
    if ('503' in upper or 'UNAVAILABLE' in upper or 'HIGH DEMAND' in upper or
            'TEMPORARILY UNAVAILABLE' in upper):
        return ('Gemini remained temporarily busy after automatic retries. Your completed '
                'local results were preserved; try the analysis again later.')
    return message


def _is_transient_api_error(exc):
    message = _safe_error(exc).upper()
    return ('503' in message or 'UNAVAILABLE' in message or
            'HIGH DEMAND' in message or 'TEMPORARILY UNAVAILABLE' in message)


def generate_content_with_retry(client, model, contents, cancel=None, report=None,
                                label='Analyzing', retry_delays=(2, 5, 10)):
    """Generate once, retrying only temporary service-unavailable responses."""
    report = report or (lambda stage, message: None)
    delays = tuple(retry_delays)
    for attempt in range(len(delays) + 1):
        _check_cancel(cancel)
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as exc:
            if attempt >= len(delays) or not _is_transient_api_error(exc):
                raise
            delay = delays[attempt]
            report(label, f'Gemini is temporarily busy. Retrying in {delay} seconds '
                          f'({attempt + 1} of {len(delays)})…')
            _wait(cancel, delay)
    raise AssertionError('Unreachable retry state')


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
    uploaded = upload_video(video, cancel, report, client, poll_interval, processing_timeout)
    response_text, analysis_seconds = generate_for_file(
        uploaded.remote, video.name, prompt, model, cancel, report, client)
    return AnalysisResult(
        text=response_text,
        model=model,
        remote_name=uploaded.remote_name,
        remote_uri=uploaded.remote_uri,
        upload_seconds=uploaded.upload_seconds,
        processing_seconds=uploaded.processing_seconds,
        analysis_seconds=analysis_seconds,
    )


def upload_video(video, cancel=None, report=None, client=None, poll_interval=5,
                 processing_timeout=1800):
    video = Path(video).resolve(strict=True)
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
            raise GeminiError(f'Gemini could not check the processing status for {video.name}: '
                              f'{_friendly_api_error(exc)}') from exc
    return UploadedVideo(remote, str(getattr(remote, 'name', '') or ''),
                         str(getattr(remote, 'uri', '') or ''), upload_seconds,
                         time.monotonic() - processing_started)


def generate_for_file(remote, video_name, prompt, model=DEFAULT_MODEL, cancel=None,
                      report=None, client=None, label='Analyzing'):
    client = client or _client()
    report = report or (lambda stage, message: None)
    _check_cancel(cancel)
    report(label, f'Gemini is analyzing {video_name} with the reviewed prompt…')
    started = time.monotonic()
    try:
        response = generate_content_with_retry(
            client, model, [remote, prompt], cancel, report, label)
        text = str(getattr(response, 'text', '') or '').strip()
    except Exception as exc:
        raise GeminiError(f'Gemini could not analyze {video_name}: {_friendly_api_error(exc)}') from exc
    if not text:
        raise GeminiError(f'Gemini returned an empty response for {video_name}.')
    return text, time.monotonic() - started


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
