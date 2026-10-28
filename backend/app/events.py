"""In-process event bus: the server publishes what happens (frames processed, scans finished, agent calls)
and any number of listeners (the laptop dashboard, the phone for guide requests) subscribe.

Publishing never blocks and never raises: a slow listener loses its oldest events instead of stalling a scan.
New subscribers can ask for the last events first, so a dashboard opened mid-scan catches up.
"""
from __future__ import annotations

import asyncio
import itertools
import time
from collections import deque


class Subscription:
    def __init__(self, bus: "EventBus", role: str, maxsize: int):
        self.bus, self.role = bus, role
        self.queue: asyncio.Queue = asyncio.Queue(maxsize)
        self.dropped = 0

    def _put(self, event: dict) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # drop the oldest
            self.dropped += 1
        self.queue.put_nowait(event)

    async def get(self) -> dict:
        return await self.queue.get()

    def close(self) -> None:
        self.bus._subs.discard(self)

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, *exc) -> None:
        self.close()


class EventBus:
    def __init__(self, history: int = 300):
        self._subs: set[Subscription] = set()
        self._history: deque[dict] = deque(maxlen=history)
        self._seq = itertools.count(1)

    def publish(self, event: dict) -> dict:
        """Stamp (`seq`, `ts`) and deliver to every subscriber. Returns the stamped event."""
        stamped = {**event, "seq": next(self._seq), "ts": event.get("ts", time.time())}
        self._history.append(stamped)
        for s in list(self._subs):
            s._put(stamped)
        return stamped

    def subscribe(self, role: str = "dashboard", replay: int = 0, maxsize: int = 500) -> Subscription:
        """`replay` = how many of the most recent events to deliver first."""
        sub = Subscription(self, role, maxsize)
        if replay:
            for e in list(self._history)[-replay:]:
                sub._put(e)
        self._subs.add(sub)
        return sub

    def count(self, role: str) -> int:
        return sum(1 for s in self._subs if s.role == role)

    def history(self, n: int | None = None) -> list[dict]:
        h = list(self._history)
        return h if n is None else h[-n:]
