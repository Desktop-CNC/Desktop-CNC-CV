"Asynchronous publisher and subscriber events sent throught he JSON files (status, analyzer results, etc.)"
from __future__ import annotations
import asyncio
import time
from collections import deque
from typing import Optional


class EventBus:
    """
    Broadcasts dict events to the WebSocket subscribers such as the web views log
    Events are published and received as:
        {"type": "camera.status" | "camera.info" | "analysis" | ..., "camera": "<camera id>", "ts": <unix seconds>, <fields for chosen type>}
    Any changes to this must be updated on the website as well
    """

    def __init__(self, history: int = 200):
        self._subs: set[asyncio.Queue] = set()
        self._history: deque[dict] = deque(maxlen=history)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit_threadsafe(self, event: dict) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self.emit, event)

    def emit(self, event: dict) -> None:
        event.setdefault("ts", time.time())
        self._history.append(event)
        for q in list(self._subs):
            if q.full():  # drop oldest for slow subscribers
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)

    def recent(self, limit: int = 50) -> list[dict]:
        return list(self._history)[-limit:]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
