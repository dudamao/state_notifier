#!/bin/zsh
set -eu

script_dir=${0:A:h}
local_python="$script_dir/.venv/bin/python"
eim_python=/Users/duyikang/.espressif/tools/python/v6.0.2/venv/bin/python

if [[ -x "$local_python" ]] && "$local_python" -c 'import serial, PIL' >/dev/null 2>&1; then
  exec "$local_python" "$script_dir/bridge.py" "$@"
fi

if [[ -x "$eim_python" ]] && "$eim_python" -c 'import serial, PIL' >/dev/null 2>&1; then
  exec "$eim_python" "$script_dir/bridge.py" "$@"
fi

if python3 -c 'import serial, PIL' >/dev/null 2>&1; then
  exec python3 "$script_dir/bridge.py" "$@"
fi

print -u2 "Host dependencies are unavailable. Run ./host/setup_macos.sh first."
exit 2
