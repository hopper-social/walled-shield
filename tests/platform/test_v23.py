"""v2.3: simpler install (open chrome://extensions with the path copied) and the Pip assistant assets."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def test_open_extensions_page_copies_path_and_opens_chrome(home, monkeypatch):
    from walled import paths
    calls: list[list[str]] = []
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("xclip", "chromium") else None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(["run", *cmd]) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(["popen", *cmd]))
    r = paths.open_extensions_page()
    assert r["copied"] is True and r["browser"] == "chromium"
    assert (Path(str(r["path"])) / "manifest.json").exists()            # the folder is (re)created first
    assert ["run", "xclip", "-selection", "clipboard"] in calls
    assert ["popen", "chromium", "chrome://extensions"] in calls


def test_open_extensions_page_without_browser_or_clipboard(home, monkeypatch):
    from walled import installation, paths
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setattr(paths.shutil, "which", lambda name: None)
    r = paths.open_extensions_page()
    assert r == {"browser": None, "copied": False, "path": str(paths.extension_home())}
    hint = installation.extension_hint()
    assert "Open chrome://extensions in Chrome" in hint and str(paths.extension_home()) in hint
    assert "Developer mode" in hint and "Load unpacked" in hint


def test_release_bundle_has_double_click_installers():
    build = (ROOT / "scripts" / "build_release.py").read_text()
    assert "Install Walled.command" in build and "START HERE.txt" in build
    for name in ("Install Walled.command", "Uninstall Walled.command"):
        f = ROOT / "packaging" / name
        assert f.read_text().startswith("#!/bin/bash") and f.stat().st_mode & 0o111
    installer = (ROOT / "install.sh").read_text()
    assert "read -r -p" not in installer                                 # no questions asked
    assert "walled\" extension" in installer and "--no-open" in installer
    assert subprocess.run(["bash", "-n", str(ROOT / "install.sh")]).returncode == 0


def test_pip_assets_are_wired_in():
    web = ROOT / "src" / "walled" / "web"
    index = (web / "index.html").read_text()
    assert index.index("pet.js") < index.index("app.js")
    assert 'class="dock"' in index and 'class="side"' in index        # sidebar on desktop, dock when compact
    pet = (web / "pet.js").read_text()
    assert "PipArt.figure" in pet and index.index("pip-art.js") < index.index("pet.js")
    companion = (ROOT / "src" / "walled" / "extension" / "src" / "companion.js").read_text()
    assert "PipArt.markup" in companion and "poof(" in companion


# ---------------------------------------------------------------------------------- Pip the pet
@pytest.fixture
def walled_home(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLED_GUARD_HOME", str(tmp_path / "w"))
    return tmp_path


def test_pet_settings_are_normalised(walled_home):
    from walled_guard import config
    s = config.Settings.from_json({"pet": {"mood": "hyper", "sound": "chimes", "volume": 900, "where": "moon", "enabled": 0}})
    assert s.pet == {**config.PET_DEFAULTS, "sound": "chimes", "volume": 100, "enabled": False, "intro": False}
    assert config.Settings().pet == config.PET_DEFAULTS


def test_browser_pip_only_when_desktop_pip_is_not_running(walled_home):
    import os
    from walled_guard import config
    s = config.Settings()
    assert config.pet_for_browser(s)["in_browser"] is True               # nothing on the desktop yet
    config.pet_pid_file().parent.mkdir(parents=True, exist_ok=True)
    config.pet_pid_file().write_text(str(os.getpid()))                   # a live process = desktop Pip
    assert config.pet_for_browser(s)["in_browser"] is False
    s.pet["where"] = "both"
    assert config.pet_for_browser(s)["in_browser"] is True
    config.pet_pid_file().write_text("999999999")                        # stale pid file → not running
    s.pet["where"] = "auto"
    assert config.pet_for_browser(s)["in_browser"] is True
    s.pet["enabled"] = False
    assert config.pet_for_browser(s)["in_browser"] is False


def test_pet_window_is_pinned_to_the_corner(walled_home):
    from walled import pet
    assert pet.anchor((230, 124), (1440, 900), "bottom-right") == (1440 - 230 - pet.MARGIN_X, 900 - 124 - pet.MARGIN_Y)
    assert pet.anchor((230, 124), (1440, 900), "bottom-left") == (pet.MARGIN_X, 900 - 124 - pet.MARGIN_Y)

    class Win:
        calls: list = []
        def resize(self, w, h): self.calls.append(("resize", w, h))
        def move(self, x, y): self.calls.append(("move", x, y))
    bridge = pet.Bridge((1440, 900))
    bridge._window = Win()
    assert bridge.resize("open") == list(pet.OPEN)
    assert Win.calls == [("resize", *pet.OPEN), ("move", *pet.anchor(pet.OPEN, (1440, 900), "bottom-right"))]
    assert bridge.resize("idle") == list(pet.IDLE)
    assert not [n for n in dir(bridge) if not n.startswith("_") and not callable(getattr(bridge, n))]   # only methods reach JS


def test_pet_without_pywebview_explains_and_spawn_is_a_no_op(walled_home, monkeypatch, capsys):
    from walled import pet
    monkeypatch.setattr(pet, "available", lambda: False)
    assert pet.run() == 1 and "ChatGPT" in capsys.readouterr().out
    assert pet.spawn() is False


def test_pet_kwargs_filter_unknown_pywebview_options():
    from walled import pet
    def create_window(title, url=None, width=800, on_top=False):
        return None
    assert pet._kwargs(create_window, {"width": 1, "on_top": True, "transparent": True}) == {"width": 1, "on_top": True}


def test_claude_waiting_only_when_a_human_is_asked(walled_home):
    import time as _time
    from walled.service import WalledService
    service = WalledService(walled_home / "vault.json")
    service.mcp_approval_event("asking", "fs", "tools/call", False, "")
    service.mcp_approval_event("answered", "fs", "tools/call", True, "auto")       # instant always-allow
    _time.sleep(1.8)
    assert [e.type for e in service.events.since(0)] == []
    service.mcp_approval_event("asking", "github", "tools/call", False, "")
    _time.sleep(1.8)                                                                # still waiting → Pip speaks up
    service.mcp_approval_event("answered", "github", "tools/call", False, "deny")
    types = [e.type for e in service.events.since(0)]
    assert types == ["claude.waiting", "claude.decision"]
    assert service.events.since(0)[-1].tone == "bad"


def test_host_ping_tells_the_extension_about_pip(walled_home, monkeypatch):
    from walled_guard import client, host
    monkeypatch.setattr(client, "call", lambda *a, **k: None)
    reply = host.handle({"type": "ping"})
    assert reply["pet"]["in_browser"] is True and reply["pet"]["sound"] == "purr"


def test_shared_runtime_is_served_and_packaged():
    ext = ROOT / "src" / "walled" / "extension"
    import json as _json
    manifest = _json.loads((ext / "manifest.json").read_text())
    scripts = [s["js"] for s in manifest["content_scripts"]]
    assert ["src/pip-art.js", "src/buddy.js", "src/buddy-browser.js"] in scripts
    assert ["src/pip-art.js", "src/companion.js"] in scripts
    control = (ROOT / "src" / "walled" / "control.py").read_text()
    assert '"buddy.js", "pip-art.js"' in control
    art = (ext / "src" / "pip-art.js").read_text()
    for expr in ("happy", "thumbs", "love", "calm", "alert", "protect", "confused", "think", "sleep", "sad",
                 "angry", "shy", "surprised", "cheer", "warn", "busy"):
        assert f"    {expr}:" in art
    assert 'protect:   ["stern", "firm", "shield", "", "green"' in art          # Protecting is green
    buddy = (ext / "src" / "buddy.js").read_text()
    for theme in ("purr", "chimes", "bubbles"):
        assert f'"{theme}"' in buddy
    assert "adoptedStyleSheets" in buddy                     # works under the app's strict CSP
    assert ".innerHTML" not in buddy                                         # notes are built with textContent
