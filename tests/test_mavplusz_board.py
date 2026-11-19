"""BKK boards and trip details from mavplusz.hu when FUTÁR fails.

fixtures/realtime/mavplusz_bkk.json holds real planner answers of
2026-11-18 ~19:54 CET to the module's own queries: the board of FUTÁR stop
BKK_F00940 (Báthory utca, bus 9 and trolleybus 72; three departures a
pattern over an hour, the last one scheduled only), and the details of its
first trip with an unknown trip beside it (a null alias).
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from pathlib import Path

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from custom_components.hungarian_transport import futar, mavplusz, mavplusz_board as mb
from custom_components.hungarian_transport.const import DOMAIN

from .test_futar import _call, _inject, _reauth_flows, _setup
from .test_upstream import FakeClock

FIX = json.loads((Path(__file__).parent / "fixtures" / "realtime" / "mavplusz_bkk.json").read_text(encoding="utf-8"))
WALL = FIX["board"]["wall"]
BOARD_DOC = FIX["board"]["response"]
TRIPS_DOC = FIX["trips"]["response"]
STOP = "BKK_F00940"
BOARD = "arrivals-and-departures-for-stop.json"
TRIP = "trip-details.json"
FIRST_TRIP = "BKK_D25280605"
BOARD_PARAMS = {"stopId": STOP, "minutesAfter": "60", "minutesBefore": "0", "onlyDepartures": "true",
                "includeReferences": "true", "includeVehicleFromTrip": "true"}


class Planner:
    """A fake mavplusz.hu: the fixture's board for BKK:F00940 (null for any
    other stop), the fixture's trip for its first trip and null for the
    others; records every request's variables. down: every request fails."""

    def __init__(self) -> None:
        self.asked: list[dict] = []
        self.down = False
        self.trip = TRIPS_DOC["data"]["t0"]

    async def __call__(self, method, url, data):
        body = json.loads(data)
        self.asked.append(body["variables"])
        if self.down:
            return AiohttpClientMockResponse(method, url, status=502)
        if "stop(id:" in body["query"]:
            stop = copy.deepcopy(BOARD_DOC["data"]["stop"]) if body["variables"]["id"] == "BKK:F00940" else None
            return AiohttpClientMockResponse(method, url, json={"data": {"stop": stop}})
        aliases = [k for k in body["variables"] if k[0] == "t"]
        answer = {a: (copy.deepcopy(self.trip) if body["variables"][a] == "BKK:D25280605" else None) for a in aliases}
        return AiohttpClientMockResponse(method, url, json={"data": answer})


@pytest.fixture
async def make_planner(aioclient_mock):
    """make_planner(clock=None, **kwargs) -> (PlannerBoards, Planner, clock),
    on a session closed after the test."""
    sessions = []

    def make(clock: FakeClock | None = None, **kwargs) -> tuple[mb.PlannerBoards, Planner, FakeClock]:
        fake = Planner()
        aioclient_mock.post(mavplusz.URL, side_effect=fake)
        clock = clock or FakeClock()
        session = aioclient_mock.create_session(asyncio.get_running_loop())
        sessions.append(session)
        boards = mb.PlannerBoards(session, monotonic=clock.monotonic, sleep=clock.sleep, wall=lambda: WALL, **kwargs)
        return boards, fake, clock

    yield make
    for session in sessions:
        await session.close()


def _proxy(monkeypatch, boards, clock: FakeClock, failure: Exception | None = None, seen: list | None = None):
    """A FutarProxy whose FUTÁR raises failure (answers when None)."""

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
        if seen is not None:
            seen.append((path, timeout.total))
        if failure is not None:
            raise failure
        return {"status": "OK", "data": {"entry": {"stopTimes": []}}, "from": "futar"}

    monkeypatch.setattr(futar, "futar_get", fake_get)
    return futar.FutarProxy(None, "secret-key", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)


async def _decoded(doc, decode):
    """A fake mavplusz.fetch's answer doc as the real one gives it: through
    the caller's decode hook (as JSON text), when it hands one."""
    return doc if decode is None else await decode(bytearray(json.dumps(doc).encode()))


def _futar_down(proxy: futar.FutarProxy) -> None:
    """FUTÁR's outage stands, as after a failed call, and its probe is due
    (FUTAR_OUTAGE_PROBE_SEC since it was last asked): the next request asks
    FUTÁR first, cut short for the fallback; the ones after it within
    FUTAR_OUTAGE_PROBE_SEC ask the fallback first, until FUTÁR answers."""
    proxy._outages.failed(None, "BKK FUTÁR unavailable (%s); not logged again until it answers", "test")
    _probe_due(proxy)


def _futar_first(proxy: futar.FutarProxy) -> None:
    """Every request asks FUTÁR first in its outage too, cut short for the
    fallback (the order before FUTAR_OUTAGE_PROBE_SEC): the reserved
    tokens' and tickets' lifetimes, which the probe and the callers joining
    its request rely on, tested on many requests at once."""
    proxy._in_outage = lambda path: False


def _probe_due(proxy: futar.FutarProxy) -> None:
    """FUTÁR was last asked FUTAR_OUTAGE_PROBE_SEC ago or more: the next
    request in its outage is the probe (without moving the clock, which
    would refill the buckets)."""
    proxy._futar_asked_at = -1e18


def test_ids_map_between_futar_and_the_planner() -> None:
    assert mb.planner_id("BKK_F00940") == "BKK:F00940"
    assert mb.planner_id("BKK_C96610561") == "BKK:C96610561"
    assert mb.futar_id("BKK:C96610561") == "BKK_C96610561"
    # Another feed's ids are none of the planner's BKK ones, nor theirs FUTÁR's.
    assert mb.planner_id("volan_123") is None
    assert mb.planner_id("BKK_") is None
    assert mb.planner_id("") is None
    assert mb.futar_id("hkir:hkir_779365_6") == ""
    assert mb.futar_id(None) == ""


def test_the_queries_carry_the_ids_as_variables_never_in_the_text() -> None:
    body = mb.build_board_query("BKK:F00940", WALL)
    assert "F00940" not in body["query"]
    assert body["variables"] == {"id": "BKK:F00940", "start": WALL, "range": 480 * 60, "n": mb.DEPARTURES_PER_PATTERN}
    trips = mb.build_trip_query([mb.TripKey("BKK:A", "20261118"), mb.TripKey("BKK:B", "20261118"),
                                 mb.TripKey("BKK:C", "20261117")])
    assert "BKK:" not in trips["query"] and "2026" not in trips["query"]
    assert trips["variables"] == {"d0": "20261118", "d1": "20261117", "t0": "BKK:A", "t1": "BKK:B", "t2": "BKK:C"}
    assert "t2: trip(id: $t2)" in trips["query"] and "serviceDate: $d1" in trips["query"]


def test_a_planner_board_is_futar_shaped_with_live_and_scheduled_rows() -> None:
    board = mb.parse_board(BOARD_DOC, STOP, WALL)
    doc = mb.render_board(board, "60", WALL)
    assert doc["status"] == "OK" and doc["source"] == "mavplusz"
    assert doc["currentTime"] == WALL * 1000
    entry, refs = doc["data"]["entry"], doc["data"]["references"]
    assert entry["stopId"] == STOP
    assert "BKK_0090" in entry["routeIds"] and "BKK_4720" in entry["routeIds"]
    times = entry["stopTimes"]
    assert [st["tripId"] for st in times] == [
        "BKK_D25280605", "BKK_C96610581", "BKK_D25280611", "BKK_C96610586", "BKK_D25280617", "BKK_C96610692",
    ]
    first, last = times[0], times[-1]
    # A live trip: the timetable and the prediction, as epoch seconds.
    assert first["stopId"] == STOP and first["serviceDate"] == "20261118"
    assert first["departureTime"] == 1794956400 + 71880
    assert first["predictedDepartureTime"] - first["departureTime"] == 256
    assert first["stopHeadsign"] == "Óbuda, Bogdáni út"
    assert first["wheelchairAccessible"] is True and first["mayRequireBooking"] is False
    # A scheduled one has no prediction, as on FUTÁR: no delay on the card.
    assert "predictedDepartureTime" not in last and "predictedArrivalTime" not in last
    assert refs["trips"]["BKK_D25280605"] == {
        "id": "BKK_D25280605", "routeId": "BKK_0090", "tripHeadsign": "Óbuda, Bogdáni út",
        "wheelchairAccessible": True, "bikesAllowed": False,
    }
    assert refs["routes"]["BKK_0090"] == {
        "id": "BKK_0090", "shortName": "9", "longName": "", "type": "BUS", "agencyId": "BKK",
        "color": "009EE3", "textColor": "FFFFFF",
    }
    assert refs["routes"]["BKK_4720"]["type"] == "TROLLEYBUS"
    # FUTÁR's names have no "Budapest, " (the card matches destinations by name).
    assert refs["stops"][STOP]["name"] == "Báthory utca / Bajcsy-Zsilinszky út"
    assert refs["stops"][STOP]["lat"] == pytest.approx(47.507189)


def test_a_cards_horizon_cuts_the_board_and_its_references() -> None:
    board = mb.parse_board(BOARD_DOC, STOP, WALL)
    doc = mb.render_board(board, "15", WALL)
    times = doc["data"]["entry"]["stopTimes"]
    assert [st["tripId"] for st in times] == ["BKK_D25280605", "BKK_C96610581", "BKK_D25280611"]
    assert set(doc["data"]["references"]["trips"]) == {st["tripId"] for st in times}
    # Ten minutes later the first bus (7.8 min after WALL) has left, past the minute's grace.
    later = mb.render_board(board, "60", WALL + 600)
    assert later["data"]["entry"]["stopTimes"][0]["tripId"] == "BKK_C96610581"
    # A horizon that is no number is the card's default.
    assert len(mb.render_board(board, "x", WALL)["data"]["entry"]["stopTimes"]) == 6


def test_cancelled_departures_other_feeds_and_special_routes() -> None:
    doc = copy.deepcopy(BOARD_DOC)
    calls = doc["data"]["stop"]["stoptimesWithoutPatterns"]
    calls[0]["realtimeState"] = "CANCELED"
    calls[1]["trip"]["gtfsId"] = "hkir:hkir_1"
    calls[2]["pickupType"] = "CALL_AGENCY"
    calls[2]["trip"]["route"].update(mode="RAIL", type=109)
    calls[2]["stop"] = {"gtfsId": "BKK:F00941", "name": "Budapest, Pole", "platformCode": "B",
                        "parentStation": {"gtfsId": "BKK:CSF00940"}}
    board = mb.parse_board(doc, STOP, WALL)
    out = mb.render_board(board, "60", WALL)
    times = out["data"]["entry"]["stopTimes"]
    assert [st["tripId"] for st in times][:1] == ["BKK_D25280611"]
    assert "BKK_D25280605" not in {st["tripId"] for st in times}
    assert times[0]["mayRequireBooking"] is True and times[0]["stopId"] == "BKK_F00941"
    assert out["data"]["references"]["routes"]["BKK_0090"]["type"] == "SUBURBAN_RAILWAY"
    assert out["data"]["references"]["stops"]["BKK_F00941"] == {
        "id": "BKK_F00941", "name": "Pole", "platformCode": "B", "parentStationId": "BKK_CSF00940",
    }


def test_an_unknown_stop_is_none_and_an_error_is_a_value_error() -> None:
    assert mb.parse_board({"data": {"stop": None}}, STOP, WALL) is None
    with pytest.raises(ValueError):
        mb.parse_board({"errors": [{"message": "boom"}]}, STOP, WALL)
    with pytest.raises(ValueError):
        mb.parse_board({"data": {"stop": []}}, STOP, WALL)


def test_trip_details_are_futar_shaped() -> None:
    doc = mb.parse_trip(TRIPS_DOC["data"]["t0"], FIRST_TRIP, "20261118", WALL)
    entry, refs = doc["data"]["entry"], doc["data"]["references"]
    assert doc["source"] == "mavplusz" and entry["tripId"] == FIRST_TRIP and entry["serviceDate"] == "20261118"
    sts = entry["stopTimes"]
    assert len(sts) == 32 and [s["stopSequence"] for s in sts[:3]] == [0, 1, 2]
    assert refs["stops"][sts[0]["stopId"]]["name"] == "Kőbánya alsó vasútállomás"
    assert "lat" in refs["stops"][sts[0]["stopId"]]
    assert sts[0]["predictedDepartureTime"] - sts[0]["departureTime"] == 70065 - 70080
    assert refs["trips"][FIRST_TRIP]["routeId"] == "BKK_0090"
    assert mb.parse_trip(None, "BKK_X", "20261118", WALL) is None
    assert mb.parse_trip(dict(TRIPS_DOC["data"]["t0"], stoptimesForDate=[]), FIRST_TRIP, "20261118", WALL) is None


@pytest.mark.parametrize(
    "failure",
    [futar.FutarError("HTTP 503"), futar.FutarError("TimeoutError"), futar.FutarUpstreamRateLimited("HTTP 429"),
     futar.FutarAuthError("HTTP 401")],
    ids=["outage", "timeout", "upstream-429", "rejected-key"],
)
async def test_futar_failing_is_answered_from_the_planner(make_planner, aioclient_mock, monkeypatch, failure) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, failure)
    data = await proxy.get(BOARD, BOARD_PARAMS)
    assert data["source"] == "mavplusz"
    assert len(data["data"]["entry"]["stopTimes"]) == 6
    assert fake.asked == [{"id": "BKK:F00940", "start": WALL, "range": 480 * 60, "n": mb.DEPARTURES_PER_PATTERN}]
    # A rejected key still asks for a new one (the handler reads this).
    assert proxy.key_rejected is isinstance(failure, futar.FutarAuthError)


async def test_a_rejected_key_failing_fast_is_answered_from_the_planner(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarAuthError("HTTP 401"))
    await proxy.get(BOARD, BOARD_PARAMS)
    clock.now += mb.BOARD_TTL
    # Not asked again (REJECTED_RETRY), yet the board is the planner's.
    data = await proxy.get(BOARD, BOARD_PARAMS)
    assert data["source"] == "mavplusz" and len(fake.asked) == 2 and proxy.key_rejected


async def test_futar_answering_never_asks_the_planner(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    seen: list = []
    proxy = _proxy(monkeypatch, boards, clock, None, seen)
    data = await proxy.get(BOARD, BOARD_PARAMS)
    assert data["from"] == "futar" and fake.asked == []
    # FUTÁR answering has its whole budget everywhere: it is cut short for
    # the planner only while its outage stands.
    await proxy.get("search.json", {"query": "Keleti"})
    assert seen == [(BOARD, 18.0), ("search.json", 18.0)]


@pytest.mark.parametrize(
    "failure",
    [futar.FutarRateLimited("rate limited"), futar.FutarStatusError("status NOT_FOUND")],
    ids=["own-rate-limit", "error-status"],
)
async def test_failures_that_are_no_outage_are_not_answered_from_the_planner(make_planner, aioclient_mock, monkeypatch, failure) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, failure)
    with pytest.raises(type(failure)):
        await proxy.get(BOARD, BOARD_PARAMS)
    assert fake.asked == []


async def test_paths_the_planner_does_not_serve_keep_futars_error(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    with pytest.raises(futar.FutarError, match="HTTP 503"):
        await proxy.get("route-details.json", {"routeId": "BKK_0090"})
    with pytest.raises(futar.FutarError, match="HTTP 503"):
        await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="volan_123"))
    assert fake.asked == []


async def test_planner_down_too_raises_futars_own_error(make_planner, aioclient_mock, monkeypatch, caplog) -> None:
    """The card then fills the board from Transitous, as without the planner."""
    boards, fake, clock = make_planner()
    fake.down = True
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    with pytest.raises(futar.FutarError, match="HTTP 503") as err:
        await proxy.get(BOARD, BOARD_PARAMS)
    assert type(err.value) is futar.FutarError
    # Backing off: another stop is not asked either, and the outage is said once.
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_F00941"))
    assert len(fake.asked) == 1
    warnings = [r for r in caplog.records if r.name.endswith("mavplusz_board") and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "HTTP 502" in warnings[0].getMessage()
    # It answers again after the backoff (and the stop's failed ask, BOARD_TTL).
    fake.down = False
    clock.now += max(mb.FAIL_BACKOFF, mb.BOARD_TTL)
    data = await proxy.get(BOARD, BOARD_PARAMS)
    assert data["source"] == "mavplusz"
    assert any("answers for BKK boards again" in r.getMessage() for r in caplog.records)


async def test_an_unknown_stop_keeps_futars_error(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    with pytest.raises(futar.FutarError, match="HTTP 503"):
        await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_NOSUCH"))
    # The planner's "no such stop" is an answer: cached like a board.
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_NOSUCH"))
    assert len(fake.asked) == 1


async def test_one_planner_request_per_stop_for_every_card_and_horizon(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    answers = await asyncio.gather(*(
        proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter=m)) for m in ("15", "60", "180", "60")
    ))
    assert len(fake.asked) == 1
    assert [len(a["data"]["entry"]["stopTimes"]) for a in answers] == [3, 6, 6, 6]
    # Cached for BOARD_TTL, then asked again.
    clock.now += mb.BOARD_TTL - 1
    await proxy.get(BOARD, BOARD_PARAMS)
    assert len(fake.asked) == 1
    clock.now += 1
    await proxy.get(BOARD, BOARD_PARAMS)
    assert len(fake.asked) == 2
    assert mb.BOARD_TTL >= futar.FUTAR_CACHE_TTL


async def test_a_stop_is_not_asked_twice_within_the_ttl_even_unanswered(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    # A malformed answer is no board (that stop's request failed: not asked
    # for REQUEST_FAIL_TTL, and no backoff), and the stop counts as asked.
    async def broken(method, url, data):
        fake.asked.append(json.loads(data)["variables"])
        return AiohttpClientMockResponse(method, url, json={"data": {"stop": []}})

    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=broken)
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, BOARD_PARAMS)
    clock.now += max(mb.FAIL_BACKOFF, mb.BOARD_TTL) + 1
    assert clock.now - 1000 < mb.REQUEST_FAIL_TTL
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, BOARD_PARAMS)
    assert len(fake.asked) == 1


