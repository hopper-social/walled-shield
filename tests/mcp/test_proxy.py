import io
import json
import pathlib
import queue
import sys
import tempfile
from unittest.mock import patch
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request as HttpRequest, urlopen

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mcp_approval_gate.activity import ActivityStore, MAX_EVENTS
from mcp_approval_gate.gate import ApprovalGate, CONTROL_METHODS, DENIED, Request, decode_message
from mcp_approval_gate.http_proxy import BoundedHTTPServer, handler_for
from mcp_approval_gate.popup import MAX_PREVIEW_CHARS, PopupApprover, action_description, approval_preview
from mcp_approval_gate.stdio_proxy import serve as serve_stdio


class GateTests(unittest.TestCase):
    def test_decision_alert_failure_cannot_change_outcome(self):
        events = []
        gate = ApprovalGate(lambda request: request.method == "tools/call",
                            on_decision=lambda request, allowed: events.append(allowed))
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
        self.assertEqual(gate.evaluate(request, "notes"), (request, None))
        self.assertEqual(events, [True])
        def offline(*_):
            raise OSError("offline")
        failing = ApprovalGate(lambda _: True, on_decision=offline)
        self.assertEqual(failing.evaluate(request, "notes"), (request, None))
        blocked = ApprovalGate(lambda _: False, on_decision=offline)
        self.assertEqual(blocked.evaluate(request, "notes")[1]["error"]["code"], DENIED)
        self.assertEqual(failing.evaluate({"id": 2, "method": "initialize"}, "notes")[0]["method"], "initialize")

    def test_json_decoder_accepts_valid_unicode_and_finite_numbers(self):
        message = decode_message(b'{"label":"\\ud83d\\ude00","amount":2.5}')
        self.assertEqual(message, {"label": "😀", "amount": 2.5})

    def test_json_decoder_rejects_excessive_nesting_without_recursion_error(self):
        nested = b'[' * 1200 + b'0' + b']' * 1200
        with self.assertRaisesRegex(ValueError, "JSON nesting too deep"):
            decode_message(nested)

    def test_json_decoder_bounds_integer_conversion_before_parsing(self):
        self.assertEqual(decode_message(b'12345678901234567890'), 12345678901234567890)
        with self.assertRaisesRegex(ValueError, "JSON integer too large"):
            decode_message(b'{"params":{"value":' + b'9' * 2000 + b'}}')

    def test_only_known_setup_is_silent_by_default(self):
        seen = []
        gate = ApprovalGate(lambda request: seen.append(request.method) or False)
        for method in CONTROL_METHODS:
            message = {"jsonrpc": "2.0", "id": 1, "method": method}
            self.assertEqual(gate.evaluate(message, "notes"), (message, None))
        for method in ("tools/call", "resources/read", "prompts/get", "unknown/action", "tools/listAll"):
            message = {"jsonrpc": "2.0", "id": 2, "method": method}
            self.assertIsNone(gate.evaluate(message, "notes")[0])
        self.assertEqual(seen, ["tools/call", "resources/read", "prompts/get", "unknown/action", "tools/listAll"])

    def test_strict_mode_prompts_for_setup_too(self):
        seen = []
        gate = ApprovalGate(lambda request: seen.append(request.method) or False, strict=True)
        self.assertEqual(gate.evaluate({"id": 1, "method": "initialize"}, "notes")[1]["error"]["code"], DENIED)
        self.assertEqual(gate.evaluate({"id": 2, "method": "tools/list"}, "notes")[1]["error"]["code"], DENIED)
        self.assertEqual(seen, ["initialize", "tools/list"])

    def test_audit_excludes_arguments_and_request_ids(self):
        with self.assertLogs("mcp_approval_gate.gate", level="INFO") as captured:
            gate = ApprovalGate(lambda _: False)
            gate.evaluate({"id": "private-request-id", "method": "initialize"}, "notes")
            gate.evaluate({"id": 2, "method": "tools/call", "params": {
                "name": "notes_write", "arguments": {"text": "secret body"}}}, "notes")
        log = "\n".join(captured.output)
        self.assertIn("auto_forwarded", log)
        self.assertIn("blocked", log)
        self.assertIn("notes_write", log)
        self.assertNotIn("private-request-id", log)
        self.assertNotIn("secret body", log)

    def test_action_copy_describes_known_operation_without_claiming_effects(self):
        self.assertEqual(action_description("tools/call", '"notes_write"'),
                         'Claude wants to run tool "notes_write".')
        self.assertIn("read a resource", action_description("resources/read", "(not a tool call)"))
        self.assertIn("retrieve a prompt", action_description("prompts/get", "(not a tool call)"))
        self.assertEqual(action_description("other/new", "(not a tool call)"),
                         "Claude wants to send this request to the server.")

    def test_private_bounded_activity_contains_only_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "history" / "activity.sqlite3"
            store = ActivityStore(path)
            gate = ApprovalGate(lambda _: False, activity=store)
            gate.evaluate({"id": "secret-id", "method": "initialize"}, "notes")
            gate.evaluate({"id": 2, "method": "tools/call", "params": {
                "name": "notes_write", "arguments": {"text": "secret body"}}}, "notes")
            self.assertEqual([entry[1] for entry in store.recent()], ["blocked", "auto_forwarded"])
            self.assertEqual(store.recent()[0][3], "notes_write")
            for i in range(MAX_EVENTS + 2):
                store.record("notes", "ping", None, "auto_forwarded")
            self.assertEqual(len(store.recent(200)), 100)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM activity").fetchone()[0], MAX_EVENTS)
            store.close()
            self.assertNotIn(b"secret body", path.read_bytes())
            self.assertNotIn(b"secret-id", path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_activity_command_is_readable_and_escapes_metadata(self):
        from mcp_approval_gate.main import main
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "private" / "activity.sqlite3"
            with patch("mcp_approval_gate.activity.database_path", return_value=path):
                store = ActivityStore()
                store.record("notes", "initialize", None, "auto_forwarded")
                store.record("notes", "tools/call", "write\nspoof", "blocked")
                store.close()
                output = io.StringIO()
                with patch("sys.stdout", output):
                    self.assertEqual(main(["activity"]), 0)
                self.assertIn("Setup / discovery", output.getvalue())
                self.assertIn("Blocked", output.getvalue())
                self.assertIn('"write\\nspoof"', output.getvalue())

    def test_history_failure_denies_before_forwarding(self):
        class BrokenHistory:
            def record(self, *_):
                raise OSError("disk full")
        self.assertIsNone(ApprovalGate(lambda _: True, activity=BrokenHistory()).evaluate(
            {"id": 3, "method": "tools/call"}, "notes")[0])
        self.assertIsNone(ApprovalGate(lambda _: True, activity=BrokenHistory()).evaluate(
            {"id": 4, "method": "initialize"}, "notes")[0])

    def test_popup_preview_never_hides_arguments_or_control_characters(self):
        safe = Request("notes", "tools/call", {"name": "write", "text": "A\u202eB"}, 1)
        labels, details = approval_preview(safe)
        self.assertIn("write", labels[2])
        self.assertIn("\\u202e", details)
        self.assertNotIn("\u202e", details)
        oversized = Request("notes", "tools/call", {"name": "write", "text": "x" * MAX_PREVIEW_CHARS}, 2)
        self.assertIsNone(approval_preview(oversized))
        self.assertIsNone(approval_preview(Request("notes", "tools/call", {"name": "x" * 300}, 3)))
        approver = PopupApprover.__new__(PopupApprover)
        approver.closed = threading.Event()
        approver.pending = queue.Queue()
        self.assertFalse(approver.decide(oversized))
        self.assertTrue(approver.pending.empty())

    def test_cancellation_dismisses_active_popup_without_tk(self):
        class Window:
            destroyed = False
            def grab_release(self):
                pass
            def destroy(self):
                self.destroyed = True

        class Root:
            def after(self, *_):
                pass

        approver = PopupApprover.__new__(PopupApprover)
        approver.closed = threading.Event()
        approver.pending = queue.Queue()
        approver.active = None
        approver.timeout = 5
        approver.stop_event = None
        approver.root = Root()
        cancellation = threading.Event()
        request = Request("notes", "tools/call", {"name": "write"}, 7, cancellation)
        result = []
        worker = threading.Thread(target=lambda: result.append(approver.decide(request)))
        worker.start()
        _, response, deadline, _ = approver.pending.get(timeout=1)
        window = Window()
        approver.active = (window, response, deadline, request)
        cancellation.set()
        approver._tick()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])
        self.assertTrue(window.destroyed)
        self.assertIsNone(approver.active)

    def test_cancellation_wins_a_simultaneous_allow(self):
        cancelled = threading.Event()
        def decide(_):
            cancelled.set()
            return True
        allowed, denied = ApprovalGate(decide).evaluate(
            {"id": 7, "method": "tools/call"}, "server", cancelled)
        self.assertIsNone(allowed)
        self.assertEqual(denied["error"]["code"], DENIED)

    def test_allow_block_and_notifications(self):
        seen = []
        gate = ApprovalGate(lambda request: seen.append(request) or request.parameters.get("name") == "safe")
        allow = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "safe"}}
        block = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "unsafe"}}
        self.assertEqual(gate.evaluate(allow, "server"), (allow, None))
        self.assertEqual(gate.evaluate(block, "server")[1]["error"]["code"], DENIED)
        self.assertEqual(gate.evaluate({"jsonrpc": "2.0", "method": "notifications/initialized"}, "server")[0]["method"], "notifications/initialized")
        self.assertEqual([request.parameters["name"] for request in seen], ["safe", "unsafe"])

    def test_method_without_id_cannot_bypass_approval_as_notification(self):
        gate = ApprovalGate(lambda _: True)
        self.assertEqual(gate.evaluate({"method": "tools/call", "params": {"name": "unsafe"}}, "server"),
                         (None, None))
        self.assertEqual(gate.evaluate({"method": "tools/call", "id": None}, "server")[1]["error"]["code"], DENIED)

    def test_invalid_request_ids_never_reach_approval(self):
        seen = []
        gate = ApprovalGate(lambda request: seen.append(request) or True)
        for invalid in (None, True, False, {}, [], [1]):
            forwarded, denied = gate.evaluate({"jsonrpc": "2.0", "id": invalid,
                                               "method": "tools/call"}, "notes")
            self.assertIsNone(forwarded)
            self.assertIsNone(denied["id"])
            self.assertEqual(denied["error"]["code"], DENIED)
        for valid in (0, 1.25, "request-1"):
            self.assertIsNotNone(gate.evaluate({"id": valid, "method": "tools/call"}, "notes")[0])
        self.assertEqual(len(seen), 3)

    def test_fail_closed_and_capacity(self):
        gate = ApprovalGate(lambda _: (_ for _ in ()).throw(RuntimeError("UI unavailable")), max_pending=1)
        msg = {"jsonrpc": "2.0", "id": 0, "method": "tools/call"}
        self.assertIsNone(gate.evaluate(msg, "server")[0])
        gate.slots.acquire()
        self.assertIsNone(gate.evaluate(msg, "server")[0])
        gate.slots.release()

    def test_concurrent_requests_are_independently_decided(self):
        from concurrent.futures import ThreadPoolExecutor
        barrier = threading.Barrier(8)
        def decide(request):
            barrier.wait(timeout=3)
            return request.request_id % 2 == 0
        gate = ApprovalGate(decide, max_pending=8)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(gate.evaluate, {"id": i, "method": "tools/call"}, "server") for i in range(8)]
            results = [future.result(timeout=4) for future in futures]
        self.assertEqual([result[0] is not None for result in results], [i % 2 == 0 for i in range(8)])

    def test_batch_is_not_forwarded(self):
        msg = [{"id": 1, "method": "tools/call"}, {"id": 2, "method": "tools/call"}]
        forwarded, denied = ApprovalGate(lambda _: True).evaluate(msg, "server")
        self.assertIsNone(forwarded)
        self.assertEqual([item["id"] for item in denied], [1, 2])
        for batch in ([], [{"method": "notifications/initialized"}]):
            forwarded, denied = ApprovalGate(lambda _: True).evaluate(batch, "server")
            self.assertIsNone(forwarded)
            self.assertIsNone(denied["id"])
            self.assertEqual(denied["error"]["code"], DENIED)


