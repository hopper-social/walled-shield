"""Operator commands: quickstart, setup, test, renew, status, wrap/unwrap, service, uninstall.

Everything here is interactive, user-facing glue around the core modules. Each command
prints what it changed and how to undo it; nothing is deleted without ``--purge``.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import desktop_config
from .daemon_client import DaemonUnavailable, call as daemon_call
from .paths import ensure_private_dir, state_dir, write_private_file
from .telegram_approver import BotAPI, TelegramError, pair_private_chat
from .telegram_session import ApprovalSession, SessionError


def _names(value: str | None) -> set[str] | None:
    return {item.strip() for item in value.split(",") if item.strip()} if value else None


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        print(f"{question} [Y/n] y")
        return True
    if not sys.stdin.isatty():
        return False
    answer = input(f"{question} [Y/n] ").strip().lower()
    return answer in ("", "y", "yes")


def _credentials_present() -> bool:
    from .telegram_launcher import CredentialError, credential_directory, load_credentials
    try:
        load_credentials(credential_directory())
        return True
    except CredentialError:
        return False


def _wait_for_daemon(seconds: float = 20) -> dict[str, Any] | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = _daemon_status()
        if state is not None:
            return state
        time.sleep(0.5)
    return None


def _daemon_status() -> dict[str, Any] | None:
    try:
        return daemon_call({"op": "status"}, timeout=3)
    except DaemonUnavailable:
        return None


def setup(show_next_steps: bool = True) -> int:
    if _daemon_status() is not None:
        print("The approval daemon is running. Stop it first (one bot poller at a time):\n"
              "  mcp-approval-gate uninstall-service   (or stop the 'mcp-approval-gate daemon' process)")
        return 1
    directory = ensure_private_dir(state_dir())
    print("MCP Approval Gate — Telegram setup\n")
    print("1. In Telegram open @BotFather, send /newbot, and copy the token it gives you.")
    print("   Use a NEW bot dedicated to this gate.\n")
    token = os.environ.get("MCP_TELEGRAM_BOT_TOKEN") or getpass.getpass("Bot token (hidden): ").strip()
    try:
        me = BotAPI(token).call("getMe", {})
    except (ValueError, TelegramError) as exc:
        print(f"Token rejected: {exc}")
        return 1
    username = me.get("username") if isinstance(me, dict) else None
    print(f"\n2. Open a private chat with @{username or 'your bot'} and send the command below.")
    try:
        chat_id, user_id = pair_private_chat(token)
    except TelegramError as exc:
        print(f"Pairing failed: {exc}")
        return 1
    pairing_file = directory / "telegram-pairing.json"
    pairing = {"chat_id": chat_id, "user_id": user_id}
    try:
        previous = json.loads(pairing_file.read_text(encoding="utf-8"))
        if previous.get("user_id") == user_id and isinstance(previous.get("alert_chat_id"), int):
            pairing["alert_chat_id"] = previous["alert_chat_id"]
    except (OSError, ValueError, AttributeError):
        pass
    write_private_file(directory / "telegram-bot-token", token + "\n")
    write_private_file(pairing_file, json.dumps(pairing) + "\n")
    try:
        ApprovalSession(directory / "telegram-session.json", chat_id, user_id).activate()
    except SessionError as exc:
        print(f"Could not start the approval session: {exc}")
        return 1
    print(f"\n✅ Paired. Credentials saved privately in {directory}")
    if not show_next_steps:
        return 0
    print("\nNext steps:")
    print("  mcp-approval-gate install-service   # run the gate in the background at login")
    print("  mcp-approval-gate wrap-desktop      # guard every Claude Desktop MCP server")
    print("  Quit and reopen Claude Desktop.")
    return 0


def renew() -> int:
    from .approval_daemon import new_pair_code
    if _daemon_status() is None:
        print("Approval daemon is not running. Start it with 'mcp-approval-gate install-service' "
              "(or 'mcp-approval-gate daemon'), then run renew again.")
        return 1
    code = new_pair_code()
    print(f"Send this to your gate bot in the paired private chat within 2 minutes:\n\n  /pair {code}\n")
    try:
        ok = daemon_call({"op": "renew", "code": code}, timeout=130).get("ok") is True
    except DaemonUnavailable as exc:
        print(f"Renewal failed: {exc}")
        return 1
    print("✅ Approvals are ON for 24 hours." if ok else "Timed out; approvals unchanged.")
    return 0 if ok else 1


def status(config: str | None = None) -> int:
    state = _daemon_status()
    if state is None:
        print("Daemon:    NOT RUNNING — guarded servers will refuse to start (fail closed)")
    else:
        expires = state.get("session_expires_at")
        session = (f"ON ({int((expires - time.time()) / 60)} min left)" if expires else
                   "OFF — run 'mcp-approval-gate renew'")
        print(f"Daemon:    running, Telegram {'reachable' if state.get('healthy') else 'UNREACHABLE'}")
        print(f"Approvals: {session}")
        print(f"Waiting:   {state.get('pending', 0)}   Always-allow rules: {state.get('rules', 0)}")
    path = Path(config) if config else desktop_config.default_config_path()
    if not path.exists():
        print(f"Claude Desktop config not found at {path}")
        return 0
    print(f"\nClaude Desktop servers ({path}):")
    for name, value in desktop_config.status(path).items():
        print(f"  {'🔒' if value == 'guarded' else '⚠️ '} {name}: {value}")
    return 0


def wrap(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else desktop_config.default_config_path()
    if not path.exists():
        print(f"Claude Desktop config not found: {path}\nAdd your MCP servers in Claude Desktop "
              "(Settings → Developer → Edit Config) first, or pass --config.")
        return 1
    report, data = desktop_config.wrap(path, only=_names(args.only), exclude=_names(args.exclude),
                                       approver=args.approver, strict=args.strict, dry_run=args.dry_run)
    for name in report.wrapped:
        print(f"🔒 guarded   {name}")
    for name in report.already:
        print(f"✓  already   {name}")
    for name, why in report.skipped.items():
        print(f"⚠️  skipped   {name}: {why}")
    if args.dry_run:
        print("\n(dry run) resulting mcpServers:\n" + json.dumps(data.get("mcpServers", {}), indent=2))
    elif report.changed:
        print(f"\nBackup: {report.backup}\nQuit and reopen Claude Desktop to apply.")
        if args.approver == "daemon" and _daemon_status() is None:
            print("Tip: run 'mcp-approval-gate install-service' so the gate is always running.")
    else:
        print("\nNothing to change.")
    return 0


def unwrap(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else desktop_config.default_config_path()
    report, data = desktop_config.unwrap(path, only=_names(args.only), dry_run=args.dry_run)
    for name in report.unwrapped:
        print(f"restored  {name}")
    for name, why in report.skipped.items():
        print(f"skipped   {name}: {why}")
    if report.changed and not args.dry_run:
        print(f"\nBackup: {report.backup}\nQuit and reopen Claude Desktop to apply.")
    return 0


def quickstart(args: argparse.Namespace) -> int:
    """Telegram pairing → background service → guard Claude Desktop → live test approval."""
    if not (sys.platform == "darwin" or sys.platform.startswith("linux")):
        print("quickstart supports macOS and Linux. On other systems use '--approver popup' "
              "(see the README).")
        return 1
    print("━━ MCP Approval Gate quickstart ━━\n")
    print("Step 1/4 · Telegram")
    if _credentials_present():
        print("  ✓ Telegram bot already paired")
    else:
        code = setup(show_next_steps=False)
        if code != 0:
            return code
    print("\nStep 2/4 · Background service")
    state = _daemon_status()
    if state is not None:
        print("  ✓ Approval daemon already running")
    elif _confirm("  Run the approval daemon in the background at login?", args.yes):
        from . import service
        print("  " + service.install())
        state = _wait_for_daemon()
        if state is None:
            where = (str(state_dir() / "daemon.log") if sys.platform == "darwin"
                     else "journalctl --user -u mcp-approval-gate")
            print(f"  ✗ Daemon did not come up. Check the log: {where}")
            return 1
        print("  ✓ Daemon running")
    else:
        print("  Skipped. Guarded servers will start the daemon on demand (--autostart).")
    if state is not None and not state.get("session_active"):
        print("  Approvals are OFF (session expired).")
        if _confirm("  Turn them on now?", args.yes) and renew() != 0:
            return 1
    print("\nStep 3/4 · Guard Claude Desktop")
    path = Path(args.config) if args.config else desktop_config.default_config_path()
    if not path.exists():
        print(f"  No Claude Desktop config at {path}.\n  Add MCP servers in Claude Desktop "
              "(Settings → Developer → Edit Config), then run: mcp-approval-gate wrap-desktop")
    else:
        plan, _ = desktop_config.wrap(path, dry_run=True)
        for name in plan.wrapped:
            print(f"  • will guard  {name}")
        for name in plan.already:
            print(f"  ✓ guarded     {name}")
        for name, why in plan.skipped.items():
            print(f"  ⚠ skipped     {name}: {why}")
        if plan.wrapped and _confirm(f"  Guard {len(plan.wrapped)} server(s) now? (a backup is kept)", args.yes):
            report, _ = desktop_config.wrap(path)
            print(f"  ✓ Done. Backup: {report.backup}")
            print("  ➜ Quit and reopen Claude Desktop to apply.")
    print("\nStep 4/4 · Test")
    if state is not None and _confirm("  Send a test approval to your phone now?", args.yes):
        test_approval()
    print("\nAll set. Check any time with: mcp-approval-gate status")
    return 0


def test_approval() -> int:
    """Round-trip a harmless sample request through the daemon and the user's phone."""
    if _daemon_status() is None:
        print("Approval daemon is not running. Start it with: mcp-approval-gate install-service")
        return 1
    print("  Sent. Open Telegram and tap Allow or Block…")
    try:
        reply = daemon_call({"op": "approve", "server": "mcp-approval-gate self-test",
                             "method": "tools/call",
                             "params": {"name": "self_test", "arguments": {
                                 "message": "This is a test. Tap Allow or Block."}}}, timeout=300)
    except DaemonUnavailable as exc:
        print(f"  ✗ Test failed: {exc}")
        return 1
    reason = reply.get("reason")
    if reply.get("decision") == "allow":
        print("  ✓ You tapped Allow — the gate would have forwarded the request.")
    elif reason == "session ended":
        print("  ✗ Approvals are OFF. Run: mcp-approval-gate renew")
        return 1
    elif reason == "deny":
        print("  ✓ You tapped Block — the gate would have refused the request.")
    else:
        print(f"  ✓ No tap ({reason}) — the gate blocked it, as it should.")
    return 0


