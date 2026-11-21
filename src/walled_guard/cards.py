"""Telegram messages for the browser guard and the vault.

Only masked samples, rule names and file names are ever sent: Telegram bot chats are not
end-to-end encrypted, so prompt text, file contents and passwords stay on the computer.
"""

from __future__ import annotations

import html
from typing import Sequence

from .rules import Hit

DECISION = {
    "continue": "✅ <b>Sent</b> (Continue)",
    "cancel": "✋ <b>Stopped</b> (Cancel)",
    "warn": "⚠️ <b>Allowed with a warning</b> (rule set to warn)",
    "block": "⛔ <b>Blocked automatically</b> (rule set to block)",
}


def _hits(hits: Sequence[Hit]) -> str:
    return "\n".join(f"• {html.escape(h.name)}" + (f" — <code>{html.escape(h.sample)}</code>" if h.sample else "")
                     for h in list(hits)[:8])


def guard_card(site: str, kind: str, names: Sequence[str], hits: Sequence[Hit],
               decision: str | None = None, via: str | None = None) -> str:
    what = "a prompt" if kind == "prompt" else ("a file upload" if len(names) <= 1 else f"{len(names)} file uploads")
    lines = [f"🛡 <b>Sensitive data on {html.escape(site)}</b>", f"In {what}"]
    if kind == "file" and names:
        lines += [f"📎 {html.escape(n[:80])}" for n in list(names)[:5]]
    if hits:
        lines += ["", "<b>Matched</b>", _hits(hits)]
    if decision is None:
        lines += ["", "The dialog is also open on your computer. <b>Allow</b> sends it unchanged, "
                      "<b>Block</b> stops it. First answer wins."]
    else:
        where = {"phone": " on Telegram", "desktop": " on the computer", "rule": ""}.get(via or "", "")
        lines += ["", DECISION.get(decision, html.escape(decision)) + where]
    return "\n".join(lines)


def fill_card(host: str, username: str) -> str:
    user = username if len(username) <= 3 else username[:2] + "•••" + (username[username.find("@"):] if "@" in username else username[-1])
    return ("🔑 <b>Password fill requested</b>\n"
            f"Site: <code>{html.escape(host)}</code>\nAccount: <code>{html.escape(user)}</code>\n\n"
            "Allow fills it once in your browser. The password is never sent to Telegram.")