class StdioTests(unittest.TestCase):
    def test_invalid_id_never_reaches_stdio_child(self):
        inbound = io.BytesIO(b'{"jsonrpc":"2.0","id":{"bad":1},"method":"tools/call"}\n')
        outbound = io.BytesIO()
        decisions = []
        serve_stdio([sys.executable, "-u", "-c", "import sys; sys.stdin.read()"],
                    ApprovalGate(lambda request: decisions.append(request) or True),
                    "test", inbound, outbound)
        self.assertEqual(decisions, [])
        self.assertIsNone(json.loads(outbound.getvalue())["id"])

    def test_proxy_credentials_are_not_inherited_by_wrapped_server(self):
        import os
        from unittest.mock import patch
        code = ("import json,os,sys\nfor line in sys.stdin:\n"
                " m=json.loads(line); print(json.dumps({'id':m['id'],'result':{"
                "'token':os.environ.get('MCP_TELEGRAM_BOT_TOKEN'),"
                "'approval':os.environ.get('MCP_APPROVAL_TOKEN'),"
                "'upstream':os.environ.get('MCP_UPSTREAM_TOKEN'),"
                "'server_secret':os.environ.get('SERVER_SPECIFIC_SECRET')}}),flush=True)")
        incoming = io.BytesIO(b'{"id":1,"method":"tools/call"}\n')
        outgoing = io.BytesIO()
        with patch.dict(os.environ, {"MCP_TELEGRAM_BOT_TOKEN": "secret-token",
                                  "MCP_APPROVAL_TOKEN": "proxy-secret",
                                  "MCP_UPSTREAM_TOKEN": "remote-secret",
                                  "SERVER_SPECIFIC_SECRET": "server-secret"}):
            serve_stdio([sys.executable, "-u", "-c", code], ApprovalGate(lambda _: True),
                        "notes", incoming, outgoing)
        result = json.loads(outgoing.getvalue().splitlines()[0])["result"]
        self.assertEqual(result, {"token": None, "approval": None,
                                  "upstream": None, "server_secret": "server-secret"})

    def test_end_to_end_approval_and_denial(self):
        code = "import sys,json\nfor line in sys.stdin:\n m=json.loads(line); print(json.dumps({'jsonrpc':'2.0','id':m.get('id'),'result':{'echo':m.get('params')}}),flush=True) if 'id' in m else None"
        messages = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "allow"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "block"}}]
        inbound = io.BytesIO(b"".join((json.dumps(m) + "\n").encode() for m in messages))
        outbound = io.BytesIO()
        serve_stdio([sys.executable, "-u", "-c", code],
                    ApprovalGate(lambda request: request.parameters["name"] == "allow"),
                    "test", inbound, outbound)
        replies = [json.loads(line) for line in outbound.getvalue().splitlines()]
        self.assertEqual(len(replies), 2)
        self.assertIn("result", next(reply for reply in replies if reply["id"] == 1))
        self.assertEqual(next(reply for reply in replies if reply["id"] == 2)["error"]["code"], DENIED)


    def test_deeply_nested_stdio_request_is_rejected_before_approval(self):
        nested = b'[' * 1200 + b'0' + b']' * 1200
        inbound = io.BytesIO(nested + b'\n')
        outbound = io.BytesIO()
        decisions = []
        serve_stdio([sys.executable, "-u", "-c", "import sys; sys.stdin.read()"],
                    ApprovalGate(lambda request: decisions.append(request) or True),
                    "test", inbound, outbound)
        self.assertEqual(decisions, [])
        self.assertEqual(json.loads(outbound.getvalue())["error"]["code"], -32700)

    def test_cancellation_during_approval_prevents_forwarding(self):
        code = ("import sys,json\nfor line in sys.stdin:\n"
                " m=json.loads(line); print(json.dumps({'jsonrpc':'2.0','id':m.get('id'),"
                "'result':{'method':m.get('method')}}),flush=True)")
        messages = [b'{"jsonrpc":"2.0","id":7,"method":"tools/call"}\n',
                    b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":7}}\n']
        outbound = io.BytesIO()
        serve_stdio([sys.executable, "-u", "-c", code], ApprovalGate(lambda _: True),
                    "test", io.BytesIO(b"".join(messages)), outbound)
        replies = [json.loads(line) for line in outbound.getvalue().splitlines()]
        self.assertEqual(next(item for item in replies if item.get("id") == 7)["error"]["code"], DENIED)
        self.assertFalse(any(item.get("result", {}).get("method") == "tools/call" for item in replies))

    def test_cancellation_after_approval_starts_never_forwards_tool(self):
        class Input:
            def __init__(self):
                self.lines = queue.Queue()
            def readline(self, _):
                return self.lines.get(timeout=5)

        started = threading.Event()
        inbound = Input()
        outbound = io.BytesIO()
        code = ("import sys,json\nfor line in sys.stdin:\n"
                " m=json.loads(line); print(json.dumps({'jsonrpc':'2.0','id':m.get('id'),"
                "'result':{'method':m.get('method')}}),flush=True) if 'id' in m else None")
        def decide(request):
            started.set()
            self.assertTrue(request.cancelled.wait(timeout=2))
            return True

        worker = threading.Thread(target=serve_stdio, args=(
            [sys.executable, "-u", "-c", code], ApprovalGate(decide), "test", inbound, outbound))
        worker.start()
        try:
            inbound.lines.put(b'{"jsonrpc":"2.0","id":7,"method":"tools/call"}\n')
            self.assertTrue(started.wait(timeout=3))
            inbound.lines.put(b'{"jsonrpc":"2.0","method":"notifications/cancelled",'
                              b'"params":{"requestId":7}}\n')
            inbound.lines.put(b'')
            worker.join(timeout=4)
            self.assertFalse(worker.is_alive())
            replies = [json.loads(line) for line in outbound.getvalue().splitlines()]
            self.assertEqual(next(item for item in replies if item.get("id") == 7)["error"]["code"], DENIED)
            self.assertFalse(any(item.get("result", {}).get("method") == "tools/call" for item in replies))
        finally:
            if worker.is_alive():
                inbound.lines.put(b'')
                worker.join(timeout=5)

    def test_server_termination_returns_error_for_pending_request(self):
        inbound = io.BytesIO(b'{"jsonrpc":"2.0","id":7,"method":"tools/call"}\n')
        outbound = io.BytesIO()
        serve_stdio([sys.executable, "-u", "-c", "import sys; sys.stdin.readline()"],
                    ApprovalGate(lambda _: True), "test", inbound, outbound)
        replies = [json.loads(line) for line in outbound.getvalue().splitlines()]
        self.assertEqual(replies[0]["id"], 7)
        self.assertIn("error", replies[0])


class HttpTests(unittest.TestCase):
    def test_invalid_upstream_port_fails_at_configuration_time(self):
        gate = ApprovalGate(lambda _: True)
        for url in ("http://127.0.0.1:99999/mcp", "http://127.0.0.1:0/mcp",
                    "https://example.com:notaport/mcp", "https://:443/mcp"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler_for(url, gate, "secret")
        handler_for("http://127.0.0.1:8080/mcp", gate, "secret")

    def test_invalid_id_never_reaches_http_upstream(self):
        import http.client

        decisions = []
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda request: decisions.append(request) or True),
            "secret"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
        try:
            body = b'{"jsonrpc":"2.0","id":{"bad":1},"method":"tools/call"}'
            client.request("POST", "/mcp", body, {"Authorization": "Bearer secret",
                                                "Content-Type": "application/json"})
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIsNone(json.loads(response.read())["id"])
            self.assertEqual(decisions, [])
        finally:
            client.close()
            proxy.shutdown()
            proxy.server_close()

    def test_deeply_nested_http_request_is_rejected_before_approval_or_forwarding(self):
        import http.client

        decisions = []
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda request: decisions.append(request) or True),
            "secret"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
        try:
            nested = b'[' * 1200 + b'0' + b']' * 1200
            client.request("POST", "/mcp", nested, {"Authorization": "Bearer secret",
                                                   "Content-Type": "application/json"})
            response = client.getresponse()
            self.assertEqual(response.status, 400)
            self.assertEqual(json.loads(response.read()), {"error": "invalid JSON"})
            self.assertEqual(decisions, [])
        finally:
            client.close()
            proxy.shutdown()
            proxy.server_close()

    def test_idle_connections_are_bounded_before_threads_spawn(self):
        import socket
        server = BoundedHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda _: True), "secret"))
        for _ in range(64):
            server.client_slots.acquire()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=5) as client:
                client.settimeout(5)
                self.assertEqual(client.recv(1), b"")
        finally:
            for _ in range(64):
                server.client_slots.release()
            server.shutdown()
            server.server_close()

    def test_conflicting_http_framing_is_rejected_and_connection_closed(self):
        import http.client
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda _: True), "secret"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
            client.putrequest("POST", "/mcp")
            client.putheader("Authorization", "Bearer secret")
            client.putheader("Content-Length", "1")
            client.putheader("Content-Length", "50")
            client.endheaders(b"x")
            response = client.getresponse()
            self.assertEqual(response.status, 400)
            self.assertTrue(response.will_close)
            response.read()
            client.close()
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_incomplete_body_cannot_be_approved_or_forwarded(self):
        import socket
        import http.client
        decisions = []
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda request: decisions.append(request) or True),
            "secret"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}'
            with socket.create_connection(("127.0.0.1", proxy.server_port), timeout=5) as client:
                client.settimeout(5)
                client.sendall(b"POST /mcp HTTP/1.1\r\nHost: localhost\r\n"
                               b"Authorization: Bearer secret\r\nContent-Length: " +
                               str(len(body) + 8).encode() + b"\r\n\r\n" + body)
                client.shutdown(socket.SHUT_WR)
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assertEqual(response.status, 400)
                self.assertTrue(response.will_close)
                self.assertIn(b"incomplete", response.read())
            self.assertEqual(decisions, [])
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_non_ascii_authorization_is_rejected_without_handler_failure(self):
        import http.client
        import socket
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda _: self.fail("unexpected approval")),
            "local-test-token"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            with socket.create_connection(("127.0.0.1", proxy.server_port), timeout=5) as client:
                client.settimeout(5)
                client.sendall(b"POST /mcp HTTP/1.1\r\nHost: localhost\r\n"
                               b"Authorization: Bearer caf\xe9\r\nContent-Length: 0\r\n\r\n")
                response = http.client.HTTPResponse(client)
                response.begin()
                self.assertEqual(response.status, 401)
                self.assertTrue(response.will_close)
                response.read()
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_ambiguous_or_invalid_headers_cannot_reach_approval(self):
        import socket
        import http.client
        decisions = []
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda request: decisions.append(request) or True),
            "secret"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}'
        cases = (
            (b"Content-Length: +" + str(len(body)).encode(), 400),
            (b"Content-Length: " + str(len(body)).encode() + b"\r\nTransfer-Encoding:", 400),
            (b"Content-Length: " + str(len(body)).encode() +
             b"\r\nAuthorization: Bearer secret", 401),
        )
        try:
            for headers, status in cases:
                with self.subTest(headers=headers):
                    with socket.create_connection(("127.0.0.1", proxy.server_port), timeout=5) as client:
                        client.settimeout(5)
                        client.sendall(b"POST /mcp HTTP/1.1\r\nHost: localhost\r\n"
                                       b"Authorization: Bearer secret\r\n" + headers + b"\r\n\r\n" + body)
                        response = http.client.HTTPResponse(client)
                        response.begin()
                        self.assertEqual(response.status, status)
                        self.assertTrue(response.will_close)
                        response.read()
            self.assertEqual(decisions, [])
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_ambiguous_json_is_rejected_before_approval_or_forwarding(self):
        import http.client
        decisions = []
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda request: decisions.append(request) or True),
            "secret"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            bodies = (
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","method":"initialize"}',
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"safe","name":"other"}}',
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"value":NaN}}',
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"value":1e999}}',
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"\\ud800"}}',
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"\\ud800":1}}',
            )
            for body in bodies:
                with self.subTest(body=body):
                    client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
                    try:
                        client.request("POST", "/mcp", body, {"Authorization": "Bearer secret"})
                        response = client.getresponse()
                        self.assertEqual(response.status, 400)
                        response.read()
                    finally:
                        client.close()
            self.assertEqual(decisions, [])
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_disconnected_http_caller_cannot_forward_after_approval(self):
        import socket
        approved = threading.Event()
        released = threading.Event()
        cancelled = threading.Event()
        received = threading.Event()

        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                received.set()
                self.send_response(200)
                self.end_headers()
            def log_message(self, *_):
                pass

        def decide(request):
            approved.set()
            if request.cancelled.wait(2):
                cancelled.set()
            released.wait(2)
            return True

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            f"http://127.0.0.1:{upstream.server_port}/mcp", ApprovalGate(decide), "secret"))
        for server in (upstream, proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}'
            client = socket.create_connection(("127.0.0.1", proxy.server_port), timeout=2)
            try:
                client.sendall(b"POST /mcp HTTP/1.1\r\nHost: localhost\r\n"
                               b"Authorization: Bearer secret\r\nContent-Length: " +
                               str(len(body)).encode() + b"\r\n\r\n" + body)
                self.assertTrue(approved.wait(2))
            finally:
                client.close()
            self.assertTrue(cancelled.wait(2))
            released.set()
            self.assertFalse(received.wait(0.3))
        finally:
            released.set()
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()

    def test_upstream_redirect_cannot_bypass_gate(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(307)
                self.send_header("Location", "https://example.invalid/mcp")
                self.end_headers()
            def log_message(self, *_):
                pass
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            f"http://127.0.0.1:{upstream.server_port}/mcp", ApprovalGate(lambda _: True), "secret"))
        for server in (upstream, proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            from urllib.error import HTTPError
            req = HttpRequest(f"http://127.0.0.1:{proxy.server_port}/mcp",
                              b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}',
                              {"Authorization": "Bearer secret"})
            with self.assertRaises(HTTPError) as error:
                urlopen(req, timeout=5)
            self.assertEqual(error.exception.code, 502)
        finally:
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()

    def test_upstream_failure_is_not_approved_as_success(self):
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            "http://127.0.0.1:1/mcp", ApprovalGate(lambda _: True), "secret"))
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        try:
            from urllib.error import HTTPError
            req = HttpRequest(f"http://127.0.0.1:{proxy.server_port}/mcp",
                              b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}',
                              {"Authorization": "Bearer secret"})
            with self.assertRaises(HTTPError) as error:
                urlopen(req, timeout=5)
            self.assertEqual(error.exception.code, 502)
            self.assertEqual(json.loads(error.exception.read())["id"], 1)
        finally:
            proxy.shutdown()
            proxy.server_close()

    def test_get_stream_and_delete_relay_session_without_approval(self):
        import http.client

        first = b'event: message\ndata: {"id":1}\n\n'
        second = b'event: message\ndata: {"id":2}\n\n'
        release = threading.Event()
        first_sent = threading.Event()
        received = []
        decisions = []

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(("GET", self.path, self.headers.get("Mcp-Session-Id"),
                                 self.headers.get("Last-Event-ID"), self.headers.get("Authorization")))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Mcp-Session-Id", "session-7")
                self.end_headers()
                self.wfile.write(first)
                self.wfile.flush()
                first_sent.set()
                if release.wait(3):
                    self.wfile.write(second)
                    self.wfile.flush()

            def do_DELETE(self):
                received.append(("DELETE", self.path, self.headers.get("Mcp-Session-Id"),
                                 self.headers.get("Last-Event-ID"), self.headers.get("Authorization")))
                self.send_response(204)
                self.send_header("Mcp-Session-Id", "session-7")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_):
                pass

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            f"http://127.0.0.1:{upstream.server_port}/mcp",
            ApprovalGate(lambda request: decisions.append(request) or True), "secret", "upstream-secret"))
        for server in (upstream, proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        stream = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
        try:
            stream.request("GET", "/mcp", headers={"Authorization": "Bearer secret",
                                                    "Mcp-Session-Id": "session-7",
                                                    "Last-Event-ID": "event-3"})
            response = stream.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
            self.assertEqual(response.getheader("Mcp-Session-Id"), "session-7")
            self.assertTrue(first_sent.wait(2))
            self.assertEqual(response.read(len(first)), first)
            delete = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
            try:
                delete.request("DELETE", "/mcp", headers={"Authorization": "Bearer secret",
                                                          "Mcp-Session-Id": "session-7"})
                ended = delete.getresponse()
                self.assertEqual(ended.status, 204)
                self.assertEqual(ended.getheader("Mcp-Session-Id"), "session-7")
                self.assertEqual(ended.read(), b"")
            finally:
                delete.close()
            release.set()
            self.assertEqual(response.read(), second)
            self.assertEqual(received, [
                ("GET", "/mcp", "session-7", "event-3", "Bearer upstream-secret"),
                ("DELETE", "/mcp", "session-7", None, "Bearer upstream-secret"),
            ])
            self.assertEqual(decisions, [])
        finally:
            release.set()
            stream.close()
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()

    def test_hop_by_hop_content_length_is_not_reintroduced(self):
        import http.client

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Connection", "Content-Length")
                self.send_header("Content-Length", "3")
                self.end_headers()
                self.wfile.write(b"abc")

            def log_message(self, *_):
                pass

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            f"http://127.0.0.1:{upstream.server_port}/mcp", ApprovalGate(lambda _: True), "secret"))
        for server in (upstream, proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
        try:
            client.request("GET", "/mcp", headers={"Authorization": "Bearer secret"})
            response = client.getresponse()
            self.assertIsNone(response.getheader("Content-Length"))
            self.assertEqual(response.read(), b"abc")
        finally:
            client.close()
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()

    def test_interrupted_upstream_sse_does_not_retry_or_fabricate_success(self):
        import http.client
        import socket

        first = b'event: message\ndata: {"jsonrpc":"2.0","id":9,"result":{}}\n\n'
        received = []
        decisions = []

        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(first) + 100))
                self.end_headers()
                self.wfile.write(first)
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()

            def log_message(self, *_):
                pass

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            f"http://127.0.0.1:{upstream.server_port}/mcp",
            ApprovalGate(lambda request: decisions.append(request.request_id) or True), "secret"))
        for server in (upstream, proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
        try:
            message = {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                       "params": {"name": "notes_write", "arguments": {"name": "once"}}}
            client.request("POST", "/mcp", json.dumps(message).encode(),
                           {"Authorization": "Bearer secret", "Content-Type": "application/json"})
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
            self.assertEqual(response.read(len(first)), first)
            with self.assertRaises(http.client.IncompleteRead) as interrupted:
                response.read()
            self.assertNotIn(b'"result"', interrupted.exception.partial)
            self.assertEqual(received, [message])
            self.assertEqual(decisions, [9])
        finally:
            client.close()
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()

    def test_authorization_allow_block_and_streaming(self):
        received = []
        class Upstream(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append((json.loads(body), self.headers.get("Authorization"),
                                 self.headers.get("Mcp-Protocol-Version"), self.headers.get("X-Hop-Request")))
                self.send_response(200)
                self.send_header("Mcp-Session-Id", "test-session")
                self.send_header("Connection", "X-Hop-Response")
                self.send_header("X-Hop-Response", "must-not-leak")
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{}}\n\n")
            def log_message(self, *_):
                pass
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
            f"http://127.0.0.1:{upstream.server_port}/mcp", ApprovalGate(lambda r: r.request_id == 1), "secret"))
        for server in (upstream, proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def post(message, token="secret"):
                req = HttpRequest(f"http://127.0.0.1:{proxy.server_port}/mcp", json.dumps(message).encode(),
                                  {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                   "Mcp-Protocol-Version": "2025-06-18"})
                return urlopen(req, timeout=5)
            allow = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "test"}}
            import http.client
            client = http.client.HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
            client.request("POST", "/mcp", json.dumps(allow).encode(), {
                "Authorization": "Bearer secret", "Content-Type": "application/json",
                "Mcp-Protocol-Version": "2025-06-18", "Connection": "X-Hop-Request",
                "X-Hop-Request": "must-not-leak"})
            approved = client.getresponse()
            self.assertEqual(approved.headers["Mcp-Session-Id"], "test-session")
            self.assertIsNone(approved.headers.get("X-Hop-Response"))
            self.assertIn(b"event: message", approved.read())
            client.close()
            self.assertEqual(json.loads(post(dict(allow, id=2)).read())["error"]["code"], DENIED)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0][1:], (None, "2025-06-18", None))
            from urllib.error import HTTPError
            with self.assertRaises(HTTPError) as error:
                post(allow, "wrong")
            self.assertEqual(error.exception.code, 401)
        finally:
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
