"""Private local hook transport for POSIX and native Windows.

POSIX retains the owner-only Unix datagram socket. Native Windows uses a
loopback UDP socket with a fresh random token published under LOCALAPPDATA.
The endpoint file inherits the signed-in user's ACL and is removed when the
bridge exits.
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import tempfile
from typing import Any, Dict, List, Optional

from runtime_paths import (
    IS_WINDOWS,
    default_endpoint_path,
    default_socket_path,
    ensure_runtime_directory,
)


def platform_ipc_mode(windows: Optional[bool] = None) -> str:
    return "udp" if (IS_WINDOWS if windows is None else windows) else "unix"


def _acquire_lock(path: str, windows: bool) -> Any:
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("cannot safely open bridge lock {}".format(path)) from exc
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        if windows:
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                handle.write(b"\0")
            handle.seek(0)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        handle.close()
        raise RuntimeError("another State Notifier bridge is already running") from exc
    return handle


def _atomic_endpoint_write(path: Path, document: Dict[str, Any], windows: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="bridge-endpoint.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not windows:
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class HookReceiver:
    def __init__(self, mode: str = "auto", socket_path: Optional[str] = None,
                 endpoint_path: Optional[str] = None) -> None:
        self.mode = platform_ipc_mode() if mode == "auto" else mode
        if self.mode not in ("unix", "udp"):
            raise ValueError("unsupported hook IPC mode: {}".format(self.mode))
        self.socket_path = socket_path or default_socket_path()
        self.endpoint_path = endpoint_path or default_endpoint_path()
        self.socket: Optional[socket.socket] = None
        self.lock_handle: Any = None
        self.token: Optional[str] = None
        self.cleanup_owned = False

    def open(self) -> None:
        windows_transport = self.mode == "udp"
        ensure_runtime_directory()
        lock_target = (self.endpoint_path if windows_transport else self.socket_path) + ".lock"
        self.lock_handle = _acquire_lock(lock_target, IS_WINDOWS)
        try:
            if windows_transport:
                listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket = listener
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind(("127.0.0.1", 0))
                self.token = secrets.token_urlsafe(32)
                host, port = listener.getsockname()
                _atomic_endpoint_write(Path(self.endpoint_path), {
                    "version": 1,
                    "host": host,
                    "port": port,
                    "token": self.token,
                }, windows=IS_WINDOWS)
                self.cleanup_owned = True
            else:
                try:
                    existing = os.lstat(self.socket_path)
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.getuid():
                        raise RuntimeError("refusing to replace unsafe socket path {}".format(
                            self.socket_path))
                    os.unlink(self.socket_path)
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                self.socket = listener
                listener.bind(self.socket_path)
                os.chmod(self.socket_path, 0o600)
                self.cleanup_owned = True
            listener.setblocking(False)
        except Exception:
            self.close()
            raise

    def receive(self) -> List[Dict[str, Any]]:
        if self.socket is None:
            return []
        messages: List[Dict[str, Any]] = []
        while True:
            try:
                raw = self.socket.recv(4096)
            except BlockingIOError:
                return messages
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError, UnicodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if self.mode == "udp":
                supplied = payload.pop("_token", None)
                if not (isinstance(supplied, str) and self.token and
                        hmac.compare_digest(supplied, self.token)):
                    continue
            messages.append(payload)

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if self.cleanup_owned:
            cleanup = self.endpoint_path if self.mode == "udp" else self.socket_path
            try:
                os.unlink(cleanup)
            except OSError:
                pass
            self.cleanup_owned = False
        if self.lock_handle is not None:
            self.lock_handle.close()
            self.lock_handle = None
        self.token = None


def send_hook_event(event: Dict[str, str], mode: str = "auto",
                    socket_path: Optional[str] = None,
                    endpoint_path: Optional[str] = None) -> None:
    selected = platform_ipc_mode() if mode == "auto" else mode
    if selected == "udp":
        endpoint = Path(endpoint_path or default_endpoint_path())
        with endpoint.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
        host = document.get("host")
        port = document.get("port")
        token = document.get("token")
        if (document.get("version") != 1 or host != "127.0.0.1" or
                not isinstance(port, int) or not 1 <= port <= 65535 or
                not isinstance(token, str) or len(token) < 32):
            raise ValueError("invalid State Notifier hook endpoint")
        payload = dict(event)
        payload["_token"] = token
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(0.2)
            client.sendto(encoded, (host, port))
        return
    if selected != "unix":
        raise ValueError("unsupported hook IPC mode: {}".format(selected))
    encoded = json.dumps(event, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
        client.settimeout(0.2)
        client.sendto(encoded, socket_path or default_socket_path())
