"""Register the agent as a native-messaging host for Chromium browsers.

For each browser this writes a host manifest that tells the browser which program to
start (a small wrapper script that runs ``python -m walled_guard host``) and which
extension may talk to it. The extension ID is fixed by the public key in the
extension's manifest, so it is the same on every computer.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from .audit import state_dir

HOST_NAME = "com.walled.guard"
EXTENSION_ID = "lbnpgknepeocidimhgmodenkcfngmkdj"


@dataclass
class Target:
    browser: str
    manifest_dir: Path | None = None        # macOS / Linux
    registry_key: str | None = None         # Windows (HKEY_CURRENT_USER)


def targets() -> list[Target]:
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
        dirs = {
            "Google Chrome": base / "Google" / "Chrome",
            "Chrome for Testing": base / "Google" / "Chrome for Testing",
            "Chromium": base / "Chromium",
            "Microsoft Edge": base / "Microsoft Edge",
            "Brave": base / "BraveSoftware" / "Brave-Browser",
            "Arc": base / "Arc" / "User Data",
        }
        return [Target(name, path / "NativeMessagingHosts") for name, path in dirs.items()]
    if sys.platform.startswith("win"):
        keys = {
            "Google Chrome": r"Software\Google\Chrome\NativeMessagingHosts",
            "Chromium": r"Software\Chromium\NativeMessagingHosts",
            "Microsoft Edge": r"Software\Microsoft\Edge\NativeMessagingHosts",
            "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
        }
        return [Target(name, registry_key=key + "\\" + HOST_NAME) for name, key in keys.items()]
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    dirs = {
        "Google Chrome": config / "google-chrome",
        "Chrome for Testing": config / "google-chrome-for-testing",
        "Chromium": config / "chromium",
        "Microsoft Edge": config / "microsoft-edge",
        "Brave": config / "BraveSoftware" / "Brave-Browser",
    }
    return [Target(name, path / "NativeMessagingHosts") for name, path in dirs.items()]


def manifest(host_path: Path, extension_id: str = EXTENSION_ID) -> dict[str, object]:
    return {
        "name": HOST_NAME,
        "description": "Walled Guard desktop agent (sensitive prompt and upload guard)",
        "path": str(host_path),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id}/"],
    }


def write_launcher(directory: Path, python: str = sys.executable) -> Path:
    """Write the program the browser starts. Paths with spaces are quoted."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    extra_env = "".join(f"export {k}={_sh_quote(v)}\n" for k, v in _passthrough_env().items())
    if sys.platform.startswith("win"):
        path = directory / "walled-guard-host.bat"
        path.write_text(f'@echo off\r\n"{python}" -m walled_guard host %*\r\n', encoding="utf-8")
        return path
    path = directory / "walled-guard-host"
    path.write_text(f'#!/bin/sh\n{extra_env}exec {_sh_quote(python)} -m walled_guard host "$@"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _passthrough_env() -> dict[str, str]:
    """Browsers start the host with a minimal PATH; keep Tesseract findable (Homebrew paths)."""
    # Keep only absolute, existing directories: an empty or relative PATH entry ("." or "")
    # would let a file in whatever folder Chrome starts us from shadow a real program.
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if os.path.isabs(p) and os.path.isdir(p)]
    for p in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
        if os.path.isdir(p) and p not in parts:
            parts.append(p)
    env = {"PATH": os.pathsep.join(parts)}
    package_root = Path(__file__).resolve().parents[1]
    if "site-packages" not in package_root.parts:      # running from a source checkout, not pip-installed
        env["PYTHONPATH"] = str(package_root)
    for key in ("WALLED_GUARD_HOME", "TESSDATA_PREFIX"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def install(extension_id: str = EXTENSION_ID, browsers: list[str] | None = None) -> list[str]:
    from .audit import private_dir
    launcher = write_launcher(private_dir())
    done = []
    for target in targets():
        if browsers and not any(b.lower() in target.browser.lower() for b in browsers):
            continue
        if target.manifest_dir is not None:
            # Install for browsers whose profile folder exists (or every one if asked explicitly).
            if not browsers and not target.manifest_dir.parent.exists():
                continue
            target.manifest_dir.mkdir(parents=True, exist_ok=True)
            path = target.manifest_dir / f"{HOST_NAME}.json"
            path.write_text(json.dumps(manifest(launcher, extension_id), indent=2), encoding="utf-8")
            done.append(f"{target.browser}: {path}")
        elif target.registry_key is not None:
            manifest_path = state_dir() / f"{HOST_NAME}.json"
            manifest_path.write_text(json.dumps(manifest(launcher, extension_id), indent=2), encoding="utf-8")
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, target.registry_key) as key:   # type: ignore[attr-defined]
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))           # type: ignore[attr-defined]
            done.append(f"{target.browser}: HKCU\\{target.registry_key}")
    return done


def uninstall() -> list[str]:
    removed = []
    for target in targets():
        if target.manifest_dir is not None:
            path = target.manifest_dir / f"{HOST_NAME}.json"
            if path.exists():
                path.unlink()
                removed.append(str(path))
        elif target.registry_key is not None:
            try:
                import winreg
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, target.registry_key)   # type: ignore[attr-defined]
                removed.append("HKCU\\" + target.registry_key)
            except OSError:
                pass
    return removed


def installed() -> list[str]:
    found = []
    for target in targets():
        if target.manifest_dir is not None and (target.manifest_dir / f"{HOST_NAME}.json").exists():
            found.append(target.browser)
        elif target.registry_key is not None:
            try:
                import winreg
                winreg.OpenKey(winreg.HKEY_CURRENT_USER, target.registry_key).Close()   # type: ignore[attr-defined]
                found.append(target.browser)
            except OSError:
                pass
    return found
