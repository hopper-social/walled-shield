"""Shared background approval service: one Telegram bot poller for every guarded MCP server.

Why a daemon: Telegram allows exactly one ``getUpdates`` consumer per bot. Claude Desktop
starts every configured MCP server at once, so if each wrapped server polled the bot itself,
the second one would break the first. Instead each proxy asks this daemon over a private
Unix socket and the daemon owns the bot.

Wire protocol (one JSON object per line, request then response, 1 MiB max):

    → {"op": "approve", "server": "...", "method": "tools/call", "params": {...}}
    ← {"decision": "allow" | "deny", "reason": "allow|always|auto|deny|expired|..."}

    → {"op": "status"}                 ← {"ok": true, "healthy": ..., "session_expires_at": ...}
    → {"op": "renew", "code": "..."}   ← {"ok": true|false}   (waits for /pair <code> from the paired chat)
    → {"op": "ask", "text": "<html>", "timeout": 120}   ← {"decision": "allow" | "deny"}   (Walled Shield)
    → {"op": "notify", "text": "<html>"}                 ← {"ok": true|false}             (Walled Shield)

A client that closes its socket while waiting cancels the prompt (fail closed: no late Allow).
The socket lives in a 0700 directory, so only the same OS user can reach it.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path

from .request_format import approval_card, outcome_line, tool_name
from .telegram_approver import ALLOW, ALWAYS, DENY, TelegramApprover
from .gate import Request

LOG = logging.getLogger(__name__)
MAX_LINE = 1024 * 1024
MAX_CONNECTIONS = 128   # open client sockets; extra connections are closed (= denied) at once
SESSION_NOTICE_INTERVAL = 600


@dataclass
class DaemonConfig:
    timeout: float = 120.0
    show_inputs: bool = True
    deny_truncated: bool = False
    max_pending: int = 32


def read_line(conn: socket.socket, deadline: float | None = None) -> bytes:
    buffer = bytearray()
    while b"\n" not in buffer:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("request not received in time")
            conn.settimeout(remaining)
        chunk = conn.recv(65536)
        if not chunk:
            raise ConnectionError("client closed")
        buffer += chunk
        if len(buffer) > MAX_LINE:
            raise ValueError("request too large")
    line, _, _rest = bytes(buffer).partition(b"\n")
    return line


class ApprovalDaemon:
    def __init__(self, approver: TelegramApprover, config: DaemonConfig | None = None):
        self.approver = approver
        self.config = config or DaemonConfig()
        self.rules: set[tuple[str, str]] = set()
        self.rules_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(self.config.max_pending)
        # (phase "asking"|"answered", server, method, allowed, reason): lets the host app react
        self.on_approval: Callable[[str, str, str, bool, str], None] | None = None
        self.renewals: dict[str, threading.Event] = {}
        self.renew_lock = threading.Lock()
        self.last_session_notice = 0.0
        self.counts = {"allowed": 0, "blocked": 0}
        # Optional sensitive-data check (Walled Shield rules). Returns [(rule name, masked sample,
        # action)]; any hit disables "always allow" for that call and hides its inputs on Telegram.
        self.inspect: Callable[[str, str, object], list[tuple[str, str, str]]] | None = None
        self.extra_commands: Callable[[str], str | None] | None = None
        approver.on_command = self.command
        approver.on_private_text = self._renewal_text
        approver.on_pairout = self.on_pairout

    # ----- decisions -------------------------------------------------------------------
    @staticmethod
    def rule_key(server: str, method: str, params: object) -> tuple[str, str]:
        return server, tool_name(method, params) or method

    def approve(self, server: str, method: str, params: object,
                cancelled: threading.Event | None = None) -> tuple[bool, str]:
        key = self.rule_key(server, method, params)
        hits: list[tuple[str, str, str]] = []
        if self.inspect is not None:
            try:
                hits = self.inspect(server, method, params)
            except Exception:
                LOG.warning("Sensitive-data check failed; asking without auto-allow")
                hits = [("check unavailable", "", "ask")]
        if any(action == "block" for _, _, action in hits):
            self.counts["blocked"] += 1
            names = ", ".join(sorted({name for name, _, _ in hits}))
            self.approver.send_notice(f"⛔ Blocked automatically: Claude's {tool_name(method, params) or method} "
                                      f"on {server[:80]} matched a block rule ({names[:300]}).")
            return False, "blocked by rule"
        with self.rules_lock:
            if key in self.rules and not hits:
                self.counts["allowed"] += 1
                return True, "auto"
        if not self.approver._authorized():
            self._session_notice()
            self.counts["blocked"] += 1
            return False, "session ended"
        if not self.slots.acquire(blocking=False):
            self.counts["blocked"] += 1
            return False, "busy"
        try:
            text, truncated = approval_card(server, method, params, self.config.timeout,
                                            self.config.show_inputs and not hits)
            if hits:
                import html as _html
                warning = "\n".join(f"• {_html.escape(n)}" + (f" <code>{_html.escape(m)}</code>" if m else "")
                                     for n, m, _ in hits[:8])
                text = text.replace("\n", "\n⚠️ <b>Sensitive data in this request</b> (inputs hidden):\n"
                                    + warning + "\n", 1)
            if truncated and self.config.deny_truncated:
                self.approver.send_notice(text.split("\n\n⏳")[0] +
                                          "\n\n❌ <b>Blocked automatically</b>: request too large "
                                          "to preview (--deny-truncated).", parse_mode="HTML")
                self.counts["blocked"] += 1
                return False, "too large"
            label = f"♾ Always allow {key[1][:40]}"
            choice = self.approver.ask(text, cancelled, allow_always=not hits, always_label=label,
                                       parse_mode="HTML", timeout=self.config.timeout,
                                       outcome_text=outcome_line)
        finally:
            self.slots.release()
        if choice == ALWAYS:
            with self.rules_lock:
                self.rules.add(key)
        allowed = choice in (ALLOW, ALWAYS)
        self.counts["allowed" if allowed else "blocked"] += 1
        try:
            self.approver.notify_decision(Request(server, method, params, None), allowed)
        except Exception:
            LOG.warning("Decision alert unavailable")
        return allowed, choice if allowed else DENY

    def _session_notice(self) -> None:
        now = time.monotonic()
        if now - self.last_session_notice < SESSION_NOTICE_INTERVAL and self.last_session_notice:
            return
        self.last_session_notice = now
        self.approver.send_notice(
            "🔒 Claude asked for an MCP action, but approvals are OFF (session expired or /pairout).\n"
            "The request was blocked.\n\nTo turn approvals on, run on the computer:\n"
            "mcp-approval-gate renew")

    # ----- Telegram commands -----------------------------------------------------------
    def command(self, text: str) -> str | None:
        name = text.split()[0].split("@")[0].lower()
        if self.extra_commands is not None:          # Walled Shield adds /lock, /vault, /guard, /help
            extra = self.extra_commands(text)
            if extra is not None:
                return extra
        if name in ("/start", "/help"):
            return ("MCP Approval Gate\n\n"
                    "/status – gate health and session\n"
                    "/rules – tools you set to always allow\n"
                    "/clear – remove all always-allow rules\n"
                    "/pairout – turn approvals off now (everything is blocked)")
        if name == "/status":
            expires = self.approver.session.expires_at() if self.approver.session else None
            session = ("no session required" if self.approver.session is None else
                       f"active, {int((expires - time.time()) / 60)} min left" if expires else "OFF")
            with self.rules_lock:
                rule_count = len(self.rules)
            return (f"🟢 Gate running\nApprovals: {session}\nWaiting now: {len(self.approver.pending)}\n"
                    f"Always-allow rules: {rule_count}\nSince start: {self.counts['allowed']} allowed, "
                    f"{self.counts['blocked']} blocked")
        if name == "/rules":
            with self.rules_lock:
                rules = sorted(self.rules)
            if not rules:
                return "No always-allow rules. Every action asks you."
            return "Always allowed (until /clear or restart):\n" + "\n".join(
                f"• {tool} on {server}" for server, tool in rules)
        if name == "/clear":
            with self.rules_lock:
                count = len(self.rules)
                self.rules.clear()
            return f"Cleared {count} rule(s). Every action asks you again."
        return None

    def on_pairout(self) -> None:
        with self.rules_lock:
            self.rules.clear()

    # ----- session renewal without stopping the bot -----------------------------------
    def _renewal_text(self, text: str) -> str | None:
        parts = text.split()
        # "/pair CODE" typed by hand, or "/start CODE" from a t.me/<bot>?start=CODE deep link
        if len(parts) != 2 or parts[0] not in ("/pair", "/start"):
            return None
        with self.renew_lock:
            event = self.renewals.get(parts[1])
        if event is None or self.approver.session is None:
            return None
        self.approver.session.activate()
        self.approver.session_failed = False
        event.set()
        return "🔓 Approvals ON for 24 hours. Claude's MCP actions will be sent here."

    def renew(self, code: str, timeout: float = 120) -> bool:
        if len(code) < 12 or len(code) > 64 or not code.replace("-", "").replace("_", "").isalnum():
            return False
        event = threading.Event()
        with self.renew_lock:
            self.renewals[code] = event
        try:
            return event.wait(timeout)
        finally:
            with self.renew_lock:
                self.renewals.pop(code, None)

    def status(self) -> dict[str, object]:
        expires = self.approver.session.expires_at() if self.approver.session else None
        return {"ok": True, "healthy": self.approver.healthy,
                "session_active": self.approver._authorized(), "session_expires_at": expires,
                "pending": len(self.approver.pending), "rules": len(self.rules)}

    # ----- socket server ---------------------------------------------------------------
    def handle_connection(self, conn: socket.socket) -> None:
        try:
            message = json.loads(read_line(conn, time.monotonic() + 10))
            if not isinstance(message, dict):
                raise ValueError("object expected")
            self.handle_message(conn, message)
        except Exception as exc:  # malformed client: answer deny if we still can
            LOG.debug("approval client error: %s", type(exc).__name__)
            try:
                self._send(conn, {"decision": "deny", "reason": "invalid request"})
            except OSError:
                pass

    def handle_message(self, conn: socket.socket, message: dict[str, Any]) -> None:
        op = message.get("op")
        if op == "status":
            return self._send(conn, self.status())
        if op == "renew":
            code = message.get("code")
            return self._send(conn, {"ok": isinstance(code, str) and self.renew(code)})
        if op == "notify":
            text = message.get("text")
            ok = isinstance(text, str) and self.approver.send_notice(text[:4096], parse_mode="HTML")
            return self._send(conn, {"ok": bool(ok)})
        if op == "ask":
            text, timeout = message.get("text"), message.get("timeout", self.config.timeout)
            if not isinstance(text, str) or not isinstance(timeout, (int, float)):
                return self._send(conn, {"decision": "deny", "reason": "invalid request"})
            seconds = max(5.0, min(600.0, float(timeout)))
            return self._watch(conn, lambda cancelled: self._ask_text(text, cancelled, seconds))
        if op != "approve":
            return self._send(conn, {"decision": "deny", "reason": "unknown op"})
        server, method = message.get("server"), message.get("method")
        if not (isinstance(server, str) and isinstance(method, str) and server and method):
            return self._send(conn, {"decision": "deny", "reason": "invalid request"})
        self._approve_watching(conn, server[:256], method[:256], message.get("params", {}))

    def _ask_text(self, text: str, cancelled: threading.Event, timeout: float) -> tuple[bool, str]:
        if not self.approver._authorized():
            self._session_notice()
            return False, "session ended"
        if not self.slots.acquire(blocking=False):
            return False, "busy"
        try:
            choice = self.approver.ask(text[:3800], cancelled, parse_mode="HTML", timeout=timeout,
                                       outcome_text=outcome_line)
        finally:
            self.slots.release()
        return choice == ALLOW, choice

    def _approve_watching(self, conn: socket.socket, server: str, method: str,
                          params: object) -> None:
        def work(cancelled: threading.Event) -> tuple[bool, str]:
            if self.on_approval is not None:
                self.on_approval("asking", server, method, False, "")
            allowed, reason = self.approve(server, method, params, cancelled)
            if self.on_approval is not None:
                self.on_approval("answered", server, method, allowed, reason)
            return allowed, reason
        self._watch(conn, work)

    def _watch(self, conn: socket.socket, work: Callable[[threading.Event], tuple[bool, str]]) -> None:
        """Run ``work`` while watching the client; a closed socket withdraws the prompt."""
        cancelled = threading.Event()
        result: list[tuple[bool, str]] = []
        worker = threading.Thread(target=lambda: result.append(work(cancelled)), daemon=True)
        worker.start()
        conn.setblocking(False)
        while worker.is_alive():
            worker.join(0.1)
            readable, _, _ = select.select([conn], [], [], 0)
            if readable:
                try:
                    if conn.recv(1) == b"":
                        cancelled.set()  # client gone: the prompt is withdrawn, never forwarded
                except BlockingIOError:
                    pass
                except OSError:
                    cancelled.set()
        if cancelled.is_set() or not result:
            return
        allowed, reason = result[0]
        conn.setblocking(True)
        self._send(conn, {"decision": "allow" if allowed else "deny", "reason": reason})

    @staticmethod
    def _send(conn: socket.socket, payload: dict[str, object]) -> None:
        conn.setblocking(True)
        conn.sendall((json.dumps(payload) + "\n").encode())


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Threaded Unix-socket server with a hard cap on concurrent connections."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: str, handler: type[socketserver.BaseRequestHandler],
                 max_connections: int = MAX_CONNECTIONS) -> None:
        self.connection_slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)  # client sees EOF → DaemonApprover denies
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


def serve(daemon: ApprovalDaemon, path: Path, stop: threading.Event,
          max_connections: int = MAX_CONNECTIONS) -> None:
    """Listen on ``path`` (in a private directory) until ``stop`` is set."""
    if path.exists() or path.is_symlink():
        try:  # refuse to hijack a live daemon; reclaim a stale socket
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(1)
            probe.connect(str(path))
            probe.close()
            raise OSError("another approval daemon is already listening")
        except (ConnectionRefusedError, FileNotFoundError):
            path.unlink(missing_ok=True)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            daemon.handle_connection(self.request)

    old_umask = os.umask(0o177)
    try:
        server = _Server(str(path), Handler, max_connections)
    finally:
        os.umask(old_umask)
    os.chmod(path, 0o600)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    thread.start()
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        path.unlink(missing_ok=True)


def new_pair_code() -> str:
    return secrets.token_urlsafe(12)
