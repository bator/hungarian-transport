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
import re
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


def _static(numbers: dict[str, list[str]]) -> bytes:
    """A static GTFS zip whose trips.txt gives each train number its trip ids."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        rows = "".join(f"{tid},R,S,{num} személyvonat\n" for num, ids in numbers.items() for tid in ids)
        zf.writestr("trips.txt", "trip_id,route_id,service_id,trip_short_name\n" + rows)
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
    unknown one null. asks has the (id, date) pairs of every request."""

    def __init__(self, runs: dict[tuple[str, str], dict] | None = None) -> None:
        self.runs = {
            (IC741, TODAY): TRAINS["t0"], (IC741_MOD, TODAY): TRAINS["t1"],
            (OS3311, TODAY): TRAINS["t2"], (IC741, YESTERDAY): TRAINS["t3"],
        } if runs is None else runs
        self.asks: list[list[tuple[str, str]]] = []

    async def __call__(self, method, url, data):
        body = json.loads(data)
        variables = body["variables"]
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


def _statics(aioclient_mock, mav: dict | None, gysev: dict | None = GYSEV_OTHER) -> None:
    """The two static feeds' train numbers; None is a static feed that fails."""
    for src, numbers in ((MAV_SRC, mav), (GYSEV_SRC, gysev)):
        if numbers is None:
            aioclient_mock.get(src.static, status=503)
        else:
            aioclient_mock.get(src.static, content=_static(numbers))


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
    """30 trains, one trip id each, are 60 trips with last night's runs: 40
    go out (today's all first), the trains with a trip cut are matched on
    the feeds, and the next call asks for the rest."""
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
    assert [d for _g, d in asked] == [TODAY] * 30 + [YESTERDAY] * 10
    assert set(first["trips"]) == set(list(numbers)[:10])
    assert sorted(_feed_gets(aioclient_mock)) == [J + "1_198/trip-updates.pb", J + "1_198/vehicle-positions.pb"]
    clock.now += 30
    second = await hub.query("mav", reqs)
    await session.close()
    assert [d for _g, d in planner.asks[1]] == [YESTERDAY] * 20
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
