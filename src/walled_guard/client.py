"""Talk to the long-running Walled service from the short-lived native host.

The service listens on the same private Unix socket as the MCP approval daemon. If it is
not running, Telegram features are simply unavailable: the desktop dialog still decides,
so the browser guard keeps working on its own (the assignment's required behaviour).
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from typing import Any


def _path() -> str:
    from mcp_approval_gate.paths import socket_path
    return str(socket_path())


def available() -> bool:
    return not sys.platform.startswith("win")


def call(payload: dict[str, Any], timeout: float = 10) -> dict[str, Any] | None:
    """One request/response. None when the service is not reachable."""
    if not available():
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(_path())
            conn.sendall((json.dumps(payload) + "\n").encode())
            return _read(conn)
    except (OSError, ValueError):
        return None


def _read(conn: socket.socket) -> dict[str, Any] | None:
    buffer = b""
    while b"\n" not in buffer:
        chunk = conn.recv(65536)
        if not chunk:
            return None
        buffer += chunk
        if len(buffer) > 1024 * 1024:
            return None
    reply = json.loads(buffer.split(b"\n", 1)[0])
    return reply if isinstance(reply, dict) else None


class PhoneQuestion:
    """An Allow/Block question on Telegram that can be withdrawn (closing the socket)."""

    def __init__(self, text: str, timeout: float):
        self.text, self.timeout = text, timeout
        self._conn: socket.socket | None = None
        self._lock = threading.Lock()
        self._withdrawn = False

    def wait(self) -> tuple[str, str] | None:
        """("allow" | "deny", reason), or None if Telegram is unavailable or it was withdrawn."""
        if not available():
            return None
        try:
            conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with self._lock:
                if self._withdrawn:
                    conn.close()
                    return None
                self._conn = conn
            conn.settimeout(5)
            conn.connect(_path())
            conn.sendall((json.dumps({"op": "ask", "text": self.text, "timeout": self.timeout}) + "\n").encode())
            conn.settimeout(self.timeout + 15)
            reply = _read(conn)
        except (OSError, ValueError):
            return None
        finally:
            self.withdraw()
        if not reply or reply.get("decision") not in ("allow", "deny"):
            return None
        return str(reply["decision"]), str(reply.get("reason", ""))

    def withdraw(self) -> None:
        with self._lock:
            self._withdrawn = True
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