async def test_the_fallback_has_a_share_of_the_one_planner_limit(make_planner, aioclient_mock, monkeypatch, caplog) -> None:
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(fallback_burst=2, monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    got = []
    for stop in ("BKK_F00940", "BKK_F1", "BKK_F2"):
        try:
            got.append(await proxy.get(BOARD, dict(BOARD_PARAMS, stopId=stop)))
        except futar.FutarError as err:
            got.append(str(err))
    assert len(fake.asked) == 2
    assert got[0]["source"] == "mavplusz" and got[2] == "HTTP 503"
    assert any("for BKK boards while FUTÁR fails over the integration's rate limit" in r.getMessage() for r in caplog.records)
    # The refused board spent none of the total: the delays keep all but the two.
    delays = limiter.client(RuntimeError)
    for _ in range(mavplusz.PLANNER_BURST - 2):
        delays.take()
    with pytest.raises(RuntimeError):
        delays.take()


async def test_trip_details_of_several_cards_go_in_one_request_on_the_boards_date(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    await proxy.get(BOARD, BOARD_PARAMS)
    fake.asked.clear()
    ids = [FIRST_TRIP, "BKK_C96610581", "BKK_D25280611", "BKK_X1", "BKK_X2"]
    results = await asyncio.gather(*(proxy.get(TRIP, {"tripId": t}) for t in ids), return_exceptions=True)
    assert len(fake.asked) == 1
    assert fake.asked[0]["t0"] == "BKK:D25280605" and fake.asked[0]["d0"] == "20261118"
    assert sorted(v for k, v in fake.asked[0].items() if k[0] == "t") == sorted(mb.planner_id(t) for t in ids)
    assert results[0]["source"] == "mavplusz" and results[0]["data"]["entry"]["tripId"] == FIRST_TRIP
    # The planner does not know the others: FUTÁR's error, as the card handles a failed trip-details.
    assert all(isinstance(r, futar.FutarError) for r in results[1:])
    # Cached per trip: asked again only after TRIP_TTL.
    await proxy.get(TRIP, {"tripId": FIRST_TRIP})
    assert len(fake.asked) == 1
    clock.now += mb.TRIP_TTL
    await proxy.get(TRIP, {"tripId": FIRST_TRIP})
    assert len(fake.asked) == 2


async def test_a_trip_no_board_named_is_asked_for_today_in_budapest(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    await proxy.get(TRIP, {"tripId": FIRST_TRIP})
    assert fake.asked == [{"d0": "20261118", "t0": "BKK:D25280605"}]


async def test_closing_the_proxy_closes_the_planner(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, _fake, clock = make_planner()
    closed = []
    monkeypatch.setattr(boards, "close", lambda: closed.append(True))
    _proxy(monkeypatch, boards, clock).close()
    assert closed == [True]


def test_futar_then_the_planner_answer_before_the_card_gives_up() -> None:
    worst = futar.FUTAR_TOKEN_WAIT_SEC + futar.FUTAR_FALLBACK_TIMEOUT_SEC + mb.PLANNER_TIMEOUT.total
    assert worst <= futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    # The proxy times the fallback by its own word, and a late event loop eats less than the margin.
    assert mb.PlannerBoards.timeout == mb.PLANNER_TIMEOUT.total
    assert futar.FUTAR_FALLBACK_SLACK_SEC < futar.FUTAR_MARGIN_SEC


async def test_the_service_answers_a_board_from_the_planner_when_futar_is_down(
    hass, aioclient_mock, monkeypatch
) -> None:
    _inject(monkeypatch, FakeClock())
    fake = Planner()
    aioclient_mock.get(f"{futar.BKK_API}/{BOARD}", status=503)
    aioclient_mock.post(mavplusz.URL, side_effect=fake)
    monkeypatch.setattr(mb.time, "time", lambda: WALL)
    await _setup(hass)

    resp = await _call(hass, path=BOARD, params=BOARD_PARAMS)
    assert resp["data"]["source"] == "mavplusz"
    assert len(resp["data"]["data"]["entry"]["stopTimes"]) == 6
    # The planner's own request: its User-Agent, the ids as variables.
    post = [c for c in aioclient_mock.mock_calls if c[0].lower() == "post"]
    assert len(post) == 1 and json.loads(post[0][2])["variables"]["id"] == "BKK:F00940"
    assert post[0][3]["User-Agent"] == mb.USER_AGENT


async def test_a_rejected_key_answered_by_the_planner_still_asks_for_a_new_one(
    hass, aioclient_mock, monkeypatch
) -> None:
    _inject(monkeypatch, FakeClock())
    aioclient_mock.get(f"{futar.BKK_API}/{BOARD}", status=401)
    aioclient_mock.post(mavplusz.URL, side_effect=Planner())
    monkeypatch.setattr(mb.time, "time", lambda: WALL)
    await _setup(hass)

    resp = await _call(hass, path=BOARD, params=BOARD_PARAMS)
    await hass.async_block_till_done()
    assert resp["data"]["source"] == "mavplusz"
    assert len(_reauth_flows(hass)) == 1
    # Another endpoint still answers as before: the card shows the rejected key.
    other = await _call(hass, path="search.json", params={"query": "Keleti"})
    assert other["error"] == "futar_auth"
    assert hass.services.has_service(DOMAIN, "futar")


# --- One planner limit, stations, the FUTÁR budget, hostile answers, dates and batches.


def test_one_limit_and_one_backoff_for_every_planner_requester() -> None:
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(burst=3, fallback_burst=2, monotonic=clock.monotonic)
    delays, boards = limiter.client(RuntimeError), limiter.client(KeyError, fallback=True)
    boards.take()
    delays.take()
    delays.take()
    # Three of the total, whoever took them: neither gets a fourth.
    with pytest.raises(RuntimeError):
        delays.take()
    with pytest.raises(KeyError):
        boards.take()
    # A failure of either stops both; the longer backoff stands.
    clock.now += 100
    clock.stopped = False
    delays.failed(60)
    boards.failed(30)
    assert boards.backing_off() and delays.backing_off()
    clock.now += 59
    with pytest.raises(KeyError):
        boards.take()
    clock.now += 1
    assert not limiter.backing_off()
    boards.take()


def test_the_shared_limits_are_the_documented_ones() -> None:
    from custom_components.hungarian_transport import realtime

    # docs/realtime.md "Polling": one request every two seconds, a burst of 30,
    # for the hub and the boards together; the boards half of it.
    assert (mavplusz.PLANNER_RATE, mavplusz.PLANNER_BURST) == (realtime.MAV_RATE, realtime.MAV_BURST) == (0.5, 30)
    assert mavplusz.FALLBACK_RATE <= mavplusz.PLANNER_RATE / 2 and mavplusz.FALLBACK_BURST <= mavplusz.PLANNER_BURST / 2


async def test_a_board_failure_stops_the_hubs_planner_requests_and_back(aioclient_mock) -> None:
    from custom_components.hungarian_transport import realtime

    from .test_mavplusz import T0_REQ, _cfg, _jbb
    from .test_mavplusz import Planner as TripPlanner

    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    trips = TripPlanner()
    aioclient_mock.post(mavplusz.URL, side_effect=trips)
    _jbb(aioclient_mock)
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    hub = realtime.RealtimeHub(session, feeds={"v": _cfg()}, monotonic=clock.monotonic, planner_limiter=limiter)
    boards = mb.PlannerBoards(session, monotonic=clock.monotonic, wall=lambda: WALL, limiter=limiter)
    boards._failed(ValueError("HTTP 502"))
    await hub.query("v", [T0_REQ])
    assert trips.asked == []
    clock.now += mb.FAIL_BACKOFF
    await hub.query("v", [T0_REQ])
    assert len(trips.asked) == 1
    # The hub's failure is the boards' backoff too: FUTÁR keeps its budget.
    hub._mav_failed("HTTP 503")
    assert not boards.can_answer(BOARD, BOARD_PARAMS)
    await session.close()


async def test_home_assistant_hands_one_limiter_to_the_hub_and_the_boards(hass, aioclient_mock, monkeypatch) -> None:
    import custom_components.hungarian_transport as integration
    from custom_components.hungarian_transport import realtime

    seen = {}
    real_hub, real_boards = realtime.RealtimeHub, mb.PlannerBoards

    def hub(*args, **kwargs):
        seen["hub"] = kwargs["planner_limiter"]
        return real_hub(*args, **kwargs)

    def boards(*args, **kwargs):
        seen["boards"] = kwargs["limiter"]
        return real_boards(*args, **kwargs)

    monkeypatch.setattr(integration.realtime, "RealtimeHub", hub)
    monkeypatch.setattr(integration.mavplusz_board, "PlannerBoards", boards)
    _inject(monkeypatch, FakeClock())
    await _setup(hass)
    assert isinstance(seen["hub"], mavplusz.PlannerLimiter) and seen["boards"] is seen["hub"]
    assert hass.data[DOMAIN]["planner_limiter"] is seen["hub"]


async def test_a_key_failing_fast_takes_futars_token_before_the_planner(make_planner, aioclient_mock, monkeypatch) -> None:
    """A rejected key must not make the planner the BKK source with no
    limit but its own: every fast failure spends FUTÁR's token first."""
    boards, fake, clock = make_planner(FakeClock(stopped=True))

    async def rejected(session, api_key, path, params, timeout=None, *, executor=None):
        raise futar.FutarAuthError("HTTP 401")

    monkeypatch.setattr(futar, "futar_get", rejected)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards, burst=2)
    assert (await proxy.get(BOARD, BOARD_PARAMS))["source"] == "mavplusz"
    # Fails fast without asking FUTÁR, and its token answers this one.
    other = dict(BOARD_PARAMS, stopId="BKK_F00941")
    with pytest.raises(futar.FutarAuthError):
        await proxy.get(BOARD, other)
    assert len(fake.asked) == 2
    # FUTÁR's bucket is empty now: the rejected key is the answer, the planner not asked.
    with pytest.raises(futar.FutarAuthError) as err:
        await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_F00942"))
    assert isinstance(err.value, futar.FutarKeyRejected) and len(fake.asked) == 2
    assert proxy.key_rejected


@pytest.mark.parametrize(
    ("path", "params"),
    [
        (BOARD, dict(BOARD_PARAMS, stopId="BKK_CSF09999")),
        (TRIP, {"tripId": "BKK_1234567890"}),
        (BOARD, dict(BOARD_PARAMS, stopId="volan_1")),
    ],
    ids=["futar-station-unknown", "ad-hoc-trip", "other-feed"],
)
async def test_futar_keeps_its_whole_budget_where_the_planner_cannot_answer(
    make_planner, aioclient_mock, monkeypatch, path, params
) -> None:
    boards, fake, clock = make_planner()
    if mb.is_station(params.get("stopId", "")):
        # A station the planner said it does not know, and FUTÁR named no pole of
        # (test_an_unknown_futar_station_is_asked_of_the_planner_once).
        boards._probe_result(params["stopId"], False)
    seen: list = []
    proxy = _proxy(monkeypatch, boards, clock, None, seen)
    await proxy.get(path, params)
    assert seen == [(path, futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC)]
    assert not boards.can_answer(path, params)
    # Nor is the planner asked when FUTÁR fails there.
    failing = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    with pytest.raises(futar.FutarError):
        await failing.get(path, params)
    assert fake.asked == []


async def test_futar_keeps_its_whole_budget_while_the_planner_backs_off(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, _fake, clock = make_planner()
    seen: list = []
    proxy = _proxy(monkeypatch, boards, clock, None, seen)
    boards._failed(ValueError("HTTP 502"))
    _futar_down(proxy)
    await proxy.get(BOARD, BOARD_PARAMS)
    clock.now += mb.FAIL_BACKOFF
    _futar_down(proxy)
    await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="61"))
    assert [t for _p, t in seen] == [futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC, futar.FUTAR_FALLBACK_TIMEOUT_SEC]


def _station_board(stop: str = "BKK:CS056233") -> dict:
    """The fixture's board as a GTFS parent station's: the planner's stop()
    of a station holds its child stops' departures."""
    node = copy.deepcopy(BOARD_DOC["data"]["stop"])
    node.update(gtfsId=stop, name="Budapest, Keleti pályaudvar")
    for st in node["stoptimesWithoutPatterns"]:
        st["stop"] = {"gtfsId": "BKK:F01336", "name": "Budapest, Keleti pályaudvar M", "lat": 47.5, "lon": 19.08,
                      "platformCode": None, "parentStation": {"gtfsId": stop}}
    return {"data": {"stop": node}}


def test_a_gtfs_station_is_one_planner_stop_whose_board_names_its_poles() -> None:
    board = mb.parse_board(_station_board(), "BKK_CS056233", WALL)
    doc = mb.render_board(board, "60", WALL)
    stops = doc["data"]["references"]["stops"]
    assert doc["data"]["entry"]["stopId"] == "BKK_CS056233" and stops["BKK_CS056233"]["name"] == "Keleti pályaudvar"
    assert {st["stopId"] for st in doc["data"]["entry"]["stopTimes"]} == {"BKK_F01336"}
    assert stops["BKK_F01336"]["parentStationId"] == "BKK_CS056233"
    assert mb.build_board_query("BKK:CS056233", WALL)["variables"]["id"] == "BKK:CS056233"


class StationPlanner(Planner):
    """The fake planner, answering the aliased poles of a FUTÁR station
    too: the fixture's board for BKK:F00940, null for the others."""

    async def __call__(self, method, url, data):
        body = json.loads(data)
        if "s0: stop(id:" not in body["query"]:
            return await super().__call__(method, url, data)
        self.asked.append(body["variables"])
        answer = {
            k: (copy.deepcopy(BOARD_DOC["data"]["stop"]) if v == "BKK:F00940" else None)
            for k, v in body["variables"].items() if k[0] == "s" and k[1:].isdigit()
        }
        return AiohttpClientMockResponse(method, url, json={"data": answer})


async def test_a_futar_station_is_answered_from_the_poles_futar_named(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, _fake, clock = make_planner()
    fake = StationPlanner()
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=fake)
    station = "BKK_CSF00940"
    params = dict(BOARD_PARAMS, stopId=station)
    # FUTÁR's own board of the station: its poles and its name.
    good = {"status": "OK", "data": {
        "entry": {"stopId": station, "stopTimes": [{"tripId": "BKK_T1", "stopId": "BKK_F00940", "serviceDate": "20261118"}]},
        "references": {"stops": {
            station: {"id": station, "name": "Báthory utca", "lat": 47.5, "lon": 19.05},
            "BKK_F00939": {"id": "BKK_F00939", "name": "Báthory utca", "parentStationId": station},
        }},
    }}

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
        if failing:
            raise futar.FutarError("HTTP 503")
        return good

    failing = False
    monkeypatch.setattr(futar, "futar_get", fake_get)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    # The planner said it does not know the station.
    boards._probe_result(station, False)
    assert not boards.can_answer(BOARD, params)
    await proxy.get(BOARD, params)
    assert boards.can_answer(BOARD, params)
    failing = True
    clock.now += futar.FUTAR_CACHE_TTL
    doc = await proxy.get(BOARD, params)
    assert doc["source"] == "mavplusz" and len(fake.asked) == 1
    asked = fake.asked[0]
    assert sorted(v for k, v in asked.items() if k[0] == "s" and k[1:].isdigit()) == ["BKK:F00939", "BKK:F00940"]
    assert asked["n"] == mb.CHILD_DEPARTURES
    entry, stops = doc["data"]["entry"], doc["data"]["references"]["stops"]
    assert entry["stopId"] == station and len(entry["stopTimes"]) == 6
    assert stops[station] == {"id": station, "name": "Báthory utca", "lat": 47.5, "lon": 19.05}
    # The poles carry FUTÁR's station as their parent: the card tells its own poles so.
    assert stops["BKK_F00940"]["parentStationId"] == station


def test_futars_answers_teach_only_well_formed_station_poles() -> None:
    boards = mb.PlannerBoards(None)
    junk = [None, [], {"data": []}, {"data": {"references": {"stops": [1, 2]}}},
            {"data": {"references": {"stops": {"a": {"id": 5, "parentStationId": "BKK_CSF1"}}}}},
            {"data": {"entry": {"stopTimes": [None, {"stopId": "BKK_CSF2", "tripId": 3, "serviceDate": 10 ** 30}]}}}]
    for doc in junk:
        boards.learn(BOARD, {"stopId": "BKK_CSF1"}, doc)
    # No pole learnt: the station itself, for the planner to say whether it knows it.
    assert not boards._stations and boards._board_ids("BKK_CSF1") == ("BKK:CSF1",)
    for i in range(mb.MAX_CHILD_STOPS + 5):
        boards.learn("route-details.json", {}, {"data": {"references": {"stops": {
            "p": {"id": f"BKK_F{i}", "parentStationId": "BKK_CSF1"}}}}})
    assert len(boards._board_ids("BKK_CSF1")) == mb.MAX_CHILD_STOPS
    for i in range(mb.MAX_STATIONS + 3):
        boards._learn_pole(f"BKK_CSF{i + 10}", "BKK_F1")
    assert len(boards._stations) == mb.MAX_STATIONS


async def test_a_futar_station_the_planner_knows_is_its_own_board(make_planner, aioclient_mock, monkeypatch) -> None:
    """BKK's GTFS has FUTÁR's combined station BKK_CSF00062 (Batthyány
    tér): one stop() of the planner answers its board, with no pole known
    from FUTÁR, and the planner's answer is remembered (PROBE_TTL): later
    boards ask it the same way, poles learnt or not."""
    boards, _fake, clock = make_planner()
    station = "BKK_CSF00062"
    fake = Planner()
    board = _station_board("BKK:CSF00062")

    async def answer(method, url, data):
        body = json.loads(data)
        fake.asked.append(body["variables"])
        return AiohttpClientMockResponse(method, url, json=board)

    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=answer)
    params = dict(BOARD_PARAMS, stopId=station)
    assert boards.can_answer(BOARD, params)
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    doc = await proxy.get(BOARD, params)
    assert doc["source"] == "mavplusz" and doc["data"]["entry"]["stopId"] == station
    assert [a["id"] for a in fake.asked] == ["BKK:CSF00062"]
    assert boards._known(station) is True
    boards.learn(BOARD, {"stopId": "BKK_X"}, {"data": {"references": {"stops": {
        "p": {"id": "BKK_F00063", "parentStationId": station}}}}})
    assert boards._board_ids(station) == ("BKK:CSF00062",)
    clock.now += mb.PROBE_TTL
    assert boards._known(station) is None and boards._board_ids(station) == ("BKK:F00063",)


async def test_an_unknown_futar_station_is_asked_of_the_planner_once(make_planner, aioclient_mock, monkeypatch) -> None:
    """A FUTÁR-only station (null in the planner) with no pole known: the
    first board FUTÁR cannot give asks the planner for it; its null is kept
    for UNKNOWN_TTL (PROBE_TTL once repeated), during which FUTÁR keeps its
    whole budget there and the planner is not asked again. A failed request
    says nothing either way."""
    boards, fake, clock = make_planner()
    station = "BKK_CSF09999"
    params = dict(BOARD_PARAMS, stopId=station)
    fake.down = True
    failing = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    with pytest.raises(futar.FutarError):
        await failing.get(BOARD, params)
    assert boards._known(station) is None and boards.can_answer(BOARD, dict(params, minutesAfter="61")) is False
    clock.now += max(mb.FAIL_BACKOFF, mb.BOARD_TTL)
    fake.down = False
    with pytest.raises(futar.FutarError):
        await failing.get(BOARD, dict(params, minutesAfter="62"))
    assert [a.get("id") for a in fake.asked] == ["BKK:CSF09999", "BKK:CSF09999"]
    assert boards._known(station) is False and not boards.can_answer(BOARD, params)
    clock.now += mb.BOARD_TTL
    with pytest.raises(futar.FutarError):
        await failing.get(BOARD, dict(params, minutesAfter="63"))
    assert len(fake.asked) == 2
    clock.now += mb.PROBE_TTL
    assert boards.can_answer(BOARD, params)


def test_the_keleti_preset_is_seeded_until_futar_names_its_own_poles() -> None:
    """The card's Keleti preset (BKK_CSF01131) is FUTÁR's alone: its board
    is merged from the seeded poles from the first start on, and from the
    poles FUTÁR names once it has."""
    seed = mb.SEED_STATIONS["BKK_CSF01131"]
    assert 0 < len(seed["poles"]) <= mb.MAX_CHILD_STOPS
    assert all(mb.planner_id(p) and not mb.is_station(p) for p in seed["poles"])
    assert seed["ref"]["name"] == "Keleti pályaudvar"
    boards = mb.PlannerBoards(None)
    assert boards._board_ids("BKK_CSF01131") == tuple(mb.planner_id(p) for p in seed["poles"])
    assert boards.can_answer(BOARD, {"stopId": "BKK_CSF01131"})
    boards.learn(BOARD, {"stopId": "BKK_CSF01131"}, {"data": {"entry": {"stopTimes": [{"stopId": "BKK_F01131"}]}}})
    assert boards._board_ids("BKK_CSF01131") == ("BKK:F01131",)


async def test_seeded_poles_are_asked_as_the_stations_board(make_planner, aioclient_mock) -> None:
    boards, _fake, _clock = make_planner()
    fake = StationPlanner()
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=fake)
    assert await boards.answer(BOARD, dict(BOARD_PARAMS, stopId="BKK_CSF01131")) is None
    poles = sorted(v for k, v in fake.asked[0].items() if k[0] == "s" and k[1:].isdigit())
    assert poles == sorted(mb.planner_id(p) for p in mb.SEED_STATIONS["BKK_CSF01131"]["poles"])


