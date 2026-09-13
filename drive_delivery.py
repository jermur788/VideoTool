"""Optional Google Drive delivery for locally saved VideoTool results."""

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time


SCOPES = ('https://www.googleapis.com/auth/drive.file',)
CLIENT_FILENAME = 'google-drive-oauth-client.json'
TOKEN_FILENAME = 'google-drive-token.json'
RESULTS_FOLDER = 'VideoTool Gemini Results'
FOLDER_MIME = 'application/vnd.google-apps.folder'
DOC_MIME = 'application/vnd.google-apps.document'


class DriveError(Exception):
    """A plain-language Google Drive delivery error."""


class DriveCancelled(DriveError):
    """The user cancelled before the next Drive stage."""


@dataclass(frozen=True)
class DeliveryResult:
    document_id: str
    url: str
    title: str
    folder_id: str
    seconds: float
    response_sha256: str


def config_directory(environ=None, home=None):
    environ = os.environ if environ is None else environ
    base = environ.get('XDG_CONFIG_HOME')
    if base:
        return Path(base).expanduser() / 'videotool'
    return Path(home or Path.home()) / '.config' / 'videotool'


def client_path(environ=None, home=None):
    return config_directory(environ, home) / CLIENT_FILENAME


def token_path(environ=None, home=None):
    return config_directory(environ, home) / TOKEN_FILENAME


def metadata_path(response_path):
    response_path = Path(response_path)
    return response_path.with_name(response_path.name + '.drive.json')


def receipt_path(response_path):
    response_path = Path(response_path)
    return response_path.with_name(response_path.name + '.drive.md')


def _private_regular_file(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink() and not info.st_mode & 0o077


def _atomic_private_text(target, text):
    target = Path(target)
    temporary_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.parent.is_symlink() or (target.exists() and target.is_symlink()):
            raise OSError('the private credential location is a symbolic link')
        target.parent.chmod(0o700)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent,
                                         prefix='.drive-', delete=False) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
        target.chmod(0o600)
    except OSError as exc:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        raise DriveError(f'VideoTool could not save its private Google Drive setup: {exc}') from exc


def _atomic_sidecar(target, text):
    """Write non-secret delivery metadata without changing the video folder's permissions."""
    target = Path(target)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent,
                                         prefix='.videotool-drive-', delete=False) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
    except OSError as exc:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        raise DriveError(f'The Google Drive receipt could not be saved beside the local response: {exc}') from exc


