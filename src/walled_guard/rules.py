"""What counts as sensitive: the rule engine shared by the browser guard and the MCP gate.

A rule is one of
  keyword   exact text such as WALLED_SENSITIVE, a client name or a project codename
  builtin   a ready-made detector (card numbers, Aadhaar, PAN, API keys, private keys…)
  regex     a user-written pattern
  filename  a glob matched against an uploaded file's name   (e.g. *payroll*.xlsx)
  folder    a folder; MCP tool calls touching a path inside it are flagged

and carries what to do when it matches:
  action    ask   → native Continue / Cancel dialog (the assignment's behaviour)
            warn  → let it through, but record it and alert on Telegram
            block → stop it without asking
  phone     off | notify (Telegram card after the decision) | approve (Telegram Allow/Block
            races the desktop dialog; whichever answers first wins)

Patterns are written in the subset of regex syntax shared by Python and JavaScript, so
the browser extension can run the same keyword / builtin / regex rules locally and a
prompt without any hit is never delayed.
"""

from __future__ import annotations

import fnmatch
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

MARKER = "WALLED_SENSITIVE"
KINDS = ("keyword", "builtin", "regex", "filename", "folder")
ACTIONS = ("ask", "warn", "block")
PHONE = ("off", "notify", "approve")
SEVERITY = {"warn": 0, "ask": 1, "block": 2}