def test_learnt_stations_survive_a_restart() -> None:
    """What learn() gathered goes out as plain JSON (stations_data), once
    per change (on_stations_changed), and a PlannerBoards made with it
    answers the same stations at once."""
    changes = []
    boards = mb.PlannerBoards(None, on_stations_changed=lambda: changes.append(1))
    doc = {"data": {
        "entry": {"stopTimes": [{"stopId": "BKK_F00940", "tripId": "BKK_T1", "serviceDate": "20261118"}]},
        "references": {"stops": {
            "BKK_CSF00940": {"id": "BKK_CSF00940", "name": "Báthory utca", "lat": 47.5, "lon": 19.05},
            "BKK_F00939": {"id": "BKK_F00939", "parentStationId": "BKK_CSF00940"},
        }},
    }}
    boards.learn(BOARD, {"stopId": "BKK_CSF00940"}, doc)
    assert len(changes) == 1
    boards.learn(BOARD, {"stopId": "BKK_CSF00940"}, doc)
    assert len(changes) == 1
    stored = json.loads(json.dumps(boards.stations_data()))
    again = mb.PlannerBoards(None, stations=stored)
    assert again._board_ids("BKK_CSF00940") == ("BKK:F00939", "BKK:F00940")
    assert again._stations == boards._stations
    assert again._stations["BKK_CSF00940"]["ref"] == {"id": "BKK_CSF00940", "name": "Báthory utca", "lat": 47.5, "lon": 19.05}


@pytest.mark.parametrize(
    "data", [None, [], "x", {"stations": []}, {"stations": {"BKK_F1": {"poles": ["BKK_F2"]}}},
             {"stations": {"BKK_CSF1": {"poles": ["BKK_CSF2", 5, "volan_1"]}}},
             {"stations": {"BKK_CSF1": {"poles": "BKK_F2"}}}, {"stations": {"BKK_CSF1": None}}],
)
def test_a_broken_stored_record_restores_nothing(data) -> None:
    assert mb.restore_stations(data) == {}


def test_a_restored_record_keeps_the_limits() -> None:
    many = {f"BKK_CSF{i}": {"ref": {"name": 7, "lat": 999, "lon": 0}, "poles": [f"BKK_F{j}" for j in range(40)]}
            for i in range(mb.MAX_STATIONS + 10)}
    got = mb.restore_stations({"stations": many})
    assert len(got) == mb.MAX_STATIONS and "BKK_CSF0" not in got and f"BKK_CSF{mb.MAX_STATIONS + 9}" in got
    assert all(len(st["poles"]) == mb.MAX_CHILD_STOPS for st in got.values())
    assert got["BKK_CSF10"]["ref"] == {"id": "BKK_CSF10", "name": ""}


async def test_home_assistant_keeps_the_learnt_stations_in_its_store(
    hass, hass_storage, aioclient_mock, monkeypatch, freezer
) -> None:
    """Loaded at setup, written a while after learn() changes them (once),
    removed with the entry."""
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    import custom_components.hungarian_transport as integration
    from custom_components.hungarian_transport import storage

    hass_storage[storage.STATIONS_KEY] = {
        "version": storage.STATIONS_VERSION, "minor_version": 1, "key": storage.STATIONS_KEY,
        "data": {"stations": {"BKK_CSF00940": {"ref": None, "poles": ["BKK_F00940"]}}},
    }
    made = []
    real = mb.PlannerBoards

    def boards(*args, **kwargs):
        made.append(real(*args, **kwargs))
        return made[-1]

    monkeypatch.setattr(integration.mavplusz_board, "PlannerBoards", boards)
    _inject(monkeypatch, FakeClock())
    entry = await _setup(hass)
    (planner,) = made
    assert planner._board_ids("BKK_CSF00940") == ("BKK:F00940",)
    planner.learn(BOARD, {"stopId": "BKK_X"}, {"data": {"references": {"stops": {
        "p": {"id": "BKK_F00941", "parentStationId": "BKK_CSF00940"}}}}})
    freezer.tick(timedelta(seconds=storage.STATIONS_SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[storage.STATIONS_KEY]["data"]["stations"]["BKK_CSF00940"]["poles"] == ["BKK_F00940", "BKK_F00941"]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert storage.STATIONS_KEY not in hass_storage


HOSTILE = [10 ** 20, 2 ** 64, 2 ** 63, -(2 ** 63), 10 ** 400, 1e20, float("nan"), "1794956400", True, None, [], {}]


def _ha_json(answer) -> None:
    """answer (or None) through Home Assistant's own JSON (orjson), as the
    service response goes to the card: an int past 64 bits broke it."""
    from homeassistant.helpers.json import json_bytes

    if answer is not None:
        json_bytes(answer)


@pytest.mark.parametrize("value", HOSTILE, ids=[repr(v)[:12] for v in HOSTILE])
async def test_hostile_times_never_escape_the_fallback(make_planner, aioclient_mock, monkeypatch, value) -> None:
    """The planner's answer is not ours: a serviceDay datetime cannot hold
    (it raised OverflowError) or of the wrong type is no time, never an
    error past answer()."""
    boards, _fake, _clock = make_planner()
    board = copy.deepcopy(BOARD_DOC)
    trip = copy.deepcopy(TRIPS_DOC["data"]["t0"])
    for st in board["data"]["stop"]["stoptimesWithoutPatterns"] + trip["stoptimesForDate"]:
        st["serviceDay"] = value
        st["realtimeDeparture"] = value
        st["stop"]["lat"] = value
    calls = board["data"]["stop"]["stoptimesWithoutPatterns"]
    calls[0]["serviceDay"] = 1794956400
    calls[0]["scheduledDeparture"] = value
    # The shape's length too: one trip of the fixture's times, any length.
    good = copy.deepcopy(TRIPS_DOC["data"]["t0"])
    good["tripGeometry"] = {"points": "}gz`HaoosBPADdBJfH?P@", "length": value}

    async def fetch(session, body, *, decode=None, **_kwargs):
        # JSON numbers no int64 holds, NaN as Python's json reads it.
        if "stop(id:" in body["query"]:
            return await _decoded(board, decode)
        aliases = [k for k in body["variables"] if k[0] == "t"]
        return await _decoded({"data": {
            k: good if body["variables"][k] == "BKK:GOOD" else trip for k in aliases}}, decode)

    monkeypatch.setattr(mavplusz, "fetch", fetch)
    out = await boards.answer(BOARD, BOARD_PARAMS)
    assert out is None or all(isinstance(st["departureTime"], int) for st in out["data"]["entry"]["stopTimes"])
    _ha_json(out)
    assert await boards.answer(TRIP, {"tripId": FIRST_TRIP}) is None
    shaped = await boards.answer(TRIP, {"tripId": "BKK_GOOD"})
    assert shaped["data"]["entry"]["polyline"]["points"]
    _ha_json(shaped)


async def test_answers_of_the_wrong_shape_never_escape_the_fallback(make_planner, aioclient_mock, monkeypatch) -> None:
    import random

    rng = random.Random(20261118)
    junk = [None, 0, -1, 10 ** 25, 2 ** 64, 1.5, "", "x" * 300, True, [], {}, [None], {"gtfsId": 7}]

    def mangle(node):
        if isinstance(node, dict):
            for key in list(node):
                if rng.random() < 0.15:
                    node[key] = rng.choice(junk)
                else:
                    mangle(node[key])
        elif isinstance(node, list):
            for item in node:
                mangle(item)
        return node

    boards, _fake, clock = make_planner()
    docs = []

    async def fetch(session, body, *, decode=None, **_kwargs):
        source = BOARD_DOC if "stop(id:" in body["query"] else {"data": {
            k: copy.deepcopy(TRIPS_DOC["data"]["t0"]) for k in body["variables"] if k[0] == "t"}}
        doc = mangle(copy.deepcopy(source))
        docs.append(doc)
        return await _decoded(doc, decode)

    monkeypatch.setattr(mavplusz, "fetch", fetch)
    for i in range(60):
        clock.now += mb.FAIL_BACKOFF + mb.TRIP_TTL
        _ha_json(await boards.answer(BOARD, dict(BOARD_PARAMS, stopId=f"BKK_F{i}")))
        _ha_json(await boards.answer(TRIP, {"tripId": f"BKK_T{i}"}))
    # Asked on, never raised: a broken answer fails its own request, never a backoff.
    assert len(docs) > 60


def test_a_service_day_is_its_noons_date_also_when_the_clocks_change() -> None:
    from datetime import datetime

    for y, m, d in ((2026, 3, 29), (2026, 10, 25), (2026, 11, 18)):
        noon = datetime(y, m, d, 12, tzinfo=mavplusz.BUDAPEST)
        service_day = int(noon.timestamp()) - 12 * 3600
        assert mb._date(service_day) == f"{y}{m:02d}{d:02d}"
    assert mb._date(10 ** 20) == "" and mb._date(None) == ""


def test_a_board_asks_enough_departures_a_pattern_to_fill_a_card() -> None:
    # One tram every three minutes fills the card's 40 rows within the
    # board's window, and its 180-minute horizon.
    assert mb.DEPARTURES_PER_PATTERN >= 180 // 3
    # The fixture's stop times, at their real size: twenty patterns of
    # them, or a FUTÁR station's poles, fit the board's answer cap.
    calls = BOARD_DOC["data"]["stop"]["stoptimesWithoutPatterns"]
    row = len(json.dumps(calls, ensure_ascii=False).encode()) / len(calls)
    assert 20 * mb.DEPARTURES_PER_PATTERN * row < mb.BOARD_MAX_BYTES
    assert mb.MAX_CHILD_STOPS * 3 * mb.CHILD_DEPARTURES * row < mb.BOARD_MAX_BYTES


async def test_a_board_answer_may_be_larger_than_the_trip_details(make_planner, aioclient_mock, monkeypatch) -> None:
    boards, _fake, _clock = make_planner()
    caps = []

    async def fetch(session, body, *, user_agent=None, timeout=None, cap=mavplusz.MAX_RESPONSE_BYTES, decode=None):
        caps.append((cap, timeout.total))
        return await _decoded(BOARD_DOC if "stop(id:" in body["query"] else {"data": {"t0": None}}, decode)

    monkeypatch.setattr(mavplusz, "fetch", fetch)
    await boards.answer(BOARD, BOARD_PARAMS)
    await boards.answer(TRIP, {"tripId": FIRST_TRIP})
    assert caps == [(mb.BOARD_MAX_BYTES, mb.PLANNER_TIMEOUT.total), (mavplusz.MAX_RESPONSE_BYTES, mb.TRIP_TIMEOUT.total)]


async def test_a_trip_on_a_futar_board_is_asked_on_futars_service_date(make_planner, aioclient_mock, monkeypatch) -> None:
    """An after-midnight night bus runs on yesterday's service day: FUTÁR's
    board says so, and its details come from that day's run."""
    boards, fake, clock = make_planner()
    board = {"status": "OK", "data": {"entry": {"stopTimes": [
        {"tripId": FIRST_TRIP, "serviceDate": "20261117"}, {"tripId": "BKK_T2", "serviceDate": 1794870000000}]}}}
    failing = False

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
        if failing:
            raise futar.FutarError("HTTP 503")
        return board

    monkeypatch.setattr(futar, "futar_get", fake_get)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    await proxy.get(BOARD, BOARD_PARAMS)
    failing = True
    await asyncio.gather(proxy.get(TRIP, {"tripId": FIRST_TRIP}), proxy.get(TRIP, {"tripId": "BKK_T2"}),
                         return_exceptions=True)
    assert len(fake.asked) == 1
    asked = fake.asked[0]
    assert {asked[f"d{i}"] for i in range(2) if f"d{i}" in asked} == {"20261117"}


def _night_trip(service_day: int) -> dict:
    trip = copy.deepcopy(TRIPS_DOC["data"]["t0"])
    for i, st in enumerate(trip["stoptimesForDate"]):
        st.update(serviceDay=service_day, realtime=False, scheduledArrival=25 * 3600 + 60 * i,
                  scheduledDeparture=25 * 3600 + 60 * i)
    return trip


async def test_a_trip_no_board_dated_is_asked_for_both_days_at_night(aioclient_mock) -> None:
    """At 01:15 a trip of the planner's yesterday is under way and the same
    trip of today is tomorrow night's: both are asked, in one request, and
    the run under way is the answer."""
    from datetime import datetime

    day = lambda d: int(datetime(2026, 11, d, 12, tzinfo=mavplusz.BUDAPEST).timestamp()) - 12 * 3600  # noqa: E731
    wall = day(18) + 25 * 3600 + 15 * 60
    asked = []

    async def answer(method, url, data):
        body = json.loads(data)
        asked.append(body["variables"])
        dates = body["variables"]
        out = {
            alias: _night_trip(day(int(dates[var][-2:])))
            for alias, var in re.findall(r"(t\d+): trip\(id: \$t\d+\) \{.*?serviceDate: \$(d\d+)\)", body["query"])
        }
        return AiohttpClientMockResponse(method, url, json={"data": out})

    aioclient_mock.post(mavplusz.URL, side_effect=answer)
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    clock = FakeClock()
    boards = mb.PlannerBoards(session, monotonic=clock.monotonic, sleep=clock.sleep, wall=lambda: wall)
    doc = await boards.answer(TRIP, {"tripId": FIRST_TRIP})
    await session.close()
    assert len(asked) == 1 and sorted(v for k, v in asked[0].items() if k[0] == "d") == ["20261118", "20261119"]
    assert doc["data"]["entry"]["serviceDate"] == "20261118"
    # In the day only today's run is asked.
    assert mb.PlannerBoards(None, wall=lambda: day(18) + 9 * 3600)._dates_to_try() == ["20261118"]


async def test_a_trip_asked_while_a_batch_is_under_way_does_not_wait_for_it(make_planner, aioclient_mock) -> None:
    boards, fake, _clock = make_planner()
    release = asyncio.Event()
    started = []

    async def slow(method, url, data):
        started.append(json.loads(data)["variables"])
        if len(started) == 1:
            await release.wait()
        return await fake(method, url, data)

    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=slow)
    first = asyncio.ensure_future(boards.answer(TRIP, {"tripId": "BKK_X1"}))
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(started) == 1
    # The first request hangs; the next trip's request goes beside it.
    second = await asyncio.wait_for(boards.answer(TRIP, {"tripId": FIRST_TRIP}), 1)
    assert second["data"]["entry"]["tripId"] == FIRST_TRIP and not first.done()
    release.set()
    assert await first is None and len(started) == 2


def test_every_trip_answers_before_the_card_gives_up() -> None:
    wait = mb.TRIP_BATCH_WAIT + mb.TRIP_TIMEOUT.total
    assert wait <= mb.PLANNER_TIMEOUT.total
    assert futar.FUTAR_TOKEN_WAIT_SEC + futar.FUTAR_FALLBACK_TIMEOUT_SEC + wait <= futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC


# --- The review of 2026-11-19: tokens before the cut, per-request failures,
# --- stops the planner lacks, the card's budget, the lifecycle, fair boards,
# --- trip shapes, and the learnt stations' ageing.


class AnyStopPlanner(Planner):
    """The fake planner answering the fixture's board for every stop asked."""

    async def __call__(self, method, url, data):
        body = json.loads(data)
        if "stop(id: $id)" not in body["query"]:
            return await super().__call__(method, url, data)
        self.asked.append(body["variables"])
        if self.down:
            return AiohttpClientMockResponse(method, url, status=502)
        stop = copy.deepcopy(BOARD_DOC["data"]["stop"])
        stop["gtfsId"] = body["variables"]["id"]
        return AiohttpClientMockResponse(method, url, json={"data": {"stop": stop}})


def _use(aioclient_mock, fake) -> None:
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=fake)


