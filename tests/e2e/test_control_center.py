"""The Control Center UI in a real browser: launch ticket, setup quest, dashboard, vault."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHROME = os.environ.get("CHROME") or "/opt/pw-browsers/chromium-1243/real/chrome-linux64/chrome"


@pytest.fixture
def app(tmp_path, monkeypatch, pw):
    if not Path(CHROME).exists():
        pytest.skip("no Chrome binary")
    sys.path.insert(0, str(ROOT / "src"))
    monkeypatch.setenv("WALLED_GUARD_HOME", str(tmp_path / "w"))
    gate = tmp_path / "g"
    gate.mkdir(mode=0o700)
    monkeypatch.setenv("MCP_GATE_STATE_DIR", str(gate))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    from walled.control import ControlServer
    from walled.service import WalledService
    service = WalledService(tmp_path / "w" / "vault.json")
    server = ControlServer(service)
    server.start()
    browser = pw.chromium.launch(executable_path=CHROME)
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield {"page": page, "browser": browser, "server": server, "service": service, "errors": errors}
    browser.close()
    server.stop()


def open_app(app):
    ticket = app["server"].new_ticket()
    app["page"].goto(f"http://127.0.0.1:{app['server'].port}/#k={ticket}")
    return ticket


def test_ticket_opens_once_and_quest_runs_to_the_dashboard(app):
    page = app["page"]
    ticket = open_app(app)
    page.wait_for_selector(".quest", timeout=10000)
    assert "#k=" not in page.url                                         # ticket removed from the address bar
    page.click("button:has-text('Start →')")
    page.wait_for_selector("text=Connect your browser")
    app["service"].extension_seen = time.time()                          # the extension says hi
    page.wait_for_selector(".live.ok >> text=Extension connected", timeout=10000)
    for _ in range(6):                                                   # first catch, Telegram, Claude…
        if "vault" in page.inner_text(".quest-stage h1").lower():
            break
        page.click("text=Skip for now")
        page.wait_for_timeout(600)
    assert "vault" in page.inner_text(".quest-stage h1").lower()
    page.click("text=Choose a vault passphrase")
    page.wait_for_selector("text=This is not your Mac password")
    page.fill("input[placeholder='Vault passphrase']", "correct horse battery")
    page.fill("input[placeholder='Type it again']", "correct horse battery")
    page.click("button:text-is('Create vault')")
    page.wait_for_selector("text=Tick the box")                          # must confirm it's saved
    page.check("input[aria-label='I have saved it']")
    page.click("button:text-is('Create vault')")
    page.wait_for_selector("text=Vault ready")
    assert app["service"].vault.status()["unlocked"]
    page.click("button:has-text('Continue →')")
    page.wait_for_selector(".finale")
    page.click("text=Go to dashboard")
    page.wait_for_selector(".assist .pa")
    assert app["errors"] == []
    # the same ticket cannot open a second window
    other = app["browser"].new_page()
    other.goto(f"http://127.0.0.1:{app['server'].port}/#k={ticket}")
    other.wait_for_selector("text=Open Walled from the app")


def test_dashboard_rules_and_vault_pages_render(app):
    from walled_guard import config
    cfg = config.load()
    cfg.settings.onboarded = True
    config.save(cfg)
    page = app["page"]
    open_app(app)
    page.wait_for_selector(".assist .pa")
    for view, marker in [("sensitive", "Your words & markers"), ("telegram", "Guided in Telegram Web"),
                         ("browser", "Load the extension"), ("vault", "Choose a vault passphrase"), ("tests", "verified"),
                         ("activity", "Every decision, in real time"), ("settings", "Confirm before filling"), ("claude", "Claude Desktop servers")]:
        page.evaluate(f"location.hash = '#{view}'")
        page.wait_for_selector(f"text={marker}", timeout=8000)
    page.evaluate("location.hash = '#telegram'")
    page.click("text=Paste a bot token")
    page.fill("input[aria-label='Bot token']", "nope")
    assert page.is_disabled("button:text-is(\"Connect\")")
    page.fill("input[aria-label='Bot token']", "123456789:AAH" + "x" * 32)
    assert page.get_attribute("input[aria-label='Bot token']", "type") == "text"       # no password-manager prompt
    assert page.get_attribute("input[aria-label='Bot token']", "autocomplete") == "off"
    assert not page.is_disabled("button:text-is(\"Connect\")")
    assert app["errors"] == []


def test_pip_desktop_page_pets_alerts_and_follows_settings(app):
    """buddy.html: what the transparent desktop window shows (walled pet)."""
    from walled_guard import config
    page = app["page"]
    page.set_viewport_size({"width": 360, "height": 450})
    page.goto(f"http://127.0.0.1:{app['server'].port}/buddy.html#k={app['server'].new_ticket()}")
    pip = page.wait_for_selector(".wb-pip")
    page.wait_for_function("window.__buddy !== undefined")
    # first run: Pip says hello once and explains the two gestures
    hello = page.wait_for_selector(".wb-card:has-text('Hi, I')", timeout=6000)
    assert "Stroke me" in hello.inner_text()
    page.click("button:text-is('Nice to meet you')")
    page.wait_for_timeout(500)
    assert config.load().settings.pet["intro"] is True
    page.evaluate("window.__buddy.closeCard()")
    page.wait_for_timeout(2500)                                          # let any walk finish
    box = pip.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)                                              # hovering: he stops to pay attention
    page.wait_for_timeout(1200)
    box = pip.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx - 20, cy)
    for k in range(70):                                                  # stroke him for ~3 s
        page.mouse.move(cx + (18 if k % 2 == 0 else -18), cy, steps=4)
        page.wait_for_timeout(35)
    assert "petting" in pip.get_attribute("class")
    assert page.evaluate("window.__buddy.current") == "love"            # shy → love
    assert "love" in page.evaluate("window.__buddy.sound.played")
    page.mouse.move(5, 5)
    page.wait_for_timeout(900)
    # one click → one small panel; choices are saved
    pip.click()
    assert "boop" in page.evaluate("window.__buddy.sound.played")
    page.click(".wb-panel button:text-is('Chimes')")
    page.wait_for_timeout(400)
    assert config.load().settings.pet["sound"] == "chimes"
    page.click(".wb-panel .wb-x")
    page.wait_for_selector(".wb-panel", state="detached")
    # something important: a held-back prompt → a clear note with Pip protecting (green) + the calm alert sound
    app["service"].record_activity({"site": "ChatGPT", "kind": "prompt", "decision": "cancel", "rules": ["Client: Acme Corp"]})
    note = page.wait_for_selector(".wb-card.safe", timeout=8000)
    assert "held that back" in note.inner_text() and "Client: Acme Corp" in note.inner_text()
    assert note.query_selector(".pa[data-expr='protect'][data-aura='green']")
    assert "alert" in page.evaluate("window.__buddy.sound.played")
    # the "test passed" news that came right after doesn't bury it
    assert page.query_selector(".wb-card.safe")
    page.click(".wb-card button:text-is('Got it')")
    page.wait_for_selector(".wb-card", state="detached")
    # settings from the app reach him live
    import json as _json
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{app['server'].port}/api/settings", method="PUT",
                                 data=_json.dumps({"pet": {"corner": "bottom-left", "mood": "focus"}}).encode(),
                                 headers={"X-Walled-Token": app["server"].token, "Content-Type": "application/json"})
    urllib.request.urlopen(req).read()
    page.wait_for_selector(".wb[data-corner='bottom-left']", timeout=8000)
    assert app["errors"] == []


def test_pip_gets_grumpy_when_over_petted_and_asks_for_attention(app):
    page = app["page"]
    page.set_viewport_size({"width": 360, "height": 450})
    from walled_guard import config
    cfg = config.load(); cfg.settings.pet["intro"] = True; config.save(cfg)
    page.goto(f"http://127.0.0.1:{app['server'].port}/buddy.html#k={app['server'].new_ticket()}")
    pip = page.wait_for_selector(".wb-pip")
    page.wait_for_function("window.__buddy !== undefined")
    page.wait_for_timeout(1000)
    box = pip.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx - 15, cy)
    for k in range(160):                                    # non-stop gentle petting until he's had enough (~8 s)
        page.mouse.move(cx + (15 if k % 2 == 0 else -15), cy, steps=3)
        page.wait_for_timeout(55)
        if page.evaluate("window.__buddy.current") == "angry":
            break
    assert page.evaluate("window.__buddy.current") == "angry"
    quip = page.wait_for_selector(".wb-quip", timeout=3000).inner_text()
    assert "grumble" in page.evaluate("window.__buddy.sound.played")
    assert any(w in quip for w in ("enough", "pats", "space", "said", "Hmph", "Not now"))
    page.mouse.move(5, 5)
    page.wait_for_timeout(7500)                             # sulk wears off
    # attention-seeking: he asks, you pet → delighted
    page.evaluate("void window.__buddy.act('beg', true)")      # don't await his 12 s wait
    page.wait_for_timeout(400)
    assert page.evaluate("window.__buddy.current") == "shy"
    assert "beg" in page.evaluate("window.__buddy.sound.played")
    box = pip.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx - 15, cy)
    for k in range(10):
        page.mouse.move(cx + (15 if k % 2 == 0 else -15), cy, steps=3)
    assert page.evaluate("window.__buddy.current") == "love"
    assert app["errors"] == []
