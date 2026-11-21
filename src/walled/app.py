"""Open the Control Center in its own window.

Order of preference: a native window (pywebview, if installed) → a chromeless Chrome/Edge/
Brave "app" window → the default browser. The Walled service is started first if needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from walled_guard import audit

from .control import control_file


def _probe(info: dict[str, Any]) -> bool:
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/state",
                                 headers={"X-Walled-Token": info["token"]})
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:   # noqa: S310 - loopback only
            return bool(resp.status == 200)
    except OSError:
        return False


def running() -> dict[str, Any] | None:
    try:
        info = json.loads(control_file().read_text())
    except (OSError, ValueError):
        return None
    return info if _probe(info) else None


def ensure_service(timeout: float = 20) -> dict[str, Any]:
    info = running()
    if info:
        return info
    log = audit.state_dir() / "service.log"
    audit.private_dir()
    with open(log, "ab") as out:
        subprocess.Popen([sys.executable, "-m", "walled", "service"], stdout=out, stderr=out,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = running()
        if info:
            return info
        time.sleep(0.3)
    raise RuntimeError(f"The Walled service did not start. See {log}")


def browser_for_app_window() -> str | None:
    candidates = {
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                   "/Applications/Chromium.app/Contents/MacOS/Chromium"],
        "win32": [os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                  os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe")],
    }.get(sys.platform, [])
    for path in candidates:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def quiet_profile(profile: Path) -> None:
    """The app window has its own browser profile: switch off password saving and autofill there,
    so a bot token or vault passphrase typed into Walled is never offered to a password manager."""
    prefs_path = profile / "Default" / "Preferences"
    try:
        prefs = json.loads(prefs_path.read_text()) if prefs_path.exists() else {}
    except (OSError, ValueError):
        prefs = {}
    prefs["credentials_enable_service"] = False
    prefs["credentials_enable_autosignin"] = False
    prefs.setdefault("profile", {})["password_manager_enabled"] = False
    autofill = prefs.setdefault("autofill", {})
    autofill["profile_enabled"] = False
    autofill["credit_card_enabled"] = False
    prefs.setdefault("translate", {})["enabled"] = False
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    prefs_path.write_text(json.dumps(prefs))


def open_window(url: str) -> str:
    try:
        import webview
        webview.create_window("Walled", url, width=1200, height=800, min_size=(900, 620))
        webview.start()
        return "native window"
    except ImportError:
        pass
    browser = browser_for_app_window()
    if browser:
        profile = audit.state_dir() / "app-window"
        quiet_profile(profile)
        subprocess.Popen([browser, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1240,860",
                          "--no-first-run", "--no-default-browser-check", "--disable-features=Translate,AutofillServerCommunication"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "app window"
    webbrowser.open(url)
    return "browser tab"


def spawn_app(page: str = "") -> None:
    """Open the main window from another process (Pip, the service) without blocking it."""
    subprocess.Popen([sys.executable, "-m", "walled", "app", *([page] if page else [])], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)


def ticket(info: dict[str, Any]) -> str:
    """A one-time, 60-second launch ticket: safe to put on a command line, unlike the token."""
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api/ticket", data=b"{}", method="POST",
                                 headers={"X-Walled-Token": info["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:   # noqa: S310 - loopback only
        return str(json.loads(resp.read())["ticket"])


def main(page: str = "") -> int:
    try:
        info = ensure_service()
        key = ticket(info)
    except (RuntimeError, OSError, KeyError, ValueError) as exc:
        print(exc)
        return 1
    how = open_window(f"http://127.0.0.1:{info['port']}/#k={key}" + (f"&p={page}" if page else ""))
    print(f"Walled Control Center opened ({how}).")
    return 0
