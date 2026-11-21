"""Command-line interface: proxies (stdio/http), the approval daemon, and operator commands.

Nothing is captured transparently: an MCP server is guarded only when it is launched
through this program (``wrap-desktop`` does that for every Claude Desktop server).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
from typing import TYPE_CHECKING, Any, Callable

from . import __version__
from .activity import ActivityStore
from .gate import ApprovalGate
from .telegram_approver import TelegramApprover, TelegramError, pair_alert_chat, pair_private_chat
from .telegram_session import ApprovalSession, SessionError, session_path
from .stdio_proxy import serve as serve_stdio
from .http_proxy import BoundedHTTPServer, handler_for
from .gate import Request
from .paths import socket_path

if TYPE_CHECKING:
    from .popup import PopupApprover

DAEMON_HELP = "ask the shared background daemon (one Telegram bot for every guarded server)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-approval-gate",
        description="Approve or block every Claude MCP tool call from Telegram or a local popup.",
        epilog="First time? Run: mcp-approval-gate quickstart")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    modes = parser.add_subparsers(dest="mode", required=True, metavar="COMMAND")
    quick = modes.add_parser("quickstart", help="guided one-time setup: Telegram, background service, "
                             "guard every Claude Desktop server, send a test approval")
    quick.add_argument("--yes", "-y", action="store_true", help="accept every step without asking")
    quick.add_argument("--config", help="path to claude_desktop_config.json")
    modes.add_parser("test", help="send a sample approval to your phone and report what you tapped")
    remove = modes.add_parser("uninstall", help="restore Claude Desktop, remove the service "
                              "(and with --purge, the saved credentials)")
    remove.add_argument("--purge", action="store_true", help="also delete the bot token, pairing and history")
    remove.add_argument("--config", help="path to claude_desktop_config.json")
    stdio = modes.add_parser("stdio", help="wrap a local Claude Desktop MCP server")
    stdio.add_argument("--server", required=True, help="friendly name displayed in prompt")
    stdio.add_argument("--strict", action="store_true", help="ask about setup/discovery requests too")
    stdio.add_argument("--approver", choices=("popup", "telegram", "daemon"), default="popup",
                       help="popup: local window; telegram: this process polls the bot; daemon: " + DAEMON_HELP)
    stdio.add_argument("--autostart", action="store_true", help="start the approval daemon if it is not running")
    stdio.add_argument("command", nargs=argparse.REMAINDER, help="-- followed by server executable and args")
    http = modes.add_parser("http", help="reverse proxy a remote Streamable HTTP MCP server")
    http.add_argument("--upstream", required=True, help="full upstream /mcp endpoint URL")
    http.add_argument("--host", default="127.0.0.1")
    http.add_argument("--port", type=int, default=8765)
    http.add_argument("--strict", action="store_true", help="ask about setup/discovery requests too")
    http.add_argument("--approver", choices=("popup", "telegram", "daemon"), default="popup")
    http.add_argument("--autostart", action="store_true", help="start the approval daemon if it is not running")
    activity = modes.add_parser("activity", help="show recent local connection and decision history")
    activity.add_argument("--limit", type=int, default=30)
    modes.add_parser("permissions", help="explain permissions that MCP approval cannot grant")
    modes.add_parser("pair-telegram", help="pair one private Telegram chat using a local one-time code")
    modes.add_parser("pair-alert", help="verify a separate Telegram alert group/channel using a one-time code")
    modes.add_parser("pair-session", help="authorize private Telegram approvals for 24 hours")
    modes.add_parser("pairout", help="end the Telegram approval session locally; alerts remain active")
    daemon = modes.add_parser("daemon", help="run the shared Telegram approval service (foreground)")
    daemon.add_argument("--timeout", type=float, default=float(os.environ.get("MCP_GATE_TIMEOUT", "120")),
                        help="seconds to wait for a Telegram answer before blocking (default 120)")
    daemon.add_argument("--hide-inputs", action="store_true",
                        default=os.environ.get("MCP_GATE_HIDE_INPUTS") == "1",
                        help="never send argument values to Telegram (metadata only)")
    daemon.add_argument("--deny-truncated", action="store_true",
                        default=os.environ.get("MCP_GATE_DENY_TRUNCATED") == "1",
                        help="auto-block requests too large to preview in full")
    modes.add_parser("setup", help="interactive first-time setup: bot token, pairing, session")
    modes.add_parser("renew", help="turn Telegram approvals on for 24h via the running daemon")
    status_cmd = modes.add_parser("status", help="show daemon health and which Claude Desktop servers are guarded")
    status_cmd.add_argument("--config", help="path to claude_desktop_config.json")
    wrap = modes.add_parser("wrap-desktop", help="route every Claude Desktop MCP server through the gate")
    wrap.add_argument("--config", help="path to claude_desktop_config.json")
    wrap.add_argument("--only", help="comma-separated server names to wrap")
    wrap.add_argument("--exclude", help="comma-separated server names to leave alone")
    wrap.add_argument("--approver", choices=("daemon", "telegram", "popup"), default="daemon")
    wrap.add_argument("--strict", action="store_true", help="also ask for setup/discovery requests")
    wrap.add_argument("--dry-run", action="store_true")
    unwrap = modes.add_parser("unwrap-desktop", help="restore the original Claude Desktop server entries")
    unwrap.add_argument("--config")
    unwrap.add_argument("--only")
    unwrap.add_argument("--dry-run", action="store_true")
    modes.add_parser("install-service", help="run the daemon at login (launchd / systemd --user)")
    modes.add_parser("uninstall-service", help="remove the login service")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s: %(message)s")
    if args.mode in ("quickstart", "test", "uninstall", "setup", "renew", "status", "wrap-desktop",
                     "unwrap-desktop", "install-service", "uninstall-service"):
        from . import cli_admin
        return cli_admin.run(args)
    if args.mode == "daemon":
        return run_daemon(args)
    if args.mode == "permissions":
        print("Outside MCP approval: Claude's screen/file-sharing prompt is controlled in Claude.\n"
              "macOS Screen Recording and other system permissions must be granted or denied "
              "in macOS Privacy & Security.\n"
              "This proxy cannot grant, dismiss, or reliably detect those prompts. It only "
              "approves configured MCP requests.\n"
              "Telegram approval, when configured, also cannot override either permission.")
        return 0
    if args.mode == "pair-telegram":
        try:
            chat_id, user_id = pair_private_chat(os.environ.get("MCP_TELEGRAM_BOT_TOKEN", ""))
            ApprovalSession(session_path(), chat_id, user_id).activate()
        except (ValueError, TelegramError, SessionError) as exc:
            logging.error("Telegram pairing failed: %s", exc)
            return 1
        print(f"Private chat paired. Set MCP_TELEGRAM_CHAT_ID={chat_id} and MCP_TELEGRAM_USER_ID={user_id} "
              "in Claude's guarded server environment. Keep the bot token private.")
        return 0
    if args.mode in ("pair-session", "pairout"):
        try:
            chat_id = int(os.environ.get("MCP_TELEGRAM_CHAT_ID", ""))
            user_id = int(os.environ.get("MCP_TELEGRAM_USER_ID", ""))
            session = ApprovalSession(session_path(), chat_id, user_id)
            if args.mode == "pair-session":
                paired = pair_private_chat(os.environ.get("MCP_TELEGRAM_BOT_TOKEN", ""))
                if paired != (chat_id, user_id):
                    raise TelegramError("Only the configured private chat can renew approvals")
                session.activate()
                print("Approval session paired for 24 hours. Alerts use their separate destination.")
            else:
                session.revoke()
                print("Approval session ended. Alerts remain active.")
            return 0
        except (ValueError, TelegramError, SessionError) as exc:
            logging.error("Telegram approval session unavailable: %s", exc)
            return 1
    if args.mode == "pair-alert":
        try:
            alert_id = pair_alert_chat(os.environ.get("MCP_TELEGRAM_BOT_TOKEN", ""),
                                       int(os.environ.get("MCP_TELEGRAM_USER_ID", "")))
        except (ValueError, TelegramError) as exc:
            logging.error("Telegram alert pairing failed: %s", exc)
            return 1
        print(f"Alert destination verified. Set alert_chat_id to {alert_id} "
              "in the private telegram-pairing.json file, then restart the guarded connector.")
        return 0
    try:
        history = ActivityStore()
    except (OSError, ValueError) as exc:
        logging.error("Private activity history unavailable: %s", exc)
        return 1
    if args.mode == "activity":
        try:
            entries = history.recent(args.limit)
            if not entries:
                print("No MCP request activity recorded yet.")
            for at, outcome, server, tool, method in reversed(entries):
                label = {"auto_forwarded": "Setup / discovery", "allowed": "Allowed",
                         "connector_ready": "Connector alert sent"}.get(outcome, "Blocked")
                print(f"{at}  {label:22} {json.dumps(server)}: {json.dumps(tool or method)}")
        finally:
            history.close()
        return 0
    if args.mode == "stdio":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            parser.error("stdio requires -- followed by the MCP server command")
    else:
        inbound = os.environ.get("MCP_APPROVAL_TOKEN", "")
        if not inbound:
            parser.error("set MCP_APPROVAL_TOKEN to a strong, random inbound bearer token")
        if args.host not in ("127.0.0.1", "localhost", "::1"):
            parser.error("bind only to loopback; use an authenticated HTTPS gateway for remote access")
        try:
            handler_for(args.upstream, ApprovalGate(lambda _: False), inbound)
        except ValueError as exc:
            parser.error(str(exc))
    stopping = threading.Event()
    popup: PopupApprover | None = None
    remote: TelegramApprover | None = None
    decide: Callable[[Request], bool]
    if args.approver == "daemon":
        from .daemon_client import DaemonApprover, DaemonUnavailable, ensure_daemon
        try:
            ensure_daemon(args.autostart)
        except (DaemonUnavailable, OSError) as exc:
            logging.error("Approval daemon unavailable; MCP server not started: %s", exc)
            history.close()
            return 1
        decide = DaemonApprover().decide
    elif args.approver == "telegram":
        try:
            alert_chat = os.environ.get("MCP_TELEGRAM_ALERT_CHAT_ID")
            remote = TelegramApprover(os.environ.get("MCP_TELEGRAM_BOT_TOKEN", ""),
                int(os.environ.get("MCP_TELEGRAM_CHAT_ID", "")),
                int(os.environ.get("MCP_TELEGRAM_USER_ID", "")), stop_event=stopping,
                alert_chat_id=int(alert_chat) if alert_chat is not None else None,
                session=ApprovalSession(session_path(),
                    int(os.environ.get("MCP_TELEGRAM_CHAT_ID", "")),
                    int(os.environ.get("MCP_TELEGRAM_USER_ID", ""))))
            remote.start()
            remote.notify_permission_boundary(history)
        except (ValueError, TelegramError, OSError, sqlite3.Error) as exc:
            logging.error("Telegram approval unavailable; MCP server not started: %s", exc)
            if remote:
                remote.stop()
            history.close()
            return 1
        decide = remote.decide
    else:
        try:
            from .popup import PopupApprover
            popup = PopupApprover(stop_event=stopping)
        except Exception as exc:
            logging.error("Cannot create an approval window; MCP server not started: %s", exc)
            history.close()
            return 1
        decide = popup.decide
    gate = ApprovalGate(decide, strict=args.strict, activity=history,
                        on_decision=remote.notify_decision if remote else None)
    http_server: BoundedHTTPServer | None = None
    worker: threading.Thread | None = None
    if args.mode == "http":
        handler = handler_for(args.upstream, gate, inbound, os.environ.get("MCP_UPSTREAM_TOKEN", ""))
        try:
            http_server = BoundedHTTPServer((args.host, args.port), handler)
        except OSError as exc:
            logging.error("HTTP listener failed: %s", exc)
            stopping.set()
            if remote:
                remote.stop()
            if popup:
                popup.stop()
                popup.root.destroy()
            history.close()
            return 1
        threading.Thread(target=http_server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
    else:
        def connector_ready() -> None:
            if remote is not None:
                remote.notify_connector_ready()
            history.record(args.server, "initialize", None, "connector_ready")

        def run_stdio() -> None:
            try:
                serve_stdio(command, gate, args.server, stop=stopping,
                            on_connected=connector_ready if remote else None)
            except (OSError, ValueError) as exc:
                logging.error("MCP server failed: %s", exc)
            finally:
                stopping.set()
        worker = threading.Thread(target=run_stdio, daemon=True)
        worker.start()
    def stop_signal(*_: Any) -> None:
        stopping.set()
        if popup:
            popup.stop()
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    try:
        if popup:
            popup.run()
        else:
            stopping.wait()
    finally:
        stopping.set()
        if remote:
            remote.stop()
        if worker:
            worker.join(timeout=5)
        if http_server:
            http_server.shutdown()
            http_server.server_close()
        history.close()
    return 0


def run_daemon(args: argparse.Namespace) -> int:
    """Own the Telegram bot and serve approval requests on the private Unix socket."""
    import fcntl
    from .approval_daemon import ApprovalDaemon, DaemonConfig, serve as serve_daemon
    from .paths import ensure_private_dir, state_dir
    try:
        directory = ensure_private_dir(state_dir())
        lock = open(directory / "daemon.lock", "a+")
        os.chmod(directory / "daemon.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logging.info("Approval daemon already running")
            return 0
        history = ActivityStore()
    except (OSError, ValueError) as exc:
        logging.error("Private state unavailable: %s", exc)
        return 1
    stopping = threading.Event()
    if not all(os.environ.get(name) for name in ("MCP_TELEGRAM_BOT_TOKEN", "MCP_TELEGRAM_CHAT_ID",
                                                  "MCP_TELEGRAM_USER_ID")):
        logging.error("Telegram credentials missing. Run 'mcp-approval-gate setup' once, then start the daemon "
                      "with 'mcp-approval-gate daemon' or 'mcp-approval-gate install-service'.")
        history.close()
        return 1
    try:
        chat_id = int(os.environ.get("MCP_TELEGRAM_CHAT_ID", ""))
        user_id = int(os.environ.get("MCP_TELEGRAM_USER_ID", ""))
        alert_chat = os.environ.get("MCP_TELEGRAM_ALERT_CHAT_ID")
        remote = TelegramApprover(os.environ.get("MCP_TELEGRAM_BOT_TOKEN", ""), chat_id, user_id,
                                  timeout=args.timeout, stop_event=stopping,
                                  alert_chat_id=int(alert_chat) if alert_chat else None,
                                  session=ApprovalSession(session_path(), chat_id, user_id))
        remote.start()
        remote.notify_permission_boundary(history)
    except (ValueError, TelegramError, OSError, sqlite3.Error) as exc:
        logging.error("Telegram unavailable; approval daemon not started: %s", exc)
        stopping.set()
        history.close()
        return 1
    service = ApprovalDaemon(remote, DaemonConfig(timeout=args.timeout, show_inputs=not args.hide_inputs,
                                                  deny_truncated=args.deny_truncated))
    remote.send_notice("🟢 MCP Approval Gate is online. Claude's MCP actions will ask you here.\n"
                       "/help for commands.")
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    logging.info("Approval daemon listening on %s", socket_path())
    try:
        serve_daemon(service, socket_path(), stopping)
    except OSError as exc:
        logging.error("Approval daemon socket failed: %s", exc)
        return 1
    finally:
        stopping.set()
        remote.stop()
        history.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
