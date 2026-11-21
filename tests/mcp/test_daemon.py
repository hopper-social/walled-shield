"""Shared approval daemon: Telegram routing, redaction, concurrency, cancellation, Desktop wrapping."""

import io
import json
import os
import pathlib
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from mcp_approval_gate import desktop_config
from mcp_approval_gate.approval_daemon import ApprovalDaemon, DaemonConfig, serve
from mcp_approval_gate.daemon_client import DaemonApprover, call
from mcp_approval_gate.gate import ApprovalGate, Request
from mcp_approval_gate.stdio_proxy import serve as serve_stdio
from mcp_approval_gate.telegram_approver import TelegramApprover
from mcp_approval_gate.telegram_session import ApprovalSession

CHAT = 123


class FakeAPI:
    def __init__(self):
        self.lock = threading.Lock()
        self.sent = []
        self.edits = []

    def call(self, method, payload, timeout=12):
        with self.lock:
            if method == "sendMessage":
                self.sent.append(payload)
                return {"message_id": len(self.sent)}
            if method in ("editMessageText", "editMessageReplyMarkup"):
                self.edits.append(payload)
                return True
        if method == "getUpdates":
            time.sleep(0.01)
            return []
        return True

    def prompts(self):
        with self.lock:
            return [(i + 1, p) for i, p in enumerate(self.sent) if "reply_markup" in p]


def tap(remote, message_id, payload, choice):
    row = payload["reply_markup"]["inline_keyboard"]
    buttons = [b for r in row for b in r]
    data = next(b["callback_data"] for b in buttons if b["callback_data"].startswith(choice + ":"))
    remote._process({"id": "cb", "data": data, "from": {"id": CHAT},
                     "message": {"message_id": message_id, "chat": {"id": CHAT, "type": "private"}}})


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class DaemonHarness:
    def __init__(self, session=None, **config):
        self.dir = TemporaryDirectory()
        self.api = FakeAPI()
        self.stop = threading.Event()
        self.remote = TelegramApprover("t", CHAT, CHAT, api=self.api, stop_event=self.stop,
                                       session=session, timeout=5)
        self.remote.start()
        self.daemon = ApprovalDaemon(self.remote, DaemonConfig(**{"timeout": 5, **config}))
        self.path = pathlib.Path(self.dir.name) / "a.sock"
        self.thread = threading.Thread(target=serve, args=(self.daemon, self.path, self.stop), daemon=True)
        self.thread.start()
        wait_for(self.path.exists)
        self.client = DaemonApprover(self.path, timeout=10)

    def close(self):
        self.stop.set()
        self.remote.stop()
        self.thread.join(3)
        self.dir.cleanup()


def write(name, text="x", rid=1):
    return Request("files", "tools/call", {"name": "write_file",
                   "arguments": {"path": name, "content": text}}, rid, threading.Event())


