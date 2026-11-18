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
from pathlib import Path

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
    # FUTÁR has its share of the card's budget on the paths the planner can
    # answer, the whole budget on the others.
    await proxy.get("search.json", {"query": "Keleti"})
    assert seen == [(BOARD, futar.FUTAR_FALLBACK_TIMEOUT_SEC), ("search.json", 18.0)]


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
    # It answers again after the backoff.
    fake.down = False
    clock.now += mb.FAIL_BACKOFF
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
    # Cached for BOARD_TTL (FUTÁR's own), then asked again.
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
    # A malformed answer is no board (and backs off), but the stop counts as asked.
    async def broken(method, url, data):
        fake.asked.append(json.loads(data)["variables"])
        return AiohttpClientMockResponse(method, url, json={"data": {"stop": []}})

    aioclient_mock.clear_requests()
    aioclient_mock.post(mavplusz.URL, side_effect=broken)
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, BOARD_PARAMS)
    clock.now += mb.FAIL_BACKOFF - 1
    assert mb.FAIL_BACKOFF > mb.BOARD_TTL
    with pytest.raises(futar.FutarError):
        await proxy.get(BOARD, BOARD_PARAMS)
    assert len(fake.asked) == 1


async def test_the_planner_has_a_rate_limit_of_its_own(make_planner, aioclient_mock, monkeypatch, caplog) -> None:
    boards, fake, clock = make_planner(FakeClock(stopped=True), burst=2)
    proxy = _proxy(monkeypatch, boards, clock, futar.FutarError("HTTP 503"))
    got = []
    for stop in ("BKK_F00940", "BKK_F1", "BKK_F2"):
        try:
            got.append(await proxy.get(BOARD, dict(BOARD_PARAMS, stopId=stop)))
        except futar.FutarError as err:
            got.append(str(err))
    assert len(fake.asked) == 2
    assert got[0]["source"] == "mavplusz" and got[2] == "HTTP 503"
    assert any("over the integration's rate limit" in r.getMessage() for r in caplog.records)


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
