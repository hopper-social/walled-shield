"""User settings: rules and preferences, stored privately as JSON in the agent's state folder.

The file is read on every request by the short-lived native host and by the long-running
service, so edits made in the Control Center apply to the very next prompt or upload.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .audit import state_dir
from .rules import Rule, RuleEngine, default_rules

VERSION = 1
_ENGINES: dict[str, RuleEngine] = {}

# Pip, the little guardian that lives in a corner of the screen (or of the browser).
PET_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "where": "auto",          # auto: on the desktop when the overlay can run, else in the browser
    "mood": "calm",           # calm · playful · focus (rarely moves, quieter)
    "sound": "purr",          # what petting sounds like: purr · chimes · bubbles · off
    "volume": 45,             # 0-100; alerts are soft by design
    "corner": "bottom-right",
    "intro": False,           # Pip said hello once
}
_PET_CHOICES = {"where": ("auto", "desktop", "browser", "both"), "mood": ("calm", "playful", "focus"),
                "sound": ("purr", "chimes", "bubbles", "off"), "corner": ("bottom-right", "bottom-left")}


def normalize_pet(raw: object) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    pet = dict(PET_DEFAULTS)
    pet["enabled"] = bool(data.get("enabled", True))
    pet["intro"] = bool(data.get("intro", False))
    for key, options in _PET_CHOICES.items():
        if data.get(key) in options:
            pet[key] = data[key]
    try:
        pet["volume"] = max(0, min(100, int(data.get("volume", PET_DEFAULTS["volume"]))))
    except (TypeError, ValueError):
        pass
    return pet


def pet_pid_file() -> Path:
    return state_dir() / "pet.pid"


def pet_running() -> bool:
    """True while the desktop overlay process is alive (it writes its pid on start)."""
    try:
        pid = int(pet_pid_file().read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def pet_for_browser(settings: "Settings") -> dict[str, Any]:
    """What the extension needs: show Pip in the page corner unless he's already on the desktop."""
    pet = dict(settings.pet)
    where = pet["where"]
    pet["in_browser"] = bool(pet["enabled"]) and (where in ("browser", "both") or (where == "auto" and not pet_running()))
    return pet


@dataclass
class Settings:
    dialog_timeout: int = 120               # seconds before an unanswered dialog counts as Cancel
    vault_autolock_minutes: int = 15
    fill_approval: str = "phone"            # phone: Telegram tap (desktop dialog if Telegram is off)
                                            # desktop: desktop dialog · none: unlocked vault fills directly
    guard_alerts: bool = True               # send Telegram alerts for browser hits (per-rule phone setting)
    mcp_rules: bool = True                  # scan Claude MCP tool calls with the same rules
    onboarded: bool = False                 # first-run quest finished or skipped
    goals: list[str] = field(default_factory=lambda: ["browser", "claude", "passwords"])
    dismissed: list[str] = field(default_factory=list)   # suggestion ids the user dismissed
    activity_channel: bool = True           # mirror activity to the Telegram activity channel (if paired)
    tests: dict[str, Any] = field(default_factory=dict)  # Test center results: id → {ok, at, detail}
    pet: dict[str, Any] = field(default_factory=lambda: dict(PET_DEFAULTS))

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Settings":
        s = cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})
        s.dialog_timeout = max(15, min(600, int(s.dialog_timeout)))
        s.vault_autolock_minutes = max(1, min(240, int(s.vault_autolock_minutes)))
        if s.fill_approval not in ("phone", "desktop", "none"):
            s.fill_approval = "phone"
        s.onboarded = bool(s.onboarded)
        s.goals = [g for g in (s.goals if isinstance(s.goals, list) else []) if g in ("browser", "claude", "passwords")]
        s.dismissed = [str(d)[:80] for d in (s.dismissed if isinstance(s.dismissed, list) else [])][-200:]
        s.activity_channel = bool(s.activity_channel)
        s.tests = {str(k)[:40]: v for k, v in (s.tests if isinstance(s.tests, dict) else {}).items()
                   if isinstance(v, dict)}
        s.pet = normalize_pet(s.pet)
        return s


@dataclass
class Config:
    rules: list[Rule] = field(default_factory=default_rules)
    settings: Settings = field(default_factory=Settings)

    def engine(self) -> RuleEngine:
        """Compiled rules, reused until the rules change (regexes are compiled once)."""
        key = json.dumps([r.to_json() for r in self.rules], sort_keys=True)
        cached = _ENGINES.get(key)
        if cached is None:
            if len(_ENGINES) > 8:
                _ENGINES.clear()
            cached = _ENGINES[key] = RuleEngine(self.rules)
        return cached

    def to_json(self) -> dict[str, Any]:
        return {"version": VERSION, "rules": [r.to_json() for r in self.rules], "settings": asdict(self.settings)}


def path() -> Path:
    return state_dir() / "config.json"


def load() -> Config:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError):
        return Config()                     # unreadable file: safe defaults (the marker rule is on)
    rules = []
    for item in data.get("rules", []):
        try:
            rules.append(Rule.from_json(item))
        except (TypeError, ValueError):
            continue
    if not rules:
        rules = default_rules()
    return Config(rules, Settings.from_json(data.get("settings") or {}))


def save(config: Config) -> None:
    for r in config.rules:
        r.validate()
    target = path()
    from .audit import private_dir
    private_dir()
    tmp = target.with_name(f".config.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(config.to_json(), fh, indent=2)
    os.replace(tmp, target)