async def test_a_dashboards_slow_futar_boards_are_all_answered(make_planner, aioclient_mock, monkeypatch) -> None:
    """FUTÁR slow but answering (10 s a board, within its whole budget): a
    dashboard's 40 poles at once. While FUTÁR answers nothing is cut short.
    In its outage, FUTÁR is cut short only for the boards whose planner
    token is reserved first (the fallback's burst), which the planner
    answers; the rest keep FUTÁR's whole budget, and FUTÁR's answer.
    Before, every board was cut, and the boards past the burst failed."""
    boards, _fake, clock = make_planner()
    fake = AnyStopPlanner()
    _use(aioclient_mock, fake)

    async def slow(session, api_key, path, params, timeout=None, *, executor=None):
        await asyncio.sleep(0)
        if timeout.total < 10:
            raise futar.FutarError("TimeoutError")
        return {"status": "OK", "data": {"entry": {"stopTimes": []}, "references": {}}, "from": "futar"}

    monkeypatch.setattr(futar, "futar_get", slow)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    got = await asyncio.gather(
        *(proxy.get(BOARD, dict(BOARD_PARAMS, stopId=f"BKK_F1{i:04d}")) for i in range(40)), return_exceptions=True
    )
    assert [g.get("from") for g in got] == ["futar"] * 40 and fake.asked == []
    _futar_down(proxy)
    got = await asyncio.gather(
        *(proxy.get(BOARD, dict(BOARD_PARAMS, stopId=f"BKK_F{i:05d}")) for i in range(40)), return_exceptions=True
    )
    assert not [g for g in got if isinstance(g, BaseException)]
    planner = [g for g in got if g.get("source") == "mavplusz"]
    assert len(planner) == len(fake.asked) == mavplusz.FALLBACK_BURST
    assert len([g for g in got if g.get("from") == "futar"]) == 40 - mavplusz.FALLBACK_BURST


async def test_futar_is_cut_short_only_with_the_fallbacks_token_in_hand(make_planner, aioclient_mock, monkeypatch) -> None:
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(fallback_burst=1, monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    seen: list = []
    proxy = _proxy(monkeypatch, boards, clock, None, seen)
    # FUTÁR answering in its outage: the token reserved for the fallback goes back.
    _futar_down(proxy)
    await proxy.get(BOARD, BOARD_PARAMS)
    _futar_down(proxy)
    await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="61"))
    assert [t for _p, t in seen] == [futar.FUTAR_FALLBACK_TIMEOUT_SEC] * 2
    # The share spent elsewhere: nothing could answer, FUTÁR keeps its whole budget.
    limiter.client(RuntimeError, fallback=True).take()
    assert not boards.can_answer(BOARD, BOARD_PARAMS)
    _futar_down(proxy)
    await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="62"))
    assert seen[-1][1] == futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC


def test_a_request_one_bucket_refuses_spends_no_token_of_the_others() -> None:
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(burst=2, fallback_burst=5, vehicle_burst=5, monotonic=clock.monotonic)
    hub = limiter.client(RuntimeError)
    boards = limiter.client(KeyError, fallback=True)
    vehicles = limiter.client(ValueError, vehicles=True)
    hub.take()
    hub.take()
    # The hub has the total: the boards' and the vehicles' calls are refused ...
    for _ in range(5):
        with pytest.raises(KeyError):
            boards.take()
        with pytest.raises(ValueError):
            vehicles.take()
    # ... and spent nothing of their shares: the total's next token is theirs.
    clock.now += 2
    boards.take()
    clock.now += 2
    vehicles.take()
    # A reserved token goes back unspent; a quiet refusal is no refusal logged.
    clock.now += 2
    assert boards.reserve()
    assert not limiter.available(fallback=True)
    boards.refund()
    assert limiter.available(fallback=True)


def _oversize(method, url, data):
    return AiohttpClientMockResponse(method, url, text='{"data":{"stop":' + " " * (mb.BOARD_MAX_BYTES + 10) + "null}}")


def _graphql_error(method, url, data):
    return AiohttpClientMockResponse(method, url, json={"errors": [{"message": "Validation error"}], "data": None})


def _wrong_shape(method, url, data):
    return AiohttpClientMockResponse(method, url, json={"data": {"stop": [1, 2]}})


def _not_json(method, url, data):
    return AiohttpClientMockResponse(method, url, text="<html>maintenance</html>")


def _bad_request(method, url, data):
    return AiohttpClientMockResponse(method, url, status=400)


@pytest.mark.parametrize(
    "broken", [_oversize, _graphql_error, _wrong_shape, _bad_request],
    ids=["over-cap", "graphql-error", "wrong-shape", "http-400"],
)
async def test_a_board_failing_for_its_own_sake_backs_nobody_off(make_planner, aioclient_mock, monkeypatch, caplog, broken) -> None:
    """A property of that stop's answer, not an outage: the realtime hub and
    the other stops keep asking, nothing is warned of, and that stop is not
    asked again for REQUEST_FAIL_TTL (FUTÁR keeps its whole budget there)."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    fake = AnyStopPlanner()
    asked = []

    async def answer(method, url, data):
        body = json.loads(data)
        if body["variables"].get("id") == "BKK:F_BIG":
            asked.append(body["variables"])
            return broken(method, url, data)
        return await fake(method, url, data)

    _use(aioclient_mock, answer)
    big = dict(BOARD_PARAMS, stopId="BKK_F_BIG")
    assert await boards.answer(BOARD, big) is None
    assert not limiter.backing_off()
    limiter.client(RuntimeError).take()  # the hub's delays
    assert (await boards.answer(BOARD, BOARD_PARAMS))["source"] == "mavplusz"
    assert not [r for r in caplog.records if r.name.endswith("mavplusz_board") and r.levelno >= logging.WARNING]
    assert not boards.can_answer(BOARD, big)
    clock.now += mb.REQUEST_FAIL_TTL - 1
    assert await boards.answer(BOARD, big) is None and len(asked) == 1
    clock.now += 1
    assert boards.can_answer(BOARD, big)
    await boards.answer(BOARD, big)
    assert len(asked) == 2


@pytest.mark.parametrize("status", [500, 503, 429, 403])
async def test_the_planner_failing_backs_every_planner_request_off(make_planner, aioclient_mock, caplog, status) -> None:
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    async def down(method, url, data):
        return AiohttpClientMockResponse(method, url, status=status)

    _use(aioclient_mock, down)
    assert await boards.answer(BOARD, BOARD_PARAMS) is None
    assert limiter.backing_off()
    assert any(f"HTTP {status}" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


async def test_a_trip_a_graphql_error_names_is_no_answer_and_the_others_are(make_planner, aioclient_mock) -> None:
    """OTP answers a bad id or date with a DataFetchingException whose path
    starts with its alias (live, 2026-11-19): that trip is no answer (and
    not asked again for a while), the batch's others are, and nobody backs
    off. An error naming no alias fails the batch alone."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    asked = []

    async def answer(method, url, data):
        body = json.loads(data)
        asked.append(body["variables"])
        aliases = [k for k in body["variables"] if k[0] == "t"]
        bad = [a for a in aliases if body["variables"][a] != "BKK:D25280605"]
        out = {a: (None if a in bad else copy.deepcopy(TRIPS_DOC["data"]["t0"])) for a in aliases}
        errors = [{"message": "Internal server error: x", "path": [a, "stoptimesForDate"],
                   "extensions": {"classification": "DataFetchingException"}} for a in bad]
        return AiohttpClientMockResponse(method, url, json={"errors": errors, "data": out})

    _use(aioclient_mock, answer)
    good, bad = await asyncio.gather(boards.answer(TRIP, {"tripId": FIRST_TRIP}), boards.answer(TRIP, {"tripId": "BKK_X9"}))
    assert len(asked) == 1
    assert good["data"]["entry"]["tripId"] == FIRST_TRIP and bad is None
    assert not limiter.backing_off()
    assert not boards.can_answer(TRIP, {"tripId": "BKK_X9"}) and boards.can_answer(TRIP, {"tripId": FIRST_TRIP})
    async def error(method, url, data):
        return _graphql_error(method, url, data)

    _use(aioclient_mock, error)
    clock.now += mb.TRIP_TTL
    assert await boards.answer(TRIP, {"tripId": FIRST_TRIP}) is None
    assert not limiter.backing_off() and not boards.can_answer(TRIP, {"tripId": FIRST_TRIP})


async def test_a_stop_the_planner_does_not_know_is_not_asked_again(make_planner, aioclient_mock, monkeypatch) -> None:
    """A plain BKK stop the planner has no board of (a HÉV stop, Kelenföld's
    MÁV station BKK_005510017): its null is kept for UNKNOWN_TTL, and once
    it was null twice in a row for PROBE_TTL, during which FUTÁR keeps its
    whole budget there and the planner is not asked."""
    boards, fake, clock = make_planner()
    params = dict(BOARD_PARAMS, stopId="BKK_005510017")
    seen: list = []
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"), seen)
    for i in range(4):
        with pytest.raises(futar.FutarError):
            await proxy.get(BOARD, dict(params, minutesAfter=str(60 + i)))
        clock.now += mb.BOARD_TTL
    assert [a["id"] for a in fake.asked] == ["BKK:005510017"]
    assert not boards.can_answer(BOARD, params)
    assert [t for _p, t in seen[1:]] == [futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC] * 3
    # Null again after UNKNOWN_TTL: two in a row, kept for PROBE_TTL.
    clock.now += mb.UNKNOWN_TTL
    assert boards.can_answer(BOARD, params)
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, params)
    assert len(fake.asked) == 2
    clock.now += mb.PROBE_TTL - 1
    assert not boards.can_answer(BOARD, params)
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, params)
    assert len(fake.asked) == 2
    clock.now += 1
    assert boards.can_answer(BOARD, params)


async def test_a_gtfs_station_the_planner_lacks_is_answered_from_its_poles(make_planner, aioclient_mock, monkeypatch) -> None:
    """The Kelenföld preset (BKK_CS007896) is null in the planner: FUTÁR
    names its poles (their parent station), and once the planner said it
    does not know the station itself, its board is merged from theirs."""
    boards, _fake, clock = make_planner()
    fake = StationPlanner()
    _use(aioclient_mock, fake)
    station = "BKK_CS007896"
    params = dict(BOARD_PARAMS, stopId=station)
    refs = {"data": {"references": {"stops": {
        "p": {"id": "BKK_F00940", "parentStationId": station}, "q": {"id": "BKK_005510017", "parentStationId": station}}}}}
    # Another answer naming the station's poles teaches nothing (a route's
    # details name hundreds of parent stations); the station's own board does.
    boards.learn("route-details.json", {}, refs)
    boards.learn(BOARD, {"stopId": "BKK_F00940"}, refs)
    assert station not in boards._stations
    boards.learn(BOARD, {"stopId": station}, refs)
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, params)
    assert fake.asked[0]["id"] == "BKK:CS007896"
    clock.now += mb.BOARD_TTL
    doc = await proxy.get(BOARD, params)
    assert doc["source"] == "mavplusz" and doc["data"]["entry"]["stopId"] == station
    assert sorted(v for k, v in fake.asked[1].items() if k[0] == "s" and k[1:].isdigit()) == ["BKK:005510017", "BKK:F00940"]
    assert doc["data"]["references"]["stops"]["BKK_F00940"]["parentStationId"] == station
    # The store keeps it as it keeps FUTÁR's combined stations.
    assert mb.restore_stations(json.loads(json.dumps(boards.stations_data())))[station]["poles"]


class _Hanging:
    """A FUTÁR that answers nothing until released, then fails having taken
    its whole timeout (a hang), on the proxy's clock."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.release = asyncio.Event()
        self.timeouts: list[float] = []

    async def __call__(self, session, api_key, path, params, timeout=None, *, executor=None):
        self.timeouts.append(timeout.total)
        asked = self.clock.now
        await self.release.wait()
        self.clock.now = asked + timeout.total
        raise futar.FutarError("TimeoutError")


async def test_futar_failing_after_its_whole_budget_does_not_ask_the_planner(make_planner, aioclient_mock, monkeypatch) -> None:
    """The planner backing off when the board was asked: FUTÁR has its
    whole 18 s. It times out then, the backoff long over, but the planner's
    6 s would answer past the card's 20 s: FUTÁR's error, the planner not
    asked. Cut short with the planner's token instead, it answers in time."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    hang = _Hanging(clock)
    monkeypatch.setattr(futar, "futar_get", hang)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    limiter.failed(10)
    hang.release.set()
    started = clock.now
    with pytest.raises(futar.FutarError, match="TimeoutError"):
        await proxy.get(BOARD, BOARD_PARAMS)
    assert hang.timeouts == [futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC] and fake.asked == []
    assert clock.now - started == futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    # FUTÁR's outage stands: the planner is asked first, FUTÁR not waited for.
    started = clock.now
    doc = await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="61"))
    assert doc["source"] == "mavplusz" and len(hang.timeouts) == 1 and clock.now == started
    # FUTÁR's probe is cut short with the planner's token instead, and answers in time.
    clock.now += futar.FUTAR_OUTAGE_PROBE_SEC
    started = clock.now
    doc = await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="62"))
    assert doc["source"] == "mavplusz" and hang.timeouts[-1] == futar.FUTAR_FALLBACK_TIMEOUT_SEC
    assert clock.now - started + mb.PLANNER_TIMEOUT.total <= futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC


