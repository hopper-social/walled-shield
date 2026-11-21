"""Install the approval daemon as a login service (launchd on macOS, systemd --user on Linux).

Running as a service means approvals keep working after reboots and crashes; the daemon is
restarted automatically. Proxies started with ``--autostart`` also spawn it on demand, so
the service is recommended rather than required.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .paths import ensure_private_dir, state_dir

LABEL = "com.walled.shield"
UNIT = "walled-shield.service"


def _command(python: str) -> list[str]:
    # One background process for the whole product: Telegram bot, MCP approvals, browser-guard
    # alerts, password vault and the Control Center API (``walled service``).
    return [python, "-m", "walled", "service"]


def _plist(python: str) -> str:
    log = escape(str(state_dir() / "daemon.log"))
    args = "".join(f"<string>{escape(a)}</string>" for a in _command(python))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>Umask</key><integer>63</integer>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def _unit(python: str) -> str:
    return f"""[Unit]
Description=Walled Shield (Telegram approvals, AI data guard, password vault)
After=network-online.target

[Service]
ExecStart={" ".join(shlex.quote(a) for a in _command(python))}
Restart=always
RestartSec=5
# Hardening that works in unprivileged user services (no mount namespaces required).
UMask=0077
NoNewPrivileges=yes
LockPersonality=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
SystemCallArchitectures=native

[Install]
WantedBy=default.target
"""


def install(python: str | None = None, load: bool = True) -> str:
    python = python or sys.executable
    ensure_private_dir(state_dir())
    if sys.platform == "darwin":
        target = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_plist(python), encoding="utf-8")
        if load and shutil.which("launchctl"):
            domain = f"gui/{os.getuid()}"
            subprocess.run(["launchctl", "bootout", domain, str(target)], capture_output=True)
            subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        return f"launchd agent installed: {target}"
    if sys.platform.startswith("linux"):
        target = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user" / UNIT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_unit(python), encoding="utf-8")
        if load and shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=True)
        return f"systemd user unit installed: {target}"
    raise OSError("Automatic service install supports macOS and Linux; run 'mcp-approval-gate daemon' manually")


def uninstall() -> str:
    if sys.platform == "darwin":
        target = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
        if shutil.which("launchctl"):
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(target)], capture_output=True)
        target.unlink(missing_ok=True)
        return "launchd agent removed"
    if sys.platform.startswith("linux"):
        target = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user" / UNIT
        if shutil.which("systemctl"):
            subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], capture_output=True)
        target.unlink(missing_ok=True)
        return "systemd user unit removed"
    raise OSError("Unsupported platform")
