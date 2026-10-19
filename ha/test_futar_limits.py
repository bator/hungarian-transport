#!/usr/bin/env python3
"""FUTÁR proxy limits (O-2): parameter allowlist and upstream rate limit.
No network, no Home Assistant."""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "hungarian_transport"
_pkg = types.ModuleType("hungarian_transport")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules["hungarian_transport"] = _pkg
try:
    import aiohttp  # noqa: F401
except ImportError:
    _aiohttp = types.ModuleType("aiohttp")
    _aiohttp.ClientTimeout = lambda **kw: kw
    _aiohttp.ClientError = type("ClientError", (Exception,), {})
    _aiohttp.ClientSession = object
    sys.modules["aiohttp"] = _aiohttp
from hungarian_transport import futar  # noqa: E402

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(("ok" if ok else "FAIL") + f"  {name}" + (f" {detail}" if detail and not ok else ""))
    failed += not ok


calls: list[tuple[str, dict]] = []


async def fake_get(_session, _key, path, params):
    calls.append((path, dict(params)))
    return {"status": "OK", "n": len(calls)}


futar.futar_get = fake_get
clock = [1000.0]
futar._monotonic = lambda: clock[0]


async def main() -> None:
    proxy = futar.FutarProxy(None, "secret")
    await proxy.get("search.json", {"query": "Keleti", "limit": 5, "nonce": "1", "key": "from-browser"})
    check("unknown parameters (and a browser key) do not reach FUTÁR",
          calls[-1] == ("search.json", {"query": "Keleti", "limit": "5"}), repr(calls[-1]))
    n = len(calls)
    for i in range(20):
        await proxy.get("search.json", {"query": "Keleti", "limit": 5, "nonce": str(i)})
    check("made-up parameters do not defeat the shared cache", len(calls) == n, repr(len(calls)))
    await proxy.get("trip-details.json", {"tripId": "BKK_1", "stopId": "x"})
    check("each endpoint keeps only its own parameters", calls[-1] == ("trip-details.json", {"tripId": "BKK_1"}),
          repr(calls[-1]))
    try:
        await proxy.get("search.json", {"query": "x" * (futar.FUTAR_PARAM_MAX_LEN + 1)})
        err = None
    except futar.FutarError as e:
        err = e
    check("an over-long value is refused without a call", err is not None and len(calls) == n + 1, repr(err))
    # What the card sends today (every BkkLib.fetch call in src/lib/*.js).
    card = {
        "arrivals-and-departures-for-stop.json": {"stopId", "minutesAfter", "minutesBefore", "onlyDepartures",
                                                  "includeReferences", "includeVehicleFromTrip"},
        "plan-trip.json": {"fromPlace", "toPlace", "mode", "numItineraries", "searchWindow", "date", "time", "arriveBy"},
        "route-details.json": {"routeId", "includeReferences"},
        "schedule-for-stop.json": {"stopId", "includeReferences"},
        "search.json": {"query"},
        "trip-details.json": {"tripId"},
    }
    check("every allowed endpoint has an allowlist", set(futar.FUTAR_PARAMS) == set(futar.FUTAR_PATHS))
    for path, keys in card.items():
        check(f"{path}: the allowlist keeps every parameter the card sends", keys <= futar.FUTAR_PARAMS[path],
              repr(keys - futar.FUTAR_PARAMS[path]))

    # Rate limit on a virtual clock: a sleep moves the clock, so waiting is measurable.
    async def virtual_sleep(seconds):
        clock[0] += seconds

    futar._sleep = virtual_sleep
    proxy = futar.FutarProxy(None, "secret")
    calls.clear()
    start = clock[0]
    for i in range(futar.FUTAR_BURST + 10):
        await proxy.get("search.json", {"query": f"q{i}"})
    check("over the burst a call waits for a token instead of being refused",
          len(calls) == futar.FUTAR_BURST + 10 and abs((clock[0] - start) - 10 / futar.FUTAR_RATE) < 0.05,
          repr((len(calls), clock[0] - start)))
    hits = len(calls)
    # The last query: still cached whatever the burst is (the cache keeps FUTAR_CACHE_MAX).
    await proxy.get("search.json", {"query": f"q{futar.FUTAR_BURST + 9}"})
    check("cache hits do not need a token", len(calls) == hits)
    clock[0] += 3600
    await proxy.get("search.json", {"query": "after an hour"})
    check("the bucket refills to FUTAR_BURST, not beyond", proxy._tokens == futar.FUTAR_BURST - 1, repr(proxy._tokens))

    # A clock that does not move (nothing refills): refused after FUTAR_TOKEN_WAIT_SEC of sleeping.
    slept: list[float] = []

    async def stuck_sleep(seconds):
        slept.append(seconds)

    futar._sleep = stuck_sleep
    proxy._tokens = 0.0
    proxy._tokens_at = clock[0]
    try:
        await proxy.get("search.json", {"query": "starved"})
        err = None
    except futar.FutarRateLimited as e:
        err = e
    check("a call that cannot get a token in FUTAR_TOKEN_WAIT_SEC is refused",
          err is not None and sum(slept) <= futar.FUTAR_TOKEN_WAIT_SEC and sum(slept) > futar.FUTAR_TOKEN_WAIT_SEC - 0.5,
          repr((err, sum(slept))))
    check("a refusal is not an outage: no unavailable state", proxy._failing is False)
    futar._sleep = asyncio.sleep


async def load_pattern(delay: float, gate: int) -> tuple[int, int]:
    """One destination list on a cold cache (departures.js:361-405): 1 board,
    40 trip-details, 32 route-details, through the card's concurrency gate,
    on the real clock. Returns (refused, sent)."""
    futar._monotonic = time.monotonic
    sent = [0]

    async def slow_get(_session, _key, path, params):
        sent[0] += 1
        await asyncio.sleep(delay)
        return {"status": "OK"}

    futar.futar_get = slow_get
    proxy = futar.FutarProxy(None, "secret")
    jobs = ([("arrivals-and-departures-for-stop.json", {"stopId": "BKK_F1"})]
            + [("trip-details.json", {"tripId": f"BKK_T{i}"}) for i in range(40)]
            + [("route-details.json", {"routeId": f"BKK_R{i}"}) for i in range(32)])
    gate_sem = asyncio.Semaphore(gate)
    refused = [0]

    async def one(path, params):
        async with gate_sem:
            try:
                await proxy.get(path, params)
            except futar.FutarRateLimited:
                refused[0] += 1

    await asyncio.gather(*(one(p, q) for p, q in jobs))
    return refused[0], sent[0]


asyncio.run(main())
for delay, gate in [(0.1, 4), (0.1, 8), (0.1, 12), (0.1, 24), (0.25, 24)]:
    refused, sent = asyncio.run(load_pattern(delay, gate))
    check(f"a 73-call destination list (delay {delay} s, gate {gate}) loads with no refusal",
          refused == 0 and sent == 73, repr((refused, sent)))
if failed:
    sys.exit(1)
print("futar limit tests passed")
