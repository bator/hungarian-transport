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
"""
from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
import logging
from pathlib import Path

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
    assert res[KEYS[2]] == mavplusz.TripResult(None, None)


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


def test_feed_table_opts_in_the_planners_feeds() -> None:
    table = realtime.load_feed_table()
    flagged = {(fid, s.prefix) for fid, cfg in table.items() for s in cfg.sources if s.mavplusz}
    assert {p for _f, p in flagged} == {"hkir:", "paks:", "debrecen:", "mvkzrt:", "VBUSZ:"}
    doc = json.loads(realtime.FEEDS_FILE.read_text(encoding="utf-8"))
    assert sum(1 for f in doc["feeds"].values() for s in f["sources"] if s.get("mavplusz") is True) == 64
    for fid in ("veszprem", "miskolc"):
        assert "route_start" in table[fid].match and all(s.mavplusz for s in table[fid].sources), fid
    for fid in ("mav", "gysev", "oroshaza"):
        assert not any(s.mavplusz for s in table[fid].sources), fid
    for fid, cfg in table.items():
        if any(s.mavplusz for s in cfg.sources):
            assert "trip" in cfg.match, fid
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
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), clock=FakeClock(stopped=True))
    for _ in range(realtime.FETCH_BURST):
        hub._rate.take()
    resp = await hub.query("v", [T0_REQ])
    await session.close()
    # Nothing goes out (jbb is refused by the same bucket): no answer yet.
    assert aioclient_mock.call_count == 0
    assert resp == {"feed": "v", "trips": {}, "error": "rt_unavailable"}


async def test_the_planner_takes_a_token(aioclient_mock) -> None:
    aioclient_mock.post(mavplusz.URL, side_effect=Planner())
    hub, session, _clock = await _hub(aioclient_mock, _cfg(), clock=FakeClock(stopped=True))
    for _ in range(realtime.FETCH_BURST - 1):
        hub._rate.take()
    await hub.query("v", [T0_REQ])
    await session.close()
    assert aioclient_mock.call_count == 1
    with pytest.raises(realtime.RealtimeRateLimited):
        hub._rate.take()


@pytest.mark.parametrize(
    "req",
    [
        {"key": "t", "train": "4510"},
        {"key": "none"},
    ],
    ids=["train", "nothing"],
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
    # The card's own trip id goes along (unknown to the planner), and the
    # resolved one.
    assert planner.asked[1] == {"d0": "20261114", "t0": "mvkzrt:10977_60_073700_static", "t1": "mvkzrt:10977_60_073700_B4P"}
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
