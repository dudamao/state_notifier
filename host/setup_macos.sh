#!/bin/zsh
set -eu

script_dir=${0:A:h}
venv_dir="$script_dir/.venv"

python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --disable-pip-version-check -r "$script_dir/requirements.txt"
"$venv_dir/bin/python" -m pip install --disable-pip-version-check --no-deps --force-reinstall "$script_dir"
print "macOS host environment is ready: $venv_dir"
