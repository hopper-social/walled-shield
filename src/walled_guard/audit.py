"""Local decision log: when, which site, prompt or file, and what the user chose.

Prompt text, file names and file contents are never written.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

MAX_LINES = 1000


def state_dir() -> Path:
    override = os.environ.get("WALLED_GUARD_HOME")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "WalledGuard"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WalledGuard"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "walled-guard"


def private_dir() -> Path:
    """The state folder, created private (0700). A looser folder we own is tightened."""
    path = state_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        info = path.lstat()
        if path.is_symlink():
            raise OSError(f"{path} must not be a symlink")
        if info.st_uid == os.getuid() and info.st_mode & 0o077:
            os.chmod(path, 0o700)
    return path


def log_path() -> Path:
    return state_dir() / "decisions.jsonl"


def record(site: str, kind: str, decision: str, count: int = 1,
           rules: list[str] | None = None, via: str | None = None) -> None:
    try:
        private_dir()
        path = log_path()
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "site": site, "kind": kind,
                 "decision": decision, "count": count}
        if rules:
            entry["rules"] = rules[:10]          # rule names only, never the matched text
        if via:
            entry["via"] = via
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            path.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass                                    # the log is best effort; never block on it


def recent(limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
