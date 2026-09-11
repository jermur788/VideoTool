#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$script_dir/.venv/bin/python" ]; then
    exec "$script_dir/.venv/bin/python" "$script_dir/videotool_gui.py"
fi
exec python3 "$script_dir/videotool_gui.py"
