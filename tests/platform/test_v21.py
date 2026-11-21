"""v2.1: security fixes, behaviour-based insights, onboarding, deep-link pairing, packaging."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

from test_platform import FakeAPI, control, platform, req, set_rule, tap, wait_for  # noqa: F401  (fixtures)


# ----------------------------------------------------------------- security fixes
def test_state_folder_and_log_are_private(tmp_path, monkeypatch):
    home = tmp_path / "w"
    home.mkdir(mode=0o755)                                   # pre-existing loose folder gets tightened
    monkeypatch.setenv("WALLED_GUARD_HOME", str(home))
    from walled_guard import audit, config
    audit.record("ChatGPT", "prompt", "cancel", rules=["x"])
    config.save(config.load())
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE((home / "decisions.jsonl").stat().st_mode) == 0o600
    assert stat.S_IMODE((home / "config.json").stat().st_mode) == 0o600


def test_pdf_flate_bomb_is_bounded(monkeypatch):
    from walled_guard import inspector
    monkeypatch.setattr(inspector, "MAX_STREAM", 1024 * 1024)
    monkeypatch.setattr(inspector, "MAX_INFLATED", 2 * 1024 * 1024)
    bomb = zlib.compress(b"\0" * (200 * 1024 * 1024), 9)           # 200 MB of zeros in ~200 KB
    pdf = b"%PDF-1.4\n1 0 obj <<>> stream\n" + bomb + b"\nendstream endobj\n%%EOF"
    started = time.monotonic()
    result = inspector.inspect_file("bomb.pdf", pdf)
    assert time.monotonic() - started < 10 and not result.found


def test_redos_patterns_are_rejected_and_custom_regex_is_capped():
    from walled_guard.rules import REGEX_SCAN_LIMIT, Rule, RuleEngine
    for bad in (r"(a+)+$", r"(\w*)*x", r"(x|y+){2,}"):
        with pytest.raises(ValueError):
            Rule("bad", "regex", bad).validate()
    engine = RuleEngine([Rule("tail", "regex", r"NEEDLE")])
    assert not engine.scan("a" * (REGEX_SCAN_LIMIT + 10) + "NEEDLE")      # beyond the cap


def test_launcher_quotes_and_sanitises_path(tmp_path, monkeypatch):
    from walled_guard import installer
    monkeypatch.setenv("PATH", f".:{tmp_path}:relative/bin::/usr/bin")
    launcher = installer.write_launcher(tmp_path / "dir with 'quote", python="/opt/py thon/bin/python3")
    text = launcher.read_text()
    assert "'/opt/py thon/bin/python3'" in text
    path_line = next(line for line in text.splitlines() if line.startswith("export PATH="))
    entries = path_line.split("=", 1)[1].strip("'").split(":")
    assert all(os.path.isabs(e) for e in entries) and "." not in entries and "relative/bin" not in entries


def test_launch_ticket_is_single_use_and_short_lived(control):  # noqa: F811
    code, data = req(control, "/api/ticket", "POST", {})
    ticket = data["ticket"]
    code, session = req(control, "/api/session", "POST", {"ticket": ticket}, token=False)
    assert code == 200 and session["token"] != control.token
    assert req(control, "/api/session", "POST", {"ticket": ticket}, token=False)[0] == 401    # used
    import urllib.request
    r = urllib.request.Request(f"http://127.0.0.1:{control.port}/api/state", headers={"X-Walled-Token": session["token"]})
    assert urllib.request.urlopen(r).status == 200
    control.tickets["old"] = time.monotonic() - 1                                            # expired
    assert req(control, "/api/session", "POST", {"ticket": "old"}, token=False)[0] == 401
    assert req(control, "/api/ticket", "POST", {}, token=False)[0] == 401                    # needs admin


def test_fill_falls_back_to_desktop_dialog_when_telegram_is_off(platform, monkeypatch):  # noqa: F811
    from walled_guard import host
    vault = platform["service"].vault
    vault.create("correct horse battery")
    entry = vault.save_entry("github.com", "me", "pw-123456")
    platform["service"].daemon = None                                       # Telegram not set up
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "cancel")
    assert host.handle({"type": "vault_fill", "origin": "https://github.com", "entry": entry.id})["ok"] is False
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "continue")
    assert host.handle({"type": "vault_fill", "origin": "https://github.com", "entry": entry.id})["password"] == "pw-123456"
    cfg = platform["config"].load()
    cfg.settings.fill_approval = "none"
    platform["config"].save(cfg)
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "cancel")                # no dialog shown at all
    assert host.handle({"type": "vault_fill", "origin": "https://github.com", "entry": entry.id})["ok"] is True


# ----------------------------------------------------------------- behaviour & game layer
def test_suggestions_learn_from_decisions(control):  # noqa: F811
    from walled_guard import audit
    for _ in range(6):
        audit.record("ChatGPT", "prompt", "continue", rules=["Payment card number"], via="desktop")
    for _ in range(5):
        audit.record("ChatGPT", "prompt", "cancel", rules=["WALLED_SENSITIVE marker"], via="phone")
    ins = req(control, "/api/insights")[1]
    ids = [s["id"] for s in ins["suggestions"]]
    assert "warn:builtin-card" in ids and "block:marker" in ids
    data = req(control, "/api/suggestions/apply", "POST", {"id": "warn:builtin-card"})[1]
    rules = {r["id"]: r for r in req(control, "/api/rules")[1]["rules"]}
    assert rules["builtin-card"]["action"] == "warn"
    assert "warn:builtin-card" not in [s["id"] for s in data["suggestions"]]
    data = req(control, "/api/suggestions/dismiss", "POST", {"id": "block:marker"})[1]
    assert "block:marker" not in [s["id"] for s in data["suggestions"]]


def test_score_badges_and_stats_reflect_reality(control, platform):  # noqa: F811
    ins = req(control, "/api/insights")[1]
    before = ins["score"]["xp"]
    assert not next(b for b in ins["badges"] if b["id"] == "first_catch")["earned"]
    from walled_guard import audit
    audit.record("ChatGPT", "prompt", "cancel", rules=["WALLED_SENSITIVE marker"], via="desktop")
    platform["service"].vault.create("correct horse battery")
    ins = req(control, "/api/insights")[1]
    assert ins["score"]["xp"] > before
    assert next(b for b in ins["badges"] if b["id"] == "first_catch")["earned"]
    assert ins["stats"]["stopped"] >= 1
    assert {c["id"] for c in ins["checklist"]} >= {"agent", "extension", "telegram", "vault"}


def test_extension_heartbeat(platform):  # noqa: F811
    from walled_guard import host
    assert platform["service"].extension_seen is None
    reply = host.handle({"type": "ping"})
    assert reply["service"] is True and platform["service"].extension_seen is not None


def test_onboarding_state(control):  # noqa: F811
    assert req(control, "/api/state")[1]["onboarded"] is False
    data = req(control, "/api/onboarding", "PUT", {"onboarded": True, "goals": ["browser", "evil"]})[1]
    assert data == {"onboarded": True, "goals": ["browser"]}
    assert req(control, "/api/state")[1]["goals"] == ["browser"]


def test_session_expiry_reminder(control, platform):  # noqa: F811
    class Soon:
        def active(self): return True
        def expires_at(self): return time.time() + 1800
    platform["remote"].session = Soon()
    ins = req(control, "/api/insights")[1]
    renew = next(s for s in ins["suggestions"] if s["id"] == "renew")
    assert "min" in renew["text"] and renew["action"]["kind"] == "renew"


# ----------------------------------------------------------------- Telegram deep links
def test_pairing_accepts_deep_link_start():
    from mcp_approval_gate.telegram_approver import pair_private_chat
    codes: list[str] = []

    class API:
        calls = 0

        def call(self, method, payload, timeout=12):
            if method == "getMe":
                return {"username": "b"}
            API.calls += 1
            if API.calls == 1:
                return []
            code = codes[0].split("/pair ")[1].split()[0]
            return [{"update_id": 5, "message": {"text": f"/start {code}", "chat": {"id": 7, "type": "private"},
                                                 "from": {"id": 7, "is_bot": False}}}]
    assert pair_private_chat("t", api=API(), announce=codes.append) == (7, 7)


def test_renewal_by_deep_link(platform):  # noqa: F811
    import threading
    daemon = platform["service"].daemon

    class Session:
        on = False
        def active(self): return self.on
        def expires_at(self): return None
        def activate(self): self.on = True
    platform["remote"].session = Session()
    result = {}
    t = threading.Thread(target=lambda: result.update(ok=daemon.renew("abcdefghijkl1234", timeout=5)))
    t.start()
    time.sleep(0.2)
    reply = daemon._renewal_text("/start abcdefghijkl1234")
    t.join(6)
    assert reply and result["ok"] is True


def test_pairing_snapshot_has_link_and_countdown(control):  # noqa: F811
    control.pairing.state, control.pairing.code, control.pairing.raw = "waiting", "/pair CODE123", "CODE123"
    control.pairing.bot, control.pairing.deadline = "walled_bot", time.monotonic() + 90
    snap = req(control, "/api/telegram/pairing")[1]
    assert snap["link"] == "https://t.me/walled_bot?start=CODE123" and 85 <= snap["seconds_left"] <= 90


# ----------------------------------------------------------------- packaging
def test_extension_is_installed_to_a_stable_folder(tmp_path, monkeypatch):
    from walled import paths
    monkeypatch.setattr(paths, "extension_home", lambda: tmp_path / "Walled Extension")
    target = paths.install_extension()
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["key"] and (target / "src" / "content.js").exists()
    (target / "stale.js").write_text("x")
    paths.install_extension()                                          # refresh replaces old files
    assert not (target / "stale.js").exists()


def test_uninstall_purge_needs_confirmation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WALLED_GUARD_HOME", str(tmp_path / "w"))
    monkeypatch.setenv("MCP_GATE_STATE_DIR", str(tmp_path / "g"))
    (tmp_path / "w").mkdir()
    (tmp_path / "g").mkdir()
    from walled import installation, paths
    monkeypatch.setattr(paths, "extension_home", lambda: tmp_path / "ext")
    monkeypatch.setattr(installation, "mac_app_path", lambda: tmp_path / "Walled.app")
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    installation.uninstall(["--purge"])
    assert (tmp_path / "w").exists()
    installation.uninstall(["--purge", "--yes"])
    assert not (tmp_path / "w").exists() and not (tmp_path / "g").exists()


def test_macos_app_bundle(tmp_path):
    from walled import installation
    import plistlib
    app = installation.build_mac_app("/venv/bin/python", tmp_path / "Walled.app")
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "Walled" and info["CFBundleIdentifier"] == installation.APP_ID
    exe = app / "Contents" / "MacOS" / "Walled"
    assert os.access(exe, os.X_OK) and "-m walled app" in exe.read_text()


def test_cli_entry_points():
    out = subprocess.run([sys.executable, "-m", "walled", "--version"], capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")})
    assert out.stdout.strip().startswith("walled 2.")
