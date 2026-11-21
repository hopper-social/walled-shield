"""Route every MCP server in Claude Desktop's config through the approval gate (and back).

``wrap`` rewrites each stdio entry of ``claude_desktop_config.json``

    "files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/x"]}

into

    "files": {"command": "/usr/bin/python3",
              "args": ["-m", "mcp_approval_gate", "stdio", "--server", "files", "--approver", "daemon",
                       "--autostart", "--", "npx", "-y", "@modelcontextprotocol/server-filesystem", "/x"]}

so Claude Desktop can no longer reach the original server except through the gate. The
original command is kept verbatim after ``--``, which makes ``unwrap`` exact. ``env`` and any
other keys are preserved. A timestamped backup is written before every change, and the new
file is written atomically. Entries without a ``command`` (remote ``url`` connectors) are
reported, never silently left unguarded.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

GATE_MODULE = ["-m", "mcp_approval_gate"]


def default_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home()) / "Claude/claude_desktop_config.json"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "Claude/claude_desktop_config.json"


@dataclass
class Report:
    wrapped: list[str] = field(default_factory=list)
    unwrapped: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    backup: Path | None = None
    changed: bool = False


def is_wrapped(entry: Mapping[str, object]) -> bool:
    """True for entries written by this tool (current ``-m`` form or the legacy script form)."""
    args = entry.get("args")
    if not (isinstance(args, list) and len(args) >= 3 and all(isinstance(a, str) for a in args)
            and "--" in args):
        return False
    if args[:3] == [*GATE_MODULE, "stdio"]:
        return True
    return Path(args[0]).name in ("main.py", "telegram_launcher.py") and args[1] == "stdio"


def wrap_entry(name: str, entry: Mapping[str, object], python: str, approver: str,
               strict: bool) -> dict[str, object]:
    command = entry["command"]
    args = entry.get("args") or []
    if not isinstance(command, str) or not command or not isinstance(args, list) or \
            not all(isinstance(a, str) for a in args):
        raise ValueError("command must be a string and args a list of strings")
    gate_args = [*GATE_MODULE, "stdio", "--server", name, "--approver", approver]
    if approver == "daemon":
        gate_args.append("--autostart")
    if strict:
        gate_args.append("--strict")
    wrapped = dict(entry)
    wrapped["command"] = python
    wrapped["args"] = gate_args + ["--", command, *args]
    return wrapped


def unwrap_entry(entry: dict[str, Any]) -> dict[str, Any]:
    args = entry["args"]
    original = args[args.index("--") + 1:]
    if not original:
        raise ValueError("wrapped entry has no original command")
    restored = dict(entry)
    restored["command"] = original[0]
    restored["args"] = original[1:]
    if not restored["args"]:
        restored.pop("args")
    return restored


def _load(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Claude Desktop config must be a JSON object")
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must be an object")
    return data


def _save(path: Path, data: dict[str, Any], report: Report) -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 1
    while backup.exists():  # never overwrite an earlier backup
        counter += 1
        backup = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
    shutil.copy2(path, backup)
    report.backup = backup
    temporary = path.with_name(f".{path.name}.tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        shutil.copymode(path, temporary)
    except OSError:
        pass
    os.replace(temporary, path)


def wrap(path: Path, *, only: set[str] | None = None, exclude: set[str] | None = None,
         approver: str = "daemon", strict: bool = False, python: str | None = None,
         dry_run: bool = False) -> tuple[Report, dict[str, Any]]:
    data = _load(path)
    servers = data.setdefault("mcpServers", {})
    report = Report()
    python = python or sys.executable
    for name, entry in list(servers.items()):
        if (only and name not in only) or (exclude and name in exclude):
            continue
        if not isinstance(entry, dict):
            report.skipped[name] = "not an object"
            continue
        if is_wrapped(entry):
            args = entry.get("args")
            if isinstance(args, list) and args[:2] == GATE_MODULE:
                report.already.append(name)
                continue
            entry = unwrap_entry(entry)  # legacy script-path wrapping: re-wrap with the module form
        if "command" not in entry:
            report.skipped[name] = ("remote connector (no local command) — guard it with "
                                    "'mcp-approval-gate http' behind an HTTPS gateway")
            continue
        try:
            servers[name] = wrap_entry(name, entry, python, approver, strict)
        except ValueError as exc:
            report.skipped[name] = str(exc)
            continue
        report.wrapped.append(name)
    report.changed = bool(report.wrapped)
    if report.changed and not dry_run:
        _save(path, data, report)
    return report, data


def unwrap(path: Path, *, only: set[str] | None = None, dry_run: bool = False) -> tuple[Report, dict[str, Any]]:
    data = _load(path)
    servers = data.get("mcpServers", {})
    report = Report()
    for name, entry in list(servers.items()):
        if only and name not in only:
            continue
        if isinstance(entry, dict) and is_wrapped(entry):
            try:
                servers[name] = unwrap_entry(entry)
                report.unwrapped.append(name)
            except ValueError as exc:
                report.skipped[name] = str(exc)
    report.changed = bool(report.unwrapped)
    if report.changed and not dry_run:
        _save(path, data, report)
    return report, data


def status(path: Path) -> dict[str, str]:
    servers = _load(path).get("mcpServers", {})
    result = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            result[name] = "invalid"
        elif is_wrapped(entry):
            result[name] = "guarded"
        elif "command" in entry:
            result[name] = "UNGUARDED"
        else:
            result[name] = "remote (not guardable here)"
    return result