def uninstall(args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else desktop_config.default_config_path()
    if path.exists():
        report, _ = desktop_config.unwrap(path)
        print(f"Restored {len(report.unwrapped)} Claude Desktop server(s)"
              + (f"; backup: {report.backup}" if report.backup else ""))
    from . import service
    try:
        print(service.uninstall())
    except OSError as exc:
        print(f"Service: {exc}")
    if args.purge:
        directory = state_dir()
        if directory.exists() and not directory.is_symlink():
            shutil.rmtree(directory)
            print(f"Deleted credentials and history in {directory}")
        print("Also revoke the bot in Telegram: @BotFather → /revoke (or /deletebot).")
    print("Quit and reopen Claude Desktop. To remove the program itself, delete its install "
          "folder (or 'pipx uninstall mcp-approval-gate').")
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        if args.mode == "quickstart":
            return quickstart(args)
        if args.mode == "test":
            return test_approval()
        if args.mode == "uninstall":
            return uninstall(args)
        if args.mode == "setup":
            return setup()
        if args.mode == "renew":
            return renew()
        if args.mode == "status":
            return status(args.config)
        if args.mode == "wrap-desktop":
            return wrap(args)
        if args.mode == "unwrap-desktop":
            return unwrap(args)
        from . import service
        print(service.install() if args.mode == "install-service" else service.uninstall())
        return 0
    except (OSError, ValueError) as exc:
        logging.error("%s failed: %s", args.mode, exc)
        return 1
    except KeyboardInterrupt:
        print()
        return 130
