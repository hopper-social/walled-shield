"""The merged product end to end, without a browser: the native host, the Walled service, the
MCP approval daemon and a fake Telegram Bot API, all talking over the real Unix socket."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from mcp_approval_gate.approval_daemon import ApprovalDaemon, DaemonConfig
from mcp_approval_gate.approval_daemon import serve as serve_socket
from mcp_approval_gate.daemon_client import call as daemon_call
from mcp_approval_gate.telegram_approver import TelegramApprover

CHAT = 4242


class FakeAPI:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    def call(self, method, payload, timeout=12):
        with self.lock:
            if method == "sendMessage":
                self.sent.append(payload)
                return {"message_id": len(self.sent)}
            if method in ("editMessageText", "editMessageReplyMarkup"):
                self.edits.append(payload)
                return True
        if method == "getUpdates":
            time.sleep(0.01)
            return []
        if method == "getMe":
            return {"username": "walled_test_bot"}
        return True

    def all_text(self) -> str:
        with self.lock:
            return json.dumps(self.sent + self.edits, ensure_ascii=False)

    def prompts(self):
        with self.lock:
            return [(i + 1, p) for i, p in enumerate(self.sent) if "reply_markup" in p]


def wait_for(predicate, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def tap(remote, message_id, payload, choice):
    data = next(b["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for b in row
                if b["callback_data"].startswith(choice + ":"))
    remote._process({"id": "cb", "data": data, "from": {"id": CHAT},
                     "message": {"message_id": message_id, "chat": {"id": CHAT, "type": "private"}}})


@pytest.fixture
def platform(tmp_path, monkeypatch):
    """A running Walled service with Telegram connected to a fake Bot API."""
    monkeypatch.setenv("WALLED_GUARD_HOME", str(tmp_path / "walled"))
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir(mode=0o700)
    monkeypatch.setenv("MCP_GATE_STATE_DIR", str(gate_dir))
    monkeypatch.setenv("MCP_GATE_SOCKET", str(gate_dir / "s.sock"))
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "wait")          # nobody at the desktop unless a test says so
    from walled.service import WalledService
    from walled_guard import config

    cfg = config.load()
    cfg.settings.dialog_timeout = 15
    config.save(cfg)
    service = WalledService(tmp_path / "walled" / "vault.json")
    api = FakeAPI()
    stop = threading.Event()
    remote = TelegramApprover("t", CHAT, CHAT, api=api, stop_event=stop, timeout=15)
    remote.healthy = True
    daemon = ApprovalDaemon(remote, DaemonConfig(timeout=15))
    daemon.inspect = service.inspect_mcp
    daemon.extra_commands = service.telegram_command
    service.remote, service.daemon = remote, daemon
    thread = threading.Thread(target=serve_socket, args=(service, gate_dir / "s.sock", stop), daemon=True)
    thread.start()
    wait_for(lambda: (gate_dir / "s.sock").exists())
    yield {"service": service, "api": api, "remote": remote, "config": config, "tmp": tmp_path}
    stop.set()
    thread.join(3)


def set_rule(config, rule_id, **changes):
    cfg = config.load()
    for r in cfg.rules:
        if r.id == rule_id:
            for k, v in changes.items():
                setattr(r, k, v)
    config.save(cfg)


def host(message):
    from walled_guard import host as native
    return native.handle(message)


# ----------------------------------------------------------------- browser guard × Telegram
def test_phone_answer_wins_the_race_and_closes_the_dialog(platform):
    set_rule(platform["config"], "marker", phone="approve")
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(host({"type": "confirm_prompt", "site": "chatgpt",
                                                             "text": "my WALLED_SENSITIVE note"})))
    t.start()
    message_id, prompt = wait_for(lambda: platform["api"].prompts() and platform["api"].prompts()[-1])
    assert "Sensitive data on ChatGPT" in prompt["text"] and "my WALLED_SENSITIVE note" not in prompt["text"]
    tap(platform["remote"], message_id, prompt, "a")
    t.join(10)
    assert result["decision"] == "continue" and result["via"] == "phone"


def test_desktop_answer_wins_and_withdraws_the_phone_question(platform, monkeypatch):
    set_rule(platform["config"], "marker", phone="approve")
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "cancel")
    reply = host({"type": "confirm_prompt", "site": "perplexity", "text": "WALLED_SENSITIVE"})
    assert reply["decision"] == "cancel" and reply["via"] == "desktop"
    wait_for(lambda: "Stopped" in platform["api"].all_text())            # outcome alert after the decision


def test_warn_rule_sends_masked_alert_and_lets_it_through(platform):
    set_rule(platform["config"], "builtin-card", action="warn", phone="notify")
    reply = host({"type": "confirm_prompt", "site": "chatgpt", "text": "pay with 4111 1111 1111 1111"})
    assert reply["decision"] == "allow" and reply["via"] == "rule"
    text = wait_for(lambda: platform["api"].sent and platform["api"].all_text())
    assert "•••• 1111" in text and "4111 1111 1111 1111" not in text


def test_block_rule_stops_without_a_dialog(platform):
    set_rule(platform["config"], "builtin-private_key", action="block")
    started = time.monotonic()
    reply = host({"type": "confirm_prompt", "site": "chatgpt", "text": "-----BEGIN RSA PRIVATE KEY-----\nabc"})
    assert reply["decision"] == "cancel" and reply["via"] == "rule" and time.monotonic() - started < 3


def test_file_upload_alert_names_the_file_not_its_content(platform, monkeypatch):
    import base64
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "cancel")
    data = base64.b64encode(b"top secret WALLED_SENSITIVE body").decode()
    reply = host({"type": "inspect_files", "site": "chatgpt", "files": [{"name": "plan.txt", "data": data}]})
    assert reply["decision"] == "cancel"
    text = wait_for(lambda: platform["api"].sent and platform["api"].all_text())
    assert "plan.txt" in text and "top secret" not in text


def test_without_the_service_the_desktop_dialog_still_decides(platform, monkeypatch):
    monkeypatch.setenv("MCP_GATE_SOCKET", str(platform["tmp"] / "nowhere.sock"))
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "continue")
    set_rule(platform["config"], "marker", phone="approve")
    reply = host({"type": "confirm_prompt", "site": "chatgpt", "text": "WALLED_SENSITIVE"})
    assert reply["decision"] == "continue" and reply["via"] == "desktop"


# ----------------------------------------------------------------- MCP gate × rules
def test_mcp_call_with_sensitive_data_hides_inputs_and_cannot_be_always_allowed(platform):
    params = {"name": "send_email", "arguments": {"body": "Aadhaar 2345 6789 0124"}}
    reply: dict = {}
    t = threading.Thread(target=lambda: reply.update(daemon_call(
        {"op": "approve", "server": "mail", "method": "tools/call", "params": params}, timeout=20)))
    t.start()
    message_id, prompt = wait_for(lambda: platform["api"].prompts() and platform["api"].prompts()[-1])
    assert "Sensitive data in this request" in prompt["text"] and "2345 6789 0124" not in prompt["text"]
    buttons = [b["callback_data"][0] for row in prompt["reply_markup"]["inline_keyboard"] for b in row]
    assert "w" not in buttons                                             # no "Always allow"
    tap(platform["remote"], message_id, prompt, "a")
    t.join(10)
    assert reply["decision"] == "allow"


def test_always_allow_does_not_cover_a_sensitive_call(platform):
    daemon = platform["service"].daemon
    daemon.rules.add(("mail", "send_email"))
    clean = daemon_call({"op": "approve", "server": "mail", "method": "tools/call",
                         "params": {"name": "send_email", "arguments": {"body": "hello"}}}, timeout=5)
    assert clean["reason"] == "auto"
    t = threading.Thread(target=lambda: daemon_call({"op": "approve", "server": "mail", "method": "tools/call",
        "params": {"name": "send_email", "arguments": {"body": "card 4111 1111 1111 1111"}}}, timeout=20), daemon=True)
    t.start()
    wait_for(lambda: platform["api"].prompts())                           # it asked instead of auto-allowing


def test_folder_rule_blocks_mcp_call(platform, tmp_path):
    from walled_guard.rules import Rule
    cfg = platform["config"].load()
    cfg.rules.append(Rule("Finance", "folder", str(tmp_path / "Finance"), action="block", browser=False))
    platform["config"].save(cfg)
    reply = daemon_call({"op": "approve", "server": "fs", "method": "tools/call",
                         "params": {"name": "read_file", "arguments": {"path": str(tmp_path / "Finance" / "q1.pdf")}}})
    assert reply == {"decision": "deny", "reason": "blocked by rule"}
    assert "Blocked automatically" in platform["api"].all_text()


# ----------------------------------------------------------------- vault × Telegram
def test_password_fill_needs_a_telegram_tap_and_never_sends_the_password(platform):
    vault = platform["service"].vault
    vault.create("correct horse battery")
    entry = vault.save_entry("github.com", "vibhav@example.com", "Sup3r-Secret-PW!")
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(host(
        {"type": "vault_fill", "origin": "https://github.com", "entry": entry.id})))
    t.start()
    message_id, prompt = wait_for(lambda: platform["api"].prompts() and platform["api"].prompts()[-1])
    assert "github.com" in prompt["text"] and "vibhav@example.com" not in prompt["text"]
    tap(platform["remote"], message_id, prompt, "a")
    t.join(10)
    assert result == {"ok": True, "username": "vibhav@example.com", "password": "Sup3r-Secret-PW!"}
    assert "Sup3r-Secret-PW!" not in platform["api"].all_text()


def test_fill_blocked_on_telegram(platform):
    vault = platform["service"].vault
    vault.create("correct horse battery")
    entry = vault.save_entry("github.com", "me", "pw-123456")
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(host({"type": "vault_fill", "origin": "https://github.com", "entry": entry.id})))
    t.start()
    message_id, prompt = wait_for(lambda: platform["api"].prompts() and platform["api"].prompts()[-1])
    tap(platform["remote"], message_id, prompt, "b")
    t.join(10)
    assert result["ok"] is False and "password" not in result


def test_fill_refused_for_another_site(platform):
    vault = platform["service"].vault
    vault.create("correct horse battery")
    entry = vault.save_entry("github.com", "me", "pw-123456")
    reply = host({"type": "vault_fill", "origin": "https://github.com.evil.example", "entry": entry.id})
    assert reply["ok"] is False and not platform["api"].prompts()


def test_locked_vault_and_telegram_lock(platform):
    vault = platform["service"].vault
    vault.create("correct horse battery")
    assert host({"type": "vault_find", "origin": "https://github.com"})["ok"] is True
    assert "locked" in platform["service"].telegram_command("/lock").lower()
    reply = host({"type": "vault_find", "origin": "https://github.com"})
    assert reply["ok"] is False and reply["locked"] is True


def test_service_status_over_socket(platform):
    status = daemon_call({"op": "status"})
    assert status["service"] == "walled" and status["telegram"] is True


# ----------------------------------------------------------------- Control Center API
@pytest.fixture
def control(platform):
    from walled.control import ControlServer
    server = ControlServer(platform["service"])
    server.start()
    yield server
    server.stop()


def req(server, path, method="GET", body=None, token=True, host=None):
    r = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", method=method,
                               data=json.dumps(body).encode() if body is not None else None)
    if token:
        r.add_header("X-Walled-Token", server.token)
    if host:
        r.add_header("Host", host)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def test_api_requires_token_and_loopback_host(control):
    assert req(control, "/api/state", token=False)[0] == 401
    assert req(control, "/api/state", host="evil.example:80")[0] == 403
    code, state = req(control, "/api/state")
    assert code == 200 and state["telegram"]["running"] is True
    with urllib.request.urlopen(f"http://127.0.0.1:{control.port}/", timeout=5) as resp:   # UI shell: no token
        page = resp.read().decode()
        assert "Walled" in page and "default-src 'self'" in resp.headers["Content-Security-Policy"]


def test_api_rules_roundtrip_and_validation(control):
    code, data = req(control, "/api/rules")
    rules = data["rules"]
    rules.append({"name": "Acme", "kind": "keyword", "pattern": "Acme Corp"})
    code, data = req(control, "/api/rules", "PUT", {"rules": rules})
    assert code == 200 and any(r["pattern"] == "Acme Corp" for r in data["rules"])
    code, data = req(control, "/api/rules/test", "POST", {"text": "deal with ACME corp"})
    assert [h["name"] for h in data["hits"]] == ["Acme"]
    bad = rules + [{"name": "x", "kind": "regex", "pattern": "(?i)x"}]
    assert req(control, "/api/rules", "PUT", {"rules": bad})[0] == 400


def test_api_vault_flow(control):
    assert req(control, "/api/vault/create", "POST", {"master": "short"})[0] == 400
    code, st = req(control, "/api/vault/create", "POST", {"master": "a long master pass"})
    assert st["unlocked"] is True
    code, entry = req(control, "/api/vault/entries", "POST", {"origin": "https://x.example", "username": "u", "password": "p1-secret"})
    assert "password" not in entry
    code, entries = req(control, "/api/vault/entries")
    assert entries["entries"][0]["origin"] == "x.example"
    assert req(control, f"/api/vault/entries/{entry['id']}/password")[1]["password"] == "p1-secret"
    req(control, "/api/vault/lock", "POST", {})
    assert req(control, "/api/vault/entries")[0] == 400
    assert req(control, "/api/vault/unlock", "POST", {"master": "wrong master pw"})[0] == 400
    assert req(control, "/api/vault/unlock", "POST", {"master": "a long master pass"})[1]["entries"] == 1


def test_api_connectors_guard_claude_desktop(control, tmp_path, monkeypatch):
    from mcp_approval_gate import desktop_config
    cfg = tmp_path / "claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"files": {"command": "npx", "args": ["server"]}}}))
    monkeypatch.setattr(desktop_config, "default_config_path", lambda: cfg)
    assert req(control, "/api/connectors")[1]["servers"] == {"files": "UNGUARDED"}
    code, data = req(control, "/api/connectors/wrap", "POST", {"names": ["files"]})
    assert data["servers"] == {"files": "guarded"} and data["changed"]
    code, data = req(control, "/api/connectors/unwrap", "POST", {})
    assert data["servers"] == {"files": "UNGUARDED"}


def test_api_settings_are_clamped(control):
    code, s = req(control, "/api/settings", "PUT", {"dialog_timeout": 5, "fill_approval": "weird", "evil": 1})
    assert s["dialog_timeout"] == 15 and s["fill_approval"] == "phone" and "evil" not in s
