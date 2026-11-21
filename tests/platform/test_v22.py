"""v2.2: event bus, activity channel, token pairing from the browser, test center, keychain vault,
side-panel tickets, samples and practice pages, host live channel, self-repair."""

from __future__ import annotations

import io
import json
import struct
import threading
import time
import urllib.request

import pytest

from test_platform import control, platform, req, set_rule, tap, wait_for  # noqa: F401  (fixtures)


# ----------------------------------------------------------------- event bus
def test_event_bus_long_poll_and_restart():
    from walled.events import EventBus
    bus = EventBus()
    got = []
    t = threading.Thread(target=lambda: got.extend(bus.since(0, 5)))
    t.start()
    time.sleep(0.1)
    bus.publish("x.happened", "Something happened", tone="ok", detail=1)
    t.join(2)
    assert got and got[0].title == "Something happened" and got[0].data == {"detail": 1}
    fresh = EventBus()                                                        # the service restarted
    started = time.monotonic()
    assert fresh.since(999, 0.3) == [] and time.monotonic() - started < 1
    fresh.publish("y", "Next")
    assert [e.title for e in fresh.since(999, 0)] == ["Next"]                 # replayed to a client that was ahead


def test_events_over_socket_and_api(platform, control):  # noqa: F811
    from mcp_approval_gate.daemon_client import call
    svc = platform["service"]
    svc.events.publish("vault.created", "Password vault created", tone="ok")
    reply = call({"op": "events", "since": 0, "timeout": 0})
    assert reply["ok"] and reply["events"][-1]["title"] == "Password vault created"
    data = req(control, "/api/events?since=0")[1]
    assert data["last"] == reply["last"]


# ----------------------------------------------------------------- activity mirror
def test_activity_is_mirrored_to_the_log_channel_only(platform):  # noqa: F811
    from mcp_approval_gate.daemon_client import call
    remote = platform["remote"]
    remote.alert_chat_id = -100123
    call({"op": "activity", "site": "ChatGPT", "kind": "prompt", "decision": "cancel", "via": "desktop",
          "rules": ["Payment card number"], "kinds": []})
    sent = wait_for(lambda: [m for m in platform["api"].sent if m.get("chat_id") == -100123])
    assert "Stopped" in sent[0]["text"] and "Payment card number" in sent[0]["text"]
    assert not [m for m in platform["api"].sent if m.get("chat_id") == remote.chat_id]   # approvals chat stays clean
    assert any(e.type == "guard.decision" for e in platform["service"].events.since(0))


def test_no_log_channel_means_no_mirror(platform):  # noqa: F811
    platform["service"].record_activity({"site": "ChatGPT", "kind": "prompt", "decision": "cancel"})
    time.sleep(0.2)
    assert platform["api"].sent == []


def test_host_reports_decisions_with_file_kinds(platform, monkeypatch):  # noqa: F811
    import base64
    from walled_guard import host
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "cancel")
    host.handle({"type": "inspect_files", "site": "chatgpt",
                 "files": [{"name": "a.txt", "data": base64.b64encode(b"WALLED_SENSITIVE").decode()}]})
    event = wait_for(lambda: [e for e in platform["service"].events.since(0) if e.type == "guard.decision"])[-1]
    assert event.data["kind"] == "file" and event.data["kinds"] == ["text"] and event.data["decision"] == "cancel"


def test_activity_feeds_the_test_center(platform):  # noqa: F811
    svc = platform["service"]
    svc.record_activity({"site": "Perplexity", "kind": "file", "decision": "cancel", "kinds": ["pdf", "image"],
                         "rules": ["Payment card number"], "via": "phone"})
    tests = platform["config"].load().settings.tests
    assert {"file_pdf", "file_image", "detector_card", "race"} <= set(tests)
    assert any(e.type == "test.passed" for e in svc.events.since(0))


