"""SharedTTLCache: request sharing, expiry and size cap."""

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HassJob
from homeassistant.util import dt as dt_util
from homeassistant.util.async_ import get_scheduled_timer_handles

from custom_components.hungarian_transport import cache as cache_mod
from custom_components.hungarian_transport.cache import SharedTTLCache


async def test_concurrent_misses_share_one_fetch() -> None:
    cache = SharedTTLCache(ttl=10, max_entries=8)
    calls = 0
    gate = asyncio.Event()

    async def fetch():
        nonlocal calls
        calls += 1
        await gate.wait()
        return {"n": calls}

    waiters = [asyncio.ensure_future(cache.get("k", fetch)) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*waiters)

    assert calls == 1
    assert results == [{"n": 1}] * 3


async def test_expired_entries_are_dropped_on_store(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: clock[0])
    cache = SharedTTLCache(ttl=15, max_entries=100)

    async def value(v):
        return v

    await cache.get("old", lambda: value("big payload"))
    clock[0] += 60
    await cache.get("new", lambda: value("fresh"))

    assert len(cache) == 1


async def test_an_expired_entry_is_dropped_when_read(monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: clock[0])
    cache = SharedTTLCache(ttl=15, max_entries=100)

    async def value(v):
        return v

    await cache.get("old", lambda: value("big payload"))
    await cache.get("other", lambda: value("big payload"))
    clock[0] += 60

    async def boom():
        raise RuntimeError("upstream down")

    # A miss whose fetch fails stores nothing, but the stale boards go.
    with pytest.raises(RuntimeError):
        await cache.get("old", boom)
    assert len(cache) == 0


async def test_expired_entries_go_without_another_call(monkeypatch) -> None:
    """Nobody polls once the dashboards close: the last boards are dropped
    when their TTL ends, not at some later store."""
    clock = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: clock[0])
    timers: list[tuple[float, object]] = []

    def call_later(delay, action):
        timers.append((delay, action))
        return lambda: timers.remove((delay, action))

    cache = SharedTTLCache(ttl=15, max_entries=100, call_later=call_later)

    async def value(v):
        return v

    await cache.get("a", lambda: value("big payload"))
    clock[0] += 5
    await cache.get("b", lambda: value("big payload"))
    # One timer at a time, for the oldest entry.
    assert [delay for delay, _ in timers] == [15]

    clock[0] += 10
    timers.pop()[1]()
    assert len(cache) == 1
    assert [delay for delay, _ in timers] == [5]

    clock[0] += 5
    timers.pop()[1]()
    assert len(cache) == 0
    assert timers == []


def expiry_timers(hass) -> list:
    """The cache's expiry timers still scheduled on Home Assistant's loop."""
    return [
        handle
        for handle in get_scheduled_timer_handles(hass.loop)
        if not handle.cancelled()
        and handle._args
        and isinstance(job := handle._args[-1], HassJob)
        and job.name == "hungarian_transport cache expiry"
    ]


async def test_hass_expiry_timer_purges_the_cache(hass, monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(cache_mod.time, "monotonic", lambda: clock[0])
    cache = SharedTTLCache(ttl=15, max_entries=8, call_later=cache_mod.hass_call_later(hass))

    async def value(v):
        return v

    await cache.get("k", lambda: value("v"))
    assert len(expiry_timers(hass)) == 1

    clock[0] += 15
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=15), fire_all=True)
    await hass.async_block_till_done()
    assert len(cache) == 0
    assert expiry_timers(hass) == []


async def test_hass_expiry_timer_is_cancelled_when_home_assistant_stops(hass) -> None:
    """Home Assistant does not cancel an async_call_later timer at shutdown
    (a HassJob's cancel_on_shutdown notwithstanding): close_on_stop does,
    and no timer comes back for a fetch that ends after it."""
    cache = SharedTTLCache(ttl=15, max_entries=8, call_later=cache_mod.hass_call_later(hass))
    cache_mod.close_on_stop(hass, cache.close)
    gate = asyncio.Event()

    async def value(v):
        await gate.wait()
        return v

    gate.set()
    await cache.get("k", lambda: value("v"))
    assert len(expiry_timers(hass)) == 1

    gate.clear()
    late = asyncio.ensure_future(cache.get("late", lambda: value("v")))
    await asyncio.sleep(0)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert expiry_timers(hass) == []

    gate.set()
    assert await late == "v"
    assert expiry_timers(hass) == []


async def test_closed_cache_still_answers_without_a_timer() -> None:
    timers: list[object] = []

    def call_later(delay, action):
        timers.append(action)
        return lambda: timers.remove(action)

    cache = SharedTTLCache(ttl=15, max_entries=8, call_later=call_later)

    async def value(v):
        return v

    await cache.get("a", lambda: value("a"))
    assert len(timers) == 1
    cache.close()
    assert timers == []
    assert await cache.get("b", lambda: value("b")) == "b"
    assert timers == []


async def test_size_cap_and_errors_are_not_cached() -> None:
    cache = SharedTTLCache(ttl=60, max_entries=2)

    async def value(v):
        return v

    for key in ("a", "b", "c"):
        await cache.get(key, lambda key=key: value(key))
    assert len(cache) == 2

    async def boom():
        raise RuntimeError("upstream down")

    try:
        await cache.get("err", boom)
    except RuntimeError:
        pass
    assert len(cache) == 2
    assert await cache.get("err", lambda: value("ok")) == "ok"


async def test_task_factory_owns_the_fetch_tasks() -> None:
    """Home Assistant passes async_create_background_task so the request is
    tracked (and cancelled at shutdown) instead of an untracked future."""
    created: list[asyncio.Task] = []

    def factory(coro):
        task = asyncio.get_running_loop().create_task(coro, name="tracked")
        created.append(task)
        return task

    cache = SharedTTLCache(ttl=10, max_entries=8, task_factory=factory)

    async def value(v):
        return v

    assert await cache.get("k", lambda: value("v")) == "v"
    assert await cache.get("k", lambda: value("again")) == "v"
    assert len(created) == 1 and created[0].get_name() == "tracked"
