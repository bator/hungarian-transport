"""Short-lived cache whose misses share one upstream request per key."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Hashable
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from . import upstream

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

TaskFactory = Callable[[Coroutine[Any, Any, Any]], "asyncio.Future[Any]"]
# call_later(delay, action) runs action after delay seconds; returns the cancel.
CallLater = Callable[[float, Callable[[], None]], Callable[[], None]]


def hass_call_later(hass: HomeAssistant) -> CallLater:
    """The expiry timer inside Home Assistant. Home Assistant does not cancel
    an async_call_later timer at shutdown (a HassJob's cancel_on_shutdown
    covers only timers that hand the job to the loop themselves), so the
    cache's owner closes the cache then: close_on_stop."""
    # Imported here: this module and elvira.py load without Home Assistant
    # (their tests drive them without it).
    from homeassistant.core import HassJob, callback
    from homeassistant.helpers.event import async_call_later

    def call_later(delay: float, action: Callable[[], None]) -> Callable[[], None]:
        @callback
        def _run(_now: datetime) -> None:
            action()

        return async_call_later(hass, delay, HassJob(_run, "hungarian_transport cache expiry"))

    return call_later


def close_on_stop(hass: HomeAssistant, close: Callable[[], None]) -> Callable[[], None]:
    """Call close (SharedTTLCache.close) when Home Assistant stops; returns
    the listener's remove, for an owner that goes before that (an entry
    unloaded)."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.core import Event, callback

    @callback
    def _stop(_event: Event) -> None:
        close()

    return hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, _stop)


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
    ``call_later`` is ``hass_call_later(hass)`` there, and ``close`` cancels
    its timer for good when Home Assistant stops or the owner unloads.
    ``monotonic`` is the clock of the TTL (the owner's, upstream.monotonic
    by default).
    """

    def __init__(
        self,
        ttl: float,
        max_entries: int,
        task_factory: TaskFactory | None = None,
        call_later: CallLater | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._inflight: dict[Hashable, asyncio.Future] = {}
        self._task_factory: TaskFactory = task_factory or asyncio.ensure_future
        self._call_later = call_later
        self._cancel_expiry: Callable[[], None] | None = None
        self._closed = False
        self._monotonic = monotonic or upstream.monotonic

    def __len__(self) -> int:
        return len(self._data)

    async def get(self, key: Hashable, fetch: Callable[[], Awaitable[Any]]) -> Any:
        now = self._monotonic()
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
        now = self._monotonic()
        self._purge(now)
        while len(self._data) >= self._max:
            self._data.pop(next(iter(self._data)))
        self._data[key] = (now, task.result())
        self._schedule_expiry()

    def close(self) -> None:
        """No expiry timer from now on: the pending one is cancelled, and a
        fetch that ends later (shutdown cancels it, or it is past that) does
        not start another. The cache still answers; stale entries then go
        on the next miss or store, as without ``call_later``."""
        self._closed = True
        if self._cancel_expiry is not None:
            self._cancel_expiry()
            self._cancel_expiry = None

    def _schedule_expiry(self) -> None:
        """One timer at a time, for when the oldest entry expires."""
        if (
            self._call_later is None
            or self._closed
            or self._cancel_expiry is not None
            or not self._data
        ):
            return
        oldest = min(ts for ts, _ in self._data.values())
        delay = max(0.0, oldest + self._ttl - self._monotonic())
        self._cancel_expiry = self._call_later(delay, self._expire)

    def _expire(self) -> None:
        self._cancel_expiry = None
        self._purge(self._monotonic())
        self._schedule_expiry()
