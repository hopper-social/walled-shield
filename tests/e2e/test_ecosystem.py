"""v2.2 ecosystem, end to end in a real Chrome with the real extension, agent and service:

* the Telegram Web assistant drives BotFather (login → consent → /newbot → name → username →
  token spotted → connected → START) on a stand-in web.telegram.org;
* live companion cards for service events on the page you're on;
* the secret-copy guard;
* the Walled app inside Chrome's side panel;
* the Claude.ai site adapter.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FIXTURES = Path(__file__).parent / "fixtures"
EXT_ID = "lbnpgknepeocidimhgmodenkcfngmkdj"
CHROME = os.environ.get("CHROME") or "/opt/pw-browsers/chromium-1243/real/chrome-linux64/chrome"
PAGES = {"chatgpt.com": "chatgpt.html", "claude.ai": "claude.html", "web.telegram.org": "telegram.html"}


@pytest.fixture(scope="module")
def eco(tmp_path_factory, pw):
    if not Path(CHROME).exists():
        pytest.skip("no Chrome binary")
    base = tmp_path_factory.mktemp("eco")
    home, gate = base / "walled", base / "gate"
    home.mkdir()
    gate.mkdir(mode=0o700)
    sock = gate / "s.sock"
    env = {"WALLED_GUARD_HOME": str(home), "MCP_GATE_STATE_DIR": str(gate), "MCP_GATE_SOCKET": str(sock),
           "XDG_CONFIG_HOME": str(base / "cfg")}
    old = {k: os.environ.get(k) for k in [*env, "WALLED_GUARD_AUTO_ANSWER"]}
    os.environ.update(env)
    os.environ["WALLED_GUARD_AUTO_ANSWER"] = "cancel"
    sys.path.insert(0, str(SRC))
    from mcp_approval_gate.approval_daemon import serve as serve_socket
    from walled.control import ControlServer
    from walled.service import WalledService
    from walled_guard import installer

    service = WalledService(home / "vault.json")
    control = ControlServer(service)
    control.start()
    service.control = control
    stop = threading.Event()
    threading.Thread(target=serve_socket, args=(service, sock, stop), daemon=True).start()

    real = installer.write_launcher(base / "installed")
    launcher = base / "host.sh"
    launcher.write_text("#!/bin/sh\nexport WALLED_GUARD_AUTO_ANSWER=cancel\n"
                        + "".join(f'export {k}="{v}"\n' for k, v in env.items()) + f'exec "{real}" "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    profile = base / "profile"
    (profile / "NativeMessagingHosts").mkdir(parents=True)
    (profile / "NativeMessagingHosts" / "com.walled.guard.json").write_text(json.dumps({
        "name": "com.walled.guard", "description": "test", "path": str(launcher), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXT_ID}/"]}))
    ext = ROOT / "src" / "walled" / "extension"
    ctx = pw.chromium.launch_persistent_context(str(profile), executable_path=CHROME, headless=False,
        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--no-first-run"])

    def serve(page_file):
        def handler(route):
            route.fulfill(status=200, content_type="text/html", body=(FIXTURES / page_file).read_text())
        return handler
    for host, page_file in PAGES.items():
        ctx.route(f"https://{host}/**", serve(page_file))
    # wait for the extension's live channel (it subscribes as soon as the worker starts)
    deadline = time.time() + 20
    while time.time() < deadline and not ctx.service_workers:
        time.sleep(0.2)
    time.sleep(2)
    yield {"ctx": ctx, "service": service, "control": control, "home": home}
    ctx.close()
    control.stop()
    stop.set()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def card(page, text, timeout=15000):
    """A companion card (open shadow root, so Playwright's CSS engine can see into it)."""
    return page.wait_for_selector(f'walled-companion .card:has-text("{text}")', timeout=timeout)


def click_card_button(page, label, timeout=15000):
    page.wait_for_selector(f'walled-companion button.b:has-text("{label}")', timeout=timeout).click()


def test_telegram_web_assistant_end_to_end(eco):
    ctx, service, control = eco["ctx"], eco["service"], eco["control"]

    def fake_pair(token):                         # instead of calling the real Bot API
        assert token.startswith("123456789:AAH")
        with control.pairing.lock:
            control.pairing.bot, control.pairing.code, control.pairing.raw = "walled_test_bot", "/pair CODE1234567890", "CODE1234567890"
            control.pairing.deadline = time.monotonic() + 120
    control._pair = fake_pair

    with ctx.expect_page(timeout=20000) as opened:
        service.events.publish("assist.telegram_web", "Opening Telegram Web")  # what the app's button does
    page = opened.value
    page.wait_for_load_state()
    card(page, "Log in to Telegram Web")
    page.click("#fake-login")                                                   # user scans the QR
    card(page, "You're logged in")
    click_card_button(page, "Continue")
    assert "tgaddr" in page.evaluate("location.hash") and "BotFather" in page.evaluate("decodeURIComponent(location.hash)")
    click_card_button(page, "Send /newbot")
    page.wait_for_function("window.__sent.includes('/newbot')")
    click_card_button(page, "Send name")
    page.wait_for_function("window.__sent.includes('Walled Guard')")
    click_card_button(page, "Send username")
    page.wait_for_function("window.__sent.some(t => /^walled_[a-z0-9]+_bot$/.test(t))")
    card(page, "Your bot is ready")                                             # token spotted on the page
    click_card_button(page, "Connect to Walled")
    card(page, "Last step: press START")
    hashed = page.evaluate("decodeURIComponent(location.hash)")
    assert "domain=walled_test_bot" in hashed and "start=CODE1234567890" in hashed
    # the user presses START; the service confirms the pairing
    with control.pairing.lock:
        control.pairing.state = "done"
    service.events.publish("telegram.paired", "Telegram paired with @walled_test_bot", tone="ok", bot="walled_test_bot")
    card(page, "Paired! Walled is connected")
    page.close()


def test_companion_shows_live_events_on_the_current_page(eco):
    page = eco["ctx"].new_page()
    page.goto("https://chatgpt.com/")
    card(page, "Walled is watching over ChatGPT")                               # one-time welcome
    click_card_button(page, "Got it")
    page.bring_to_front()
    eco["service"].events.publish("vault.created", "Password vault created", tone="ok")
    card(page, "Password vault created")
    page.close()


def test_secret_copy_guard(eco):
    page = eco["ctx"].new_page()
    page.goto("https://chatgpt.com/")
    page.evaluate("""() => {
        const p = document.createElement('p'); p.id = 'secret'; p.textContent = 'ghp_' + 'a1B2'.repeat(9);
        document.body.append(p);
        const r = document.createRange(); r.selectNodeContents(p);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r);
        document.dispatchEvent(new ClipboardEvent('copy', {bubbles: true}));
    }""")
    card(page, "GitHub token copied")
    page.close()


def test_claude_ai_prompts_are_guarded(eco):
    page = eco["ctx"].new_page()
    page.goto("https://claude.ai/")
    page.wait_for_timeout(1500)
    page.click("#composer")
    page.keyboard.insert_text("Summarise WALLED_SENSITIVE notes")
    page.keyboard.press("Enter")
    log = eco["home"] / "decisions.jsonl"
    deadline = time.time() + 20
    while time.time() < deadline and not (log.exists() and "Claude" in log.read_text()):
        time.sleep(0.2)
    assert '"site": "Claude"' in log.read_text()
    page.wait_for_timeout(300)
    assert page.evaluate("window.__log.sent") == []                             # Cancel → not sent
    page.close()


def test_side_panel_hosts_the_app(eco):
    from walled_guard import config
    cfg = config.load()
    cfg.settings.onboarded = True
    config.save(cfg)
    page = eco["ctx"].new_page()
    page.goto(f"chrome-extension://{EXT_ID}/sidepanel/sidepanel.html")
    frame_el = page.wait_for_selector("iframe#app:not([hidden])", timeout=20000)
    frame = frame_el.content_frame()
    frame.wait_for_selector(".assist .pa", timeout=20000)
    for _ in range(40):                     # Pip greets (typing effect); CSP forbids wait_for_function here
        if len(frame.inner_text(".pip-say")) > 20:
            break
        page.wait_for_timeout(250)
    assert len(frame.inner_text(".pip-say")) > 20
    page.close()
