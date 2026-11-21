"""Private, bounded local activity history; never persists request arguments or IDs."""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from pathlib import Path

MAX_EVENTS = 1000


def database_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/MCPApprovalGate/activity.sqlite3"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MCPApprovalGate/activity.sqlite3"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "mcp-approval-gate/activity.sqlite3"


class ActivityStore:
    def __init__(self, path: Path | None = None):
        self.path = path or database_path()
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.is_symlink() or self.path.is_symlink():
            raise OSError("Activity location must not be a symlink")
        if os.name != "nt" and (parent.stat().st_mode & 0o077):
            raise OSError("Activity directory must be private (chmod 700)")
        if self.path.exists() and os.name != "nt" and (self.path.stat().st_mode & 0o077):
            raise OSError("Activity database must be private (chmod 600)")
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
        if os.name != "nt":
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        self.db.execute("CREATE TABLE IF NOT EXISTS activity (seq INTEGER PRIMARY KEY, at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), server TEXT NOT NULL, method TEXT NOT NULL, tool TEXT, outcome TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS notices (channel TEXT NOT NULL, recipient TEXT NOT NULL, version INTEGER NOT NULL, PRIMARY KEY (channel, recipient))")
        self.db.commit()
        import threading
        self.lock = threading.Lock()

    def record(self, server: str, method: str, tool: str | None, outcome: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO activity (server, method, tool, outcome) VALUES (?, ?, ?, ?)",
                            (server[:256], method[:256], tool[:256] if tool else None, outcome))
            self.db.execute("DELETE FROM activity WHERE seq <= (SELECT COALESCE(MAX(seq), 0) - ? FROM activity)", (MAX_EVENTS,))

    def recent(self, limit: int = 30) -> list[tuple[str, str, str, str | None, str]]:
        with self.lock:
            return self.db.execute("SELECT at, outcome, server, tool, method FROM activity ORDER BY seq DESC LIMIT ?",
                                   (max(1, min(limit, 100)),)).fetchall()

    def notice_sent(self, channel: str, recipient: str, version: int) -> bool:
        with self.lock:
            row = self.db.execute("SELECT version FROM notices WHERE channel = ? AND recipient = ?",
                                  (channel, recipient)).fetchone()
            return row is not None and row[0] >= version

    def mark_notice_sent(self, channel: str, recipient: str, version: int) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO notices (channel, recipient, version) VALUES (?, ?, ?) "
                            "ON CONFLICT(channel, recipient) DO UPDATE SET version = max(version, excluded.version)",
                            (channel, recipient, version))

    def close(self) -> None:
        with self.lock:
            self.db.close()
