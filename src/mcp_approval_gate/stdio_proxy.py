"""Transparent newline-delimited JSON-RPC stdio MCP proxy.

Claude Desktop launches this process instead of the real MCP server. It starts the real
server as a child, relays server→client lines untouched, and passes every client→server
message through :class:`~mcp_approval_gate.gate.ApprovalGate`. Approvals run on a bounded
thread pool so one slow decision never blocks other requests; ``notifications/cancelled``
withdraws a pending approval, and a child exit fails every outstanding request.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import IO, Any, Callable, Optional, Protocol

from .gate import ApprovalGate, MAX_BODY, decode_message, error_response, valid_request_id

LOG = logging.getLogger(__name__)


class LineReader(Protocol):
    """Anything with a bounded ``readline`` (``sys.stdin.buffer``, a pipe, a test double)."""

    def readline(self, limit: int, /) -> bytes: ...


def encode(message: object) -> bytes:
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def serve(command: list[str], gate: ApprovalGate, server: str,
          input_stream: LineReader | None = None, output_stream: IO[bytes] | None = None,
          stop: threading.Event | None = None, on_connected: Callable[[], None] | None = None) -> None:
    reader_in: LineReader = input_stream or sys.stdin.buffer
    output_stream = output_stream or sys.stdout.buffer
    child_env = os.environ.copy()
    for name in ("MCP_TELEGRAM_BOT_TOKEN", "MCP_TELEGRAM_CHAT_ID", "MCP_TELEGRAM_USER_ID",
                 "MCP_TELEGRAM_ALERT_CHAT_ID", "MCP_TELEGRAM_SESSION_FILE", "MCP_APPROVAL_TOKEN",
                 "MCP_UPSTREAM_TOKEN"):
        child_env.pop(name, None)
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=None, bufsize=0, env=child_env)
    assert child.stdin is not None and child.stdout is not None  # both are PIPEs
    child_in: IO[bytes] = child.stdin
    child_out: IO[bytes] = child.stdout
    write_child = threading.Lock()
    write_client = threading.Lock()
    done = threading.Event()
    stop = stop or threading.Event()
    outstanding: dict[str, object] = {}     # request key -> original JSON-RPC id
    initializing: set[str] = set()
    connected = False
    alert_worker: threading.Thread | None = None
    pending: dict[str, threading.Event] = {}  # request key -> cancellation flag
    pending_lock = threading.Lock()

    def request_key(message: object) -> str | None:
        if isinstance(message, dict) and "method" in message and valid_request_id(message.get("id")):
            return json.dumps(message["id"])
        return None

    def send_client(message: object) -> None:
        with write_client:
            output_stream.write(encode(message))
            output_stream.flush()

    def handle(line: bytes) -> None:
        try:
            if len(line) > MAX_BODY:
                raise ValueError("request too large")
            message = decode_message(line)
            if not isinstance(message, (dict, list)):
                raise ValueError("invalid JSON-RPC message")
            key = request_key(message)
            with pending_lock:
                cancellation = pending.get(key) if key is not None else None
            forwarded, denied = gate.evaluate(message, server, cancellation)
            if denied is not None:
                if key is not None:
                    with pending_lock:
                        pending.pop(key, None)
                send_client(denied)
            if forwarded is not None:
                with write_child:
                    with pending_lock:
                        cancelled = cancellation is not None and cancellation.is_set()
                        if cancelled and key is not None:
                            pending.pop(key, None)
                        elif key is not None and not done.is_set() and not stop.is_set():
                            outstanding[key] = forwarded["id"]
                        if not cancelled and not done.is_set() and not stop.is_set():
                            try:
                                child_in.write(encode(forwarded))
                                child_in.flush()
                            except OSError:
                                if key is not None:
                                    outstanding.pop(key, None)
                                    pending.pop(key, None)
                                raise
                            if key is not None and forwarded.get("method") == "initialize":
                                initializing.add(key)
                            return
                    if key is not None:
                        send_client(error_response(forwarded["id"],
                                                   "MCP request cancelled" if cancelled else "MCP server unavailable"))
        except (ValueError, UnicodeError, OSError, BrokenPipeError) as exc:
            LOG.warning("MCP forwarding failed: %s", exc)
            try:
                parsed = decode_message(line)
                if isinstance(parsed, dict) and "id" in parsed:
                    send_client(error_response(parsed["id"], "MCP server unavailable or invalid request"))
            except (ValueError, UnicodeError, OSError):
                pass

    def notify_connected() -> None:
        if on_connected is None:
            return
        try:
            on_connected()
        except Exception:
            LOG.warning("Connector-ready alert could not be delivered")

    def read_child() -> None:
        nonlocal connected, alert_worker
        try:
            while line := child_out.readline(MAX_BODY + 2):
                if len(line) > MAX_BODY:
                    LOG.warning("Oversized server response; stopping MCP server")
                    child.terminate()
                    break
                ready = False
                try:
                    reply = json.loads(line)
                    if isinstance(reply, dict) and "method" not in reply and "id" in reply:
                        with write_child:
                            key = json.dumps(reply["id"])
                            if key in initializing:
                                initializing.discard(key)
                                if "result" in reply and not connected:
                                    connected = True
                                    ready = True
                            outstanding.pop(key, None)
                            with pending_lock:
                                pending.pop(key, None)
                except (ValueError, UnicodeError, TypeError):
                    pass
                with write_client:
                    output_stream.write(line)
                    output_stream.flush()
                if ready and on_connected is not None:
                    alert_worker = threading.Thread(target=notify_connected, name="mcp-connector-alert", daemon=True)
                    alert_worker.start()
        except OSError as exc:
            LOG.warning("MCP server output failed: %s", exc)
        finally:
            with write_child:
                done.set()
                with pending_lock:
                    for cancellation in pending.values():
                        cancellation.set()
                for request_id in outstanding.values():
                    send_client(error_response(request_id, "MCP server terminated before responding"))
                outstanding.clear()

    inbound: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=64)
    work_slots = threading.BoundedSemaphore(32)

    def read_client() -> None:
        try:
            while not done.is_set() and not stop.is_set():
                line = reader_in.readline(MAX_BODY + 2)
                if not line:
                    break
                if not line.endswith(b"\n") and len(line) > MAX_BODY:
                    LOG.warning("Oversized MCP client message discarded")
                    while line and not line.endswith(b"\n"):
                        line = reader_in.readline(MAX_BODY + 2)
                    continue
                try:
                    message = decode_message(line)
                except (ValueError, UnicodeError):
                    send_client({"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": "Invalid MCP JSON-RPC request"}})
                    continue
                try:
                    key = request_key(message)
                    if key is not None:
                        with pending_lock:
                            if key in pending or len(pending) >= 256:
                                send_client(error_response(message.get("id") if isinstance(message, dict) else None,
                                                          "Too many or duplicate MCP requests"))
                                continue
                            pending[key] = threading.Event()
                    elif (isinstance(message, dict) and message.get("method") == "notifications/cancelled"
                          and isinstance(message.get("params"), dict)):
                        cancelled_key = json.dumps(message["params"].get("requestId"))
                        with pending_lock:
                            if cancelled_key in pending:
                                pending[cancelled_key].set()
                except (ValueError, UnicodeError, TypeError):
                    pass
                while not done.is_set() and not stop.is_set():
                    try:
                        inbound.put(line, timeout=0.2)
                        break
                    except queue.Full:
                        continue
        except OSError as exc:
            LOG.warning("MCP client input failed: %s", type(exc).__name__)
        finally:
            while not done.is_set() and not stop.is_set():
                try:
                    inbound.put(None, timeout=0.2)
                    break
                except queue.Full:
                    continue

    reader = threading.Thread(target=read_child, name="mcp-server-reader", daemon=True)
    client_reader = threading.Thread(target=read_client, name="mcp-client-reader", daemon=True)
    reader.start()
    client_reader.start()
    try:
        with ThreadPoolExecutor(max_workers=32) as workers:
            while not done.is_set() and not stop.is_set():
                try:
                    line = inbound.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    message = decode_message(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(message, dict) and message.get("method") == "initialize":
                    handle(line)
                else:
                    while not done.is_set() and not stop.is_set() and not work_slots.acquire(timeout=0.2):
                        pass
                    if done.is_set() or stop.is_set():
                        break
                    workers.submit(handle, line).add_done_callback(lambda _: work_slots.release())
    finally:
        done.set()
        with pending_lock:
            for cancellation in pending.values():
                cancellation.set()
        try:
            child_in.close()
        except OSError:
            pass
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        reader.join(timeout=2)
        if alert_worker:
            alert_worker.join(timeout=11)
        child_out.close()