# ----------------------------------------------------------------- pairing from the browser, tickets
def test_pair_token_from_the_extension(platform, control, monkeypatch):  # noqa: F811
    from walled_guard import host

    def fake_pair(token):
        with control.pairing.lock:
            control.pairing.bot, control.pairing.code, control.pairing.raw = "b_bot", "/pair XYZ123456789", "XYZ123456789"
            control.pairing.deadline = time.monotonic() + 120
    monkeypatch.setattr(control, "_pair", fake_pair)
    platform["service"].control = control
    assert host.handle({"type": "pair_token", "token": "nope"})["ok"] is False
    reply = host.handle({"type": "pair_token", "token": "123456789:AAH" + "x" * 32})
    assert reply["ok"] and reply["link"] == "https://t.me/b_bot?start=XYZ123456789" and reply["code"] == "/pair XYZ123456789"
    assert any(e.type == "telegram.pairing" for e in platform["service"].events.since(0))


def test_side_panel_ticket(platform, control):  # noqa: F811
    from walled_guard import host
    platform["service"].control = control
    reply = host.handle({"type": "ticket"})
    assert reply["ok"] and reply["url"] == f"http://127.0.0.1:{control.port}/"
    assert req(control, "/api/session", "POST", {"ticket": reply["ticket"]}, token=False)[0] == 200


def test_app_may_only_be_framed_by_walled(control):  # noqa: F811
    with urllib.request.urlopen(f"http://127.0.0.1:{control.port}/", timeout=5) as resp:
        csp = resp.headers["Content-Security-Policy"]
        assert "frame-ancestors 'self' chrome-extension://lbnpgknepeocidimhgmodenkcfngmkdj" in csp
        assert resp.headers.get("X-Frame-Options") is None


def test_telegram_web_setup_asks_the_extension(platform, control):  # noqa: F811
    data = req(control, "/api/telegram/web-setup", "POST", {})[1]
    assert data == {"extension": False}
    assert platform["service"].events.since(0)[-1].type == "assist.telegram_web"


# ----------------------------------------------------------------- test center
def test_dialog_and_telegram_tests(platform, control, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "continue")
    assert req(control, "/api/tests/dialog", "POST", {})[1] == {"ok": True, "choice": "continue"}
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(req(control, "/api/tests/telegram", "POST", {})[1]))
    t.start()
    message_id, prompt = wait_for(lambda: platform["api"].prompts() and platform["api"].prompts()[-1])
    assert "Walled test" in prompt["text"]
    tap(platform["remote"], message_id, prompt, "a")
    t.join(10)
    assert result == {"ok": True, "choice": "allow", "reason": "allow"}
    tests = req(control, "/api/tests")[1]["tests"]
    assert tests["dialog"]["ok"] and tests["telegram"]["ok"] and tests["race"]["ok"] is None


def test_race_test_phone_wins(platform, control, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "wait")             # nobody at the computer
    result: dict = {}
    t = threading.Thread(target=lambda: result.update(req(control, "/api/tests/race", "POST", {})[1]))
    t.start()
    message_id, prompt = wait_for(lambda: platform["api"].prompts() and platform["api"].prompts()[-1])
    tap(platform["remote"], message_id, prompt, "b")
    t.join(10)
    assert result == {"ok": True, "via": "phone", "choice": "cancel"}


def test_telegram_tests_need_pairing(platform, control):  # noqa: F811
    platform["service"].daemon = None
    code, data = req(control, "/api/tests/telegram", "POST", {})
    assert code == 400 and "Pair Telegram" in data["error"]


def test_samples_and_practice_pages(control):  # noqa: F811
    with urllib.request.urlopen(f"http://127.0.0.1:{control.port}/samples/secret_report.pdf", timeout=5) as resp:
        assert resp.read(5) == b"%PDF-"
    with urllib.request.urlopen(f"http://127.0.0.1:{control.port}/practice/signup", timeout=5) as resp:
        assert b"new-password" in resp.read()
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{control.port}/samples/../control.py", timeout=5)
        raise AssertionError("path traversal should be refused")
    except urllib.error.HTTPError as err:
        assert err.code == 404


