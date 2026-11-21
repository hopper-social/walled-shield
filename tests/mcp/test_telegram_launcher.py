import json
import pathlib
import sys
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mcp_approval_gate.telegram_launcher import CredentialError, load_credentials, main
from mcp_approval_gate.telegram_session import ApprovalSession
from mcp_approval_gate.main import main as proxy_main


class TelegramLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name) / "credentials"
        self.directory.mkdir(mode=0o700)
        self.token = self.directory / "telegram-bot-token"
        self.pairing = self.directory / "telegram-pairing.json"
        self.token.write_text("fake-private-token\n")
        self.pairing.write_text(json.dumps({"chat_id": 123, "user_id": 456}))
        self.token.chmod(0o600)
        self.pairing.chmod(0o600)

    def test_private_credentials_are_loaded_without_printing(self):
        self.assertEqual(load_credentials(self.directory), {
            "MCP_TELEGRAM_BOT_TOKEN": "fake-private-token",
            "MCP_TELEGRAM_CHAT_ID": "123", "MCP_TELEGRAM_USER_ID": "456"})

    def test_missing_insecure_symlink_and_invalid_credentials_fail_closed(self):
        for path in (self.token, self.pairing):
            path.chmod(0o644)
            with self.assertRaises(CredentialError):
                load_credentials(self.directory)
            path.chmod(0o600)
        self.pairing.write_text('{"chat_id": true, "user_id": 456}')
        with self.assertRaises(CredentialError):
            load_credentials(self.directory)
        self.pairing.write_text('{"chat_id": 123, "user_id": 456}')
        self.token.unlink()
        self.token.symlink_to(self.pairing)
        with self.assertRaises(CredentialError):
            load_credentials(self.directory)
        self.token.unlink()
        self.token.write_text("fake-private-token")
        self.token.chmod(0o600)
        self.directory.chmod(0o755)
        with self.assertRaises(CredentialError):
            load_credentials(self.directory)

    def test_optional_alert_destination_is_private_and_validated(self):
        self.pairing.write_text(json.dumps({"chat_id": 123, "user_id": 456, "alert_chat_id": -789}))
        self.assertEqual(load_credentials(self.directory)["MCP_TELEGRAM_ALERT_CHAT_ID"], "-789")
        for invalid in (123, True, "-789", 0):
            self.pairing.write_text(json.dumps({"chat_id": 123, "user_id": 456,
                                                 "alert_chat_id": invalid}))
            with self.assertRaises(CredentialError):
                load_credentials(self.directory)

    def test_missing_pairing_does_not_start_server(self):
        self.pairing.unlink()
        with patch("mcp_approval_gate.telegram_launcher.Path.home", return_value=pathlib.Path(self.temp.name)), \
             patch("mcp_approval_gate.telegram_launcher.os.execve") as execute, \
             patch("mcp_approval_gate.telegram_launcher.sys.stderr") as stderr:
            self.assertEqual(main(["stdio", "--approver", "telegram", "--", "server"]), 1)
            execute.assert_not_called()
            self.assertNotIn("fake-private-token", "".join(str(c) for c in stderr.method_calls))

    def test_pair_alert_uses_private_credential_launcher(self):
        root = pathlib.Path(self.temp.name)
        private = root / "Library" / "Application Support" / "MCPApprovalGate"
        private.parent.mkdir(parents=True)
        self.directory.rename(private)
        with patch("mcp_approval_gate.telegram_launcher.Path.home", return_value=root), \
             patch("mcp_approval_gate.telegram_launcher.os.execve") as execute:
            self.assertEqual(main(["pair-alert"]), 0)
        self.assertEqual(execute.call_args.args[1][-1], "pair-alert")
        self.assertEqual(execute.call_args.args[2]["MCP_TELEGRAM_USER_ID"], "456")

    def test_one_private_authentication_renews_24_hour_session(self):
        path = self.directory / "telegram-session.json"
        environment = {"MCP_TELEGRAM_CHAT_ID": "123", "MCP_TELEGRAM_USER_ID": "456",
                       "MCP_TELEGRAM_BOT_TOKEN": "test",
                       "MCP_TELEGRAM_SESSION_FILE": str(path)}
        with patch.dict("mcp_approval_gate.main.os.environ", environment), \
             patch("mcp_approval_gate.main.pair_private_chat", return_value=(999, 456)):
            self.assertEqual(proxy_main(["pair-session"]), 1)
            self.assertFalse(path.exists())
        with patch.dict("mcp_approval_gate.main.os.environ", environment), \
             patch("mcp_approval_gate.main.pair_private_chat", return_value=(123, 456)):
            self.assertEqual(proxy_main(["pair-session"]), 0)
        self.assertTrue(ApprovalSession(path, 123, 456).active())
        with patch.dict("mcp_approval_gate.main.os.environ", environment):
            self.assertEqual(proxy_main(["pairout"]), 0)
        self.assertFalse(ApprovalSession(path, 123, 456).active())

    def test_exec_preserves_arguments_and_never_puts_token_on_command_line(self):
        root = pathlib.Path(self.temp.name)
        private = root / "Library" / "Application Support" / "MCPApprovalGate"
        private.parent.mkdir(parents=True)
        self.directory.rename(private)
        args = ["stdio", "--server", "notes", "--approver", "telegram", "--", "python3", "notes.py"]
        with patch("mcp_approval_gate.telegram_launcher.Path.home", return_value=root), \
             patch("mcp_approval_gate.telegram_launcher.os.execve") as execute, \
             patch.dict("mcp_approval_gate.telegram_launcher.os.environ", {"MCP_TELEGRAM_ALERT_CHAT_ID": "-999"}):
            self.assertEqual(main(args), 0)
        binary, argv, env = execute.call_args.args
        self.assertEqual(argv[1:3], ["-m", "mcp_approval_gate"])  # runs the installed package
        self.assertEqual(argv[3:], args)
        self.assertNotIn("fake-private-token", " ".join(argv))
        self.assertEqual(env["MCP_TELEGRAM_BOT_TOKEN"], "fake-private-token")
        self.assertNotIn("MCP_TELEGRAM_ALERT_CHAT_ID", env)
        self.assertEqual(binary, sys.executable)


if __name__ == "__main__":
    unittest.main()
