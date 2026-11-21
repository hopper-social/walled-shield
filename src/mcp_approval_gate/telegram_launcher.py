"""Start the Telegram MCP proxy with credentials from private local files.

No token is placed in Claude's config or on the command line. This launcher must
be readable by this user. Run it with the same arguments normally passed to the CLI:
``python -m mcp_approval_gate.telegram_launcher stdio --approver telegram -- <server>``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


class CredentialError(Exception):
    pass


def _read_private(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 4096):
                raise CredentialError("Credential file is not a private regular file")
            with os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
                return stream.read(4097)
        finally:
            os.close(fd)
    except (OSError, UnicodeError) as exc:
        raise CredentialError("Cannot read private Telegram credentials") from exc


def load_credentials(directory: Path) -> dict[str, str]:
    try:
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise CredentialError("Credential directory must be private")
        token = _read_private(directory / "telegram-bot-token").strip()
        data = json.loads(_read_private(directory / "telegram-pairing.json"))
        chat_id = data["chat_id"]
        user_id = data["user_id"]
        alert_chat_id = data.get("alert_chat_id")
        if (not token or len(token) > 4096 or type(chat_id) is not int or chat_id <= 0
                or type(user_id) is not int or user_id <= 0
                or (alert_chat_id is not None and
                    (type(alert_chat_id) is not int or alert_chat_id >= 0))):
            raise CredentialError("Invalid Telegram credentials")
        credentials = {"MCP_TELEGRAM_BOT_TOKEN": token,
                       "MCP_TELEGRAM_CHAT_ID": str(chat_id),
                       "MCP_TELEGRAM_USER_ID": str(user_id)}
        if alert_chat_id is not None:
            credentials["MCP_TELEGRAM_ALERT_CHAT_ID"] = str(alert_chat_id)
        return credentials
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CredentialError("Telegram credentials are unavailable or invalid") from exc


def credential_directory() -> Path:
    """Private state directory; falls back to the original macOS layout if only that exists."""
    from .paths import state_dir
    directory = state_dir()
    legacy = Path.home() / "Library" / "Application Support" / "MCPApprovalGate"
    if not directory.exists() and legacy.exists():
        return legacy
    return directory


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    directory = credential_directory()
    try:
        credentials = load_credentials(directory)
        options = argv[:argv.index("--")] if "--" in argv else argv
        if argv[:1] != ["daemon"] and argv not in (["pair-alert"], ["pair-session"], ["pairout"]) and not any(
                options[i:i + 2] == ["--approver", "telegram"] for i in range(len(options) - 1)):
            raise CredentialError("Configure --approver telegram before the server command")
        environment = os.environ.copy()
        environment.pop("MCP_TELEGRAM_ALERT_CHAT_ID", None)
        environment["MCP_TELEGRAM_SESSION_FILE"] = str(directory / "telegram-session.json")
        os.execve(sys.executable, [sys.executable, "-m", "mcp_approval_gate", *argv], {**environment, **credentials})
    except CredentialError as exc:
        print(f"Telegram MCP server not started: {exc}", file=sys.stderr)
        return 1
    return 0  # type: ignore[unreachable]  # execve never returns; reached only when mocked in tests


if __name__ == "__main__":
    raise SystemExit(main())
