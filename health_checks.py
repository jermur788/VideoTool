"""Read-only readiness checks shared by the GUI and installer."""

from dataclasses import dataclass
import importlib
import shutil
import sys

import gemini_analysis


@dataclass(frozen=True)
class Check:
    name: str
    ready: bool
    detail: str
    optional: bool = False


def checks(which=shutil.which, importer=importlib.import_module):
    python_ready = sys.version_info >= (3, 10)
    result = [Check('Python', python_ready,
                    f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}' +
                    ('' if python_ready else ' — version 3.10 or newer is required'))]
    try:
        importer('tkinter')
    except (ImportError, ModuleNotFoundError):
        result.append(Check('Desktop window', False,
                            'Tkinter missing — install python3-tk'))
    else:
        result.append(Check('Desktop window', True, 'Tkinter is available'))
    for command in ('ffmpeg', 'ffprobe'):
        location = which(command)
        result.append(Check(command, bool(location),
                            location or f'{command} was not found on PATH'))
    sdk = gemini_analysis.sdk_present()
    key = gemini_analysis.api_key_present()
    if sdk and key:
        detail = 'Client installed and API key saved'
    elif not sdk and not key:
        detail = 'Optional client and API key are not set up'
    elif not sdk:
        detail = 'API key saved; optional client is not installed'
    else:
        detail = 'Client installed; API key is not saved'
    result.append(Check('Gemini', sdk and key, detail, optional=True))
    return result


def report(items=None):
    items = checks() if items is None else items
    lines = []
    for item in items:
        mark = 'Ready' if item.ready else ('Optional' if item.optional else 'Needs attention')
        lines.append(f'{item.name}: {mark} — {item.detail}')
    return '\n'.join(lines)