# ----------------------------------------------------------------- vault: keychain mode
def test_keychain_vault(platform, control, monkeypatch):  # noqa: F811
    from walled import keychain
    store: dict[str, str] = {}
    monkeypatch.setattr(keychain, "available", lambda: True)
    monkeypatch.setattr(keychain, "store", lambda s: store.update(k=s))
    monkeypatch.setattr(keychain, "load", lambda: store.get("k"))
    st = req(control, "/api/vault/create", "POST", {"mode": "keychain"})[1]
    assert st["unlocked"] and st["mode"] == "keychain" and len(store["k"]) >= 40
    req(control, "/api/vault/lock", "POST", {})
    assert req(control, "/api/vault/unlock", "POST", {})[1]["unlocked"] is True        # nothing to type
    store.clear()
    req(control, "/api/vault/lock", "POST", {})
    code, data = req(control, "/api/vault/unlock", "POST", {})
    assert code == 400 and "keychain" in data["error"]


def test_keychain_unavailable_is_explained(platform, control, monkeypatch):  # noqa: F811
    from walled import keychain
    monkeypatch.setattr(keychain, "available", lambda: False)
    code, data = req(control, "/api/vault/create", "POST", {"mode": "keychain"})
    assert code == 400 and "passphrase" in data["error"]


def test_wrong_passphrase_message_is_clear(platform):  # noqa: F811
    from walled.vault import VaultError
    vault = platform["service"].vault
    vault.create("a long vault passphrase")
    vault.lock()
    with pytest.raises(VaultError, match="not your computer password"):
        vault.unlock("my mac password")


def test_keychain_commands_never_put_the_secret_on_argv(monkeypatch):
    from walled import keychain
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(keychain.sys, "platform", "darwin")
    monkeypatch.setattr(keychain.subprocess, "run", lambda cmd, **kw: (calls.append((cmd, kw.get("input"))), Done())[1])
    keychain.store("S3CRET-VALUE")
    cmd, stdin = calls[0]
    assert cmd == ["security", "-i"] and "S3CRET-VALUE" not in " ".join(cmd) and "S3CRET-VALUE" in stdin


# ----------------------------------------------------------------- native host live channel
def test_host_subscribe_forwards_events(platform):  # noqa: F811
    import os
    from walled_guard import host
    read_fd, write_fd = os.pipe()
    out = io.BytesIO()

    class Pipe(io.RawIOBase):
        def readable(self):
            return True

        def readinto(self, b):
            data = os.read(read_fd, len(b))
            b[:len(data)] = data
            return len(data)
    stdin = io.BufferedReader(Pipe())
    t = threading.Thread(target=host.subscribe, args=(stdin, out, 0), daemon=True)
    t.start()
    time.sleep(0.3)
    platform["service"].events.publish("telegram.paired", "Telegram paired with @b", tone="ok")
    wait_for(lambda: out.getvalue())
    os.close(write_fd)                                           # Chrome disconnects
    t.join(5)
    raw = out.getvalue()
    message = json.loads(raw[4:4 + struct.unpack("<I", raw[:4])[0]])
    assert message["type"] == "event" and message["event"]["title"] == "Telegram paired with @b"


# ----------------------------------------------------------------- deep links for the log channel
def test_alert_pairing_accepts_startgroup_deep_link():
    from mcp_approval_gate.telegram_approver import pair_alert_chat
    codes: list[str] = []

    class API:
        n = 0

        def call(self, method, payload, timeout=12):
            if method in ("getMe", "sendMessage"):
                return {"username": "b", "message_id": 1}
            API.n += 1
            if API.n == 1:
                return []
            code = codes[0].split("/alertpair ")[1].split()[0]
            return [{"update_id": 3, "message": {"text": f"/start@walled_bot {code}", "chat": {"id": -555, "type": "supergroup"},
                                                 "from": {"id": 7, "is_bot": False}}}]
    assert pair_alert_chat("t", 7, api=API(), announce=codes.append) == -555


def test_self_repair_registers_browser(tmp_path, monkeypatch):
    from walled.service import WalledService
    monkeypatch.setenv("WALLED_GUARD_HOME", str(tmp_path / "w"))
    from walled import paths
    from walled_guard import installer
    called = []
    monkeypatch.setattr(installer, "installed", lambda: [])
    monkeypatch.setattr(installer, "install", lambda *a, **k: called.append("install") or [])
    monkeypatch.setattr(paths, "extension_home", lambda: tmp_path / "ext")
    WalledService(tmp_path / "v.json").heal()
    assert called == ["install"] and (tmp_path / "ext" / "manifest.json").exists()
