"""A small in-memory event bus: the glue between the service, the app, the extension and Telegram.

Every part publishes what just happened ("Telegram paired", "Prompt stopped on ChatGPT",
"Vault locked", "Test passed"). The app polls it, the extension listens through a long-lived
native-messaging channel, and selected events go to the Telegram activity channel. Events
carry titles and masked details only, never prompt text, file contents or passwords.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

MAX_EVENTS = 300


@dataclass
class Event:
    type: str                         # e.g. "telegram.paired", "guard.decision", "vault.locked", "test.passed"
    title: str
    text: str = ""
    tone: str = "info"                # info | ok | warn | bad
    data: dict[str, Any] = field(default_factory=dict)
    id: int = 0
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class EventBus:
    def __init__(self) -> None:
        self._events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self._ids = itertools.count(1)
        self._cond = threading.Condition()

    def publish(self, type: str, title: str, text: str = "", tone: str = "info", **data: Any) -> Event:
        event = Event(type, title[:200], text[:600], tone, data)
        with self._cond:
            event.id = next(self._ids)
            self._events.append(event)
            self._cond.notify_all()
        return event

    def last_id(self) -> int:
        with self._cond:
            return self._events[-1].id if self._events else 0

    def since(self, after: int, timeout: float = 0.0) -> list[Event]:
        """Events with id > ``after``; waits up to ``timeout`` seconds for the first one."""
        deadline = time.monotonic() + max(0.0, min(timeout, 55.0))
        with self._cond:
            last = self._events[-1].id if self._events else 0
            if after > last:                    # the service restarted: everything it has is new to this client
                after = 0
            while True:
                found = [e for e in self._events if e.id > after]
                remaining = deadline - time.monotonic()
                if found or remaining <= 0:
                    return found
                self._cond.wait(remaining)
