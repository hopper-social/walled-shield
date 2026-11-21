"""Install, uninstall and status for the whole product."""

from __future__ import annotations

import plistlib
import shutil
import sys
from pathlib import Path

from walled_guard import installer as browser
from walled_guard import ocr

from . import __version__

APP_ID = "com.walled.shield.app"


def mac_app_path() -> Path:
    return Path.home() / "Applications" / "Walled.app"


def build_mac_app(python: str = sys.executable, target: Path | None = None) -> Path:
    """A small, local app bundle so Walled lives in Launchpad, Spotlight and the Dock.

    It is created on this Mac (not downloaded), so it carries no quarantine flag and opens
    without Gatekeeper prompts. It only runs ``python -m walled app``.
    """
    app = target or mac_app_path()
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    launcher = macos / "Walled"
    launcher.write_text(f'#!/bin/sh\nexec "{python}" -m walled app\n', encoding="utf-8")
    launcher.chmod(0o755)
    info = {"CFBundleName": "Walled", "CFBundleDisplayName": "Walled", "CFBundleIdentifier": APP_ID,
            "CFBundleExecutable": "Walled", "CFBundleIconFile": "Walled", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True}
    with open(app / "Contents" / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)
    icon = Path(__file__).with_name("web") / "icon.png"
    if icon.exists():
        try:
            from PIL import Image
            Image.open(icon).save(resources / "Walled.icns")
        except Exception:
            pass
    return app


def install(args: list[str]) -> int:
    print(f"Walled Shield {__version__}\n")
    done = browser.install(browsers=[a for a in args if not a.startswith("-")] or None)
    print("Browser guard registered with:" if done else "No Chromium browser profile found (use: walled install chrome).")
    for line in done:
        print(f"  ✓ {line}")
    if sys.platform in ("darwin", "linux") and "--no-service" not in args:
        try:
            from mcp_approval_gate import service
            print("  ✓ background service: " + service.install(sys.executable).splitlines()[0])
        except Exception as exc:              # systemd missing in containers etc.
            print(f"  ! background service not installed ({exc}); 'walled app' starts it on demand")
    if sys.platform == "darwin":
        print(f"  ✓ app: {build_mac_app()}  (Launchpad / Spotlight: “Walled”)")
    if sys.platform.startswith("linux"):
        print(f"  ✓ app menu entry: {linux_desktop_entry()}")
    from .paths import install_extension
    ext = install_extension()
    print(f"  ✓ browser extension folder: {ext}")
    if not ocr.available():
        print("\n  · Optional: install Tesseract so Walled can read text inside screenshots (macOS: brew install tesseract).")
    if "--no-open" not in args:
        print()
        print(extension_hint())
    return 0


def extension_hint() -> str:
    """Open chrome://extensions with the folder path on the clipboard and say what's left (3 clicks)."""
    from .paths import open_extensions_page
    r = open_extensions_page()
    paste = "⌘⇧G, ⌘V, Enter" if sys.platform == "darwin" else "paste the path (Ctrl+L, Ctrl+V), Enter"
    where = f"{r['browser']} is open on the extensions page" if r["browser"] else "Open chrome://extensions in Chrome"
    clip = "the folder path is already copied" if r["copied"] else f"the folder is {r['path']}"
    return (f"Last step — add the extension (about 20 seconds). {where}; {clip}.\n"
            f"  1. Switch on “Developer mode” (top right)\n"
            f"  2. Click “Load unpacked”, then {paste}\n"
            f"  3. Click Select. Pip says hi on chatgpt.com when it's working.")


def linux_desktop_entry(python: str = sys.executable) -> Path:
    apps = Path.home() / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    icon = Path(__file__).with_name("web") / "icon.png"
    entry = apps / "walled.desktop"
    entry.write_text("[Desktop Entry]\nType=Application\nName=Walled\nComment=Guard what you share with AI\n"
                     f"Exec=\"{python}\" -m walled app\nIcon={icon}\nTerminal=false\nCategories=Utility;Security;\n",
                     encoding="utf-8")
    return entry