def _load_json(path, private=True):
    path = Path(path)
    if private and not _private_regular_file(path):
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def save_client_config(source, target=None):
    """Validate and privately retain an installed-desktop OAuth client file."""
    source = Path(source).expanduser()
    if source.is_symlink():
        raise DriveError('Choose the original Google OAuth setup JSON file, not a symbolic link.')
    try:
        data = json.loads(source.resolve(strict=True).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as exc:
        raise DriveError('That file is not a readable Google OAuth setup JSON file.') from exc
    installed = data.get('installed') if isinstance(data, dict) else None
    required = ('client_id', 'client_secret', 'auth_uri', 'token_uri')
    if not isinstance(installed, dict) or any(not installed.get(key) for key in required):
        raise DriveError('Choose OAuth credentials created with application type Desktop app.')
    _atomic_private_text(target or client_path(), json.dumps(data, indent=2) + '\n')
    return Path(target or client_path())


def sdk_present(importer=__import__):
    try:
        importer('google.oauth2.credentials', fromlist=['Credentials'])
        importer('google_auth_oauthlib.flow', fromlist=['InstalledAppFlow'])
        importer('googleapiclient.discovery', fromlist=['build'])
    except (ImportError, ModuleNotFoundError):
        return False
    return True


def readiness(environ=None, home=None):
    if not sdk_present():
        return False, 'Google Drive support is not installed.'
    if not _private_regular_file(client_path(environ, home)):
        return False, 'Google Drive is not set up yet.'
    if not _private_regular_file(token_path(environ, home)):
        return False, 'Google Drive setup is saved; connect your account once.'
    return True, 'Google Drive is connected.'


def connect(cancel=None, report=None, client_file=None, token_file=None, flow_factory=None):
    """Run Google's installed-desktop OAuth flow and privately save refreshable credentials."""
    client_file = Path(client_file or client_path())
    token_file = Path(token_file or token_path())
    if not _private_regular_file(client_file):
        raise DriveError('Choose a Google Desktop app OAuth setup file first.')
    if not sdk_present():
        raise DriveError('Google Drive support is not installed. Update VideoTool with online support.')
    _check_cancel(cancel)
    report = report or (lambda stage, message: None)
    report('Connecting Drive', 'Your browser will open so you can allow VideoTool to save Google Docs.')
    try:
        if flow_factory is None:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow_factory = InstalledAppFlow.from_client_secrets_file
        flow = flow_factory(str(client_file), SCOPES)
        credentials = flow.run_local_server(port=0, open_browser=True,
                                            authorization_prompt_message='')
        _check_cancel(cancel)
        _atomic_private_text(token_file, credentials.to_json())
    except DriveCancelled:
        raise
    except Exception as exc:
        raise DriveError(f'Google Drive connection did not complete: {_friendly_error(exc)}') from exc
    return token_file


def disconnect(token_file=None):
    target = Path(token_file or token_path())
    try:
        if target.exists() or target.is_symlink():
            target.unlink()
            return True
    except OSError as exc:
        raise DriveError(f'VideoTool could not remove the saved Google Drive connection: {exc}') from exc
    return False


def _credentials(token_file=None):
    token_file = Path(token_file or token_path())
    if not _private_regular_file(token_file):
        raise DriveError('Google Drive is not connected. Open Google Drive… and connect your account.')
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            _atomic_private_text(token_file, credentials.to_json())
    except DriveError:
        raise
    except Exception as exc:
        raise DriveError(f'Google Drive could not refresh the saved connection: {_friendly_error(exc)}') from exc
    if not credentials.valid:
        raise DriveError('Google Drive needs to be connected again through Google Drive….')
    return credentials


def _services(credentials=None, builder=None):
    credentials = credentials or _credentials()
    if builder is None:
        try:
            from googleapiclient.discovery import build
        except (ImportError, ModuleNotFoundError) as exc:
            raise DriveError('Google Drive support is not installed. Update VideoTool with online support.') from exc
        builder = build
    return (builder('drive', 'v3', credentials=credentials, cache_discovery=False),
            builder('docs', 'v1', credentials=credentials, cache_discovery=False))


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise DriveCancelled('Google Drive delivery cancelled. The local Gemini response was kept.')


def _wait(cancel, seconds):
    if cancel is None:
        time.sleep(seconds)
    elif cancel.wait(seconds):
        _check_cancel(cancel)


def _status_code(exc):
    response = getattr(exc, 'resp', None)
    return getattr(response, 'status', None)


def _friendly_error(exc):
    status = _status_code(exc)
    message = str(exc)
    for path in (client_path(), token_path()):
        saved = _load_json(path)
        if not saved:
            continue
        installed = saved.get('installed', saved)
        if isinstance(installed, dict):
            for key in ('client_id', 'client_secret', 'token', 'refresh_token'):
                secret = installed.get(key)
                if isinstance(secret, str) and secret:
                    message = message.replace(secret, '[hidden]')
    upper = message.upper()
    if status == 401 or 'UNAUTHENTICATED' in upper or 'INVALID_GRANT' in upper:
        return 'Google Drive authorization expired or was withdrawn. Connect the account again.'
    if status == 403 and ('RATE' in upper or 'QUOTA' in upper):
        return 'Google Drive quota is temporarily unavailable. Retry Drive delivery later.'
    if status == 403:
        return 'Google Drive did not allow VideoTool to create the result. Check the connected account and enabled APIs.'
    if status == 429:
        return 'Google Drive is temporarily rate-limiting requests. Retry Drive delivery later.'
    if status and status >= 500:
        return 'Google Drive is temporarily unavailable. Retry Drive delivery later.'
    return message


def _execute(request_factory, cancel=None, report=None, retry_delays=(2, 5, 10)):
    delays = tuple(retry_delays)
    for attempt in range(len(delays) + 1):
        _check_cancel(cancel)
        try:
            return request_factory().execute()
        except Exception as exc:
            status = _status_code(exc)
            transient = status == 429 or (status is not None and status >= 500)
            if attempt >= len(delays) or not transient:
                raise DriveError(_friendly_error(exc)) from exc
            delay = delays[attempt]
            if report:
                report('Saving to Drive', f'Google Drive is temporarily busy. Retrying in {delay} seconds…')
            _wait(cancel, delay)
    raise AssertionError('Unreachable retry state')


def _response_file(path):
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise DriveError('The saved Gemini response is a symbolic link and cannot be uploaded.')
    try:
        path = candidate.resolve(strict=True)
        data = path.read_bytes()
    except OSError as exc:
        raise DriveError(f'The saved Gemini response is not available: {exc}') from exc
    if not path.is_file() or path.suffix.lower() != '.md':
        raise DriveError('Choose a saved VideoTool Markdown response.')
    return path, data, hashlib.sha256(data).hexdigest()


def previous_delivery(response_path):
    try:
        path, _data, digest = _response_file(response_path)
    except DriveError:
        return None
    saved = _load_json(metadata_path(path), private=False)
    if not saved or saved.get('response_sha256') != digest:
        return None
    required = ('document_id', 'url', 'title', 'folder_id', 'seconds', 'response_sha256')
    if any(key not in saved for key in required):
        return None
    try:
        return DeliveryResult(**{key: saved[key] for key in required})
    except (TypeError, ValueError):
        return None


def _safe_title(path, now=None):
    now = now or datetime.now().astimezone()
    stem = path.stem
    if stem.startswith('VideoTool-'):
        stem = stem[len('VideoTool-'):]
    return f'{stem} - Google Drive - {now.strftime("%Y%m%d-%H%M%S")}'[:240]


def _utf16_length(value):
    return len(value.encode('utf-16-le')) // 2


def _doc_content(markdown):
    """Return clean text plus Docs paragraph-style and bullet ranges."""
    output = []
    styles = []
    bullets = []
    index = 1
    bullet_start = None
    for raw in markdown.splitlines():
        line = raw
        style = 'NORMAL_TEXT'
        bullet = False
        if line.startswith('### '):
            line, style = line[4:], 'HEADING_3'
        elif line.startswith('## '):
            line, style = line[3:], 'HEADING_2'
        elif line.startswith('# '):
            line, style = line[2:], 'TITLE'
        elif line.startswith('- '):
            line, bullet = line[2:], True
        rendered = line + '\n'
        start = index
        index += _utf16_length(rendered)
        output.append(rendered)
        if style != 'NORMAL_TEXT':
            styles.append((start, index, style))
        if bullet and bullet_start is None:
            bullet_start = start
        if not bullet and bullet_start is not None:
            bullets.append((bullet_start, start))
            bullet_start = None
    if bullet_start is not None:
        bullets.append((bullet_start, index))
    return ''.join(output), styles, bullets


def _folder(drive, cancel=None, report=None):
    query = ("trashed = false and mimeType = 'application/vnd.google-apps.folder' and "
             "appProperties has { key='videotoolRole' and value='gemini-results' }")
    found = _execute(lambda: drive.files().list(q=query, spaces='drive', fields='files(id,name)',
                                                pageSize=10), cancel, report)
    files = found.get('files', [])
    if files:
        return files[0]['id']
    created = _execute(lambda: drive.files().create(
        body={'name': RESULTS_FOLDER, 'mimeType': FOLDER_MIME,
              'appProperties': {'videotoolRole': 'gemini-results'}}, fields='id'), cancel, report)
    return created['id']


def _existing_document(drive, digest, cancel=None, report=None, retry_delays=(2, 5, 10)):
    query = ("trashed = false and mimeType = 'application/vnd.google-apps.document' and "
             "appProperties has { key='videotoolRole' and value='gemini-response' } and "
             f"appProperties has {{ key='responseSha256' and value='{digest}' }}")
    found = _execute(lambda: drive.files().list(
        q=query, spaces='drive', fields='files(id,name,webViewLink,parents)', pageSize=10),
        cancel, report, retry_delays)
    files = found.get('files', [])
    return files[0] if files else None


def _save_delivery_receipts(path, result, saved_at):
    metadata = asdict(result)
    metadata['saved_at'] = saved_at
    _atomic_sidecar(metadata_path(path), json.dumps(metadata, indent=2) + '\n')
    receipt = (
        '# VideoTool Google Drive delivery\n\n'
        f'- Local response: `{path.name}`\n'
        f'- Google Doc: {result.url}\n'
        f'- Google Doc ID: `{result.document_id}`\n'
        f'- Drive folder: `{RESULTS_FOLDER}`\n'
        f'- Saved: {saved_at}\n'
        f'- Drive save time: {result.seconds:.1f} seconds\n\n'
        '## Validation\n\n'
        'The Gemini response was already saved locally before VideoTool created and populated '
        'the native Google Doc. This receipt can be retried without uploading the video or '
        'running Gemini again.\n')
    _atomic_sidecar(receipt_path(path), receipt)


def deliver(response_path, cancel=None, report=None, services=None, now=None,
            retry_delays=(2, 5, 10)):
    """Save an existing local Markdown result as a native Google Doc exactly once."""
    path, data, digest = _response_file(response_path)
    prior = previous_delivery(path)
    if prior:
        return prior
    report = report or (lambda stage, message: None)
    _check_cancel(cancel)
    report('Saving to Drive', 'Saving the local Gemini response to Google Drive…')
    started = time.monotonic()
    drive, docs = services or _services()
    folder_id = _folder(drive, cancel, report)
    existing = _existing_document(drive, digest, cancel, report, retry_delays)
    if existing:
        url = existing.get('webViewLink') or f'https://docs.google.com/document/d/{existing["id"]}/edit'
        result = DeliveryResult(existing['id'], url, existing.get('name') or _safe_title(path, now),
                                (existing.get('parents') or [folder_id])[0],
                                time.monotonic() - started, digest)
        _save_delivery_receipts(
            path, result, (now or datetime.now().astimezone()).isoformat(timespec='seconds'))
        report('Saved to Drive', f'Existing Google Doc found: {result.title}')
        return result
    title = _safe_title(path, now)
    created = _execute(lambda: drive.files().create(
        body={'name': title, 'mimeType': DOC_MIME, 'parents': [folder_id],
              'appProperties': {'videotoolRole': 'gemini-response',
                                'responseSha256': digest}}, fields='id,webViewLink'),
        cancel, report, retry_delays)
    document_id = created['id']
    text, styles, bullets = _doc_content(data.decode('utf-8'))
    requests = [{'insertText': {'location': {'index': 1}, 'text': text}}]
    requests.extend({'updateParagraphStyle': {
        'range': {'startIndex': start, 'endIndex': end},
        'paragraphStyle': {'namedStyleType': style}, 'fields': 'namedStyleType'}}
                    for start, end, style in styles)
    requests.extend({'createParagraphBullets': {
        'range': {'startIndex': start, 'endIndex': end},
        'bulletPreset': 'BULLET_DISC_CIRCLE_SQUARE'}} for start, end in bullets)
    _execute(lambda: docs.documents().batchUpdate(
        documentId=document_id, body={'requests': requests}), cancel, report, retry_delays)
    seconds = time.monotonic() - started
    url = created.get('webViewLink') or f'https://docs.google.com/document/d/{document_id}/edit'
    result = DeliveryResult(document_id, url, title, folder_id, seconds, digest)
    saved_at = (now or datetime.now().astimezone()).isoformat(timespec='seconds')
    _save_delivery_receipts(path, result, saved_at)
    report('Saved to Drive', f'Google Doc saved: {title}')
    return result
