#!/usr/bin/env python3
"""FUTÁR proxy limits (O-2): parameter allowlist and upstream rate limit.
No network, no Home Assistant."""
from __future__ import annotations

import asyncio
import sys
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

    # Rate limit: misses only; a burst, then FUTAR_RATE per second.
    proxy = futar.FutarProxy(None, "secret")
    calls.clear()
    limited = 0
    for i in range(futar.FUTAR_BURST + 10):
        try:
            await proxy.get("search.json", {"query": f"q{i}"})
        except futar.FutarRateLimited:
            limited += 1
    check("a burst above FUTAR_BURST distinct misses is cut", len(calls) == futar.FUTAR_BURST and limited == 10,
          repr((len(calls), limited)))
    hits = len(calls)
    await proxy.get("search.json", {"query": "q0"})
    check("cache hits do not need a token", len(calls) == hits)
    clock[0] += 1.0
    ok = 0
    for i in range(10):
        try:
            await proxy.get("search.json", {"query": f"later{i}"})
            ok += 1
        except futar.FutarRateLimited:
            pass
    check("tokens come back at FUTAR_RATE per second", ok == int(futar.FUTAR_RATE), repr(ok))
    clock[0] += 3600
    await proxy.get("search.json", {"query": "after an hour"})
    check("the bucket refills to FUTAR_BURST, not beyond", proxy._tokens == futar.FUTAR_BURST - 1, repr(proxy._tokens))


asyncio.run(main())
if failed:
    sys.exit(1)
print("futar limit tests passed")
