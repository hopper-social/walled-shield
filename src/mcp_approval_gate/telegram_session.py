"""Private, expiring Telegram approval authorization independent of alert routing."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import sys
from pathlib import Path

SESSION_SECONDS = 24 * 60 * 60


class SessionError(Exception):
    pass


def session_path() -> Path:
    configured = os.environ.get("MCP_TELEGRAM_SESSION_FILE")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/MCPApprovalGate/telegram-session.json"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MCPApprovalGate/telegram-session.json"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "mcp-approval-gate/telegram-session.json"


class ApprovalSession:
    def __init__(self, path: Path, chat_id: int, user_id: int):
        self.path = path
        self.chat_id = chat_id
        self.user_id = user_id

    def _directory(self) -> None:
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SessionError("Telegram session directory must be private")

    def active(self) -> bool:
        return self.expires_at() is not None

    def expires_at(self) -> float | None:
        """Expiry timestamp of a valid, private, matching session; ``None`` when inactive."""
        try:
            self._directory()
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_size > 4096):
                    return None
                with os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
                    data = json.load(stream)
            finally:
                os.close(fd)
            valid = (type(data.get("chat_id")) is int and data["chat_id"] == self.chat_id
                     and type(data.get("user_id")) is int and data["user_id"] == self.user_id
                     and type(data.get("expires_at")) in (int, float)
                     and time.time() < data["expires_at"] <= time.time() + SESSION_SECONDS + 60)
            return float(data["expires_at"]) if valid else None
        except (OSError, ValueError, TypeError, AttributeError, UnicodeError, SessionError):
            return None

    def _write(self, expires_at: float) -> None:
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise SessionError("Cannot create private Telegram session directory") from exc
        self._directory()
        fd, temporary = tempfile.mkstemp(prefix=".telegram-session-", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"chat_id": self.chat_id, "user_id": self.user_id,
                           "expires_at": expires_at}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise SessionError("Cannot update Telegram approval session") from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def activate(self) -> None:
        self._write(time.time() + SESSION_SECONDS)

    def revoke(self) -> None:
        self._write(0)
