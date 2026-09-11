"""Safe user-level desktop installation for VideoTool."""

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from videotool_version import __version__


APP_FILES = (
    'conversion_history.py', 'gemini_analysis.py', 'health_checks.py',
    'processing_receipts.py', 'tooltips.py', 'upload_presets.py',
    'user_settings.py', 'videotool.py', 'videotool_gui.py',
    'videotool_version.py', 'requirements-gemini.txt', 'README.md',
    'NEXT_VERSION.md', 'install-videotool.py', 'desktop_install.py',
)
MANIFEST = '.videotool-install.json'


class InstallError(Exception):
    pass


def destinations(environ=None, home=None):
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    data = Path(environ.get('XDG_DATA_HOME', home / '.local' / 'share')).expanduser()
    binary = Path(environ.get('VIDEOTOOL_BIN_DIR', home / '.local' / 'bin')).expanduser()
    return {
        'app': data / 'videotool',
        'desktop': data / 'applications' / 'videotool.desktop',
        'icon': data / 'icons' / 'hicolor' / 'scalable' / 'apps' / 'videotool.svg',
        'launcher': binary / 'videotool',
        'uninstaller': binary / 'videotool-uninstall',
    }


def _atomic_bytes(target, data, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.videotool-',
                                         delete=False) as temporary:
            temporary.write(data)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(mode)
        os.replace(temporary_path, target)
    except OSError as exc:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        raise InstallError(f'Could not install {target}: {exc}') from exc


def _managed(paths):
    try:
        data = json.loads((paths['app'] / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and data.get('application') == 'VideoTool' else None


def _desktop_quote(value):
    value = str(value)
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$') + '"'


def install(source_root=None, paths=None, update=False, with_gemini=False, runner=subprocess.run):
    source = Path(source_root or Path(__file__).resolve().parent).resolve()
    paths = destinations() if paths is None else {key: Path(value) for key, value in paths.items()}
    manifest = _managed(paths)
    if update and manifest is None:
        raise InstallError('VideoTool is not installed here yet. Run the installer without --update first.')
    if not update and manifest is None:
        collisions = [path for path in paths.values() if path.exists()]
        if collisions:
            raise InstallError(f'Refusing to replace an unmanaged file or folder: {collisions[0]}')
    missing = [name for name in APP_FILES if not (source / name).is_file()]
    for name in ('packaging/videotool.svg', 'packaging/videotool.desktop.in',
                 'packaging/videotool-launcher.sh.in', 'packaging/videotool-uninstall.sh.in'):
        if not (source / name).is_file():
            missing.append(name)
    if missing:
        raise InstallError(f'Installation source is incomplete: {missing[0]}')

    paths['app'].mkdir(parents=True, exist_ok=True)
    if manifest:
        for old_name in manifest.get('app_files', []):
            if old_name not in APP_FILES and Path(old_name).name == old_name:
                (paths['app'] / old_name).unlink(missing_ok=True)
    for name in APP_FILES:
        _atomic_bytes(paths['app'] / name, (source / name).read_bytes(),
                      0o755 if name == 'install-videotool.py' else 0o644)
    _atomic_bytes(paths['icon'], (source / 'packaging/videotool.svg').read_bytes())
    launcher = (source / 'packaging/videotool-launcher.sh.in').read_text(encoding='utf-8')
    launcher = launcher.replace('@APP_DIR@', shlex.quote(str(paths['app'])))
    _atomic_bytes(paths['launcher'], launcher.encode(), 0o755)
    uninstaller = (source / 'packaging/videotool-uninstall.sh.in').read_text(encoding='utf-8')
    uninstaller = uninstaller.replace('@INSTALLER@', shlex.quote(str(paths['app'] / 'install-videotool.py')))
    _atomic_bytes(paths['uninstaller'], uninstaller.encode(), 0o755)
    desktop = (source / 'packaging/videotool.desktop.in').read_text(encoding='utf-8')
    desktop = desktop.replace('@EXEC@', _desktop_quote(paths['launcher']))
    _atomic_bytes(paths['desktop'], desktop.encode(), 0o644)

    managed_venv = bool(manifest and manifest.get('managed_venv'))
    record = {
        'application': 'VideoTool', 'version': __version__,
        'app_files': list(APP_FILES), 'managed_venv': managed_venv,
    }
    _atomic_bytes(paths['app'] / MANIFEST, (json.dumps(record, indent=2) + '\n').encode())
    if with_gemini:
        environment = paths['app'] / '.venv'
        if environment.exists() and not managed_venv:
            raise InstallError('The installed .venv is not managed by VideoTool, so it was left unchanged.')
        created_environment = not environment.exists()
        try:
            if created_environment:
                runner([sys.executable, '-m', 'venv', '--system-site-packages', str(environment)], check=True)
            runner([str(environment / 'bin' / 'python'), '-m', 'pip', 'install',
                    '-r', str(paths['app'] / 'requirements-gemini.txt')], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            if created_environment:
                shutil.rmtree(environment, ignore_errors=True)
            raise InstallError(
                'VideoTool was installed, but optional Gemini setup failed. '
                'The local conversion features are still installed and a later --update '
                f'--with-gemini can retry it: {exc}') from exc
        managed_venv = True
        record['managed_venv'] = True
        _atomic_bytes(paths['app'] / MANIFEST, (json.dumps(record, indent=2) + '\n').encode())
    return paths


def uninstall(paths=None, remove_settings=False, environ=None, home=None):
    paths = destinations(environ, home) if paths is None else {key: Path(value) for key, value in paths.items()}
    manifest = _managed(paths)
    if manifest is None:
        raise InstallError('No managed VideoTool installation was found.')
    for key in ('desktop', 'icon', 'launcher', 'uninstaller'):
        paths[key].unlink(missing_ok=True)
    for name in manifest.get('app_files', []):
        if name in APP_FILES:
            (paths['app'] / name).unlink(missing_ok=True)
    if manifest.get('managed_venv'):
        shutil.rmtree(paths['app'] / '.venv', ignore_errors=True)
    (paths['app'] / MANIFEST).unlink(missing_ok=True)
    try:
        paths['app'].rmdir()
    except OSError:
        pass
    if remove_settings:
        config = Path((environ or os.environ).get('XDG_CONFIG_HOME', Path(home or Path.home()) / '.config')) / 'videotool'
        shutil.rmtree(config, ignore_errors=True)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install VideoTool for the current user.')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--update', action='store_true', help='update an existing managed installation')
    action.add_argument('--uninstall', action='store_true', help='remove the managed installation')
    parser.add_argument('--with-gemini', action='store_true', help='download optional Gemini packages')
    parser.add_argument('--remove-settings', action='store_true',
                        help='with --uninstall, also remove saved non-secret preferences')
    args = parser.parse_args(argv)
    if args.remove_settings and not args.uninstall:
        parser.error('--remove-settings requires --uninstall')
    try:
        paths = (uninstall(remove_settings=args.remove_settings) if args.uninstall else
                 install(update=args.update, with_gemini=args.with_gemini))
    except (InstallError, OSError, subprocess.CalledProcessError) as exc:
        print(f'VideoTool installation failed: {exc}', file=sys.stderr)
        return 1
    if args.uninstall:
        print('VideoTool was removed. Your preferences were ' +
              ('also removed.' if args.remove_settings else 'preserved.'))
    else:
        verb = 'updated' if args.update else 'installed'
        print(f'VideoTool {__version__} was {verb} for this user.')
        print(f'Open VideoTool from the application menu, or run: {paths["launcher"]}')
        print(f'Check readiness at any time with: {paths["launcher"]} --health')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
