"""Short-lived cache whose misses share one upstream request per key."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from functools import partial
import time
from typing import Any


class SharedTTLCache:
    """TTL cache in front of slow upstream calls (ELVIRA, FUTÁR).

    Every card on every open dashboard polls the same boards, so a miss starts
    one task that all concurrent callers await. Expired entries are dropped on
    every store, so large responses do not outlive their TTL in memory.
    """

    def __init__(self, ttl: float, max_entries: int) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future] = {}

    def __len__(self) -> int:
        return len(self._data)

    async def get(self, key: Hashable, fetch: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._data.get(key)
        if hit is not None and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(fetch())
            self._inflight[key] = task
            task.add_done_callback(partial(self._store, key))
        # One caller giving up (a card detached) must not cancel the request
        # the other callers are waiting for.
        return await asyncio.shield(task)

    def _store(self, key: Hashable, task: asyncio.Future) -> None:
        self._inflight.pop(key, None)
        if task.cancelled() or task.exception() is not None:
            return
        now = time.monotonic()
        for stale in [k for k, (ts, _) in self._data.items() if now - ts >= self._ttl]:
            del self._data[stale]
        while len(self._data) >= self._max:
            self._data.pop(next(iter(self._data)))
        self._data[key] = (now, task.result())
