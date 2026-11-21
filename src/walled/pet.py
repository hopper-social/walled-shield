"""Pip on the desktop: a tiny, transparent, always-on-top window in a corner of the screen.

It runs as its own process (``walled pet``) so the main app window and the service never wait
on it. The page is ``buddy.html`` from the Control Center, opened with a one-time ticket like the
app. Without pywebview (or without a display) Pip lives in the browser corner instead: the
extension sees that no desktop Pip is running and shows him there.
"""

from __future__ import annotations

import atexit
import importlib.util
import inspect
import os
import signal
import subprocess
import sys
from typing import Any

from walled_guard import audit, config

IDLE = (230, 124)          # just enough room to pace about
OPEN = (360, 450)          # while a note or the menu is showing
MARGIN_X, MARGIN_Y = 14, 86 if sys.platform == "darwin" else 52     # clear of the Dock / taskbar


def available() -> bool:
    if importlib.util.find_spec("webview") is None:
        return False
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def spawn() -> bool:
    """Start the overlay process unless it's already running or can't run here."""
    if config.pet_running() or not available():
        return False
    log = open(audit.private_dir() / "pet.log", "ab")          # noqa: SIM115 - handed to the child
    subprocess.Popen([sys.executable, "-m", "walled", "pet"], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    return True


def stop() -> None:
    try:
        pid = int(config.pet_pid_file().read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    config.pet_pid_file().unlink(missing_ok=True)


def anchor(size: tuple[int, int], screen: tuple[int, int], corner: str) -> tuple[int, int]:
    """Top-left window position that keeps the window's bottom corner pinned to the screen corner."""
    w, h = size
    sw, sh = screen
    x = MARGIN_X if corner == "bottom-left" else sw - w - MARGIN_X
    return max(0, x), max(0, sh - h - MARGIN_Y)


class Bridge:
    """Called from the page as window.pywebview.api.<name>(...). Public methods only."""

    def __init__(self, screen: tuple[int, int]) -> None:
        self._window: Any = None
        self._screen = screen
        self._size = IDLE

    def _corner(self) -> str:
        return str(config.load().settings.pet["corner"])

    def resize(self, mode: str) -> list[int]:
        self._size = OPEN if mode == "open" else IDLE
        if self._window is not None:
            x, y = anchor(self._size, self._screen, self._corner())
            self._window.resize(*self._size)
            self._window.move(x, y)
        return list(self._size)

    def open_app(self) -> bool:
        from .app import spawn_app
        spawn_app()
        return True

    def reconnect(self) -> str:
        """The service restarted (new port and token): hand the page a fresh launch link."""
        from .app import ensure_service, ticket
        info = ensure_service()
        return f"http://127.0.0.1:{info['port']}/buddy.html#k={ticket(info)}"

    def hide(self) -> bool:
        if self._window is not None:
            self._window.destroy()
        return True


def _kwargs(fn: Any, wanted: dict[str, Any]) -> dict[str, Any]:
    """pywebview's options grew over versions: pass only the ones this version knows."""
    try:
        names = set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return wanted
    return {k: v for k, v in wanted.items() if k in names}


def run() -> int:
    if not available():
        print("Pip's desktop mode needs pywebview and a display (pip install pywebview). "
              "Until then he lives in the corner of ChatGPT, Perplexity and Claude.")
        return 1
    if config.pet_running():
        print("Pip is already on your screen.")
        return 0
    import webview

    from .app import ensure_service, ticket
    info = ensure_service()
    url = f"http://127.0.0.1:{info['port']}/buddy.html#k={ticket(info)}"
    screens = getattr(webview, "screens", None) or []
    screen = (int(screens[0].width), int(screens[0].height)) if screens else (1440, 900)
    bridge = Bridge(screen)
    x, y = anchor(IDLE, screen, bridge._corner())
    options = _kwargs(webview.create_window, {
        "js_api": bridge, "width": IDLE[0], "height": IDLE[1], "x": x, "y": y, "frameless": True,
        "on_top": True, "transparent": True, "easy_drag": False, "focus": False, "resizable": False,
        "shadow": False, "min_size": (120, 90), "background_color": "#000000"})
    bridge._window = webview.create_window("Pip", url, **options)
    pid_file = config.pet_pid_file()
    pid_file.write_text(str(os.getpid()))
    atexit.register(lambda: pid_file.unlink(missing_ok=True))

    def leave(*_: object) -> None:
        pid_file.unlink(missing_ok=True)
        os._exit(0)
    signal.signal(signal.SIGTERM, leave)
    webview.start(_settle, bridge._window)
    return 0


def _settle(window: Any) -> None:
    """macOS niceties, best effort: no Dock icon for Pip, and he stays put on every Space."""
    if sys.platform != "darwin":
        return
    try:
        import AppKit
        from PyObjCTools import AppHelper

        def apply() -> None:
            AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
            native = getattr(window, "native", None)
            if native is not None:
                native.setCollectionBehavior_(1 << 0 | 1 << 4 | 1 << 6)   # all Spaces · stationary · skip ⌘`
                native.setHidesOnDeactivate_(False)
        AppHelper.callAfter(apply)
    except Exception:                 # older pywebview / pyobjc: Pip still works, just with a Dock icon
        pass