class DaemonTests(unittest.TestCase):
    def setUp(self):
        self.h = DaemonHarness()

    def tearDown(self):
        self.h.close()

    def decide_async(self, request):
        pool = ThreadPoolExecutor(1)
        self.addCleanup(pool.shutdown)
        return pool.submit(self.h.client.decide, request)

    def test_allow_shows_arguments_and_stamps_outcome(self):
        future = self.decide_async(write("report.txt", "hello"))
        message_id, prompt = wait_for(lambda: self.h.api.prompts() and self.h.api.prompts()[-1])
        self.assertIn("write_file", prompt["text"])
        self.assertIn("report.txt", prompt["text"])
        self.assertEqual(prompt["parse_mode"], "HTML")
        tap(self.h.remote, message_id, prompt, "a")
        self.assertTrue(future.result(5))
        edit = wait_for(lambda: self.h.api.edits and self.h.api.edits[-1])
        self.assertIn("Allowed", edit["text"])
        self.assertEqual(edit["reply_markup"], {"inline_keyboard": []})

    def test_block_and_timeout_deny(self):
        future = self.decide_async(write("a.txt"))
        message_id, prompt = wait_for(lambda: self.h.api.prompts() and self.h.api.prompts()[-1])
        tap(self.h.remote, message_id, prompt, "b")
        self.assertFalse(future.result(5))
        self.h.daemon.config.timeout = 0.3
        self.assertFalse(self.h.client.decide(write("b.txt")))
        self.assertIn("No answer", wait_for(lambda: self.h.api.edits[-1:] and
                                            "No answer" in self.h.api.edits[-1]["text"] and
                                            self.h.api.edits[-1])["text"])

    def test_secrets_are_redacted(self):
        request = Request("api", "tools/call", {"name": "call", "arguments": {
            "api_key": "abc123", "headers": {"Authorization": "Bearer abcdefghijklmnop"},
            "note": "key sk-ant-REALSECRETVALUE1234567890 here", "city": "Kanpur"}}, 1, threading.Event())
        future = self.decide_async(request)
        message_id, prompt = wait_for(lambda: self.h.api.prompts() and self.h.api.prompts()[-1])
        for secret in ("abc123", "abcdefghijklmnop", "REALSECRETVALUE"):
            self.assertNotIn(secret, prompt["text"])
        self.assertIn("Kanpur", prompt["text"])
        tap(self.h.remote, message_id, prompt, "b")
        self.assertFalse(future.result(5))

    def test_always_allow_rule_then_clear(self):
        future = self.decide_async(write("1.txt"))
        message_id, prompt = wait_for(lambda: self.h.api.prompts() and self.h.api.prompts()[-1])
        tap(self.h.remote, message_id, prompt, "w")
        self.assertTrue(future.result(5))
        before = len(self.h.api.prompts())
        self.assertTrue(self.h.client.decide(write("2.txt")))       # no new prompt
        self.assertEqual(len(self.h.api.prompts()), before)
        other = Request("files", "tools/call", {"name": "delete_file", "arguments": {}}, 3, threading.Event())
        self.h.daemon.config.timeout = 0.3
        self.assertFalse(self.h.client.decide(other))              # rule is per tool
        self.assertIn("write_file", self.h.daemon.command("/rules"))
        self.assertIn("Cleared 1", self.h.daemon.command("/clear"))
        self.assertFalse(self.h.client.decide(write("3.txt")))

    def test_concurrent_requests_resolve_independently(self):
        pool = ThreadPoolExecutor(6)
        self.addCleanup(pool.shutdown)
        futures = {i: pool.submit(self.h.client.decide, write(f"{i}.txt", rid=i)) for i in range(6)}
        prompts = wait_for(lambda: len(self.h.api.prompts()) == 6 and self.h.api.prompts())
        for message_id, prompt in prompts:
            index = int(prompt["text"].split("&quot;path&quot;: &quot;")[1].split(".txt")[0])
            tap(self.h.remote, message_id, prompt, "a" if index % 2 == 0 else "b")
        self.assertEqual({i: f.result(5) for i, f in futures.items()},
                         {i: i % 2 == 0 for i in range(6)})

    def test_cancelled_request_withdraws_prompt_and_late_tap_is_ignored(self):
        request = write("late.txt")
        future = self.decide_async(request)
        message_id, prompt = wait_for(lambda: self.h.api.prompts() and self.h.api.prompts()[-1])
        request.cancelled.set()
        self.assertFalse(future.result(5))
        wait_for(lambda: any("Cancelled" in e.get("text", "") for e in self.h.api.edits))
        tap(self.h.remote, message_id, prompt, "a")  # too late: nothing pending
        self.assertEqual(self.h.daemon.counts["allowed"], 0)

    def test_status_and_unknown_requests(self):
        self.assertTrue(call({"op": "status"}, self.h.path)["healthy"])
        self.assertEqual(call({"op": "nope"}, self.h.path)["decision"], "deny")
        self.assertEqual(call({"op": "approve", "server": ""}, self.h.path)["decision"], "deny")


class SessionTests(unittest.TestCase):
    def test_expired_session_denies_and_renew_turns_it_on(self):
        with TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            session = ApprovalSession(pathlib.Path(directory) / "s.json", CHAT, CHAT)
            session.revoke()
            h = DaemonHarness(session=session)
            try:
                self.assertFalse(h.client.decide(write("x.txt")))
                self.assertTrue(any("approvals are OFF" in p["text"] for p in h.api.sent))
                pool = ThreadPoolExecutor(1)
                renewed = pool.submit(call, {"op": "renew", "code": "abcdefghijklmnop"}, h.path, 10)
                wait_for(lambda: h.daemon.renewals)
                h.remote._process_message({"text": "/pair wrongcodewrongcode", "chat": {"id": CHAT, "type": "private"},
                                           "from": {"id": CHAT, "is_bot": False}})
                self.assertFalse(session.active())
                h.remote._process_message({"text": "/pair abcdefghijklmnop", "chat": {"id": 999, "type": "private"},
                                           "from": {"id": 999, "is_bot": False}})
                self.assertFalse(session.active())  # stranger cannot renew
                h.remote._process_message({"text": "/pair abcdefghijklmnop", "chat": {"id": CHAT, "type": "private"},
                                           "from": {"id": CHAT, "is_bot": False}})
                self.assertTrue(renewed.result(5)["ok"])
                self.assertTrue(session.active())
                h.daemon.rules.add(("files", "write_file"))
                h.remote._process_message({"text": "/pairout", "chat": {"id": CHAT, "type": "private"},
                                           "from": {"id": CHAT, "is_bot": False}})
                self.assertFalse(session.active())
                self.assertEqual(h.daemon.rules, set())
                pool.shutdown()
            finally:
                h.close()

    def test_daemon_unavailable_fails_closed(self):
        self.assertFalse(DaemonApprover(pathlib.Path("/nonexistent/x.sock")).decide(write("x")))


