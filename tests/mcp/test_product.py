"""Product-level commands: Desktop wrapping forms, self-test, uninstall, service files, CLI."""

import argparse
import contextlib
import io
import json
import os
import pathlib
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "mcp"))
from mcp_approval_gate import __version__, cli_admin, desktop_config, service
from mcp_approval_gate.main import main
from test_daemon import DaemonHarness, tap, wait_for


def write_config(directory: str, servers: dict) -> pathlib.Path:
    path = pathlib.Path(directory) / "claude_desktop_config.json"
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


class DesktopWrappingForms(unittest.TestCase):
    def test_wrapped_entry_runs_the_installed_module(self):
        with TemporaryDirectory() as directory:
            path = write_config(directory, {"files": {"command": "npx", "args": ["srv"]}})
            desktop_config.wrap(path, python="/venv/bin/python")
            entry = json.loads(path.read_text())["mcpServers"]["files"]
            self.assertEqual(entry["command"], "/venv/bin/python")
            self.assertEqual(entry["args"][:3], ["-m", "mcp_approval_gate", "stdio"])
            self.assertEqual(entry["args"][-3:], ["--", "npx", "srv"])

    def test_legacy_script_wrapping_is_upgraded_not_double_wrapped(self):
        legacy = {"command": "/usr/bin/python3", "args": [
            "/old/checkout/main.py", "stdio", "--server", "files", "--approver", "telegram",
            "--", "npx", "srv"]}
        with TemporaryDirectory() as directory:
            path = write_config(directory, {"files": legacy})
            self.assertEqual(desktop_config.status(path)["files"], "guarded")
            report, _ = desktop_config.wrap(path, python="/venv/bin/python")
            self.assertEqual(report.wrapped, ["files"])
            args = json.loads(path.read_text())["mcpServers"]["files"]["args"]
            self.assertEqual(args.count("--"), 1)
            self.assertNotIn("/old/checkout/main.py", args)
            self.assertEqual(args[-2:], ["npx", "srv"])

    def test_original_double_dash_arguments_survive_round_trip(self):
        original = {"command": "tool", "args": ["--flag", "--", "positional"]}
        with TemporaryDirectory() as directory:
            path = write_config(directory, {"t": dict(original)})
            desktop_config.wrap(path)
            desktop_config.unwrap(path)
            self.assertEqual(json.loads(path.read_text())["mcpServers"]["t"], original)


class SelfTestCommand(unittest.TestCase):
    def run_selftest(self, choice):
        harness = DaemonHarness()
        try:
            with patch.dict(os.environ, {"MCP_GATE_SOCKET": str(harness.path)}):
                pool = ThreadPoolExecutor(1)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    future = pool.submit(cli_admin.test_approval)
                    message_id, prompt = wait_for(lambda: harness.api.prompts() and harness.api.prompts()[-1])
                    self.assertIn("self_test", prompt["text"])
                    tap(harness.remote, message_id, prompt, choice)
                    self.assertEqual(future.result(10), 0)
                pool.shutdown()
            return output.getvalue()
        finally:
            harness.close()

    def test_allow_and_block_are_reported(self):
        self.assertIn("tapped Allow", self.run_selftest("a"))
        self.assertIn("tapped Block", self.run_selftest("b"))

    def test_daemon_missing_is_reported(self):
        with patch.dict(os.environ, {"MCP_GATE_SOCKET": "/nonexistent/sock"}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli_admin.test_approval(), 1)
        self.assertIn("not running", output.getvalue())


class UninstallCommand(unittest.TestCase):
    def test_restores_desktop_and_purges_only_when_asked(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as state:
            os.chmod(state, 0o700)
            original = {"files": {"command": "npx", "args": ["srv"]}}
            path = write_config(directory, original)
            desktop_config.wrap(path)
            secret = pathlib.Path(state, "telegram-bot-token")
            secret.write_text("x")
            with patch.dict(os.environ, {"MCP_GATE_STATE_DIR": state}), \
                    patch.object(service, "uninstall", return_value="service removed"), \
                    contextlib.redirect_stdout(io.StringIO()):
                cli_admin.uninstall(argparse.Namespace(config=str(path), purge=False))
                self.assertTrue(secret.exists())
                self.assertEqual(json.loads(path.read_text())["mcpServers"], original)
                cli_admin.uninstall(argparse.Namespace(config=str(path), purge=True))
            self.assertFalse(pathlib.Path(state).exists())


class ServiceDefinitions(unittest.TestCase):
    def test_service_runs_the_unified_walled_service(self):
        # Walled Shield: one background process (Telegram, MCP approvals, browser alerts, vault).
        plist = service._plist("/venv/bin/python")
        self.assertIn("<string>-m</string><string>walled</string><string>service</string>", plist)
        self.assertIn("<key>KeepAlive</key><true/>", plist)
        unit = service._unit("/path with space/python")
        self.assertIn("ExecStart='/path with space/python' -m walled service", unit)
        self.assertIn("Restart=always", unit)


class CommandLine(unittest.TestCase):
    def test_version_and_status(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            main(["--version"])
        self.assertIn(__version__, output.getvalue())
        with TemporaryDirectory() as directory:
            path = write_config(directory, {"a": {"command": "x"}, "r": {"url": "https://e"}})
            with patch.dict(os.environ, {"MCP_GATE_SOCKET": "/nonexistent/sock"}), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["status", "--config", str(path)]), 0)
        text = output.getvalue()
        self.assertIn("NOT RUNNING", text)
        self.assertIn("a: UNGUARDED", text)
        self.assertIn("r: remote", text)


if __name__ == "__main__":
    unittest.main()
