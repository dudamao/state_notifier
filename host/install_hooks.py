#!/usr/bin/python3
"""Merge or remove State Notifier hooks without touching config.toml."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

from runtime_paths import default_codex_home


HOOKS_PATH = default_codex_home() / "hooks.json"
HOOK_SCRIPT = Path(__file__).resolve().parent / "hook_notify.py"


def hook_commands(python_executable: str, hook_script: Path) -> tuple[str, str]:
    arguments = [python_executable, str(hook_script)]
    posix = " ".join(shlex.quote(argument) for argument in arguments)
    windows = subprocess.list2cmdline(arguments)
    return posix, windows


HOOK_COMMAND, HOOK_COMMAND_WINDOWS = hook_commands(sys.executable, HOOK_SCRIPT)
LEGACY_HOOK_COMMAND = '/usr/bin/python3 "{}"'.format(
    HOOK_SCRIPT.as_posix().replace('"', '\\"'))


def definitions() -> Dict[str, List[Dict[str, Any]]]:
    handler = {
        "type": "command",
        "command": HOOK_COMMAND,
        "commandWindows": HOOK_COMMAND_WINDOWS,
        "timeout": 1,
    }
    return {
        "PreToolUse": [{"matcher": "^request_user_input$", "hooks": [dict(handler)]}],
        "PermissionRequest": [{"matcher": ".*", "hooks": [dict(handler)]}],
        "Stop": [{"hooks": [dict(handler)]}],
    }


def load_document(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"description": "User-level Codex lifecycle hooks.", "hooks": {}}
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError("hooks.json root must be an object")
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json 'hooks' must be an object")
    return document


def is_ours(handler: Any) -> bool:
    return isinstance(handler, dict) and (
        handler.get("command") == HOOK_COMMAND or
        handler.get("command") == LEGACY_HOOK_COMMAND or
        handler.get("commandWindows") == HOOK_COMMAND_WINDOWS
    )


def merge_hooks(document: Dict[str, Any]) -> bool:
    changed = False
    hooks = document.setdefault("hooks", {})
    for event, groups in definitions().items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise ValueError("hooks.{} must be an array".format(event))
        for candidate in groups:
            duplicate = any(
                isinstance(group, dict)
                and group.get("matcher") == candidate.get("matcher")
                and any(is_ours(item) for item in group.get("hooks", []))
                for group in existing
            )
            if not duplicate:
                existing.append(candidate)
                changed = True
    return changed


def remove_hooks(document: Dict[str, Any]) -> bool:
    changed = False
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept_handlers = [item for item in group["hooks"] if not is_ours(item)]
            if len(kept_handlers) != len(group["hooks"]):
                changed = True
            if kept_handlers:
                replacement = dict(group)
                replacement["hooks"] = kept_handlers
                kept_groups.append(replacement)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    return changed


def backup(path: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = path.with_name("{}.bak-{}".format(path.name, stamp))
    shutil.copy2(path, destination)
    return destination


def atomic_write(path: Path, document: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="hooks.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "status"), nargs="?", default="install")
    parser.add_argument("--path", type=Path, default=HOOKS_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args()

    document = load_document(args.path)
    if args.action == "status":
        count = sum(
            1
            for groups in document.get("hooks", {}).values()
            if isinstance(groups, list)
            for group in groups
            if isinstance(group, dict)
            for handler in group.get("hooks", [])
            if is_ours(handler)
        )
        print("State Notifier hook handlers: {}".format(count))
        return 0

    changed = merge_hooks(document) if args.action == "install" else remove_hooks(document)
    if not changed:
        print("No change: State Notifier hooks are already {}.".format(
            "installed" if args.action == "install" else "absent"))
        return 0

    backup_path = backup(args.path) if args.path.exists() else None
    atomic_write(args.path, document)
    print("Updated {}".format(args.path))
    if backup_path is not None:
        print("Backup: {}".format(backup_path))
    if args.action == "install":
        print("Open /hooks in Codex, review these commands, and trust them before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
