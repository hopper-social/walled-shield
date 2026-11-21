"""Where Walled keeps things on disk."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def extension_source() -> Path:
    """The browser extension shipped inside the package (also the repo's ./extension)."""
    return Path(__file__).with_name("extension")


def extension_home() -> Path:
    """A stable, easy-to-find folder for Chrome's "Load unpacked" (survives deleting Downloads)."""
    if sys.platform == "darwin":
        return Path.home() / "Applications" / "Walled Extension"
    if sys.platform.startswith("win"):
        import os
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Walled" / "extension"
    return Path.home() / ".local" / "share" / "walled" / "extension"


def install_extension() -> Path:
    """Copy (or refresh) the extension into its stable folder. Same key → same extension ID."""
    target = extension_home()
    source = extension_source().resolve()
    if target.resolve() == source:
        return target
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    return target


def reveal(path: Path) -> None:
    import subprocess
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


_CHROMES_MAC = ("Google Chrome", "Brave Browser", "Microsoft Edge", "Chromium", "Arc")
_CHROMES_LINUX = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser", "microsoft-edge")


def copy_to_clipboard(text: str) -> bool:
    """Best effort: pbcopy / wl-copy / xclip / xsel / clip. False if none worked."""
    import subprocess
    candidates = ([["pbcopy"]] if sys.platform == "darwin" else [["clip"]] if sys.platform.startswith("win")
                  else [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]])
    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, timeout=3, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def open_extensions_page() -> dict[str, object]:
    """Make "Load unpacked" a 3-click job: open chrome://extensions and put the folder path on the clipboard.

    Returns {"browser": name or None, "copied": bool, "path": str}.
    """
    import subprocess
    target = extension_home()
    if not (target / "manifest.json").exists():
        install_extension()
    copied = copy_to_clipboard(str(target))
    opened = None
    if sys.platform == "darwin":
        for app in _CHROMES_MAC:
            if Path(f"/Applications/{app}.app").exists() or (Path.home() / "Applications" / f"{app}.app").exists():
                if subprocess.run(["open", "-a", app, "chrome://extensions"], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=10).returncode == 0:
                    opened = app
                    break
    elif sys.platform.startswith("linux"):
        for exe in _CHROMES_LINUX:
            if shutil.which(exe):
                subprocess.Popen([exe, "chrome://extensions"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                opened = exe
                break
    return {"browser": opened, "copied": copied, "path": str(target)}