async def test_a_caller_joining_a_request_under_way_is_timed_from_its_own_ask(make_planner, aioclient_mock, monkeypatch) -> None:
    """Two cards, one FUTÁR request: the first asked 15 s before the second.
    When FUTÁR fails at 18 s the first card has given up (no planner for
    it), the second has time left, and the planner answers it."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    hang = _Hanging(clock)
    monkeypatch.setattr(futar, "futar_get", hang)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    limiter.failed(10)
    first = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(5):
        await asyncio.sleep(0)
    clock.now += 15
    second = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(5):
        await asyncio.sleep(0)
    hang.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert len(hang.timeouts) == 1
    assert isinstance(results[0], futar.FutarError)
    assert results[1]["source"] == "mavplusz" and len(fake.asked) == 1


async def test_a_futar_answer_landing_after_the_entry_is_removed_writes_no_store(
    hass, hass_storage, aioclient_mock, monkeypatch, freezer
) -> None:
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    import custom_components.hungarian_transport as integration
    from custom_components.hungarian_transport import storage

    made = []
    real = mb.PlannerBoards
    monkeypatch.setattr(integration.mavplusz_board, "PlannerBoards",
                        lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    _inject(monkeypatch, FakeClock())
    entry = await _setup(hass)
    (planner,) = made
    # A station learnt and written first: the removal is what deletes it.
    planner.learn(BOARD, {"stopId": "BKK_X"}, {"data": {"references": {"stops": {
        "p": {"id": "BKK_F00940", "parentStationId": "BKK_CSF00940"}}}}})
    freezer.tick(timedelta(seconds=storage.STATIONS_SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert "BKK_CSF00940" in hass_storage[storage.STATIONS_KEY]["data"]["stations"]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert storage.STATIONS_KEY not in hass_storage
    # A card's FUTÁR request under way at the removal answers now.
    planner.learn(BOARD, {"stopId": "BKK_X"}, {"data": {"references": {"stops": {
        "p": {"id": "BKK_F00941", "parentStationId": "BKK_CSF00940"}}}}})
    freezer.tick(timedelta(seconds=storage.STATIONS_SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert storage.STATIONS_KEY not in hass_storage


async def test_a_reload_keeps_the_poles_learnt_just_before(hass, hass_storage, aioclient_mock, monkeypatch) -> None:
    """Learnt within STATIONS_SAVE_DELAY of a reload: written at the unload,
    so the next planner starts with them (the pending write the next one
    replaced lost them before)."""
    import custom_components.hungarian_transport as integration
    from custom_components.hungarian_transport import storage

    made = []
    real = mb.PlannerBoards
    monkeypatch.setattr(integration.mavplusz_board, "PlannerBoards",
                        lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    _inject(monkeypatch, FakeClock())
    entry = await _setup(hass)
    made[0].learn(BOARD, {"stopId": "BKK_X"}, {"data": {"references": {"stops": {
        "p": {"id": "BKK_F00941", "parentStationId": "BKK_CSF00940"}}}}})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[storage.STATIONS_KEY]["data"]["stations"]["BKK_CSF00940"]["poles"] == ["BKK_F00941"]
    assert len(made) == 2 and made[1]._board_ids("BKK_CSF00940") == ("BKK:F00941",)
    # The old planner learns nothing more.
    made[0].learn(BOARD, {"stopId": "BKK_X"}, {"data": {"references": {"stops": {
        "p": {"id": "BKK_F00942", "parentStationId": "BKK_CSF00940"}}}}})
    assert "BKK_F00942" not in made[0].stations_data()["stations"]["BKK_CSF00940"]["poles"]


def _hub_board() -> dict:
    """A hub's board: one dense pattern every minute for eight hours, and a
    sparse one every 30 minutes, both from the fixture's stop times."""
    node = copy.deepcopy(BOARD_DOC["data"]["stop"])
    dense, sparse = node["stoptimesWithoutPatterns"][0], node["stoptimesWithoutPatterns"][1]
    day, start = dense["serviceDay"], WALL - dense["serviceDay"]
    rows = []
    for i in range(480):
        st = copy.deepcopy(dense)
        st.update(realtime=False, scheduledDeparture=start + 60 * i, scheduledArrival=start + 60 * i)
        st["trip"]["gtfsId"] = f"BKK:DENSE{i}"
        rows.append(st)
    for i in range(16):
        st = copy.deepcopy(sparse)
        st.update(realtime=False, scheduledDeparture=start + 1800 * i + 30, scheduledArrival=start + 1800 * i + 30)
        st["trip"]["gtfsId"] = f"BKK:SPARSE{i}"
        rows.append(st)
    for st in rows:
        st["serviceDay"] = day
    node["stoptimesWithoutPatterns"] = rows
    return {"data": {"stop": node}}


def test_each_route_and_headsign_keeps_its_share_of_a_hub_board() -> None:
    """Cut to its 80 earliest rows the hub board above held 80 minutes of
    the dense pattern and three rows of the sparse one: a three-hour card,
    or one for the sparse line's destination, had none past that."""
    board = mb.parse_board(_hub_board(), STOP, WALL)
    doc = mb.render_board(board, "180", WALL)
    times = doc["data"]["entry"]["stopTimes"]
    sparse = [st for st in times if st["tripId"].startswith("BKK_SPARSE")]
    dense = [st for st in times if st["tripId"].startswith("BKK_DENSE")]
    assert len(sparse) == 6 and max(st["departureTime"] for st in sparse) > WALL + 150 * 60
    assert len(dense) == mb.ROWS_PER_PATTERN
    assert [st["departureTime"] for st in times] == sorted(st["departureTime"] for st in times)
    # Only the references of the rows given.
    assert set(doc["data"]["references"]["trips"]) == {st["tripId"] for st in times}
    # Past the cap every pattern keeps the same number, the earliest.
    rows = [{"tripId": f"T{g}-{i}", "stopHeadsign": "x", "departureTime": i} for i in range(50) for g in range(10)]
    trips = {r["tripId"]: {"routeId": r["tripId"].split("-")[0], "tripHeadsign": "x"} for r in rows}
    cut = mb._fair_cut(rows, trips, 205)
    assert len(cut) == 205
    counts = {g: sum(1 for r in cut if r["tripId"].startswith(f"T{g}-")) for g in range(10)}
    assert sorted(counts.values()) == [20] * 5 + [21] * 5


def test_a_cards_board_answer_stays_small() -> None:
    """RENDER_MAX_ROWS rows with their references, at the fixture's real
    sizes, are some 160 kB: the card's answer, not the planner's."""
    rendered = mb.render_board(mb.parse_board(BOARD_DOC, STOP, WALL), "480", WALL)
    times = rendered["data"]["entry"]["stopTimes"]
    per_row = len(json.dumps(rendered).encode()) / len(times)
    assert mb.RENDER_MAX_ROWS * per_row < 256 * 1024
    assert mb.BOARD_MAX_ROWS * mb.STOP_TIME_BYTES <= mb.BOARD_MAX_BYTES


def test_trip_details_carry_the_trips_shape() -> None:
    """OTP2's tripGeometry is FUTÁR's polyline (a Google encoding of 1e5,
    live on 2026-11-19): the card draws the ride from the planner's trip
    details too, which it keeps for the page."""
    query = mb.build_trip_query([mb.TripKey("BKK:A", "20261118")])["query"]
    assert "tripGeometry { points length }" in query
    trip = copy.deepcopy(TRIPS_DOC["data"]["t0"])
    trip["tripGeometry"] = {"points": "}gz`HaoosBPADdBJfH?P@", "length": 7}
    doc = mb.parse_trip(trip, FIRST_TRIP, "20261118", WALL)
    assert doc["data"]["entry"]["polyline"] == {"points": "}gz`HaoosBPADdBJfH?P@", "length": 7}
    for bad in (None, "x", {"points": 5}, {"points": ""}, {"points": "ab\x00c"}, {"points": "a" * (mb.MAX_POLYLINE_CHARS + 1)}):
        trip["tripGeometry"] = bad
        assert "polyline" not in mb.parse_trip(trip, FIRST_TRIP, "20261118", WALL)["data"]["entry"]


def test_learnt_stations_keep_the_most_recently_seen_and_age_out() -> None:
    wall = [float(WALL)]
    boards = mb.PlannerBoards(None, wall=lambda: wall[0])
    for i in range(mb.MAX_CHILD_STOPS):
        boards._learn_pole("BKK_CSF1", f"BKK_F{i}")
    # Seen again, the first pole is the most recent: a new one replaces the second.
    boards._learn_pole("BKK_CSF1", "BKK_F0")
    boards._learn_pole("BKK_CSF1", "BKK_FNEW")
    poles = list(boards._stations["BKK_CSF1"]["poles"])
    assert len(poles) == mb.MAX_CHILD_STOPS and "BKK_F0" in poles and "BKK_F1" not in poles
    for i in range(mb.MAX_STATIONS - 1):
        boards._learn_pole(f"BKK_CSF{i + 10}", "BKK_F1")
    boards._learn_pole("BKK_CSF1", "BKK_F0")
    boards._learn_pole("BKK_CSF99999", "BKK_F1")
    assert "BKK_CSF1" in boards._stations and "BKK_CSF10" not in boards._stations
    # A pole not seen for STATION_MAX_AGE goes, and a station with none.
    wall[0] += mb.STATION_MAX_AGE - 10
    boards._learn_pole("BKK_CSF1", "BKK_F0")
    wall[0] += 11
    data = json.loads(json.dumps(boards.stations_data()))
    assert list(data["stations"]) == ["BKK_CSF1"] and data["stations"]["BKK_CSF1"]["poles"] == ["BKK_F0"]
    again = mb.restore_stations(data, wall[0])
    assert again == boards._stations
    assert mb.restore_stations(data, wall[0] + mb.STATION_MAX_AGE + 1) == {}


def test_a_record_of_before_the_seen_times_still_restores() -> None:
    old = {"stations": {"BKK_CSF2": {"ref": None, "poles": ["BKK_F5"]},
                        "BKK_CSF1": {"ref": {"id": "BKK_CSF1", "name": "A"}, "poles": ["BKK_F1", "BKK_F2"]}}}
    got = mb.restore_stations(old, WALL)
    assert list(got) == ["BKK_CSF2", "BKK_CSF1"]
    assert got["BKK_CSF1"] == {"ref": {"id": "BKK_CSF1", "name": "A"}, "poles": {"BKK_F1": WALL, "BKK_F2": WALL}, "seen": WALL}
    boards = mb.PlannerBoards(None, stations=old, wall=lambda: WALL)
    assert boards._board_ids("BKK_CSF1") == ("BKK:F1", "BKK:F2")


# --- The third review: reports to the store, requests never sent, the
# --- fallback's token and FUTÁR's budget, planner-wide failures, the
# --- documented numbers, and closing.


def test_a_station_seen_more_often_than_daily_is_stored_once_a_day_and_kept() -> None:
    """Every sighting moved the record's "last seen" in memory but was news
    only a day after the previous sighting: a station shown every hour was
    stored once, with its first sighting, and a restart 30 days on dropped
    it. Now a sighting is news a day after the last report, and the last
    record written keeps the station; a pole not seen since ages out."""
    wall = [float(WALL)]
    saved: list = []
    boards = mb.PlannerBoards(
        None, wall=lambda: wall[0],
        on_stations_changed=lambda: saved.append(json.loads(json.dumps(boards.stations_data()))),
    )
    once = {"data": {"references": {"stops": {
        "BKK_F2": {"id": "BKK_F2", "parentStationId": "BKK_CSF1"}}}}}
    hourly = {"data": {"references": {"stops": {
        "BKK_F1": {"id": "BKK_F1", "parentStationId": "BKK_CSF1"}}}}}
    boards.learn("route-details.json", {}, once)
    hours = 31 * 24
    for _ in range(hours):
        boards.learn("route-details.json", {}, hourly)
        wall[0] += 3600
    # Once a day (the first sighting of each pole too), not once an hour.
    assert len(saved) == 1 + hours // 24
    restored = mb.restore_stations(saved[-1], wall[0])
    assert list(restored) == ["BKK_CSF1"] and list(restored["BKK_CSF1"]["poles"]) == ["BKK_F1"]
    assert wall[0] - restored["BKK_CSF1"]["seen"] <= mb.SEEN_RESOLUTION + 3600


async def test_a_board_request_never_sent_does_not_count_as_asked(make_planner, aioclient_mock) -> None:
    """The fallback's share was used up: the board's request was refused and
    nothing sent. A token back, the stop is asked (with the token reserved
    for it), not refused for BOARD_TTL as already asked."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(fallback_burst=1, monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    limiter.client(RuntimeError, fallback=True).take()
    assert await boards.answer(BOARD, BOARD_PARAMS) is None and fake.asked == []
    clock.now += 1 / mavplusz.FALLBACK_RATE
    ticket = boards.reserve(BOARD, BOARD_PARAMS)
    assert ticket is not None
    doc = await boards.answer(BOARD, BOARD_PARAMS, ticket)
    ticket.release()
    assert doc["source"] == "mavplusz" and len(fake.asked) == 1
    # Neither does one the backoff held back.
    limiter.failed(1)
    other = dict(BOARD_PARAMS, stopId="BKK_F00941")
    assert await boards.answer(BOARD, other) is None and len(fake.asked) == 1
    clock.now += 1 + 1 / mavplusz.FALLBACK_RATE
    await boards.answer(BOARD, other)
    assert len(fake.asked) == 2


async def test_a_board_asked_and_unanswered_is_not_offered_within_the_ttl(make_planner, aioclient_mock, monkeypatch) -> None:
    """A board whose request failed is not asked again within BOARD_TTL,
    so the planner cannot answer it then: FUTÁR is not cut short for it,
    also when the backoff is over before that."""
    monkeypatch.setattr(mb, "FAIL_BACKOFF", 1)
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    fake.down = True
    assert await boards.answer(BOARD, BOARD_PARAMS) is None and len(fake.asked) == 1
    clock.now += 1
    assert not limiter.backing_off()
    assert not boards.can_answer(BOARD, BOARD_PARAMS) and boards.reserve(BOARD, BOARD_PARAMS) is None
    clock.now += mb.BOARD_TTL - 1
    fake.down = False
    assert boards.can_answer(BOARD, BOARD_PARAMS)
    # A request still under way is no failure: a card asking meanwhile joins it.
    gate = asyncio.Event()

    async def slow(method, url, data):
        await gate.wait()
        return await fake(method, url, data)

    _use(aioclient_mock, slow)
    first = asyncio.ensure_future(boards.answer(BOARD, BOARD_PARAMS))
    for _ in range(5):
        await asyncio.sleep(0)
    assert boards.can_answer(BOARD, BOARD_PARAMS)
    gate.set()
    assert (await first)["source"] == "mavplusz"


async def test_the_token_of_a_request_whose_callers_all_left_goes_back(make_planner, aioclient_mock, monkeypatch) -> None:
    """FUTÁR in its outage: its probe cut short, with a planner token
    reserved, and every card detached before FUTÁR failed. Nobody falls
    back, and the token goes back (before, it was kept for good)."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    gate = asyncio.Event()

    async def failing(session, api_key, path, params, timeout=None, *, executor=None):
        await gate.wait()
        raise futar.FutarError("ClientConnectorError")

    monkeypatch.setattr(futar, "futar_get", failing)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    _futar_down(proxy)
    call = asyncio.ensure_future(proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_F01")))
    for _ in range(3):
        await asyncio.sleep(0)
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    call.cancel()
    await asyncio.gather(call, return_exceptions=True)
    gate.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST and fake.asked == []
    # Two cards sharing the probe's request (the second found no token of
    # the share left to ask the planner first), one of them gone: the other
    # falls back on the probe's token, spent once and not given back on top.
    gate.clear()
    _futar_down(proxy)
    other = limiter.client(RuntimeError, fallback=True)
    for _ in range(mavplusz.FALLBACK_BURST - 1):
        other.take()
    first = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    second = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(3):
        await asyncio.sleep(0)
    first.cancel()
    gate.set()
    assert (await second)["source"] == "mavplusz" and len(fake.asked) == 1
    assert limiter._fallback._tokens == 0


async def test_futar_keeps_its_whole_budget_when_the_token_wait_leaves_no_room(make_planner, aioclient_mock, monkeypatch) -> None:
    """A token wait (by the clock) past what FUTÁR's share and the planner's
    6 s leave: FUTÁR was cut to 8 s anyway, and the planner then dropped as
    too late, so the card had an error FUTÁR had ten more seconds for. Now
    nothing is reserved and FUTÁR has the rest of the card's budget."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    hang = _Hanging(clock)
    hang.release.set()
    monkeypatch.setattr(futar, "futar_get", hang)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    room = futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC + futar.FUTAR_FALLBACK_SLACK_SEC
    room -= futar.FUTAR_FALLBACK_TIMEOUT_SEC + mb.PLANNER_TIMEOUT.total
    waits = iter([room + 0.3, room])

    async def slow_acquire() -> float:
        wait = next(waits)
        clock.now += wait
        return wait

    monkeypatch.setattr(proxy._rate, "acquire", slow_acquire)
    _futar_down(proxy)
    with pytest.raises(futar.FutarError, match="TimeoutError"):
        await proxy.get(BOARD, BOARD_PARAMS)
    assert hang.timeouts == [pytest.approx(futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC - room - 0.3)]
    assert fake.asked == [] and limiter._fallback._tokens == mavplusz.FALLBACK_BURST
    # Within the room, FUTÁR's probe is cut short and the planner answers in time.
    _probe_due(proxy)
    doc = await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="61"))
    assert doc["source"] == "mavplusz" and hang.timeouts[-1] == futar.FUTAR_FALLBACK_TIMEOUT_SEC


async def test_futar_is_cut_short_only_while_its_outage_stands(make_planner, aioclient_mock, monkeypatch) -> None:
    """While FUTÁR answers, no planner token is reserved and FUTÁR has its
    whole budget; a request failing fast is still answered by the planner
    (its own token), and starts the outage: from then on FUTÁR's probe is
    cut short with a token reserved (the other requests ask the planner
    first), until it answers again."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    fake = AnyStopPlanner()
    _use(aioclient_mock, fake)
    seen: list = []
    failing = [False]

    async def futar_get(session, api_key, path, params, timeout=None, *, executor=None):
        seen.append(timeout.total)
        assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - (timeout.total < 10) - len(fake.asked)
        if failing[0]:
            raise futar.FutarError("HTTP 503")
        return {"status": "OK", "data": {"entry": {"stopTimes": []}}, "from": "futar"}

    monkeypatch.setattr(futar, "futar_get", futar_get)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    whole = futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    assert (await proxy.get(BOARD, BOARD_PARAMS))["from"] == "futar"
    failing[0] = True
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="61")))["source"] == "mavplusz"
    _probe_due(proxy)
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_F00941")))["source"] == "mavplusz"
    # Not the probe: the planner first, FUTÁR not asked.
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_F00944")))["source"] == "mavplusz"
    assert len(seen) == 3
    failing[0] = False
    _probe_due(proxy)
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_F00942")))["from"] == "futar"
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, stopId="BKK_F00943")))["from"] == "futar"
    assert seen == [whole, whole, futar.FUTAR_FALLBACK_TIMEOUT_SEC, futar.FUTAR_FALLBACK_TIMEOUT_SEC, whole]
    # The planner was asked for the three boards FUTÁR did not give, nothing reserved is left.
    assert len(fake.asked) == 3 and limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 3