class EndToEndStdioTests(unittest.TestCase):
    """Real notes MCP server behind the stdio proxy, decisions made through the daemon."""

    def test_allowed_write_happens_blocked_write_never_reaches_server(self):
        h = DaemonHarness()
        notes = TemporaryDirectory()
        os.chmod(notes.name, 0o700)
        try:
            gate = ApprovalGate(h.client.decide)
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": {"name": "approved", "text": "hello"}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": {"name": "blocked", "text": "nope"}}},
            ]
            reader, writer = os.pipe()
            output = io.BytesIO()
            decided = set()

            def undecided():
                fresh = [p for p in h.api.prompts() if p[0] not in decided]
                return fresh[0] if fresh else None

            def client():  # Claude Desktop: sends requests, keeps stdin open until both answered
                with os.fdopen(writer, "wb") as stream:
                    for message in messages:
                        stream.write((json.dumps(message) + "\n").encode())
                        stream.flush()
                    for _ in range(2):  # the human on Telegram
                        message_id, prompt = wait_for(undecided, 10)
                        decided.add(message_id)
                        tap(h.remote, message_id, prompt, "a" if "approved" in prompt["text"] else "b")
                    wait_for(lambda: {2, 3} <= {json.loads(line).get("id")
                                                for line in output.getvalue().splitlines()}, 10)

            clicker = threading.Thread(target=client, daemon=True)
            clicker.start()
            stop = threading.Event()
            with os.fdopen(reader, "rb") as stream:
                serve_stdio([sys.executable, str(ROOT / "src/mcp_approval_gate/examples/notes_server.py"), "--directory", notes.name],
                            gate, "private notes", input_stream=stream, output_stream=output, stop=stop)
            clicker.join(5)
            replies = {r["id"]: r for r in map(json.loads, output.getvalue().splitlines()) if "id" in r}
            self.assertNotIn("error", replies[2])
            self.assertEqual(replies[3]["error"]["code"], -32001)
            self.assertEqual(pathlib.Path(notes.name, "approved.txt").read_text(), "hello")
            self.assertFalse(pathlib.Path(notes.name, "blocked.txt").exists())
        finally:
            notes.cleanup()
            h.close()


class DesktopConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.path = pathlib.Path(self.dir.name) / "claude_desktop_config.json"
        self.original = {"globalShortcut": "x", "mcpServers": {
            "files": {"command": "npx", "args": ["-y", "server-filesystem", "/tmp"], "env": {"A": "1"}},
            "remote": {"url": "https://example.com/mcp"},
            "bare": {"command": "/usr/local/bin/srv"}}}
        self.path.write_text(json.dumps(self.original))

    def tearDown(self):
        self.dir.cleanup()

    def test_wrap_is_complete_idempotent_and_reversible(self):
        report, _ = desktop_config.wrap(self.path, python="/usr/bin/python3")
        self.assertEqual(sorted(report.wrapped), ["bare", "files"])
        self.assertIn("remote", report.skipped)
        self.assertTrue(report.backup.exists())
        data = json.loads(self.path.read_text())
        files = data["mcpServers"]["files"]
        self.assertEqual(files["command"], "/usr/bin/python3")
        self.assertEqual(files["env"], {"A": "1"})
        self.assertEqual(files["args"][files["args"].index("--") + 1:], ["npx", "-y", "server-filesystem", "/tmp"])
        self.assertIn("daemon", files["args"])
        self.assertEqual(data["globalShortcut"], "x")
        self.assertEqual(desktop_config.status(self.path)["files"], "guarded")
        again, _ = desktop_config.wrap(self.path)
        self.assertFalse(again.changed)
        desktop_config.unwrap(self.path)
        self.assertEqual(json.loads(self.path.read_text()), self.original)

    def test_only_exclude_and_dry_run(self):
        report, data = desktop_config.wrap(self.path, only={"files"}, dry_run=True)
        self.assertEqual(report.wrapped, ["files"])
        self.assertEqual(json.loads(self.path.read_text()), self.original)  # untouched
        report, _ = desktop_config.wrap(self.path, exclude={"files"})
        self.assertEqual(report.wrapped, ["bare"])
        self.assertEqual(desktop_config.status(self.path)["files"], "UNGUARDED")


if __name__ == "__main__":
    unittest.main()
