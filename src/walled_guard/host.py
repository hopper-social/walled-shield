"""Chrome native-messaging host: the bridge between the extension and this desktop agent.

Wire format (defined by Chrome): each message is a 4-byte little-endian length followed
by that many bytes of UTF-8 JSON, on stdin (requests) and stdout (responses). Chrome
starts this process when the extension connects and closes stdin when it disconnects.

Requests
  {"type": "ping"}
      -> {"ok", "version", "ocr", "dialog", "rules": [...], "service": bool}
  {"type": "confirm_prompt", "site": "chatgpt", "text": "..."}
      -> {"ok", "found", "decision": "allow" | "continue" | "cancel", "hits": [...], "via"}
  {"type": "inspect_files", "site": "perplexity",
   "files": [{"name": "a.pdf", "mime": "application/pdf", "data": "<base64>"},
             {"name": "big.pdf", "mime": "application/pdf", "skipped": "larger than 25 MB"}]}
      -> {"ok", "results": [{"name", "kind", "found", "where", "error", "hits"}], "decision", "via"}
  {"type": "vault_find", "origin": "https://github.com"}           -> {"ok", "entries": [{id, username}]}
  {"type": "vault_fill", "origin": "...", "entry": "<entry id>"}             -> {"ok", "username", "password"}
  {"type": "vault_save", "origin": "...", "username": "...", "password": "..."} -> {"ok"}

"allow" means nothing sensitive was found (or every matching rule is set to warn), so no
dialog was shown. Every failure is {"ok": false, "error": "..."}; the extension blocks.

Decisions
  Each matching rule has an action (ask / warn / block) and a phone setting (off / notify /
  approve). The strongest action wins. "ask" shows the native dialog; with "approve" the same
  question also goes to Telegram and the first answer wins. Telegram needs the Walled service;
  without it the desktop dialog decides on its own.
"""

from __future__ import annotations

import base64
import json
import queue
import struct
import sys
import threading
from typing import Any, BinaryIO

from . import __version__, audit, cards, client, config, dialogs, inspector, ocr
from .rules import Hit, dedupe, phone_mode, strongest

SITES = {"chatgpt": "ChatGPT", "perplexity": "Perplexity", "claude": "Claude"}
MAX_MESSAGE = 64 * 1024 * 1024


def read_message(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(4)
    if len(header) < 4:
        return None                                  # Chrome closed the pipe
    (length,) = struct.unpack("<I", header)
    if length > MAX_MESSAGE:
        raise ValueError("message too large")
    payload = stream.read(length)
    if len(payload) < length:
        return None
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")
    return message


def write_message(stream: BinaryIO, message: dict[str, Any]) -> None:
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data)
    stream.flush()


# ------------------------------------------------------------------ decisions
def decide(site: str, kind: str, request: dialogs.Request, hits: list[Hit],
           names: list[str], settings: config.Settings, kinds: list[str] | None = None) -> tuple[str, str]:
    """Apply the rules' policy and report it to the service (live feed, activity channel)."""
    decision, via = _decide(site, kind, request, hits, names, settings)
    client.call({"op": "activity", "site": site, "kind": kind, "decision": "warn" if via == "rule" and decision == "allow"
                 else "block" if via == "rule" else decision, "via": via, "rules": [h.name for h in hits],
                 "kinds": kinds or []}, timeout=3)
    return decision, via


def _decide(site: str, kind: str, request: dialogs.Request, hits: list[Hit],
            names: list[str], settings: config.Settings) -> tuple[str, str]:
    """Apply the rules' policy. Returns (decision, via): decision is continue/cancel/allow."""
    action = strongest(hits) if hits else "ask"            # unreadable files: always ask
    phone = phone_mode(hits) if settings.guard_alerts else "off"
    request.rules = [h.name for h in hits]

    if action == "warn":
        _notify(phone, cards.guard_card(site, kind, names, hits, "warn", "rule"))
        audit.record(site, kind, "warn", count=max(1, len(names)), rules=request.rules, via="rule")
        return "allow", "rule"
    if action == "block":
        _notify(phone, cards.guard_card(site, kind, names, hits, "block", "rule"))
        audit.record(site, kind, "block", count=max(1, len(names)), rules=request.rules, via="rule")
        return "cancel", "rule"

    decision, via = _ask(request, site, kind, names, hits, phone, settings.dialog_timeout)
    if via == "desktop":                                    # a phone answer already stamped its card
        _notify(phone, cards.guard_card(site, kind, names, hits, decision, via))
    audit.record(site, kind, decision, count=max(1, len(names)), rules=request.rules, via=via)
    return decision, via


def _notify(phone: str, text: str) -> None:
    if phone in ("notify", "approve"):
        client.call({"op": "notify", "text": text}, timeout=8)


def _ask(request: dialogs.Request, site: str, kind: str, names: list[str], hits: list[Hit],
         phone: str, timeout: int) -> tuple[str, str]:
    dialogs.TIMEOUT_S = timeout
    if phone != "approve":
        return dialogs.ask(request), "desktop"
    request.phone = True
    answers: queue.Queue[tuple[str, str]] = queue.Queue()
    close_dialog = threading.Event()
    question = client.PhoneQuestion(cards.guard_card(site, kind, names, hits), timeout)

    def desktop() -> None:
        answers.put(("desktop", dialogs.ask(request, close_dialog)))

    def telegram() -> None:
        reply = question.wait()
        # Only a real tap counts. "Expired", "session off" or "not configured" leave the
        # decision to the desktop dialog.
        if reply and reply[1] in ("allow", "deny"):
            answers.put(("phone", dialogs.CONTINUE if reply[0] == "allow" else dialogs.CANCEL))

    threading.Thread(target=desktop, daemon=True).start()
    threading.Thread(target=telegram, daemon=True).start()
    try:
        via, decision = answers.get(timeout=timeout + 20)
    except queue.Empty:
        via, decision = "desktop", dialogs.CANCEL
    if via == "desktop":
        question.withdraw()
    else:
        close_dialog.set()
    return decision, via