async def test_a_page_that_is_not_json_backs_the_planner_off(make_planner, aioclient_mock, caplog) -> None:
    """An HTTP 200 that is no JSON (a maintenance or Cloudflare page) is the
    planner failing, not one stop's request: the shared backoff, a warning,
    and the stop is asked again after it (and BOARD_TTL after its failed
    ask), not after REQUEST_FAIL_TTL."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)

    async def maintenance(method, url, data):
        return _not_json(method, url, data)

    _use(aioclient_mock, maintenance)
    assert await boards.answer(BOARD, BOARD_PARAMS) is None
    assert limiter.backing_off()
    assert any("not JSON" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    clock.now += max(mb.FAIL_BACKOFF, mb.BOARD_TTL)
    assert boards.can_answer(BOARD, BOARD_PARAMS)
    _use(aioclient_mock, fake)
    assert (await boards.answer(BOARD, BOARD_PARAMS))["source"] == "mavplusz"


def test_the_documented_numbers_are_the_constants() -> None:
    """docs/realtime.md "BKK boards when FUTÁR fails" and "Polling"."""
    from custom_components.hungarian_transport import realtime, storage

    assert (mavplusz.FALLBACK_RATE, mavplusz.FALLBACK_BURST) == (0.25, 15)
    assert (mavplusz.VEHICLE_RATE, mavplusz.VEHICLE_BURST) == (0.1, 6)
    assert storage.STATIONS_SAVE_DELAY == 300
    # Backoffs: the planner a minute (the hub), 30 s (the boards), the vehicles 3 minutes.
    assert (realtime.FAIL_BACKOFF, mb.FAIL_BACKOFF, realtime.VEHICLE_FAIL_BACKOFF) == (60, 30, 180)
    # The card's 20 s, FUTÁR's 8 s of it, the planner's 6 s, half a second of slack.
    assert (futar.CARD_TIMEOUT_SEC, futar.FUTAR_FALLBACK_TIMEOUT_SEC, mb.PLANNER_TIMEOUT.total) == (20, 8, 6)
    assert futar.FUTAR_FALLBACK_SLACK_SEC == 0.5
    # In FUTÁR's outage, one probe every 30 s; a reserved token goes back within a second of its take.
    assert (futar.FUTAR_OUTAGE_PROBE_SEC, mb.REFUND_WITHIN_SEC) == (30, 1.0)
    # Per stop per 35 s, eight hours, 60 departures a pattern, 80 rows a pattern, 400 a card, 2 MB.
    assert (mb.BOARD_TTL, mb.BOARD_WINDOW_MIN, mb.DEPARTURES_PER_PATTERN) == (35, 480, 60)
    assert (mb.ROWS_PER_PATTERN, mb.RENDER_MAX_ROWS, mb.BOARD_MAX_BYTES) == (80, 400, 2 * 1024 * 1024)
    # Trip details: 50 ms a batch, 40 trips a request, cached 60 s.
    assert (mb.TRIP_BATCH_WAIT, mb.MAX_TRIPS_PER_REQUEST, mb.TRIP_TTL) == (0.05, 40, 60)
    # Stations: 24 poles, 20 departures a pattern each, 512 stations, 30 days, 12 hours known, 10 minutes failing.
    assert (mb.MAX_CHILD_STOPS, mb.CHILD_DEPARTURES, mb.MAX_STATIONS) == (24, 20, 512)
    assert (mb.STATION_MAX_AGE, mb.PROBE_TTL, mb.REQUEST_FAIL_TTL) == (30 * 86400, 12 * 3600, 600)
    # A null 10 minutes, 12 hours once it was two in a row.
    assert (mb.UNKNOWN_TTL, mb.UNKNOWN_REPEATS) == (600, 2)
    assert mb.NIGHT_END_HOUR == 5 and len(mb.SEED_STATIONS["BKK_CSF01131"]["poles"]) == 15


def test_a_closed_planner_reserves_nothing() -> None:
    boards = mb.PlannerBoards(None, wall=lambda: WALL)
    assert boards.reserve(BOARD, BOARD_PARAMS) is not None
    boards.close()
    assert boards.can_answer(BOARD, BOARD_PARAMS) and boards.reserve(BOARD, BOARD_PARAMS) is None


def test_a_stored_time_in_the_future_counts_as_now() -> None:
    """A clock set back, an edited file: a "last seen" ahead of now kept a
    station for longer than STATION_MAX_AGE; it counts as now instead."""
    ahead = WALL + 10 * 86400
    data = {"stations": {"BKK_CSF1": {"ref": None, "poles": ["BKK_F1"], "seen": {"BKK_F1": ahead}, "at": ahead}}}
    got = mb.restore_stations(data, WALL)
    assert got["BKK_CSF1"]["seen"] == WALL and got["BKK_CSF1"]["poles"] == {"BKK_F1": WALL}
    # Written again unseen, it ages out from then.
    again = mb.PlannerBoards(None, stations=data, wall=lambda: WALL).stations_data()
    assert mb.restore_stations(again, WALL + mb.STATION_MAX_AGE + 1) == {}


async def test_closing_the_planner_answers_the_trip_details_under_way() -> None:
    """Closed while a batch of trip details waits (the entry unloaded): the
    batch is cancelled, its callers have no answer at once instead of
    waiting for good, and no batch is started after that."""
    gate = asyncio.Event()

    async def sleep(_seconds: float) -> None:
        await gate.wait()

    boards = mb.PlannerBoards(None, sleep=sleep, wall=lambda: WALL)
    for ready in (0, 1):
        call = asyncio.ensure_future(boards.answer(TRIP, {"tripId": f"BKK_X{ready}"}))
        for _ in range(ready * 3 + 1):
            await asyncio.sleep(0)
        if not ready:
            # Closed before the trip's _batched ran: it refuses at once, and
            # no batch is made (a batch's task made but not run yet is
            # test_closing_the_planner_before_a_batch_runs_answers_its_trips).
            boards.close()
            assert await asyncio.wait_for(call, 1) is None
            boards = mb.PlannerBoards(None, sleep=sleep, wall=lambda: WALL)
            continue
        assert boards._sending
        boards.close()
        assert await asyncio.wait_for(call, 1) is None
    await asyncio.sleep(0)
    assert not boards._sending
    assert await boards.answer(TRIP, {"tripId": FIRST_TRIP}) is None and not boards._sending


# --- The fourth review: one token for a batch of trip details, the batch
# --- closed before it ran, the tickets' owners, the store's reports, the
# --- planner closed under a fallback and closed once.


class AnyTripPlanner(AnyStopPlanner):
    """The fake planner answering the fixture's board for every stop and
    the fixture's trip for every trip asked."""

    async def __call__(self, method, url, data):
        body = json.loads(data)
        if "stop(id: $id)" in body["query"]:
            return await super().__call__(method, url, data)
        self.asked.append(body["variables"])
        answer = {}
        for alias in (k for k in body["variables"] if k[0] == "t"):
            trip = copy.deepcopy(TRIPS_DOC["data"]["t0"])
            trip["gtfsId"] = body["variables"][alias]
            answer[alias] = trip
        return AiohttpClientMockResponse(method, url, json={"data": answer})


def _trip_params(i: int) -> dict:
    return {"tripId": f"BKK_D2528{i:04d}"}


async def test_a_dashboards_trip_details_share_one_planner_token(make_planner, aioclient_mock, monkeypatch) -> None:
    """FUTÁR hanging in its outage, a card's 40 trip details at once: every
    one is cut short and answered by one planner request on one token.
    Before, each reserved a token of its own: 15 (the share) were cut short
    and answered, and 25 kept FUTÁR's whole budget and timed out."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    fake = AnyTripPlanner()
    _use(aioclient_mock, fake)
    hang = _Hanging(clock)
    monkeypatch.setattr(futar, "futar_get", hang)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    _futar_down(proxy)
    _futar_first(proxy)
    calls = [asyncio.ensure_future(proxy.get(TRIP, _trip_params(i))) for i in range(40)]
    for _ in range(5):
        await asyncio.sleep(0)
    assert hang.timeouts == [futar.FUTAR_FALLBACK_TIMEOUT_SEC] * 40
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    hang.release.set()
    got = await asyncio.gather(*calls)
    assert [g["source"] for g in got] == ["mavplusz"] * 40
    assert len(fake.asked) == 1 and len([k for k in fake.asked[0] if k[0] == "t"]) == 40
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1


async def test_trip_details_leave_the_share_for_another_cards_board(make_planner, aioclient_mock, monkeypatch) -> None:
    """While a card's 40 trip details wait for FUTÁR, another card's board
    still has a token: FUTÁR is cut short for it and the planner answers.
    Before, the trips held the whole share: FUTÁR had its 18 s for the
    board, and the planner could not answer it (can_answer False)."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    fake = AnyTripPlanner()
    _use(aioclient_mock, fake)
    hang = _Hanging(clock)
    monkeypatch.setattr(futar, "futar_get", hang)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    _futar_down(proxy)
    _futar_first(proxy)
    calls = [asyncio.ensure_future(proxy.get(TRIP, _trip_params(i))) for i in range(40)]
    for _ in range(5):
        await asyncio.sleep(0)
    assert boards.can_answer(BOARD, BOARD_PARAMS)
    board = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(5):
        await asyncio.sleep(0)
    assert hang.timeouts[-1] == futar.FUTAR_FALLBACK_TIMEOUT_SEC
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 2
    hang.release.set()
    got = await asyncio.gather(*calls, board)
    assert [g["source"] for g in got] == ["mavplusz"] * 41
    assert len(fake.asked) == 2 and limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 2


def test_trip_tickets_share_a_token_per_batch_window_and_request() -> None:
    """The trip details reserved within TRIP_BATCH_WAIT share one token, for
    one request's 40 trip keys at most (two a trip at night); a later one,
    or one past the 40, has one of its own. The token goes back once every
    ticket on it is released unspent, and is spent once."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=FakeClock(stopped=True).monotonic)
    wall = [float(WALL)]
    boards = mb.PlannerBoards(None, monotonic=clock.monotonic, wall=lambda: wall[0], limiter=limiter)

    def left() -> float:
        return limiter._fallback._tokens

    tickets = [boards.reserve(TRIP, _trip_params(i)) for i in range(41)]
    assert all(tickets) and left() == mavplusz.FALLBACK_BURST - 2
    clock.now += mb.TRIP_BATCH_WAIT * 2
    later = boards.reserve(TRIP, _trip_params(41))
    assert left() == mavplusz.FALLBACK_BURST - 3
    # Released unspent: each token goes back with its last ticket.
    for ticket in tickets[:39]:
        ticket.release()
        ticket.release()
    assert left() == mavplusz.FALLBACK_BURST - 3
    tickets[39].release()
    assert left() == mavplusz.FALLBACK_BURST - 2
    # Spent once by the request of any ticket on it: given back by none.
    assert later.spend() and not later.spend()
    later.release()
    tickets[40].release()
    assert left() == mavplusz.FALLBACK_BURST - 1
    # At night a trip no board dated is asked for two days: 20 fill a request.
    wall[0] = WALL - 17 * 3600
    assert len(boards._dates_to_try()) == 2
    clock.now += mb.TRIP_BATCH_WAIT * 2
    night = [boards.reserve(TRIP, _trip_params(i)) for i in range(21)]
    assert all(night) and left() == mavplusz.FALLBACK_BURST - 3


def _trip_reserver() -> tuple[mb.PlannerBoards, mavplusz.PlannerLimiter, FakeClock]:
    """PlannerBoards for reserve(TRIP_PATH, …) alone, both clocks stopped:
    nothing refills, and a backoff lasts until the test ends."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=FakeClock(stopped=True).monotonic)
    boards = mb.PlannerBoards(None, monotonic=clock.monotonic, wall=lambda: WALL, limiter=limiter)
    return boards, limiter, clock


def test_a_trip_that_failed_lately_reserves_no_token() -> None:
    """A trip whose planner request failed for its own sake within
    REQUEST_FAIL_TTL is not asked again: it reserves nothing, neither a
    token of its own nor a share of the one open for the others (FUTÁR
    keeps its whole budget for it). After REQUEST_FAIL_TTL it does again."""
    boards, limiter, clock = _trip_reserver()
    failed = _trip_params(1)
    boards._fail(("trip", boards._trip_id(failed)))
    assert boards.reserve(TRIP, failed) is None
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST
    other = boards.reserve(TRIP, _trip_params(0))
    assert other is not None and limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    assert boards.reserve(TRIP, failed) is None
    assert other._token.holders == 1 and other._token.weight == 1
    clock.now += mb.REQUEST_FAIL_TTL
    assert boards.reserve(TRIP, failed) is not None


def test_no_trip_joins_the_shared_token_while_the_planner_backs_off() -> None:
    """The planner failed after a trip reserved the shared token: a trip
    reserved in the same window during the backoff gets no share of it
    (its request would not go, so FUTÁR keeps its whole budget for it)."""
    boards, limiter, _clock = _trip_reserver()
    first = boards.reserve(TRIP, _trip_params(0))
    assert first is not None and first._token.held
    limiter.failed(mb.FAIL_BACKOFF)
    assert boards._rate.backing_off()
    assert boards.reserve(TRIP, _trip_params(1)) is None
    assert first._token.holders == 1 and first._token.weight == 1
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    first.release()
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST


def test_a_trip_reserved_after_the_shared_token_was_spent_has_a_token_of_its_own() -> None:
    """The shared token spent by a request within TRIP_BATCH_WAIT of its
    opening: the next trip reserved does not join it (its request could
    not spend it), it opens a token of its own and spends that."""
    boards, limiter, _clock = _trip_reserver()
    first = boards.reserve(TRIP, _trip_params(0))
    assert first.spend()
    later = boards.reserve(TRIP, _trip_params(1))
    assert later is not None and later._token is not first._token
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 2
    assert later.spend()


def test_a_trip_reserved_after_the_shared_token_went_back_has_a_token_of_its_own() -> None:
    """The shared token refunded (its one ticket released unspent) within
    TRIP_BATCH_WAIT of its opening: the next trip reserved does not join
    it, it opens and holds a token of its own."""
    boards, limiter, _clock = _trip_reserver()
    first = boards.reserve(TRIP, _trip_params(0))
    first.release()
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST
    later = boards.reserve(TRIP, _trip_params(1))
    assert later is not None and later._token is not first._token
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    later.release()
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST


async def test_a_later_batch_on_a_spent_shared_token_takes_one_of_its_own(make_planner, aioclient_mock, monkeypatch) -> None:
    """Two trips share a token, but FUTÁR fails them apart: the first
    trip's batch spends the shared token; the second's batch, holding only
    a ticket on that spent token, takes a token when its request goes and
    still answers. Two requests, two tokens, both cards answered."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=FakeClock(stopped=True).monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    fake = AnyTripPlanner()
    _use(aioclient_mock, fake)
    gates = {0: asyncio.Event(), 1: asyncio.Event()}

    async def failing(session, api_key, path, params, timeout=None, *, executor=None):
        await gates[int(params["tripId"][-4:])].wait()
        raise futar.FutarError("TimeoutError")

    monkeypatch.setattr(futar, "futar_get", failing)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    _futar_down(proxy)
    _futar_first(proxy)
    calls = [asyncio.ensure_future(proxy.get(TRIP, _trip_params(i))) for i in range(2)]
    for _ in range(5):
        await asyncio.sleep(0)
    # One token for both, reserved before FUTÁR was asked.
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    gates[0].set()
    assert (await calls[0])["source"] == "mavplusz"
    assert len(fake.asked) == 1 and limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    assert not calls[1].done()
    gates[1].set()
    assert (await calls[1])["source"] == "mavplusz"
    assert len(fake.asked) == 2 and limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 2
    assert [len([k for k in asked if k[0] == "t"]) for asked in fake.asked] == [1, 1]


async def test_closing_the_planner_before_a_batch_runs_answers_its_trips() -> None:
    """Closed right after a trip's details made their batch, before its task
    first ran: the task is cancelled without running, so its own cleanup
    never settles the batch. close() does, and the caller has no answer
    instead of waiting for good."""
    gate = asyncio.Event()
    ran: list = []

    async def sleep(seconds: float) -> None:
        ran.append(seconds)
        await gate.wait()

    boards = mb.PlannerBoards(None, sleep=sleep, wall=lambda: WALL)
    key = mb.TripKey("BKK:X0", "20261118")
    call = asyncio.ensure_future(boards._batched(key, None))
    await asyncio.sleep(0)
    # _batched made the batch and its task, which has not run.
    assert boards._sending and ran == []
    boards.close()
    with pytest.raises(mb.PlannerError, match="closed"):
        await asyncio.wait_for(call, 1)
    assert ran == [] and not boards._sending


async def test_a_ticket_is_released_by_the_last_caller_that_saw_it(make_planner, aioclient_mock, monkeypatch) -> None:
    """Two cards share a failed FUTÁR request and its planner token. The
    first has no fallback (asked 13 s before FUTÁR failed: too late for
    the card) and leaves first; the second's planner request spends the
    token reserved, takes no other and gives none back. Then a board the
    planner has cached answers both cards: the unspent token comes back."""
    clock = FakeClock(stopped=True)
    # The limiter's clock stands still: its shares do not refill meanwhile.
    limiter = mavplusz.PlannerLimiter(monotonic=FakeClock(stopped=True).monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    gate = asyncio.Event()

    took = [13]

    async def failing(session, api_key, path, params, timeout=None, *, executor=None):
        asked = clock.now
        await gate.wait()
        clock.now = asked + took[0]
        raise futar.FutarError("TimeoutError")

    monkeypatch.setattr(futar, "futar_get", failing)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    taken: list = []
    refunded: list = []
    real_take, real_refund = boards._rate.take, boards._rate.refund
    monkeypatch.setattr(boards._rate, "take", lambda: taken.append(1) or real_take())
    monkeypatch.setattr(boards._rate, "refund", lambda: refunded.append(1) or real_refund())
    _futar_down(proxy)
    _futar_first(proxy)
    first = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(3):
        await asyncio.sleep(0)
    clock.now += 7
    second = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(3):
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(results[0], futar.FutarError) and results[1]["source"] == "mavplusz"
    assert len(fake.asked) == 1 and taken == [] and refunded == []
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    # The planner's board cached: both cards answered from it, nothing spent.
    gate.clear()
    took[0] = 0
    calls = [asyncio.ensure_future(proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="61"))) for _ in range(2)]
    for _ in range(3):
        await asyncio.sleep(0)
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 2
    gate.set()
    assert [g["source"] for g in await asyncio.gather(*calls)] == ["mavplusz"] * 2
    assert len(fake.asked) == 1 and refunded == [1]
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1


async def test_a_later_request_of_the_same_key_holds_no_earlier_ticket(make_planner, aioclient_mock, monkeypatch) -> None:
    """A card's FUTÁR request failed, its fallback still under way (the
    planner's board asked for another card), when the next card asks the
    same board of FUTÁR again. The first token goes back when the first
    card is done, not only once nobody asks for that board any more; and
    the first card finishing leaves the next request's callers alone: its
    fallback still gets the token reserved for it (FUTÁR was cut short
    for it), not none."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    handed: list = []
    real_answer = boards.answer

    async def answer(path, params, ticket=None):
        handed.append((params.get("minutesAfter"), ticket))
        return await real_answer(path, params, ticket)

    monkeypatch.setattr(boards, "answer", answer)
    planner_gate = asyncio.Event()

    async def slow(method, url, data):
        await planner_gate.wait()
        return await fake(method, url, data)

    _use(aioclient_mock, slow)
    hang = asyncio.Event()
    modes = ["fail", "fail", "hang"]

    async def futar_get(session, api_key, path, params, timeout=None, *, executor=None):
        if modes.pop(0) == "hang":
            await hang.wait()
        raise futar.FutarError("ClientConnectorError")

    monkeypatch.setattr(futar, "futar_get", futar_get)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    _futar_down(proxy)
    _futar_first(proxy)
    later = dict(BOARD_PARAMS, minutesAfter="61")

    async def step(params):
        call = asyncio.ensure_future(proxy.get(BOARD, params))
        for _ in range(5):
            await asyncio.sleep(0)
        return call

    other = await step(BOARD_PARAMS)
    first = await step(later)
    second = await step(later)
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 3 and not first.done()
    planner_gate.set()
    assert [g["source"] for g in await asyncio.gather(other, first)] == ["mavplusz"] * 2
    # The first card's token is back; the second card's is held for it.
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 2
    hang.set()
    assert (await second)["source"] == "mavplusz" and len(fake.asked) == 1
    assert limiter._fallback._tokens == mavplusz.FALLBACK_BURST - 1
    # Each card's fallback had the token reserved for its own request.
    assert [after for after, _ in handed] == ["60", "61", "61"]
    assert all(ticket is not None for _, ticket in handed)
    assert len({id(ticket) for _, ticket in handed}) == 3


