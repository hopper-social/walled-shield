"""Protocol and filesystem boundaries of the opt-in local notes server."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mcp_approval_gate.examples.notes_server import call_tool, process

ROOT = pathlib.Path(__file__).resolve().parents[2]


class NotesServerTests(unittest.TestCase):
    def test_invalid_requests_are_rejected_without_executing_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = pathlib.Path(folder)
            cases = [
                ({"jsonrpc": "1.0", "id": 1, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": {"name": "bad", "text": "bad"}}}, -32600),
                ({"jsonrpc": "2.0", "id": True, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": {"name": "bad", "text": "bad"}}}, -32600),
                ({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": {"name": "bad", "text": "bad"},
                    "unexpected": "value"}}, -32602),
                ({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "notes_write", "arguments": []}}, -32602),
            ]
            for message, code in cases:
                with self.subTest(message=message):
                    self.assertEqual(process(message, directory)["error"]["code"], code)
                    self.assertFalse((directory / "bad.txt").exists())
            self.assertIsNone(process({"jsonrpc": "2.0", "method": "notifications/initialized"}, directory))
            self.assertEqual(process({"jsonrpc": "2.0", "id": 4, "method": "initialize",
                                      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                 "clientInfo": {"name": "test", "version": "1"}}}, directory)
                             ["result"]["protocolVersion"], "2025-06-18")

    def test_invalid_json_returns_parse_error_and_next_request_succeeds(self):
        with tempfile.TemporaryDirectory() as folder:
            command = [sys.executable, "-u", str(ROOT / "src" / "mcp_approval_gate" / "examples" / "notes_server.py"),
                       "--directory", folder]
            inbound = (b'{invalid\n'
                       b'{"jsonrpc":"2.0","id":1,"method":"ping","method":"tools/call"}\n'
                       b'{"jsonrpc":"2.0","id":3,"method":"ping","params":{"value":NaN}}\n'
                       b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
            result = subprocess.run(command, input=inbound, capture_output=True, timeout=5, check=True)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual([reply["error"]["code"] for reply in replies[:3]], [-32700] * 3)
            self.assertTrue(all(reply["id"] is None for reply in replies[:3]))
            self.assertEqual(replies[3]["result"], {})

    def test_directory_swap_cannot_redirect_a_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            notes = root / "notes"
            notes.mkdir()
            outside = root / "outside"
            outside.mkdir()
            moved = root / "moved"
            original_fsync = os.fsync
            swapped = False

            def swap_before_replace(fd):
                nonlocal swapped
                if not swapped:
                    swapped = True
                    notes.rename(moved)
                    notes.symlink_to(outside, target_is_directory=True)
                return original_fsync(fd)

            with patch("mcp_approval_gate.examples.notes_server.os.fsync", side_effect=swap_before_replace):
                result = call_tool(notes, "notes_write", {"name": "private", "text": "safe"})
            self.assertTrue(swapped)
            self.assertFalse((outside / "private.txt").exists())
            if not result.get("isError"):
                self.assertEqual((moved / "private.txt").read_text(), "safe")
            self.assertTrue(call_tool(notes, "notes_read", {"name": "private"})["isError"])

    def test_notes_remain_private_and_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            self.assertFalse(call_tool(root, "notes_write", {"name": "a", "text": "first"}).get("isError"))
            self.assertFalse(call_tool(root, "notes_write", {"name": "a", "text": "second"}).get("isError"))
            self.assertEqual((root / "a.txt").read_text(), "second")
            self.assertEqual((root / "a.txt").stat().st_mode & 0o777, 0o600)
            self.assertEqual(sorted(item.name for item in root.iterdir()), ["a.txt"])
            for name in ("../escape", "bad/name", "", "a" * 65):
                self.assertTrue(call_tool(root, "notes_write", {"name": name, "text": "x"})["isError"])
            (root / "invalid.txt").write_bytes(b"\xff")
            (root / "oversized.txt").write_bytes(b"x" * (256 * 1024 + 1))
            outside = root.parent / (root.name + "-outside.txt")
            (root / "linked.txt").symlink_to(outside)
            for name in ("invalid", "oversized", "linked"):
                self.assertTrue(call_tool(root, "notes_read", {"name": name})["isError"])
            alias = root / "alias"
            alias.symlink_to(root, target_is_directory=True)
            self.assertTrue(call_tool(alias, "notes_write", {"name": "escape", "text": "x"})["isError"])
