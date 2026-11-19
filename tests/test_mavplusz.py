"""mavplusz.hu, the first source of the feeds with "mavplusz": true.

fixtures/realtime/mavplusz_trips.json is a real planner answer of
2026-11-14 07:2x CET for four trips (stoptimes trimmed): t0 and t1 run with
realtime data (t1's pattern has a second vehicle, on another trip), t2 is
asked for a later date (no realtime; its pattern's vehicle is today's run),
t3 does not exist. The canceled and skipped cases are that answer edited.
fixtures/realtime/mavplusz_routes.json (2026-11-14 07:4x CET) lists the
trips of Veszprém line 2 and Miskolc lines 32 and 14 that day (14 has two
trips at 07:10, from both ends) and the realtime of a running trip of
Veszprém 2 and of Miskolc 32, for the route + start path.
fixtures/realtime/mavplusz_trains.json (2026-11-18 19:52 CET) has train
741's two trips that day (UPDATED and MODIFIED, the same calls), train 3311
(MODIFIED) and 741's first trip on the day before, under which the planner
listed the vehicle of 741's live run, for the train path; the static train
numbers those trips are asked by are made up per test (_static).
fixtures/realtime/mavplusz_rt_pattern.json (2026-11-18 21:52 CET) has three
live MODIFIED trains whose own patterns are realtime ones without vehicles,
and the vehicles of their routes' patterns and of two Volán routes.
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from datetime import datetime
import io
import logging
from pathlib import Path
import random
import re
from typing import Any
import zipfile

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
from yarl import URL

from custom_components.hungarian_transport import mavplusz, realtime
from custom_components.hungarian_transport.const import DOMAIN, SERVICE_REALTIME

from .test_upstream import FakeClock

FIX = Path(__file__).parent / "fixtures" / "realtime"
DOC = json.loads((FIX / "mavplusz_trips.json").read_text(encoding="utf-8"))
ANSWER = DOC["response"]
ROUTES_DOC = json.loads((FIX / "mavplusz_routes.json").read_text(encoding="utf-8"))
ROUTES = ROUTES_DOC["routes"]["response"]["data"]
CITY_TRIPS = ROUTES_DOC["trips"]["response"]["data"]
# Just after the route fixture's newest vehicle.
CITY_WALL = 1794638500
# The fixture's request time (its newest vehicle is 10 s older).
WALL = 1794637020
# The jbb fixtures' time (test_realtime.NOW).
JBB_WALL = 1793605800
SERVICE_DAY = 1794610800
J = "https://jbb.ghsq.de/gtfs/hu/"
SZFV = J + "hkir_hkir_V-14827/"
KEYS = [
    mavplusz.TripKey("hkir:hkir_2036_1-3", "20261114", "hkir:"),
    mavplusz.TripKey("hkir:hkir_2511_15-6", "20261114", "hkir:"),
    mavplusz.TripKey("hkir:hkir_4440_273-3", "20261116", "hkir:"),
    mavplusz.TripKey("hkir:hkir_nope_0-0", "20261114", "hkir:"),
]
# The card's view of t0: parent stop ids of its first four calls.
T0_STOPS = ["hkir_779365", "hkir_779410", "hkir_779389", "hkir_779367"]


def _pb(name: str) -> bytes:
    return (FIX / f"{name}.pb").read_bytes()


class LoopClock:
    """The event loop's own clock, virtual (the loop_clock fixture): it
    stands still while anything is ready to run, and when the loop would
    wait for its next timer it jumps there at once instead. Nothing here
    waits for I/O (aioclient_mock answers in the loop), so asyncio.sleep,
    asyncio.timeout and call_later run in the order their seconds give,
    the same every run however fast or busy the machine is, and a test
    reads the round's seconds off loop.time() exactly."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.now = loop.time()
        self._select = loop._selector.select

    def time(self) -> float:
        return self.now

    def select(self, timeout: float | None = None):
        # Whatever is ready already (a call_soon_threadsafe), else the
        # next timer's seconds pass at once. With no timer at all the
        # loop waits as it would (nothing in these tests does).
        events = self._select(0)
        if events or timeout == 0:
            return events
        if timeout is None:
            return self._select(None)
        self.now += timeout
        return []


@pytest.fixture
async def loop_clock():
    """The test's event loop on a LoopClock, back on its own clock before
    the loop shuts down (whose waits a virtual clock would end at once)."""
    loop = asyncio.get_running_loop()
    clock = LoopClock(loop)
    loop.time = clock.time
    loop._selector.select = clock.select
    try:
        yield clock
    finally:
        del loop.time
        del loop._selector.select


def _trip(alias: str) -> dict:
    return copy.deepcopy(ANSWER["data"][alias])


def _as(trip: dict, gtfs_id: str) -> dict:
    """The fixture trip under another id (its own vehicle follows)."""
    return json.loads(json.dumps(trip).replace(trip["gtfsId"], gtfs_id))


# ---------------------------------------------------------------- the module


def test_query_asks_by_variables_one_date_variable_per_date() -> None:
    body = mavplusz.build_query(KEYS)
    assert body["variables"] == DOC["variables"]
    query = body["query"]
    assert query.startswith("query($d0: String, $d1: String, $t0: String!, $t1: String!")
    assert "t3: trip(id: $t3)" in query and "stoptimesForDate(serviceDate: $d1)" in query
    # Ids never go into the query text, and no vehicle ids (plates) are asked.
    assert "hkir_" not in query and "vehicleId" not in query
    assert len(json.dumps(body)) < 4 * 1024


def test_ids_and_dates() -> None:
    assert mavplusz.gtfs_id("hkir_2036_1-3", "hkir:") == "hkir:hkir_2036_1-3"
    assert mavplusz.gtfs_id("hkir:hkir_2036_1-3", "hkir:") == "hkir:hkir_2036_1-3"
    assert mavplusz.service_date("20261116", WALL) == "20261116"
    # Not YYYYMMDD: today in Budapest, which is UTC+1 in November.
    assert mavplusz.service_date(None, 1794614400 - 1800) == "20261114"  # 23:30 UTC on the 13th
    assert mavplusz.service_date("2026-11-16", 1794614400 - 3 * 3600) == "20261113"  # 21:00 UTC


def test_delays_times_and_stripped_ids() -> None:
    res = mavplusz.parse_response(ANSWER, KEYS)
    tu = res[KEYS[0]].trip_update
    assert (tu.trip_id, tu.start_date, tu.route_id, tu.relationship, tu.delay) == (
        "hkir_2036_1-3", "20261114", "hkir_V-R-200-329", 0, None
    )
    first = tu.stops[0]
    assert first.stop_id == "hkir_779365_6"
    assert (first.arr_delay, first.dep_delay) == (-454, 162)
    assert (first.arr_time, first.dep_time) == (SERVICE_DAY + 22346, SERVICE_DAY + 22962)
    assert [u.dep_delay for u in tu.stops] == [162, 103, 51, 92, 122]
    assert all(u.relationship == 0 and u.seq is None for u in tu.stops)


def test_only_the_trips_own_vehicle_with_its_status() -> None:
    res = mavplusz.parse_response(ANSWER, KEYS)
    pattern = ANSWER["data"]["t1"]["pattern"]["vehiclePositions"]
    assert len(pattern) == 2 and {v["trip"]["gtfsId"] for v in pattern} != {"hkir:hkir_2511_15-6"}
    own = next(v for v in pattern if v["trip"]["gtfsId"] == "hkir:hkir_2511_15-6")
    v = res[KEYS[1]].vehicle
    assert v.trip_id == "hkir_2511_15-6" and v.start_date == "20261114"
    assert (v.lat, v.lon, v.timestamp) == (own["lat"], own["lon"], own["lastUpdated"])
    assert v.stop_id == own["stopRelationship"]["stop"]["gtfsId"].removeprefix("hkir:")
    assert v.status == {"INCOMING_AT": 0, "STOPPED_AT": 1, "IN_TRANSIT_TO": 2}[own["stopRelationship"]["status"]]
    assert v.bearing == own["heading"] and v.speed == pytest.approx(own["speed"])


@pytest.mark.parametrize(("status", "code"), [("INCOMING_AT", 0), ("STOPPED_AT", 1), ("IN_TRANSIT_TO", 2), ("?", None)])
def test_vehicle_status_mapping(status: str, code: int | None) -> None:
    trip = _trip("t0")
    trip["pattern"]["vehiclePositions"][0]["stopRelationship"]["status"] = status
    assert mavplusz.parse_trip(trip, KEYS[0]).vehicle.status == code


def test_a_trip_without_realtime_on_the_date_has_neither_update_nor_vehicle() -> None:
    res = mavplusz.parse_response(ANSWER, KEYS)
    # Its pattern's vehicle is on this trip id, but today's run of it.
    assert ANSWER["data"]["t2"]["pattern"]["vehiclePositions"][0]["trip"]["gtfsId"] == KEYS[2].gtfs_id
    assert res[KEYS[2]][:2] == (None, None)
    # Its end is still known: its last call's scheduled time that day.
    last = ANSWER["data"]["t2"]["stoptimesForDate"][-1]
    assert res[KEYS[2]].ends == last["serviceDay"] + max(last["realtimeArrival"], last["realtimeDeparture"])


def test_a_missing_trip_is_none_and_absent_from_the_feed() -> None:
    res = mavplusz.parse_response(ANSWER, KEYS)
    assert res[KEYS[3]] is None
    feed = mavplusz.to_feed(list(res.values()), WALL)
    assert feed.timestamp == WALL
    assert set(feed.trip_updates) == {"hkir_2036_1-3", "hkir_2511_15-6"}
    assert set(feed.vehicles) == {"hkir_2036_1-3", "hkir_2511_15-6"}


def test_a_call_without_realtime_has_no_delay() -> None:
    trip = _trip("t0")
    trip["stoptimesForDate"][1]["realtime"] = False
    stop = mavplusz.parse_trip(trip, KEYS[0]).trip_update.stops[1]
    assert (stop.stop_id, stop.arr_delay, stop.arr_time, stop.dep_delay, stop.dep_time, stop.relationship) == (
        "hkir_779410_1", None, None, None, None, 0
    )


def _canceled(trip: dict, *indices: int) -> dict:
    for i, st in enumerate(trip["stoptimesForDate"]):
        if not indices or i in indices:
            st["realtimeState"] = "CANCELED"
    return trip


def _hit(feed: realtime.Feed, **req) -> dict | None:
    cfg = realtime.FeedConfig("v", (), frozenset({"trip"}), 60, 60)
    src = realtime.Source("", "", "hkir:", "", True)
    return realtime._answer(cfg, [(src, feed, feed, None)], {"key": "k", **req}, WALL)


def test_every_call_canceled_is_a_canceled_trip() -> None:
    res = mavplusz.parse_trip(_canceled(_trip("t0")), KEYS[0])
    assert res.trip_update.relationship == 3 and res.vehicle is None
    hit = _hit(mavplusz.to_feed([res], WALL), trip_id="hkir_2036_1-3", stops=T0_STOPS, **{"from": 1})
    assert (hit["canceled"], hit["delay"]) == (True, None)


def test_one_canceled_call_is_a_skipped_stop() -> None:
    res = mavplusz.parse_trip(_canceled(_trip("t0"), 1), KEYS[0])
    assert [u.relationship for u in res.trip_update.stops] == [0, 1, 0, 0, 0]
    feed = mavplusz.to_feed([res], WALL)
    hit = _hit(feed, trip_id="hkir_2036_1-3", stops=T0_STOPS, **{"from": 1, "to": 3})
    assert (hit["skipped"], hit["delay"], hit["dest_delay"], hit["canceled"]) == (True, None, 92, False)


def test_the_answer_has_the_jbb_shape() -> None:
    feed = mavplusz.to_feed(list(mavplusz.parse_response(ANSWER, KEYS).values()), WALL)
    hit = _hit(feed, trip_id="hkir_2036_1-3", date="20261114", stops=T0_STOPS, **{"from": 1, "to": 3})
    assert list(hit) == ["trip_id", "delay", "time", "dest_delay", "dest_time", "approx", "canceled", "skipped", "vehicle"]
    assert (hit["delay"], hit["time"], hit["dest_delay"], hit["dest_time"]) == (
        103, SERVICE_DAY + 23023, 92, SERVICE_DAY + 23132
    )
    assert hit["vehicle"]["status"] == 2 and hit["vehicle"]["stop_id"] == "hkir_109055_2"
    # Another day's date is another run: no match.
    assert _hit(feed, trip_id="hkir_2036_1-3", date="20261115", stops=T0_STOPS, **{"from": 1}) is None


@pytest.mark.parametrize(
    "doc",
    [
        {"errors": [{"message": "Validation error"}]},
        {"data": {"t0": None}, "errors": [{"message": "partial"}]},
        {"data": {"t0": None}},  # no t1..t3
        {"data": None},
        [],
        {"data": {"t0": "x", "t1": None, "t2": None, "t3": None}},
    ],
)
def test_an_answer_not_trusted_whole_is_a_value_error(doc) -> None:
    with pytest.raises(ValueError):
        mavplusz.parse_response(doc, KEYS)


def test_malformed_calls_and_positions_are_dropped() -> None:
    trip = _trip("t0")
    trip["stoptimesForDate"][0]["arrivalDelay"] = "soon"
    trip["stoptimesForDate"].append("garbage")
    trip["pattern"]["vehiclePositions"][0]["lat"] = None
    res = mavplusz.parse_trip(trip, KEYS[0])
    assert res.trip_update.stops[0].arr_delay is None and len(res.trip_update.stops) == 5
    assert res.vehicle is None


def test_the_largest_answer_fits_under_the_cap() -> None:
    """40 trips of 100 calls, 15 vehicles on each one's pattern: more than
    any trip has, yet under MAX_RESPONSE_BYTES, which keeps the decoding on
    the event loop short."""
    trip = _trip("t1")
    trip["stoptimesForDate"] = [trip["stoptimesForDate"][0]] * 100
    trip["pattern"]["vehiclePositions"] = [trip["pattern"]["vehiclePositions"][0]] * 15
    answer = {"data": {f"t{i}": _as(trip, f"hkir:hkir_{i}_100-{i}") for i in range(mavplusz.MAX_TRIPS_PER_REQUEST)}}
    size = len(json.dumps(answer).encode())
    assert 0.6 * mavplusz.MAX_RESPONSE_BYTES < size < mavplusz.MAX_RESPONSE_BYTES <= 1024 * 1024


async def test_an_answer_over_the_cap_is_a_value_error_and_not_read_whole(aioclient_mock) -> None:
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    body = json.dumps({"data": {"t0": None}, "pad": "x" * (2 * 1024 * 1024)}).encode()
    aioclient_mock.post(mavplusz.URL, content=body)
    with pytest.raises(ValueError, match="over 1048576"):
        await mavplusz.fetch(session, mavplusz.build_query(KEYS[:1]))
    await session.close()


async def test_an_answer_nested_too_deep_is_a_value_error(aioclient_mock) -> None:
    """The decoder gives up on it with a RecursionError: the caller's
    failure path (backoff, one log line) is the one for ValueError."""
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    aioclient_mock.post(mavplusz.URL, content=b"[" * 100_000 + b"]" * 100_000)
    with pytest.raises(ValueError, match="nested too deep"):
        await mavplusz.fetch(session, mavplusz.build_query(KEYS[:1]))
    await session.close()


def test_feed_table_opts_in_the_planners_feeds() -> None:
    table = realtime.load_feed_table()
    flagged = {(fid, s.prefix) for fid, cfg in table.items() for s in cfg.sources if s.mavplusz}
    assert {p for _f, p in flagged} == {"hkir:", "paks:", "debrecen:", "mvkzrt:", "VBUSZ:", "1:"}
    doc = json.loads(realtime.FEEDS_FILE.read_text(encoding="utf-8"))
    assert sum(1 for f in doc["feeds"].values() for s in f["sources"] if s.get("mavplusz") is True) == 67
    for fid in ("veszprem", "miskolc"):
        assert "route_start" in table[fid].match and all(s.mavplusz for s in table[fid].sources), fid
    # The trains: MÁV's two sources and GYSEV's own, each with the static
    # feed its train numbers come from.
    for fid in ("mav", "gysev"):
        assert "train" in table[fid].match, fid
        assert all(s.mavplusz and s.static and s.prefix == "1:" for s in table[fid].sources), fid
    assert not any(s.mavplusz for s in table["oroshaza"].sources)
    for fid, cfg in table.items():
        if any(s.mavplusz for s in cfg.sources):
            assert cfg.match & {"trip", "train"}, fid
    # Only a literal true opts in.
    parsed = realtime.parse_feed_table(
        {"feeds": {"a": {"sources": [{"trip_updates": "https://x/tu", "mavplusz": "yes"}], "match": ["trip"]}}}
    )
    assert parsed["a"].sources[0].mavplusz is False


# ---------------------------------------------------------------- the hub


def _cfg(*match: str, opt_in: bool = True) -> realtime.FeedConfig:
    src = realtime.Source(SZFV + "trip-updates.pb", SZFV + "vehicle-positions.pb", "hkir:", "", opt_in)
    return realtime.FeedConfig("v", (src,), frozenset(match or {"trip"}), 60, 60)


class Planner:
    """A fake mavplusz.hu: answers each asked trip id from trips and each
    asked route from routes (null for the others), and records the
    variables of every request (routes ones in route_asks too)."""

    def __init__(self, trips: dict[str, dict] | None = None, routes: dict[str, dict] | None = None) -> None:
        self.trips = {"hkir:hkir_2036_1-3": _trip("t0"), "hkir:hkir_2511_15-6": _trip("t1")}
        self.trips.update({t["gtfsId"]: copy.deepcopy(t) for t in CITY_TRIPS.values()})
        self.trips.update(trips or {})
        self.routes = {v: copy.deepcopy(ROUTES[k]) for k, v in ROUTES_DOC["routes"]["variables"].items() if k[0] == "r"}
        self.routes.update(routes or {})
        self.asked: list[dict[str, str]] = []
        self.route_asks: list[dict[str, str]] = []

    async def __call__(self, method, url, data):
        body = json.loads(data)
        self.asked.append(body["variables"])
        if "route(id:" in body["query"]:
            self.route_asks.append(body["variables"])
            table, kind = self.routes, "r"
        else:
            table, kind = self.trips, "t"
        aliases = [k for k in body["variables"] if k[0] == kind]
        answer = {"data": {a: table.get(body["variables"][a]) for a in aliases}}
        return AiohttpClientMockResponse(method, url, json=answer)


def _jbb(aioclient_mock) -> None:
    aioclient_mock.get(SZFV + "trip-updates.pb", content=_pb("volan-szekesfehervar-trip-updates"))
    aioclient_mock.get(SZFV + "vehicle-positions.pb", content=_pb("volan-szekesfehervar-vehicle-positions"))


