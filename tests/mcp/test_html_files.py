"""Scoping and end-to-end approval tests for opt-in local HTML access."""

import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mcp_approval_gate.examples.html_files import call_tool
from mcp_approval_gate.examples.notes_server import process
from mcp_approval_gate.gate import ApprovalGate, DENIED
from mcp_approval_gate.stdio_proxy import serve

ROOT = pathlib.Path(__file__).resolve().parents[2]


class HtmlToolTests(unittest.TestCase):
    def test_list_only_top_level_html_and_read_exact_utf8_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "older.html").write_text("<p>old</p>")
            newest = root / "Latest File.HTML"
            newest.write_text("<p>नया</p>")
            (root / "subfolder").mkdir()
            (root / "subfolder" / "secret.html").write_text("secret")
            (root / "not-html.txt").write_text("text")
            listed = call_tool(root, "html_list", {})["content"][0]["text"]
            self.assertTrue(listed.startswith("Latest File.HTML |"))
            self.assertIn("older.html", listed)
            self.assertNotIn("secret.html", listed)
            self.assertNotIn("not-html.txt", listed)
            read = call_tool(root, "html_read", {"filename": newest.name})
            self.assertEqual(read["content"][0]["text"], "<p>नया</p>")

    def test_escape_symlink_oversize_and_invalid_utf8_are_denied(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            safe = root / "safe"
            safe.mkdir()
            (root / "outside.html").write_text("secret")
            (safe / "linked.html").symlink_to(root / "outside.html")
            (safe / "huge.html").write_bytes(b"a" * (256 * 1024 + 1))
            (safe / "invalid.html").write_bytes(b"\xff")
            for name in ("../outside.html", "linked.html", "huge.html", "invalid.html",
                         "/etc/passwd", "safe\\outside.html", "note.txt"):
                with self.subTest(name=name):
                    self.assertTrue(call_tool(safe, "html_read", {"filename": name})["isError"])
            self.assertNotIn("linked.html", call_tool(safe, "html_list", {})["content"][0]["text"])
            self.assertTrue(call_tool(root / "outside.html", "html_list", {})["isError"])
            (root / "alias").symlink_to(safe, target_is_directory=True)
            self.assertTrue(call_tool(root / "alias", "html_list", {})["isError"])
            self.assertTrue(call_tool(safe, "html_read", {"filename": "missing.html", "extra": 1})["isError"])

    def test_html_tools_are_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            self.assertNotIn("html_read", str(process(listing, root)))
            self.assertIn("html_read", str(process(listing, root, root)))

    def test_stdio_approval_controls_read_and_blocked_read_never_reaches_server(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            notes = root / "notes"
            notes.mkdir()
            html = root / "html"
            html.mkdir()
            (html / "example.html").write_text("<main>approved</main>")
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "html_list", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "html_read", "arguments": {"filename": "example.html"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                    "name": "html_read", "arguments": {"filename": "example.html"}}},
            ]
            seen = []
            def decide(request):
                seen.append(request.parameters)
                return request.request_id != 4
            output = io.BytesIO()
            serve([sys.executable, "-u", str(ROOT / "src" / "mcp_approval_gate" / "examples" / "notes_server.py"),
                   "--directory", str(notes), "--html-directory", str(html)],
                  ApprovalGate(decide), "notes", io.BytesIO(b"".join(
                      (json.dumps(message) + "\n").encode() for message in messages)), output)
            replies = {reply["id"]: reply for reply in
                       (json.loads(line) for line in output.getvalue().splitlines())}
            self.assertIn("html_read", {tool["name"] for tool in replies[1]["result"]["tools"]})
            self.assertIn("example.html", replies[2]["result"]["content"][0]["text"])
            self.assertEqual(replies[3]["result"]["content"][0]["text"], "<main>approved</main>")
            self.assertEqual(replies[4]["error"]["code"], DENIED)
            # requests are decided concurrently, so only the set of decisions is deterministic
            self.assertEqual(sorted(item["name"] for item in seen), ["html_list", "html_read", "html_read"])
