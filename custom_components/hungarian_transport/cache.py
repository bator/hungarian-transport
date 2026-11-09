"""Short-lived cache whose misses share one upstream request per key."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Hashable
from datetime import datetime
from functools import partial
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TaskFactory = Callable[[Coroutine[Any, Any, Any]], "asyncio.Future[Any]"]
# call_later(delay, action) runs action after delay seconds; returns the cancel.
CallLater = Callable[[float, Callable[[], None]], Callable[[], None]]


def hass_call_later(hass: HomeAssistant) -> CallLater:
    """The expiry timer inside Home Assistant: cancelled at shutdown, like
    the fetch tasks, instead of a timer left behind."""
    # Imported here: this module and elvira.py load without Home Assistant
    # (ha/test_elvira.py).
    from homeassistant.core import HassJob, callback
    from homeassistant.helpers.event import async_call_later

    def call_later(delay: float, action: Callable[[], None]) -> Callable[[], None]:
        @callback
        def _run(_now: datetime) -> None:
            action()

        return async_call_later(
            hass, delay, HassJob(_run, "hungarian_transport cache expiry", cancel_on_shutdown=True)
        )

    return call_later


class SharedTTLCache:
    """TTL cache in front of slow upstream calls (ELVIRA, FUTÁR).

    Every card on every open dashboard polls the same boards, so a miss starts
    one task that all concurrent callers await. Expired entries are dropped on
    every miss and every store. With ``call_later`` they also go when their
    TTL ends: once the dashboards close nobody asks again, and an ELVIRA
    board is megabytes.

    ``task_factory`` creates the task of a miss; inside Home Assistant it is
    ``hass.async_create_background_task`` so the request is tracked and
    cancelled at shutdown instead of lingering as an untracked future.
    ``call_later`` is ``hass_call_later(hass)`` there for the same reason.
    """

    def __init__(
        self,
        ttl: float,
        max_entries: int,
        task_factory: TaskFactory | None = None,
        call_later: CallLater | None = None,
    ) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future] = {}
        self._task_factory: TaskFactory = task_factory or asyncio.ensure_future
        self._call_later = call_later
        self._cancel_expiry: Callable[[], None] | None = None

    def __len__(self) -> int:
        return len(self._data)

    async def get(self, key: Hashable, fetch: Callable[[], Awaitable[Any]]) -> Any:
        now = time.monotonic()
        hit = self._data.get(key)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        # A miss: the stale entries go now, even if this fetch fails.
        self._purge(now)
        task = self._inflight.get(key)
        if task is None:
            task = self._task_factory(fetch())
            self._inflight[key] = task
            task.add_done_callback(partial(self._store, key))
        # One caller giving up (a card detached) must not cancel the request
        # the other callers are waiting for.
        return await asyncio.shield(task)

    def _purge(self, now: float) -> None:
        for stale in [k for k, (ts, _) in self._data.items() if now - ts >= self._ttl]:
            del self._data[stale]

    def _store(self, key: Hashable, task: asyncio.Future) -> None:
        self._inflight.pop(key, None)
        if task.cancelled() or task.exception() is not None:
            return
        now = time.monotonic()
        self._purge(now)
        while len(self._data) >= self._max:
            self._data.pop(next(iter(self._data)))
        self._data[key] = (now, task.result())
        self._schedule_expiry()

    def _schedule_expiry(self) -> None:
        """One timer at a time, for when the oldest entry expires."""
        if self._call_later is None or self._cancel_expiry is not None or not self._data:
            return
        oldest = min(ts for ts, _ in self._data.values())
        delay = max(0.0, oldest + self._ttl - time.monotonic())
        self._cancel_expiry = self._call_later(delay, self._expire)

    def _expire(self) -> None:
        self._cancel_expiry = None
        self._purge(time.monotonic())
        self._schedule_expiry()
