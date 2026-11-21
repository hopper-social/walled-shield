"""Render a pending MCP request as a Telegram HTML approval card, with secrets redacted.

Showing arguments is what lets the remote person make an informed decision, so it is the
default for the shared daemon. Values whose *key* looks like a credential, and strings that
look like well-known token formats, are replaced before anything leaves the machine. When
the preview has to be shortened the card says so explicitly; operators who must never
approve from a partial view can run the daemon with ``--deny-truncated``.
"""

from __future__ import annotations

import html
import json
import re

MAX_ARGUMENT_PREVIEW = 2800   # characters of *escaped* HTML; the whole card must stay under 4096
TELEGRAM_MAX_TEXT = 4096
REDACTED = "«redacted»"

_SECRET_KEY = re.compile(
    r"(pass(word|wd|phrase)?|pwd|secret|token|api[_-]?key|apikey|auth(orization)?|bearer|"
    r"cookie|session[_-]?id|private[_-]?key|client[_-]?secret|credentials?|otp|pin|"
    r"(secret|access|signing|encryption|master|private)[_-]?(access[_-]?)?key|"
    r"connection[_-]?string|dsn|webhook[_-]?url)$",
    re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(sk-(ant-)?[A-Za-z0-9_\-]{16,}"            # OpenAI / Anthropic style keys
    r"|gh[pousr]_[A-Za-z0-9]{20,}"                # GitHub tokens
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"             # Slack
    r"|AKIA[0-9A-Z]{16}"                          # AWS access key id
    r"|AIza[0-9A-Za-z_\-]{30,}"                   # Google API key
    r"|\d{6,12}:[A-Za-z0-9_\-]{30,}"              # Telegram bot token
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"  # JWT
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$))")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}")
# scheme://user:password@host  →  scheme://«redacted»@host
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@")
# ?token=…  &api_key=…  (query strings and form bodies)
_QUERY_SECRET = re.compile(
    r"(?i)([?&;](?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|api[_-]?key|apikey|key|"
    r"secret|client[_-]?secret|password|passwd|pwd|sig|signature|auth|code)=)[^&#\s]+")
# "Authorization: …", "X-Api-Key: …", "password=…;" inside free text, logs or connection strings
_INLINE_SECRET = re.compile(
    r"(?i)\b((?:x-)?api-key|x-auth-token|authorization|proxy-authorization|cookie|set-cookie|"
    r"password|passwd|pwd|secret|token)(\s*[:=]\s*)(?!«redacted»)[^\s;&,\"']+")


def redact(value: object, depth: int = 0) -> object:
    """Return a copy of a JSON value with credential-like content replaced."""
    if depth > 64:
        return REDACTED
    if isinstance(value, dict):
        return {key: (REDACTED if isinstance(key, str) and _SECRET_KEY.search(key)
                      and value[key] not in (None, "", [], {}) else redact(value[key], depth + 1))
                for key in value}
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    """Replace secrets embedded in a free-text string (tokens, URLs, headers, key=value)."""
    text = _SECRET_VALUE.sub(REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + " " + REDACTED, text)
    text = _URL_USERINFO.sub(lambda m: m.group(1) + REDACTED + "@", text)
    text = _QUERY_SECRET.sub(lambda m: m.group(1) + REDACTED, text)
    return _INLINE_SECRET.sub(lambda m: m.group(1) + m.group(2) + REDACTED, text)


def tool_name(method: str, params: object) -> str | None:
    if method == "tools/call" and isinstance(params, dict) and isinstance(params.get("name"), str):
        return str(params["name"])
    return None


def arguments_of(method: str, params: object) -> object:
    if method == "tools/call" and isinstance(params, dict):
        return params.get("arguments", {})
    if method == "resources/read" and isinstance(params, dict):
        return {"uri": params.get("uri")}
    if method == "prompts/get" and isinstance(params, dict):
        return {"name": params.get("name"), "arguments": params.get("arguments", {})}
    return params


def argument_preview(method: str, params: object, limit: int = MAX_ARGUMENT_PREVIEW) -> tuple[str, bool]:
    """HTML-escaped, redacted, pretty JSON of the arguments, and whether it had to be cut.

    The limit applies to the *escaped* text: ``<`` becomes ``&lt;`` (4 characters), so code or
    HTML arguments can grow several-fold. Cutting before escaping kept the raw text short but
    let the card exceed Telegram's 4096-character limit, and an undeliverable card is a
    silent denial. The cut is made on the raw text, so an entity is never split.
    """
    rendered = json.dumps(redact(arguments_of(method, params)), ensure_ascii=False, indent=2,
                          sort_keys=False, default=str)
    escaped = html.escape(rendered)
    if len(escaped) <= limit:
        return escaped, False
    low, high = 0, min(len(rendered), limit)          # largest raw prefix that fits once escaped
    while low < high:
        middle = (low + high + 1) // 2
        if len(html.escape(rendered[:middle])) <= limit:
            low = middle
        else:
            high = middle - 1
    return html.escape(rendered[:low]), True


def approval_card(server: str, method: str, params: object, timeout: float,
                  show_inputs: bool = True) -> tuple[str, bool]:
    """HTML text for the Telegram prompt and whether the argument preview is truncated."""
    tool = tool_name(method, params)
    e = html.escape
    lines = ["🔐 <b>Claude wants to run an MCP action</b>", ""]
    if tool:
        lines.append(f"<b>Tool:</b> <code>{e(tool[:200])}</code>")
    lines.append(f"<b>Server:</b> {e(server[:200])}")
    lines.append(f"<b>Method:</b> <code>{e(method[:200])}</code>")
    truncated = False
    if show_inputs:
        preview, truncated = argument_preview(method, params)
        if preview in ("{}", "null", "[]"):  # escaping leaves these unchanged
            lines.append("<b>Arguments:</b> none")
        else:
            lines += ["<b>Arguments:</b>", f"<pre>{preview}</pre>"]  # already escaped
            if truncated:
                lines.append("⚠️ <i>Preview shortened — Allow sends the full, unseen request.</i>")
    else:
        lines.append("<b>Arguments:</b> hidden by configuration")
    lines += ["", f"⏳ Auto-blocks in {timeout:g}s if you don't answer."]
    return "\n".join(lines), truncated


def outcome_line(reason: str) -> str:
    return {
        "allow": "✅ <b>Allowed</b>",
        "always": "✅ <b>Allowed</b> — this tool is now auto-allowed until /clear",
        "deny": "❌ <b>Blocked</b>",
        "expired": "⌛ <b>No answer — blocked</b>",
        "cancelled": "↩️ <b>Cancelled by Claude</b>",
        "shutdown": "⛔ <b>Gate stopped — blocked</b>",
        "session ended": "🔒 <b>Approvals turned off — blocked</b>",
    }.get(reason, "❌ <b>Blocked</b> (approval unavailable)")
