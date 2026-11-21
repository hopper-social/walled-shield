"""Client side of the approval daemon, used by the stdio and HTTP proxies.

Every failure — daemon missing, socket error, malformed reply, timeout — is a denial.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .gate import Request
from .paths import ensure_private_dir, socket_path, state_dir

LOG = logging.getLogger(__name__)


def launcher_command() -> list[str]:
    """How to start the daemon with credentials loaded from the private state directory."""
    return [sys.executable, "-m", "walled", "service"]   # the unified Walled Shield service


class DaemonUnavailable(Exception):
    pass


def _connect(path: Path, timeout: float) -> socket.socket:
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        conn.connect(str(path))
    except OSError as exc:
        conn.close()
        raise DaemonUnavailable("approval daemon is not running") from exc
    return conn


def call(payload: dict[str, object], path: Path | None = None, timeout: float = 10) -> dict[str, Any]:
    path = path or socket_path()
    conn = _connect(path, timeout)
    try:
        conn.sendall((json.dumps(payload) + "\n").encode())
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                raise DaemonUnavailable("approval daemon closed the connection")
            buffer += chunk
            if len(buffer) > 65536:
                raise DaemonUnavailable("approval daemon reply too large")
        reply = json.loads(buffer.split(b"\n", 1)[0])
        if not isinstance(reply, dict):
            raise DaemonUnavailable("invalid daemon reply")
        return reply
    except (OSError, ValueError) as exc:
        raise DaemonUnavailable("approval daemon unavailable") from exc
    finally:
        conn.close()


def start_daemon_process(wait: float = 20) -> None:
    """Spawn the daemon via the credential launcher, detached from this process."""
    directory = ensure_private_dir(state_dir())
    log_fd = os.open(directory / "daemon.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        subprocess.Popen(launcher_command(), stdin=subprocess.DEVNULL,
                         stdout=log_fd, stderr=log_fd, start_new_session=True, close_fds=True)
    finally:
        os.close(log_fd)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            if call({"op": "status"}, timeout=2).get("ok"):
                return
        except DaemonUnavailable:
            time.sleep(0.3)
    raise DaemonUnavailable("approval daemon did not start; see daemon.log in the state directory")


def ensure_daemon(autostart: bool, path: Path | None = None) -> dict[str, Any]:
    try:
        return call({"op": "status"}, path, timeout=3)
    except DaemonUnavailable:
        if not autostart:
            raise
    start_daemon_process()
    return call({"op": "status"}, path, timeout=3)


class DaemonApprover:
    """Drop-in ``decide(request) -> bool`` that asks the shared daemon."""

    def __init__(self, path: Path | None = None, timeout: float = 300):
        self.path = path or socket_path()
        self.timeout = timeout  # hard ceiling; the daemon's own prompt timeout is shorter

    def decide(self, request: Request) -> bool:
        if request.cancelled is not None and request.cancelled.is_set():
            return False
        try:
            conn = _connect(self.path, 5)
        except DaemonUnavailable:
            LOG.warning("Approval daemon unavailable; request denied")
            return False
        try:
            conn.sendall((json.dumps({"op": "approve", "server": request.server,
                                      "method": request.method, "params": request.parameters},
                                     default=str) + "\n").encode())
            conn.settimeout(0.2)
            deadline = time.monotonic() + self.timeout
            buffer = b""
            while b"\n" not in buffer:
                if request.cancelled is not None and request.cancelled.is_set():
                    return False  # closing the socket withdraws the Telegram prompt
                if time.monotonic() > deadline:
                    return False
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    return False
                buffer += chunk
                if len(buffer) > 65536:
                    return False
            reply = json.loads(buffer.split(b"\n", 1)[0])
            return isinstance(reply, dict) and reply.get("decision") == "allow"
        except (OSError, ValueError):
            LOG.warning("Approval daemon error; request denied")
            return False
        finally:
            conn.close()
