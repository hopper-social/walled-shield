"""Per-user private locations shared by the daemon, proxies, launcher and setup wizard."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


def state_dir() -> Path:
    """Private state directory (mode 0700). Matches the historical layout of earlier releases."""
    configured = os.environ.get("MCP_GATE_STATE_DIR")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/MCPApprovalGate"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MCPApprovalGate"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "mcp-approval-gate"


def socket_path() -> Path:
    configured = os.environ.get("MCP_GATE_SOCKET")
    return Path(configured) if configured else state_dir() / "approvals.sock"


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` as 0700 or refuse to use one that others can read or that is a symlink."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{path} must be a real directory, not a symlink")
    if os.name != "nt":
        if info.st_uid != os.getuid():
            raise OSError(f"{path} must be owned by the current user")
        if info.st_mode & 0o077:
            raise OSError(f"{path} must be private (chmod 700)")
    return path


def write_private_file(path: Path, data: str) -> None:
    """Atomically write a 0600 file inside an already-private directory."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
