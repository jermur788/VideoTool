"""Small persistent preferences for the VideoTool desktop window."""

import json
import os
from pathlib import Path
import tempfile


def settings_path(environ=None, home=None):
    environ = os.environ if environ is None else environ
    base = environ.get('XDG_CONFIG_HOME')
    if base:
        return Path(base).expanduser() / 'videotool' / 'settings.json'
    home = Path.home() if home is None else Path(home)
    return home / '.config' / 'videotool' / 'settings.json'


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def default_source_folder(path=None):
    """Return a configured, currently available folder, otherwise None."""
    value = _read(path or settings_path()).get('default_source_folder')
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def save_default_source_folder(folder, path=None):
    """Persist an existing source folder without creating or changing it."""
    candidate = Path(folder).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f'The starting folder is not available: {candidate}') from exc
    if not resolved.is_dir():
        raise ValueError(f'The starting folder is not a folder: {candidate}')
    target = Path(path or settings_path())
    data = _read(target)
    data['default_source_folder'] = str(resolved)
    temporary_path = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent,
                                         prefix='.settings-', delete=False) as temporary:
            json.dump(data, temporary, indent=2)
            temporary.write('\n')
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
    except OSError as exc:
        if temporary_path:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ValueError(f'VideoTool could not save the starting folder: {exc}') from exc
    return resolved


def clear_default_source_folder(path=None):
    """Return the picker to its normal operating-system-selected location."""
    target = Path(path or settings_path())
    data = _read(target)
    if 'default_source_folder' not in data:
        return False
    data.pop('default_source_folder')
    try:
        if data:
            target.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        else:
            target.unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError(f'VideoTool could not reset the starting folder: {exc}') from exc
    return True
