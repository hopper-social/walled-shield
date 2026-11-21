"""Control Center API and UI server, inside the Walled service.

Security
  * listens on 127.0.0.1 only, on a random port;
  * every /api call needs a token (header X-Walled-Token). The per-start admin token sits in
    a 0600 file that only the app launcher reads. The launcher never puts it on a command
    line (other users can read process lists): it asks for a one-time *launch ticket*
    (single use, 60 s), opens the window with the ticket in the URL fragment, and the page
    trades it for a window session token in a POST body;
  * the Host header must be 127.0.0.1:<port> or localhost:<port> (blocks DNS rebinding);
  * no CORS headers, and the custom header forces a preflight we never approve, so web pages
    in your browser cannot call it.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit

from walled_guard import audit, config, installer, ocr
from walled_guard import dialogs as guard_dialogs
from walled_guard.rules import BUILTINS, Rule

from . import __version__
from .vault import VaultError, generate, strength

if TYPE_CHECKING:
    from .service import WalledService

LOG = logging.getLogger("walled.control")
WEB = Path(__file__).with_name("web")
SAMPLES = Path(__file__).with_name("samples")      # test files to upload (Test center)
PRACTICE = Path(__file__).with_name("practice")    # practice sign-up / login pages


def _local(utc: str) -> str:
    """SQLite UTC timestamp → local ISO time, comparable with the browser log."""
    try:
        import calendar
        secs = calendar.timegm(time.strptime(utc[:19], "%Y-%m-%dT%H:%M:%S"))
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(secs))
    except (ValueError, TypeError):
        return str(utc)


class Download:
    """A route result sent as a file (audit export) instead of JSON."""

    def __init__(self, body: bytes, ctype: str, filename: str) -> None:
        self.body, self.ctype, self.filename = body, ctype, filename


def control_file() -> Path:
    return audit.state_dir() / "control.json"


class Pairing:
    """Telegram pairing in the background so the UI can show the code and poll."""

    def __init__(self) -> None:
        self.state = "idle"             # idle | waiting | done | error
        self.code = ""
        self.raw = ""                   # bare code for the t.me deep link
        self.deadline = 0.0
        self.bot = ""
        self.error = ""
        self.lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            kind = "startgroup" if self.code.startswith("/alertpair") else "start"
            link = f"https://t.me/{self.bot}?{kind}={self.raw}" if self.bot and self.raw else ""
            left = max(0, int(self.deadline - time.monotonic())) if self.state == "waiting" else 0
            return {"state": self.state, "code": self.code, "bot": self.bot, "error": self.error,
                    "link": link, "seconds_left": left}


class ControlServer:
    def __init__(self, service: "WalledService", port: int = 0):
        self.service = service
        self.token = secrets.token_urlsafe(32)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.pairing = Pairing()
        self.activity_pairing = Pairing()
        self.query: dict[str, str] = {}
        self.renew_code: str | None = None
        self.renew_deadline = 0.0
        self.renew_result = "idle"
        self.renew_link = ""
        self.tickets: dict[str, float] = {}          # one-time launch tickets → expiry
        self.sessions: dict[str, float] = {}         # window session tokens → expiry
        self.auth_lock = threading.Lock()

    # ------------------------------------------------------------------ auth
    def new_ticket(self) -> str:
        ticket = secrets.token_urlsafe(24)
        with self.auth_lock:
            now = time.monotonic()
            self.tickets = {t: exp for t, exp in self.tickets.items() if exp > now}
            self.tickets[ticket] = now + 60
        return ticket

    def redeem(self, ticket: str) -> str | None:
        with self.auth_lock:
            exp = self.tickets.pop(ticket, None)
            if exp is None or exp < time.monotonic():
                return None
            token = secrets.token_urlsafe(32)
            self.sessions[token] = time.monotonic() + 12 * 3600
            if len(self.sessions) > 20:                  # keep the newest windows only
                for old in sorted(self.sessions, key=self.sessions.__getitem__)[:-20]:
                    self.sessions.pop(old)
            return token

    def authorised(self, token: str) -> bool:
        if not token:
            return False
        if hmac.compare_digest(token.encode(), self.token.encode()):
            return True
        with self.auth_lock:
            now = time.monotonic()
            return any(hmac.compare_digest(token.encode(), t.encode()) and exp > now
                       for t, exp in self.sessions.items())

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.3}, daemon=True).start()
        path = control_file()
        audit.private_dir()
        tmp = path.with_name(".control.json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid()}, fh)
        os.replace(tmp, path)
        LOG.info("Control Center on http://127.0.0.1:%s", self.port)

    def stop(self) -> None:
        self.httpd.shutdown()
        try:
            control_file().unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------ routes
    def _activity_channel(self) -> bool:
        with self.service.lock:
            remote = self.service.remote
        return bool(remote is not None and remote.alert_chat_id != remote.chat_id)

    def routes(self) -> dict[tuple[str, str], Callable[[dict[str, Any], re.Match[str]], Any]]:
        return {
            ("GET", r"/api/state"): lambda b, m: self.state(),
            ("POST", r"/api/ticket"): lambda b, m: {"ticket": self.new_ticket()},
            ("GET", r"/api/rules"): lambda b, m: self.get_rules(),
            ("PUT", r"/api/rules"): lambda b, m: self.put_rules(b),
            ("POST", r"/api/rules/test"): lambda b, m: self.test_rules(b),
            ("GET", r"/api/settings"): lambda b, m: config.load().to_json()["settings"],
            ("PUT", r"/api/settings"): lambda b, m: self.put_settings(b),
            ("POST", r"/api/telegram/setup"): lambda b, m: self.telegram_setup(b),
            ("GET", r"/api/telegram/pairing"): lambda b, m: self.pairing.snapshot(),
            ("POST", r"/api/telegram/renew"): lambda b, m: self.telegram_renew(),
            ("GET", r"/api/telegram/renew"): lambda b, m: self.renew_state(),
            ("GET", r"/api/insights"): lambda b, m: self.insights(),
            ("GET", r"/api/events"): lambda b, m: self.events_since(),
            ("GET", r"/api/tests"): lambda b, m: self.tests(),
            ("POST", r"/api/tests/(dialog|telegram|race)"): lambda b, m: self.run_test(m.group(1)),
            ("POST", r"/api/telegram/activity"): lambda b, m: self.activity_setup(),
            ("POST", r"/api/telegram/web-setup"): lambda b, m: self.telegram_web_setup(),
            ("GET", r"/api/telegram/activity"): lambda b, m: self.activity_pairing.snapshot(),
            ("POST", r"/api/suggestions/(dismiss|apply)"): lambda b, m: self.suggestion(m.group(1), b),
            ("PUT", r"/api/onboarding"): lambda b, m: self.onboarding(b),
            ("GET", r"/api/browser"): lambda b, m: self.browser_info(),
            ("GET", r"/api/pet"): lambda b, m: self.service.pet_state(),
            ("POST", r"/api/pet/(show|hide|open-app)"): lambda b, m: self.service.pet_action(m.group(1)),
            ("POST", r"/api/browser/(reveal|refresh|extensions)"): lambda b, m: self.browser_action(m.group(1)),
            ("POST", r"/api/telegram/test"): lambda b, m: self.telegram_test(),
            ("GET", r"/api/connectors"): lambda b, m: self.connectors(),
            ("POST", r"/api/connectors/(wrap|unwrap)"): lambda b, m: self.connectors_change(m.group(1), b),
            ("POST", r"/api/browser/register"): lambda b, m: {"registered": installer.install()},
            ("POST", r"/api/guard/dialog-test"): lambda b, m: self.dialog_test(),
            ("GET", r"/api/vault"): lambda b, m: self.service.vault.status(),
            ("POST", r"/api/vault/(create|unlock|lock)"): lambda b, m: self.vault_state(m.group(1), b),
            ("GET", r"/api/vault/entries"): lambda b, m: {"entries": self.service.vault.entries()},
            ("POST", r"/api/vault/entries"): lambda b, m: self.vault_save(b),
            ("DELETE", r"/api/vault/entries/([a-f0-9]{12})"): lambda b, m: self.vault_delete(m.group(1)),
            ("GET", r"/api/vault/entries/([a-f0-9]{12})/password"): lambda b, m: self.vault_reveal(m.group(1)),
            ("POST", r"/api/vault/generate"): lambda b, m: self.vault_generate(b),
            ("GET", r"/api/activity"): lambda b, m: self.activity(),
            ("GET", r"/api/activity/export"): lambda b, m: self.activity_export(),
        }

    # ------------------------------------------------------------------ handlers
    def state(self) -> dict[str, Any]:
        cfg = config.load()
        try:
            from mcp_approval_gate import desktop_config
            servers = desktop_config.status(desktop_config.default_config_path())
        except Exception:
            servers = {}
        return {
            "version": __version__,
            "uptime": int(time.time() - self.service.started),
            "telegram": {**self.service.telegram_state(), "renew_code": self.renew_code,
                         "activity_channel": self._activity_channel()},
            "onboarded": cfg.settings.onboarded, "goals": cfg.settings.goals,
            "guard": {"browsers": installer.installed(), "extension_id": installer.EXTENSION_ID,
                      "extension_seen": self.service.extension_seen,
                      "ocr": ocr.available(), "dialog": guard_dialogs.backend_name()},
            "mcp": {"servers": servers},
            "rules": {"total": len(cfg.rules), "enabled": sum(r.enabled for r in cfg.rules)},
            "vault": self.service.vault.status(),
        }

    def get_rules(self) -> dict[str, Any]:
        result: dict[str, Any] = {"rules": [r.to_json() for r in config.load().rules],
                "detectors": {k: {"label": d.label, "example": d.example} for k, d in BUILTINS.items()}}
        return result

    def put_rules(self, body: dict[str, Any]) -> dict[str, Any]:
        items = body.get("rules")
        if not isinstance(items, list) or len(items) > 500:
            raise ValueError("rules must be a list")
        cfg = config.load()
        rules = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("invalid rule")
            rules.append(Rule.from_json(item))
        cfg.rules = rules
        config.save(cfg)
        self.service.events.publish("rules.saved", "Sensitive-data rules updated", tone="info")
        return self.get_rules()

    def test_rules(self, body: dict[str, Any]) -> dict[str, Any]:
        text = str(body.get("text") or "")[:20000]
        engine = config.load().engine()
        hits = engine.scan(text, "typed") + engine.scan_filename(text.strip()) + engine.scan_paths([text.strip()])
        return {"hits": [h.to_json() for h in hits]}

    def put_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        cfg = config.load()
        current = cfg.to_json()["settings"]
        merged = {**current, **{k: v for k, v in body.items() if k in current}}
        if isinstance(body.get("pet"), dict):                       # partial pet updates merge
            merged["pet"] = {**current["pet"], **body["pet"]}
        cfg.settings = config.Settings.from_json(merged)
        config.save(cfg)
        if cfg.settings.pet != current["pet"]:
            self.service.events.publish("pet.settings", "", tone="info", pet=cfg.settings.pet)
        self.service.vault.autolock = cfg.settings.vault_autolock_minutes * 60
        settings: dict[str, Any] = cfg.to_json()["settings"]
        return settings

    # Telegram ----------------------------------------------------------------------
    def telegram_setup(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.start_pairing(str(body.get("token") or ""), wait=True)

    def start_pairing(self, token: str, wait: bool = True) -> dict[str, Any]:
        """Used by the app (pasted token) and by the browser extension (token spotted on
        web.telegram.org / copied). Returns the snapshot with the code and deep link."""
        token = token.strip()
        if not re.fullmatch(r"\d{5,12}:[A-Za-z0-9_-]{30,64}", token):
            raise ValueError("That doesn't look like a bot token from @BotFather (digits:letters).")
        with self.pairing.lock:
            busy = self.pairing.state == "waiting" and time.monotonic() < self.pairing.deadline + 15
            if busy:
                raise ValueError("Pairing already in progress")
            self.pairing.state, self.pairing.code, self.pairing.error = "waiting", "", ""
            self.pairing.deadline = time.monotonic() + 135
        self.service.events.publish("telegram.pairing", "Connecting your bot…", "Token received", "info")
        threading.Thread(target=self._pair, args=(token,), daemon=True).start()
        for _ in range(60):                          # give the UI the code straight away
            snap = self.pairing.snapshot()
            if snap["code"] or snap["state"] != "waiting":
                return snap
            time.sleep(0.1)
        return self.pairing.snapshot()

    def _pair(self, token: str) -> None:
        from mcp_approval_gate.paths import ensure_private_dir, write_private_file
        from mcp_approval_gate.telegram_approver import BotAPI, pair_private_chat
        from mcp_approval_gate.telegram_launcher import credential_directory
        from mcp_approval_gate.telegram_session import ApprovalSession

        def announce(text: str) -> None:
            match = re.search(r"/pair (\S+)", text)
            with self.pairing.lock:
                self.pairing.code = "/pair " + match.group(1) if match else text
                self.pairing.raw = match.group(1) if match else ""
                self.pairing.deadline = time.monotonic() + 120

        try:
            me = BotAPI(token).call("getMe", {})
            with self.pairing.lock:
                self.pairing.bot = me.get("username", "") if isinstance(me, dict) else ""
            self.service.stop_telegram()             # one poller per bot
            chat_id, user_id = pair_private_chat(token, announce=announce)
            directory = ensure_private_dir(credential_directory())
            write_private_file(directory / "telegram-bot-token", token + "\n")
            write_private_file(directory / "telegram-pairing.json",
                               json.dumps({"chat_id": chat_id, "user_id": user_id}) + "\n")
            ApprovalSession(directory / "telegram-session.json", chat_id, user_id).activate()
            ok = self.service.start_telegram()
            with self.pairing.lock:
                self.pairing.state = "done" if ok else "error"
                self.pairing.error = "" if ok else (self.service.telegram_error or "could not start the bot")
            if ok:
                self.service.events.publish("telegram.paired", f"Telegram paired with @{self.pairing.bot}",
                                            "Approvals are on for 24 hours.", "ok", bot=self.pairing.bot)
        except Exception as exc:
            with self.pairing.lock:
                self.pairing.state, self.pairing.error = "error", str(exc)[:200]
            self.service.events.publish("telegram.pairing_failed", "Telegram pairing didn't finish", str(exc)[:200], "bad")
            self.service.start_telegram()            # restore the previous bot, if any

    def telegram_renew(self) -> dict[str, Any]:
        from mcp_approval_gate.approval_daemon import new_pair_code
        with self.service.lock:
            daemon = self.service.daemon
        if daemon is None:
            raise ValueError("Telegram is not running. Pair a bot first.")
        code = new_pair_code()
        self.renew_code = "/pair " + code
        self.renew_deadline = time.monotonic() + 120
        bot = self.service.bot_name

        def wait() -> None:
            ok = daemon.renew(code, timeout=120)
            self.renew_code = None
            self.renew_result = "done" if ok else "expired"
            if ok:
                self.service.events.publish("telegram.renewed", "Telegram approvals on for 24 hours", tone="ok")
        self.renew_result = "waiting"
        threading.Thread(target=wait, daemon=True).start()
        self.renew_link = f"https://t.me/{bot}?start={code}" if bot else ""
        return self.renew_state()

    def renew_state(self, link: str = "") -> dict[str, Any]:
        left = max(0, int(self.renew_deadline - time.monotonic())) if self.renew_code else 0
        return {"code": self.renew_code, "state": self.renew_result, "seconds_left": left, "link": link or self.renew_link}

    def telegram_test(self) -> dict[str, Any]:
        with self.service.lock:
            remote = self.service.remote
        if remote is None:
            raise ValueError("Telegram is not running")
        return {"sent": remote.send_notice("👋 Test from the Walled app. Alerts will arrive here.")}

    # Events & Test center ---------------------------------------------------------------
    def events_since(self) -> dict[str, Any]:
        since = int(self.query.get("since", "0") or 0) if self.query.get("since", "0").isdigit() else 0
        found = self.service.events.since(since, 0)
        return {"events": [e.to_json() for e in found], "last": self.service.events.last_id()}

    def tests(self) -> dict[str, Any]:
        from .service import TEST_TITLES
        results = config.load().settings.tests
        try:
            from mcp_approval_gate.activity import database_path
            if "mcp" not in results and database_path().exists():
                con = sqlite3.connect(f"file:{database_path()}?mode=ro", uri=True)
                row = con.execute("SELECT 1 FROM activity WHERE outcome IN ('allow','always') LIMIT 1").fetchone()
                con.close()
                if row:
                    self.service.mark_test("mcp", True, "seen in the activity log")
                    results = config.load().settings.tests
        except sqlite3.Error:
            pass
        return {"tests": {k: {"title": v, **(results.get(k) or {"ok": None})} for k, v in TEST_TITLES.items()}}

    def run_test(self, which: str) -> dict[str, Any]:
        from walled_guard import cards  # noqa: F401  (imported for symmetry; cards stay masked)
        if which == "dialog":
            choice = guard_dialogs.ask(guard_dialogs.Request(
                "prompt", "Walled test", ["This is a test. Choose Continue or Cancel — both count as a pass."]))
            self.service.mark_test("dialog", True, f"you chose {choice}")
            return {"ok": True, "choice": choice}
        with self.service.lock:
            daemon = self.service.daemon
        if daemon is None:
            raise ValueError("Pair Telegram first (Telegram page).")
        if not daemon.approver._authorized():
            raise ValueError("Telegram approvals are off. Turn them on for 24 h first.")
        card = ("🧪 <b>Walled test</b>\nTap <b>Allow</b> or <b>Block</b>. Either answer passes the test.")
        if which == "telegram":
            allowed, reason = daemon._ask_text(card, threading.Event(), 60)
            ok = reason in ("allow", "deny")
            self.service.mark_test("telegram", ok, f"you tapped {'Allow' if allowed else 'Block'}" if ok else reason)
            return {"ok": ok, "choice": "allow" if allowed else "block", "reason": reason}
        # race: the same question on the desktop and on the phone; the first real answer wins
        answers: list[tuple[str, str]] = []
        done = threading.Event()
        close_dialog, withdraw = threading.Event(), threading.Event()

        def desktop() -> None:
            choice = guard_dialogs.ask(guard_dialogs.Request(
                "prompt", "Walled race test", ["Answer here or on your phone. The first answer wins."], phone=True), close_dialog)
            if not close_dialog.is_set():
                answers.append(("desktop", choice))
                done.set()

        def phone() -> None:
            allowed, reason = daemon._ask_text(card.replace("Walled test", "Walled race test"), withdraw, 60)
            if reason in ("allow", "deny") and not withdraw.is_set():
                answers.append(("phone", "continue" if allowed else "cancel"))
                done.set()

        threading.Thread(target=desktop, daemon=True).start()
        threading.Thread(target=phone, daemon=True).start()
        done.wait(75)
        close_dialog.set()
        withdraw.set()
        if not answers:
            return {"ok": False, "reason": "no answer"}
        via, choice = answers[0]
        self.service.mark_test("race", True, f"answered on the {'phone' if via == 'phone' else 'computer'} first")
        return {"ok": True, "via": via, "choice": choice}

    def telegram_web_setup(self) -> dict[str, Any]:
        """Ask the browser extension to open Telegram Web and guide the BotFather steps there."""
        connected = bool(self.service.extension_seen and time.time() - self.service.extension_seen < 24 * 3600)
        self.service.events.publish("assist.telegram_web", "Opening Telegram Web", tone="info")
        return {"extension": connected}

    def activity_setup(self) -> dict[str, Any]:
        """Pair a separate Telegram group/channel that receives the activity feed."""
        from mcp_approval_gate.telegram_launcher import CredentialError, credential_directory, load_credentials
        try:
            creds = load_credentials(credential_directory())
        except CredentialError as exc:
            raise ValueError("Pair your Telegram bot first.") from exc
        with self.activity_pairing.lock:
            if self.activity_pairing.state == "waiting" and time.monotonic() < self.activity_pairing.deadline:
                return self.activity_pairing.snapshot()
            self.activity_pairing.state, self.activity_pairing.code, self.activity_pairing.error = "waiting", "", ""
            self.activity_pairing.bot = self.service.bot_name or ""
            self.activity_pairing.deadline = time.monotonic() + 135
        threading.Thread(target=self._pair_activity, args=(creds,), daemon=True).start()
        for _ in range(60):
            snap = self.activity_pairing.snapshot()
            if snap["code"] or snap["state"] != "waiting":
                break
            time.sleep(0.1)
        snap = self.activity_pairing.snapshot()
        if snap["bot"] and self.activity_pairing.raw:
            snap["link"] = f"https://t.me/{snap['bot']}?startgroup={self.activity_pairing.raw}"
        return snap

    def _pair_activity(self, creds: dict[str, str]) -> None:
        from mcp_approval_gate.paths import write_private_file
        from mcp_approval_gate.telegram_approver import pair_alert_chat
        from mcp_approval_gate.telegram_launcher import credential_directory

        def announce(text: str) -> None:
            match = re.search(r"/alertpair (\S+)", text)
            with self.activity_pairing.lock:
                self.activity_pairing.code = "/alertpair " + match.group(1) if match else text
                self.activity_pairing.raw = match.group(1) if match else ""
                self.activity_pairing.deadline = time.monotonic() + 120

        try:
            self.service.stop_telegram()
            chat = pair_alert_chat(creds["MCP_TELEGRAM_BOT_TOKEN"], int(creds["MCP_TELEGRAM_USER_ID"]), announce=announce)
            path = credential_directory() / "telegram-pairing.json"
            data = json.loads(path.read_text())
            data["alert_chat_id"] = chat
            write_private_file(path, json.dumps(data) + "\n")
            with self.activity_pairing.lock:
                self.activity_pairing.state = "done"
            self.service.events.publish("telegram.activity_channel", "Activity channel connected", tone="ok")
        except Exception as exc:
            with self.activity_pairing.lock:
                self.activity_pairing.state, self.activity_pairing.error = "error", str(exc)[:200]
        finally:
            self.service.start_telegram()

    # Insights / onboarding ------------------------------------------------------------
    def insights(self) -> dict[str, Any]:
        from . import insights
        state = self.state()
        items = insights.checklist(state)
        return {"score": insights.score(items), "checklist": items, "badges": insights.badges(),
                "stats": insights.stats(), "suggestions": insights.suggestions(state)}

    def suggestion(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        sid = str(body.get("id") or "")[:80]
        if action == "dismiss":
            cfg = config.load()
            if sid not in cfg.settings.dismissed:
                cfg.settings.dismissed.append(sid)
                config.save(cfg)
            return self.insights()
        from . import insights
        match = next((s for s in insights.suggestions(self.state()) if s["id"] == sid), None)
        if match is None or not match.get("action"):
            raise ValueError("That suggestion no longer applies.")
        act = match["action"]
        if act["kind"] == "renew":
            return {"renew": self.telegram_renew()}
        if act["kind"] == "rule":
            cfg = config.load()
            for r in cfg.rules:
                if r.id == act["rule"]:
                    for key, value in act["set"].items():
                        if key in ("action", "phone"):
                            setattr(r, key, value)
            config.save(cfg)
        return self.insights()

    def onboarding(self, body: dict[str, Any]) -> dict[str, Any]:
        cfg = config.load()
        if "onboarded" in body:
            cfg.settings.onboarded = bool(body["onboarded"])
        if isinstance(body.get("goals"), list):
            cfg.settings.goals = [g for g in body["goals"] if g in ("browser", "claude", "passwords")]
        config.save(cfg)
        return {"onboarded": cfg.settings.onboarded, "goals": cfg.settings.goals}

    def browser_info(self) -> dict[str, Any]:
        from .paths import extension_home
        home = extension_home()
        return {"extension_path": str(home), "extension_ready": (home / "manifest.json").exists(),
                "extension_id": installer.EXTENSION_ID, "extension_seen": self.service.extension_seen,
                "browsers": installer.installed()}

    def browser_action(self, action: str) -> dict[str, Any]:
        from .paths import extension_home, install_extension, open_extensions_page, reveal
        if action == "refresh" or not (extension_home() / "manifest.json").exists():
            install_extension()
        if action == "reveal":
            reveal(extension_home())
        if action == "extensions":
            return {**self.browser_info(), "opened": open_extensions_page()}
        return self.browser_info()

    # Connectors --------------------------------------------------------------------
    def connectors(self) -> dict[str, Any]:
        from mcp_approval_gate import desktop_config
        path = desktop_config.default_config_path()
        try:
            servers = desktop_config.status(path)
        except Exception as exc:
            return {"path": str(path), "servers": {}, "error": f"Claude Desktop config not readable: {exc}"}
        return {"path": str(path), "servers": servers, "error": None}

    def connectors_change(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        from mcp_approval_gate import desktop_config
        names = body.get("names")
        only = set(names) if isinstance(names, list) and names else None
        path = desktop_config.default_config_path()
        if action == "wrap":
            report, _ = desktop_config.wrap(path, only=only, approver="daemon")
        else:
            report, _ = desktop_config.unwrap(path, only=only)
        result = self.connectors()
        result["changed"] = report.changed
        result["note"] = "Quit and reopen Claude Desktop to apply." if report.changed else ""
        return result

    def dialog_test(self) -> dict[str, Any]:
        choice = guard_dialogs.ask(guard_dialogs.Request("file", "ChatGPT", ["quarterly_report.pdf — page 2"]))
        return {"choice": choice}

    # Vault -------------------------------------------------------------------------
    def vault_state(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        vault = self.service.vault
        keychain = body.get("mode") == "keychain"
        if action == "lock":
            vault.lock()
            self.service.events.publish("vault.locked", "Vault locked", tone="info")
        elif action == "create":
            if keychain:
                vault.create_with_keychain()
            else:
                vault.create(str(body.get("master") or ""))
            self.service.events.publish("vault.created", "Password vault created", tone="ok")
        else:
            if keychain or (vault.mode() == "keychain" and not body.get("master")):
                vault.unlock_with_keychain()
            else:
                vault.unlock(str(body.get("master") or ""))
            self.service.events.publish("vault.unlocked", "Vault unlocked", tone="ok")
        return vault.status()

    def vault_save(self, body: dict[str, Any]) -> dict[str, Any]:
        entry = self.service.vault.save_entry(str(body.get("origin") or ""), str(body.get("username") or ""),
                                              str(body.get("password") or ""), str(body.get("note") or ""),
                                              body.get("id") or None)
        return entry.public()

    def vault_delete(self, entry_id: str) -> dict[str, Any]:
        self.service.vault.delete(entry_id)
        return {"deleted": entry_id}

    def vault_reveal(self, entry_id: str) -> dict[str, Any]:
        entry = self.service.vault.get(entry_id)
        return {"password": entry.password, "strength": strength(entry.password)}

    def vault_generate(self, body: dict[str, Any]) -> dict[str, Any]:
        pw = generate(int(body.get("length") or 20), bool(body.get("symbols", True)))
        return {"password": pw, "strength": strength(pw)}

    # Activity ----------------------------------------------------------------------
    def activity_export(self) -> Download:
        """The audit trail as CSV or JSON: rule names, sites and outcomes only (never content)."""
        import csv
        import io
        fmt = getattr(self, "query", {}).get("format", "csv")
        events = self.activity()["events"]
        stamp = time.strftime("%Y-%m-%d")
        if fmt == "json":
            body = json.dumps({"exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "events": events}, indent=2).encode()
            return Download(body, "application/json", f"walled-audit-{stamp}.json")
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["time", "source", "where", "what", "decision", "rules", "via"])
        for e in events:
            row = [e.get("time", ""), e.get("source", ""), e.get("where", ""), e.get("what", ""), e.get("decision", ""),
                   "; ".join(map(str, e.get("rules") or [])), e.get("via", "")]
            writer.writerow([("'" + str(v)) if str(v)[:1] in ("=", "+", "-", "@") else v for v in row])   # no CSV formula injection
        return Download(out.getvalue().encode(), "text/csv; charset=utf-8", f"walled-audit-{stamp}.csv")

    def activity(self) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        for e in audit.recent(200):
            events.append({"time": e.get("time", ""), "source": "browser", "where": e.get("site", ""),
                           "what": e.get("kind", ""), "decision": e.get("decision", ""),
                           "rules": e.get("rules", []), "via": e.get("via", "")})
        try:
            from mcp_approval_gate.activity import database_path
            if database_path().exists():
                con = sqlite3.connect(f"file:{database_path()}?mode=ro", uri=True)
                rows = con.execute("SELECT at, server, method, tool, outcome FROM activity "
                                   "ORDER BY seq DESC LIMIT 200").fetchall()
                con.close()
                for at, srv, method, tool, outcome in rows:
                    if outcome == "connector_ready":
                        continue
                    events.append({"time": _local(at), "source": "claude", "where": srv, "what": tool or method,
                                   "decision": outcome, "rules": [], "via": ""})
        except sqlite3.Error:
            pass
        events.sort(key=lambda e: e["time"], reverse=True)
        return {"events": events[:300]}

    # ------------------------------------------------------------------ HTTP plumbing
    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "Walled"
            sys_version = ""

            def log_message(self, fmt: str, *args: Any) -> None:
                LOG.debug(fmt, *args)

            def _host_ok(self) -> bool:
                host = self.headers.get("Host", "")
                return host in (f"127.0.0.1:{server.port}", f"localhost:{server.port}")

            def _send(self, code: int, body: bytes, ctype: str = "application/json", filename: str = "") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                if filename:
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if ctype.startswith("text/html"):
                    # Only Walled's own extension (the Chrome side panel) may embed the app.
                    self.send_header("Content-Security-Policy",
                                     "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                     "script-src 'self'; connect-src 'self'; "
                                     f"frame-ancestors 'self' chrome-extension://{installer.EXTENSION_ID}")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, code: int, payload: Any) -> None:
                self._send(code, json.dumps(payload).encode())

            def _dispatch(self) -> None:
                if not self._host_ok():
                    return self._json(403, {"error": "bad host"})
                parts = urlsplit(self.path)
                path = parts.path
                from urllib.parse import parse_qsl
                server.query = dict(parse_qsl(parts.query))
                if not path.startswith("/api/"):
                    return self._static(path)
                token = self.headers.get("X-Walled-Token", "")
                if path == "/api/session" and self.command == "POST":
                    return self._redeem()
                if not server.authorised(token):
                    return self._json(401, {"error": "unauthorised"})
                body: dict[str, Any] = {}
                if self.command in ("POST", "PUT"):
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 1024 * 1024:
                        return self._json(413, {"error": "too large"})
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        parsed = json.loads(raw or b"{}")
                    except ValueError:
                        return self._json(400, {"error": "invalid JSON"})
                    if not isinstance(parsed, dict):
                        return self._json(400, {"error": "object expected"})
                    body = parsed
                for (method, pattern), fn in server.routes().items():
                    match = re.fullmatch(pattern, path)
                    if match and method == self.command:
                        try:
                            result = fn(body, match)
                            if isinstance(result, Download):
                                return self._send(200, result.body, result.ctype, result.filename)
                            return self._json(200, result)
                        except (ValueError, VaultError) as exc:
                            return self._json(400, {"error": str(exc)})
                        except Exception as exc:
                            LOG.exception("API error")
                            return self._json(500, {"error": f"{type(exc).__name__}"})
                return self._json(404, {"error": "not found"})

            def _redeem(self) -> None:
                length = min(int(self.headers.get("Content-Length") or 0), 4096)
                try:
                    ticket = str(json.loads(self.rfile.read(length) or b"{}").get("ticket", ""))
                except (ValueError, AttributeError):
                    ticket = ""
                token = server.redeem(ticket)
                if token is None:
                    return self._json(401, {"error": "This link has expired. Open Walled again."})
                return self._json(200, {"token": token})

            def _static(self, path: str) -> None:
                root, name = WEB, "index.html" if path in ("/", "") else path.lstrip("/")
                if name.startswith("sounds/"):
                    return self._sound(name[len("sounds/"):])
                if name in ("buddy.js", "pip-art.js"):   # Pip's look and runtime are shared with the extension
                    root = Path(__file__).with_name("extension") / "src"
                for prefix, folder in (("samples/", SAMPLES), ("practice/", PRACTICE)):
                    if name.startswith(prefix):
                        root, name = folder, name[len(prefix):] or "login.html"
                        if root is PRACTICE and "." not in name:
                            name += ".html"
                target = (root / name).resolve()
                if root.resolve() not in target.parents or not target.is_file():
                    return self._send(404, b"not found", "text/plain")
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                self._send(200, target.read_bytes(), ctype)

            def _sound(self, name: str) -> None:
                """Pip's recorded sounds: your own folder first (<state>/sounds), then the packaged ones."""
                folders = [audit.state_dir() / "sounds", WEB.parent / "extension" / "sounds"]
                if name == "index.json":
                    found = sorted({f.name for d in folders if d.is_dir() for f in d.iterdir()
                                    if re.fullmatch(r"[a-z]+\.(mp3|ogg|wav|m4a)", f.name)})
                    return self._send(200, json.dumps(found).encode())
                if not re.fullmatch(r"[a-z]+\.(mp3|ogg|wav|m4a)", name):
                    return self._send(404, b"not found", "text/plain")
                for d in folders:
                    if (d / name).is_file():
                        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                        return self._send(200, (d / name).read_bytes(), ctype)
                return self._send(404, b"not found", "text/plain")

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _dispatch

        return Handler