def _pole_refs(station: str, poles) -> dict:
    return {"data": {"references": {"stops": {p: {"id": p, "parentStationId": station} for p in poles}}}}


def test_the_reports_for_the_store_stay_bounded() -> None:
    """The stations and poles dropped at the limits are forgotten in the
    reports too: they stay about as many as the stations and poles kept."""
    boards = mb.PlannerBoards(None, wall=lambda: WALL)
    for i in range(mb.MAX_STATIONS + 50):
        boards.learn("route-details.json", {}, _pole_refs(f"BKK_CSF{i:05d}", [f"BKK_F{i:05d}"]))
    assert len(boards._stations) == mb.MAX_STATIONS
    assert len(boards._reported) <= mb.MAX_STATIONS
    poles = [f"BKK_F9{i:04d}" for i in range(mb.MAX_CHILD_STOPS + 10)]
    for pole in poles:
        boards.learn("route-details.json", {}, _pole_refs("BKK_CSF99999", [pole]))
    assert len(boards._stations["BKK_CSF99999"]["poles"]) == mb.MAX_CHILD_STOPS
    assert len(boards._reported["BKK_CSF99999"]) <= mb.MAX_CHILD_STOPS + 1


def test_a_station_or_pole_dropped_and_seen_again_is_news_for_the_store() -> None:
    """A station or a pole dropped at the limits and named again within a
    day is news (the store has lost it), however recently it was reported
    before it was dropped; its next sighting within a day is not."""
    wall = [float(WALL)]
    saved: list = []
    boards = mb.PlannerBoards(None, wall=lambda: wall[0], on_stations_changed=lambda: saved.append(1))

    def seen(station: str, pole: str) -> bool:
        before = len(saved)
        boards.learn("route-details.json", {}, _pole_refs(station, [pole]))
        return len(saved) > before

    assert seen("BKK_CSF00001", "BKK_F00001")
    wall[0] += 3600
    for i in range(2, mb.MAX_STATIONS + 2):
        seen(f"BKK_CSF{i:05d}", f"BKK_F{i:05d}")
    assert "BKK_CSF00001" not in boards._stations
    wall[0] += 3600
    assert seen("BKK_CSF00001", "BKK_F00001")
    assert "BKK_CSF00001" in boards.stations_data()["stations"]
    wall[0] += mb.SEEN_RESOLUTION - 3600
    assert not seen("BKK_CSF00001", "BKK_F00001")
    # A pole pushed out by MAX_CHILD_STOPS newer ones, the station reported just now.
    assert seen("BKK_CSF00002", "BKK_F20000")
    for i in range(1, mb.MAX_CHILD_STOPS + 1):
        assert seen("BKK_CSF00002", f"BKK_F2{i:04d}")
    assert "BKK_F20000" not in boards._stations["BKK_CSF00002"]["poles"]
    wall[0] += 3600
    assert seen("BKK_CSF00002", "BKK_F20000")
    assert "BKK_F20000" in boards.stations_data()["stations"]["BKK_CSF00002"]["poles"]
    wall[0] += 3600
    assert not seen("BKK_CSF00002", "BKK_F20000")


async def test_a_closed_planner_asks_nothing_for_a_fallback_under_way(make_planner, aioclient_mock, monkeypatch) -> None:
    """FUTÁR failing just after the entry was unloaded: the fallback under
    way sends no board request (before, it did), and FUTÁR's error stands."""
    clock = FakeClock(stopped=True)
    boards, fake, clock = make_planner(clock)
    hang = _Hanging(clock)
    monkeypatch.setattr(futar, "futar_get", hang)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    _futar_down(proxy)
    call = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(3):
        await asyncio.sleep(0)
    proxy.close()
    hang.release.set()
    with pytest.raises(futar.FutarError, match="TimeoutError"):
        await call
    assert fake.asked == []
    assert await boards.answer(BOARD, BOARD_PARAMS) is None and fake.asked == []


async def test_unloading_closes_the_planner_once_and_writes_its_stations_once(
    hass, hass_storage, aioclient_mock, monkeypatch
) -> None:
    """The proxy closes the planner it was handed (its one owner); the
    unload then writes what it learnt, once, closed by then."""
    import custom_components.hungarian_transport as integration
    from custom_components.hungarian_transport import storage

    made = []
    real = mb.PlannerBoards
    monkeypatch.setattr(integration.mavplusz_board, "PlannerBoards",
                        lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    writes = []
    real_save = storage.async_save_stations

    async def save(hass_, data):
        writes.append((made[0]._closed, data))
        await real_save(hass_, data)

    monkeypatch.setattr(storage, "async_save_stations", save)
    _inject(monkeypatch, FakeClock())
    entry = await _setup(hass)
    (planner,) = made
    closes = []
    real_close = planner.close
    monkeypatch.setattr(planner, "close", lambda: closes.append(1) or real_close())
    planner.learn(BOARD, {"stopId": "BKK_X"}, _pole_refs("BKK_CSF00940", ["BKK_F00941"]))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert closes == [1]
    assert [(closed, data["stations"]["BKK_CSF00940"]["poles"]) for closed, data in writes] == [(True, ["BKK_F00941"])]
    assert hass_storage[storage.STATIONS_KEY]["data"]["stations"]["BKK_CSF00940"]["poles"] == ["BKK_F00941"]


# --- The review of 2026-11-19 (round 6): decoding off the loop, FUTÁR's
# --- outage probed instead of waited for, the board's TTL, one null, one
# --- bad alias, bounded shapes, refunds after a hold, and the gaps.


def _padded(doc: dict, size: int) -> str:
    """doc as JSON text of at least size bytes (a field nothing reads)."""
    return json.dumps(dict(doc, extensions={"pad": "x" * size}))


async def test_a_large_answer_is_decoded_and_parsed_off_the_event_loop(make_planner, aioclient_mock) -> None:
    """A board answer runs to 2 MB, and decoding it with its rows took some
    56 ms on a laptop: one of EXECUTOR_BYTES or more is decoded and parsed
    in the executor in one go; a smaller one, nearly every answer, on the
    loop. Trip details alike."""
    ran: list = []

    async def executor(func, *args):
        ran.append(func)
        return func(*args)

    boards, _fake, clock = make_planner(executor=executor)
    sizes = {"board": 0, "trip": 0}

    async def answer(method, url, data):
        body = json.loads(data)
        if "stop(id:" in body["query"]:
            doc = {"data": {"stop": copy.deepcopy(BOARD_DOC["data"]["stop"])}}
            return AiohttpClientMockResponse(method, url, text=_padded(doc, sizes["board"]))
        doc = {"data": {k: copy.deepcopy(TRIPS_DOC["data"]["t0"]) for k in body["variables"] if k[0] == "t"}}
        return AiohttpClientMockResponse(method, url, text=_padded(doc, sizes["trip"]))

    _use(aioclient_mock, answer)
    small = await boards.answer(BOARD, BOARD_PARAMS)
    assert len(small["data"]["entry"]["stopTimes"]) == 6 and ran == []
    sizes["board"] = mb.EXECUTOR_BYTES
    clock.now += mb.BOARD_TTL
    big = await boards.answer(BOARD, BOARD_PARAMS)
    assert big == small and ran == [mb._decode_parse]
    assert (await boards.answer(TRIP, {"tripId": FIRST_TRIP}))["data"]["entry"]["tripId"] == FIRST_TRIP
    assert ran == [mb._decode_parse]
    sizes["trip"] = mb.EXECUTOR_BYTES
    trip = await boards.answer(TRIP, {"tripId": "BKK_D25280606"})
    assert trip["data"]["entry"]["tripId"] == "BKK_D25280606" and ran == [mb._decode_parse] * 2


async def test_a_large_answer_of_the_wrong_shape_fails_its_request_alone(make_planner, aioclient_mock) -> None:
    """Parsed in the executor, an answer of the wrong shape is still that
    stop's failure (REQUEST_FAIL_TTL), not the planner's (no backoff)."""
    ran: list = []

    async def executor(func, *args):
        ran.append(func)
        return func(*args)

    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter, executor=executor)

    async def wrong(method, url, data):
        return AiohttpClientMockResponse(method, url, text=_padded({"data": {"stop": [1, 2]}}, mb.EXECUTOR_BYTES))

    _use(aioclient_mock, wrong)
    assert await boards.answer(BOARD, BOARD_PARAMS) is None
    assert ran and not limiter.backing_off() and boards._is_failing(("board", STOP))


def _counting_futar(monkeypatch, clock: FakeClock, results: list) -> list:
    """FUTÁR failing (results: "fail", "hang" taking its whole timeout, or
    "ok"; the last one repeats): the timeouts of the calls it got."""
    calls: list = []

    async def futar_get(session, api_key, path, params, timeout=None, *, executor=None):
        calls.append((path, timeout.total))
        mode = results.pop(0) if len(results) > 1 else results[0]
        if mode == "ok":
            return {"status": "OK", "data": {"entry": {"stopTimes": []}, "references": {}}, "from": "futar"}
        if mode == "hang":
            clock.now += timeout.total
            raise futar.FutarError("TimeoutError")
        raise futar.FutarError("HTTP 503")

    monkeypatch.setattr(futar, "futar_get", futar_get)
    return calls


class _FutarTokens:
    """The tokens of a proxy's FUTÁR limit its requests took since the last
    taken(): the bucket's tokens before and after, what it refilled
    meanwhile (the clock moved, after the request took its token) added
    back. Exact as long as the bucket was not full again by then: read it
    (taken()) after the clock is moved, before the requests counted."""

    def __init__(self, proxy: futar.FutarProxy, clock: FakeClock) -> None:
        self._proxy, self._clock = proxy, clock
        self._tokens, self._at = proxy.tokens, clock.now

    def taken(self) -> float:
        tokens, at = self._proxy.tokens, self._clock.now
        taken = self._tokens + futar.FUTAR_RATE * (at - self._at) - tokens
        self._tokens, self._at = tokens, at
        return round(taken, 9)


async def test_in_futars_outage_the_planner_answers_first(make_planner, aioclient_mock, monkeypatch) -> None:
    """FUTÁR down (hanging its whole timeout): once its outage stands, the
    boards and trip details a card polls are answered by the planner at
    once, FUTÁR not asked; before, each waited 8 s for FUTÁR first (18 s
    once the planner's share was used up). The paths the planner does not
    serve still ask FUTÁR."""
    boards, _fake, clock = make_planner()
    fake = AnyTripPlanner()
    _use(aioclient_mock, fake)
    calls = _counting_futar(monkeypatch, clock, ["hang"])
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, BOARD_PARAMS)  # FUTÁR's whole budget: the outage starts
    assert len(calls) == 1
    started = clock.now
    for i in range(5):
        clock.now += 5
        tokens = _FutarTokens(proxy, clock)
        doc = await proxy.get(BOARD, dict(BOARD_PARAMS, stopId=f"BKK_F1{i:04d}"))
        assert doc["source"] == "mavplusz"
        # Each the one token of FUTÁR's limit a request takes, not asked of FUTÁR.
        assert tokens.taken() == 1
        assert (await proxy.get(TRIP, _trip_params(i)))["source"] == "mavplusz"
        assert tokens.taken() == 1
    # No FUTÁR wait: the 5 s steps, and the trip details' batch window.
    assert len(calls) == 1 and clock.now - started == pytest.approx(25 + 5 * mb.TRIP_BATCH_WAIT)
    # Search is FUTÁR's alone: asked as before.
    with pytest.raises(futar.FutarError):
        await proxy.get("search.json", {"query": "Keleti"})
    assert calls[-1] == ("search.json", futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC)


async def test_futars_outage_is_probed_once_every_thirty_seconds(make_planner, aioclient_mock, monkeypatch) -> None:
    """One request at a time asks FUTÁR in its outage, FUTAR_OUTAGE_PROBE_SEC
    after it was last asked, first as before (cut short, the planner after
    it); the requests meanwhile, the ones beside the probe too, are the
    planner's. FUTÁR answering the probe ends the outage: FUTÁR first again."""
    boards, _fake, clock = make_planner()
    fake = AnyStopPlanner()
    _use(aioclient_mock, fake)
    gate = asyncio.Event()
    calls: list = []
    answering = [False]

    async def futar_get(session, api_key, path, params, timeout=None, *, executor=None):
        calls.append(timeout.total)
        await gate.wait()
        if answering[0]:
            return {"status": "OK", "data": {"entry": {"stopTimes": []}, "references": {}}, "from": "futar"}
        raise futar.FutarError("HTTP 503")

    monkeypatch.setattr(futar, "futar_get", futar_get)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    gate.set()
    assert (await proxy.get(BOARD, BOARD_PARAMS))["source"] == "mavplusz"  # the outage starts
    stops = iter(f"BKK_F2{i:04d}" for i in range(100))
    clock.now += futar.FUTAR_OUTAGE_PROBE_SEC - 1
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, stopId=next(stops))))["source"] == "mavplusz"
    assert len(calls) == 1
    # The probe is due: one request asks FUTÁR (cut short), the others beside it do not.
    clock.now += 1
    gate.clear()
    tokens = _FutarTokens(proxy, clock)
    probe = asyncio.ensure_future(proxy.get(BOARD, dict(BOARD_PARAMS, stopId=next(stops))))
    for _ in range(5):
        await asyncio.sleep(0)
    beside = await asyncio.gather(*(proxy.get(BOARD, dict(BOARD_PARAMS, stopId=next(stops))) for _ in range(3)))
    assert [b["source"] for b in beside] == ["mavplusz"] * 3
    assert calls == [futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC, futar.FUTAR_FALLBACK_TIMEOUT_SEC]
    gate.set()
    assert (await probe)["source"] == "mavplusz"
    # One token each: the probe's for FUTÁR (the planner after it takes
    # none), the three beside it for the planner asked first.
    assert tokens.taken() == 4
    # The next probe FUTAR_OUTAGE_PROBE_SEC after the failed one; FUTÁR answers it.
    clock.now += futar.FUTAR_OUTAGE_PROBE_SEC - 1
    await proxy.get(BOARD, dict(BOARD_PARAMS, stopId=next(stops)))
    assert len(calls) == 2
    clock.now += 1
    answering[0] = True
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, stopId=next(stops))))["from"] == "futar"
    # The outage is over: every request asks FUTÁR first, with its whole budget.
    asked = len(fake.asked)
    tokens = _FutarTokens(proxy, clock)
    for _ in range(3):
        assert (await proxy.get(BOARD, dict(BOARD_PARAMS, stopId=next(stops))))["from"] == "futar"
    assert tokens.taken() == 3
    assert calls[-3:] == [futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC] * 3 and len(fake.asked) == asked


