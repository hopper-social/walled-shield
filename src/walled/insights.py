"""Learn from how the user actually decides, and turn it into a score, badges and advice.

Everything is computed locally from the decision log (rule names and outcomes only) and
the live state. Nothing is sent anywhere.

* Protection score: weighted checklist of real, verifiable setup facts.
* Suggestions: e.g. "You let 'Email address' through 9 of 10 times → switch to Warn only?",
  "You always stop 'Payroll files' → Block automatically?", "Approvals end in 40 min → Renew".
* Badges: small rewards for things the user has really done (first catch, phone answer, …).
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

from walled_guard import audit, config

LEVELS = [(0, "At risk"), (25, "Basic"), (50, "Protected"), (75, "Hardened"), (95, "Fully hardened")]   # security posture


def _parse(t: str) -> float:
    try:
        return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0


def stats(days: int = 7) -> dict[str, Any]:
    since = time.time() - days * 86400
    events = [e for e in audit.recent(1000) if _parse(str(e.get("time", ""))) >= since]
    decisions = Counter(str(e.get("decision")) for e in events)
    rules = Counter(r for e in events for r in (e.get("rules") or []))
    return {"days": days, "events": len(events),
            "stopped": decisions["cancel"] + decisions["block"],
            "allowed": decisions["continue"] + decisions["allow"],
            "warned": decisions["warn"],
            "by_phone": sum(1 for e in events if e.get("via") == "phone"),
            "top_rules": rules.most_common(3)}


def checklist(state: dict[str, Any]) -> list[dict[str, Any]]:
    t, g, v = state["telegram"], state["guard"], state["vault"]
    servers = list((state.get("mcp") or {}).get("servers", {}).values())
    guardable = [s for s in servers if s in ("guarded", "UNGUARDED")]
    events = audit.recent(1000)
    cfg = config.load()
    items = [
        ("agent", "Browser agent registered", 15, bool(g["browsers"]), "browser"),
        ("extension", "Extension connected", 15, bool(g.get("extension_seen")), "browser"),
        ("first_catch", "First catch: try WALLED_SENSITIVE", 10,
         any(e.get("decision") in ("cancel", "continue", "block", "warn") for e in events), "browser"),
        ("telegram", "Telegram paired", 15, bool(t["configured"] and t["running"]), "telegram"),
        ("approvals", "Approvals on (24 h session)", 5, bool(t["session_active"]), "telegram"),
        ("connectors", "Claude connectors guarded", 15,
         bool(guardable) and all(s == "guarded" for s in guardable), "connectors"),
        ("vault", "Password vault created", 10, bool(v["exists"]), "vault"),
        ("ocr", "OCR ready for images", 5, bool(g["ocr"]), "browser"),
        ("own_rule", "Added a rule of your own", 10,
         any(r.kind in ("keyword", "regex", "filename", "folder") and r.id != "marker" for r in cfg.rules), "rules"),
    ]
    if not guardable:                                   # no Claude Desktop servers: not the user's fault
        items = [i for i in items if i[0] != "connectors"]
    actions = {"agent": "Connect your browser", "extension": "Load the browser extension", "first_catch": "Try your first catch",
               "telegram": "Pair Telegram", "approvals": "Turn Telegram approvals on", "connectors": "Guard your Claude tools",
               "vault": "Create your password vault", "ocr": "Install the OCR engine", "own_rule": "Add a word that's sensitive for you"}
    return [{"id": i, "title": title, "action": actions.get(i, title), "points": pts, "done": done, "view": view}
            for i, title, pts, done, view in items]


def score(items: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(i["points"] for i in items) or 1
    got = sum(i["points"] for i in items if i["done"])
    pct = round(100 * got / total)
    level = [name for floor, name in LEVELS if pct >= floor][-1]
    nxt = next((floor for floor, _ in LEVELS if floor > pct), None)
    return {"percent": pct, "xp": got, "max_xp": total, "level": level, "next_level_at": nxt}


def badges() -> list[dict[str, Any]]:
    events = audit.recent(1000)
    cfg = config.load()
    got = {
        "first_catch": any(e.get("decision") in ("cancel", "block") for e in events),
        "phone": any(e.get("via") == "phone" for e in events),
        "ten_catches": sum(1 for e in events if e.get("decision") in ("cancel", "block")) >= 10,
        "rule_maker": any(r.id != "marker" and r.kind != "builtin" for r in cfg.rules),
        "strict": any(r.action == "block" and r.enabled for r in cfg.rules),
    }
    meta = {"first_catch": ("shield", "Detection verified", "A sensitive send was intercepted before reaching an AI service"),
            "phone": ("phone", "Out-of-band approval", "A decision was made from the paired Telegram device"),
            "ten_catches": ("pulse", "Sustained enforcement", "10 or more sensitive sends intercepted"),
            "rule_maker": ("id", "Custom policy", "At least one organisation-specific rule is defined"),
            "strict": ("lock", "Hard block policy", "At least one rule blocks without asking")}
    return [{"id": k, "icon": meta[k][0], "title": meta[k][1], "text": meta[k][2], "earned": v} for k, v in got.items()]


def suggestions(state: dict[str, Any]) -> list[dict[str, Any]]:
    cfg = config.load()
    dismissed = set(cfg.settings.dismissed)
    out: list[dict[str, Any]] = []
    t = state["telegram"]
    exp = t.get("session_expires_at")
    if t.get("running") and t.get("session_active") and exp and exp - time.time() < 2 * 3600:
        mins = max(1, int((exp - time.time()) / 60))
        out.append({"id": "renew", "tone": "warn", "text": f"Telegram approvals switch off in {mins} min.",
                    "action": {"label": "Renew now", "kind": "renew"}})
    elif t.get("running") and not t.get("session_active"):
        out.append({"id": "renew", "tone": "warn", "text": "Telegram approvals are off, so Claude tools and phone answers are blocked.",
                    "action": {"label": "Turn on for 24 h", "kind": "renew"}})

    # Behaviour: how does the user decide, per rule, when asked?
    since = time.time() - 30 * 86400
    per_rule: dict[str, Counter[str]] = defaultdict(Counter)
    for e in audit.recent(1000):
        if e.get("via") not in ("desktop", "phone") or _parse(str(e.get("time", ""))) < since:
            continue
        for name in e.get("rules") or []:
            per_rule[name][str(e.get("decision"))] += 1
    by_name = {r.name: r for r in cfg.rules}
    for name, counts in per_rule.items():
        rule = by_name.get(name)
        n = sum(counts.values())
        if rule is None or rule.action != "ask" or n < 5:
            continue
        if counts["continue"] / n >= 0.8 and f"warn:{rule.id}" not in dismissed:
            out.append({"id": f"warn:{rule.id}", "tone": "info",
                        "text": f"You let “{name}” through {counts['continue']} of {n} times. Switch it to Warn only? "
                                "You'll still get a record and a Telegram alert, without the dialog.",
                        "action": {"label": "Switch to Warn", "kind": "rule", "rule": rule.id, "set": {"action": "warn"}}})
        elif counts["cancel"] / n >= 0.9 and f"block:{rule.id}" not in dismissed:
            out.append({"id": f"block:{rule.id}", "tone": "info",
                        "text": f"You stopped “{name}” {counts['cancel']} of {n} times. Block it automatically?",
                        "action": {"label": "Block automatically", "kind": "rule", "rule": rule.id, "set": {"action": "block"}}})
    if not state["guard"]["ocr"] and "ocr" not in dismissed:
        out.append({"id": "ocr", "tone": "info", "text": "Install Tesseract so text inside screenshots and scans is checked "
                    "(macOS: brew install tesseract).", "action": None})
    return [s for s in out if s["id"] not in dismissed or s["id"] == "renew"]
