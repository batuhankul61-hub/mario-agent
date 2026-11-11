"""Things Mario should say without being asked (a reminder is due, a new mail,
a meeting is starting, the battery is low). The scheduler pushes them here and
the browser polls GET /api/events; a running voice session hands each one to
the model as a system note, otherwise the page shows a notification."""

import itertools
import threading
import time
from collections import deque
from functools import lru_cache

KEEP = 100


class Events:
    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self._items: deque[dict] = deque(maxlen=KEEP)
        self._lock = threading.Lock()

    def push(self, kind: str, message: str, **data) -> dict:
        with self._lock:
            event = {"seq": next(self._seq), "kind": kind, "message": message, "time": time.time()}
            event.update(data)
            self._items.append(event)
            return event

    @property
    def last(self) -> int:
        with self._lock:
            return self._items[-1]["seq"] if self._items else 0

    def after(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self._items if e["seq"] > seq]


@lru_cache
def get_events() -> Events:
    return Events()
