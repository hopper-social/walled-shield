"""Browser end-to-end for the merged product: rules made in the app reach the extension, and
the password helper suggests, saves and fills through the running Walled service.

Same harness as test_extension.py (real Chrome, real extension, real native host), plus an
in-process Walled service on a private socket. The password helper is normally enabled
from the popup (which needs a permission prompt a test cannot click), so the test loads a
copy of the extension with the helper statically allowed on the two test sites.
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

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FIXTURES = Path(__file__).parent / "fixtures"
EXT_ID = "lbnpgknepeocidimhgmodenkcfngmkdj"
CHROME = os.environ.get("CHROME") or "/opt/pw-browsers/chromium-1243/real/chrome-linux64/chrome"
PAGES = {"chatgpt.com": "chatgpt.html", "login.example.com": "login.html", "signup.example.com": "signup.html"}


@pytest.fixture(scope="module")
def world(tmp_path_factory, pw):
    if not Path(CHROME).exists():
        pytest.skip("no Chrome for Testing binary")
    base = tmp_path_factory.mktemp("platform-e2e")
    walled_home, gate = base / "walled", base / "gate"
    walled_home.mkdir()
    gate.mkdir(mode=0o700)
    sock = gate / "s.sock"
    env = {"WALLED_GUARD_HOME": str(walled_home), "MCP_GATE_STATE_DIR": str(gate), "MCP_GATE_SOCKET": str(sock)}
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    sys.path.insert(0, str(SRC))
    from mcp_approval_gate.approval_daemon import serve as serve_socket
    from walled.service import WalledService
    from walled_guard import config, installer
    from walled_guard.rules import Rule

    cfg = config.load()
    cfg.rules.append(Rule("Client: Acme", "keyword", "Acme Corp", id="acme"))
    config.save(cfg)
    service = WalledService(walled_home / "vault.json")
    service.vault.create("correct horse battery")
    service.vault.save_entry("login.example.com", "vibhav@example.com", "Saved-Pa55word!")
    stop = threading.Event()
    threading.Thread(target=serve_socket, args=(service, sock, stop), daemon=True).start()

    # extension copy with the helper on the test sites
    ext = base / "extension"
    shutil.copytree(ROOT / "src" / "walled" / "extension", ext)
    manifest = json.loads((ext / "manifest.json").read_text())
    manifest["host_permissions"] += ["https://login.example.com/*", "https://signup.example.com/*"]
    manifest["content_scripts"].append({"matches": ["https://login.example.com/*", "https://signup.example.com/*"],
                                        "js": ["src/passwords.js"], "run_at": "document_idle"})
    (ext / "manifest.json").write_text(json.dumps(manifest))

    answer = base / "answer"
    answer.write_text("cancel")
    real = installer.write_launcher(base / "installed")
    launcher = base / "host.sh"
    launcher.write_text("#!/bin/sh\n" + f'export WALLED_GUARD_AUTO_ANSWER="$(cat "{answer}")"\n'
                        + "".join(f'export {k}="{v}"\n' for k, v in env.items()) + f'exec "{real}" "$@"\n')
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    # The service runs in this process: with Telegram off, fills fall back to the desktop dialog.
    old["WALLED_GUARD_AUTO_ANSWER"] = os.environ.get("WALLED_GUARD_AUTO_ANSWER")
    os.environ["WALLED_GUARD_AUTO_ANSWER"] = "continue"
    profile = base / "profile"
    (profile / "NativeMessagingHosts").mkdir(parents=True)
    (profile / "NativeMessagingHosts" / "com.walled.guard.json").write_text(json.dumps({
        "name": "com.walled.guard", "description": "test", "path": str(launcher), "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXT_ID}/"]}))
    if True:
        ctx = pw.chromium.launch_persistent_context(str(profile), executable_path=CHROME, headless=False,
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--no-first-run"])
        def serve(page_file):
            def handler(route):
                route.fulfill(status=200, content_type="text/html", body=(FIXTURES / page_file).read_text())
            return handler
        for host, page_file in PAGES.items():
            ctx.route(f"https://{host}/**", serve(page_file))
        yield {"ctx": ctx, "service": service, "home": walled_home, "answer": answer}
        ctx.close()
    stop.set()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def wait_until(page, predicate, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        page.wait_for_timeout(100)
    return False


def decisions(world):
    p = world["home"] / "decisions.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


def click_chip(page, nth=0):
    """The chip is in a closed shadow root: click its n-th button by position, like a user."""
    box = page.locator("walled-password-chip").bounding_box()
    page.mouse.click(box["x"] + 40, box["y"] + 40 + nth * 44)


def test_custom_rule_from_the_app_reaches_the_extension(world):
    page = world["ctx"].new_page()
    page.goto("https://chatgpt.com/")
    page.wait_for_timeout(1500)                                      # first rule sync
    page.click("#prompt-textarea")
    page.keyboard.insert_text("Draft the renewal for Acme Corp")
    page.keyboard.press("Enter")
    assert wait_until(page, lambda: decisions(world) and decisions(world)[-1].get("rules") == ["Client: Acme"])
    page.wait_for_timeout(300)
    assert page.evaluate("window.__log.sent") == []                    # Cancel: not sent, text kept
    assert "Acme Corp" in page.inner_text("#prompt-textarea")
    page.close()


def test_built_in_detector_in_the_browser(world):
    page = world["ctx"].new_page()
    page.goto("https://chatgpt.com/")
    page.wait_for_timeout(1500)
    page.click("#prompt-textarea")
    page.keyboard.insert_text("my card is 4111 1111 1111 1111")
    page.keyboard.press("Enter")
    assert wait_until(page, lambda: decisions(world)[-1].get("rules") == ["Payment card number"])
    assert page.evaluate("window.__log.sent") == []
    page.close()


def test_password_suggestion_and_save(world):
    page = world["ctx"].new_page()
    page.goto("https://signup.example.com/")
    page.fill("#email", "new.user@example.com")
    page.click("#pw1")
    page.wait_for_selector("walled-password-chip", state="visible")
    page.wait_for_timeout(300)
    click_chip(page)                                                  # "Use a strong password"
    pw1, pw2 = page.input_value("#pw1"), page.input_value("#pw2")
    assert pw1 == pw2 and len(pw1) == 20
    click_chip(page)                                                  # "Save to Walled vault"
    vault = world["service"].vault
    assert wait_until(page, lambda: any(e["origin"] == "signup.example.com" for e in vault.entries()))
    saved = next(e for e in vault.entries() if e["origin"] == "signup.example.com")
    assert saved["username"] == "new.user@example.com" and vault.get(saved["id"]).password == pw1
    page.close()


def test_login_fill_from_vault(world):
    page = world["ctx"].new_page()
    page.goto("https://login.example.com/")
    page.click("#pw")
    page.wait_for_selector("walled-password-chip", state="visible")
    page.wait_for_timeout(800)                                        # vault lookup
    click_chip(page)                                                  # "Fill vibhav@example.com"
    assert wait_until(page, lambda: page.input_value("#pw") == "Saved-Pa55word!", 10)
    assert page.input_value("#email") == "vibhav@example.com"
    page.close()


def test_locked_vault_fills_nothing(world):
    world["service"].vault.lock()
    page = world["ctx"].new_page()
    page.goto("https://login.example.com/")
    page.click("#pw")
    page.wait_for_selector("walled-password-chip", state="visible")
    page.wait_for_timeout(800)
    click_chip(page)
    page.wait_for_timeout(500)
    assert page.input_value("#pw") == ""
    world["service"].vault.unlock("correct horse battery")
    page.close()


def test_page_cannot_press_the_chip(world):
    page = world["ctx"].new_page()
    page.goto("https://login.example.com/")
    page.click("#pw")
    page.wait_for_selector("walled-password-chip", state="visible")
    page.wait_for_timeout(800)
    box = page.locator("walled-password-chip").bounding_box()
    # A script on the page synthesises a click where the Fill button is: it must be ignored.
    page.evaluate("""([x, y]) => {
        const el = document.elementFromPoint(x, y);
        el.dispatchEvent(new MouseEvent('click', {bubbles: true, composed: true, clientX: x, clientY: y}));
    }""", [box["x"] + 40, box["y"] + 40])
    page.wait_for_timeout(600)
    assert page.input_value("#pw") == ""
    page.close()
