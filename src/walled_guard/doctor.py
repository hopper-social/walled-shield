"""Start the registered host the way Chrome does, and report the first thing that fails.

Chrome reads the host manifest, runs the program in "path" with the extension origin as
its argument and a minimal environment, writes one framed message to stdin and expects
one framed reply on stdout. Anything else (a non-executable file, a crash at import, a
stray print on stdout, an argument the program rejects) shows up in the extension only as
"native host has exited" or "host not found". This command shows the real reason.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

from . import installer

ORIGIN = f"chrome-extension://{installer.EXTENSION_ID}/"


def _manifests() -> list[tuple[str, Path]]:
    found = []
    for target in installer.targets():
        if target.manifest_dir is not None:
            path = target.manifest_dir / f"{installer.HOST_NAME}.json"
            if path.exists():
                found.append((target.browser, path))
    return found


def check_manifest(path: Path) -> tuple[list[str], Path | None]:
    problems: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"manifest unreadable: {exc}"], None
    if data.get("name") != installer.HOST_NAME:
        problems.append(f'"name" is {data.get("name")!r}, expected {installer.HOST_NAME!r}')
    if data.get("type") != "stdio":
        problems.append('"type" must be "stdio"')
    if ORIGIN not in (data.get("allowed_origins") or []):
        problems.append(f'"allowed_origins" does not contain {ORIGIN} — Chrome will say "access forbidden"')
    exe = Path(str(data.get("path", "")))
    if not exe.is_absolute():
        problems.append('"path" must be absolute')
    elif not exe.exists():
        problems.append(f"program not found: {exe}")
    elif not os.access(exe, os.X_OK):
        problems.append(f"program is not executable: chmod +x '{exe}'")
    return problems, exe


def launch(exe: Path) -> tuple[bool, str]:
    """Run like Chrome: origin argument, minimal PATH, one framed ping."""
    env = {"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if sys.platform.startswith("win"):
        env = dict(os.environ)
    payload = json.dumps({"type": "ping", "id": 1}).encode()
    try:
        proc = subprocess.run([str(exe), ORIGIN], input=struct.pack("<I", len(payload)) + payload,
                              capture_output=True, timeout=30, env=env)
    except OSError as exc:
        return False, f"could not start it: {exc}"
    except subprocess.TimeoutExpired:
        return False, "no reply within 30 s"
    err = proc.stderr.decode(errors="replace").strip()
    out = proc.stdout
    if len(out) < 4:
        return False, f"exited with code {proc.returncode} before replying" + (f":\n{err}" if err else "")
    length = struct.unpack("<I", out[:4])[0]
    try:
        reply = json.loads(out[4:4 + length])
    except ValueError:
        return False, f"replied with something that is not a framed JSON message (stray output on stdout?): {out[:80]!r}"
    if not reply.get("ok"):
        return False, f"replied with an error: {reply}"
    return True, f"agent {reply.get('version')} · OCR {'yes' if reply.get('ocr') else 'no'} · dialog: {reply.get('dialog')}"


def run(summary: bool = True) -> int:
    manifests = _manifests()
    if not manifests:
        print("✗ No browser registration found. Run: walled-guard install")
        return 1
    healthy = True
    for browser, path in manifests:
        print(f"{browser}\n  manifest: {path}")
        problems, exe = check_manifest(path)
        for p in problems:
            print(f"  ✗ {p}")
        if problems or exe is None:
            healthy = False
            continue
        ok, detail = launch(exe)
        print(f"  {'✓' if ok else '✗'} {detail}")
        healthy &= ok
    if not summary:
        return 0 if healthy else 1
    print("\nAll good. If the popup still says “not found”, check that chrome://extensions shows the ID "
          f"{installer.EXTENSION_ID} and restart the browser." if healthy else
          "\nFix the ✗ lines above (re-running ./install.sh rewrites the registration), then restart the browser.")
    return 0 if healthy else 1