# ------------------------------------------------------------------ validators
def luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_VERHOEFF_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
               [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
               [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
               [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
               [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
               [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff(value: str) -> bool:
    """Aadhaar numbers end in a Verhoeff check digit; this removes most false positives."""
    digits = [int(c) for c in value if c.isdigit()]
    if len(digits) != 12:
        return False
    c = 0
    for i, d in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][d]]
    return c == 0


VALIDATORS: dict[str, Callable[[str], bool]] = {"luhn": luhn, "verhoeff": verhoeff}


# ------------------------------------------------------------------ built-in detectors
@dataclass(frozen=True)
class Detector:
    label: str
    pattern: str
    flags: str = ""                 # "i" for case-insensitive (same letter in Python and JS)
    validator: str | None = None
    default: bool = False           # enabled in a fresh install
    example: str = ""


BUILTINS: dict[str, Detector] = {
    "card": Detector("Payment card number", r"\b(?:\d[ -]?){12,18}\d\b", validator="luhn", default=True,
                     example="4111 1111 1111 1111"),
    "aadhaar": Detector("Aadhaar number", r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b", validator="verhoeff",
                        default=True, example="2345 6789 0124"),
    "private_key": Detector("Private key", r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----", default=True,
                            example="-----BEGIN RSA PRIVATE KEY-----"),
    "api_key": Detector(
        "API key or token",
        r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36,}"
        r"|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|\d{8,10}:AA[A-Za-z0-9_-]{33})\b",
        default=True, example="ghp_" + "a1B2" * 9),
    "pan": Detector("PAN (India)", r"\b[A-Z]{3}[ABCFGHLJPT][A-Z]\d{4}[A-Z]\b", example="ABCPE1234F"),
    "ifsc": Detector("Bank IFSC code", r"\b[A-Z]{4}0[A-Z0-9]{6}\b", example="HDFC0001234"),
    "email": Detector("Email address", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", example="name@example.com"),
    "phone_in": Detector("Indian mobile number", r"(?:\+91[ -]?)?\b[6-9]\d{4}[ -]?\d{5}\b", example="+91 98765 43210"),
    "jwt": Detector("JSON Web Token", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
                    example="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijk"),
    "password": Detector("Password in text", r"\b(?:password|passwd|pwd|passcode)\s*[:=]\s*\S{4,}", flags="i",
                         example="password: hunter22"),
}


# Catastrophic backtracking guard: a quantified group whose body is itself quantified,
# e.g. (a+)+, (\w*)*, (x|y+){2,}. Python's re has no timeout, so reject these up front.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)[+*{]")
REGEX_SCAN_LIMIT = 2 * 1024 * 1024          # custom regexes see at most the first 2 MB of a text


# ------------------------------------------------------------------ rules
@dataclass
class Rule:
    name: str
    kind: str
    pattern: str
    enabled: bool = True
    action: str = "ask"
    phone: str = "notify"
    case_sensitive: bool = False    # keywords and regex
    browser: bool = True            # applies to ChatGPT / Perplexity
    mcp: bool = True                # applies to Claude MCP tool calls
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown rule type {self.kind!r}")
        if self.action not in ACTIONS or self.phone not in PHONE:
            raise ValueError("invalid action or phone setting")
        if not self.name.strip() or not self.pattern.strip():
            raise ValueError("name and pattern are required")
        if len(self.pattern) > 2000:
            raise ValueError("pattern too long")
        if self.kind == "builtin" and self.pattern not in BUILTINS:
            raise ValueError(f"unknown detector {self.pattern!r}")
        if self.kind == "regex":
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
            if _NESTED_QUANTIFIER.search(self.pattern):
                raise ValueError("this pattern can take exponential time on some text (a repeated group "
                                 "that is itself repeated, like (a+)+). Simplify it.")
            if re.search(r"\(\?[aiLmsux]|\(\?P|\\[AZz]|\(\?<[=!]?[A-Za-z]|\(\?>|[*+?}]\+", self.pattern):
                raise ValueError("use regex syntax that works in both Python and JavaScript "
                                 "(no inline flags, named groups or possessive quantifiers)")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Rule":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        rule = cls(**known)
        rule.validate()
        return rule


def default_rules() -> list[Rule]:
    rules = [Rule("WALLED_SENSITIVE marker", "keyword", MARKER, case_sensitive=True, id="marker")]
    for key, det in BUILTINS.items():
        rules.append(Rule(det.label, "builtin", key, enabled=det.default, id=f"builtin-{key}"))
    return rules


@dataclass
class Hit:
    rule_id: str
    name: str
    action: str
    phone: str
    sample: str                     # masked, safe to show on Telegram

    def to_json(self) -> dict[str, str]:
        return asdict(self)


def mask(value: str, kind: str) -> str:
    value = value.strip()
    if kind == "keyword":
        return value[:40]
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8 and len(digits) >= len(value) * 0.6:
        return "•••• " + digits[-4:]
    if len(value) <= 6:
        return "•" * len(value)
    return value[:3] + "•" * min(12, len(value) - 5) + value[-2:]


_WS = re.compile(r"\s+")
_OCR_LOOKALIKES = str.maketrans({"0": "O", "1": "I", "l": "I", "|": "I", "!": "I", "5": "S", "$": "S", "€": "E"})


def _ocr_fold(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.translate(_OCR_LOOKALIKES).upper()).replace("L", "I")


class RuleEngine:
    """Compiled rules. ``scan`` is called with the text and how it was obtained:

    typed      exactly what the user typed (prompts)
    extracted  text from a file or PDF: keywords also match across line breaks
    ocr        text read from pixels: keywords also tolerate look-alike characters
    """

    def __init__(self, rules: list[Rule]):
        self.rules = [r for r in rules if r.enabled]
        self._compiled: list[tuple[Rule, re.Pattern[str] | None, Callable[[str], bool] | None]] = []
        for r in self.rules:
            if r.kind == "builtin":
                det = BUILTINS[r.pattern]
                flags = re.IGNORECASE if "i" in det.flags else 0
                self._compiled.append((r, re.compile(det.pattern, flags),
                                       VALIDATORS.get(det.validator or "")))
            elif r.kind == "regex":
                self._compiled.append((r, re.compile(r.pattern, 0 if r.case_sensitive else re.IGNORECASE), None))
            elif r.kind == "keyword":
                self._compiled.append((r, None, None))

    def scan(self, text: str, mode: str = "typed", surface: str = "browser", builtins: bool = True) -> list[Hit]:
        hits: list[Hit] = []
        if not text:
            return hits
        compact = _WS.sub("", text) if mode != "typed" else ""
        folded = _ocr_fold(text) if mode == "ocr" else ""
        for rule, pattern, validator in self._compiled:
            if not getattr(rule, surface, True) or (not builtins and rule.kind == "builtin"):
                continue
            if pattern is None:                                   # keyword
                hay, needle = (text, rule.pattern) if rule.case_sensitive else (text.lower(), rule.pattern.lower())
                found = needle in hay
                if not found and mode != "typed":
                    c = compact if rule.case_sensitive else compact.lower()
                    found = _WS.sub("", needle) in c
                if not found and mode == "ocr":
                    target = _ocr_fold(rule.pattern)
                    found = len(target) >= 6 and target in folded
                if found:
                    hits.append(Hit(rule.id, rule.name, rule.action, rule.phone, mask(rule.pattern, "keyword")))
                continue
            target = text if rule.kind == "builtin" else text[:REGEX_SCAN_LIMIT]
            for match in pattern.finditer(target):
                if validator is None or validator(match.group(0)):
                    hits.append(Hit(rule.id, rule.name, rule.action, rule.phone, mask(match.group(0), rule.kind)))
                    break
        return hits

    def scan_filename(self, name: str) -> list[Hit]:
        out = []
        for r in self.rules:
            if r.kind == "filename" and r.browser and fnmatch.fnmatch(name.lower(), r.pattern.lower()):
                out.append(Hit(r.id, r.name, r.action, r.phone, name[:80]))
        return out

    def scan_paths(self, strings: list[str]) -> list[Hit]:
        """Folder and filename rules against path-like strings (MCP tool arguments)."""
        out: list[Hit] = []
        folders = [r for r in self.rules if r.kind == "folder" and r.mcp]
        names = [r for r in self.rules if r.kind == "filename" and r.mcp]
        if not (folders or names):
            return out
        for s in strings:
            if len(s) > 4096 or ("/" not in s and "\\" not in s and not names):
                continue
            path = os.path.normpath(os.path.expanduser(s))
            for r in folders:
                root = os.path.normpath(os.path.expanduser(r.pattern))
                if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
                    out.append(Hit(r.id, r.name, r.action, r.phone, Path(path).name or path))
            for r in names:
                if fnmatch.fnmatch(Path(path).name.lower(), r.pattern.lower()):
                    out.append(Hit(r.id, r.name, r.action, r.phone, Path(path).name))
        return dedupe(out)

    def browser_export(self) -> list[dict[str, Any]]:
        """Rules the extension can evaluate locally (keyword / builtin / regex)."""
        out: list[dict[str, Any]] = []
        for r in self.rules:
            if not r.browser:
                continue
            if r.kind == "keyword":
                out.append({"id": r.id, "kind": "keyword", "pattern": r.pattern, "case": r.case_sensitive})
            elif r.kind == "builtin":
                det = BUILTINS[r.pattern]
                out.append({"id": r.id, "kind": "regex", "pattern": det.pattern, "flags": det.flags,
                            "validator": det.validator})
            elif r.kind == "regex":
                out.append({"id": r.id, "kind": "regex", "pattern": r.pattern,
                            "flags": "" if r.case_sensitive else "i", "validator": None})
        return out


def dedupe(hits: list[Hit]) -> list[Hit]:
    seen: set[str] = set()
    out = []
    for h in hits:
        if h.rule_id not in seen:
            seen.add(h.rule_id)
            out.append(h)
    return out


def strongest(hits: list[Hit]) -> str:
    return max((h.action for h in hits), key=SEVERITY.__getitem__, default="warn")


def phone_mode(hits: list[Hit]) -> str:
    modes = {h.phone for h in hits}
    return "approve" if "approve" in modes else "notify" if "notify" in modes else "off"


def strings_in(value: object, limit: int = 2000) -> list[str]:
    """Every string inside a JSON value (keys included), for scanning MCP arguments."""
    out: list[str] = []
    stack = [value]
    visited = 0
    while stack and len(out) < limit and visited < limit * 5:      # bounded even for huge/deep JSON
        visited += 1
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            out.extend(k for k in item if isinstance(k, str))
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return out