# ------------------------------------------------------------------ requests
def handle(message: dict[str, Any]) -> dict[str, Any]:
    kind = message.get("type")
    site = SITES.get(str(message.get("site", "")).lower(), "this site")

    if kind == "ping":
        cfg = config.load()
        service = client.call({"op": "status", "client": "extension"}, timeout=2)
        return {"ok": True, "version": __version__, "ocr": ocr.available(), "dialog": dialogs.backend_name(),
                "rules": cfg.engine().browser_export(), "service": service is not None,
                "telegram": bool(service and service.get("session_active")),
                "pet": config.pet_for_browser(cfg.settings)}

    if kind == "confirm_prompt":
        text = message.get("text")
        if not isinstance(text, str):
            return {"ok": False, "error": "text missing"}
        cfg = config.load()
        hits = inspector.inspect_prompt(text, cfg.engine())   # re-check here: the agent is the authority
        if not hits:
            return {"ok": True, "found": False, "decision": "allow", "hits": []}
        decision, via = decide(site, "prompt", dialogs.Request("prompt", site, [dialogs.preview(text)]),
                               hits, [], cfg.settings)
        return {"ok": True, "found": True, "decision": decision, "via": via, "hits": [h.to_json() for h in hits]}

    if kind == "inspect_files":
        files = message.get("files")
        if not isinstance(files, list) or not files:
            return {"ok": False, "error": "files missing"}
        cfg = config.load()
        engine = cfg.engine()
        results = []
        for item in files:
            name = str(item.get("name") or "unnamed file")
            mime = str(item.get("mime") or "")
            if item.get("skipped"):                       # e.g. too large to read in the page
                finding = inspector.Finding(name, inspector.classify(name, mime, b""), error=str(item["skipped"]))
                finding.add(engine.scan_filename(name), "file name")
                results.append(finding)
                continue
            try:
                data = base64.b64decode(item.get("data") or "", validate=True)
            except (ValueError, TypeError):
                results.append(inspector.Finding(name, "other", error="file data unreadable"))
                continue
            results.append(inspector.inspect_file(name, data, mime, engine))

        flagged = [r for r in results if r.found]
        unchecked = [r for r in results if not r.found and r.error and r.kind in ("text", "pdf", "image")]
        decision, via = "allow", ""
        if flagged or unchecked:                          # fail closed: an unreadable PDF is the user's call
            lines = [f"{r.name} — {', '.join(r.where)}" for r in flagged]
            lines += [f"{r.name} — not checked ({r.error})" for r in unchecked]
            hits = dedupe([h for r in flagged for h in r.hits])
            names = [r.name for r in flagged + unchecked]
            kinds = sorted({r.kind for r in flagged + unchecked})
            decision, via = decide(site, "file", dialogs.Request("file", site, lines), hits, names, cfg.settings, kinds)
        return {"ok": True, "results": [r.to_json() for r in results], "decision": decision, "via": via}

    if kind in ("ticket", "pair_token"):
        reply = client.call({"op": kind, **{k: v for k, v in message.items() if k not in ("type", "id")}}, timeout=20)
        return reply if reply is not None else {"ok": False, "error": "Walled is not running. Open the Walled app."}

    if kind in ("vault_find", "vault_fill", "vault_save", "vault_status"):
        reply = client.call({"op": kind, **{k: v for k, v in message.items() if k not in ("type", "id")}},
                            timeout=150 if kind == "vault_fill" else 15)
        return reply if reply is not None else {"ok": False, "error": "Walled is not running. Open the Walled app."}

    return {"ok": False, "error": f"unknown request type: {kind!r}"}


def subscribe(stdin: BinaryIO, stdout: BinaryIO, since: int) -> int:
    """Long-lived channel: forward service events to the extension until Chrome disconnects."""
    lock = threading.Lock()
    stop = threading.Event()

    def pump() -> None:
        last = since
        while not stop.is_set():
            reply = client.call({"op": "events", "since": last, "timeout": 25}, timeout=35)
            if reply is None:
                with lock:
                    write_message(stdout, {"type": "service", "online": False})
                stop.wait(5)
                continue
            for event in reply.get("events", []):
                last = max(last, int(event.get("id", 0)))
                with lock:
                    write_message(stdout, {"type": "event", "event": event})
            if not reply.get("events"):
                last = min(last, int(reply.get("last", last)))

    threading.Thread(target=pump, daemon=True).start()
    while stdin.read(1):                                   # Chrome closes stdin when the port closes
        pass
    stop.set()
    return 0


def serve(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    while True:
        try:
            message = read_message(stdin)
        except (ValueError, json.JSONDecodeError) as exc:
            write_message(stdout, {"ok": False, "error": f"bad message: {exc}"})
            return 1
        if message is None:
            return 0
        if message.get("type") == "subscribe":
            since = message.get("since", 0)
            return subscribe(stdin, stdout, since if isinstance(since, int) else 0)
        try:
            reply = handle(message)
        except Exception as exc:                          # fail closed, keep the bridge alive
            reply = {"ok": False, "error": f"internal error: {type(exc).__name__}"}
        if "id" in message:
            reply["id"] = message["id"]
        write_message(stdout, reply)
