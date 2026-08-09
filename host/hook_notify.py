#!/usr/bin/python3
"""Forward privacy-minimized Codex hook events to the local VFD bridge."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from hook_ipc import send_hook_event


def normalize_event(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Return only fields needed by the bridge; never forward hook content."""
    hook_name = payload.get("hook_event_name")
    event: Optional[str] = None
    if hook_name == "PreToolUse" and payload.get("tool_name") == "request_user_input":
        event = "INPUT"
    elif hook_name == "PermissionRequest":
        event = "APPROVAL"
    elif hook_name == "Stop":
        event = "DONE"
    if event is None:
        return None

    result = {"event": event}
    for key in ("session_id", "turn_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            return 0
        event = normalize_event(payload)
        if event is None:
            return 0
        send_hook_event(event)
    except (OSError, ValueError, TypeError):
        # Hooks are observability only. A stopped bridge must never block Codex.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