def _calls(aioclient_mock, method: str) -> list:
    return [c for c in aioclient_mock.mock_calls if c[0].lower() == method]


T0_REQ = {"key": "a", "trip_id": "hkir_2036_1-3", "date": "20261114", "stops": T0_STOPS, "from": 1, "to": 3}
# test_realtime's Székesfehérvár trip, in the jbb fixtures only.
SZFV_REQ = {
    "key": "s",
    "trip_id": "hkir_H6112_607-1",
    "date": "20261102",
    "stops": ["hkir_666060", "hkir_665955", "hkir_666225"],
    "repeat": [0],
    "from": 1,
    "to": 2,
}


async def _hub(aioclient_mock, cfg: realtime.FeedConfig, wall: float = WALL, clock: FakeClock | None = None):
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    clock = clock or FakeClock()
    hub = realtime.RealtimeHub(session, feeds={"v": cfg, "alias": cfg}, monotonic=clock.monotonic, wall=lambda: wall)
    return hub, session, clock


async def test_the_planner_answers_and_no_jbb_feed_is_read(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, _clock = await _hub(aioclient_mock, _cfg())
    trips = [
        T0_REQ,
        {"key": "b", "trip_id": "hkir:hkir_2511_15-6"},
        {"key": "later", "trip_id": "hkir_4440_273-3", "date": "20261116"},
        {"key": "nope", "trip_id": "hkir_nope_0-0", "date": "20261114"},
    ]
    resp = await hub.query("alias", trips)
    await session.close()
    assert aioclient_mock.call_count == 1
    method, url, _data, headers = aioclient_mock.mock_calls[0]
    assert (method.lower(), url) == ("post", URL(mavplusz.URL))
    assert "github.com/bator/hungarian-transport" in headers["User-Agent"]
    # One request, ids and the service date (Budapest today without one).
    assert planner.asked == [DOC["variables"]]
    assert resp["feed"] == "alias" and resp["updated"] == WALL
    assert set(resp["trips"]) == {"a", "b"}
    a = resp["trips"]["a"]
    assert (a["trip_id"], a["delay"], a["dest_delay"], a["approx"], a["canceled"]) == (
        "hkir_2036_1-3", 103, 92, False, False
    )
    assert resp["trips"]["b"]["vehicle"]["timestamp"] == next(
        v["lastUpdated"] for v in ANSWER["data"]["t1"]["pattern"]["vehiclePositions"]
        if v["trip"]["gtfsId"] == "hkir:hkir_2511_15-6"
    )


async def test_answers_are_cached_per_trip_for_the_interval(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, clock = await _hub(aioclient_mock, _cfg())
    await hub.query("v", [T0_REQ])
    clock.now += 30
    # Within the interval: only the trip not asked yet goes out.
    second = await hub.query("v", [T0_REQ, {"key": "b", "trip_id": "hkir_2511_15-6", "date": "20261114"}])
    clock.now += 29
    await hub.query("v", [T0_REQ])
    assert len(planner.asked) == 2
    assert planner.asked[1] == {"d0": "20261114", "t0": "hkir:hkir_2511_15-6"}
    assert second["trips"]["a"]["delay"] == 103 and "b" in second["trips"]
    clock.now += 2
    await hub.query("v", [T0_REQ])
    await session.close()
    assert len(planner.asked) == 3
    assert not _calls(aioclient_mock, "get")


@pytest.mark.parametrize(
    "failure",
    [
        {"status": 502},
        {"exc": asyncio.TimeoutError()},
        {"text": "<html>maintenance</html>"},
        {"json": {"errors": [{"message": "boom"}]}},
    ],
    ids=["http", "timeout", "not-json", "graphql-error"],
)
async def test_a_failing_planner_falls_back_to_jbb(aioclient_mock, failure) -> None:
    aioclient_mock.post(mavplusz.URL, **failure)
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    resp = await hub.query("v", [SZFV_REQ])
    await session.close()
    assert len(_calls(aioclient_mock, "post")) == 1 and len(_calls(aioclient_mock, "get")) == 2
    assert resp["updated"] == 1793605797
    assert (resp["trips"]["s"]["delay"], resp["trips"]["s"]["dest_delay"]) == (190, 181)


async def test_a_slow_planner_round_falls_back_to_jbb(aioclient_mock, monkeypatch) -> None:
    """The planner round, the wait for the other cards' requests included,
    has one deadline: past it the jbb feeds answer."""
    monkeypatch.setattr(realtime, "PLANNER_TIMEOUT", 0.05)
    planner = Planner()

    async def slow(method, url, data):
        await asyncio.sleep(5)
        return await planner(method, url, data)

    aioclient_mock.post(mavplusz.URL, side_effect=slow)
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    resp = await asyncio.wait_for(hub.query("v", [SZFV_REQ]), 2)
    # Another card's request holding the lock: this one waits no longer.
    async with hub._mav_lock:
        second = await asyncio.wait_for(hub.query("v", [SZFV_REQ]), 2)
    await session.close()
    assert resp["trips"]["s"]["delay"] == 190 and second["trips"]["s"]["delay"] == 190
    assert len(_calls(aioclient_mock, "post")) == 1


async def test_a_hung_planner_request_backs_off_like_a_failed_one(aioclient_mock, monkeypatch, caplog) -> None:
    """The round's deadline cuts a request that never answers before
    aiohttp's own timeout would: that is an outage too, so the next polls
    within FAIL_BACKOFF read the jbb feeds without posting again."""
    monkeypatch.setattr(realtime, "PLANNER_TIMEOUT", 0.05)

    async def hang(method, url, data):
        await asyncio.sleep(5)

    aioclient_mock.post(mavplusz.URL, side_effect=hang)
    _jbb(aioclient_mock)
    hub, session, clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    for _ in range(3):
        resp = await asyncio.wait_for(hub.query("v", [SZFV_REQ]), 2)
        assert resp["trips"]["s"]["delay"] == 190
        clock.now += 1
    await session.close()
    assert len(_calls(aioclient_mock, "post")) == 1
    assert hub._mav_retry_at > clock.now
    assert caplog.text.count("unavailable") == 1 and "no answer within 0.05 s" in caplog.text
    assert hub._mav_cut is None


async def test_a_round_spent_waiting_for_the_lock_is_no_outage(aioclient_mock, monkeypatch, caplog) -> None:
    """Only a request of this round that hung backs off: waiting for another
    card's one past the deadline does not."""
    monkeypatch.setattr(realtime, "PLANNER_TIMEOUT", 0.05)
    aioclient_mock.post(mavplusz.URL, side_effect=Planner())
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    async with hub._mav_lock:
        resp = await asyncio.wait_for(hub.query("v", [SZFV_REQ]), 2)
    await session.close()
    assert resp["trips"]["s"]["delay"] == 190
    assert hub._mav_retry_at < 0 and "unavailable" not in caplog.text
    assert not _calls(aioclient_mock, "post")


async def test_a_request_begun_late_in_the_round_is_not_begun_and_no_outage(
    aioclient_mock, monkeypatch, caplog
) -> None:
    """Another card's request holds the lock for most of the round: this
    card's is not begun only to be cut before a healthy planner answers
    (which would back off every card for FAIL_BACKOFF as an outage). The
    feeds answer this round, and the next one asks the planner.

    The lock is let go three quarters of a round after this card's request
    came to it, so a quarter of the round is left (under PLANNER_MIN_SHARE)
    however late the event loop runs the steps up to then; the planner
    answers in a tenth of one."""
    monkeypatch.setattr(realtime, "PLANNER_TIMEOUT", 1.0)
    caplog.set_level(logging.DEBUG, logger=realtime.__name__)
    planner = Planner({"hkir:hkir_H6112_607-1": _as(_trip("t0"), "hkir:hkir_H6112_607-1")})

    async def slowish(method, url, data):
        await asyncio.sleep(0.1)
        return await planner(method, url, data)

    aioclient_mock.post(mavplusz.URL, side_effect=slowish)
    _jbb(aioclient_mock)
    hub, session, clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)

    class Lock(asyncio.Lock):
        """The planner's lock, telling when a request comes to it held."""

        def __init__(self) -> None:
            super().__init__()
            self.waited = asyncio.Event()

        async def acquire(self) -> bool:
            if self.locked():
                self.waited.set()
            return await super().acquire()

    hub._mav_lock = lock = Lock()

    async def holder():
        async with lock:
            await asyncio.wait_for(lock.waited.wait(), 5)
            await asyncio.sleep(0.75 * realtime.PLANNER_TIMEOUT)

    held = asyncio.create_task(holder())
    await asyncio.sleep(0)
    resp = await asyncio.wait_for(hub.query("v", [SZFV_REQ]), 5)
    await held
    assert resp["trips"]["s"]["delay"] == 190
    assert not planner.asked and len(_calls(aioclient_mock, "get")) == 2
    assert "of the round left, not asked" in caplog.text
    assert hub._mav_retry_at < clock.now and "unavailable" not in caplog.text
    resp = await asyncio.wait_for(hub.query("v", [SZFV_REQ]), 5)
    await session.close()
    assert len(planner.asked) == 1 and resp["trips"]["s"]["trip_id"] == "hkir_H6112_607-1"


async def test_a_jbb_copy_from_a_planner_outage_leaves_memory_while_the_planner_answers(aioclient_mock) -> None:
    """A planner outage loads the jbb feeds. Once the planner answers again
    no call reads them, and the decoded copies go all the same after
    IDLE_INTERVALS of their interval."""
    state = {"fail": True}
    planner = Planner({"hkir:hkir_H6112_607-1": _as(_trip("t0"), "hkir:hkir_H6112_607-1")})

    async def flaky(method, url, data):
        if state["fail"]:
            return AiohttpClientMockResponse(method, url, status=503)
        return await planner(method, url, data)

    aioclient_mock.post(mavplusz.URL, side_effect=flaky)
    _jbb(aioclient_mock)
    hub, session, clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    await hub.query("v", [SZFV_REQ])
    assert all(e.data is not None for e in hub._entries.values())
    state["fail"] = False
    clock.now += realtime.FAIL_BACKOFF + 1
    for _ in range(realtime.IDLE_INTERVALS + 1):
        resp = await hub.query("v", [SZFV_REQ])
        assert resp["trips"]["s"]["trip_id"] == "hkir_H6112_607-1"
        clock.now += 60
    await hub.query("v", [SZFV_REQ])
    await session.close()
    assert len(_calls(aioclient_mock, "get")) == 2
    assert all(e.data is None for e in hub._entries.values())


async def test_a_planner_outage_is_logged_once_backs_off_and_recovers_once(aioclient_mock, caplog) -> None:
    aioclient_mock.post(mavplusz.URL, status=503)
    _jbb(aioclient_mock)
    hub, session, clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    await hub.query("v", [SZFV_REQ])
    clock.now += realtime.FAIL_BACKOFF - 1
    resp = await hub.query("v", [SZFV_REQ])
    assert resp["trips"]["s"]["delay"] == 190
    assert len(_calls(aioclient_mock, "post")) == 1  # the backoff: jbb's copy only
    clock.now += 2
    await hub.query("v", [SZFV_REQ])
    assert len(_calls(aioclient_mock, "post")) == 2
    assert caplog.text.count("mavplusz.hu") == 1 and "unavailable" in caplog.text
    # It answers again: one info line, and the jbb feeds are not read.
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=Planner({"hkir:hkir_H6112_607-1": _as(_trip("t0"), "hkir:hkir_H6112_607-1")}))
    clock.now += realtime.FAIL_BACKOFF + 1
    with caplog.at_level(logging.INFO):
        resp = await hub.query("v", [SZFV_REQ])
        clock.now += 61
        await hub.query("v", [SZFV_REQ])
    await session.close()
    assert caplog.text.count("answers again") == 1
    assert resp["trips"]["s"]["trip_id"] == "hkir_H6112_607-1" and resp["updated"] == JBB_WALL
    assert not _calls(aioclient_mock, "get")


async def test_a_planner_that_knows_none_of_the_trips_falls_back_to_jbb(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _jbb(aioclient_mock)
    hub, session, clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    resp = await hub.query("v", [SZFV_REQ])
    assert resp["trips"]["s"]["delay"] == 190
    assert len(planner.asked) == 1 and len(_calls(aioclient_mock, "get")) == 2
    # The miss is cached like an answer: within the interval no new request
    # to either (the jbb copy is cached too).
    clock.now += 30
    await hub.query("v", [SZFV_REQ])
    await session.close()
    assert len(planner.asked) == 1 and len(_calls(aioclient_mock, "get")) == 2


async def test_a_trip_the_planner_lacks_among_known_ones_reads_no_jbb(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL)
    resp = await hub.query("v", [SZFV_REQ, {**T0_REQ, "date": "20261114"}])
    await session.close()
    assert set(resp["trips"]) == {"a"}
    assert not _calls(aioclient_mock, "get")


async def test_a_rate_limited_planner_is_not_asked(aioclient_mock) -> None:
    aioclient_mock.post(mavplusz.URL, side_effect=Planner())
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), wall=JBB_WALL, clock=FakeClock(stopped=True))
    for _ in range(realtime.MAV_BURST):
        hub._mav_rate.take()
    resp = await hub.query("v", [SZFV_REQ])
    await session.close()
    # Nothing goes to the planner; the feeds, in a bucket of their own, answer.
    assert not _calls(aioclient_mock, "post") and len(_calls(aioclient_mock, "get")) == 2
    assert resp["trips"]["s"]["delay"] == 190


async def test_the_planner_takes_a_token(aioclient_mock) -> None:
    aioclient_mock.post(mavplusz.URL, side_effect=Planner())
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), clock=FakeClock(stopped=True))
    for _ in range(realtime.MAV_BURST - 1):
        hub._mav_rate.take()
    await hub.query("v", [T0_REQ])
    await session.close()
    assert aioclient_mock.call_count == 1
    with pytest.raises(realtime.RealtimeRateLimited):
        hub._mav_rate.take()
    # Not the feeds' token.
    for _ in range(realtime.FETCH_BURST):
        hub._rate.take()


async def test_planner_requests_cannot_use_up_the_feed_fetches(aioclient_mock) -> None:
    """Any logged-in user can ask the planner for trips no card shows, each
    a request: that uses up the planner's tokens only, and a card of a feed
    without the planner still gets its fetch."""
    ids = [f"hkir:hkir_x{i}_1-1" for i in range(realtime.MAV_BURST + 1)]
    planner = Planner({gid: _as(_trip("t0"), gid) for gid in ids})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _jbb(aioclient_mock)
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    jbb_only = _cfg(opt_in=False)
    hub = realtime.RealtimeHub(
        session,
        feeds={"v": _cfg(), "j": dataclasses.replace(jbb_only, feed_id="j")},
        monotonic=FakeClock(stopped=True).monotonic,
        wall=lambda: JBB_WALL,
    )
    for gid in ids:
        await hub.query("v", [{"key": "k", "trip_id": gid, "date": "20261114"}])
    resp = await hub.query("j", [SZFV_REQ])
    await session.close()
    assert len(planner.asked) == realtime.MAV_BURST
    assert len(_calls(aioclient_mock, "get")) == 2
    assert resp["trips"]["s"]["delay"] == 190


@pytest.mark.parametrize(
    "req",
    [
        {"key": "t", "train": "4510"},
        {"key": "none"},
    ],
    ids=["train-without-a-static-feed", "nothing"],
)
async def test_requests_without_a_trip_id_or_route_read_only_jbb(aioclient_mock, req) -> None:
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg("trip", "route_start", "train"), wall=JBB_WALL)
    await hub.query("v", [req])
    await session.close()
    assert len(_calls(aioclient_mock, "get")) == 2 and not _calls(aioclient_mock, "post")


async def test_an_unresolved_route_beside_trip_ids_reads_both(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg("trip", "route_start"), wall=JBB_WALL)
    resp = await hub.query("v", [T0_REQ, {"key": "r", "route": "x", "start": 480}])
    await session.close()
    # The route the planner does not know is matched on the feeds.
    assert planner.route_asks == [{"d0": mavplusz.service_date(None, JBB_WALL), "r0": "hkir:x"}]
    assert len(planner.asked) == 2 and len(_calls(aioclient_mock, "get")) == 2
    # The trip id is the planner's; the newest stamp is jbb's here.
    assert resp["trips"]["a"]["delay"] == 103
    assert resp["updated"] == JBB_WALL


async def test_a_source_without_the_key_never_asks_the_planner(aioclient_mock) -> None:
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg(opt_in=False), wall=JBB_WALL)
    resp = await hub.query("v", [SZFV_REQ])
    await session.close()
    assert not _calls(aioclient_mock, "post")
    assert resp["trips"]["s"]["delay"] == 190


async def test_the_service_asks_the_planner_for_an_opted_in_feed(hass, aioclient_mock, freezer) -> None:
    freezer.move_to("2026-11-14T06:17:00+00:00")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_REALTIME, {"feed": "volan-szekesfehervar", "trips": [T0_REQ]},
        blocking=True, return_response=True,
    )
    assert resp["trips"]["a"]["delay"] == 103
    assert aioclient_mock.call_count == 1 and len(planner.asked) == 1


# ---------------------------------------------------------------- route + start


VB = J + "VBUSZ/"
MV = J + "mvkzrt/"
VB_REQ = {"key": "v", "route": "2", "start": 454, "date": "20261114", "stops": ["SP1751", "SP1753"], "from": 0, "to": 1}
# Miskolc's cards send their own static trip id and stop ids too.
MV_REQ = {
    "key": "m", "trip_id": "10977_60_073700_static", "route": "32", "start": 457, "date": "20261114",
    "stops": ["mvk_1", "mvk_2"], "from": 0, "to": 1,
}


def _city_cfg(prefix: str) -> realtime.FeedConfig:
    base = VB if prefix == "VBUSZ:" else MV
    src = realtime.Source(base + "trip-updates.pb", base + "vehicle-positions.pb", prefix, "", True)
    return realtime.FeedConfig("c", (src,), frozenset({"trip", "route_start"}), 60, 60)


