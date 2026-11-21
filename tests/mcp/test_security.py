"""Security properties, each stated as the attack it defends against.

Run alone with:  python -m unittest tests.test_security -v
"""

import io
import json
import os
import pathlib
import random
import socket
import stat
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "mcp"))
from mcp_approval_gate import desktop_config
from mcp_approval_gate.approval_daemon import MAX_LINE, serve
from mcp_approval_gate.daemon_client import DaemonApprover
from mcp_approval_gate.gate import CONTROL_METHODS, ApprovalGate, Request, decode_message
from mcp_approval_gate.paths import ensure_private_dir, write_private_file
from mcp_approval_gate.request_format import REDACTED, TELEGRAM_MAX_TEXT, approval_card, redact
from mcp_approval_gate.stdio_proxy import serve as serve_stdio
from test_daemon import CHAT, DaemonHarness, tap, wait_for, write


class SecretRedaction(unittest.TestCase):
    """Attack: secrets in tool arguments leak to Telegram's servers."""

    LEAKS = {
        "password key": {"password": "hunter2"},
        "nested token": {"config": {"auth": {"refresh_token": "r-123456"}}},
        "api key header name": {"X-Api-Key": "k-987654"},
        "aws secret": {"aws_secret_access_key": "wJalrXUtnFEMIK7MDENG"},
        "generic secret_key": {"secret_key": "abcdef"},
        "private key field": {"private_key": "pk"},
        "connection string field": {"connection_string": "Server=x;Password=y"},
        "anthropic key in text": {"note": "use sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv now"},
        "openai key in list": {"keys": ["sk-proj-1234567890abcdefghij"]},
        "github token": {"cmd": "git clone https://ghp_abcdefghijklmnopqrstuvwxyz0123@github.com/x"},
        "slack token": {"t": "xoxb-1234567890-abcdefghij"},
        "aws access key id": {"t": "AKIAIOSFODNN7EXAMPLE"},
        "google key": {"t": "AIzaSyA1234567890abcdefghijklmnopqrstu"},
        "telegram bot token": {"t": "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"},
        "jwt": {"t": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"},
        "bearer header": {"headers": "Authorization: Bearer abcdefghijklmnop"},
        "url credentials": {"url": "postgres://admin:s3cretpw@db.internal:5432/app"},
        "query token": {"url": "https://api.example.com/hook?token=QWERTY123&user=me"},
        "query api_key": {"url": "https://maps.example.com/x?api_key=ZXCVBN987"},
        "inline password": {"sql": "CONNECT user=app password=letmein99;"},
        "cookie header": {"raw": "Cookie: sessionid=abc123xyz"},
        "pem private key": {"key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"},
    }
    SECRET_FRAGMENTS = ["hunter2", "r-123456", "k-987654", "wJalrXUtnFEMIK7MDENG", "abcdef\"",
                        "Password=y", "AbCdEfGhIjKlMnOp", "1234567890abcdefghij", "ghp_abcdef",
                        "xoxb-1234567890", "AKIAIOSFODNN7EXAMPLE", "AIzaSyA123", "AAHdqTcvCH1v",
                        "dozjgNryP4J3", "abcdefghijklmnop", "s3cretpw", "QWERTY123", "ZXCVBN987",
                        "letmein99", "abc123xyz", "MIIEpAIBAAKCAQEA"]

    def test_every_secret_form_is_redacted(self):
        for label, arguments in self.LEAKS.items():
            with self.subTest(label):
                rendered = json.dumps(redact(arguments), ensure_ascii=False)
                self.assertIn(REDACTED, rendered)
                for fragment in self.SECRET_FRAGMENTS:
                    self.assertNotIn(fragment, rendered, f"{label} leaked {fragment!r}")

    def test_ordinary_values_are_left_readable(self):
        ordinary = {"path": "/Users/me/report.txt", "city": "Kanpur", "sort_key": "date",
                    "token_count": 120, "content": "Meeting at 5pm", "url": "https://example.com/page?id=7"}
        self.assertEqual(redact(ordinary), ordinary)

    def test_card_shows_redaction_not_secret(self):
        text, _ = approval_card("db", "tools/call", {"name": "query", "arguments": {
            "dsn": "postgres://u:p4ss@h/db", "sql": "select 1"}}, 120)
        self.assertNotIn("p4ss", text)
        self.assertIn("select 1", text)


class CardDelivery(unittest.TestCase):
    """Attack/bug: an argument that cannot be shown must never become a silent denial."""

    HOSTILE = {
        "html heavy": "<div>&amp;</div>" * 2000,
        "quotes": '"\'' * 5000,
        "emoji": "🔐" * 5000,
        "nested": {"a": [{"b": "<" * 300}] * 40},
        "binary-ish": "".join(chr(c) for c in range(32, 2000)) * 3,
    }

    def test_card_always_fits_telegram_limit(self):
        for label, content in self.HOSTILE.items():
            with self.subTest(label):
                text, truncated = approval_card("s" * 500, "tools/call", {
                    "name": "t" * 500, "arguments": {"content": content}}, 120)
                self.assertLessEqual(len(text), TELEGRAM_MAX_TEXT)
                self.assertTrue(truncated)
                self.assertIn("Preview shortened", text)
                self.assertEqual(text.count("<pre>"), text.count("</pre>"))

    def test_html_in_arguments_cannot_inject_markup(self):
        text, _ = approval_card("srv", "tools/call", {"name": "<b>x</b>", "arguments": {
            "a": "</pre><a href='https://evil'>tap</a>"}}, 120)
        self.assertNotIn("<a href", text)
        self.assertNotIn("<b>x</b>", text)

    def test_large_html_write_reaches_the_phone(self):
        harness = DaemonHarness()
        try:
            pool = ThreadPoolExecutor(1)
            future = pool.submit(harness.client.decide, Request("files", "tools/call", {
                "name": "write_file", "arguments": {"path": "page.html", "content": "<p>&</p>" * 3000}},
                1, threading.Event()))
            message_id, prompt = wait_for(lambda: harness.api.prompts() and harness.api.prompts()[-1])
            tap(harness.remote, message_id, prompt, "a")
            self.assertTrue(future.result(5))
            pool.shutdown()
        finally:
            harness.close()

    def test_deny_truncated_blocks_without_asking(self):
        harness = DaemonHarness(deny_truncated=True)
        try:
            allowed = harness.client.decide(Request("files", "tools/call", {
                "name": "write_file", "arguments": {"content": "x" * 10000}}, 1, threading.Event()))
            self.assertFalse(allowed)
            self.assertEqual(harness.api.prompts(), [])
            self.assertIn("Blocked automatically", harness.api.sent[-1]["text"])
        finally:
            harness.close()


class Impersonation(unittest.TestCase):
    """Attack: someone other than the paired person approves, or an old tap is replayed."""

    def setUp(self):
        self.harness = DaemonHarness(timeout=0.8)

    def tearDown(self):
        self.harness.close()

    def attempt(self, mutate):
        pool = ThreadPoolExecutor(1)
        self.addCleanup(pool.shutdown)
        future = pool.submit(self.harness.client.decide, write("x.txt"))
        message_id, prompt = wait_for(lambda: self.harness.api.prompts() and self.harness.api.prompts()[-1])
        data = prompt["reply_markup"]["inline_keyboard"][0][1]["callback_data"]  # the Allow button
        callback = {"id": "cb", "data": data, "from": {"id": CHAT},
                    "message": {"message_id": message_id, "chat": {"id": CHAT, "type": "private"}}}
        mutate(callback)
        self.harness.remote._process(callback)
        return future.result(5)

    def test_forged_taps_are_ignored(self):
        forgeries = {
            "other user": lambda c: c["from"].update(id=999),
            "group chat": lambda c: c["message"]["chat"].update(type="group"),
            "other chat": lambda c: c["message"]["chat"].update(id=999),
            "other message": lambda c: c["message"].update(message_id=4242),
            "tampered nonce": lambda c: c.update(data=c["data"][:-1] + ("0" if c["data"][-1] != "0" else "1")),
            "unknown action": lambda c: c.update(data="z:" + c["data"][2:]),
            "always without offer": lambda c: c.update(data="x" + c["data"][1:]),
        }
        for label, mutate in forgeries.items():
            with self.subTest(label):
                self.assertFalse(self.attempt(mutate), f"{label} was accepted")

    def test_replayed_tap_cannot_approve_a_new_request(self):
        pool = ThreadPoolExecutor(2)
        self.addCleanup(pool.shutdown)
        first = pool.submit(self.harness.client.decide, write("1.txt"))
        message_id, prompt = wait_for(lambda: self.harness.api.prompts() and self.harness.api.prompts()[-1])
        tap(self.harness.remote, message_id, prompt, "a")
        self.assertTrue(first.result(5))
        second = pool.submit(self.harness.client.decide, write("2.txt"))
        wait_for(lambda: len(self.harness.api.prompts()) == 2)
        tap(self.harness.remote, message_id, prompt, "a")  # replay the first card's button
        self.assertFalse(second.result(5))

    def test_always_allow_is_scoped_to_one_server(self):
        self.harness.daemon.rules.add(("files", "write_file"))
        self.assertTrue(self.harness.client.decide(write("ok.txt")))
        other = Request("other-server", "tools/call", {"name": "write_file", "arguments": {}}, 2, threading.Event())
        self.assertFalse(self.harness.client.decide(other))  # asked, unanswered → blocked


class LocalSocket(unittest.TestCase):
    """Attack: another local user, a hijacker or a flood abuses the daemon socket."""

    def test_socket_is_owner_only_and_cannot_be_hijacked(self):
        harness = DaemonHarness()
        try:
            self.assertEqual(stat.S_IMODE(harness.path.stat().st_mode), 0o600)
            with self.assertRaises(OSError):  # a second daemon must not steal a live socket
                serve(harness.daemon, harness.path, threading.Event())
        finally:
            harness.close()

    def raw(self, path, payload: bytes) -> dict:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(15)
        conn.connect(str(path))
        try:
            conn.sendall(payload)
            reply = b""
            while not reply.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                reply += chunk
            return json.loads(reply or b"{}")
        finally:
            conn.close()

    def test_malformed_and_oversized_requests_are_denied(self):
        harness = DaemonHarness()
        try:
            for label, payload in {
                "garbage": b"\xff\xfe not json\n",
                "array": b"[1,2,3]\n",
                "missing server": b'{"op":"approve","method":"tools/call"}\n',
                "oversized": b'{"op":"approve","pad":"' + b"x" * (MAX_LINE + 10) + b'"}\n',
            }.items():
                with self.subTest(label):
                    self.assertEqual(self.raw(harness.path, payload).get("decision"), "deny")
            self.assertEqual(harness.api.prompts(), [])
        finally:
            harness.close()

    def test_connection_flood_is_capped_and_fails_closed(self):
        harness = DaemonHarness()
        harness.stop.set()
        harness.thread.join(3)
        stop = threading.Event()
        path = pathlib.Path(harness.dir.name) / "capped.sock"
        thread = threading.Thread(target=serve, args=(harness.daemon, path, stop, 2), daemon=True)
        thread.start()
        wait_for(path.exists)
        holders = []
        try:
            for _ in range(2):  # occupy every slot with idle connections
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                conn.connect(str(path))
                holders.append(conn)
            time.sleep(0.2)
            started = time.monotonic()
            self.assertFalse(DaemonApprover(path, timeout=10).decide(write("flood.txt")))
            self.assertLess(time.monotonic() - started, 5)
        finally:
            for conn in holders:
                conn.close()
            stop.set()
            thread.join(3)
            harness.close()


class PrivateFiles(unittest.TestCase):
    """Attack: other users read credentials, or a symlink redirects writes."""

    def test_state_directory_must_be_private_and_real(self):
        with TemporaryDirectory() as base:
            loose = pathlib.Path(base, "loose")
            loose.mkdir(mode=0o755)
            os.chmod(loose, 0o755)
            with self.assertRaises(OSError):
                ensure_private_dir(loose)
            target = pathlib.Path(base, "target")
            target.mkdir(mode=0o700)
            link = pathlib.Path(base, "link")
            link.symlink_to(target)
            with self.assertRaises(OSError):
                ensure_private_dir(link)
            fresh = ensure_private_dir(pathlib.Path(base, "fresh"))
            self.assertEqual(stat.S_IMODE(fresh.stat().st_mode), 0o700)

    def test_secret_files_are_written_0600_atomically(self):
        with TemporaryDirectory() as base:
            os.chmod(base, 0o700)
            path = pathlib.Path(base, "telegram-bot-token")
            write_private_file(path, "old")
            write_private_file(path, "new")
            self.assertEqual(path.read_text(), "new")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(sorted(p.name for p in pathlib.Path(base).iterdir()), ["telegram-bot-token"])

    def test_wrapping_keeps_claude_config_permissions(self):
        with TemporaryDirectory() as base:
            path = pathlib.Path(base, "claude_desktop_config.json")
            path.write_text(json.dumps({"mcpServers": {"s": {"command": "x", "env": {"API_KEY": "k"}}}}))
            os.chmod(path, 0o600)
            report, _ = desktop_config.wrap(path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(report.backup.stat().st_mode), 0o600)


class ChildEnvironment(unittest.TestCase):
    """Attack: the wrapped MCP server reads the gate's own credentials from its environment."""

    def test_gate_secrets_are_not_inherited(self):
        probe = ("import json,os,sys\n"
                 "sys.stdin.readline()\n"
                 "names=['MCP_TELEGRAM_BOT_TOKEN','MCP_APPROVAL_TOKEN','MCP_UPSTREAM_TOKEN','MCP_TELEGRAM_USER_ID']\n"
                 "print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'seen':[n for n in names if n in os.environ],"
                 "'other':os.environ.get('UNRELATED_SETTING')}}),flush=True)\n")
        secrets = {"MCP_TELEGRAM_BOT_TOKEN": "1:x", "MCP_APPROVAL_TOKEN": "a", "MCP_UPSTREAM_TOKEN": "u",
                   "MCP_TELEGRAM_USER_ID": "5", "UNRELATED_SETTING": "kept"}
        reader, writer = os.pipe()
        output = io.BytesIO()
        with patch.dict(os.environ, secrets):
            with os.fdopen(writer, "wb") as stream:
                stream.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
            with os.fdopen(reader, "rb") as stream:
                serve_stdio([sys.executable, "-c", probe], ApprovalGate(lambda _: False), "probe",
                            input_stream=stream, output_stream=output, stop=threading.Event())
        result = json.loads(output.getvalue().splitlines()[0])["result"]
        self.assertEqual(result, {"seen": [], "other": "kept"})


class GateFuzz(unittest.TestCase):
    """Attack: a crafted message slips an action past the gate while the human says no."""

    METHODS = ["tools/call", "Tools/Call", "tools/call ", " tools/call", "tools/call\u0000",
               "tools/list", "TOOLS/LIST", "resources/read", "prompts/get", "initialize", "ping",
               "notifications/initialized", "notifications/cancelled", "sampling/createMessage",
               "completion/complete", "", "x" * 300, "tools/list/../call", "logging/setLevel"]
    IDS = [1, 0, -1, 2**53, "a", "", None, True, 1.5, [], {}, "1" * 300]

    def random_message(self, rng):
        message = {"jsonrpc": rng.choice(["2.0", "1.0", 2])}
        if rng.random() < 0.95:
            message["method"] = rng.choice(self.METHODS + [rng.randint(0, 9), None])
        if rng.random() < 0.8:
            message["id"] = rng.choice(self.IDS)
        if rng.random() < 0.7:
            message["params"] = rng.choice([{}, {"name": "write_file", "arguments": {"p": "x"}},
                                            [], "s", None, {"name": ["nested"]}])
        if rng.random() < 0.05:
            return [message, dict(message)]  # batch
        return message

    def test_no_action_is_forwarded_when_every_decision_is_block(self):
        asked = []
        gate = ApprovalGate(lambda request: asked.append(request.method) or False)
        rng = random.Random(20261120)
        for _ in range(3000):
            message = self.random_message(rng)
            forwarded, _ = gate.evaluate(message, "fuzz")
            for item in (forwarded if isinstance(forwarded, list) else [forwarded] if forwarded else []):
                self.assertIsInstance(item, dict)
                method = item.get("method")
                if method is None:
                    continue  # client→server responses (no method) are relayed by design
                if "id" in item:
                    self.assertIn(method, CONTROL_METHODS, f"action forwarded: {item!r}")
                else:
                    self.assertTrue(isinstance(method, str) and method.startswith("notifications/"),
                                    f"non-notification forwarded without id: {item!r}")
        self.assertTrue(asked, "fuzzer never reached a decision point")

    def test_strict_mode_holds_even_control_methods(self):
        gate = ApprovalGate(lambda _: False, strict=True)
        for method in sorted(CONTROL_METHODS):
            forwarded, denied = gate.evaluate({"jsonrpc": "2.0", "id": 1, "method": method}, "s")
            self.assertIsNone(forwarded, method)
            self.assertEqual(denied["error"]["code"], -32001)

    def test_decoder_rejects_ambiguous_json_instead_of_guessing(self):
        rng = random.Random(7)
        for raw in [b'{"id":1,"id":2}', b'{"n":NaN}', b'{"n":Infinity}', b'{"n":' + b"9" * 2000 + b"}",
                    b'{"s":"\\ud800"}', b"[" * 5000 + b"]" * 5000] + [rng.randbytes(64) for _ in range(300)]:
            with self.subTest(raw=raw[:30]):
                try:
                    decode_message(raw)
                except (ValueError, RecursionError):
                    pass  # rejected: the gate answers with an error and forwards nothing


if __name__ == "__main__":
    unittest.main()
