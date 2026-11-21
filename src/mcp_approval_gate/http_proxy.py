"""Streamable HTTP MCP reverse proxy for remote (Claude Web) connectors.

Clients must present ``Authorization: Bearer <inbound token>`` (compared in constant time).
Each POSTed JSON-RPC request goes through the approval gate before it is sent upstream; the
client's own credentials and hop-by-hop headers are never forwarded, redirects are refused,
and a client that disconnects while waiting cancels its approval.
"""

from __future__ import annotations

import http.client
import json
import logging
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.message import Message
from typing import Any
from urllib.parse import urlsplit

from .gate import ApprovalGate, MAX_BODY, decode_message, error_response

LOG = logging.getLogger(__name__)
HOP_HEADERS = {"connection", "proxy-connection", "transfer-encoding", "content-length", "host",
               "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
               "cookie", "forwarded", "x-forwarded-for", "x-real-ip"}


def excluded_headers(headers: Message) -> set[str]:
    names = set(HOP_HEADERS)
    for value in headers.get_all("Connection", []):
        names.update(name.strip().lower() for name in value.split(","))
    return names


class BoundedHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.client_slots = threading.BoundedSemaphore(64)
        super().__init__(*args, **kwargs)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.client_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.client_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.client_slots.release()


def handler_for(upstream: str, gate: ApprovalGate, inbound_token: str,
                upstream_token: str = "") -> type[BaseHTTPRequestHandler]:
    target = urlsplit(upstream)
    if target.scheme not in ("http", "https") or not target.hostname or target.username or target.password:
        raise ValueError("upstream must be an http(s) URL without embedded credentials")
    hostname: str = target.hostname
    port = target.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("upstream port must be between 1 and 65535")
    if target.scheme == "http" and target.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("remote upstreams must use HTTPS")
    connections = threading.BoundedSemaphore(64)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(75)

        def log_message(self, format: str, *args: Any) -> None:
            LOG.info("MCP HTTP response sent")

        def reply(self, code: int, payload: object) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _client_disconnected(self) -> bool:
            try:
                if not select.select([self.connection], [], [], 0)[0]:
                    return False
                flags = socket.MSG_PEEK | int(getattr(socket, "MSG_DONTWAIT", 0))
                return bool(self.connection.recv(1, flags) == b"")
            except BlockingIOError:
                return False
            except (OSError, ValueError):
                return True

        def _watch_client(self, cancelled: threading.Event, finished: threading.Event) -> None:
            while not finished.is_set():
                if self._client_disconnected():
                    cancelled.set()
                    return
                finished.wait(0.1)

        def do_POST(self) -> None:
            self._limited_handle()

        def do_GET(self) -> None:
            self._limited_handle()

        def do_DELETE(self) -> None:
            self._limited_handle()

        def _limited_handle(self) -> None:
            if not connections.acquire(blocking=False):
                self.close_connection = True
                return self.reply(503, {"error": "proxy at capacity"})
            try:
                self._handle()
            except (OSError, http.client.HTTPException):
                self.close_connection = True
                LOG.warning("Client MCP connection interrupted")
            finally:
                connections.release()

        def _handle(self) -> None:
            import hmac
            if self.path != "/mcp":
                self.close_connection = True
                return self.reply(404, {"error": "not found"})
            authorizations = self.headers.get_all("Authorization", [])
            if (len(authorizations) != 1 or not authorizations[0].isascii()
                    or not hmac.compare_digest(authorizations[0], "Bearer " + inbound_token)):
                self.close_connection = True
                return self.reply(401, {"error": "unauthorized"})
            body = b""
            request_id = None
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get_all("Transfer-Encoding") or len(lengths) > 1
                    or (lengths and (not lengths[0] or not lengths[0].isascii() or not lengths[0].isdecimal()))
                    or (self.command != "POST" and lengths and lengths[0] != "0")):
                self.close_connection = True
                return self.reply(400, {"error": "unsupported request framing"})
            if self.command == "POST":
                try:
                    size = int(lengths[0]) if lengths else -1
                    if not 0 <= size <= MAX_BODY:
                        self.close_connection = True
                        return self.reply(413, {"error": "invalid or oversized body"})
                    body = self.rfile.read(size)
                    if len(body) != size:
                        self.close_connection = True
                        return self.reply(400, {"error": "incomplete request body"})
                    message = decode_message(body)
                    if not isinstance(message, dict):
                        return self.reply(400, {"error": "expected one JSON-RPC object"})
                    request_id = message.get("id")
                    cancelled = threading.Event()
                    finished = threading.Event()
                    watcher = threading.Thread(target=self._watch_client, args=(cancelled, finished), daemon=True)
                    watcher.start()
                    try:
                        forwarded, denied = gate.evaluate(message, target.netloc, cancelled)
                    finally:
                        finished.set()
                        watcher.join(timeout=0.2)
                    if cancelled.is_set() or self._client_disconnected():
                        self.close_connection = True
                        return
                    if denied is not None:
                        return self.reply(200, denied)
                    if forwarded is None:
                        return self.reply(400, {"error": "invalid request"})
                except (ValueError, UnicodeError):
                    return self.reply(400, {"error": "invalid JSON"})
            excluded = excluded_headers(self.headers)
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in excluded and key.lower() != "authorization"}
            if upstream_token:
                headers["Authorization"] = "Bearer " + upstream_token
            headers["Content-Length"] = str(len(body)) if self.command == "POST" else "0"
            connection = (http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection)(
                hostname, port, timeout=120)
            started = False
            try:
                connection.request(self.command, target.path + ("?" + target.query if target.query else ""),
                                   body=body if self.command == "POST" else None, headers=headers)
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    LOG.warning("Upstream MCP redirect refused")
                    return self.reply(502, error_response(request_id, "MCP upstream redirect refused"))
                self.send_response(response.status)
                started = True
                upstream_headers = response.headers
                excluded = excluded_headers(upstream_headers)
                for key, value in response.getheaders():
                    if key.lower() not in excluded and key.lower() != "authorization":
                        self.send_header(key, value)
                lengths = upstream_headers.get_all("Content-Length", [])
                connection_options = {name.strip().lower()
                                      for value in upstream_headers.get_all("Connection", [])
                                      for name in value.split(",")}
                if (len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdecimal()
                        and not upstream_headers.get_all("Transfer-Encoding")
                        and "content-length" not in connection_options):
                    self.send_header("Content-Length", lengths[0])
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while chunk := response.read1(8192):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, http.client.HTTPException) as exc:
                LOG.warning("Upstream MCP connection failed: %s", type(exc).__name__)
                self.close_connection = True
                if not started:
                    try:
                        self.reply(502, error_response(request_id, "MCP server unavailable"))
                    except OSError:
                        pass
            finally:
                connection.close()

    return Handler