def _jbb_like(cfg: realtime.FeedConfig, req: dict) -> dict | None:
    """What the jbb path answers for req, given the same data as GTFS-RT
    feeds give it: trip updates without a route, the route learned from
    each trip's vehicle."""
    keys = [
        mavplusz.TripKey(v, ROUTES_DOC["trips"]["variables"]["d0"], v.split(":")[0] + ":")
        for k, v in ROUTES_DOC["trips"]["variables"].items() if k[0] == "t"
    ]
    planner = mavplusz.to_feed(list(mavplusz.parse_response(ROUTES_DOC["trips"]["response"], keys).values()), CITY_WALL)
    tu = realtime.Feed(CITY_WALL, {k: dataclasses.replace(t, route_id="") for k, t in planner.trip_updates.items()})
    vp = realtime.Feed(CITY_WALL, vehicles=dict(planner.vehicles))
    known: dict[str, str] = {}
    realtime._learn_routes(known, vp)
    realtime._fill_routes(known, tu)
    return realtime._answer(cfg, [(cfg.sources[0], tu, vp, None)], req, CITY_WALL)


def test_route_query_asks_by_variables() -> None:
    keys = [mavplusz.RouteKey("VBUSZ:2", "20261114", "VBUSZ:"), mavplusz.RouteKey("mvkzrt:14", "20261114", "mvkzrt:")]
    body = mavplusz.build_route_query(keys)
    assert body["variables"] == {"d0": "20261114", "r0": "VBUSZ:2", "r1": "mvkzrt:14"}
    assert body["query"] == (
        "query($d0: String, $r0: String!, $r1: String!) { "
        "r0: route(id: $r0) { patterns { tripsForDate(serviceDate: $d0) { gtfsId } } } "
        "r1: route(id: $r1) { patterns { tripsForDate(serviceDate: $d0) { gtfsId } } } }"
    )


def test_route_trips_and_their_starts() -> None:
    keys = [mavplusz.RouteKey(v, "20261114", v.split(":")[0] + ":") for k, v in ROUTES_DOC["routes"]["variables"].items() if k[0] == "r"]
    listed = mavplusz.parse_routes(ROUTES_DOC["routes"]["response"], keys)
    vb2, mv32, mv14, nope = (listed[k] for k in keys)
    assert nope is None and len(vb2) == 32 and len(set(vb2)) == len(vb2)
    assert mavplusz.trips_starting(vb2, "VBUSZ:", 454) == ["VBUSZ:1131_2084_073400_SZ"]
    assert mavplusz.trips_starting(mv32, "mvkzrt:", 457) == ["mvkzrt:10977_60_073700_B4P"]
    # Both ends of line 14 at 07:10; and the start is read modulo a day.
    both = mavplusz.trips_starting(mv14, "mvkzrt:", 430)
    assert sorted(both) == ["mvkzrt:10949_644_071000_AWD", "mvkzrt:10949_731_071000_AWD"]
    assert mavplusz.trips_starting(mv14, "mvkzrt:", 430 + 1440) == both
    with pytest.raises(ValueError):
        mavplusz.parse_routes({"data": {"r0": None}}, keys)


async def test_veszprem_route_start_on_the_planner_answers_as_jbb_would(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    cfg = _city_cfg("VBUSZ:")
    hub, session, _clock = await _hub(aioclient_mock, cfg, wall=CITY_WALL)
    resp = await hub.query("v", [VB_REQ])
    await session.close()
    assert planner.route_asks == [{"d0": "20261114", "r0": "VBUSZ:2"}]
    assert planner.asked[1] == {"d0": "20261114", "t0": "VBUSZ:1131_2084_073400_SZ"}
    assert not _calls(aioclient_mock, "get")
    hit = resp["trips"]["v"]
    assert (hit["trip_id"], hit["delay"], hit["dest_delay"], hit["approx"]) == ("1131_2084_073400_SZ", 172, 114, False)
    assert hit == _jbb_like(cfg, VB_REQ)


async def test_miskolc_route_start_gives_the_vehicles_current_delay(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    cfg = _city_cfg("mvkzrt:")
    hub, session, _clock = await _hub(aioclient_mock, cfg, wall=CITY_WALL)
    resp = await hub.query("v", [MV_REQ])
    await session.close()
    # The resolved trip, and the card's own trip id after it (unknown to
    # the planner).
    assert planner.asked[1] == {"d0": "20261114", "t0": "mvkzrt:10977_60_073700_B4P", "t1": "mvkzrt:10977_60_073700_static"}
    assert not _calls(aioclient_mock, "get")
    hit = resp["trips"]["m"]
    assert (hit["delay"], hit["approx"], hit["vehicle"]["stop_id"]) == (34, True, "SP70")
    assert hit == _jbb_like(cfg, MV_REQ)


async def test_miskolc_without_its_vehicle_has_no_delay(aioclient_mock) -> None:
    trip = copy.deepcopy(CITY_TRIPS["t1"])
    trip["pattern"]["vehiclePositions"] = []
    aioclient_mock.post(mavplusz.URL, side_effect=Planner({trip["gtfsId"]: trip}))
    hub, session, _clock = await _hub(aioclient_mock, _city_cfg("mvkzrt:"), wall=CITY_WALL)
    resp = await hub.query("v", [MV_REQ])
    await session.close()
    # Its updates say 0 for the stops already served: no on-time it never said.
    assert resp["trips"] == {} and not _calls(aioclient_mock, "get")


async def test_many_route_start_rows_with_foreign_trip_ids_are_all_answered(aioclient_mock) -> None:
    """30 Veszprém rows, each with the card's own trip id (not the
    planner's) and a route + start: 60 keys, over the 40 a request takes.
    The resolved trips go first, so every row is answered on every poll,
    and the cached misses never take a resolved trip's place."""
    listed = [
        t["gtfsId"] for pat in ROUTES["r0"]["patterns"] for t in pat["tripsForDate"]
    ][:30]
    model = next(t for t in CITY_TRIPS.values() if t["gtfsId"].startswith("VBUSZ:"))
    planner = Planner({gid: _as(model, gid) for gid in listed})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, clock = await _hub(aioclient_mock, _city_cfg("VBUSZ:"), wall=CITY_WALL)
    rows = [
        {**VB_REQ, "key": f"k{n}", "trip_id": f"card_{n}", "start": realtime._start_minutes(gid.split(":")[1])}
        for n, gid in enumerate(listed)
    ]
    for poll in range(4):
        resp = await hub.query("v", rows)
        assert set(resp["trips"]) == {r["key"] for r in rows}, poll
        assert {resp["trips"][f"k{n}"]["trip_id"] for n in range(30)} == {g.split(":")[1] for g in listed}
        clock.now += 31
    await session.close()
    trip_asks = [a for a in planner.asked if a not in planner.route_asks]
    assert all(len([k for k in a if k[0] == "t"]) <= mavplusz.MAX_TRIPS_PER_REQUEST for a in trip_asks)
    assert set(trip_asks[0].values()) >= set(listed)
    assert not _calls(aioclient_mock, "get")


def _line14(gtfs_id: str, stops: list[str]) -> dict:
    trip = _as(copy.deepcopy(CITY_TRIPS["t1"]), gtfs_id)
    trip["route"]["gtfsId"] = "mvkzrt:14"
    for st, stop in zip(trip["stoptimesForDate"], stops):
        st["stop"]["gtfsId"] = "mvkzrt:" + stop
    return trip


@pytest.mark.parametrize(("stops", "picked"), [(["SPA1"], "10949_731_071000_AWD"), (["SPB1"], "10949_644_071000_AWD"), ([], None)])
async def test_a_shared_start_is_told_apart_by_the_stops(aioclient_mock, stops, picked) -> None:
    planner = Planner({
        "mvkzrt:10949_731_071000_AWD": _line14("mvkzrt:10949_731_071000_AWD", ["SPA1", "SPA2"]),
        "mvkzrt:10949_644_071000_AWD": _line14("mvkzrt:10949_644_071000_AWD", ["SPB1", "SPB2"]),
    })
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, _clock = await _hub(aioclient_mock, _city_cfg("mvkzrt:"), wall=CITY_WALL)
    req = {"key": "d", "route": "14", "start": 430, "date": "20261114", "stops": stops, "from": 0}
    resp = await hub.query("v", [req])
    await session.close()
    assert sorted(v for k, v in planner.asked[1].items() if k[0] == "t") == [
        "mvkzrt:10949_644_071000_AWD", "mvkzrt:10949_731_071000_AWD"
    ]
    assert resp["trips"].get("d", {}).get("trip_id") == picked
    assert not _calls(aioclient_mock, "get")


@pytest.mark.parametrize(("stops", "picked"), [(["SPA1"], "10949_731_071000_AWD"), (["SPB1"], None), ([], None)])
async def test_one_of_two_trips_at_a_shared_start_must_call_at_the_stops(aioclient_mock, stops, picked) -> None:
    """Both ends of line 14 at 07:10, only one with realtime data: a card on
    the other one gets no answer, not this one's delay and vehicle."""
    quiet = _line14("mvkzrt:10949_644_071000_AWD", ["SPB1", "SPB2"])
    for st in quiet["stoptimesForDate"]:
        st["realtime"] = False
    planner = Planner({
        "mvkzrt:10949_731_071000_AWD": _line14("mvkzrt:10949_731_071000_AWD", ["SPA1", "SPA2"]),
        "mvkzrt:10949_644_071000_AWD": quiet,
    })
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, _clock = await _hub(aioclient_mock, _city_cfg("mvkzrt:"), wall=CITY_WALL)
    req = {"key": "d", "route": "14", "start": 430, "date": "20261114", "stops": stops, "from": 0}
    resp = await hub.query("v", [req])
    await session.close()
    assert resp["trips"].get("d", {}).get("trip_id") == picked
    assert not _calls(aioclient_mock, "get")


async def test_route_lists_are_cached_for_hours_and_trips_for_the_interval(aioclient_mock) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, clock = await _hub(aioclient_mock, _city_cfg("VBUSZ:"), wall=CITY_WALL)
    await hub.query("v", [VB_REQ])
    clock.now += 30
    await hub.query("v", [VB_REQ])
    assert (len(planner.route_asks), len(planner.asked)) == (1, 2)
    clock.now += 31
    await hub.query("v", [VB_REQ])
    assert (len(planner.route_asks), len(planner.asked)) == (1, 3)
    clock.now += realtime.ROUTE_INTERVAL
    await hub.query("v", [VB_REQ])
    await session.close()
    assert (len(planner.route_asks), len(planner.asked)) == (2, 5)
    assert not _calls(aioclient_mock, "get")


@pytest.mark.parametrize("route", ["nope", "2"], ids=["unknown-route", "no-trip-at-that-start"])
async def test_an_unresolved_route_start_reads_jbb(aioclient_mock, route) -> None:
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    aioclient_mock.get(VB + "trip-updates.pb", content=_pb("veszprem-trip-updates"))
    aioclient_mock.get(VB + "vehicle-positions.pb", content=_pb("veszprem-vehicle-positions"))
    hub, session, _clock = await _hub(aioclient_mock, _city_cfg("VBUSZ:"), wall=JBB_WALL)
    await hub.query("v", [{**VB_REQ, "route": route, "start": 1}])
    await session.close()
    # One route request, no trip request, the feeds.
    assert len(planner.route_asks) == 1 and len(planner.asked) == 1
    assert len(_calls(aioclient_mock, "get")) == 2


async def test_a_failing_route_list_reads_jbb(aioclient_mock) -> None:
    aioclient_mock.post(mavplusz.URL, status=502)
    aioclient_mock.get(VB + "trip-updates.pb", content=_pb("veszprem-trip-updates"))
    aioclient_mock.get(VB + "vehicle-positions.pb", content=_pb("veszprem-vehicle-positions"))
    hub, session, _clock = await _hub(aioclient_mock, _city_cfg("VBUSZ:"), wall=JBB_WALL)
    resp = await hub.query("v", [VB_REQ])
    await session.close()
    # The backoff keeps the trip request from going out too.
    assert len(_calls(aioclient_mock, "post")) == 1 and len(_calls(aioclient_mock, "get")) == 2
    assert "error" not in resp


# ---------------------------------------------------------------- trains


TRAINS_DOC = json.loads((FIX / "mavplusz_trains.json").read_text(encoding="utf-8"))
TRAINS = TRAINS_DOC["response"]["data"]
# The train fixture's request time, 2026-11-18 19:52:45 CET (its vehicle is
# 36 s older), and that service day's midnight.
TRAIN_WALL = 1795027965
TRAIN_DAY = 1794956400
TODAY, YESTERDAY = "20261118", "20261117"
TABLE = realtime.load_feed_table()
MAV_SRC, GYSEV_SRC = TABLE["mav"].sources
IC741, IC741_MOD, OS3311 = "1:36777783", "1:36391307", "1:36322788"
# The card's train rows: station codes (Békéscsaba, Kőbánya-Kispest,
# Budapest-Keleti), the boarding one first.
REQ_741 = {"key": "741", "train": "741", "stops": ["005518069", "005510017"], "from": 0, "to": 1}
REQ_3311 = {"key": "3311", "train": "3311", "stops": ["005513748", "005510017"], "from": 0, "to": 1}
_ALIAS = re.compile(r"(t\d+): trip\(id: \$t\d+\) \{ gtfsId route \{ gtfsId \} stoptimesForDate\(serviceDate: \$(d\d+)\)")


def _static(numbers: dict[str, list[str]], runs_on: dict[str, list[str]] | None = None) -> bytes:
    """A static GTFS zip whose trips.txt gives each train number its trip
    ids; with runs_on (trip id -> its days) a service per trip and a
    calendar_dates.txt of those days, else no calendar."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        rows = "".join(f"{tid},R,S{tid},{num} személyvonat\n" for num, ids in numbers.items() for tid in ids)
        zf.writestr("trips.txt", "trip_id,route_id,service_id,trip_short_name\n" + rows)
        if runs_on is not None:
            days = "".join(f"S{tid},{d},1\n" for tid, ds in runs_on.items() for d in ds)
            zf.writestr("calendar_dates.txt", "service_id,date,exception_type\n" + days)
    return buf.getvalue()


def _midnight(date: str) -> int:
    return int(datetime.strptime(date, "%Y%m%d").replace(tzinfo=mavplusz.BUDAPEST).timestamp())


def _scheduled(trip: dict, date: str) -> dict:
    """The trip's run on another date as the planner gives one without
    realtime data: its scheduled calls, no vehicle."""
    out = copy.deepcopy(trip)
    for st in out["stoptimesForDate"]:
        st.update(realtime=False, realtimeState="SCHEDULED", arrivalDelay=0, departureDelay=0, serviceDay=_midnight(date))
    out["pattern"]["vehiclePositions"] = []
    if "code" in out["pattern"]:
        out["pattern"]["code"] = out["pattern"]["code"].split("::rt#")[0] + "::01"
    return out


def _later(trip: dict, seconds: int) -> dict:
    """The run with every call later by seconds (a night train past midnight)."""
    out = copy.deepcopy(trip)
    for st in out["stoptimesForDate"]:
        st["realtimeArrival"] += seconds
        st["realtimeDeparture"] += seconds
    return out


class TrainPlanner:
    """A fake mavplusz.hu by (trip id, service date): a run in runs as it
    is, a known trip on another date without realtime (_scheduled), an
    unknown one null; a vehicle request's route alias from routes, else
    null. asks has the (id, date) pairs of every trip request, route_asks
    the route ids of every vehicle request. vehicles is the answer of a
    vehicle request instead, when set: an HTTP status, or a coroutine
    function's answer (a slow planner)."""

    def __init__(self, runs: dict[tuple[str, str], dict] | None = None, routes: dict[str, dict] | None = None) -> None:
        self.runs = {
            (IC741, TODAY): TRAINS["t0"], (IC741_MOD, TODAY): TRAINS["t1"],
            (OS3311, TODAY): TRAINS["t2"], (IC741, YESTERDAY): TRAINS["t3"],
        } if runs is None else runs
        self.routes = routes or {}
        self.asks: list[list[tuple[str, str]]] = []
        self.route_asks: list[list[str]] = []
        self.vehicles: Any = None

    async def __call__(self, method, url, data):
        body = json.loads(data)
        variables = body["variables"]
        routes = sorted((k for k in variables if k[0] == "r"), key=lambda k: int(k[1:]))
        if routes:
            assert "trip(id:" not in body["query"]
            self.route_asks.append([variables[k] for k in routes])
            if isinstance(self.vehicles, int):
                return AiohttpClientMockResponse(method, url, status=self.vehicles)
            if self.vehicles is not None:
                return AiohttpClientMockResponse(method, url, json=await self.vehicles(variables))
            answer = {k: copy.deepcopy(self.routes.get(variables[k])) for k in routes}
            return AiohttpClientMockResponse(method, url, json={"data": answer})
        pairs = [(variables[alias], variables[d]) for alias, d in _ALIAS.findall(body["query"])]
        self.asks.append(pairs)
        answer = {}
        for (alias, _d), (gid, date) in zip(_ALIAS.findall(body["query"]), pairs):
            run = self.runs.get((gid, date))
            if run is None:
                other = next((t for (g, _day), t in self.runs.items() if g == gid), None)
                run = None if other is None else _scheduled(other, date)
            answer[alias] = copy.deepcopy(run)
        return AiohttpClientMockResponse(method, url, json={"data": answer})


# GYSEV's static feed in the MÁV tests: none of their trains.
GYSEV_OTHER = {"9999": ["39999999"]}


def _statics(
    aioclient_mock, mav: dict | None, gysev: dict | None = GYSEV_OTHER, runs_on: dict[str, list[str]] | None = None
) -> None:
    """The two static feeds' train numbers (MÁV's with the runs_on
    calendar); None is a static feed that fails."""
    for src, numbers, days in ((MAV_SRC, mav, runs_on), (GYSEV_SRC, gysev, None)):
        if numbers is None:
            aioclient_mock.get(src.static, status=503)
        else:
            aioclient_mock.get(src.static, content=_static(numbers, days))


def _feed_gets(aioclient_mock) -> list[str]:
    """The GTFS-Realtime feeds read (not the static zips)."""
    return [str(c[1]) for c in _calls(aioclient_mock, "get") if str(c[1]).endswith(".pb")]


async def _train_hub(aioclient_mock, wall: float = TRAIN_WALL, clock: FakeClock | None = None):
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    clock = clock or FakeClock()
    hub = realtime.RealtimeHub(
        session, feeds={"mav": TABLE["mav"], "gysev": TABLE["gysev"]}, monotonic=clock.monotonic, wall=lambda: wall
    )
    return hub, session, clock


@pytest.mark.parametrize(
    ("numbers", "req", "expect"),
    [
        (
            {"741": ["36777783"]}, REQ_741,
            ("36777783", 60, TRAIN_DAY + 60900, 480, TRAIN_DAY + 71880, 47.493019),
        ),
        (
            {"3311": ["36322788"]}, REQ_3311,
            ("36322788", 120, TRAIN_DAY + 65340, 360, TRAIN_DAY + 71460, None),
        ),
    ],
    ids=["updated", "modified"],
)
async def test_a_train_is_answered_by_the_planner_and_no_feed_is_read(aioclient_mock, numbers, req, expect) -> None:
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, numbers)
    hub, session, _clock = await _train_hub(aioclient_mock)
    resp = await hub.query("mav", [req])
    await session.close()
    # Its trip ids by the MÁV static feed, today's run and last night's
    # (today's first), in one request; GYSEV's static feed has no such
    # train, so its feeds are not read either.
    (tid,) = numbers[req["train"]]
    assert planner.asks == [[("1:" + tid, TODAY), ("1:" + tid, YESTERDAY)]]
    assert _feed_gets(aioclient_mock) == []
    assert resp["feed"] == "mav" and resp["updated"] == TRAIN_WALL
    hit = resp["trips"][req["key"]]
    # The same shape as from the feeds.
    assert list(hit) == ["trip_id", "delay", "time", "dest_delay", "dest_time", "approx", "canceled", "skipped", "vehicle"]
    lat = hit["vehicle"]["lat"] if hit["vehicle"] else None
    assert (hit["trip_id"], hit["delay"], hit["time"], hit["dest_delay"], hit["dest_time"], lat) == expect
    assert (hit["approx"], hit["canceled"], hit["skipped"]) == (False, False, False)


def _without_call(trip: dict, station: str) -> dict:
    out = copy.deepcopy(trip)
    out["stoptimesForDate"] = [st for st in out["stoptimesForDate"] if not st["stop"]["gtfsId"].startswith("1:" + station)]
    return out


@pytest.mark.parametrize(
    ("stops", "picked"),
    [(["005518069"], "36777783"), (["005517848"], "36777783"), (["005510017"], None), ([], None)],
)
async def test_the_variants_of_a_train_number_are_told_apart_by_the_stops(aioclient_mock, stops, picked) -> None:
    """741 runs as two trips that day: the one calling at the card's stops
    is its row's, as on the feeds; neither is no answer, nor both when they
    differ there (at Budapest-Keleti, the terminus, one departs at 19:50
    and the other on its 19:58 arrival)."""
    runs = TrainPlanner().runs
    runs[IC741_MOD, TODAY] = _without_call(_without_call(TRAINS["t1"], "005518069"), "005517848")
    planner = TrainPlanner(runs)
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"741": ["36777783", "36391307"]})
    hub, session, _clock = await _train_hub(aioclient_mock)
    resp = await hub.query("mav", [{"key": "k", "train": "0741", "stops": stops, "from": 0}])
    await session.close()
    assert sorted(planner.asks[0]) == sorted((g, d) for g in (IC741, IC741_MOD) for d in (TODAY, YESTERDAY))
    assert resp["trips"].get("k", {}).get("trip_id") == picked
    assert _feed_gets(aioclient_mock) == []


