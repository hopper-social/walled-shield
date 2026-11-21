"""Build site/index.html: the one-page install guide (all commands, copy buttons, the agent prompt).

    python scripts/build_site.py

Self-contained: CSS, the Pip character (pip-art.js) and the agent prompt are inlined, so the page
can be hosted anywhere (GitHub Pages, Netlify) or opened from disk.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/walled/__init__.py").read_text()).group(1)
REPO = "https://github.com/adarshagnihotri/walled-shield"    # ← change to your repository


def cmd(text: str, note: str = "") -> str:
    """A command block with a copy button; trailing # comments are dimmed (and not copied)."""
    lines = []
    for line in text.strip("\n").split("\n"):
        code, sep, comment = line.partition("  # ")
        lines.append(html.escape(code) + (f'<span class="c">  # {html.escape(comment)}</span>' if sep else ""))
    tip = f'<p class="note">{note}</p>' if note else ""
    return f'<div class="cmd"><button class="copy" type="button">Copy</button><pre><code>{chr(10).join(lines)}</code></pre></div>{tip}'


def main() -> int:
    import base64
    art = (ROOT / "src/walled/extension/src/pip-art.js").read_text()
    buddy = (ROOT / "src/walled/extension/src/buddy.js").read_text()
    prompt = (ROOT / "docs/AGENT_SETUP.md").read_text().split("---", 1)[1].strip()
    favicon = base64.b64encode((ROOT / "src/walled/web/favicon.png").read_bytes()).decode()
    v = VERSION
    commands = [
        ("walled app", "Open Walled (also in Launchpad / your app menu)"),
        ("walled pet", "Put Pip back in the corner of your screen"),
        ("walled extension", "Open chrome://extensions with the path copied"),
        ("walled status", "What's running and protected"),
        ("walled doctor", "Check every part and say what to fix"),
        ("walled guard dialog-test", "Show the native dialog once"),
        ("walled guard scan FILE", "Check files the way an upload is checked"),
        ("walled guard log", "Recent decisions"),
        ("walled mcp wrap-desktop", "Guard Claude Desktop tools"),
        ("walled mcp renew", "Turn Telegram approvals on for 24 h"),
        ("walled install", "Re-register browsers, login item, app icon"),
        ("walled uninstall --purge --yes", "Remove Walled and delete its data"),
    ]
    rows = "\n".join(f'<div class="cmd-row reveal"><div><code>{html.escape(c)}</code><small>{html.escape(d)}</small></div>'
                     f'<button class="copy" type="button" data-copy="{html.escape(c.replace(" FILE", " "))}">Copy</button></div>'
                     for c, d in commands)
    page = (ROOT / "site/src/index.template.html").read_text()
    script_safe = lambda js: js.replace("</script>", "<\\/script>")
    for key, value in {
        "{{V}}": v, "{{REPO}}": REPO, "{{FAVICON}}": favicon, "{{ART}}": script_safe(art), "{{BUDDY}}": script_safe(buddy),
        "{{ROWS}}": rows, "{{PROMPT}}": html.escape(prompt),
        "{{CMD_MAC}}": cmd(f"cd ~/Downloads && unzip -o walled-shield-{v}-installer.zip\ncd walled-shield-{v} && bash install.sh"),
        "{{CMD_LINUX_PRE}}": cmd("sudo apt install python3 python3-venv zenity tesseract-ocr  # once: Python, native dialog, OCR"),
        "{{CMD_LINUX}}": cmd(f"cd ~/Downloads && unzip -o walled-shield-{v}-installer.zip\ncd walled-shield-{v} && bash install.sh"),
        "{{CMD_SOURCE}}": cmd(f"git clone {REPO}.git\ncd walled-shield\nbash install.sh"),
        "{{CMD_PATH}}": cmd('echo \'export PATH="$HOME/.walled/venv/bin:$PATH"\' >> ~/.zshrc  # bash: ~/.bashrc\nsource ~/.zshrc && walled --version'),
        "{{CMD_CLAUDE}}": cmd("walled mcp wrap-desktop"),
        "{{CMD_DEV}}": cmd(f"git clone {REPO}.git && cd walled-shield\npython3 -m venv .venv && source .venv/bin/activate\npip install -U pip && pip install -e \".[dev,ocr,window]\"\npython -m playwright install chromium\npython scripts/make_samples.py  # sample files for upload tests"),
        "{{CMD_TEST}}": cmd("pytest -q tests/mcp tests/guard tests/platform  # unit + integration\nmypy src  # strict type check"),
        "{{CMD_E2E}}": cmd("export CHROME=\"$(python -c 'from playwright.sync_api import sync_playwright as s; p=s().start(); print(p.chromium.executable_path); p.stop()')\"\npytest -q tests/e2e  # Linux without a display: xvfb-run -a pytest -q tests/e2e",
                           "Loads the real extension into Chromium and drives ChatGPT, Perplexity and Claude stand-ins."),
        "{{CMD_BUILD}}": cmd("python scripts/build_release.py  # dist/: wheel, extension zip, installer, SHA256SUMS\npython scripts/build_site.py  # site/index.html (this page)"),
        "{{CMD_DOCTOR}}": cmd("walled doctor\nwalled guard doctor\ntail -n 50 ~/.walled/install.log"),
        "{{CMD_UNINSTALL}}": cmd("walled uninstall  # keeps your rules and vault\nwalled uninstall --purge --yes  # deletes them too\nbash install.sh --uninstall  # also removes ~/.walled/venv"),
    }.items():
        page = page.replace(key, value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", page)
    if leftover:
        raise SystemExit(f"unfilled placeholders: {leftover}")
    out = ROOT / "site" / "index.html"
    out.write_text(page, encoding="utf-8")
    print(f"wrote {out} ({len(page) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
