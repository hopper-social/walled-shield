"""End-to-end: the real extension in a real Chrome, talking to the real agent.

The two sites are replaced by local test doubles (same URLs, same selectors), and the
native dialog is answered automatically through WALLED_GUARD_AUTO_ANSWER, read from a
file before every request so each test can choose Continue or Cancel. Everything else —
interception, native messaging, inspection, replay — is the production code path.

Run (Linux needs a display):  xvfb-run -a pytest tests/e2e -v
Needs: pip install playwright pytest, and a Chrome for Testing / Chromium binary
(CHROME=/path/to/chrome); branded Google Chrome no longer loads unpacked extensions
from the command line.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
EXTENSION = ROOT / "src" / "walled" / "extension"
AGENT = ROOT / "src"
FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = ROOT / "samples"
EXT_ID = "lbnpgknepeocidimhgmodenkcfngmkdj"
CHROME = os.environ.get("CHROME") or shutil.which("chrome-for-testing") or "/opt/pw-browsers/chromium-1243/real/chrome-linux64/chrome"
ORANGE = "rgb(217, 72, 15)"


@pytest.fixture(scope="session")
def env(tmp_path_factory, pw):
    if not Path(CHROME).exists():
        pytest.skip("no Chrome for Testing binary (set CHROME=...)")
    if not SAMPLES.exists():
        pytest.skip("run scripts/make_samples.py first")
    base = tmp_path_factory.mktemp("e2e")
    profile, home = base / "profile", base / "agent-home"
    home.mkdir()
    answer = base / "answer"
    answer.write_text("continue")
    # The production launcher written by `walled-guard install`, wrapped only to pick the test answer.
    sys.path.insert(0, str(AGENT))
    from walled_guard import installer
    real = installer.write_launcher(base / "installed")
    launcher = base / "host.sh"
    launcher.write_text(
        "#!/bin/sh\n"
        f'ANSWER=$(cat "{answer}")\n'
        '[ "$ANSWER" = "down" ] && exit 1\n'
        f'export WALLED_GUARD_AUTO_ANSWER="$ANSWER" WALLED_GUARD_HOME="{home}"\n'
        f'exec "{real}" "$@"\n'                     # Chrome's origin argument is passed through
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    hosts = profile / "NativeMessagingHosts"
    hosts.mkdir(parents=True)
    (hosts / "com.walled.guard.json").write_text(json.dumps({
        "name": "com.walled.guard", "description": "test", "path": str(launcher),
        "type": "stdio", "allowed_origins": [f"chrome-extension://{EXT_ID}/"],
    }))
    if True:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), executable_path=CHROME, headless=False,
            args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", "--no-first-run"],
        )

        def serve(route):
            host = route.request.url.split("/")[2]
            page = "perplexity.html" if "perplexity" in host else "chatgpt.html"
            route.fulfill(status=200, content_type="text/html", body=(FIXTURES / page).read_text())

        ctx.route("https://chatgpt.com/**", serve)
        ctx.route("https://www.perplexity.ai/**", serve)
        yield {"ctx": ctx, "answer": answer, "log": home / "decisions.jsonl"}
        ctx.close()


class Site:
    def __init__(self, env, url):
        self.env = env
        self.page = env["ctx"].new_page()
        self.page.goto(url)
        self.page.wait_for_function("document.documentElement.classList.contains('walled-recolor')")

    def answer(self, value: str) -> None:
        self.env["answer"].write_text(value)

    def decisions(self) -> list[dict]:
        p = self.env["log"]
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def new_decision(self, before: int, timeout=40.0) -> dict:
        assert self.wait_until(lambda: len(self.decisions()) > before, timeout), "no dialog was shown"
        return self.decisions()[-1]

    def log(self) -> dict:
        return self.page.evaluate("window.__log")

    def wait_until(self, predicate, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            self.page.wait_for_timeout(100)
        return False

    def settle(self, ms=1500):
        self.page.wait_for_timeout(ms)


@pytest.fixture
def chatgpt(env):
    s = Site(env, "https://chatgpt.com/")
    yield s
    s.page.close()


@pytest.fixture
def perplexity(env):
    s = Site(env, "https://www.perplexity.ai/")
    yield s
    s.page.close()


def type_prompt(site: Site, text: str, selector: str) -> None:
    site.page.click(selector)
    site.page.keyboard.insert_text(text)


def drop(site: Site, names: list[str], selector="#drop") -> None:
    files = [{"name": n, "b64": base64.b64encode((SAMPLES / n).read_bytes()).decode()} for n in names]
    site.page.evaluate("""([sel, files]) => {
        const dt = new DataTransfer();
        for (const f of files) {
            const bytes = Uint8Array.from(atob(f.b64), c => c.charCodeAt(0));
            dt.items.add(new File([bytes], f.name));
        }
        document.querySelector(sel).dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt}));
    }""", [selector, files])


# ------------------------------------------------------------------------- prompts
def test_clean_prompt_is_sent_without_any_dialog(chatgpt):
    before = len(chatgpt.decisions())
    type_prompt(chatgpt, "Summarise this paragraph please", "#prompt-textarea")
    chatgpt.page.keyboard.press("Enter")
    assert chatgpt.wait_until(lambda: chatgpt.log()["sent"] == ["Summarise this paragraph please"], 3)
    assert len(chatgpt.decisions()) == before


def test_marked_prompt_cancel_keeps_text_for_editing(chatgpt):
    chatgpt.answer("cancel")
    before = len(chatgpt.decisions())
    type_prompt(chatgpt, "My API key is WALLED_SENSITIVE 1234", "#prompt-textarea")
    chatgpt.page.keyboard.press("Enter")
    assert chatgpt.new_decision(before)["decision"] == "cancel"
    chatgpt.settle(500)
    assert chatgpt.log()["sent"] == []
    assert "WALLED_SENSITIVE" in chatgpt.page.inner_text("#prompt-textarea")
    # edit and retry: the clean version goes straight through
    chatgpt.page.evaluate("document.querySelector('#prompt-textarea').innerText = 'My API key is redacted'")
    chatgpt.page.click("#prompt-textarea")
    chatgpt.page.keyboard.press("End")
    chatgpt.page.keyboard.press("Enter")
    assert chatgpt.wait_until(lambda: chatgpt.log()["sent"] == ["My API key is redacted"], 3)


def test_marked_prompt_continue_sends_unchanged(chatgpt):
    chatgpt.answer("continue")
    text = "Please review WALLED_SENSITIVE contract terms"
    type_prompt(chatgpt, text, "#prompt-textarea")
    chatgpt.page.keyboard.press("Enter")
    assert chatgpt.wait_until(lambda: chatgpt.log()["sent"] == [text])
    assert chatgpt.decisions()[-1] | {"time": None} == {"time": None, "site": "ChatGPT", "kind": "prompt", "decision": "continue", "count": 1,
                                                           "rules": ["WALLED_SENSITIVE marker"], "via": "desktop"}


def test_send_button_is_guarded_too(chatgpt):
    chatgpt.answer("cancel")
    before = len(chatgpt.decisions())
    type_prompt(chatgpt, "WALLED_SENSITIVE via button", "#prompt-textarea")
    chatgpt.page.click("#send")
    assert chatgpt.new_decision(before)["decision"] == "cancel"
    chatgpt.settle(500)
    assert chatgpt.log()["sent"] == []
    chatgpt.answer("continue")
    chatgpt.page.click("#send")
    assert chatgpt.wait_until(lambda: chatgpt.log()["sent"] == ["WALLED_SENSITIVE via button"])


def test_agent_down_blocks_marked_prompt_but_not_clean_ones(chatgpt):
    chatgpt.answer("down")
    type_prompt(chatgpt, "WALLED_SENSITIVE while agent is down", "#prompt-textarea")
    chatgpt.page.keyboard.press("Enter")
    chatgpt.page.wait_for_selector("walled-guard-toasts", state="attached", timeout=10000)
    chatgpt.settle(800)
    assert chatgpt.log()["sent"] == []
    chatgpt.page.evaluate("document.querySelector('#prompt-textarea').innerText = 'hello'")
    chatgpt.page.click("#prompt-textarea")
    chatgpt.page.keyboard.press("Enter")
    assert chatgpt.wait_until(lambda: chatgpt.log()["sent"] == ["hello"], 3)
    chatgpt.answer("continue")


# ------------------------------------------------------------------------- files
def test_clean_upload_goes_through_without_dialog(chatgpt):
    before = len(chatgpt.decisions())
    chatgpt.page.set_input_files("#upload", [str(SAMPLES / "clean_report.pdf"), str(SAMPLES / "clean_notes.txt")])
    assert chatgpt.wait_until(lambda: chatgpt.log()["uploaded"] == ["clean_report.pdf", "clean_notes.txt"])
    assert len(chatgpt.decisions()) == before


@pytest.mark.parametrize("name", ["secret_report.pdf", "secret_notes.txt", "secret_screenshot.png", "secret_scanned.pdf"])
def test_marked_upload_cancel_blocks(chatgpt, name):
    chatgpt.answer("cancel")
    before = len(chatgpt.decisions())
    chatgpt.page.set_input_files("#upload", str(SAMPLES / name))
    assert chatgpt.new_decision(before)["kind"] == "file"
    chatgpt.settle(500)
    assert chatgpt.log()["uploaded"] == []
    assert chatgpt.page.evaluate("document.querySelector('#upload').files.length") == 0


def test_marked_upload_continue_uploads_unchanged(chatgpt):
    chatgpt.answer("continue")
    chatgpt.page.set_input_files("#upload", str(SAMPLES / "secret_photo.jpg"))
    assert chatgpt.wait_until(lambda: chatgpt.log()["uploaded"] == ["secret_photo.jpg"], 40)
    size = chatgpt.page.evaluate("document.querySelector('#upload').files[0].size")
    assert size == (SAMPLES / "secret_photo.jpg").stat().st_size


def test_mixed_upload_cancel_keeps_only_clean_files(chatgpt):
    chatgpt.answer("cancel")
    chatgpt.page.set_input_files("#upload", [str(SAMPLES / "clean_notes.txt"), str(SAMPLES / "secret_report.pdf")])
    assert chatgpt.wait_until(lambda: chatgpt.log()["uploaded"] == ["clean_notes.txt"], 30)


def test_drag_and_drop(chatgpt):
    chatgpt.answer("cancel")
    before = len(chatgpt.decisions())
    drop(chatgpt, ["secret_notes.txt"])
    assert chatgpt.new_decision(before)["decision"] == "cancel"
    chatgpt.settle(500)
    assert chatgpt.log()["uploaded"] == []
    chatgpt.answer("continue")
    drop(chatgpt, ["secret_notes.txt", "clean_screenshot.png"])
    assert chatgpt.wait_until(lambda: chatgpt.log()["uploaded"] == ["drop:secret_notes.txt", "drop:clean_screenshot.png"], 30)


def test_pasted_screenshot(chatgpt):
    chatgpt.answer("cancel")
    data = base64.b64encode((SAMPLES / "secret_dark_mode.png").read_bytes()).decode()
    script = """(b64) => {
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        const dt = new DataTransfer(); dt.items.add(new File([bytes], 'image.png', {type: 'image/png'}));
        document.querySelector('#prompt-textarea').dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
    }"""
    before = len(chatgpt.decisions())
    chatgpt.page.evaluate(script, data)
    assert chatgpt.new_decision(before)["decision"] == "cancel"
    chatgpt.settle(500)
    assert chatgpt.log()["uploaded"] == []
    chatgpt.answer("continue")
    chatgpt.page.evaluate(script, data)
    assert chatgpt.wait_until(lambda: chatgpt.log()["uploaded"] == ["paste:image.png"], 40)


# ------------------------------------------------------------------------- Perplexity
def test_perplexity_prompt(perplexity):
    perplexity.answer("cancel")
    before = len(perplexity.decisions())
    type_prompt(perplexity, "WALLED_SENSITIVE question", "#ask-input")
    perplexity.page.keyboard.press("Enter")
    assert perplexity.new_decision(before) | {"time": None} == {"time": None, "site": "Perplexity", "kind": "prompt", "decision": "cancel", "count": 1,
                                                                           "rules": ["WALLED_SENSITIVE marker"], "via": "desktop"}
    perplexity.settle(500)
    assert perplexity.log()["sent"] == []
    assert perplexity.page.input_value("#ask-input") == "WALLED_SENSITIVE question"
    perplexity.answer("continue")
    perplexity.page.click("#send")
    assert perplexity.wait_until(lambda: perplexity.log()["sent"] == ["WALLED_SENSITIVE question"])


def test_perplexity_detached_file_input(perplexity):
    perplexity.answer("cancel")
    before = len(perplexity.decisions())
    with perplexity.page.expect_file_chooser() as chooser:
        perplexity.page.click("#attach")
    chooser.value.set_files(str(SAMPLES / "secret_report_split.pdf"))
    d = perplexity.new_decision(before)
    assert (d["site"], d["kind"], d["decision"]) == ("Perplexity", "file", "cancel")
    perplexity.settle(500)
    assert perplexity.log()["uploaded"] == []
    perplexity.answer("continue")
    with perplexity.page.expect_file_chooser() as chooser:
        perplexity.page.click("#attach")
    chooser.value.set_files(str(SAMPLES / "clean_report.pdf"))
    assert perplexity.wait_until(lambda: perplexity.log()["uploaded"] == ["clean_report.pdf"], 30)


# ------------------------------------------------------------------------- response colour
def colour(site: Site, selector: str) -> str:
    return site.page.eval_on_selector(selector, "el => getComputedStyle(el).color")


def test_responses_are_recoloured_on_both_sites(chatgpt, perplexity):
    assert colour(chatgpt, "#answer") == ORANGE
    assert colour(chatgpt, "#answer strong") == ORANGE
    assert colour(chatgpt, "[data-message-author-role='user'] div") != ORANGE
    assert colour(perplexity, "#answer em") == ORANGE
    assert colour(perplexity, "h1.query") != ORANGE


def test_popup_colour_change_applies_live(env, chatgpt):
    popup = env["ctx"].new_page()
    popup.goto(f"chrome-extension://{EXT_ID}/popup/popup.html")
    popup.wait_for_selector("#dot.ok, #dot.warn", timeout=15000)      # agent reachable
    popup.click("#swatches button[title='#2563eb']")
    assert chatgpt.wait_until(lambda: colour(chatgpt, "#answer") == "rgb(37, 99, 235)", 5)
    popup.click("#swatches button[title='#d9480f']")
    assert chatgpt.wait_until(lambda: colour(chatgpt, "#answer") == ORANGE, 5)
    popup.close()


def test_pip_lives_in_the_page_corner_and_can_be_petted(chatgpt):
    """No desktop Pip is running in this test, so he shows up in the corner of the chat."""
    page = chatgpt.page
    page.wait_for_selector("#walled-buddy", state="attached", timeout=10000)
    pip = page.locator("#walled-buddy .wb-pip")
    pip.wait_for(timeout=10000)
    page.wait_for_timeout(1000)
    box = pip.bounding_box()
    vw = page.viewport_size or {"width": 1280, "height": 720}
    assert box["x"] > vw["width"] - 380 and box["y"] > vw["height"] - 200        # small, in the corner
    assert box["width"] < 70
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx - 18, cy)
    for k in range(12):
        page.mouse.move(cx + (18 if k % 2 == 0 else -18), cy, steps=4)
    assert "petting" in (pip.get_attribute("class") or "")
    page.mouse.move(10, 10)
    # first-run hello, then one click → his panel, which can hide him here for an hour
    hello = page.locator("#walled-buddy button:text-is('Nice to meet you')")
    if hello.count():
        hello.click()
        page.wait_for_timeout(400)
    pip.click()
    page.locator("#walled-buddy .wb-panel button:text-is('Hide here for an hour')").click()
    page.wait_for_selector("#walled-buddy", state="detached", timeout=5000)