MAV_JBB_REQ = {"key": "ic", "train": "822", "stops": ["005501024", "005506189"], "from": 0, "to": 1}


@pytest.mark.parametrize(
    "failure",
    [{"status": 502}, {"exc": asyncio.TimeoutError()}, {"json": {"errors": [{"message": "boom"}]}}, None],
    ids=["http", "timeout", "graphql-error", "knows-none"],
)
async def test_a_train_the_planner_cannot_answer_is_matched_on_the_feeds(aioclient_mock, failure) -> None:
    """test_realtime's MÁV feeds of 2026-11-02 and the static feed's train
    822: the answer the feeds gave before the planner came first."""
    if failure is None:
        aioclient_mock.post(mavplusz.URL, side_effect=TrainPlanner({}))
    else:
        aioclient_mock.post(mavplusz.URL, **failure)
    aioclient_mock.get(MAV_SRC.static, content=(FIX / "mav-static.zip").read_bytes())
    aioclient_mock.get(GYSEV_SRC.static, content=_static(GYSEV_OTHER))
    aioclient_mock.get(J + "1_198/trip-updates.pb", content=_pb("mav-trip-updates"))
    aioclient_mock.get(J + "1_198/vehicle-positions.pb", content=_pb("mav-vehicle-positions"))
    hub, session, _clock = await _train_hub(aioclient_mock, wall=JBB_WALL)
    resp = await hub.query("mav", [MAV_JBB_REQ])
    await session.close()
    assert len(_calls(aioclient_mock, "post")) == 1
    # MÁV's feeds; GYSEV's had nothing to ask the planner for.
    assert sorted(_feed_gets(aioclient_mock)) == [J + "1_198/trip-updates.pb", J + "1_198/vehicle-positions.pb"]
    hit = resp["trips"]["ic"]
    assert (hit["trip_id"], hit["delay"], hit["dest_delay"]) == ("35700507", 0, 600)
    assert hit["vehicle"]["lat"] == pytest.approx(46.50523, abs=1e-5)


async def test_without_the_static_train_numbers_the_feeds_are_read_as_before(aioclient_mock) -> None:
    """No train number can be resolved to a trip id, for the planner nor
    for the feeds: the feeds are read as they were, and once the static
    feed answers (after its backoff) the planner is asked."""
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, None, None)
    for path in ("1_198/trip-updates.pb", "1_198/vehicle-positions.pb", "1_134/trip-updates.pb", "1_134/vehicle-positions.pb"):
        aioclient_mock.get(J + path, status=503)
    hub, session, clock = await _train_hub(aioclient_mock)
    resp = await hub.query("mav", [REQ_3311])
    assert not planner.asks and len(_feed_gets(aioclient_mock)) == 4
    assert resp["trips"] == {}
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3311": ["36322788"]})
    clock.now += realtime.STATIC_FAIL_BACKOFF + 1
    resp = await hub.query("mav", [REQ_3311])
    await session.close()
    assert len(planner.asks) == 1 and _feed_gets(aioclient_mock) == []
    assert resp["trips"]["3311"]["dest_delay"] == 360


@pytest.mark.parametrize(
    ("at", "delay"),
    [(TRAIN_DAY + 86400 + 1800, 120), (TRAIN_DAY + 86400 + 5400, None)],
    ids=["00:30-on-its-way", "01:30-over"],
)
async def test_last_nights_train_is_answered_until_it_is_over(aioclient_mock, at, delay) -> None:
    """3311 five hours later, arriving at 00:51: past midnight the row
    (which sends no date) is last night's run until 15 minutes after its
    last call. The planner keeps a finished run's realtime data, which must
    not answer for today's run."""
    planner = TrainPlanner({(OS3311, TODAY): _later(TRAINS["t2"], 5 * 3600)})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3311": ["36322788"]})
    hub, session, _clock = await _train_hub(aioclient_mock, wall=at)
    resp = await hub.query("mav", [REQ_3311])
    await session.close()
    assert planner.asks == [[(OS3311, "20261119"), (OS3311, TODAY)]]
    assert _feed_gets(aioclient_mock) == []
    assert resp["trips"].get("3311", {}).get("delay") == delay
    if delay is not None:
        assert resp["trips"]["3311"]["time"] == TRAIN_DAY + 65340 + 5 * 3600


async def test_last_nights_runs_long_over_are_not_asked_every_minute(aioclient_mock) -> None:
    """Yesterday's run of 3311 ended a day ago: it no longer changes, so it
    is kept for hours while today's is asked again every interval."""
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3311": ["36322788"]})
    hub, session, clock = await _train_hub(aioclient_mock)
    await hub.query("mav", [REQ_3311])
    clock.now += TABLE["mav"].interval + 1
    resp = await hub.query("mav", [REQ_3311])
    clock.now += realtime.ROUTE_INTERVAL
    await hub.query("mav", [REQ_3311])
    await session.close()
    assert planner.asks == [
        [(OS3311, TODAY), (OS3311, YESTERDAY)], [(OS3311, TODAY)], [(OS3311, TODAY), (OS3311, YESTERDAY)]
    ]
    assert resp["trips"]["3311"]["delay"] == 120


async def test_gysev_answers_trains_and_trip_ids_from_the_planner(aioclient_mock) -> None:
    """GYSEV's own feed matches by trip id (its city cards) and by train
    number: both kinds of row in one planner request. (The fixture's MÁV
    trips stand in for GYSEV's: one planner feed, prefix 1:, for both.)"""
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    aioclient_mock.get(GYSEV_SRC.static, content=_static({"3311": ["36322788"]}))
    hub, session, _clock = await _train_hub(aioclient_mock)
    by_trip = {"key": "t", "trip_id": "36777783", "date": TODAY, "stops": ["005518069_0"], "from": 0}
    resp = await hub.query("gysev", [REQ_3311, by_trip])
    await session.close()
    assert len(planner.asks) == 1
    assert set(planner.asks[0]) == {(IC741, TODAY), (OS3311, TODAY), (OS3311, YESTERDAY)}
    assert _feed_gets(aioclient_mock) == []
    assert resp["trips"]["3311"]["delay"] == 120
    assert (resp["trips"]["t"]["trip_id"], resp["trips"]["t"]["delay"]) == ("36777783", 60)


async def test_train_rows_over_the_trip_cap_are_matched_on_the_feeds_until_asked(aioclient_mock) -> None:
    """30 trains, one trip id each, are 60 trips with last night's runs: 20
    trains go out whole (40 trips), the others are matched on the feeds,
    and the next call asks for the rest."""
    numbers = {str(1000 + n): [str(36000000 + n)] for n in range(30)}
    runs = {("1:" + ids[0], TODAY): _as(TRAINS["t2"], "1:" + ids[0]) for ids in numbers.values()}
    planner = TrainPlanner(runs)
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, numbers)
    aioclient_mock.get(J + "1_198/trip-updates.pb", content=_pb("mav-trip-updates"))
    aioclient_mock.get(J + "1_198/vehicle-positions.pb", content=_pb("mav-vehicle-positions"))
    hub, session, clock = await _train_hub(aioclient_mock)
    reqs = [{"key": num, "train": num, "stops": ["005513748"], "from": 0} for num in numbers]
    first = await hub.query("mav", reqs)
    (asked,) = planner.asks
    assert len(asked) == mavplusz.MAX_TRIPS_PER_REQUEST
    # Each train's two runs side by side, never one without the other.
    assert asked == [("1:" + numbers[num][0], d) for num in list(numbers)[:20] for d in (TODAY, YESTERDAY)]
    assert set(first["trips"]) == set(list(numbers)[:20])
    assert sorted(_feed_gets(aioclient_mock)) == [J + "1_198/trip-updates.pb", J + "1_198/vehicle-positions.pb"]
    clock.now += 30
    second = await hub.query("mav", reqs)
    await session.close()
    assert planner.asks[1] == [("1:" + numbers[num][0], d) for num in list(numbers)[20:] for d in (TODAY, YESTERDAY)]
    assert set(second["trips"]) == set(numbers)
    assert {hit["delay"] for hit in second["trips"].values()} == {120}


def test_a_trains_vehicle_listed_under_another_alias_is_its_live_runs() -> None:
    """The unedited answer: the vehicle of 741's trip came under t3 (that
    trip on the day before, no realtime), none under t0, today's live run.
    It goes to the live run, by the trip id it names."""
    keys = [mavplusz.TripKey(TRAINS_DOC["variables"][f"t{i}"], TRAINS_DOC["variables"][d], "1:") for i, d in enumerate(("d0", "d0", "d0", "d1"))]
    assert not TRAINS["t0"]["pattern"]["vehiclePositions"] and TRAINS["t3"]["pattern"]["vehiclePositions"]
    res = mavplusz.parse_response(TRAINS_DOC["response"], keys)
    v = res[keys[0]].vehicle
    assert (v.trip_id, v.start_date, v.lat, v.timestamp) == ("36777783", TODAY, 47.4930191, 1795027929)
    assert v.stop_id == "005510017_5" and v.status == 2
    # Not to the other trip of the pattern, nor to the run without realtime.
    assert res[keys[1]].vehicle is None and res[keys[3]][:2] == (None, None)
    # Alone, today's alias has no vehicle: it is not made up.
    alone = mavplusz.parse_response({"data": {"t0": TRAINS["t0"]}}, keys[:1])
    assert alone[keys[0]].trip_update is not None and alone[keys[0]].vehicle is None


@pytest.mark.parametrize(("lag", "day"), [(0, TODAY), (-86400, YESTERDAY), (-43200, TODAY)])
def test_a_vehicle_of_a_trip_live_on_two_days_goes_to_one_run(lag, day) -> None:
    """741's trip asked for today and the day before, both live (as last
    night's run still on its way past midnight would be): the vehicle goes
    to the run its timestamp falls in, else the nearest, today's on a tie."""
    today, yesterday = mavplusz.TripKey(IC741, TODAY, "1:"), mavplusz.TripKey(IC741, YESTERDAY, "1:")
    last_night = copy.deepcopy(TRAINS["t0"])
    for st in last_night["stoptimesForDate"]:
        st["serviceDay"] -= 86400
    doc = {"data": {"t0": copy.deepcopy(TRAINS["t0"]), "t1": last_night}}
    vp = copy.deepcopy(TRAINS["t3"]["pattern"]["vehiclePositions"][0])
    vp["lastUpdated"] += lag
    doc["data"]["t0"]["pattern"]["vehiclePositions"] = [vp]
    res = mavplusz.parse_response(doc, [today, yesterday])
    with_vehicle = [k.date for k in (today, yesterday) if res[k].vehicle is not None]
    assert with_vehicle == [day]
    assert res[today].trip_update is not None and res[yesterday].trip_update is not None


async def test_a_train_is_asked_only_for_the_trips_that_run_that_day(aioclient_mock) -> None:
    """741's two trips, of which the static calendar runs only 36777783 on
    both days: the other is not asked (the planner lists any day's
    scheduled calls, run or not, so its answer could not tell)."""
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"741": ["36777783", "36391307"]}, runs_on={"36777783": [YESTERDAY, TODAY], "36391307": ["20261121"]})
    hub, session, _clock = await _train_hub(aioclient_mock)
    resp = await hub.query("mav", [REQ_741])
    await session.close()
    assert planner.asks == [[(IC741, TODAY), (IC741, YESTERDAY)]]
    hit = resp["trips"]["741"]
    assert (hit["trip_id"], hit["delay"], hit["dest_delay"]) == ("36777783", 60, 480)
    assert hit["vehicle"]["lat"] == 47.493019
    assert _feed_gets(aioclient_mock) == []


@pytest.mark.parametrize(
    ("runs_on", "asked"),
    [
        (["20261102"], True),
        (["20261030", "20261105"], False),
        (["20261110"], True),
    ],
    ids=["runs-today", "runs-neither-day", "calendar-without-today"],
)
async def test_a_train_the_calendar_does_not_run_is_matched_on_the_feeds(aioclient_mock, runs_on, asked) -> None:
    """Train 822 is on the jbb MÁV feeds of 2026-11-02. A calendar whose
    days span today but run the trip on neither day: not asked of the
    planner, but matched on the feeds as before (they never read the
    calendar, and have the run: a calendar wrong, or a train added). One
    whose days do not reach today (a copy kept from an older download)
    says nothing of today: asked (the planner fails here), then the feeds."""
    aioclient_mock.post(mavplusz.URL, status=503)
    aioclient_mock.get(MAV_SRC.static, content=_static({"822": ["35700507"]}, {"35700507": runs_on}))
    aioclient_mock.get(GYSEV_SRC.static, content=_static(GYSEV_OTHER))
    aioclient_mock.get(MAV_SRC.trip_updates, content=_pb("mav-trip-updates"))
    aioclient_mock.get(MAV_SRC.vehicle_positions, content=_pb("mav-vehicle-positions"))
    hub, session, _clock = await _train_hub(aioclient_mock, wall=JBB_WALL)
    resp = await hub.query("mav", [MAV_JBB_REQ])
    await session.close()
    assert len(_calls(aioclient_mock, "post")) == (1 if asked else 0)
    assert _feed_gets(aioclient_mock) == [MAV_SRC.trip_updates, MAV_SRC.vehicle_positions]
    hit = resp["trips"]["ic"]
    assert (hit["trip_id"], hit["dest_delay"]) == ("35700507", 600)
    assert hit["vehicle"]["lat"] == 46.50523


@pytest.mark.parametrize(
    ("change", "picked"),
    [(None, "36777783"), ("no-vehicle", "36391307"), ("later", None)],
    ids=["twins", "twins-without-vehicle", "differ"],
)
async def test_one_train_listed_as_two_trips_is_answered(aioclient_mock, change, picked) -> None:
    """741's UPDATED and MODIFIED trips call at Békéscsaba and Kőbánya-
    Kispest with the same delays: one train, answered by the one with the
    vehicle, else the first by trip id. 5 minutes apart they are two."""
    runs = TrainPlanner().runs
    if change == "no-vehicle":
        runs[IC741, YESTERDAY] = _scheduled(TRAINS["t3"], YESTERDAY)
    elif change == "later":
        runs[IC741_MOD, TODAY] = _later(TRAINS["t1"], 300)
        for st in runs[IC741_MOD, TODAY]["stoptimesForDate"]:
            st["arrivalDelay"] += 300
            st["departureDelay"] += 300
    planner = TrainPlanner(runs)
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"741": ["36777783", "36391307"]})
    hub, session, _clock = await _train_hub(aioclient_mock)
    req = {"key": "741", "train": "741", "stops": ["005518069", "005517848"], "from": 0, "to": 1}
    resp = await hub.query("mav", [req])
    await session.close()
    assert resp["trips"].get("741", {}).get("trip_id") == picked
    if picked:
        hit = resp["trips"]["741"]
        assert (hit["delay"], hit["time"]) == (60, TRAIN_DAY + 60900)
        assert (hit["vehicle"] is not None) == (change is None)


async def test_a_train_with_more_trips_than_one_request_is_answered_by_todays(aioclient_mock) -> None:
    """A number with 25 trip ids is 50 trips with last night's: over one
    request, so today's go out first and last night's not at all; the
    train is answered by today's live run calling at the card's stop."""
    ids = [str(36000000 + n) for n in range(25)]
    planner = TrainPlanner({("1:" + ids[0], TODAY): _as(TRAINS["t2"], "1:" + ids[0])})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3311": ids})
    hub, session, _clock = await _train_hub(aioclient_mock)
    resp = await hub.query("mav", [REQ_3311])
    await session.close()
    (asked,) = planner.asks
    assert asked == [("1:" + t, TODAY) for t in ids] + [("1:" + t, YESTERDAY) for t in ids[:15]]
    assert _feed_gets(aioclient_mock) == []
    assert resp["trips"]["3311"]["delay"] == 120


