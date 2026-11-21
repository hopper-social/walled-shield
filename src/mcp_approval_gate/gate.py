"""Fail-closed, per-request approval shared by the stdio and HTTP transports.

The gate sees every client→server JSON-RPC message. Requests that only set up the session or
list capabilities (``CONTROL_METHODS``) pass automatically unless ``strict`` is on; every other
request is held until ``decide`` returns. Any exception, cancellation, full queue or audit
failure denies the request with JSON-RPC error ``-32001``. Batches are never forwarded.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .activity import ActivityStore

MAX_BODY = 1024 * 1024
DENIED = -32001
# These methods establish a session or enumerate capabilities; none invokes a tool or reads a resource.
CONTROL_METHODS = frozenset({
    "initialize", "ping", "tools/list", "resources/list", "resources/templates/list", "prompts/list",
})
LOG = logging.getLogger(__name__)

JSONObject = Dict[str, Any]
#: What the gate returns for each direction: nothing, one message, or a list (batch denials).
Shaped = Optional[Union[JSONObject, List[JSONObject]]]


@dataclass(frozen=True)
class Request:
    server: str
    method: str
    parameters: object
    request_id: object
    cancelled: threading.Event | None = field(default=None, compare=False, repr=False)


def error_response(request_id: object, message: str) -> JSONObject:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": DENIED, "message": message}}


def valid_request_id(value: object) -> bool:
    return (isinstance(value, (str, int)) and not isinstance(value, bool)) or (
        isinstance(value, float) and math.isfinite(value))


def decode_message(data: bytes | str) -> object:
    def unique_members(pairs: list[tuple[str, Any]]) -> JSONObject:
        result: JSONObject = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        raise ValueError("invalid JSON constant")

    def bounded_integer(value: str) -> int:
        if len(value) > 1024:
            raise ValueError("JSON integer too large")
        return int(value)

    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON number")
        return result

    def valid_unicode(value: object) -> None:
        if isinstance(value, str):
            if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("unpaired Unicode surrogate")
        elif isinstance(value, dict):
            for key, item in value.items():
                valid_unicode(key)
                valid_unicode(item)
        elif isinstance(value, list):
            for item in value:
                valid_unicode(item)

    try:
        message = json.loads(data, object_pairs_hook=unique_members,
                             parse_constant=reject_constant, parse_float=finite_float,
                             parse_int=bounded_integer)
        valid_unicode(message)
    except RecursionError as exc:
        raise ValueError("JSON nesting too deep") from exc
    return message


def requests_in(message: object, server: str,
                cancelled: threading.Event | None = None) -> list[Request]:
    """Select client JSON-RPC requests, not responses or notifications."""
    messages = message if isinstance(message, list) else [message]
    result: list[Request] = []
    for item in messages:
        if (isinstance(item, dict) and isinstance(item.get("method"), str)
                and "id" in item and valid_request_id(item["id"])):
            result.append(Request(server, item["method"], item.get("params", {}), item["id"], cancelled))
    return result


class ApprovalGate:
    def __init__(self, decide: Callable[[Request], bool], max_pending: int = 32,
                 strict: bool = False, activity: ActivityStore | None = None,
                 on_decision: Callable[[Request, bool], None] | None = None) -> None:
        self.decide = decide
        self.strict = strict
        self.activity = activity
        self.on_decision = on_decision
        self.slots = threading.BoundedSemaphore(max_pending)

    def _audit(self, request: Request, outcome: str) -> bool:
        # Keep the audit useful without recording arguments, request IDs, or bearer tokens.
        tool = request.parameters.get("name") if request.method == "tools/call" and isinstance(request.parameters, dict) else None
        fields = {"server": request.server[:256], "method": request.method[:256], "outcome": outcome}
        if isinstance(tool, str):
            fields["tool"] = tool[:256]
        if self.activity is not None:
            try:
                self.activity.record(fields["server"], fields["method"], fields.get("tool"), outcome)
            except Exception:
                LOG.error("Activity history unavailable; request denied")
                return False
        LOG.info("MCP audit %s", json.dumps(fields, ensure_ascii=True))
        return True

    def evaluate(self, message: object, server: str,
                 cancelled: threading.Event | None = None) -> Tuple[Any, Shaped]:
        """Return (forwarded message, local denial); never forward batches."""
        if isinstance(message, list):
            denials = [error_response(item["id"] if valid_request_id(item["id"]) else None,
                                      "JSON-RPC batches are unsupported")
                       for item in message if isinstance(item, dict) and "id" in item]
            return None, denials or error_response(None, "JSON-RPC batches are unsupported")
        items = [message]
        forwarded: list[Any] = []  # client messages, forwarded verbatim
        denied: list[JSONObject] = []
        for item in items:
            if isinstance(item, dict) and "method" in item:
                method = item["method"]
                if not isinstance(method, str) or not valid_request_id(item.get("id")):
                    if isinstance(method, str) and method.startswith("notifications/") and "id" not in item:
                        forwarded.append(item)
                    elif "id" in item:
                        request_id = item["id"] if valid_request_id(item["id"]) else None
                        denied.append(error_response(request_id, "Invalid MCP request"))
                    continue
            requests = requests_in(item, server, cancelled)
            if not requests:
                forwarded.append(item)
                continue
            request = requests[0]
            allowed = False
            automatic = not self.strict and request.method in CONTROL_METHODS
            if cancelled is None or not cancelled.is_set():
                if automatic:
                    allowed = True
                elif self.slots.acquire(blocking=False):
                    try:
                        allowed = bool(self.decide(request))
                    except Exception:
                        allowed = False
                    finally:
                        self.slots.release()
            if allowed and (cancelled is None or not cancelled.is_set()):
                allowed = self._audit(request, "auto_forwarded" if automatic else "allowed")
            else:
                self._audit(request, "blocked")
            allowed = allowed and (cancelled is None or not cancelled.is_set())
            if self.on_decision is not None and not automatic:
                try:
                    self.on_decision(request, allowed)
                except Exception:
                    LOG.warning("Decision alert unavailable")
            if allowed:
                forwarded.append(item)
            else:
                denied.append(error_response(request.request_id, "MCP request blocked or approval unavailable"))
        def shape(items: list[Any]) -> Any:
            if not items:
                return None
            return items if isinstance(message, list) else items[0]

        return shape(forwarded), shape(denied)


def compact_request(request: Request) -> str:
    return json.dumps(request.parameters, ensure_ascii=True, indent=2, default=str)