async def test_in_futars_outage_what_the_planner_cannot_answer_asks_futar(make_planner, aioclient_mock, monkeypatch) -> None:
    """A stop the planner does not know (its null) has nothing but FUTÁR:
    asked as before, with the card's whole budget; the planner is not
    asked again after FUTÁR failed. One the planner was asked first for
    and had no answer asks FUTÁR with what is left of the budget, no
    fallback after it, and with the token it took for the planner: it took
    two, so in FUTÁR's outage every such request spent FUTÁR's limit twice
    as fast."""
    boards, fake, clock = make_planner()
    calls = _counting_futar(monkeypatch, clock, ["fail"])
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    tokens = _FutarTokens(proxy, clock)
    unknown = dict(BOARD_PARAMS, stopId="BKK_005510017")
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, unknown)  # the outage starts; the planner's null
    assert len(fake.asked) == 1 and not boards.can_answer(BOARD, unknown)
    assert tokens.taken() == 1  # FUTÁR's; the planner after it takes none
    with pytest.raises(futar.FutarError, match="HTTP 503"):
        await proxy.get(BOARD, dict(unknown, minutesAfter="61"))
    whole = futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    assert calls == [(BOARD, whole), (BOARD, whole)] and len(fake.asked) == 1
    assert tokens.taken() == 1
    # Asked first, the planner has no answer (a trip it does not know): FUTÁR then, once.
    with pytest.raises(futar.FutarError, match="HTTP 503"):
        await proxy.get(TRIP, {"tripId": "BKK_D99999999"})
    assert calls[-1] == (TRIP, pytest.approx(whole - mb.TRIP_BATCH_WAIT)) and len(fake.asked) == 2
    assert tokens.taken() == 1
    assert proxy.tokens == pytest.approx(futar.FUTAR_BURST - 3 + futar.FUTAR_RATE * mb.TRIP_BATCH_WAIT)


async def test_in_futars_outage_a_planner_out_of_its_share_asks_futar_with_one_token(
    make_planner, aioclient_mock, monkeypatch
) -> None:
    """The planner can answer, but its share is gone by the time its token
    is reserved (another request took it while this one took FUTÁR's):
    FUTÁR is asked as before, with the token taken for the planner, not a
    second one; the planner has none for after it either, and FUTÁR's
    error stands."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(fallback_burst=2, monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    calls = _counting_futar(monkeypatch, clock, ["fail"])
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards, burst=10)
    assert (await proxy.get(BOARD, BOARD_PARAMS))["source"] == "mavplusz"  # the outage starts
    assert proxy.tokens == 9 and len(fake.asked) == 1
    real_reserve = boards.reserve

    def reserve(path, params):
        while limiter.take(fallback=True, quiet=True):
            pass  # the share goes to the other requests of the dashboard
        return real_reserve(path, params)

    monkeypatch.setattr(boards, "reserve", reserve)
    other = dict(BOARD_PARAMS, stopId="BKK_F00941")
    assert boards.can_answer(BOARD, other)
    with pytest.raises(futar.FutarError, match="HTTP 503"):
        await proxy.get(BOARD, other)
    whole = futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    assert calls == [(BOARD, whole), (BOARD, whole)] and len(fake.asked) == 1
    # One token for the one request (it took two: FUTÁR's limit ran out
    # twice as fast, and its requests waited, then were refused, sooner).
    assert proxy.tokens == 8


async def test_the_planner_asked_first_takes_futars_token_too(make_planner, aioclient_mock, monkeypatch) -> None:
    """Not asked of FUTÁR, the request still takes a token of FUTÁR's own
    limit (as a rejected key's does): the planner is no way around it."""
    clock = FakeClock(stopped=True)
    boards, fake, clock = make_planner(clock)
    _counting_futar(monkeypatch, clock, ["fail"])
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards, burst=2)
    assert (await proxy.get(BOARD, BOARD_PARAMS))["source"] == "mavplusz"  # FUTÁR's token, the outage starts
    assert proxy.tokens == 1
    assert (await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="61")))["source"] == "mavplusz"
    assert proxy.tokens == 0
    with pytest.raises(futar.FutarRateLimited):
        await proxy.get(BOARD, dict(BOARD_PARAMS, minutesAfter="62"))
    assert len(fake.asked) == 1 and proxy.tokens == 0


async def test_a_board_is_cached_across_a_cards_polls(make_planner, aioclient_mock, monkeypatch) -> None:
    """BOARD_TTL was 15 s against the cards' 20 s at the earliest: every
    poll asked the planner again. The cards' polls (src/config.js) are
    read, not assumed."""
    config = (Path(__file__).parents[1] / "src" / "config.js").read_text(encoding="utf-8")
    least = int(re.search(r"MIN_REFRESH_S = (\d+);", config).group(1))
    assert least < mb.BOARD_TTL <= 2 * least
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    await proxy.get(BOARD, BOARD_PARAMS)
    clock.now += least
    assert (await proxy.get(BOARD, BOARD_PARAMS))["source"] == "mavplusz"
    assert len(fake.asked) == 1


async def test_one_null_keeps_a_stop_out_for_minutes_not_hours(make_planner, aioclient_mock, monkeypatch) -> None:
    """A planner reloading its graph answers every stop null for a while:
    one null kept the stop out of the fallback for PROBE_TTL (12 h). It is
    kept for UNKNOWN_TTL now; FUTÁR's outage then has its fallback again.
    A station's poles alike."""
    boards, fake, clock = make_planner()
    nulls = [True]

    async def flaky(method, url, data):
        body = json.loads(data)
        if nulls[0]:
            fake.asked.append(body["variables"])
            if "stop(id: $id)" in body["query"]:
                return AiohttpClientMockResponse(method, url, json={"data": {"stop": None}})
            return AiohttpClientMockResponse(
                method, url, json={"data": {k: None for k in body["variables"] if k.startswith("s") and k[1:].isdigit()}})
        return await fake(method, url, data)

    _use(aioclient_mock, flaky)
    # The Keleti preset: its seeded poles, then (they null) the station itself.
    station = dict(BOARD_PARAMS, stopId="BKK_CSF01131")
    assert await boards.answer(BOARD, BOARD_PARAMS) is None
    assert await boards.answer(BOARD, station) is None
    clock.now += mb.BOARD_TTL
    assert await boards.answer(BOARD, station) is None
    assert len(fake.asked) == 3
    nulls[0] = False
    clock.now += mb.UNKNOWN_TTL - 1 - mb.BOARD_TTL
    assert not boards.can_answer(BOARD, BOARD_PARAMS) and not boards.can_answer(BOARD, station)
    clock.now += 1
    # The stop's null and the poles' are past UNKNOWN_TTL: asked again.
    assert boards.can_answer(BOARD, BOARD_PARAMS)
    assert boards._board_ids("BKK_CSF01131") == boards._poles("BKK_CSF01131")
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    assert (await proxy.get(BOARD, BOARD_PARAMS))["source"] == "mavplusz"
    assert len(fake.asked) == 4


async def test_one_bad_alias_fails_its_trip_alone(make_planner, aioclient_mock) -> None:
    """A trip of the wrong shape in a batch (`"t1": []`) failed the batch's
    every trip (up to 40) for REQUEST_FAIL_TTL; it fails alone now, as a
    trip a GraphQL error names does, and nobody backs off."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)

    async def answer(method, url, data):
        body = json.loads(data)
        fake.asked.append(body["variables"])
        aliases = [k for k in body["variables"] if k[0] == "t"]
        out = {a: (copy.deepcopy(TRIPS_DOC["data"]["t0"]) if body["variables"][a] == "BKK:D25280605" else [])
               for a in aliases}
        return AiohttpClientMockResponse(method, url, json={"data": out})

    _use(aioclient_mock, answer)
    good, bad = await asyncio.gather(boards.answer(TRIP, {"tripId": FIRST_TRIP}),
                                     boards.answer(TRIP, {"tripId": "BKK_X9"}))
    assert len(fake.asked) == 1 and good["data"]["entry"]["tripId"] == FIRST_TRIP and bad is None
    assert not limiter.backing_off()
    assert boards.can_answer(TRIP, {"tripId": FIRST_TRIP}) and not boards.can_answer(TRIP, {"tripId": "BKK_X9"})
    clock.now += mb.REQUEST_FAIL_TTL
    assert boards.can_answer(TRIP, {"tripId": "BKK_X9"})


@pytest.mark.parametrize("length", [2 ** 64, -1, 22], ids=["past-64-bits", "negative", "past-the-points"])
def test_a_shapes_length_is_bounded(length) -> None:
    """tripGeometry.length is the number of points, each at least two
    characters: 2 ** 64 broke Home Assistant's JSON of the whole response."""
    from homeassistant.helpers.json import json_bytes

    trip = copy.deepcopy(TRIPS_DOC["data"]["t0"])
    points = "}gz`HaoosBPADdBJfH?P@"
    trip["tripGeometry"] = {"points": points, "length": length}
    doc = mb.parse_trip(trip, FIRST_TRIP, "20261118", WALL)
    assert doc["data"]["entry"]["polyline"] == {"points": points}
    json_bytes(doc)
    trip["tripGeometry"]["length"] = len(points)
    assert mb.parse_trip(trip, FIRST_TRIP, "20261118", WALL)["data"]["entry"]["polyline"]["length"] == len(points)


def test_a_token_held_past_a_second_does_not_overshoot_the_burst() -> None:
    """The share's 15 tokens reserved, held through FUTÁR's 8 s and the
    planner's 6 s, then all given back: with the bucket refilled meanwhile,
    18 fallback requests could go at one moment (burst 15), 37 of the
    total's 30. A token held past REFUND_WITHIN_SEC counts as spent; one
    given back sooner still goes back."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    fb = limiter.client(lambda: RuntimeError("refused"), fallback=True)
    tickets = []
    for _ in range(mavplusz.FALLBACK_BURST):
        assert fb.reserve()
        tickets.append(mb._Ticket(mb._Token(fb, clock.monotonic)))
    clock.now += futar.FUTAR_FALLBACK_TIMEOUT_SEC + mb.PLANNER_TIMEOUT.total
    sent = 0
    while fb.available():
        fb.take()
        sent += 1
    for ticket in tickets:  # FUTÁR answered after all: every ticket released unspent
        ticket.release()
    while fb.available():
        fb.take()
        sent += 1
    assert sent <= mavplusz.FALLBACK_BURST
    assert limiter._total._tokens <= mavplusz.PLANNER_BURST
    # Given back within the second: the token is there again.
    clock.now += 3600
    assert fb.reserve()
    ticket = mb._Ticket(mb._Token(fb, clock.monotonic))
    clock.now += mb.REFUND_WITHIN_SEC
    ticket.release()
    assert limiter._fallback._tokens == pytest.approx(mavplusz.FALLBACK_BURST)


@pytest.mark.parametrize("failure", ["http-502", "timeout"])
async def test_a_trip_batch_the_planner_fails_answers_no_trip(make_planner, aioclient_mock, monkeypatch, failure) -> None:
    """The whole trip-details request failing (HTTP 5xx, a timeout): every
    trip waiting for it has PlannerError, answer() None, FUTÁR's error
    stands, and the planner backs off (no trip is marked failing: it is
    asked again after the backoff)."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    aioclient_mock.clear_requests()
    if failure == "timeout":
        aioclient_mock.post(mavplusz.URL, exc=asyncio.TimeoutError())
    else:
        aioclient_mock.post(mavplusz.URL, status=502)
    trips = [{"tripId": FIRST_TRIP}, {"tripId": "BKK_D25280606"}]
    results = await asyncio.gather(*(boards._trip(t, None) for t in trips), return_exceptions=True)
    assert all(type(r) is mb.PlannerError for r in results)
    assert limiter.backing_off() and not any(boards._is_failing(("trip", mb.planner_id(t["tripId"]))) for t in trips)
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    with pytest.raises(futar.FutarError, match="HTTP 503"):
        await proxy.get(TRIP, trips[0])
    clock.now += mb.FAIL_BACKOFF
    _use(aioclient_mock, fake)
    assert (await boards.answer(TRIP, trips[0]))["data"]["entry"]["tripId"] == FIRST_TRIP


async def test_a_trip_batch_failing_for_its_own_sake_is_not_asked_again(make_planner, aioclient_mock) -> None:
    """The whole trip-details answer unusable (over its cap): every trip of
    it is failing (_RequestFailed), not asked again within REQUEST_FAIL_TTL,
    and nobody backs off."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, _fake, clock = make_planner(clock, limiter=limiter)
    asked: list = []

    async def oversize(method, url, data):
        asked.append(1)
        return AiohttpClientMockResponse(method, url, text=" " * (mavplusz.MAX_RESPONSE_BYTES + 10))

    _use(aioclient_mock, oversize)
    trips = [{"tripId": FIRST_TRIP}, {"tripId": "BKK_D25280606"}]
    results = await asyncio.gather(*(boards._trip(t, None) for t in trips), return_exceptions=True)
    assert all(isinstance(r, mb._RequestFailed) for r in results) and len(asked) == 1
    assert not limiter.backing_off()
    clock.now += mb.REQUEST_FAIL_TTL - 1
    assert [await boards.answer(TRIP, t) for t in trips] == [None, None] and len(asked) == 1
    assert not any(boards.can_answer(TRIP, t) for t in trips)
    clock.now += 1
    assert all(boards.can_answer(TRIP, t) for t in trips)


@pytest.mark.parametrize("exc", [aiohttp.ClientConnectionError("refused"), asyncio.TimeoutError()],
                         ids=["connection", "timeout"])
async def test_a_board_the_planner_does_not_answer_backs_everyone_off(make_planner, aioclient_mock, caplog, exc) -> None:
    """No connection or a timeout is the planner failing: the shared backoff
    (the realtime hub's requests too), one warning for the outage, and one
    line when it answers again."""
    clock = FakeClock()
    limiter = mavplusz.PlannerLimiter(monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, exc=exc)
    assert await boards.answer(BOARD, BOARD_PARAMS) is None
    assert limiter.backing_off() and not limiter.client(RuntimeError).available()
    clock.now += mb.FAIL_BACKOFF
    assert await boards.answer(BOARD, dict(BOARD_PARAMS, stopId="BKK_F00941")) is None
    warnings = [r for r in caplog.records if r.name.endswith("mavplusz_board") and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "unavailable" in warnings[0].getMessage()
    assert not boards._is_failing(("board", STOP))
    clock.now += max(mb.FAIL_BACKOFF, mb.BOARD_TTL)
    _use(aioclient_mock, fake)
    assert (await boards.answer(BOARD, BOARD_PARAMS))["source"] == "mavplusz"
    assert any("answers for BKK boards again" in r.getMessage() for r in caplog.records)


async def test_a_futar_request_cancelled_with_a_reserved_token_gives_it_back(make_planner, aioclient_mock, monkeypatch) -> None:
    """FUTÁR's request itself cancelled (Home Assistant stopping cancels its
    task) while it holds the fallback's reserved token: the token goes back,
    and the share has it again."""
    clock = FakeClock(stopped=True)
    limiter = mavplusz.PlannerLimiter(fallback_burst=1, monotonic=clock.monotonic)
    boards, fake, clock = make_planner(clock, limiter=limiter)
    gate = asyncio.Event()

    async def hanging(session, api_key, path, params, timeout=None, *, executor=None):
        await gate.wait()
        return {"status": "OK"}

    monkeypatch.setattr(futar, "futar_get", hanging)
    tasks: list = []

    def task_factory(coro):
        tasks.append(asyncio.ensure_future(coro))
        return tasks[-1]

    proxy = futar.FutarProxy(None, "k", task_factory, monotonic=clock.monotonic, sleep=clock.sleep, fallback=boards)
    _futar_down(proxy)
    call = asyncio.ensure_future(proxy.get(BOARD, BOARD_PARAMS))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not limiter.available(fallback=True)
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert limiter.available(fallback=True) and fake.asked == []


async def test_a_rejected_key_spends_no_token_where_the_planner_cannot_answer(make_planner, aioclient_mock, monkeypatch) -> None:
    """A call failing fast on a rejected key takes FUTÁR's token for the
    planner, but not for a stop the planner does not know: nothing is
    asked of anyone then."""
    boards, fake, clock = make_planner()
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarAuthError("HTTP 401"))
    unknown = dict(BOARD_PARAMS, stopId="BKK_005510017")
    with pytest.raises(futar.FutarAuthError):
        await proxy.get(BOARD, unknown)  # FUTÁR's token; the planner's null
    tokens = proxy._rate._tokens
    with pytest.raises(futar.FutarKeyRejected):
        await proxy.get(BOARD, dict(unknown, minutesAfter="61"))
    assert proxy._rate._tokens == tokens and len(fake.asked) == 1
    # One the planner can answer: FUTÁR's token, as a call to FUTÁR would take.
    assert (await proxy.get(BOARD, BOARD_PARAMS))["source"] == "mavplusz"
    assert proxy._rate._tokens == tokens - 1


async def test_fetch_with_a_decode_hook_keeps_its_errors(aioclient_mock) -> None:
    """mavplusz.fetch's decode hook (the boards' executor decoding) changes
    nothing else: an HTTP error is ValueError("HTTP NNN"), an oversized
    answer TooLarge, one nested too deep ValueError("answer nested too
    deep"), one that is no JSON the decoder's own error; the hook gets the
    body as a bytearray and its result is the answer."""
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    got: list = []

    async def decode(data):
        got.append(type(data))
        return mb._decode_parse(lambda doc: ("parsed", doc), data)

    try:
        for status, body, error, match in [
            (502, "", ValueError, "^HTTP 502$"),
            (200, " " * 64, mavplusz.TooLarge, None),
            (200, "[" * 600 + "]" * 600, ValueError, "^answer nested too deep$"),
            (200, "<html>", json.JSONDecodeError, None),
        ]:
            aioclient_mock.clear_requests()
            aioclient_mock.post(mavplusz.URL, status=status, text=body)
            with pytest.raises(error, match=match):
                await mavplusz.fetch(session, {}, cap=32 if len(body) == 64 else mavplusz.MAX_RESPONSE_BYTES,
                                     decode=decode)
        aioclient_mock.clear_requests()
        aioclient_mock.post(mavplusz.URL, text='{"data": 1}')
        assert await mavplusz.fetch(session, {}, decode=decode) == ("parsed", {"data": 1})
        assert set(got) == {bytearray}
    finally:
        await session.close()