async def test_an_unknown_or_callless_trip_is_not_asked_every_minute(aioclient_mock) -> None:
    """A trip the planner does not know, or one it lists without a call
    that day, will not change within the hour: asked again only after
    ROUTE_INTERVAL, while a live one is asked every interval."""
    callless = dict(_trip("t0"), gtfsId="hkir:hkir_empty_0-0", stoptimesForDate=[])
    planner = Planner({"hkir:hkir_empty_0-0": callless})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, clock = await _hub(aioclient_mock, _cfg())
    reqs = [
        T0_REQ,
        {"key": "nope", "trip_id": "hkir_nope_0-0", "date": "20261114"},
        {"key": "empty", "trip_id": "hkir_empty_0-0", "date": "20261114"},
    ]
    await hub.query("v", reqs)
    clock.now += 61
    resp = await hub.query("v", reqs)
    clock.now += realtime.ROUTE_INTERVAL
    await hub.query("v", reqs)
    await session.close()
    asked = [sorted(v for k, v in a.items() if k[0] == "t") for a in planner.asked]
    assert asked == [
        ["hkir:hkir_2036_1-3", "hkir:hkir_empty_0-0", "hkir:hkir_nope_0-0"],
        ["hkir:hkir_2036_1-3"],
        ["hkir:hkir_2036_1-3", "hkir:hkir_empty_0-0", "hkir:hkir_nope_0-0"],
    ]
    assert set(resp["trips"]) == {"a"} and not _calls(aioclient_mock, "get")


async def test_answers_kept_for_hours_cannot_grow_without_bound(aioclient_mock, monkeypatch) -> None:
    """Made-up trip ids are kept for hours (the planner does not know
    them): past MAX_MAV_ANSWERS the least recently asked go."""
    monkeypatch.setattr(realtime, "MAX_MAV_ANSWERS", 50)
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    # A planner that knows none of a call's trips: the feeds answer it.
    _jbb(aioclient_mock)
    hub, session, clock = await _hub(aioclient_mock, _cfg())
    for batch in range(4):
        reqs = [{"key": str(n), "trip_id": f"hkir_x{batch}_{n}-0", "date": "20261114"} for n in range(20)]
        await hub.query("v", reqs)
        clock.now += 1
    await hub.query("v", [T0_REQ])
    await session.close()
    assert len(hub._mav_cache) <= 50
    assert ("hkir:hkir_2036_1-3", "20261114") in hub._mav_cache
    assert ("hkir:hkir_x0_0-0", "20261114") not in hub._mav_cache


RT_DOC = json.loads((FIX / "mavplusz_rt_pattern.json").read_text(encoding="utf-8"))
RT = RT_DOC["response"]["data"]
# The rt fixture's request time, 21:52:33 CET.
RT_WALL = TRAIN_DAY + 78753
SZ3331 = "1:36322792"
# Train 3331: Cegléd (005513748) to Budapest-Keleti.
REQ_3331 = {"key": "3331", "train": "3331", "stops": ["005513748", "005510017"], "from": 0, "to": 1}


def _rt_keys() -> tuple[list[mavplusz.TripKey], list[str]]:
    v = RT_DOC["variables"]
    trips = [mavplusz.TripKey(v[f"t{i}"], v["d0"], "1:") for i in range(3)]
    return trips, [v[f"r{i}"] for i in range(4)]


def _rt_parts() -> tuple[dict, dict]:
    """The rt fixture's one answer as the two requests now made of it: the
    trips' (tN) and the vehicles' (rN)."""
    trips = {k: v for k, v in RT.items() if k[0] == "t"}
    routes = {k: v for k, v in RT.items() if k[0] == "r"}
    return {"data": trips}, {"data": routes}


def _rt_route(route: str) -> dict:
    """The rt fixture's route alias for a planner route id."""
    v = RT_DOC["variables"]
    return RT[next(k for k in v if k[0] == "r" and v[k] == route)]


def test_the_vehicle_query_asks_by_route_for_positions_without_vehicle_ids() -> None:
    trips, routes = _rt_keys()
    body = mavplusz.build_vehicle_query(routes)
    assert body["variables"] == {k: v for k, v in RT_DOC["variables"].items() if k[0] == "r"}
    assert "r3: route(id: $r3) { patterns { vehiclePositions {" in body["query"]
    assert "vehicleId" not in body["query"] and "label" not in body["query"]
    assert "trip(id:" not in body["query"]
    # The trips' request asks for no route's vehicles.
    trip_query = mavplusz.build_query(trips)["query"]
    assert "route(id:" not in trip_query and "pattern { code vehiclePositions {" in trip_query


def test_a_realtime_patterns_trips_get_their_vehicles_from_their_routes() -> None:
    """The live answer: the three MODIFIED trains' own patterns are
    realtime ones (::rt#) listing no vehicle; their vehicles are on their
    routes' scheduled patterns, each naming its trip."""
    trips, routes = _rt_keys()
    trip_doc, route_doc = _rt_parts()
    assert all(not RT[f"t{i}"]["pattern"]["vehiclePositions"] for i in range(3))
    res = mavplusz.parse_response(trip_doc, trips)
    assert [(r.route, r.rt_pattern, r.vehicle) for r in res.values()] == [
        ("1:V3775_106", True, None), ("1:V3775_106", True, None), ("1:V3803_106", True, None)
    ]
    assert all(mavplusz.needs_route_vehicles(r) for r in res.values())
    found = mavplusz.parse_vehicles(route_doc, routes)
    out = mavplusz.add_vehicles(res, [vp for ps in found.values() for vp in ps], RT_WALL)
    own = next(
        v for p in RT["r0"]["patterns"] for v in p["vehiclePositions"] if v["trip"]["gtfsId"] == SZ3331
    )
    v = out[trips[0]].vehicle
    assert (v.trip_id, v.route_id, v.lat, v.lon, v.timestamp) == (
        "36322792", "V3775_106", own["lat"], own["lon"], own["lastUpdated"]
    )
    assert all(r.vehicle is not None and r.vehicle.trip_id == k.gtfs_id[2:] for k, r in out.items())
    assert not any(mavplusz.needs_route_vehicles(r) for r in out.values())
    # The routes' sizes, as RouteSizes learns them: about 250 bytes a vehicle.
    sizes = mavplusz.answer_sizes(route_doc, routes)
    assert 200 < sizes["1:V3775_106"] / sum(len(p["vehiclePositions"]) for p in RT["r0"]["patterns"]) < 300
    # A route alias missing from the answer is not trusted; a null one is no vehicle.
    with pytest.raises(ValueError):
        mavplusz.parse_vehicles({"data": {}}, routes)
    nulls = mavplusz.parse_vehicles({"data": {f"r{i}": None for i in range(4)}}, routes)
    assert nulls == {r: [] for r in routes}
    assert mavplusz.add_vehicles(res, [], RT_WALL) == res


async def test_a_modified_trains_vehicle_comes_with_its_first_answer(aioclient_mock) -> None:
    """The trip request says its pattern is a realtime one; the vehicles of
    its route are asked right after, in a request of their own, so the
    train has its position from the first poll on. A trip on its scheduled
    pattern asks for no route."""
    planner = TrainPlanner(
        {(SZ3331, TODAY): RT["t0"], (IC741, TODAY): TRAINS["t0"], (IC741, YESTERDAY): TRAINS["t3"]},
        routes={"1:V3775_106": RT["r0"]},
    )
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": ["36322792"], "741": ["36777783"]})
    hub, session, clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    reqs = [REQ_3331, REQ_741]
    first = await hub.query("mav", reqs)
    clock.now += TABLE["mav"].interval + 1
    second = await hub.query("mav", reqs)
    await session.close()
    assert planner.route_asks == [["1:V3775_106"], ["1:V3775_106"]]
    assert len(planner.asks) == 2
    for answer in (first, second):
        hit = answer["trips"]["3331"]
        assert (hit["trip_id"], hit["delay"], hit["dest_delay"]) == ("36322792", 60, 240)
        assert hit["vehicle"]["lat"] == pytest.approx(47.4552, abs=1e-4)
    assert _feed_gets(aioclient_mock) == []


async def _rt_round(aioclient_mock, planner: TrainPlanner, *, polls: int = 1, step: float | None = None):
    """Polls train 3331 (MODIFIED, on a realtime pattern) polls times, step
    seconds apart (an interval by default): the answers, the hub."""
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": ["36322792"]})
    hub, session, clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    out = []
    for n in range(polls):
        if n:
            clock.now += step if step is not None else TABLE["mav"].interval + 1
        out.append(await hub.query("mav", [REQ_3331]))
    await session.close()
    return out, hub


@pytest.mark.parametrize("failure", [503, "slow", "too_large", "error"])
async def test_a_failing_vehicle_request_keeps_the_delays(aioclient_mock, monkeypatch, caplog, failure) -> None:
    """Whatever goes wrong with the vehicles' request (an error status, no
    answer within VEHICLE_TIMEOUT, an answer over VEHICLE_MAX_BYTES, a
    GraphQL error), the train has its delays from the planner, no vehicle,
    and nothing reads the feeds or backs the planner off: the next trip
    request goes out as usual. Logged once, at debug level."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})
    if failure == 503:
        planner.vehicles = 503
    elif failure == "slow":
        monkeypatch.setattr(realtime, "VEHICLE_TIMEOUT", 0.05)
        monkeypatch.setattr(realtime, "VEHICLE_MIN_TIMEOUT", 0.01)

        async def slow(_variables):
            await asyncio.sleep(1)
            return {"data": {"r0": RT["r0"]}}

        planner.vehicles = slow
    elif failure == "too_large":
        monkeypatch.setattr(mavplusz, "VEHICLE_MAX_BYTES", 500)

        async def big(_variables):
            return {"data": {"r0": RT["r0"]}}

        planner.vehicles = big
    else:
        async def broken(_variables):
            return {"errors": [{"message": "boom"}]}

        planner.vehicles = broken
    caplog.set_level(logging.DEBUG, logger="custom_components.hungarian_transport")
    answers, hub = await _rt_round(aioclient_mock, planner, polls=2)
    for answer in answers:
        hit = answer["trips"]["3331"]
        assert (hit["trip_id"], hit["delay"], hit["dest_delay"]) == ("36322792", 60, 240)
        assert hit["vehicle"] is None
    assert len(planner.asks) == 2 and _feed_gets(aioclient_mock) == []
    assert not hub._mav_rate.backing_off() and hub._mav_retry_at < 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert sum("gave no vehicles by route" in r.getMessage() for r in caplog.records) == 1
    # Either way the second poll asks no vehicles, for its own reason.
    assert planner.route_asks == [["1:V3775_106"]]
    if failure == "too_large":
        # Not backed off, but the route alone is over the cap now: left out.
        assert hub._vehicle_retry_at < 0
        assert hub._route_sizes.size("1:V3775_106") > mavplusz.HUGE_ROUTE_BYTES
    else:
        # Backed off (VEHICLE_FAIL_BACKOFF), the vehicles alone.
        assert hub._monotonic() < hub._vehicle_retry_at


async def test_vehicles_back_off_alone_and_come_back(aioclient_mock, caplog) -> None:
    """After a failed vehicle request none for VEHICLE_FAIL_BACKOFF, while
    the trips are asked every interval; then the vehicles come back, and
    that is logged once."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})
    planner.vehicles = 503
    caplog.set_level(logging.DEBUG, logger="custom_components.hungarian_transport")
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": ["36322792"]})
    hub, session, clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    await hub.query("mav", [REQ_3331])
    clock.now += realtime.VEHICLE_FAIL_BACKOFF - 1
    await hub.query("mav", [REQ_3331])
    assert len(planner.route_asks) == 1 and len(planner.asks) == 2
    planner.vehicles = None
    clock.now += TABLE["mav"].interval + 1
    hit = (await hub.query("mav", [REQ_3331]))["trips"]["3331"]
    await session.close()
    assert len(planner.route_asks) == 2 and len(planner.asks) == 3
    assert hit["vehicle"]["lat"] == pytest.approx(47.4552, abs=1e-4)
    assert sum("by route again" in r.getMessage() for r in caplog.records) == 1


async def test_a_vehicle_request_never_runs_into_the_rounds_deadline(aioclient_mock) -> None:
    """With less than VEHICLE_MIN_TIMEOUT of the round left after
    VEHICLE_MARGIN, the vehicles are not asked: the delays already
    answered must not be cut with them."""
    planner = TrainPlanner(routes={"1:V3775_106": RT["r0"]})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, _clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    trips, _routes = _rt_keys()
    res = mavplusz.parse_response(_rt_parts()[0], trips)
    loop = asyncio.get_running_loop()
    short = loop.time() + realtime.VEHICLE_MARGIN + realtime.VEHICLE_MIN_TIMEOUT / 2
    assert await hub._mav_vehicles(res, short) == res
    assert planner.route_asks == []
    got = await hub._mav_vehicles(res, loop.time() + realtime.PLANNER_TIMEOUT)
    await session.close()
    assert planner.route_asks == [["1:V3775_106", "1:V3803_106"]]
    assert got[trips[0]].vehicle is not None and got[trips[2]].vehicle is None


async def test_the_vehicles_have_their_own_share_of_the_planner_limit(aioclient_mock) -> None:
    """Their share used up, the trips are still asked and answered (with
    no vehicle) every interval; the share refills."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": ["36322792"]})
    clock = FakeClock()
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    limiter = mavplusz.PlannerLimiter(vehicle_burst=1, vehicle_rate=0.001, monotonic=clock.monotonic)
    hub = realtime.RealtimeHub(
        session, feeds={"mav": TABLE["mav"]}, monotonic=clock.monotonic, wall=lambda: RT_WALL, planner_limiter=limiter
    )
    first = await hub.query("mav", [REQ_3331])
    clock.now += TABLE["mav"].interval + 1
    second = await hub.query("mav", [REQ_3331])
    await session.close()
    assert len(planner.asks) == 2 and len(planner.route_asks) == 1
    assert first["trips"]["3331"]["vehicle"] is not None
    assert second["trips"]["3331"]["vehicle"] is None and second["trips"]["3331"]["delay"] == 60


def test_the_vehicle_share_is_taken_before_the_total() -> None:
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(burst=3, vehicle_burst=1, monotonic=clock.monotonic)
    delays, vehicles = limiter.client(RuntimeError), limiter.client(KeyError, vehicles=True)
    vehicles.take()
    with pytest.raises(KeyError):
        vehicles.take()
    # The refused vehicle request spent none of the total.
    delays.take()
    delays.take()
    with pytest.raises(RuntimeError):
        delays.take()


def test_route_sizes_keep_a_vehicle_request_under_its_cap() -> None:
    """Unseen routes count UNSEEN_ROUTE_BYTES, at most
    MAX_ROUTES_PER_REQUEST a request; an answer over the cap makes each of
    its routes its share of the cap, so the next request is smaller, and a
    route over the cap alone is left out until its size is forgotten. A
    route's answered size replaces that; the least recently asked first."""
    clock = FakeClock()
    sizes = mavplusz.RouteSizes(clock.monotonic)
    routes = [f"1:R{i}" for i in range(30)]
    picked = sizes.pick(routes)
    assert picked == routes[: mavplusz.MAX_ROUTES_PER_REQUEST]
    assert len(picked) * mavplusz.UNSEEN_ROUTE_BYTES <= mavplusz.VEHICLE_BUDGET_BYTES
    sizes.asked(picked, too_large=True)
    # The unseen ten first, then the shrunk ones within the budget.
    again = sizes.pick(picked)
    share = mavplusz.VEHICLE_MAX_BYTES // len(picked) + 1
    assert len(again) == mavplusz.VEHICLE_BUDGET_BYTES // share < len(picked)
    assert sizes.pick(routes)[:10] == routes[20:]
    # One route alone over the cap: never asked while its size is known.
    sizes.asked(["1:R0"], too_large=True)
    assert sizes.size("1:R0") > mavplusz.HUGE_ROUTE_BYTES and "1:R0" not in sizes.pick(["1:R0", "1:R1"])
    # An answer that came back gives the routes their real size.
    sizes.asked(["1:R1", "1:R2"], {"1:R1": 1500, "1:R2": 900})
    assert (sizes.size("1:R1"), sizes.size("1:R2")) == (1500, 900)
    clock.now += mavplusz.ROUTE_SIZE_TTL
    assert sizes.size("1:R0") == mavplusz.UNSEEN_ROUTE_BYTES and "1:R0" in sizes.pick(["1:R0"])
    # Bounded.
    many = mavplusz.RouteSizes(clock.monotonic)
    for i in range(mavplusz.MAX_ROUTE_SIZES + 10):
        many.asked([f"x{i}"], {f"x{i}": 1})
    assert len(many._sizes) == mavplusz.MAX_ROUTE_SIZES


def test_a_full_vehicle_request_of_busy_routes_fits_its_budget() -> None:
    """MAX_ROUTES_PER_REQUEST routes of 30 vehicles each (the fixture's
    positions), more than any route had at night: within the budget, which
    is under the vehicles' cap, itself far under the trips'."""
    route = {"patterns": [{"vehiclePositions": [RT["r0"]["patterns"][0]["vehiclePositions"][0]] * 30}]}
    answer = {"data": {f"r{i}": route for i in range(mavplusz.MAX_ROUTES_PER_REQUEST)}}
    assert len(json.dumps(answer).encode()) < mavplusz.VEHICLE_BUDGET_BYTES
    assert mavplusz.VEHICLE_BUDGET_BYTES < mavplusz.VEHICLE_MAX_BYTES < mavplusz.MAX_RESPONSE_BYTES


BOARD = 40
POLL = 40


def _board(jbb: bool):
    """A board of BOARD trains as the review simulated it: each number has
    two trips running both days (identical twins, as 741's) and a third of
    another weekday the static calendar leaves out; every trip runs live
    today (3311's realtime) and was over yesterday."""
    numbers = {str(2000 + n): [str(37000000 + 3 * n + j) for j in range(3)] for n in range(BOARD)}
    runs_on = {}
    runs = {}
    for ids in numbers.values():
        for tid in ids[:2]:
            runs_on[tid] = [YESTERDAY, TODAY]
            runs["1:" + tid, TODAY] = _as(TRAINS["t2"], "1:" + tid)
        runs_on[ids[2]] = ["20261122"]
    return numbers, runs_on, runs


