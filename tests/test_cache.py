"""SharedTTLCache: request sharing, expiry and size cap."""

import asyncio

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