def uninstall(args: list[str]) -> int:
    for line in browser.uninstall():
        print(f"  removed {line}")
    try:
        from mcp_approval_gate import service
        print("  " + service.uninstall())
    except Exception:
        pass
    from .paths import extension_home
    for path in (mac_app_path(), extension_home(), Path.home() / ".local/share/applications/walled.desktop"):
        if path.is_dir():
            shutil.rmtree(path)
            print(f"  removed {path}")
        elif path.exists():
            path.unlink()
            print(f"  removed {path}")
    if "--purge" in args:
        from mcp_approval_gate.paths import state_dir as gate_state
        from walled_guard.audit import state_dir as guard_state
        targets = [guard_state(), gate_state()]
        answer = "yes" if "--yes" in args else input(
            "Delete your rules, decision log, password vault and Telegram pairing? This cannot be undone. Type 'yes': ")
        if answer.strip().lower() == "yes":
            for path in targets:
                if path.exists():
                    shutil.rmtree(path)
                    print(f"  deleted {path}")
        else:
            print("  kept your data")
    else:
        print("Your rules, vault and Telegram pairing are kept (walled uninstall --purge removes them).")
    print("Remove the extension from chrome://extensions too.")
    return 0


def doctor(args: list[str]) -> int:
    """Check every moving part and say exactly what to fix."""
    from walled_guard import client, dialogs
    from walled_guard import doctor as guard_doctor

    from .app import running
    from .paths import extension_home
    problems = 0

    def line(ok: bool, text: str, fix: str = "") -> None:
        nonlocal problems
        problems += not ok
        print(f"  {'✓' if ok else '✗'} {text}" + (f"\n      → {fix}" if fix and not ok else ""))

    print(f"Walled Shield {__version__} — doctor\n")
    svc = client.call({"op": "status"}, timeout=3)
    line(svc is not None, "background service running", "walled app   (or: walled install to start it at login)")
    line(running() is not None, "Control Center API reachable", "walled app")
    if svc:
        line(bool(svc.get("telegram")), "Telegram bot connected", "open the app → Telegram → pair your bot")
        if svc.get("telegram"):
            line(bool(svc.get("session_active")), "Telegram approvals on", "app → Telegram → Renew 24 h")
    line((extension_home() / "manifest.json").exists(), f"extension folder at {extension_home()}", "walled install")
    line(not dialogs.backend_name().startswith("NONE"), f"desktop dialog: {dialogs.backend_name()}", "sudo apt install zenity")
    line(ocr.available(), "OCR (Tesseract) for images", "brew install tesseract / sudo apt install tesseract-ocr")
    print("\nBrowser agent (started exactly the way Chrome starts it):")
    problems += guard_doctor.run(summary=False) != 0
    print("\nAll good." if not problems else f"\n{problems} thing(s) to fix above.")
    return 0 if not problems else 1


def status(args: list[str]) -> int:
    from walled_guard import client

    from .app import running
    svc = client.call({"op": "status"}, timeout=3)
    print(f"Walled Shield {__version__}")
    print(f"  Service          : {'running' if svc else 'not running — run: walled app'}")
    if svc:
        print(f"  Telegram         : {'on' if svc.get('telegram') else 'not set up'}"
              + (f", approvals {'ON' if svc.get('session_active') else 'OFF (renew in the app)'}" if svc.get("telegram") else ""))
        vault = svc.get("vault") or {}
        print(f"  Vault            : {'none' if not vault.get('exists') else 'unlocked' if vault.get('unlocked') else 'locked'}")
    ui = running()
    print(f"  Control Center   : {'open at http://127.0.0.1:%s' % ui['port'] if ui else 'closed'}")
    print(f"  Browser guard    : {', '.join(browser.installed()) or 'not registered — run: walled install'}")
    print(f"  OCR              : {'yes' if ocr.available() else 'no'}")
    try:
        from mcp_approval_gate import desktop_config
        servers = desktop_config.status(desktop_config.default_config_path())
        guarded = sum(v == "guarded" for v in servers.values())
        print(f"  Claude MCP       : {guarded}/{len(servers)} servers guarded")
    except Exception:
        print("  Claude MCP       : Claude Desktop config not found")
    return 0