@pytest.mark.parametrize("jbb", [True, False], ids=["feeds-up", "feeds-down"])
async def test_a_big_board_is_answered_by_the_planner_poll_after_poll(aioclient_mock, jbb) -> None:
    """The review's case: on a 40-train board every poll asked 40 trips
    whose answer was thrown away and no train was the planner's, so with
    the feeds down there was no delay at all. Now the trains go out whole,
    the least recently asked first, a train left for a later request keeps
    its last answer meanwhile, and the runs that do not run or no longer
    change are not asked every minute: after the first few polls every
    train is answered by the planner on every poll, each asked again
    within a few polls, in at most one request of at most 40 trips."""
    numbers, runs_on, runs = _board(jbb)
    planner = TrainPlanner(runs)
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, numbers, runs_on=runs_on)
    for path in ("1_198/trip-updates.pb", "1_198/vehicle-positions.pb", "1_134/trip-updates.pb", "1_134/vehicle-positions.pb"):
        if jbb and path.startswith("1_198"):
            aioclient_mock.get(J + path, content=_pb("mav-" + path.split("/")[1].removesuffix(".pb")))
        else:
            aioclient_mock.get(J + path, status=503)
    clock = FakeClock()
    start = clock.now
    walls = [TRAIN_WALL]
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    hub = realtime.RealtimeHub(
        session, feeds={"mav": TABLE["mav"], "gysev": TABLE["gysev"]}, monotonic=clock.monotonic, wall=lambda: walls[0]
    )
    reqs = [{"key": num, "train": num, "stops": ["005513748", "005510017"], "from": 0, "to": 1} for num in numbers]
    answered: list[int] = []
    asks_per_poll: list[int] = []
    feeds_per_poll: list[int] = []
    last_asked: dict[str, float] = {}
    gaps: list[tuple[int, float]] = []
    by_id = {"1:" + t: num for num, ids in numbers.items() for t in ids}
    for poll in range(6 * 60 // POLL + 1):
        before_asks, before_feeds = len(planner.asks), len(_feed_gets(aioclient_mock))
        resp = await hub.query("mav", reqs)
        assert "error" not in resp
        asks_per_poll.append(len(planner.asks) - before_asks)
        feeds_per_poll.append(len(_feed_gets(aioclient_mock)) - before_feeds)
        for ask in planner.asks[before_asks:]:
            assert len(ask) <= mavplusz.MAX_TRIPS_PER_REQUEST
            for gid, _d in ask:
                num = by_id[gid]
                if num in last_asked and last_asked[num] != clock.now:
                    gaps.append((poll, clock.now - last_asked[num]))
                last_asked[num] = clock.now
        answered.append(sum(1 for hit in resp["trips"].values() if (hit["delay"], hit["dest_delay"]) == (120, 360)))
        clock.now += POLL
        walls[0] += POLL
    await session.close()
    asked = {pair for ask in planner.asks for pair in ask}
    # The trips of another weekday were never asked, last night's once each.
    assert all(gid != "1:" + ids[2] for gid, _d in asked for ids in numbers.values())
    assert sum(1 for ask in planner.asks for _g, d in ask if d == YESTERDAY) == 2 * BOARD
    assert max(asks_per_poll) == 1
    # Four polls to ask every train once (10 a request with last night's
    # runs), then every train is the planner's on every poll.
    assert answered[4:] == [BOARD] * (len(answered) - 4)
    assert answered[0] == 10 and answered == sorted(answered)
    assert sum(feeds_per_poll[4:]) == 0
    # Every train is asked again within two polls once the rotation has
    # settled (all asked once, then last night's runs dropped out), and
    # within the time a left-out answer is kept (MAV_STALE_INTERVALS) always.
    interval = TABLE["mav"].interval
    assert max(gap for poll, gap in gaps if poll > 5) <= 2 * POLL
    assert max(gap for _poll, gap in gaps) < interval * (1 + realtime.MAV_STALE_INTERVALS)
    assert set(last_asked) == set(numbers)
    assert clock.now - start == len(answered) * POLL


async def test_the_service_asks_the_planner_for_a_train(hass, aioclient_mock, freezer) -> None:
    freezer.move_to("2026-11-18T18:52:45+00:00")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3311": ["36322788"]})
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_REALTIME, {"feed": "mav", "trips": [REQ_3311]}, blocking=True, return_response=True
    )
    assert (resp["trips"]["3311"]["delay"], resp["trips"]["3311"]["dest_delay"]) == (120, 360)
    assert len(planner.asks) == 1 and _feed_gets(aioclient_mock) == []


# ---------------------------------------------------------------- review 2

B_REQ = {"key": "b", "trip_id": "hkir:hkir_2511_15-6"}


def _limited_hub(aioclient_mock, feeds: dict, wall: float, clock: FakeClock, **limits):
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic, **limits)
    hub = realtime.RealtimeHub(
        session, feeds=feeds, monotonic=clock.monotonic, wall=lambda: wall, planner_limiter=limiter
    )
    return hub, session


async def test_a_refused_request_keeps_the_trains_already_answered(aioclient_mock) -> None:
    """3311 answered a few seconds ago, 741 due: the request for 741 is
    refused by the rate limit. 3311 is still the planner's (cached), only
    741 is matched on the feeds, and the call is no outage."""
    for src in TABLE["mav"].sources:
        aioclient_mock.get(src.trip_updates, status=503)
        aioclient_mock.get(src.vehicle_positions, status=503)
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"741": ["36777783"], "3311": ["36322788"]})
    clock = FakeClock()
    hub, session = _limited_hub(aioclient_mock, {"mav": TABLE["mav"]}, TRAIN_WALL, clock, burst=1, rate=0.0001)
    await hub.query("mav", [REQ_3311])
    clock.now += 5
    resp = await hub.query("mav", [REQ_3311, REQ_741])
    await session.close()
    assert len(planner.asks) == 1
    assert "error" not in resp and set(resp["trips"]) == {"3311"}
    assert (resp["trips"]["3311"]["delay"], resp["trips"]["3311"]["dest_delay"]) == (120, 360)
    assert _feed_gets(aioclient_mock) == [MAV_SRC.trip_updates, MAV_SRC.vehicle_positions]


async def test_a_trip_id_not_asked_this_time_reads_the_feeds(aioclient_mock) -> None:
    """The same on a trip_id feed: the row the refused request was for is
    matched on the feeds (a trip id the planner was not asked about is no
    trip it lacks), the cached one stays the planner's."""
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _jbb(aioclient_mock)
    cfg = _cfg()
    clock = FakeClock()
    hub, session = _limited_hub(aioclient_mock, {"v": cfg}, WALL, clock, burst=1, rate=0.0001)
    await hub.query("v", [T0_REQ])
    clock.now += 5
    resp = await hub.query("v", [T0_REQ, B_REQ])
    await session.close()
    assert len(planner.asked) == 1
    assert "error" not in resp and resp["trips"]["a"]["delay"] == 103
    assert len(_calls(aioclient_mock, "get")) == 2


async def test_concurrent_cards_do_not_wait_for_each_others_vehicles(aioclient_mock, monkeypatch) -> None:
    """Three cards poll at once, each for a MODIFIED train whose vehicle is
    asked by its route, and the vehicle request is slow. It is made outside
    the trip requests' lock, so every card's trip request begins in time
    (with half of the round left: PLANNER_MIN_SHARE) and none falls back to
    the feeds; each train has its vehicle. 3331 and 3332 run on one route:
    it is asked once, the later card waits for that request. Timing scaled
    down: a 2 s round."""
    monkeypatch.setattr(realtime, "PLANNER_TIMEOUT", 2.0)
    monkeypatch.setattr(realtime, "VEHICLE_TIMEOUT", 0.9)
    monkeypatch.setattr(realtime, "VEHICLE_MARGIN", 0.05)
    monkeypatch.setattr(realtime, "VEHICLE_MIN_TIMEOUT", 0.05)
    for src in TABLE["mav"].sources:
        aioclient_mock.get(src.trip_updates, status=503)
        aioclient_mock.get(src.vehicle_positions, status=503)
    ids = {"3331": SZ3331, "3332": "1:36322546", "3333": "1:36226466"}
    planner = TrainPlanner({(ids[n], TODAY): RT[f"t{i}"] for i, n in enumerate(ids)})

    async def vehicles(variables):
        await asyncio.sleep(0.6)
        return {"data": {k: _rt_route(v) for k, v in variables.items()}}

    planner.vehicles = vehicles
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {n: [g[2:]] for n, g in ids.items()})
    hub, session, _clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    await hub._trains(MAV_SRC)
    await hub._trains(GYSEV_SRC)
    reqs = [{**REQ_3331, "key": n, "train": n} for n in ids]
    answers = await asyncio.gather(*(hub.query("mav", [r]) for r in reqs))
    await session.close()
    assert len(planner.asks) == 3 and planner.route_asks == [["1:V3775_106"], ["1:V3803_106"]]
    assert _feed_gets(aioclient_mock) == []
    for n, answer in zip(ids, answers):
        hit = answer["trips"][n]
        assert hit["trip_id"] == ids[n][2:] and hit["vehicle"] is not None


async def test_a_card_answered_from_the_cache_waits_for_the_vehicle_being_asked(aioclient_mock) -> None:
    """Two cards of the same train at once: one trip request, one vehicle
    request, and the card whose answer came from the cache while the
    vehicles were being asked gets the vehicle too, as does the cache."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})

    async def vehicles(variables):
        await asyncio.sleep(0.2)
        return {"data": {k: _rt_route(v) for k, v in variables.items()}}

    planner.vehicles = vehicles
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": ["36322792"]})
    hub, session, _clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    answers = await asyncio.gather(hub.query("mav", [REQ_3331]), hub.query("mav", [{**REQ_3331, "key": "x"}]))
    later = await hub.query("mav", [REQ_3331])
    await session.close()
    assert len(planner.asks) == 1 and len(planner.route_asks) == 1
    for hit in (answers[0]["trips"]["3331"], answers[1]["trips"]["x"], later["trips"]["3331"]):
        assert hit["delay"] == 60 and hit["vehicle"]["lat"] == pytest.approx(47.4552, abs=1e-4)
    assert not hub._mav_vehicle_waits


@pytest.mark.parametrize("status", [["IN_TRANSIT_TO"], {"a": 1}, 2, None])
def test_a_malformed_vehicle_status_is_no_status(status) -> None:
    """A list or an object as stopRelationship.status (unhashable) is no
    status, not a failed answer that would back the planner off."""
    v = TRAINS_DOC["variables"]
    keys = [mavplusz.TripKey(v[f"t{i}"], v[d], "1:") for i, d in enumerate(("d0", "d0", "d0", "d1"))]
    doc = copy.deepcopy(TRAINS_DOC["response"])
    doc["data"]["t3"]["pattern"]["vehiclePositions"][0]["stopRelationship"]["status"] = status
    res = mavplusz.parse_response(doc, keys)
    vehicles = [r.vehicle for r in res.values() if r is not None and r.vehicle is not None]
    assert vehicles and all(veh.status is None for veh in vehicles)


def test_only_a_real_day_is_a_service_date() -> None:
    from custom_components.hungarian_transport import REALTIME_TRIP_SCHEMA
    import voluptuous as vol

    assert mavplusz.service_date("20261340", WALL) == mavplusz.service_date(None, WALL) == "20261114"
    assert mavplusz.service_date("20260229", WALL) == "20261114" and mavplusz.service_date("20280229", WALL) == "20280229"
    assert REALTIME_TRIP_SCHEMA({"key": "k", "date": "20261118"})["date"] == "20261118"
    # Eight digits that are no day are that row's business (the hub never
    # asks the planner for one), not the whole call's.
    for odd in ("20261340", "20261131", "00000101"):
        assert REALTIME_TRIP_SCHEMA({"key": "k", "date": odd})["date"] == odd
    for bad in ("2026111", "2026-11-18", "202611180"):
        with pytest.raises(vol.Invalid):
            REALTIME_TRIP_SCHEMA({"key": "k", "date": bad})


async def test_a_trip_id_that_is_only_the_prefix_is_not_asked(aioclient_mock) -> None:
    """Nor one whose date is no day (the action takes any eight digits):
    the planner would answer some other day's run, or an error. The rest
    of the call is answered, and no feed is read for that row."""
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, _clock = await _hub(aioclient_mock, _cfg())
    resp = await hub.query("v", [T0_REQ, {"key": "p", "trip_id": "hkir:"}, {"key": "d", "trip_id": "hkir:hkir_2511_15-6", "date": "20261340"}])
    await session.close()
    assert planner.asked == [{"d0": "20261114", "t0": "hkir:hkir_2036_1-3"}]
    assert set(resp["trips"]) == {"a"} and _calls(aioclient_mock, "get") == []


def _alias_error(alias: str) -> dict:
    return {"message": "Exception while fetching data", "path": [alias], "extensions": {"classification": "DataFetchingException"}}


def test_an_error_of_one_alias_is_no_answer_for_that_trip_alone() -> None:
    doc = copy.deepcopy(ANSWER)
    doc["data"]["t1"] = None
    doc["errors"] = [_alias_error("t1"), {**_alias_error("t2"), "path": ["t2", "stoptimesForDate", 0]}]
    res = mavplusz.parse_response(doc, KEYS)
    assert set(res) == {KEYS[0], KEYS[3]}
    assert res[KEYS[0]].trip_update is not None and res[KEYS[3]] is None
    # Not tied to an alias of the request: the whole answer is not trusted.
    for err in ({"message": "x"}, _alias_error("t9"), {"message": "x", "path": [0]}, "boom"):
        with pytest.raises(ValueError):
            mavplusz.parse_response({**doc, "errors": [_alias_error("t1"), err]}, KEYS)
    # The route lists and the vehicles by route likewise.
    rkeys = [mavplusz.RouteKey("VBUSZ:2", "20261114", "VBUSZ:"), mavplusz.RouteKey("VBUSZ:3", "20261114", "VBUSZ:")]
    routes = mavplusz.parse_routes({"data": {"r0": None, "r1": None}, "errors": [_alias_error("r0")]}, rkeys)
    assert routes == {rkeys[1]: None}
    found = mavplusz.parse_vehicles({"data": {"r0": None, "r1": RT["r0"]}, "errors": [_alias_error("r0")]}, ["a", "1:V3775_106"])
    assert found["a"] == [] and found["1:V3775_106"]
    assert mavplusz.answer_sizes({"data": {"r1": RT["r0"]}}, ["a", "1:V3775_106"]).keys() == {"1:V3775_106"}


async def test_an_error_for_one_trip_does_not_back_the_planner_off(aioclient_mock, caplog) -> None:
    """The planner answers one alias with an error (a DataFetchingException):
    the other trips are its answers, that row is matched on the feeds, and
    the planner is not backed off: the next poll asks again."""
    planner = Planner()

    async def answer(method, url, data):
        resp = await planner(method, url, data)
        doc = json.loads(resp._response)
        doc["data"]["t1"] = None
        doc["errors"] = [_alias_error("t1")]
        return AiohttpClientMockResponse(method, url, json=doc)

    aioclient_mock.post(mavplusz.URL, side_effect=answer)
    _jbb(aioclient_mock)
    hub, session, clock = await _hub(aioclient_mock, _cfg())
    first = await hub.query("v", [T0_REQ, B_REQ])
    clock.now += 61
    await hub.query("v", [T0_REQ, B_REQ])
    await session.close()
    assert len(planner.asked) == 2 and hub._mav_retry_at < 0 and not hub._mav_rate.backing_off()
    assert first["trips"]["a"]["delay"] == 103 and "error" not in first
    assert len(_calls(aioclient_mock, "get")) == 2
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "mavplusz" in r.getMessage()]


async def test_a_trip_not_departed_yet_has_no_vehicle_request(aioclient_mock) -> None:
    """A MODIFIED train on a realtime pattern whose first call is more than
    VEHICLE_LEAD ahead has no vehicle to ask its route for; within it, it is
    asked as before."""
    trips, _routes = _rt_keys()
    res = mavplusz.parse_response(_rt_parts()[0], trips)[trips[0]]
    assert res.starts == RT_WALL - 6273 and res.ends == RT_WALL + 1287
    assert mavplusz.needs_route_vehicles(res) and mavplusz.needs_route_vehicles(res, res.starts - 299)
    assert not mavplusz.needs_route_vehicles(res, res.starts - mavplusz.VEHICLE_LEAD - 1)
    for lead, asked in ((mavplusz.VEHICLE_LEAD + 1, 0), (mavplusz.VEHICLE_LEAD - 1, 1)):
        aioclient_mock.clear_requests()
        planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})
        aioclient_mock.post(mavplusz.URL, side_effect=planner)
        _statics(aioclient_mock, {"3331": ["36322792"]})
        hub, session, _clock = await _train_hub(aioclient_mock, wall=res.starts - lead)
        hit = (await hub.query("mav", [REQ_3331]))["trips"]["3331"]
        await session.close()
        assert len(planner.route_asks) == asked and hit["delay"] == 60


@pytest.mark.parametrize("case", ["planner-hangs", "planner-answers", "static-slower-than-the-round"])
async def test_a_cold_static_feed_counts_in_the_planner_round(aioclient_mock, monkeypatch, loop_clock, case) -> None:
    """After a restart the static train numbers are downloaded first. That
    download is part of the planner round (PLANNER_TIMEOUT), so a slow one
    and a hung planner take one round, not one after the other; a planner
    that answers in the rest of the round answers as before, and a static
    feed slower than the round leaves the trains to the feeds, which wait
    for that same download. Timing scaled down: a 1 s round, on the
    virtual loop clock (loop_clock), so the seconds are exact."""
    monkeypatch.setattr(realtime, "PLANNER_TIMEOUT", 1.0)
    slow = 1.2 if case == "static-slower-than-the-round" else 0.4

    async def static(method, url, data):
        await asyncio.sleep(slow)
        return AiohttpClientMockResponse(method, url, response=_static({"822": ["35700507"]}))

    async def hung(method, url, data):
        await asyncio.sleep(5)
        return AiohttpClientMockResponse(method, url, status=503)

    if case == "planner-answers":
        aioclient_mock.post(mavplusz.URL, side_effect=TrainPlanner({("1:35700507", "20261102"): TRAINS["t0"]}))
    else:
        aioclient_mock.post(mavplusz.URL, side_effect=hung)
    aioclient_mock.get(MAV_SRC.static, side_effect=static)
    aioclient_mock.get(GYSEV_SRC.static, content=_static(GYSEV_OTHER))
    aioclient_mock.get(MAV_SRC.trip_updates, content=_pb("mav-trip-updates"))
    aioclient_mock.get(MAV_SRC.vehicle_positions, content=_pb("mav-vehicle-positions"))
    hub, session, _clock = await _train_hub(aioclient_mock, wall=JBB_WALL)
    began = loop_clock.now
    resp = await hub.query("mav", [MAV_JBB_REQ])
    took = loop_clock.now - began
    await session.close()
    posts = len(_calls(aioclient_mock, "post"))
    hit = resp["trips"].get("ic")
    if case == "planner-hangs":
        # One round (1 s) for the download and the planner together: the
        # request begun after the 0.4 s download is cut at the round's end.
        assert took == pytest.approx(realtime.PLANNER_TIMEOUT) and posts == 1 and _feed_gets(aioclient_mock)
        assert hit["trip_id"] == "35700507" and hit["dest_delay"] == 600
    elif case == "planner-answers":
        # The download, then the planner's answer at once; no feed read.
        assert took == pytest.approx(slow) and posts == 1 and _feed_gets(aioclient_mock) == []
        assert hit["trip_id"] == "35700507" and hit["delay"] == TRAINS["t0"]["stoptimesForDate"][0]["departureDelay"]
    else:
        # The round over at 1 s with the numbers not loaded: no request;
        # the feeds wait for the same download, over at 1.2 s.
        assert took == pytest.approx(slow) and posts == 0
        assert hit["trip_id"] == "35700507" and hit["dest_delay"] == 600


# ---------------------------------------------------------------- review 3

SZ3332 = "1:36322546"


async def test_a_card_with_trips_of_its_own_waits_for_the_route_being_asked(aioclient_mock) -> None:
    """Card A is in its vehicle request for route V3775_106 (train 3331)
    when card B asks for 3331 (from the cache) and 3332, new, on the same
    route. B does not ask that route again: it waits for A's request, whose
    vehicles go to both of B's trips, cached one too, and to the cache."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"], (SZ3332, TODAY): RT["t1"]})

    async def vehicles(variables):
        await asyncio.sleep(0.3)
        return {"data": {k: _rt_route(v) for k, v in variables.items()}}

    planner.vehicles = vehicles
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]], "3332": [SZ3332[2:]]})
    hub, session, _clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    await hub._trains(MAV_SRC)
    await hub._trains(GYSEV_SRC)
    a = asyncio.ensure_future(hub.query("mav", [REQ_3331]))
    await asyncio.sleep(0.1)
    assert hub._mav_vehicle_waits
    b = await hub.query("mav", [{**REQ_3331, "key": "b31"}, {**REQ_3331, "key": "b32", "train": "3332"}])
    ra = await a
    await session.close()
    assert planner.route_asks == [["1:V3775_106"]] and len(planner.asks) == 2
    for hit in (ra["trips"]["3331"], b["trips"]["b31"], b["trips"]["b32"]):
        assert hit["vehicle"] is not None
    assert b["trips"]["b32"]["trip_id"] == SZ3332[2:]
    for gid in (SZ3331, SZ3332):
        assert hub._mav_cache[(gid, TODAY)].result.vehicle is not None
    assert not hub._mav_vehicle_waits


