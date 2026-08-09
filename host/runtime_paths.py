"""Cross-platform per-user paths shared by the bridge and hook client."""

from __future__ import annotations

import os
from pathlib import Path
import getpass
import hashlib
import stat
import tempfile


IS_WINDOWS = os.name == "nt"


def runtime_directory(windows: bool | None = None) -> Path:
    """Return a private-by-construction per-user runtime directory.

    On Windows, LOCALAPPDATA is already scoped to the signed-in user. POSIX
    keeps the existing UID-suffixed directory and verifies its ownership and
    mode in :func:`ensure_runtime_directory`.
    """
    use_windows = IS_WINDOWS if windows is None else windows
    if use_windows:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "CodexStateNotifier"
        identity = os.environ.get("USERNAME") or getpass.getuser()
        suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / "CodexStateNotifier-{}".format(suffix)
    base = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    return base / "codex-sn-{}".format(os.getuid())


def ensure_runtime_directory(windows: bool | None = None) -> Path:
    use_windows = IS_WINDOWS if windows is None else windows
    path = runtime_directory(use_windows)
    try:
        path.mkdir(mode=0o700, parents=True)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("unsafe State Notifier runtime directory: {}".format(path))
    if use_windows:
        # Windows inherits the ACL from the user's LOCALAPPDATA directory;
        # POSIX chmod bits do not express that ACL.
        return path
    if info.st_uid != os.getuid():
        raise RuntimeError("unsafe State Notifier runtime directory: {}".format(path))
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
    return path


def default_socket_path() -> str:
    return str(runtime_directory() / "bridge.sock")


def default_endpoint_path() -> str:
    return str(runtime_directory() / "bridge-endpoint.json")


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"
