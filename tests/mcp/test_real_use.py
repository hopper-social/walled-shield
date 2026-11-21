"""Behavioral tests using an actual file-writing MCP tool, not an echo mock."""

import io
import json
import pathlib
import queue
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request as HttpRequest, urlopen

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mcp_approval_gate.examples.notes_server import call_tool
from mcp_approval_gate.gate import ApprovalGate, DENIED
from mcp_approval_gate.http_proxy import handler_for
from mcp_approval_gate.stdio_proxy import serve

ROOT = pathlib.Path(__file__).resolve().parents[2]


class RealUseTests(unittest.TestCase):
    def test_approved_write_persists_and_blocked_write_has_no_side_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder)
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "integration", "version": "1.0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": {"name": "approved", "text": "Real saved note"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": {"name": "blocked", "text": "Never saved"}}},
            ]
            input_stream = io.BytesIO(b"".join((json.dumps(item) + "\n").encode() for item in messages))
            output_stream = io.BytesIO()
            connected = threading.Event()
            notifications = []
            def on_connected():
                notifications.append("ready")
                connected.set()
            serve([sys.executable, "-u", str(ROOT / "src" / "mcp_approval_gate" / "examples" / "notes_server.py"),
                   "--directory", folder],
                  ApprovalGate(lambda request: request.request_id != 4), "private notes",
                  input_stream, output_stream, on_connected=on_connected)
            self.assertTrue(connected.wait(2))
            self.assertEqual(notifications, ["ready"])
            replies = {item["id"]: item for item in
                       (json.loads(line) for line in output_stream.getvalue().splitlines())}
            self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "approval-gate-notes")
            self.assertEqual({tool["name"] for tool in replies[2]["result"]["tools"]},
                             {"notes_write", "notes_read"})
            self.assertEqual(replies[3]["result"]["content"][0]["text"], "Note saved")
            self.assertEqual(replies[4]["error"]["code"], DENIED)
            self.assertEqual((directory / "approved.txt").read_text(), "Real saved note")
            self.assertFalse((directory / "blocked.txt").exists())

    def test_overlapping_stdio_writes_keep_decisions_and_side_effects_separate(self):
        class Input:
            def __init__(self):
                self.lines = queue.Queue()

            def readline(self, _limit):
                return self.lines.get(timeout=5)

        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            directory = pathlib.Path(folder)
            inbound = Input()
            outbound = io.BytesIO()
            entered = {name: threading.Event() for name in ("allow", "block")}
            release = {name: threading.Event() for name in entered}

            def decide(request):
                name = request.parameters["arguments"]["name"]
                entered[name].set()
                self.assertTrue(release[name].wait(3))
                return name == "allow"

            worker = threading.Thread(target=serve, args=(
                [sys.executable, "-u", str(ROOT / "src" / "mcp_approval_gate" / "examples" / "notes_server.py"),
                 "--directory", folder], ApprovalGate(decide), "notes", inbound, outbound))
            worker.start()
            try:
                for request_id, name in ((1, "allow"), (2, "block")):
                    inbound.lines.put((json.dumps({
                        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                        "params": {"name": "notes_write", "arguments": {
                            "name": name, "text": name + " body"}},
                    }) + "\n").encode())
                self.assertTrue(all(event.wait(3) for event in entered.values()))
                release["block"].set()
                self.assertFalse((directory / "block.txt").exists())
                release["allow"].set()
                inbound.lines.put(b"")
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                replies = {item["id"]: item for item in
                           (json.loads(line) for line in outbound.getvalue().splitlines())}
                self.assertEqual(set(replies), {1, 2})
                self.assertEqual(replies[1]["result"]["content"][0]["text"], "Note saved")
                self.assertEqual(replies[2]["error"]["code"], DENIED)
                self.assertEqual((directory / "allow.txt").read_text(), "allow body")
                self.assertFalse((directory / "block.txt").exists())
            finally:
                for event in release.values():
                    event.set()
                inbound.lines.put(b"")
                worker.join(timeout=5)

    def test_failed_initialize_does_not_signal_connector_ready(self):
        request = b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n'
        server = ('import sys,json; request=json.loads(sys.stdin.readline()); '
                  'print(json.dumps({"jsonrpc":"2.0","id":request["id"],'
                  '"error":{"code":-32000,"message":"Unavailable"}}),flush=True)')
        output = io.BytesIO()
        notifications = []
        serve([sys.executable, "-u", "-c", server], ApprovalGate(lambda _: True), "test",
              io.BytesIO(request), output, on_connected=lambda: notifications.append("ready"))
        self.assertIn(b'"error"', output.getvalue())
        self.assertEqual(notifications, [])

    def test_ambiguous_stdio_json_never_reaches_child_or_approval(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = pathlib.Path(folder) / "forwarded"
            server = ('import pathlib,sys\n'
                      'if sys.stdin.readline(): pathlib.Path(sys.argv[1]).touch()\n')
            lines = (
                b'{"jsonrpc":"2.0","id":1,"method":"tools/call","method":"initialize"}\n',
                b'{"jsonrpc":"2.0","id":2,"method":"tools/call",'
                b'"params":{"name":"write","name":"other"}}\n',
                b'{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"value":NaN}}\n',
                b'{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"value":1e999}}\n',
                b'{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"\\ud800"}}\n',
            )
            output = io.BytesIO()
            approvals = []
            serve([sys.executable, "-u", "-c", server, str(marker)],
                  ApprovalGate(lambda request: approvals.append(request) or True), "test",
                  io.BytesIO(b"".join(lines)), output)
            replies = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(replies), len(lines))
            self.assertTrue(all(reply["error"]["code"] == -32700 for reply in replies))
            self.assertEqual(approvals, [])
            self.assertFalse(marker.exists())

    def test_server_exit_cancels_approval_before_it_can_be_granted(self):
        class Input:
            def __init__(self):
                self.lines = queue.Queue()

            def readline(self, _limit):
                try:
                    return self.lines.get(timeout=5)
                except queue.Empty:
                    return b""

        with tempfile.TemporaryDirectory() as folder:
            exit_marker = pathlib.Path(folder) / "exit"
            server = ('import json,sys,time,pathlib\n'
                      'request=json.loads(sys.stdin.readline())\n'
                      'print(json.dumps({"jsonrpc":"2.0","id":request["id"],'
                      '"result":{"protocolVersion":"2025-06-18","capabilities":{},'
                      '"serverInfo":{"name":"test","version":"1"}}}),flush=True)\n'
                      'marker=pathlib.Path(sys.argv[1])\n'
                      'while not marker.exists(): time.sleep(.01)\n')
            inbound = Input()
            inbound.lines.put(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
            inbound.lines.put(b'{"jsonrpc":"2.0","id":2,"method":"tools/call",'
                              b'"params":{"name":"write"}}\n')
            output = io.BytesIO()
            deciding = threading.Event()
            stop = threading.Event()
            def decide(request):
                deciding.set()
                request.cancelled.wait(timeout=5)
                return True
            worker = threading.Thread(target=serve, args=(
                [sys.executable, "-u", "-c", server, str(exit_marker)],
                ApprovalGate(decide), "test", inbound, output, stop), daemon=True)
            worker.start()
            try:
                self.assertTrue(deciding.wait(3), "action did not enter approval")
                exit_marker.touch()
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive(), "server exit left approval waiting")
                replies = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(next(reply for reply in replies if reply["id"] == 2)["error"]["code"], DENIED)
            finally:
                stop.set()
                inbound.lines.put(b"")
                worker.join(timeout=6)

    def test_http_approval_controls_real_note_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder)
            received = []

            class NotesHTTP(BaseHTTPRequestHandler):
                def do_POST(self):
                    message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    received.append(message["id"])
                    params = message["params"]
                    result = call_tool(directory, params["name"], params["arguments"])
                    body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_):
                    pass

            upstream = ThreadingHTTPServer(("127.0.0.1", 0), NotesHTTP)
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
                "http://127.0.0.1:%d/mcp" % upstream.server_port,
                ApprovalGate(lambda request: request.request_id == 1), "test-token"))
            for server in (upstream, proxy):
                threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                def write(request_id, name):
                    message = {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                               "params": {"name": "notes_write", "arguments": {
                                   "name": name, "text": "Saved over HTTP"}}}
                    request = HttpRequest("http://127.0.0.1:%d/mcp" % proxy.server_port,
                                          json.dumps(message).encode(),
                                          {"Authorization": "Bearer test-token", "Content-Type": "application/json"})
                    return json.load(urlopen(request, timeout=5))

                self.assertEqual(write(1, "approved")["result"]["content"][0]["text"], "Note saved")
                self.assertEqual(write(2, "blocked")["error"]["code"], DENIED)
                self.assertEqual(received, [1])
                self.assertEqual((directory / "approved.txt").read_text(), "Saved over HTTP")
                self.assertFalse((directory / "blocked.txt").exists())
            finally:
                for server in (proxy, upstream):
                    server.shutdown()
                    server.server_close()

    def test_overlapping_http_writes_keep_decisions_and_side_effects_separate(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            directory = pathlib.Path(folder)
            received = []
            entered = {name: threading.Event() for name in ("allow", "block")}
            release = {name: threading.Event() for name in entered}

            class NotesHTTP(BaseHTTPRequestHandler):
                def do_POST(self):
                    message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    received.append(message["id"])
                    params = message["params"]
                    result = call_tool(directory, params["name"], params["arguments"])
                    body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_):
                    pass

            def decide(request):
                name = request.parameters["arguments"]["name"]
                entered[name].set()
                self.assertTrue(release[name].wait(3))
                return name == "allow"

            upstream = ThreadingHTTPServer(("127.0.0.1", 0), NotesHTTP)
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(
                f"http://127.0.0.1:{upstream.server_port}/mcp", ApprovalGate(decide), "test-token"))
            for server in (upstream, proxy):
                threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                def write(request_id, name):
                    message = {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                               "params": {"name": "notes_write", "arguments": {
                                   "name": name, "text": name + " body"}}}
                    request = HttpRequest(f"http://127.0.0.1:{proxy.server_port}/mcp",
                                          json.dumps(message).encode(),
                                          {"Authorization": "Bearer test-token"})
                    return json.load(urlopen(request, timeout=5))

                with ThreadPoolExecutor(max_workers=2) as pool:
                    allowed = pool.submit(write, 1, "allow")
                    blocked = pool.submit(write, 2, "block")
                    try:
                        self.assertTrue(all(event.wait(3) for event in entered.values()))
                        release["block"].set()
                        self.assertEqual(blocked.result(timeout=3)["error"]["code"], DENIED)
                        self.assertEqual(received, [])
                        self.assertFalse((directory / "block.txt").exists())
                        release["allow"].set()
                        self.assertEqual(allowed.result(timeout=3)["result"]["content"][0]["text"],
                                         "Note saved")
                        self.assertEqual(received, [1])
                        self.assertEqual((directory / "allow.txt").read_text(), "allow body")
                        self.assertFalse((directory / "block.txt").exists())
                    finally:
                        for event in release.values():
                            event.set()
            finally:
                for server in (proxy, upstream):
                    server.shutdown()
                    server.server_close()

    def test_notes_are_scoped_and_symlinks_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            directory = pathlib.Path(folder)
            secret = pathlib.Path(outside) / "secret.txt"
            secret.write_text("outside")
            (directory / "link.txt").symlink_to(secret)
            self.assertTrue(call_tool(directory, "notes_read", {"name": "link"})["isError"])
            self.assertTrue(call_tool(directory, "notes_write", {
                "name": "../secret", "text": "wrong"})["isError"])
            self.assertEqual(secret.read_text(), "outside")


if __name__ == "__main__":
    unittest.main()
