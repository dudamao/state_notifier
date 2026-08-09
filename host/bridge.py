#!/usr/bin/env python3
"""Render local Codex state and rate limits to an ESP32-C5 SN2 VFD."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import datetime as dt
import glob
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from hook_ipc import HookReceiver
from runtime_paths import default_codex_home, default_endpoint_path, default_socket_path
from vfd_display import Bitmap7, Frame40x7, TextRenderer, VfdClient
from vfd_display.client import choose_port, open_serial_port

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - reported cleanly by main
    serial = None
    list_ports = None


IS_WINDOWS = os.name == "nt"
DEFAULT_SOCKET = default_socket_path()
DEFAULT_ENDPOINT = default_endpoint_path()
DESKTOP_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
STATE_PRIORITY = ("APPROVAL", "INPUT", "ERROR", "DONE", "RUNNING", "IDLE")


def log(message: str, verbose: bool = True) -> None:
    if verbose:
        print("[state-notifier] {}".format(message), flush=True)


def parse_timestamp(value: Any, fallback: float) -> float:
    if not isinstance(value, str):
        return fallback
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return fallback


@dataclass(frozen=True)
class LimitWindow:
    minutes: int
    remaining: int
    resets_at: Optional[int] = None


@dataclass(frozen=True)
class LimitSnapshot:
    primary: Optional[LimitWindow]
    secondary: Optional[LimitWindow]
    updated_at: float
    source: str

def _integer(mapping: Dict[str, Any], *names: str) -> Optional[int]:
    for name in names:
        value = mapping.get(name)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return None


def parse_limit_snapshot(payload: Any, now: Optional[float] = None,
                         source: str = "unknown") -> Optional[LimitSnapshot]:
    if not isinstance(payload, dict):
        return None
    timestamp = time.time() if now is None else now

    def parse_window(value: Any) -> Optional[LimitWindow]:
        if not isinstance(value, dict):
            return None
        minutes = _integer(value, "windowDurationMins", "window_minutes", "window_duration_mins")
        used = _integer(value, "usedPercent", "used_percent")
        reset = _integer(value, "resetsAt", "resets_at")
        if minutes is None or used is None or not 1 <= minutes <= 525600:
            return None
        used = min(100, max(0, used))
        return LimitWindow(minutes=minutes, remaining=100 - used, resets_at=reset)

    primary = parse_window(payload.get("primary"))
    secondary = parse_window(payload.get("secondary"))
    if primary is None and secondary is None:
        return None
    return LimitSnapshot(primary, secondary, timestamp, source)


@dataclass
class SessionStatus:
    running: bool = False
    attention: Optional[str] = None
    approval_due: Optional[float] = None
    approval_marker: float = 0.0
    done_until: float = 0.0
    error_until: float = 0.0
    last_activity: float = 0.0
    pending_inputs: Set[str] = field(default_factory=set)
    summary: str = ""
    summary_updated_at: float = 0.0


def summarize_task(message: Any, maximum: int = 64) -> str:
    """Return a bounded, display-safe ASCII label from one root user message."""
    if not isinstance(message, str) or maximum < 1:
        return ""
    lines = []
    in_code_block = False
    for raw_line in message.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or not stripped:
            continue
        lowered = stripped.lower()
        if lowered in ("# files mentioned by the user:", "# files mentioned:"):
            continue
        if re.match(r"^##\s+[^:]+:\s*[/\\]", stripped):
            continue
        stripped = re.sub(r"^[#>*+\-\d.\s]+", "", stripped)
        stripped = re.sub(r"!?\[([^]]*)\]\([^)]*\)", r"\1", stripped)
        stripped = re.sub(r"https?://\S+", "", stripped)
        stripped = stripped.replace("`", "").replace("**", "").replace("__", "")
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped:
            lines.append(stripped)
    if not lines:
        return ""
    text = " ".join(lines)
    sentence = re.split(r"(?<=[。.！？!?；;])\s*", text, maxsplit=1)[0]
    replacements = str.maketrans({
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "…": "...", "•": "*",
        "→": ">", "←": "<",
    })
    normalized = unicodedata.normalize("NFKD", sentence.translate(replacements))
    has_chinese = any(
        "\u3400" <= character <= "\u4DBF" or
        "\u4E00" <= character <= "\u9FFF" or
        "\uF900" <= character <= "\uFAFF"
        for character in normalized
    )
    ascii_text = "".join(
        character if " " <= character <= "~" else " "
        for character in normalized
        if not unicodedata.combining(character)
    )
    ascii_text = re.sub(r"\s+", " ", ascii_text).strip().upper()
    if has_chinese and re.search(r"[A-Z0-9]", ascii_text):
        ascii_text = "CN " + ascii_text
    elif has_chinese:
        ascii_text = "CN TASK"
    elif not ascii_text:
        ascii_text = "TASK"
    return ascii_text[:maximum]


class StateAggregator:
    def __init__(self, approval_delay: float = 0.75, done_seconds: float = 10.0,
                 stale_running_seconds: float = 1800.0) -> None:
        self.sessions: Dict[str, SessionStatus] = {}
        self.approval_delay = approval_delay
        self.done_seconds = done_seconds
        self.stale_running_seconds = stale_running_seconds

    def _session(self, session_id: Optional[str]) -> SessionStatus:
        return self.sessions.setdefault(session_id or "unknown", SessionStatus())

    def hook(self, payload: Dict[str, Any], now: float) -> None:
        event = payload.get("event")
        session = self._session(payload.get("session_id"))
        session.last_activity = now
        if event == "INPUT":
            session.running = True
            session.attention = "INPUT"
            session.approval_due = None
        elif event == "APPROVAL":
            session.running = True
            session.approval_due = now + self.approval_delay
            session.approval_marker = now
        elif event == "DONE":
            session.running = False
            session.attention = None
            session.approval_due = None
            session.pending_inputs.clear()
            session.done_until = max(session.done_until, now + self.done_seconds)

    def task_started(self, session_id: str, when: float) -> None:
        session = self._session(session_id)
        session.running = True
        session.attention = None
        session.approval_due = None
        session.pending_inputs.clear()
        session.last_activity = max(session.last_activity, when)
        session.done_until = 0.0
        session.error_until = 0.0

    def task_complete(self, session_id: str, when: float) -> None:
        session = self._session(session_id)
        session.running = False
        session.attention = None
        session.approval_due = None
        session.pending_inputs.clear()
        session.last_activity = max(session.last_activity, when)
        session.done_until = max(session.done_until, when + self.done_seconds)

    def task_aborted(self, session_id: str, when: float) -> None:
        session = self._session(session_id)
        session.running = False
        session.attention = None
        session.approval_due = None
        session.pending_inputs.clear()
        session.last_activity = max(session.last_activity, when)
        session.error_until = max(session.error_until, when + self.done_seconds)

    def input_call(self, session_id: str, call_id: str, when: float) -> None:
        session = self._session(session_id)
        session.running = True
        session.attention = "INPUT"
        session.approval_due = None
        session.pending_inputs.add(call_id)
        session.last_activity = max(session.last_activity, when)

    def input_output(self, session_id: str, call_id: str, when: float) -> None:
        session = self._session(session_id)
        if call_id not in session.pending_inputs:
            self.progress(session_id, when)
            return
        session.pending_inputs.discard(call_id)
        if not session.pending_inputs and session.attention == "INPUT":
            session.attention = None
        session.running = True
        session.last_activity = max(session.last_activity, when)

    def progress(self, session_id: str, when: float) -> None:
        session = self._session(session_id)
        if when >= session.approval_marker:
            session.approval_due = None
            if session.attention == "APPROVAL":
                session.attention = None
        session.last_activity = max(session.last_activity, when)

    def user_message(self, session_id: str, message: Any, when: float) -> None:
        summary = summarize_task(message)
        if not summary:
            return
        session = self._session(session_id)
        if when >= session.summary_updated_at:
            session.summary = summary
            session.summary_updated_at = when
            session.last_activity = max(session.last_activity, when)

    def selected(self, now: float) -> Tuple[str, Optional[str], str]:
        for session in self.sessions.values():
            if session.approval_due is not None and now >= session.approval_due:
                session.attention = "APPROVAL"
                session.approval_due = None
            if (session.running and not session.attention and session.last_activity and
                    now - session.last_activity > self.stale_running_seconds):
                session.running = False

        expired = [
            session_id for session_id, session in self.sessions.items()
            if not session.running and not session.attention
            and now >= session.done_until and now >= session.error_until
            and session.last_activity and now - session.last_activity > self.stale_running_seconds
        ]
        for session_id in expired:
            self.sessions.pop(session_id, None)

        candidates = []
        for session_id, session in self.sessions.items():
            states = []
            if session.attention:
                states.append(session.attention)
            if now < session.error_until:
                states.append("ERROR")
            if now < session.done_until:
                states.append("DONE")
            if session.running:
                states.append("RUNNING")
            if states:
                state = min(states, key=STATE_PRIORITY.index)
                candidates.append((STATE_PRIORITY.index(state), -session.last_activity,
                                   state, session_id, session.summary))
        if not candidates:
            return "IDLE", None, ""
        _, _, state, session_id, summary = min(candidates)
        return state, session_id, summary

    def current(self, now: float) -> str:
        return self.selected(now)[0]


@dataclass
class TailFile:
    offset: int = 0
    session_id: Optional[str] = None
    is_root: bool = True
    metadata_seen: bool = False
    ignored: bool = False
    device: Optional[int] = None
    inode: Optional[int] = None
    header_prefix: Optional[bytes] = None


JsonBounds = Tuple[int, int]


def _skip_whitespace(raw: bytes, position: int) -> int:
    while position < len(raw) and raw[position] in b" \t\r\n":
        position += 1
    return position


def _skip_json_string(raw: bytes, position: int) -> int:
    if position >= len(raw) or raw[position] != ord('"'):
        raise ValueError("expected JSON string")
    position += 1
    while position < len(raw):
        byte = raw[position]
        if byte == ord('\\'):
            position += 2
            continue
        position += 1
        if byte == ord('"'):
            return position
    raise ValueError("unterminated JSON string")


def _skip_json_value(raw: bytes, position: int) -> int:
    position = _skip_whitespace(raw, position)
    if position >= len(raw):
        raise ValueError("missing JSON value")
    if raw[position] == ord('"'):
        return _skip_json_string(raw, position)
    if raw[position] in (ord('{'), ord('[')):
        stack = [raw[position]]
        position += 1
        while position < len(raw) and stack:
            byte = raw[position]
            if byte == ord('"'):
                position = _skip_json_string(raw, position)
                continue
            if byte in (ord('{'), ord('[')):
                stack.append(byte)
            elif byte == ord('}'):
                if stack[-1] != ord('{'):
                    raise ValueError("mismatched JSON object")
                stack.pop()
            elif byte == ord(']'):
                if stack[-1] != ord('['):
                    raise ValueError("mismatched JSON array")
                stack.pop()
            position += 1
        if stack:
            raise ValueError("unterminated JSON compound")
        return position
    while position < len(raw) and raw[position] not in b",}] \t\r\n":
        position += 1
    return position


def _json_object_members(raw: bytes, bounds: Optional[JsonBounds] = None) -> Dict[str, JsonBounds]:
    start, end = bounds if bounds is not None else (0, len(raw))
    position = _skip_whitespace(raw, start)
    if position >= end or raw[position] != ord('{'):
        raise ValueError("expected JSON object")
    position += 1
    members: Dict[str, JsonBounds] = {}
    while True:
        position = _skip_whitespace(raw, position)
        if position >= end:
            raise ValueError("unterminated JSON object")
        if raw[position] == ord('}'):
            return members
        key_start = position
        key_end = _skip_json_string(raw, key_start)
        key = json.loads(raw[key_start:key_end])
        if not isinstance(key, str):
            raise ValueError("invalid JSON key")
        position = _skip_whitespace(raw, key_end)
        if position >= end or raw[position] != ord(':'):
            raise ValueError("missing JSON colon")
        value_start = _skip_whitespace(raw, position + 1)
        value_end = _skip_json_value(raw, value_start)
        members[key] = (value_start, value_end)
        position = _skip_whitespace(raw, value_end)
        if position < end and raw[position] == ord(','):
            position += 1
            continue
        if position < end and raw[position] == ord('}'):
            return members
        raise ValueError("invalid JSON object separator")


def _json_member_string(raw: bytes, members: Dict[str, JsonBounds],
                        key: str) -> Optional[str]:
    bounds = members.get(key)
    if bounds is None:
        return None
    try:
        value = json.loads(raw[bounds[0]:bounds[1]])
    except (ValueError, TypeError, UnicodeError):
        return None
    return value if isinstance(value, str) else None


class RolloutTailer:
    def __init__(self, codex_home: Path, aggregator: StateAggregator,
                 limit_callback: Callable[[LimitSnapshot], None],
                 recovery_seconds: float = 1800.0) -> None:
        self.root = codex_home / "sessions"
        self.aggregator = aggregator
        self.limit_callback = limit_callback
        self.recovery_seconds = recovery_seconds
        self.files: Dict[Path, TailFile] = {}
        self.last_discovery = 0.0
        self.last_poll = 0.0

    @staticmethod
    def _reset_file_state(state: TailFile) -> None:
        state.offset = 0
        state.session_id = None
        state.is_root = True
        state.metadata_seen = False
        state.ignored = False

    def _discover(self, now: float) -> None:
        if now - self.last_discovery < 1.0:
            return
        self.last_discovery = now
        pattern = str(self.root / "*" / "*" / "*" / "rollout-*.jsonl")
        for name in glob.glob(pattern):
            path = Path(name)
            if path in self.files:
                continue
            try:
                if now - path.stat().st_mtime <= self.recovery_seconds:
                    self.files[path] = TailFile()
            except OSError:
                continue

    def poll(self, now: float) -> None:
        if now - self.last_poll < 0.5:
            return
        self.last_poll = now
        self._discover(now)
        for path, state in list(self.files.items()):
            try:
                file_info = path.stat()
                if now - file_info.st_mtime > self.recovery_seconds:
                    self.files.pop(path, None)
                    continue
                size = file_info.st_size
                identity_changed = (
                    state.device is not None and
                    (state.device != file_info.st_dev or state.inode != file_info.st_ino)
                )
                state.device = file_info.st_dev
                state.inode = file_info.st_ino
                with path.open("rb") as identity_handle:
                    sample = identity_handle.read(256)
                if b"\n" in sample:
                    prefix = sample.split(b"\n", 1)[0] + b"\n"
                elif len(sample) == 256:
                    prefix = sample
                else:
                    prefix = None
                if (state.header_prefix is not None and prefix is not None and
                        prefix != state.header_prefix):
                    identity_changed = True
                if prefix is not None:
                    state.header_prefix = prefix
                if identity_changed or size < state.offset:
                    self._reset_file_state(state)
                if state.ignored:
                    state.offset = size
                    continue
                if size == state.offset:
                    continue
                with path.open("rb") as handle:
                    handle.seek(state.offset)
                    while True:
                        line_start = handle.tell()
                        raw = handle.readline()
                        if not raw:
                            break
                        # Do not consume a record that is still being appended.
                        if not raw.endswith(b"\n"):
                            state.offset = line_start
                            break
                        state.offset = handle.tell()
                        self.process_raw_record(state, raw, now)
                        if state.ignored:
                            state.offset = size
                            break
            except (OSError, ValueError):
                continue

    def process_raw_record(self, state: TailFile, raw: bytes, now: float) -> None:
        try:
            top = _json_object_members(raw)
        except (ValueError, TypeError, UnicodeError):
            return
        kind = _json_member_string(raw, top, "type")
        when = parse_timestamp(_json_member_string(raw, top, "timestamp"), now)
        payload_bounds = top.get("payload")
        if payload_bounds is None:
            return
        try:
            payload = _json_object_members(raw, payload_bounds)
        except (ValueError, TypeError, UnicodeError):
            return

        if kind == "session_meta":
            if state.metadata_seen:
                return
            state.metadata_seen = True
            state.session_id = _json_member_string(raw, payload, "id")
            state.is_root = True
            source_bounds = payload.get("source")
            if source_bounds is not None and raw[source_bounds[0]:source_bounds[0] + 1] == b"{":
                try:
                    source = _json_object_members(raw, source_bounds)
                    state.is_root = (
                        "subagent" not in source and
                        _json_member_string(raw, source, "type") != "subagent"
                    )
                except (ValueError, TypeError, UnicodeError):
                    return
            state.ignored = not state.is_root
            return
        if not state.is_root or not state.session_id:
            return

        payload_type = _json_member_string(raw, payload, "type")
        if kind == "event_msg":
            if payload_type == "user_message":
                self.aggregator.user_message(
                    state.session_id, _json_member_string(raw, payload, "message"), when)
            elif payload_type == "task_started":
                self.aggregator.task_started(state.session_id, when)
            elif payload_type == "task_complete":
                self.aggregator.task_complete(state.session_id, when)
            elif payload_type == "turn_aborted":
                self.aggregator.task_aborted(state.session_id, when)
            elif payload_type == "token_count":
                rate_bounds = payload.get("rate_limits")
                info_bounds = payload.get("info")
                if rate_bounds is None and info_bounds is not None:
                    try:
                        info = _json_object_members(raw, info_bounds)
                        rate_bounds = info.get("rate_limits")
                    except (ValueError, TypeError, UnicodeError):
                        rate_bounds = None
                if rate_bounds is None:
                    return
                # Decode only the numeric rate_limits sub-object, never the
                # surrounding JSONL record.
                try:
                    rate_limits = json.loads(raw[rate_bounds[0]:rate_bounds[1]])
                except (ValueError, TypeError, UnicodeError):
                    return
                snapshot = parse_limit_snapshot(rate_limits, when, "jsonl")
                if snapshot:
                    self.limit_callback(snapshot)
            return

        if kind != "response_item":
            return
        call_id = (_json_member_string(raw, payload, "call_id") or
                   _json_member_string(raw, payload, "id"))
        if (payload_type == "function_call" and
                _json_member_string(raw, payload, "name") == "request_user_input"):
            if call_id:
                self.aggregator.input_call(state.session_id, call_id, when)
            return
        if payload_type in ("function_call_output", "custom_tool_call_output"):
            if call_id:
                self.aggregator.input_output(state.session_id, call_id, when)
            else:
                self.aggregator.progress(state.session_id, when)
            return
        # Progress is derived from envelope metadata only. Prompt, reply,
        # command argument and output values are never decoded.
        self.aggregator.progress(state.session_id, when)

class RateLimitWorker(threading.Thread):
    def __init__(self, callback: Callable[[LimitSnapshot], None], verbose: bool,
                 codex_binary: Optional[str] = None) -> None:
        super().__init__(name="codex-rate-limits", daemon=True)
        self.callback = callback
        self.verbose = verbose
        self.codex_binary = codex_binary
        self.stop_event = threading.Event()
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 10
        self.stdout_buffer = bytearray()
        self.stdout_queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self.stdout_reader: Optional[threading.Thread] = None
        self.stdout_source: Any = None

    @staticmethod
    def codex_binaries(explicit: Optional[str] = None) -> List[str]:
        candidates: List[str] = []
        configured = explicit or os.environ.get("CODEX_BINARY")
        if configured:
            candidates.append(str(Path(configured).expanduser()))
        if sys.platform == "darwin" and DESKTOP_CODEX.is_file() and os.access(DESKTOP_CODEX, os.X_OK):
            candidates.append(str(DESKTOP_CODEX))
        if IS_WINDOWS:
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                standalone = (Path(local_app_data) / "Programs" / "OpenAI" /
                              "Codex" / "bin" / "codex.exe")
                if standalone.is_file():
                    candidates.append(str(standalone))
        path_binary = shutil.which("codex")
        if path_binary:
            candidates.append(path_binary)
        unique: List[str] = []
        seen: Set[str] = set()
        for candidate in candidates:
            resolved = os.path.normcase(os.path.realpath(candidate))
            if resolved not in seen:
                seen.add(resolved)
                unique.append(candidate)
        return unique

    @staticmethod
    def app_server_command(binary: str, windows: Optional[bool] = None) -> List[str]:
        use_windows = IS_WINDOWS if windows is None else windows
        if use_windows and Path(binary).suffix.lower() in (".cmd", ".bat"):
            command_processor = os.environ.get("COMSPEC") or "cmd.exe"
            command_line = subprocess.list2cmdline([binary, "app-server"])
            return [command_processor, "/d", "/s", "/c", command_line]
        return [binary, "app-server"]

    def stop(self) -> None:
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def _send(self, message: Dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        encoded = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        self.process.stdin.write(encoded)
        self.process.stdin.flush()

    def _pump_stdout(self, source: Any,
                     output_queue: "queue.Queue[Optional[bytes]]") -> None:
        try:
            while True:
                line = source.readline()
                if not line:
                    break
                output_queue.put(line)
        except (OSError, ValueError):
            pass
        finally:
            output_queue.put(None)

    def _ensure_stdout_reader(self) -> None:
        assert self.process and self.process.stdout
        source = self.process.stdout
        if self.stdout_source is source and self.stdout_reader is not None:
            return
        self.stdout_source = source
        self.stdout_queue = queue.Queue()
        self.stdout_reader = threading.Thread(
            target=self._pump_stdout, args=(source, self.stdout_queue),
            name="codex-rate-limits-stdout", daemon=True)
        self.stdout_reader.start()

    def _read_until(self, request_id: int, timeout: float) -> Optional[Dict[str, Any]]:
        assert self.process and self.process.stdout
        self._ensure_stdout_reader()
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            if b"\n" in self.stdout_buffer:
                raw, _, remainder = self.stdout_buffer.partition(b"\n")
                self.stdout_buffer = bytearray(remainder)
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("method") == "account/rateLimits/updated":
                    params = message.get("params", {})
                    snapshot = parse_limit_snapshot(
                        params.get("rateLimits"), time.time(), "app-server")
                    if snapshot:
                        self.callback(snapshot)
                if message.get("id") == request_id:
                    return message
                continue

            remaining = max(0.0, deadline - time.monotonic())
            try:
                chunk = self.stdout_queue.get(timeout=min(1.0, remaining))
            except queue.Empty:
                continue
            if chunk is None:
                return None
            self.stdout_buffer.extend(chunk)
        return None

    def _session(self, binary: str) -> None:
        self.process = subprocess.Popen(
            self.app_server_command(binary), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0)
        self.stdout_buffer.clear()
        self.stdout_source = None
        self.stdout_reader = None
        self.stdout_queue = queue.Queue()
        self._send({
            "method": "initialize", "id": 1,
            "params": {"clientInfo": {"name": "state_notifier", "title": "State Notifier", "version": "1.0.0"}},
        })
        initialized = self._read_until(1, 15.0)
        if initialized is None or "error" in initialized:
            raise RuntimeError("Codex app-server did not initialize")
        self._send({"method": "initialized", "params": {}})

        while not self.stop_event.is_set():
            self.request_id += 1
            request_id = self.request_id
            self._send({"method": "account/rateLimits/read", "id": request_id, "params": None})
            response = self._read_until(request_id, 20.0)
            if response is None:
                raise RuntimeError("Codex rate-limit request timed out")
            if "error" in response or not isinstance(response.get("result"), dict):
                raise RuntimeError("Codex rate-limit request failed")
            result = response["result"]
            rate_limits = result.get("rateLimitsByLimitId")
            selected = None
            if isinstance(rate_limits, dict):
                selected = rate_limits.get("codex")
            if not isinstance(selected, dict):
                selected = result.get("rateLimits")
            snapshot = parse_limit_snapshot(selected, time.time(), "app-server")
            if snapshot is None:
                raise RuntimeError("Codex returned no usable rate limits")
            self.callback(snapshot)
            self.stop_event.wait(60.0)

    def run(self) -> None:
        binaries = self.codex_binaries(self.codex_binary)
        if not binaries:
            log("Codex binary not found; using JSONL rate-limit fallback", self.verbose)
            return
        binary_index = 0
        while not self.stop_event.is_set():
            binary = binaries[binary_index]
            try:
                log("rate-limit reader using {}".format(binary), self.verbose)
                self._session(binary)
            except (OSError, RuntimeError, BrokenPipeError) as exc:
                log("rate-limit reader failed on {}: {}".format(binary, exc), self.verbose)
            finally:
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                self.process = None
                self.stdout_buffer.clear()
                self.stdout_source = None
                self.stdout_reader = None
                self.stdout_queue = queue.Queue()
            binary_index = (binary_index + 1) % len(binaries)
            self.stop_event.wait(2.0 if len(binaries) > 1 else 5.0)


ICON_COLUMNS = {
    "RUNNING": (0x08, 0x1C, 0x3E, 0x1C, 0x08),
    "APPROVAL": (0x3E, 0x3E, 0x00, 0x3E, 0x3E),
    "INPUT": (0x3E, 0x3E, 0x00, 0x3E, 0x3E),
    "DONE": (0x3E, 0x3E, 0x3E, 0x3E, 0x3E),
    "ERROR": (0x22, 0x14, 0x08, 0x14, 0x22),
    "IDLE": (0, 0, 0, 0, 0),
}


def format_limit(window: LimitWindow) -> str:
    if window.minutes >= 1440:
        label = "{}D".format(window.minutes // 1440)
    elif window.minutes >= 60:
        label = "{}H".format(window.minutes // 60)
    else:
        label = "{}M".format(window.minutes)
    percent = "{}%".format(window.remaining)
    normal = "{:<2} {:>3}".format(label, percent)
    if len(normal) <= 7:
        return normal
    return (label[:7 - len(percent)] + percent.rjust(len(percent)))[:7]


class CodexDisplayRenderer:
    def __init__(self, text_renderer: TextRenderer, scroll_speed: float = 10.0) -> None:
        self.text_renderer = text_renderer
        self.scroll_speed = scroll_speed
        self.key: Optional[Tuple[str, str]] = None
        self.display_key: Optional[Tuple[str, str]] = None
        self.started = 0.0
        self.bitmap = Bitmap7(())

    def _window(self, text: str, monotonic_now: float) -> Bitmap7:
        key = (text, str(self.text_renderer.font_path))
        if key != self.key:
            self.key = key
            self.started = monotonic_now
            self.bitmap = self.text_renderer.render(text)
        if self.bitmap.width <= 35:
            return self.bitmap.window(0, 35)
        travel = self.bitmap.width + 15
        move_seconds = travel / self.scroll_speed
        cycle = 0.75 + move_seconds + 0.75
        elapsed = (monotonic_now - self.started) % cycle
        if elapsed < 0.75:
            offset = travel
        elif elapsed < 0.75 + move_seconds:
            offset = travel - int((elapsed - 0.75) * self.scroll_speed)
        else:
            offset = 0
        repeated = Bitmap7(self.bitmap.columns + (0,) * 15 + self.bitmap.columns)
        return repeated.window(offset, 35)

    def render(self, selection: Tuple[str, Optional[str], str],
               limits: Tuple[Optional[LimitWindow], Optional[LimitWindow]],
               wall_now: float, monotonic_now: float) -> Frame40x7:
        state, _session_id, summary = selection
        if state == "IDLE":
            available = [window for window in limits if window is not None and
                         (window.resets_at is None or wall_now < window.resets_at)]
            if not available:
                text = "LIMIT--"
            else:
                selected = available[int(wall_now / 3) % len(available)]
                text = format_limit(selected)
            icon = ICON_COLUMNS["IDLE"]
        else:
            prefix = "APPROVE " if state == "APPROVAL" else "INPUT " if state == "INPUT" else ""
            text = prefix + (summary or state)
            blink = state in ("APPROVAL", "INPUT", "DONE", "ERROR")
            icon = ((0,) * 5 if blink and int(monotonic_now / 0.5) % 2 else
                    ICON_COLUMNS[state])
        display_key = (state, text)
        if display_key != self.display_key:
            self.display_key = display_key
            self.key = None
        return Frame40x7(icon + self._window(text, monotonic_now).columns)


class DemoDisplayRenderer:
    FONT_CARDS = ("ABCDEFGH", "IJKLMNOP", "QRSTUVWX", "YZ012345", "6789!?+-")

    def __init__(self, renderer: CodexDisplayRenderer) -> None:
        self.renderer = renderer

    def render(self, elapsed: float, wall_now: float) -> Frame40x7:
        phase = elapsed % 50.0
        if phase < 4:
            tick = int(elapsed * 30)
            columns = tuple(0x55 if (index + tick) % 2 == 0 else 0x2A
                            for index in range(40))
            return Frame40x7(columns)
        if phase < 19:
            card = self.FONT_CARDS[min(4, int((phase - 4) / 3))]
            return Frame40x7(self.renderer.text_renderer.render(card).columns)
        if phase < 25:
            return self.renderer.render(("RUNNING", "demo", "SN2 30 FPS ANIMATION"),
                                        (None, None), wall_now, elapsed)
        if phase < 30:
            return self.renderer.render(("APPROVAL", "demo", "DEMO TASK"),
                                        (None, None), wall_now, elapsed)
        if phase < 35:
            return self.renderer.render(("INPUT", "demo", "DEMO TASK"),
                                        (None, None), wall_now, elapsed)
        if phase < 40:
            return self.renderer.render(("DONE", "demo", "DEMO TASK"),
                                        (None, None), wall_now, elapsed)
        if phase < 45:
            return self.renderer.render(("ERROR", "demo", "DEMO TASK"),
                                        (None, None), wall_now, elapsed)
        limits = (LimitWindow(10080, 91), LimitWindow(300, 82))
        return self.renderer.render(("IDLE", None, ""), limits, wall_now, elapsed)


class Bridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.verbose = args.verbose
        self.mode = "demo" if getattr(args, "demo", False) else getattr(args, "mode", "codex")
        self.aggregator = StateAggregator()
        self.limit_lock = threading.Lock()
        self.live_limits: Optional[LimitSnapshot] = None
        self.fallback_limits: Optional[LimitSnapshot] = None
        self.codex_home = Path(args.codex_home).expanduser()
        self.tailer = RolloutTailer(self.codex_home, self.aggregator, self.update_limits)
        self.rate_worker = RateLimitWorker(
            self.update_limits, self.verbose, getattr(args, "codex_binary", None))
        self.client = VfdClient(args.port, args.serial_number, self.verbose)
        self.text_renderer = TextRenderer(getattr(args, "font", None))
        self.renderer = CodexDisplayRenderer(self.text_renderer)
        self.demo_renderer = DemoDisplayRenderer(self.renderer)
        self.stop_event = threading.Event()
        self.hook_receiver = HookReceiver(
            mode=getattr(args, "ipc_mode", "auto"),
            socket_path=getattr(args, "socket_path", DEFAULT_SOCKET),
            endpoint_path=getattr(args, "endpoint_path", DEFAULT_ENDPOINT))
        self.hooks_open = False

    def update_limits(self, snapshot: LimitSnapshot) -> None:
        with self.limit_lock:
            if snapshot.source == "app-server":
                if self.live_limits is None or snapshot.updated_at >= self.live_limits.updated_at:
                    self.live_limits = snapshot
            elif (self.fallback_limits is None or
                  snapshot.updated_at >= self.fallback_limits.updated_at):
                self.fallback_limits = snapshot

    def _open_hook_socket(self) -> None:
        self.hook_receiver.open()
        self.hooks_open = True

    def _close_hook_socket(self) -> None:
        if self.hooks_open:
            self.hook_receiver.close()
            self.hooks_open = False

    def _read_hooks(self, now: float) -> None:
        for payload in self.hook_receiver.receive():
            self.aggregator.hook(payload, now)
            log("hook event {}".format(payload.get("event")), self.verbose)

    def _limits(self, now: float) -> Tuple[Optional[LimitWindow], Optional[LimitWindow]]:
        with self.limit_lock:
            live = self.live_limits
            fallback = self.fallback_limits
        # A successful app-server read remains authoritative across two normal
        # 60-second refresh intervals. JSONL is used only when live reads fail.
        snapshot = live if live is not None and now - live.updated_at <= 180.0 else fallback
        if snapshot is None or now - snapshot.updated_at > 600.0:
            return None, None
        def current(window: Optional[LimitWindow]) -> Optional[LimitWindow]:
            return None if window is None or (window.resets_at and now >= window.resets_at) else window
        return current(snapshot.primary), current(snapshot.secondary)

    def run(self) -> int:
        if serial is None:
            print("pyserial is required; use host/run_bridge.sh or install pyserial", file=sys.stderr)
            return 2
        if self.mode == "codex":
            self._open_hook_socket()
            self.rate_worker.start()
        self.client.open(wait=False)
        started = time.monotonic()
        interval = 1.0 / getattr(self.args, "fps", 30.0)
        deadline = started
        last_frame: Optional[Frame40x7] = None
        log("bridge started in {} mode; press Ctrl-C to stop".format(self.mode), True)
        try:
            while not self.stop_event.is_set():
                now = time.time()
                monotonic_now = time.monotonic()
                if self.mode == "codex":
                    self._read_hooks(now)
                    self.tailer.poll(now)
                    frame = self.renderer.render(
                        self.aggregator.selected(now), self._limits(now), now, monotonic_now)
                else:
                    frame = self.demo_renderer.render(monotonic_now - started, now)
                if frame != last_frame:
                    self.client.show(frame)
                    last_frame = frame
                deadline += interval
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
                elif remaining < -interval:
                    deadline = time.monotonic()
        except KeyboardInterrupt:
            pass
        finally:
            self.rate_worker.stop()
            if self.rate_worker.is_alive():
                self.rate_worker.join(timeout=3)
            self.client.close()
            self._close_hook_socket()
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="explicit serial device, for example COM3 or /dev/cu.*")
    parser.add_argument("--serial-number", help="Espressif USB serial number")
    parser.add_argument("--verbose", action="store_true", help="log hooks and SN2 connection events")
    parser.add_argument("--mode", choices=("codex", "demo"), default="codex",
                        help="display source (default: codex)")
    parser.add_argument("--demo", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fps", type=float, default=30.0,
                        help="render rate from 1 to 60 FPS (default: 30)")
    parser.add_argument("--font", help="deprecated; the built-in ASCII 5x7 font is always used")
    parser.add_argument("--codex-home", default=str(default_codex_home()),
                        help="Codex state root (default: CODEX_HOME or ~/.codex)")
    parser.add_argument("--codex-binary",
                        help="explicit codex executable for account/rateLimits/read")
    parser.add_argument("--socket-path", default=DEFAULT_SOCKET, help=argparse.SUPPRESS)
    parser.add_argument("--endpoint-path", default=DEFAULT_ENDPOINT, help=argparse.SUPPRESS)
    parser.add_argument("--ipc-mode", choices=("auto", "unix", "udp"),
                        default="auto", help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.fps <= 60:
        print("state-notifier: --fps must be in 1..60", file=sys.stderr)
        return 2
    bridge = Bridge(args)

    def stop_handler(_signum: int, _frame: Any) -> None:
        bridge.stop_event.set()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        return bridge.run()
    except RuntimeError as exc:
        print("state-notifier: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