async def test_a_route_answer_gives_the_cached_trips_on_it_their_vehicles(aioclient_mock) -> None:
    """3331's vehicle request was refused (the vehicles' share used up), so
    its cached answer has no vehicle. A few seconds later 3332, on the same
    route, is asked: that route's answer gives the cached 3331 its vehicle
    too, in the answer and in the cache."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"], (SZ3332, TODAY): RT["t1"]}, routes={"1:V3775_106": RT["r0"]})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]], "3332": [SZ3332[2:]]})
    clock = FakeClock()
    hub, session = _limited_hub(
        aioclient_mock, {"mav": TABLE["mav"]}, RT_WALL, clock, vehicle_burst=1, vehicle_rate=1.0
    )
    assert hub._mav_vehicle_rate.limiter.take(vehicles=True)
    first = await hub.query("mav", [REQ_3331])
    assert first["trips"]["3331"]["vehicle"] is None and planner.route_asks == []
    clock.now += 2
    second = await hub.query("mav", [REQ_3331, {**REQ_3331, "key": "3332", "train": "3332"}])
    await session.close()
    assert planner.route_asks == [["1:V3775_106"]] and len(planner.asks) == 2
    assert second["trips"]["3331"]["vehicle"]["lat"] == pytest.approx(47.4552, abs=1e-4)
    assert second["trips"]["3332"]["vehicle"] is not None
    assert hub._mav_cache[(SZ3331, TODAY)].result.vehicle is not None


def test_add_vehicles_skips_a_trip_starting_later() -> None:
    """A route answer gives no vehicle to a trip of the batch whose first
    call is over VEHICLE_LEAD ahead (a position naming its trip id is that
    id's run of another day), as needs_route_vehicles at the round's time."""
    trips, routes = _rt_keys()
    res = mavplusz.parse_response(_rt_parts()[0], trips)
    found = mavplusz.parse_vehicles(_rt_parts()[1], routes)
    positions = [vp for ps in found.values() for vp in ps]
    later = {trips[0]: res[trips[0]], trips[1]: res[trips[1]]._replace(starts=RT_WALL + 7200)}
    got = mavplusz.add_vehicles(later, positions, RT_WALL)
    assert got[trips[0]].vehicle is not None and got[trips[1]].vehicle is None


async def test_a_trip_starting_later_gets_no_vehicle_from_another_trips_route_request(aioclient_mock) -> None:
    """3331 is live, 3332 (same route) starts two hours later: the route is
    asked for 3331 alone, and its answer gives 3332 no vehicle."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"], (SZ3332, TODAY): _later(RT["t1"], 7200)})
    planner.routes = {"1:V3775_106": RT["r0"]}
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]], "3332": [SZ3332[2:]]})
    hub, session, _clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    resp = await hub.query("mav", [REQ_3331, {**REQ_3331, "key": "3332", "train": "3332"}])
    await session.close()
    assert planner.route_asks == [["1:V3775_106"]]
    assert resp["trips"]["3331"]["vehicle"] is not None
    assert hub._mav_cache[(SZ3332, TODAY)].result.vehicle is None
    assert resp["trips"].get("3332", {}).get("vehicle") is None


def test_a_run_over_needs_no_vehicle() -> None:
    """Last night's run, over for MAX_POSITION_AGE, needs no vehicle and
    gets none from a route answer, though its trip id names today's."""
    trips, routes = _rt_keys()
    res = mavplusz.parse_response(_rt_parts()[0], trips)[trips[0]]
    old = res._replace(starts=res.starts - 86400, ends=res.ends - 86400)
    key = trips[0]._replace(date=YESTERDAY)
    assert mavplusz.needs_route_vehicles(old) and not mavplusz.needs_route_vehicles(old, RT_WALL)
    # Still on its way, MAX_POSITION_AGE after its last call: it does.
    assert mavplusz.needs_route_vehicles(old, old.ends + realtime.MAX_POSITION_AGE)
    positions = [vp for ps in mavplusz.parse_vehicles(_rt_parts()[1], routes).values() for vp in ps]
    assert mavplusz.add_vehicles({key: old}, positions, RT_WALL)[key].vehicle is None


def _yesterdays(trip: dict) -> dict:
    """The run a day earlier: every call's service day one day back."""
    out = copy.deepcopy(trip)
    for st in out["stoptimesForDate"]:
        st["serviceDay"] -= 86400
    return out


async def test_last_nights_finished_run_gets_no_vehicle_of_todays(aioclient_mock) -> None:
    """3331 asked for yesterday: that run ended a day ago but the planner
    keeps its realtime calls. It needs no vehicle, so its route is not
    asked; a route answer would give it today's run's vehicle (matched by
    trip id alone), kept with the answer for hours."""
    planner = TrainPlanner({(SZ3331, YESTERDAY): _yesterdays(RT["t0"])}, routes={"1:V3775_106": RT["r0"]})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]]})
    hub, session, _clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    resp = await hub.query("mav", [{**REQ_3331, "date": YESTERDAY}])
    await session.close()
    assert planner.asks == [[(SZ3331, YESTERDAY)]] and planner.route_asks == []
    assert hub._mav_cache[(SZ3331, YESTERDAY)].result.vehicle is None
    assert resp["trips"].get("3331", {}).get("vehicle") is None


async def test_route_lists_kept_for_hours_cannot_grow_without_bound(aioclient_mock, monkeypatch) -> None:
    """Made-up routes are kept for hours (as None: the planner does not
    know them): past MAX_MAV_ROUTES the least recently asked go, not the
    route a card asks every poll."""
    monkeypatch.setattr(realtime, "MAX_MAV_ROUTES", 50, raising=False)
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    aioclient_mock.get(VB + "trip-updates.pb", content=_pb("veszprem-trip-updates"))
    aioclient_mock.get(VB + "vehicle-positions.pb", content=_pb("veszprem-vehicle-positions"))
    hub, session, clock = await _hub(aioclient_mock, _city_cfg("VBUSZ:"), wall=CITY_WALL)
    await hub.query("v", [VB_REQ])
    for batch in range(4):
        made_up = [{**VB_REQ, "key": f"x{n}", "route": f"x{batch}_{n}", "start": 1} for n in range(20)]
        await hub.query("v", [VB_REQ, *made_up])
        clock.now += 1
    await session.close()
    assert len(hub._mav_route_cache) <= 50
    assert ("VBUSZ:2", "20261114") in hub._mav_route_cache
    assert ("VBUSZ:x0_0", "20261114") not in hub._mav_route_cache
    assert ("VBUSZ:x3_19", "20261114") in hub._mav_route_cache
    # Route 2 was listed once, for every poll.
    assert sum("VBUSZ:2" in a.values() for a in planner.route_asks) == 1


async def test_a_row_whose_date_is_no_day_fails_no_call_and_is_not_asked(hass, aioclient_mock, freezer) -> None:
    """One row's date is eight digits but no day (20261131): the call is
    answered for the other rows, and the planner is not asked for that row
    (it would answer an error, or another day's run, for it)."""
    freezer.move_to("2026-11-14T06:17:00+00:00")
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    planner = Planner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    odd = {"key": "d", "trip_id": "hkir_2511_15-6", "date": "20261131"}
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_REALTIME, {"feed": "volan-szekesfehervar", "trips": [T0_REQ, odd]},
        blocking=True, return_response=True,
    )
    assert resp["trips"]["a"]["delay"] == 103 and "d" not in resp["trips"]
    assert planner.asked == [{"d0": "20261114", "t0": "hkir:hkir_2036_1-3"}]


@pytest.mark.parametrize("row", ["train", "route_start"])
async def test_a_train_or_route_row_whose_date_is_no_day_is_not_asked(aioclient_mock, row) -> None:
    """The same for a train and a route + start row: neither its trips nor
    its route list are asked for (nor for today instead)."""
    if row == "train":
        planner = TrainPlanner()
        aioclient_mock.post(mavplusz.URL, side_effect=planner)
        _statics(aioclient_mock, {"3311": ["36322788"]})
        for src in TABLE["mav"].sources:
            aioclient_mock.get(src.trip_updates, status=503)
            aioclient_mock.get(src.vehicle_positions, status=503)
        hub, session, _clock = await _train_hub(aioclient_mock)
        resp = await hub.query("mav", [{**REQ_3311, "date": "20261131"}])
        await session.close()
        assert planner.asks == [] and planner.route_asks == []
        # Matched on the feeds as they can (down here).
        assert resp == {"feed": "mav", "trips": {}, "error": "rt_unavailable"}
    else:
        planner = Planner()
        aioclient_mock.post(mavplusz.URL, side_effect=planner)
        hub, session, _clock = await _hub(aioclient_mock, _city_cfg("VBUSZ:"), wall=CITY_WALL)
        resp = await hub.query("v", [{**VB_REQ, "date": "20261131"}])
        await session.close()
        assert planner.asked == [] and not _calls(aioclient_mock, "get")
        assert resp == {"feed": "v", "updated": None, "trips": {}}


def test_an_errored_alias_teaches_route_sizes_nothing() -> None:
    """A vehicle answer's alias an error names is null: four bytes that are
    no size of that route. RouteSizes keeps what it knew (here nothing)."""
    doc = {"data": {"r0": None, "r1": RT["r0"]}, "errors": [_alias_error("r0")]}
    routes = ["1:X", "1:V3775_106"]
    sizes = mavplusz.answer_sizes(doc, routes)
    assert set(sizes) == {"1:V3775_106"}
    # A null alias with no error is a route the planner does not know: a size.
    assert mavplusz.answer_sizes({"data": {"r0": None, "r1": RT["r0"]}}, routes)["1:X"] == mavplusz.ROUTE_BYTES
    clock = FakeClock()
    rs = mavplusz.RouteSizes(clock.monotonic)
    rs.asked(routes, sizes)
    assert rs.size("1:X") == mavplusz.UNSEEN_ROUTE_BYTES
    assert rs.size("1:V3775_106") == sizes["1:V3775_106"]


async def test_the_train_numbers_load_as_the_hubs_tasks(aioclient_mock) -> None:
    """The static train numbers' download of a planner round is a task of
    the hub's task factory (Home Assistant's background tasks), as every
    other download of the hub."""
    planner = TrainPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3311": ["36322788"]})
    names: list[str] = []

    def factory(coro):
        names.append(coro.__qualname__)
        return asyncio.ensure_future(coro)

    session = aioclient_mock.create_session(asyncio.get_running_loop())
    clock = FakeClock()
    hub = realtime.RealtimeHub(
        session, feeds={"mav": TABLE["mav"]}, monotonic=clock.monotonic, wall=lambda: TRAIN_WALL, task_factory=factory
    )
    resp = await hub.query("mav", [REQ_3311])
    await session.close()
    assert resp["trips"]["3311"]["delay"] == 120
    assert names.count("RealtimeHub._trains") == 2
    assert names.count("RealtimeHub._refresh") == 2


def test_a_refusal_takes_no_token_a_refill_just_brought(caplog) -> None:
    """A bucket found empty by available() and refilled by the time the
    refusal is counted (the clock moved on in between) hands that token to
    no request refused: the next request gets it. The refusal is logged."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(burst=1, rate=0.0001, monotonic=clock.monotonic)
    real = limiter._total.available
    checks = [False]
    limiter._total.available = lambda: checks.pop() if checks else real()
    caplog.set_level(logging.WARNING, logger="custom_components.hungarian_transport")
    assert not limiter.take()
    assert sum("over the integration's rate limit" in r.getMessage() for r in caplog.records) == 1
    assert limiter.take()
    assert not limiter.take()


def _cancel_after_the_vehicle_ask(hub: realtime.RealtimeHub, req: dict) -> asyncio.Task:
    """A round of req cancelled right after _mav_vehicle_ask took its token
    and listed its routes: at the gather, before its vehicle request starts."""
    ask = hub._mav_vehicle_ask

    def cancelling(routes, timeout):
        got = ask(routes, timeout)
        if got is not None:
            asyncio.get_running_loop().call_soon(task.cancel)
            del hub._mav_vehicle_ask
        return got

    hub._mav_vehicle_ask = cancelling
    task = asyncio.ensure_future(hub.query("mav", [req]))
    return task


async def test_a_round_cancelled_before_its_vehicle_request_runs_leaves_no_route_waiting(aioclient_mock) -> None:
    """A round cancelled at the gather, before its vehicle request starts:
    the request never runs (nor its own cleanup), so the round's own
    cleanup frees its routes. Otherwise every later round would wait
    VEHICLE_TIMEOUT for a request never made, and ask none of its own."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]]})
    hub, session, clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    with pytest.raises(asyncio.CancelledError):
        await _cancel_after_the_vehicle_ask(hub, REQ_3331)
    assert planner.route_asks == [] and not hub._mav_vehicle_waits
    clock.now += TABLE["mav"].interval + 1
    hit = (await hub.query("mav", [REQ_3331]))["trips"]["3331"]
    await session.close()
    assert planner.route_asks == [["1:V3775_106"]]
    assert hit["vehicle"]["lat"] == pytest.approx(47.4552, abs=1e-4)


async def test_a_vehicle_request_that_never_ran_gives_its_token_back(aioclient_mock) -> None:
    """The same cancelled round with the vehicles' share at one token: the
    token its request never used goes back, so the next round's vehicle
    request is not refused for it."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]]})
    clock = FakeClock()
    hub, session = _limited_hub(
        aioclient_mock, {"mav": TABLE["mav"]}, RT_WALL, clock, vehicle_burst=1, vehicle_rate=0.0001
    )
    with pytest.raises(asyncio.CancelledError):
        await _cancel_after_the_vehicle_ask(hub, REQ_3331)
    assert planner.route_asks == [] and hub._mav_vehicle_rate.available()
    clock.now += TABLE["mav"].interval + 1
    hit = (await hub.query("mav", [REQ_3331]))["trips"]["3331"]
    await session.close()
    assert planner.route_asks == [["1:V3775_106"]]
    assert hit["vehicle"] is not None


async def test_a_round_answered_from_the_cache_alone_asks_no_route(aioclient_mock) -> None:
    """3331's vehicle request was refused (the vehicles' share used up), so
    its cached answer has no vehicle. The next poll, within the interval, is
    answered from the cache alone, the share refilled by then: it asks no
    route (only the trips a round's own trip request answered do; cached
    ones wait for a route another round is asking), and has no vehicle."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"]}, routes={"1:V3775_106": RT["r0"]})
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]]})
    clock = FakeClock()
    hub, session = _limited_hub(
        aioclient_mock, {"mav": TABLE["mav"]}, RT_WALL, clock, vehicle_burst=1, vehicle_rate=1.0
    )
    assert hub._mav_vehicle_rate.limiter.take(vehicles=True)
    first = await hub.query("mav", [REQ_3331])
    clock.now += 2
    assert hub._mav_vehicle_rate.available()
    second = await hub.query("mav", [REQ_3331])
    await session.close()
    assert len(planner.asks) == 1 and planner.route_asks == []
    for answer in (first, second):
        assert answer["trips"]["3331"]["delay"] == 60 and answer["trips"]["3331"]["vehicle"] is None


async def test_a_cache_hit_waiting_for_a_route_keeps_a_newer_cache_entry(aioclient_mock) -> None:
    """Card B's 3331 comes from the cache, without a vehicle, and waits for
    the route card A asks for 3332. Meanwhile 3331's cache entry is
    replaced (a newer answer). B's trip gets the vehicle; the newer entry
    is not overwritten with the older one's result."""
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"], (SZ3332, TODAY): RT["t1"]})

    async def vehicles(variables):
        await asyncio.sleep(0.2)
        return {"data": {k: _rt_route(v) for k, v in variables.items()}}

    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]], "3332": [SZ3332[2:]]})
    clock = FakeClock()
    hub, session = _limited_hub(
        aioclient_mock, {"mav": TABLE["mav"]}, RT_WALL, clock, vehicle_burst=1, vehicle_rate=1.0
    )
    assert hub._mav_vehicle_rate.limiter.take(vehicles=True)
    await hub.query("mav", [REQ_3331])
    ck = (SZ3331, TODAY)
    old = hub._mav_cache[ck]
    assert old.result.vehicle is None and planner.route_asks == []
    clock.now += 2
    planner.vehicles = vehicles
    waiting = asyncio.Event()
    wait = hub._mav_vehicle_wait

    async def waits(futs, timeout):
        waiting.set()
        return await wait(futs, timeout)

    hub._mav_vehicle_wait = waits
    a = asyncio.ensure_future(hub.query("mav", [{**REQ_3331, "key": "a", "train": "3332"}]))
    b = asyncio.ensure_future(hub.query("mav", [{**REQ_3331, "key": "b"}]))
    await waiting.wait()
    newer = old._replace(asked=old.asked + 1, at=old.at + 1)
    hub._mav_cache[ck] = newer
    rb, ra = await b, await a
    await session.close()
    assert planner.route_asks == [["1:V3775_106"]] and len(planner.asks) == 2
    assert rb["trips"]["b"]["vehicle"] is not None and ra["trips"]["a"]["vehicle"] is not None
    assert hub._mav_cache[ck] is newer


