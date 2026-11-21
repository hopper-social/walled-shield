"""The Walled service: one background process for the whole product.

It owns
  * the Telegram bot (the MCP approval daemon from assignment 1, when Telegram is set up),
  * the private Unix socket that MCP proxies and the browser guard's native host talk to,
  * the password vault key (in memory only, while unlocked),
  * the Control Center API on 127.0.0.1 (random port, per-start secret token).

Socket operations (one JSON line each way)
  MCP gate        approve · status · renew                  (assignment 1, unchanged)
  browser guard   ask · notify                              (Telegram Allow/Block and alerts)
  vault           vault_status · vault_find · vault_fill · vault_save
Without Telegram, approve/ask answer "deny"/unavailable and notify is a no-op, so the MCP
gate stays fail-closed and the browser guard falls back to the desktop dialog alone.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

from mcp_approval_gate import paths as gate_paths
from mcp_approval_gate.approval_daemon import ApprovalDaemon, DaemonConfig, read_line
from mcp_approval_gate.approval_daemon import serve as serve_socket
from mcp_approval_gate.telegram_approver import TelegramApprover, TelegramError
from mcp_approval_gate.telegram_launcher import CredentialError, credential_directory, load_credentials
from mcp_approval_gate.telegram_session import ApprovalSession
from walled_guard import audit, cards, config
from walled_guard.rules import strings_in

from . import __version__
from .events import EventBus
from .vault import Locked, Vault, VaultError, host_of

LOG = logging.getLogger("walled")

TEST_TITLES = {
    "dialog": "Desktop dialog appears", "telegram": "Telegram Allow / Block", "race": "First answer wins",
    "prompt_chatgpt": "ChatGPT prompt stopped", "prompt_perplexity": "Perplexity prompt stopped",
    "prompt_claude": "Claude prompt stopped", "file_pdf": "PDF upload checked", "file_image": "Screenshot read with OCR",
    "detector_card": "Card number detected", "password_save": "Password saved from a sign-up",
    "password_fill": "Login filled from the vault", "mcp": "Claude tool call approved on Telegram",
}


class WalledService:
    def __init__(self, vault_path: Path | None = None):
        settings = config.load().settings
        self.vault = Vault(vault_path or audit.state_dir() / "vault.json", settings.vault_autolock_minutes)
        self.daemon: ApprovalDaemon | None = None
        self._mcp_waiting: dict[tuple[str, str, int], threading.Event] = {}
        self.remote: TelegramApprover | None = None
        self.telegram_error: str | None = None
        self.bot_name: str | None = None
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.started = time.time()
        self.extension_seen: float | None = None         # last ping from the browser extension
        self.events = EventBus()
        self.control: Any = None                         # ControlServer, when the UI is running

    # ------------------------------------------------------------------ Telegram
    def telegram_configured(self) -> bool:
        try:
            load_credentials(credential_directory())
            return True
        except CredentialError:
            return False

    def start_telegram(self) -> bool:
        """(Re)start the bot from saved credentials. False when not configured or unreachable."""
        self.stop_telegram()
        try:
            creds = load_credentials(credential_directory())
        except CredentialError:
            self.telegram_error = None
            return False
        try:
            chat_id, user_id = int(creds["MCP_TELEGRAM_CHAT_ID"]), int(creds["MCP_TELEGRAM_USER_ID"])
            alert = creds.get("MCP_TELEGRAM_ALERT_CHAT_ID")
            stop = threading.Event()
            timeout = float(config.load().settings.dialog_timeout)
            remote = TelegramApprover(creds["MCP_TELEGRAM_BOT_TOKEN"], chat_id, user_id, timeout=timeout,
                                      stop_event=stop, alert_chat_id=int(alert) if alert else None,
                                      session=ApprovalSession(credential_directory() / "telegram-session.json",
                                                              chat_id, user_id))
            remote.start()
            me = remote.api.call("getMe", {})
            self.bot_name = me.get("username") if isinstance(me, dict) else None
        except (ValueError, KeyError, TelegramError, OSError) as exc:
            self.telegram_error = f"Telegram unavailable: {exc}"
            LOG.warning(self.telegram_error)
            return False
        daemon = ApprovalDaemon(remote, DaemonConfig(timeout=timeout))
        daemon.inspect = self.inspect_mcp
        daemon.on_approval = self.mcp_approval_event
        daemon.extra_commands = self.telegram_command
        with self.lock:
            self.remote, self.daemon, self.telegram_error = remote, daemon, None
        self.events.publish("telegram.online", f"Telegram connected: @{self.bot_name or 'your bot'}", tone="ok")
        remote.send_notice("🟢 <b>Walled Shield is online</b>\nClaude MCP actions, sensitive-data alerts and "
                           "password fills will reach you here. /help for commands.", parse_mode="HTML")
        return True

    def stop_telegram(self) -> None:
        with self.lock:
            remote, self.remote, self.daemon = self.remote, None, None
        if remote is not None:
            remote.stop()

    def telegram_state(self) -> dict[str, Any]:
        with self.lock:
            remote = self.remote
        expires = remote.session.expires_at() if remote and remote.session else None
        return {"configured": self.telegram_configured(), "running": remote is not None and remote.healthy,
                "session_active": bool(remote and remote._authorized()), "session_expires_at": expires,
                "bot": self.bot_name, "error": self.telegram_error}

    def telegram_command(self, text: str) -> str | None:
        name = text.split()[0].split("@")[0].lower()
        if name in ("/start", "/help"):
            return ("🛡 Walled Shield\n\n"
                    "/status – service, approvals and session\n"
                    "/guard – recent sensitive-data events in your browser\n"
                    "/vault – password vault: locked or unlocked\n"
                    "/lock – lock the password vault now\n"
                    "/rules – MCP tools you set to always allow\n"
                    "/clear – remove those always-allow rules\n"
                    "/pairout – turn approvals off now (everything that needs you is blocked)")
        parts = text.split()
        if name == "/start" and len(parts) == 2 and self.daemon is not None:
            renewed = self.daemon._renewal_text("/pair " + parts[1])        # deep-link renewal
            if renewed:
                return renewed
        if name == "/lock":
            self.vault.lock()
            self.events.publish("vault.locked", "Vault locked from Telegram", tone="warn")
            return "🔒 Password vault locked. Unlock it again from the Walled app on your computer."
        if name == "/vault":
            st = self.vault.status()
            if not st["exists"]:
                return "No password vault yet. Create one in the Walled app."
            return "🔓 Vault unlocked" if st["unlocked"] else "🔒 Vault locked"
        if name == "/guard":
            events = audit.recent(8)
            if not events:
                return "No sensitive-data events yet."
            lines = []
            for e in reversed(events):
                rules = e.get("rules") or []
                lines.append(f"• {str(e.get('time', ''))[5:16]} {e.get('site')} {e.get('kind')} → {e.get('decision')}"
                             + (f" ({', '.join(map(str, rules[:2]))})" if rules else ""))
            return "Recent browser events:\n" + "\n".join(lines)
        return None

    # ------------------------------------------------------------------ Pip on the desktop
    def pet_state(self) -> dict[str, Any]:
        from . import pet
        settings = config.load().settings
        return {"running": config.pet_running(), "available": pet.available(), "settings": settings.pet,
                "in_browser": config.pet_for_browser(settings)["in_browser"]}

    def pet_action(self, action: str) -> dict[str, Any]:
        from . import pet
        if action == "show":
            cfg = config.load()
            if not cfg.settings.pet["enabled"] or cfg.settings.pet["where"] == "browser":
                cfg.settings.pet.update(enabled=True, where="auto" if cfg.settings.pet["where"] == "browser" else cfg.settings.pet["where"])
                config.save(cfg)
            pet.spawn()
        elif action == "hide":
            pet.stop()
        else:
            from .app import spawn_app
            spawn_app()
        time.sleep(0.3)
        return self.pet_state()

    def maybe_start_pet(self) -> None:
        """At service start: Pip moves into the screen corner if the user hasn't turned him off."""
        from . import pet
        p = config.load().settings.pet
        if p["enabled"] and p["where"] in ("auto", "desktop", "both") and not os.environ.get("WALLED_NO_PET"):
            pet.spawn()

    # ------------------------------------------------------------------ MCP integration
    def mcp_approval_event(self, phase: str, server: str, method: str, allowed: bool, reason: str) -> None:
        """Claude tool calls on the event stream. "Waiting" only appears if a human is actually
        being asked (instant automatic answers stay quiet)."""
        key = (server, method, threading.get_ident())
        if phase == "asking":
            done = threading.Event()
            self._mcp_waiting[key] = done

            def later() -> None:
                if not done.wait(1.5):
                    self.events.publish("claude.waiting", f"Claude is waiting: {server} · {method}",
                                        "Answer Allow or Block on Telegram.", "warn", server=server, method=method)
            threading.Thread(target=later, daemon=True).start()
            return
        waiting = self._mcp_waiting.pop(key, None)
        quick = waiting is not None and not waiting.is_set()
        if waiting is not None:
            waiting.set()
        if quick and allowed and reason == "auto":                 # "always allow" rule: routine
            return
        self.events.publish("claude.decision", f"{'Allowed' if allowed else 'Blocked'}: {server} · {method}", reason,
                            "ok" if allowed else "bad", server=server, method=method, allowed=allowed)

    def inspect_mcp(self, server: str, method: str, params: object) -> list[tuple[str, str, str]]:
        cfg = config.load()
        if not cfg.settings.mcp_rules:
            return []
        engine = cfg.engine()
        strings = strings_in(params)
        hits = engine.scan("\n".join(strings), "extracted", surface="mcp") + engine.scan_paths(strings)
        seen: set[str] = set()
        out = []
        for h in hits:
            if h.rule_id not in seen:
                seen.add(h.rule_id)
                out.append((h.name, h.sample, h.action))
        return out

    # ------------------------------------------------------------------ socket
    def handle_connection(self, conn: socket.socket) -> None:
        try:
            message = json.loads(read_line(conn, time.monotonic() + 10))
            if not isinstance(message, dict):
                raise ValueError("object expected")
            reply = self.handle_local(conn, message)
            if reply is not None:
                ApprovalDaemon._send(conn, reply)
        except Exception as exc:
            LOG.debug("client error: %s", type(exc).__name__)
            try:
                ApprovalDaemon._send(conn, {"ok": False, "decision": "deny", "reason": "invalid request"})
            except OSError:
                pass

    def handle_local(self, conn: socket.socket, message: dict[str, Any]) -> dict[str, Any] | None:
        op = message.get("op")
        if isinstance(op, str) and op.startswith("vault_"):
            return self.vault_op(op, message)
        with self.lock:
            daemon = self.daemon
        if op == "events":
            since = message.get("since", 0)
            wait = message.get("timeout", 25)
            found = self.events.since(int(since) if isinstance(since, int) else 0,
                                      float(wait) if isinstance(wait, (int, float)) else 25.0)
            return {"ok": True, "events": [e.to_json() for e in found], "last": self.events.last_id()}
        if op == "activity":
            self.record_activity(message)
            return {"ok": True}
        if op == "ticket":
            if self.control is None:
                return {"ok": False, "error": "Control Center not running"}
            return {"ok": True, "url": f"http://127.0.0.1:{self.control.port}/", "ticket": self.control.new_ticket()}
        if op == "pair_token":
            if self.control is None:
                return {"ok": False, "error": "Open the Walled app first."}
            try:
                return {"ok": True, **self.control.start_pairing(str(message.get("token") or ""), wait=True)}
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        if op == "status":
            if message.get("client") == "extension":
                if self.extension_seen is None:
                    self.events.publish("extension.connected", "Browser extension connected", tone="ok")
                self.extension_seen = time.time()
            base = daemon.status() if daemon else {"ok": True, "healthy": False, "session_active": False}
            return {**base, "service": "walled", "version": __version__, "telegram": daemon is not None,
                    "vault": self.vault.status()}
        if daemon is None:
            if op == "notify":
                return {"ok": False, "reason": "telegram not set up"}
            return {"decision": "deny", "reason": "telegram not set up"}   # approve / ask / renew
        daemon.handle_message(conn, message)          # answers on the socket itself
        return None

    # ------------------------------------------------------------------ vault (native host)
    def vault_op(self, op: str, message: dict[str, Any]) -> dict[str, Any]:
        try:
            if op == "vault_status":
                return {"ok": True, **self.vault.status()}
            origin = str(message.get("origin") or "")
            if op == "vault_find":
                return {"ok": True, "entries": [{"id": e.id, "username": e.username}
                                                for e in self.vault.find(origin)]}
            if op == "vault_save":
                entry = self.vault.save_entry(origin, str(message.get("username") or ""),
                                              str(message.get("password") or ""))
                self._notify(f"🔐 Saved a password for <code>{entry.origin}</code> in your vault.")
                self.events.publish("vault.saved", f"Password saved for {entry.origin}", tone="ok")
                self.mark_test("password_save", True, entry.origin)
                return {"ok": True, "id": entry.id}
            if op == "vault_fill":
                entry = self.vault.get(str(message.get("entry") or ""))
                if not host_of(origin) or not (host_of(origin) == entry.origin
                                               or host_of(origin).endswith("." + entry.origin)):
                    return {"ok": False, "error": "this login belongs to a different site"}
                if not self._approve_fill(entry.origin, entry.username):
                    return {"ok": False, "error": "Fill not approved on Telegram."}
                entry = self.vault.get(entry.id)            # may have been locked meanwhile (/lock)
                self.events.publish("vault.filled", f"Password filled on {entry.origin}", tone="ok")
                self.mark_test("password_fill", True, entry.origin)
                return {"ok": True, "username": entry.username, "password": entry.password}
        except Locked:
            return {"ok": False, "locked": True, "error": "Vault is locked. Unlock it in the Walled app."}
        except VaultError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": "unknown vault request"}

    def _approve_fill(self, host: str, username: str) -> bool:
        """Second factor for a password fill: Telegram tap, else a desktop dialog.

        The socket is reachable by any program running as you; an unlocked vault alone must
        not hand out passwords silently, so "none" is an explicit opt-out in Settings.
        """
        mode = config.load().settings.fill_approval
        if mode == "none":
            return True
        with self.lock:
            daemon = self.daemon
        if mode == "phone" and daemon is not None and daemon.approver._authorized():
            allowed, _ = daemon._ask_text(cards.fill_card(host, username), threading.Event(), 120)
            return allowed
        from walled_guard import dialogs
        return dialogs.ask(dialogs.Request("fill", host, [username])) == dialogs.CONTINUE

    # ------------------------------------------------------------------ activity & tests
    def record_activity(self, message: dict[str, Any]) -> None:
        """A browser-guard decision (from the native host): publish it and mirror it to the
        Telegram activity channel. Only rule names, site, kind and outcome travel."""
        site = str(message.get("site", ""))[:40]
        kind = str(message.get("kind", ""))[:10]
        decision = str(message.get("decision", ""))[:20]
        rules = [str(r)[:60] for r in (message.get("rules") or [])][:6]
        kinds = [str(k)[:10] for k in (message.get("kinds") or [])][:6]
        via = str(message.get("via", ""))[:10]
        stopped = decision in ("cancel", "block")
        tone = "bad" if stopped else "warn" if decision == "warn" else "ok"
        verb = {"cancel": "Stopped", "block": "Blocked", "continue": "Sent after you confirmed",
                "warn": "Sent with a warning", "allow": "Allowed"}.get(decision, decision)
        self.events.publish("guard.decision", f"{verb}: {kind} on {site}", ", ".join(rules), tone,
                            site=site, kind=kind, decision=decision, via=via, rules=rules, kinds=kinds)
        if site == "ChatGPT" and kind == "prompt":
            self.mark_test("prompt_chatgpt", True, verb)
        if site == "Perplexity" and kind == "prompt":
            self.mark_test("prompt_perplexity", True, verb)
        if site == "Claude" and kind == "prompt":
            self.mark_test("prompt_claude", True, verb)
        if kind == "file" and "pdf" in kinds:
            self.mark_test("file_pdf", True, site)
        if kind == "file" and "image" in kinds:
            self.mark_test("file_image", True, site)
        if "Payment card number" in rules:
            self.mark_test("detector_card", True, site)
        if via == "phone":
            self.mark_test("race", True, "answered on the phone")
        if config.load().settings.activity_channel:
            icon = "⛔" if stopped else "⚠️" if decision == "warn" else "✅"
            self.activity_message(f"{icon} <b>{verb}</b> · {kind} on {site}"
                                  + (f"\n{', '.join(rules)}" if rules else "") + (f"\n<i>via {via}</i>" if via else ""))

    def activity_message(self, text: str) -> None:
        """Send to the separate activity channel (never to the approvals chat)."""
        with self.lock:
            remote = self.remote
        if remote is None or remote.alert_chat_id == remote.chat_id:
            return
        try:
            remote.api.call("sendMessage", {"chat_id": remote.alert_chat_id, "text": text[:4000],
                                            "parse_mode": "HTML", "protect_content": True, "disable_notification": True},
                            timeout=10)
        except Exception:
            LOG.debug("activity channel unavailable")

    def mark_test(self, test_id: str, ok: bool, detail: str = "") -> None:
        cfg = config.load()
        previous = cfg.settings.tests.get(test_id)
        cfg.settings.tests[test_id] = {"ok": ok, "at": time.time(), "detail": detail[:120]}
        config.save(cfg)
        if ok and not (previous and previous.get("ok")):
            self.events.publish("test.passed", f"Test passed: {TEST_TITLES.get(test_id, test_id)}", detail, "ok",
                                test=test_id)

    def _notify(self, text: str) -> None:
        with self.lock:
            remote = self.remote
        if remote is not None:
            remote.send_notice(text, parse_mode="HTML")

    # ------------------------------------------------------------------ run
    def run(self, with_ui: bool = True) -> int:
        import fcntl
        directory = gate_paths.ensure_private_dir(gate_paths.state_dir())
        lock = open(directory / "daemon.lock", "a+")
        os.chmod(directory / "daemon.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            LOG.info("Walled service already running")
            return 0
        self.heal()
        self.start_telegram()
        server = None
        if with_ui:
            from .control import ControlServer
            server = ControlServer(self)
            server.start()
            self.control = server
            threading.Timer(2.0, self.maybe_start_pet).start()
        signal.signal(signal.SIGTERM, lambda *_: self.stopping.set())
        signal.signal(signal.SIGINT, lambda *_: self.stopping.set())
        threading.Thread(target=self._autolock_loop, daemon=True).start()
        LOG.info("Walled service %s listening on %s", __version__, gate_paths.socket_path())
        try:
            serve_socket(self, gate_paths.socket_path(), self.stopping)   # type: ignore[arg-type]
        except OSError as exc:
            LOG.error("socket failed: %s", exc)
            return 1
        finally:
            self.stopping.set()
            self.vault.lock()
            self.stop_telegram()
            if server:
                server.stop()
        return 0

    def heal(self) -> None:
        """Repair the browser connection silently (no "register again" buttons for users)."""
        try:
            from walled_guard import installer

            from .paths import extension_home, install_extension
            if not installer.installed() or not (audit.state_dir() / "walled-guard-host").exists():
                installer.install()
            if not (extension_home() / "manifest.json").exists():
                install_extension()
        except Exception as exc:
            LOG.warning("self-repair skipped: %s", exc)

    def _autolock_loop(self) -> None:
        while not self.stopping.wait(20):
            self.vault.autolock = config.load().settings.vault_autolock_minutes * 60
            self.vault.unlocked()           # locks itself when idle too long
