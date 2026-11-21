"""Optional Telegram Bot API approver for a dedicated, paired private chat.

Telegram is a remote decision channel, not an OS permission broker. No MCP argument
values, request IDs, bot token, or note contents are sent to Telegram.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.request import HTTPRedirectHandler, Request as HttpRequest, build_opener

from .activity import ActivityStore
from .gate import Request
from .telegram_session import ApprovalSession, SessionError

PERMISSION_NOTICE_VERSION = 2
CONNECTOR_READY_NOTICE = (
    "[ALERT] Connector ready\n\n"
    "Guarded MCP handshake completed. No action is pending.\n"
    "This does not confirm Claude restarted."
)
PERMISSION_NOTICE = (
    "[ALERT] Permission limits\n\n"
    "MCP: only requests routed through this gate can be approved here.\n"
    "Claude sharing: decide in Claude.\n"
    "macOS access: decide in System Settings > Privacy & Security.\n\n"
    "This bot cannot detect or grant those prompts. "
    "No permission request is pending; this notice is sent once."
)

LOG = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> None:
        raise TelegramError("Telegram API redirected unexpectedly")


class BotAPI:
    def __init__(self, token: str):
        if not token or any(c.isspace() or c in "/?#" for c in token):
            raise ValueError("A valid MCP_TELEGRAM_BOT_TOKEN is required")
        self._token = token
        self._opener = build_opener(_NoRedirect)

    def call(self, method: str, payload: dict[str, Any], timeout: float = 12) -> object:
        if method not in {"getMe", "getUpdates", "sendMessage", "answerCallbackQuery",
                          "editMessageReplyMarkup", "editMessageText"}:
            raise ValueError("Unsupported Telegram operation")
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        request = HttpRequest(f"https://api.telegram.org/bot{self._token}/{method}",
                              data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(131073)
            if len(raw) > 131072:
                raise TelegramError("Telegram response too large")
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise TelegramError("Telegram API rejected the request")
            return result.get("result")
        except TelegramError:
            raise
        except Exception as exc:
            # Never log exception text: HTTP error messages can include the bot-token URL.
            raise TelegramError("Telegram API unavailable") from exc


ALLOW, ALWAYS, DENY = "allow", "always", "deny"
_CHOICES = {"a:": ALLOW, "b:": DENY, "w:": ALWAYS}


@dataclass
class _Pending:
    message_id: int | None = None
    event: threading.Event = field(default_factory=threading.Event)
    result: str = DENY
    early_choice: tuple[int, str] | None = None
    allow_always: bool = False


def pair_private_chat(token: str, timeout: float = 120,
                      api: BotAPI | None = None, announce: Callable[[str], None] = print) -> tuple[int, int]:
    """Pair only a private message containing a fresh code displayed on this computer."""
    bot = api if api is not None else BotAPI(token)
    bot.call("getMe", {})
    updates = bot.call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]})
    if not isinstance(updates, list):
        raise TelegramError("Invalid Telegram updates")
    offset = max((item["update_id"] for item in updates if isinstance(item, dict)
                  and isinstance(item.get("update_id"), int)), default=-1) + 1
    code = secrets.token_urlsafe(12)
    announce(f"Send /pair {code} to your dedicated bot in a PRIVATE Telegram chat within 2 minutes.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        updates = bot.call("getUpdates", {"offset": offset, "timeout": 5,
                                         "allowed_updates": ["message"]}, timeout=12)
        if not isinstance(updates, list):
            raise TelegramError("Invalid Telegram updates")
        for item in updates:
            if not isinstance(item, dict) or not isinstance(item.get("update_id"), int):
                continue
            offset = max(offset, item["update_id"] + 1)
            message = item.get("message")
            # typed "/pair CODE", or "/start CODE" sent by Telegram when the user taps the
            # t.me/<bot>?start=CODE link shown on the computer
            if not isinstance(message, dict) or message.get("text") not in ("/pair " + code, "/start " + code):
                continue
            chat = message.get("chat")
            sender = message.get("from")
            if (isinstance(chat, dict) and chat.get("type") == "private"
                    and isinstance(chat.get("id"), int) and chat["id"] > 0
                    and isinstance(sender, dict) and isinstance(sender.get("id"), int)
                    and sender["id"] > 0 and sender.get("is_bot") is False):
                return chat["id"], sender["id"]
    raise TelegramError("Pairing timed out")


def pair_alert_chat(token: str, user_id: int, timeout: float = 120,
                    api: BotAPI | None = None, announce: Callable[[str], None] = print) -> int:
    """Discover a second group/channel using a fresh one-time local pairing code."""
    if type(user_id) is not int or user_id <= 0:
        raise ValueError("A paired private Telegram user is required")
    bot = api if api is not None else BotAPI(token)
    bot.call("getMe", {})
    kinds = ["message", "channel_post"]
    updates = bot.call("getUpdates", {"timeout": 0, "allowed_updates": kinds})
    if not isinstance(updates, list):
        raise TelegramError("Invalid Telegram updates")
    offset = max((item["update_id"] for item in updates if isinstance(item, dict)
                  and isinstance(item.get("update_id"), int)), default=-1) + 1
    code = secrets.token_urlsafe(12)
    announce(f"Post /alertpair {code} in your private alert group or channel within 2 minutes. "
             "The bot must be present and able to post there. Stop the guarded connector while pairing.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        updates = bot.call("getUpdates", {"offset": offset, "timeout": 5,
                                          "allowed_updates": kinds}, timeout=12)
        if not isinstance(updates, list):
            raise TelegramError("Invalid Telegram updates")
        for item in updates:
            if not isinstance(item, dict) or not isinstance(item.get("update_id"), int):
                continue
            offset = max(offset, item["update_id"] + 1)
            message = item.get("channel_post") if "channel_post" in item else item.get("message")
            if not isinstance(message, dict):
                continue
            text = message.get("text")
            # typed "/alertpair CODE", or what Telegram posts after the t.me/<bot>?startgroup=CODE link
            if not isinstance(text, str) or not (text == "/alertpair " + code or
                                                 re.fullmatch(r"/start(@\w+)? " + re.escape(code), text)):
                continue
            chat = message.get("chat")
            sender = message.get("from")
            if not (isinstance(chat, dict) and chat.get("type") in {"group", "supergroup", "channel"}
                    and type(chat.get("id")) is int and chat["id"] < 0):
                continue
            if chat["type"] != "channel" and not (isinstance(sender, dict)
                    and sender.get("id") == user_id and sender.get("is_bot") is False):
                continue
            if chat["type"] == "channel" and "channel_post" not in item:
                continue
            reply = bot.call("sendMessage", {"chat_id": chat["id"],
                                             "text": "[ALERT] Channel test passed\n\nBot can post here. "
                                                     "Alerts move here only after local configuration.\n"
                                                     "No action is pending.",
                                             "protect_content": True}, timeout=10)
            if not isinstance(reply, dict) or not isinstance(reply.get("message_id"), int):
                raise TelegramError("Cannot deliver alerts to the selected group/channel")
            return int(chat["id"])
    raise TelegramError("Alert channel pairing timed out")


class TelegramApprover:
    def __init__(self, token: str, chat_id: int, user_id: int, timeout: float = 60,
                 api: BotAPI | None = None, stop_event: threading.Event | None = None,
                 alert_chat_id: int | None = None,
                 session: ApprovalSession | None = None):
        if (type(chat_id) is not int or chat_id <= 0
                or type(user_id) is not int or user_id <= 0):
            raise ValueError("Telegram requires a positive private chat ID and user ID")
        if (alert_chat_id is not None and (type(alert_chat_id) is not int
                or alert_chat_id >= 0)):
            raise ValueError("Telegram alert channel requires a negative numeric group/channel chat ID")
        self.chat_id = chat_id
        self.user_id = user_id
        self.alert_chat_id = alert_chat_id if alert_chat_id is not None else chat_id
        self.session = session
        self.session_failed = False
        self.timeout = timeout
        self.api = api if api is not None else BotAPI(token)
        self.stop_event = stop_event or threading.Event()
        self.pending: dict[str, _Pending] = {}
        self.lock = threading.Lock()
        self.healthy = False
        self.offset = 0
        self.worker: threading.Thread | None = None
        self.alerts: queue.Queue[str] = queue.Queue(maxsize=64)
        self.alert_worker: threading.Thread | None = None
        # Optional handler for paired-user commands such as /status; returns reply text.
        self.on_command: Callable[[str], str | None] | None = None
        # Optional handler for messages that a local CLI is waiting for (session renewal).
        self.on_private_text: Callable[[str], str | None] | None = None
        self.on_pairout: Callable[[], None] | None = None

    def start(self) -> None:
        # Fail before starting the MCP server if credentials or polling are unavailable.
        self.api.call("getMe", {})
        for _ in range(10):
            updates = self.api.call("getUpdates", {"offset": self.offset, "timeout": 0, "limit": 100,
                                                     "allowed_updates": ["callback_query", "message"]})
            if not isinstance(updates, list) or len(updates) > 100:
                raise TelegramError("Invalid Telegram updates")
            for update in updates:
                if not isinstance(update, dict) or type(update.get("update_id")) is not int:
                    raise TelegramError("Invalid Telegram update ID")
                self._process_message(update.get("message"))
                self.offset = max(self.offset, update["update_id"] + 1)
            if len(updates) < 100:
                break
        else:
            raise TelegramError("Telegram backlog not fully checked; approvals unavailable")
        self.healthy = True
        self.worker = threading.Thread(target=self._poll, name="telegram-decisions", daemon=True)
        self.worker.start()
        if self.alert_chat_id != self.chat_id:
            self.alert_worker = threading.Thread(target=self._deliver_alerts, name="telegram-alerts", daemon=True)
            self.alert_worker.start()

    def notify_decision(self, request: Request, allowed: bool) -> None:
        if self.alert_worker is None or self.stop_event.is_set():
            return
        tool = request.parameters.get("name") if request.method == "tools/call" and isinstance(request.parameters, dict) else None
        text = (f"[ALERT] Action {'allowed' if allowed else 'blocked'}\n\n"
                f"Tool: {json.dumps(tool[:256] if isinstance(tool, str) else '(unspecified)', ensure_ascii=True)}\n"
                f"Server: {json.dumps(request.server[:256], ensure_ascii=True)}\n"
                f"Method: {json.dumps(request.method[:256], ensure_ascii=True)}\n\n"
                + ("Approved for forwarding; execution is not confirmed." if allowed else
                   "Request stopped. It may have been declined or approval was unavailable."))
        try:
            self.alerts.put_nowait(text)
        except queue.Full:
            LOG.warning("Telegram decision alert queue full; outcome remains in local history")

    def _deliver_alerts(self) -> None:
        while not self.stop_event.is_set():
            try:
                text = self.alerts.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                reply = self.api.call("sendMessage", {"chat_id": self.alert_chat_id,
                    "text": text, "protect_content": True}, timeout=5)
                if not isinstance(reply, dict) or not isinstance(reply.get("message_id"), int):
                    raise TelegramError("Telegram decision alert not delivered")
            except TelegramError:
                LOG.warning("Telegram decision alert unavailable; outcome remains in local history")

    def notify_permission_boundary(self, history: ActivityStore) -> bool:
        """Tell a paired recipient once what the integration cannot observe or authorize."""
        recipient = f"{self.alert_chat_id}:{self.user_id}"
        if history.notice_sent("telegram-permissions", recipient, PERMISSION_NOTICE_VERSION):
            return False
        if not self.healthy or self.stop_event.is_set():
            raise TelegramError("Telegram approval unavailable")
        reply = self.api.call("sendMessage", {"chat_id": self.alert_chat_id,
                                              "text": PERMISSION_NOTICE,
                                              "protect_content": True}, timeout=10)
        if not isinstance(reply, dict) or not isinstance(reply.get("message_id"), int):
            raise TelegramError("Telegram permission notice could not be delivered")
        history.mark_notice_sent("telegram-permissions", recipient, PERMISSION_NOTICE_VERSION)
        return True

    def notify_connector_ready(self) -> None:
        """Announce one observed successful stdio handshake, without asserting an app restart."""
        if not self.healthy or self.stop_event.is_set():
            raise TelegramError("Telegram approval unavailable")
        reply = self.api.call("sendMessage", {"chat_id": self.alert_chat_id,
                                              "text": CONNECTOR_READY_NOTICE,
                                              "protect_content": True}, timeout=10)
        if not isinstance(reply, dict) or not isinstance(reply.get("message_id"), int):
            raise TelegramError("Telegram connector alert could not be delivered")

    def _poll(self) -> None:
        while not self.stop_event.is_set():
            try:
                updates = self.api.call("getUpdates", {"offset": self.offset, "timeout": 5,
                                                       "allowed_updates": ["callback_query", "message"]}, timeout=12)
                if not isinstance(updates, list):
                    raise TelegramError("Invalid Telegram updates")
                for update in updates:
                    if isinstance(update, dict) and isinstance(update.get("update_id"), int):
                        self.offset = max(self.offset, update["update_id"] + 1)
                        self._process_message(update.get("message"))
                        self._process(update.get("callback_query"))
                self.healthy = True
            except Exception:
                LOG.warning("Telegram polling unavailable; pending requests denied")
                with self.lock:
                    self.healthy = False
                    for pending in self.pending.values():
                        pending.result = DENY
                        pending.event.set()
                self.stop_event.wait(3)

    def _authorized(self) -> bool:
        return self.session is None or self.session.active()

    def _from_paired_user(self, message: dict[str, Any]) -> bool:
        chat = message.get("chat")
        sender = message.get("from")
        return (isinstance(chat, dict) and chat.get("id") == self.chat_id
                and chat.get("type") == "private" and isinstance(sender, dict)
                and sender.get("id") == self.user_id and sender.get("is_bot") is False)

    def _reply(self, text: str) -> None:
        try:
            self.api.call("sendMessage", {"chat_id": self.chat_id, "text": text[:4000],
                                          "protect_content": True}, timeout=10)
        except TelegramError:
            LOG.warning("Telegram command reply unavailable")

    def _process_message(self, message: object) -> None:
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            return
        text = message["text"].strip()
        if text != "/pairout":
            if text.startswith("/") and self._from_paired_user(message):
                for handler in (self.on_private_text, self.on_command):
                    if handler is None:
                        continue
                    try:
                        reply = handler(text)
                    except Exception:
                        LOG.warning("Telegram command handler failed")
                        reply = None
                    if reply:
                        self._reply(reply)
                        return
            return
        if self.session is None or not self._from_paired_user(message):
            return
        try:
            self.session.revoke()
        except SessionError:
            LOG.error("Could not persist Telegram pairout; approvals disabled in this process")
            self.session_failed = True
        with self.lock:
            for pending in self.pending.values():
                pending.result = DENY
                pending.event.set()
        if self.on_pairout is not None:
            try:
                self.on_pairout()
            except Exception:
                LOG.warning("pairout handler failed")
        try:
            self.api.call("sendMessage", {"chat_id": self.chat_id,
                "text": "[SESSION] Approvals off\n\nPending actions blocked. "
                        "Alerts stay on. Pair again locally to allow actions.",
                "protect_content": True}, timeout=10)
        except TelegramError:
            LOG.warning("Telegram pairout acknowledgement unavailable")

    def _process(self, callback: object) -> None:
        if not isinstance(callback, dict) or self.session_failed or not self._authorized():
            return
        data = callback.get("data")
        sender = callback.get("from")
        message = callback.get("message")
        if not (isinstance(data, str) and len(data) == 34 and data[:2] in _CHOICES
                and isinstance(sender, dict) and sender.get("id") == self.user_id
                and isinstance(message, dict) and isinstance(message.get("chat"), dict)
                and message["chat"].get("id") == self.chat_id
                and message["chat"].get("type") == "private"):
            return
        choice = _CHOICES[data[:2]]
        with self.lock:
            pending = self.pending.get(data[2:])
            if pending is None or pending.event.is_set() or not isinstance(message.get("message_id"), int):
                return
            if choice == ALWAYS and not pending.allow_always:
                return
            if pending.message_id is None:
                pending.early_choice = (message["message_id"], choice)
            elif pending.message_id != message["message_id"]:
                return
            else:
                pending.result = choice
                pending.event.set()
        try:
            self.api.call("answerCallbackQuery", {"callback_query_id": callback["id"],
                                                  "text": "Decision received"}, timeout=5)
        except (TelegramError, KeyError):
            pass

    def decide(self, request: Request) -> bool:
        """Legacy MCP approval: metadata only, argument values never leave the machine."""
        if (self.stop_event.is_set() or not self.healthy or self.session_failed
                or not self._authorized() or (request.cancelled and request.cancelled.is_set())):
            return False
        tool = request.parameters.get("name") if request.method == "tools/call" and isinstance(request.parameters, dict) else None
        tool_label = tool if isinstance(tool, str) else "(unspecified)"
        inputs = (request.parameters.get("arguments") if request.method == "tools/call"
                  and isinstance(request.parameters, dict) else request.parameters)
        text = ("[APPROVAL] Action waiting\n\n"
                f"Tool: {json.dumps(tool_label, ensure_ascii=True)}\n"
                f"Server: {json.dumps(request.server, ensure_ascii=True)}\n"
                f"Method: {json.dumps(request.method, ensure_ascii=True)}\n"
                f"Inputs: {'hidden' if inputs else 'none'}\n\n"
                "Allow sends the full request. Block stops it.\n"
                f"Expires in {self.timeout:g}s. Claude/macOS permissions are separate.")
        return self.ask(text, request.cancelled) == ALLOW

    def ask(self, text: str, cancelled: threading.Event | None = None, *,
            allow_always: bool = False, always_label: str = "Always allow",
            parse_mode: str | None = None, timeout: float | None = None,
            outcome_text: Callable[[str], str] | None = None) -> str:
        """Send one Allow/Block prompt to the paired private chat and wait for the tap.

        Returns ALLOW, ALWAYS (only when ``allow_always``) or DENY. Every failure path,
        timeout, cancellation, revocation or shutdown returns DENY (fail closed).
        """
        timeout = self.timeout if timeout is None else timeout
        if (self.stop_event.is_set() or not self.healthy or self.session_failed
                or not self._authorized() or (cancelled and cancelled.is_set())):
            return DENY
        if len(text) > 4096:
            return DENY
        nonce = secrets.token_hex(16)
        pending = _Pending(allow_always=allow_always)
        with self.lock:
            if not self.healthy or self.stop_event.is_set() or self.session_failed or not self._authorized():
                return DENY
            self.pending[nonce] = pending
        keyboard = [[{"text": "Block", "callback_data": "b:" + nonce},
                     {"text": "Allow", "callback_data": "a:" + nonce}]]
        if allow_always:
            keyboard.append([{"text": always_label[:60], "callback_data": "w:" + nonce}])
        payload = {"chat_id": self.chat_id, "text": text, "protect_content": True,
                   "reply_markup": {"inline_keyboard": keyboard}}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        outcome = DENY
        reason = "expired"
        try:
            try:
                reply = self.api.call("sendMessage", payload, timeout=10)
            except TelegramError:
                LOG.warning("Cannot deliver approval request; denied")
                return DENY
            if not isinstance(reply, dict) or not isinstance(reply.get("message_id"), int):
                return DENY
            with self.lock:
                pending.message_id = reply["message_id"]
                if pending.early_choice and pending.early_choice[0] == pending.message_id and not pending.event.is_set():
                    pending.result = pending.early_choice[1]
                    pending.event.set()
                pending.early_choice = None
            deadline = time.monotonic() + timeout
            while not self.stop_event.is_set() and not (cancelled and cancelled.is_set()):
                if self.session_failed or not self._authorized():
                    reason = "session ended"
                    return DENY
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if pending.event.wait(min(remaining, 0.05)):
                    ok = (self.healthy and not self.session_failed and self._authorized()
                          and not self.stop_event.is_set()
                          and not (cancelled and cancelled.is_set()))
                    outcome = pending.result if ok else DENY
                    reason = outcome if ok else "approval unavailable"
                    return outcome
            if cancelled and cancelled.is_set():
                reason = "cancelled"
            elif self.stop_event.is_set():
                reason = "shutdown"
            return DENY
        finally:
            with self.lock:
                self.pending.pop(nonce, None)
            if pending.message_id is not None:
                self._finish_message(pending.message_id, text, parse_mode, reason, outcome_text)

    def _finish_message(self, message_id: int, text: str, parse_mode: str | None,
                        reason: str, outcome_text: Callable[[str], str] | None) -> None:
        """Remove buttons; when a formatter is given also stamp the outcome on the prompt."""
        try:
            if outcome_text is None:
                self.api.call("editMessageReplyMarkup", {"chat_id": self.chat_id,
                    "message_id": message_id, "reply_markup": {"inline_keyboard": []}}, timeout=5)
                return
            payload = {"chat_id": self.chat_id, "message_id": message_id,
                       "text": (text + "\n\n" + outcome_text(reason))[:4096],
                       "reply_markup": {"inline_keyboard": []}}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            self.api.call("editMessageText", payload, timeout=5)
        except TelegramError:
            pass

    def send_notice(self, text: str, parse_mode: str | None = None) -> bool:
        """Best-effort informational message to the paired private chat (no buttons)."""
        payload = {"chat_id": self.chat_id, "text": text[:4096], "protect_content": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            reply = self.api.call("sendMessage", payload, timeout=10)
            return isinstance(reply, dict) and isinstance(reply.get("message_id"), int)
        except TelegramError:
            return False

    def stop(self) -> None:
        self.stop_event.set()
        with self.lock:
            self.healthy = False
            for pending in self.pending.values():
                pending.result = DENY
                pending.event.set()
        if self.worker and threading.current_thread() is not self.worker:
            self.worker.join(timeout=13)
        if self.alert_worker and threading.current_thread() is not self.alert_worker:
            self.alert_worker.join(timeout=6)