@pytest.mark.parametrize(
    ("failure", "cut", "backoff"),
    [("slow", True, False), ("slow", False, True), (503, True, True)],
    ids=["cut_short_times_out", "full_timeout_times_out", "cut_short_fails"],
)
async def test_a_vehicle_request_cut_short_by_the_round_that_times_out_is_no_outage(
    aioclient_mock, monkeypatch, failure, cut, backoff
) -> None:
    """A vehicle request given less than VEHICLE_TIMEOUT (the round's
    deadline near) that times out says nothing of the planner: no
    VEHICLE_FAIL_BACKOFF. One that had its full VEHICLE_TIMEOUT, or that
    failed otherwise (an error status), backs the vehicles off."""
    monkeypatch.setattr(realtime, "VEHICLE_TIMEOUT", 0.3)
    monkeypatch.setattr(realtime, "VEHICLE_MIN_TIMEOUT", 0.05)
    planner = TrainPlanner()
    if failure == 503:
        planner.vehicles = 503
    else:
        async def slow(_variables):
            await asyncio.sleep(1)
            return {"data": {"r0": RT["r0"]}}

        planner.vehicles = slow
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    hub, session, _clock = await _train_hub(aioclient_mock, wall=RT_WALL)
    trips, _routes = _rt_keys()
    res = mavplusz.parse_response(_rt_parts()[0], trips)
    left = 0.1 if cut else 1.0
    deadline = asyncio.get_running_loop().time() + realtime.VEHICLE_MARGIN + left
    assert await hub._mav_vehicles(res, deadline) == res
    await session.close()
    assert planner.route_asks == [["1:V3775_106", "1:V3803_106"]]
    assert (hub._monotonic() < hub._vehicle_retry_at) is backoff


async def _overlapping_rounds(aioclient_mock) -> dict[str, Any]:
    """200 rounds of two trains on one route, overlapping, a fifth of them
    cancelled at seeded points, the cache expiring now and then: what they
    did. Run on loop_clock, so the seeded seconds alone order the steps."""
    rnd = random.Random(7)
    planner = TrainPlanner({(SZ3331, TODAY): RT["t0"], (SZ3332, TODAY): RT["t1"]})
    inflight: dict[str, int] = {}
    overlaps = []
    cut = 0

    async def vehicles(variables):
        nonlocal cut
        routes = list(variables.values())
        for r in routes:
            inflight[r] = inflight.get(r, 0) + 1
            if inflight[r] > 1:
                overlaps.append(r)
        try:
            await asyncio.sleep(rnd.uniform(0.0, 0.1))
        except asyncio.CancelledError:
            cut += 1
            raise
        finally:
            for r in routes:
                inflight[r] -= 1
        return {"data": {k: _rt_route(v) for k, v in variables.items()}}

    planner.vehicles = vehicles
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=planner)
    _statics(aioclient_mock, {"3331": [SZ3331[2:]], "3332": [SZ3332[2:]]})
    clock = FakeClock()
    hub, session = _limited_hub(
        aioclient_mock, {"mav": TABLE["mav"]}, RT_WALL, clock,
        rate=1000, burst=1000, vehicle_rate=1000, vehicle_burst=1000,
    )
    # A round waiting for another round's vehicle request (the branch
    # under test besides the requests cancelled in flight, cut).
    waits = 0
    wait = hub._mav_vehicle_wait

    async def counted_wait(futs, timeout):
        nonlocal waits
        waits += 1
        return await wait(futs, timeout)

    hub._mav_vehicle_wait = counted_wait
    await hub._trains(MAV_SRC)
    await hub._trains(GYSEV_SRC)
    tasks = []
    cancels: list[asyncio.TimerHandle] = []
    before = asyncio.all_tasks()
    for i in range(200):
        req = [REQ_3331] if i % 3 else [{**REQ_3331, "key": "b", "train": "3332"}, REQ_3331]
        task = asyncio.ensure_future(hub.query("mav", req))
        tasks.append(task)
        if rnd.random() < 0.2:
            cancels.append(asyncio.get_running_loop().call_later(rnd.uniform(0, 0.05), task.cancel))
        await asyncio.sleep(rnd.uniform(0, 0.015))
        if rnd.random() < 0.1:
            clock.now += TABLE["mav"].interval * 10
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # A round over before its cancel fired leaves that timer pending, and
    # Home Assistant's test teardown fails on a lingering timer.
    for handle in cancels:
        handle.cancel()
    await session.close()
    return {
        "lingering": asyncio.all_tasks() - before,
        "overlaps": overlaps,
        "errors": [r for r in results if isinstance(r, BaseException) and not isinstance(r, asyncio.CancelledError)],
        "waiting": dict(hub._mav_vehicle_waits),
        "outcomes": ["cancelled" if isinstance(r, asyncio.CancelledError) else sorted(r["trips"]) for r in results],
        "asks": planner.asks,
        "route_asks": planner.route_asks,
        "waits": waits,
        "cut": cut,
    }


async def test_overlapping_and_cancelled_rounds_never_ask_one_route_twice_at_once(aioclient_mock, loop_clock) -> None:
    """200 rounds of two trains on one route, overlapping, a fifth of them
    cancelled, the cache expiring now and then: no route is in two vehicle
    requests at once, no round fails, and no route is left waiting once
    they are over. On the virtual loop clock the seeded schedule is the
    whole story: the same steps run every time (twice over, the same), and
    those include rounds waiting for another's vehicle request and rounds
    cancelled in their own one."""
    run = await _overlapping_rounds(aioclient_mock)
    # Nothing a round started is left running once they are all over.
    assert not run["lingering"] and not run["overlaps"] and not run["errors"] and not run["waiting"]
    assert run["route_asks"] and run["waits"] and run["cut"]
    assert "cancelled" in run["outcomes"] and ["3331", "b"] in run["outcomes"]
    assert run == await _overlapping_rounds(aioclient_mock)


# ---------------------------------------------------------------- review 6

# Numbers the planner's answer may hold that are no time: past 64 bits,
# floats, NaN (JSON numbers Python's json reads), strings, booleans, others.
HOSTILE = [10 ** 20, 2 ** 63, -(2 ** 63), 2 ** 64, 1e20, float("nan"), "1794956400", True, [], {}]


class RawPlanner(Planner):
    """Planner whose answer is encoded by the stdlib: ints past 64 bits
    and NaN survive, as they would from a broken or hostile server."""

    async def __call__(self, method, url, data):
        body = json.loads(data)
        self.asked.append(body["variables"])
        aliases = [k for k in body["variables"] if k[0] == "t"]
        answer = {"data": {a: self.trips.get(body["variables"][a]) for a in aliases}}
        return AiohttpClientMockResponse(method, url, text=json.dumps(answer))


def _ints(obj: Any):
    """Every int in a result, through tuples, lists, dicts and dataclasses."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, int):
        yield obj
    elif dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            yield from _ints(getattr(obj, f.name))
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _ints(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _ints(v)


@pytest.mark.parametrize(
    "field", ["serviceDay", "realtimeArrival", "realtimeDeparture", "arrivalDelay", "departureDelay", "lastUpdated"]
)
@pytest.mark.parametrize("value", HOSTILE, ids=[repr(v)[:12] for v in HOSTILE])
async def test_hostile_planner_numbers_never_reach_the_answer(aioclient_mock, field, value) -> None:
    """A number of one trip's answer that is no time (an int past 64 bits
    above all) failed Home Assistant's JSON of the whole realtime service
    response (orjson: "Integer exceeds 64-bit range"), the other trips' too,
    every poll while the answer was cached. It is no time now, nowhere: the
    answer is JSON Home Assistant can send, the cache holds no such number,
    and the other trip's answer is the one it would be anyway."""
    from homeassistant.helpers.json import json_bytes

    aioclient_mock.post(mavplusz.URL, side_effect=RawPlanner())
    hub, session, _clock = await _hub(aioclient_mock, _cfg())
    clean = await hub.query("v", [T0_REQ, B_REQ])
    await session.close()

    trip = _trip("t0")
    for st in trip["stoptimesForDate"]:
        if field != "lastUpdated":
            st[field] = value
    for vp in trip["pattern"]["vehiclePositions"]:
        if field == "lastUpdated":
            vp[field] = value
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=RawPlanner({"hkir:hkir_2036_1-3": trip}))
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg())
    resp = await hub.query("v", [T0_REQ, B_REQ])
    await session.close()
    json_bytes(resp)
    assert all(-(2 ** 63) <= n < 2 ** 63 for n in _ints(resp))
    cached = [a.result for a in hub._mav_cache.values()]
    # Every epoch up to 2100 and every delay fits in 32 bits.
    assert cached and all(abs(n) < 2 ** 32 for n in _ints(cached))
    assert resp["trips"]["b"] == clean["trips"]["b"]


@pytest.mark.parametrize(
    ("ahead", "kept"),
    [(10 * 365 * 86400, False), ("skew", False), (60, True)],
    ids=["ten-years", "over-the-skew", "a-minute"],
)
async def test_a_vehicle_stamped_ahead_of_now_is_no_position(aioclient_mock, ahead, kept) -> None:
    """MAX_POSITION_AGE only looked back: a position stamped years ahead
    never grew old. More than MAX_POSITION_AHEAD ahead of the request it is
    no position; a clock a minute fast is still one."""
    if ahead == "skew":
        ahead = realtime.MAX_POSITION_AHEAD + 1
    trip = _trip("t1")
    for vp in trip["pattern"]["vehiclePositions"]:
        vp["lastUpdated"] = WALL + ahead
    aioclient_mock.post(mavplusz.URL, side_effect=Planner({"hkir:hkir_2511_15-6": trip}))
    _jbb(aioclient_mock)
    hub, session, _clock = await _hub(aioclient_mock, _cfg())
    resp = await hub.query("v", [B_REQ])
    await session.close()
    vehicle = (resp["trips"].get("b") or {}).get("vehicle")
    assert (vehicle is not None) is kept
    if kept:
        assert vehicle["timestamp"] == WALL + ahead


def test_a_position_stamped_in_the_future_is_never_the_newest() -> None:
    """Two positions name the trip: the one stamped ten years ahead would
    have been the newest whatever came after it. It is no position."""
    trip = _trip("t1")
    own = next(vp for vp in trip["pattern"]["vehiclePositions"] if vp["trip"]["gtfsId"] == KEYS[1].gtfs_id)
    future = dict(own, lat=own["lat"] + 0.5, lastUpdated=WALL + 10 * 365 * 86400)
    trip["pattern"]["vehiclePositions"].append(future)
    assert mavplusz.parse_trip(trip, KEYS[1]).vehicle.lat == future["lat"]  # without now: as before
    v = mavplusz.parse_trip(trip, KEYS[1], now=WALL).vehicle
    assert (v.lat, v.timestamp) == (own["lat"], own["lastUpdated"])


def test_the_planners_numbers_are_bounded_like_the_boards() -> None:
    assert (mavplusz.DAY_MIN, mavplusz.DAY_MAX, mavplusz.SECONDS_MIN, mavplusz.SECONDS_MAX) == (
        946684800, 4102444800, -86400, 2 * 86400
    )
    for value in HOSTILE:
        assert mavplusz._day(value) is None and mavplusz._seconds(value) is None and mavplusz._delay(value) is None
    assert (mavplusz._day(SERVICE_DAY), mavplusz._seconds(22346), mavplusz._delay(-454)) == (SERVICE_DAY, 22346, -454)


class _Oversized:
    """An answer over MAX_RESPONSE_BYTES (fetch reads no further)."""


@pytest.mark.parametrize(
    "failure",
    [
        {"json": {"data": {"t0": []}}},
        {"text": json.dumps({"data": {"t0": None}, "pad": "x" * (mavplusz.MAX_RESPONSE_BYTES + 1)})},
        {"json": {"errors": [{"message": "Validation error"}]}},
        {"status": 400},
        {"status": 404},
    ],
    ids=["wrong-shape", "too-large", "graphql-error", "http-400", "http-404"],
)
async def test_a_request_failing_on_its_own_backs_off_only_its_trips(aioclient_mock, caplog, failure) -> None:
    """One card's request failing for a reason of its own (an alias of the
    wrong shape, an answer over the cap, a GraphQL error without a path, a
    4xx but 403 and 429) backed every planner request off for FAIL_BACKOFF,
    the BKK boards' fallback too (the shared PlannerLimiter), and again
    every poll. Now it holds back only its own trips: they are read off the
    feeds for FAIL_BACKOFF, another card's trip is asked at once, nothing
    shared backs off, no outage is logged."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    good = Planner()
    asked: list[dict[str, str]] = []

    async def answer(method, url, data):
        variables = json.loads(data)["variables"]
        asked.append(variables)
        if "hkir:hkir_H6112_607-1" in variables.values():
            return AiohttpClientMockResponse(method, url, **failure)
        return await good(method, url, data)

    aioclient_mock.post(mavplusz.URL, side_effect=answer)
    _jbb(aioclient_mock)
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    hub = realtime.RealtimeHub(
        session, feeds={"v": _cfg()}, monotonic=clock.monotonic, planner_limiter=limiter, wall=lambda: JBB_WALL
    )
    resp = await hub.query("v", [SZFV_REQ])
    assert resp["trips"]["s"]["delay"] == 190  # the feeds' answer
    assert not limiter.backing_off() and hub._mav_retry_at < 0
    # Another card's trip goes to the planner at once; this one is held.
    await hub.query("v", [T0_REQ])
    clock.now += realtime.FAIL_BACKOFF - 1
    resp = await hub.query("v", [SZFV_REQ])
    assert resp["trips"]["s"]["delay"] == 190
    assert [list(v.values())[-1] for v in asked] == ["hkir:hkir_H6112_607-1", "hkir:hkir_2036_1-3"]
    clock.now += 2
    await hub.query("v", [SZFV_REQ])
    await session.close()
    assert len(asked) == 3 and not limiter.backing_off()
    assert "unavailable" not in caplog.text


@pytest.mark.parametrize(
    "failure",
    [{"status": 503}, {"status": 429}, {"status": 403}, {"exc": asyncio.TimeoutError()}, {"text": "<html>down</html>"}],
    ids=["http-503", "http-429", "http-403", "timeout", "not-json"],
)
async def test_the_planner_failing_still_backs_everybody_off(aioclient_mock, failure) -> None:
    """No answer, a timeout, 5xx, 429, 403 or a page that is not JSON say
    the planner is failing: every planner request backs off, the boards'
    fallback too, for FAIL_BACKOFF."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    aioclient_mock.post(mavplusz.URL, **failure)
    _jbb(aioclient_mock)
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    hub = realtime.RealtimeHub(
        session, feeds={"v": _cfg()}, monotonic=clock.monotonic, planner_limiter=limiter, wall=lambda: JBB_WALL
    )
    await hub.query("v", [SZFV_REQ])
    await hub.query("v", [T0_REQ])
    await session.close()
    assert limiter.backing_off() and len(_calls(aioclient_mock, "post")) == 1


async def test_a_route_list_failing_on_its_own_holds_back_only_its_routes(aioclient_mock) -> None:
    """The route + start path the same way: a route list request of the
    wrong shape is no outage; its routes are not asked again for
    FAIL_BACKOFF, the feeds answer for them."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    aioclient_mock.post(mavplusz.URL, json={"data": {"r0": "x"}})
    aioclient_mock.get(VB + "trip-updates.pb", content=_pb("veszprem-trip-updates"))
    aioclient_mock.get(VB + "vehicle-positions.pb", content=_pb("veszprem-vehicle-positions"))
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    hub = realtime.RealtimeHub(
        session, feeds={"v": _city_cfg("VBUSZ:")}, monotonic=clock.monotonic, planner_limiter=limiter,
        wall=lambda: JBB_WALL,
    )
    first = await hub.query("v", [VB_REQ])
    clock.now += realtime.FAIL_BACKOFF - 1
    await hub.query("v", [VB_REQ])
    assert len(_calls(aioclient_mock, "post")) == 1 and not limiter.backing_off()
    clock.now += 2
    await hub.query("v", [VB_REQ])
    await session.close()
    assert len(_calls(aioclient_mock, "post")) == 2 and "error" not in first


def test_planner_down_tells_an_outage_from_a_requests_own_failure() -> None:
    import aiohttp

    down = [
        ValueError("HTTP 500"), ValueError("HTTP 503"), ValueError("HTTP 429"), ValueError("HTTP 403"),
        asyncio.TimeoutError(), aiohttp.ClientConnectionError(), OSError("reset"),
        json.JSONDecodeError("x", "<html>", 0), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad"),
    ]
    own = [
        ValueError("HTTP 400"), ValueError("HTTP 404"), mavplusz.TooLarge("over 1048576 bytes"),
        ValueError("answer nested too deep"), ValueError("GraphQL error: x"), ValueError("trip is not an object"),
        KeyError("t0"), TypeError("x"), mavplusz.TooLarge("HTTP 503"),
    ]
    assert all(mavplusz.planner_down(e) for e in down)
    assert not any(mavplusz.planner_down(e) for e in own)


def test_route_sizes_are_reckoned_without_encoding_the_answer_again(monkeypatch) -> None:
    """answer_sizes weighed each alias (up to VEHICLE_MAX_BYTES of them) by
    encoding it again on the event loop. It reckons from the patterns and
    positions now: never under the real size of a route of the live answer,
    at most a quarter over it."""
    doc = {"data": {f"r{i}": RT[f"r{i}"] for i in range(4)}}
    routes = [f"1:R{i}" for i in range(4)]
    real = {r: len(json.dumps(RT[f"r{i}"], separators=(",", ":"))) for i, r in enumerate(routes)}

    def no_dumps(*_args, **_kwargs):
        raise AssertionError("encoded again")

    monkeypatch.setattr(mavplusz.json, "dumps", no_dumps)
    sizes = mavplusz.answer_sizes(doc, routes)
    for r in routes:
        assert real[r] <= sizes[r] <= 1.25 * real[r]
