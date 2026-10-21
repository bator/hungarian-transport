"""Setup, static assets and the ELVIRA service."""

import asyncio
from datetime import timedelta
import logging
import re

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.hungarian_transport import ISSUE_MISSING_ASSETS, elvira, mav
from custom_components.hungarian_transport.assets import asset_tag
from custom_components.hungarian_transport.const import (
    DOMAIN,
    SERVICE_ELVIRA_BETWEEN,
    SERVICE_REBUILD_MAV,
)
from custom_components.hungarian_transport.elvira import ELVIRA_URL, service_days

ORIGIN = "005510009"
BUDAPEST = dt_util.get_time_zone("Europe/Budapest")
DEST = "005501016"
# Midday: one service day, so every call asks for exactly two boards.
NOON = "2026-10-19 12:00:00+02:00"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _card_urls(hass: HomeAssistant) -> set[str]:
    return set(hass.data[DATA_EXTRA_MODULE_URL].urls)


async def test_setup_serves_card_and_registers_service(hass, hass_client) -> None:
    entry = await _setup(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_ELVIRA_BETWEEN)
    card_url = hass.data[DOMAIN]["card_url"]
    assert card_url.startswith("/hungarian-transport/hungarian-transport-loader.js?v=")
    assert card_url in _card_urls(hass)

    client = await hass_client()
    resp = await client.get("/hungarian-transport/hungarian-transport-loader.js")
    assert resp.status == 200
    assert await resp.text() == "// loader"
    resp = await client.get("/hungarian-transport/hungarian-transport.js")
    assert resp.status == 200
    assert await resp.text() == "// card"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert card_url not in _card_urls(hass)



async def test_card_url_changes_with_the_bundle(hass, card_assets) -> None:
    """?v carries the release version and a hash of the bundle and loader, so
    a www/ rebuilt from source (same manifest version) is a new URL."""
    await _setup(hass)
    card_url = hass.data[DOMAIN]["card_url"]
    tag = asset_tag(card_assets)
    assert re.fullmatch(r"[0-9a-f]{10}", tag)
    assert card_url.endswith(f"-{tag}")
    (card_assets / "hungarian-transport.js").write_text("// card, rebuilt", encoding="utf-8")
    assert asset_tag(card_assets) != tag


async def test_card_loads_directly_without_loader(hass, card_assets) -> None:
    """A www/ without the loader (an older build) still gets its cards."""
    (card_assets / "hungarian-transport-loader.js").unlink()
    await _setup(hass)

    card_url = hass.data[DOMAIN]["card_url"]
    assert card_url.startswith("/hungarian-transport/hungarian-transport.js?v=")
    assert card_url in _card_urls(hass)


async def test_index_is_revalidated_not_redownloaded(hass, hass_client, card_assets) -> None:
    """The card fetches indexes with cache: 'no-cache' and relies on 304."""
    (card_assets / "volan-index.json.gz").write_bytes(b"\x1f\x8bindex")
    (card_assets / "leaflet").mkdir()
    (card_assets / "leaflet" / "leaflet.js").write_text("// leaflet", encoding="utf-8")
    await _setup(hass)
    client = await hass_client()

    first = await client.get("/hungarian-transport/volan-index.json.gz")
    assert first.status == 200
    etag = first.headers.get("ETag")
    last_modified = first.headers.get("Last-Modified")
    assert etag or last_modified
    headers = {"If-None-Match": etag} if etag else {"If-Modified-Since": last_modified}
    again = await client.get("/hungarian-transport/volan-index.json.gz", headers=headers)
    assert again.status == 304

    leaflet = await client.get("/hungarian-transport/leaflet/leaflet.js")
    assert leaflet.status == 200


async def test_shapes_folder_is_served(hass, hass_client, card_assets) -> None:
    """The card loads one shard of <feed>-shapes-<hash>/ when a map opens."""
    folder = card_assets / "volan-shapes-0123456789"
    folder.mkdir()
    (folder / "07.json.gz").write_bytes(b"\x1f\x8bshard")
    await _setup(hass)
    client = await hass_client()

    resp = await client.get("/hungarian-transport/volan-shapes-0123456789/07.json.gz")
    assert resp.status == 200
    assert await resp.read() == b"\x1f\x8bshard"


async def test_setup_fails_without_assets(hass, card_assets, caplog) -> None:
    """A source checkout has no www/: setup fails with a Repairs issue, not a traceback."""
    for asset in card_assets.iterdir():
        asset.unlink()
    card_assets.rmdir()
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ASSETS)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders == {"path": str(card_assets)}
    assert "missing its frontend assets" in caplog.text
    assert "Traceback" not in caplog.text


async def test_missing_assets_issue_goes_once_www_is_back(hass) -> None:
    """The Repairs issue outlives the boot that raised it; the first start
    with www/ in place (the release zip installed) removes it."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_MISSING_ASSETS,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_MISSING_ASSETS,
    )
    await _setup(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_MISSING_ASSETS) is None


def _board(start, arrive=None, code="966"):
    fmt = "%Y-%m-%dT%H:%M:%S"
    dep = {
        "trainId": "t1",
        "code": code,
        "name": "GÖCSEJ",
        "start": start.strftime(fmt),
        "startTrack": "6",
        "services": [{"code": "3880"}],
    }
    arr = {"trainId": "t1", "code": code, "arrive": arrive.strftime(fmt)} if arrive else None
    return dep, arr


async def test_elvira_service_returns_rows(hass, aioclient_mock, freezer) -> None:
    """StationInfo times are naive Budapest time, whatever HA's own time zone is."""
    # Frozen: near midnight the look-ahead would reach another service day.
    freezer.move_to(NOON)
    await _setup(hass)
    await hass.config.async_set_time_zone("America/Los_Angeles")
    now = dt_util.now(BUDAPEST).replace(microsecond=0)
    start = now + timedelta(minutes=30)
    dep, arr = _board(start, start + timedelta(minutes=62))
    aioclient_mock.post(
        ELVIRA_URL,
        json={"stationSchedulerDetails": {"departureScheduler": [dep], "arrivalScheduler": [arr]}},
    )

    resp = await hass.services.async_call(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        {"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": 60},
        blocking=True,
        return_response=True,
    )

    rows = resp["departures"]
    assert len(rows) == 1
    assert rows[0]["booking"] is True
    assert rows[0]["platform"] == "6"
    assert rows[0]["travel"] == 62
    assert rows[0]["dep"] == int(start.timestamp())
    # Service days (travelDate) are Budapest dates too.
    asked = {call[2]["travelDate"][:10] for call in aioclient_mock.mock_calls}
    expected = {d.isoformat() for d in service_days(now, now + timedelta(minutes=60))}
    assert asked == expected


async def test_elvira_service_shares_concurrent_requests(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(NOON)
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, json={"stationSchedulerDetails": {}})
    data = {"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": 15}
    now = dt_util.now(BUDAPEST)
    boards = 2 * len(service_days(now, now + timedelta(minutes=15)))

    def call():
        return hass.services.async_call(
            DOMAIN, SERVICE_ELVIRA_BETWEEN, data, blocking=True, return_response=True
        )

    await asyncio.gather(call(), call(), call())
    assert aioclient_mock.call_count == boards

    # Within the cache TTL the boards are not fetched again.
    await call()
    assert aioclient_mock.call_count == boards


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"origin_code": "12345", "dest_code": DEST}, "invalid_station_code"),
        ({"origin_code": ORIGIN, "dest_code": ORIGIN}, "same_station"),
    ],
)
async def test_elvira_service_rejects_bad_codes(hass, aioclient_mock, data, key) -> None:
    await _setup(hass)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, SERVICE_ELVIRA_BETWEEN, data, blocking=True, return_response=True
        )

    assert err.value.translation_key == key
    assert aioclient_mock.call_count == 0


async def _elvira_call(hass):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        {"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": 15},
        blocking=True,
        return_response=True,
    )


def _unavailable(detail: str) -> dict:
    return {"departures": [], "error": "elvira_unavailable", "detail": detail}


async def test_elvira_outage_is_an_error_and_is_not_cached(hass, aioclient_mock, freezer) -> None:
    """HTTP errors come back as an error in the response; the shared cache
    must not hold the failed board for ELVIRA_CACHE_TTL as 'no trains'."""
    freezer.move_to(NOON)
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, status=503)
    now = dt_util.now(BUDAPEST)
    boards = 2 * len(service_days(now, now + timedelta(minutes=15)))

    assert await _elvira_call(hass) == _unavailable("HTTP 503")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == boards

    aioclient_mock.clear_requests()
    aioclient_mock.post(ELVIRA_URL, json={"stationSchedulerDetails": {}})
    resp = await _elvira_call(hass)
    assert resp == {"departures": []}
    # Every board was fetched again right away: nothing empty was cached.
    assert aioclient_mock.call_count == boards


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ([1, 2], "unexpected response"),
        ("nope", "unexpected response"),
        (None, "JSONDecodeError"),
        # A 200 without stationSchedulerDetails is an error page or a
        # changed API, not a day without trains.
        ({}, "unexpected response"),
        ({"stationSchedulerDetails": None}, "unexpected response"),
        ({"message": "Hiba"}, "unexpected response"),
    ],
)
async def test_elvira_unexpected_body_is_an_error(hass, aioclient_mock, body, detail) -> None:
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, json=body)

    assert await _elvira_call(hass) == _unavailable(detail)


async def test_elvira_skips_malformed_rows(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(NOON)
    await _setup(hass)
    start = dt_util.now(BUDAPEST).replace(microsecond=0) + timedelta(minutes=10)
    dep, _arr = _board(start)
    odd = dict(dep, trainId="t2", code="968", viszonylatiJel="IC", endStation="x", services=["3880", None])
    details = {"departureScheduler": [1, "row", None, dep, odd], "arrivalScheduler": {"not": "a list"}}
    aioclient_mock.post(ELVIRA_URL, json={"stationSchedulerDetails": details})

    rows = (await _elvira_call(hass))["departures"]
    assert [(row["trainNumber"], row["booking"]) for row in rows] == [("966", True), ("968", False)]
    assert rows[1]["label"] == "GÖCSEJ" and rows[1]["head"] == ""


async def test_elvira_timeout_is_an_error(hass, aioclient_mock) -> None:
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, exc=asyncio.TimeoutError())

    assert await _elvira_call(hass) == _unavailable("TimeoutError")


async def test_elvira_outage_is_not_an_error_line_per_poll(
    hass, aioclient_mock, hass_ws_client, caplog
) -> None:
    """Cards call the service over the websocket, which logs a failed call
    at ERROR: an outage answers successfully, with the error inside."""
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, status=503)
    client = await hass_ws_client(hass)

    for msg_id in (1, 2, 3):
        await client.send_json(
            {
                "id": msg_id,
                "type": "call_service",
                "domain": DOMAIN,
                "service": SERVICE_ELVIRA_BETWEEN,
                "service_data": {"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": 15},
                "return_response": True,
            }
        )
        msg = await client.receive_json()
        assert msg["success"], msg
        assert msg["result"]["response"] == _unavailable("HTTP 503")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    # One warning per station that went down, not one per poll.
    assert len(_elvira_warnings(caplog)) == 2


def _boards(failing, departures):
    """aioclient_mock side effect: 503 for the (station, day) boards in
    ``failing`` (a bare station code fails all its days), else the origin's
    ``departures`` of that day."""

    async def side_effect(method, url, data):
        code, day = data["stationNumberCode"], data["travelDate"][:10]
        if code in failing or (code, day) in failing:
            return AiohttpClientMockResponse(method, url, status=503)
        deps = [dep for dep in departures if code == ORIGIN and dep["start"][:10] == day]
        details = {"departureScheduler": deps, "arrivalScheduler": []}
        return AiohttpClientMockResponse(method, url, json={"stationSchedulerDetails": details})

    return side_effect


def _elvira_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name.endswith(".elvira") and r.levelno == logging.WARNING
    ]


async def test_elvira_failed_destination_board_counts_as_empty(
    hass, aioclient_mock, freezer, caplog
) -> None:
    """The origin board alone carries platform and booking; a destination
    outage only costs the travel time, and is logged once, not per poll."""
    freezer.move_to(NOON)
    await _setup(hass)
    start = dt_util.now(BUDAPEST).replace(microsecond=0) + timedelta(minutes=10)
    dep, _arr = _board(start)
    aioclient_mock.post(ELVIRA_URL, side_effect=_boards({DEST}, [dep]))

    for _ in range(3):
        rows = (await _elvira_call(hass))["departures"]
        assert len(rows) == 1
        assert rows[0]["platform"] == "6" and rows[0]["booking"] is True
        assert rows[0]["travel"] is None and rows[0]["destHit"] is False
    warnings = _elvira_warnings(caplog)
    assert len(warnings) == 1 and DEST in warnings[0] and "HTTP 503" in warnings[0]

    aioclient_mock.clear_requests()
    aioclient_mock.post(ELVIRA_URL, side_effect=_boards(set(), [dep]))
    caplog.clear()
    await _elvira_call(hass)
    await _elvira_call(hass)
    assert [r.getMessage() for r in caplog.records if "answers again" in r.getMessage()] == [
        f"ELVIRA StationInfo {DEST} answers again"
    ]


async def test_elvira_failed_extra_day_board_counts_as_empty(hass, aioclient_mock, freezer) -> None:
    """Just after midnight yesterday's board is asked for its late trains;
    when only that one fails, today's departures still arrive."""
    freezer.move_to("2026-10-19 00:30:00+02:00")
    await _setup(hass)
    now = dt_util.now(BUDAPEST).replace(microsecond=0)
    yesterday = (now - timedelta(days=1)).date().isoformat()
    assert [d.isoformat() for d in service_days(now, now + timedelta(minutes=15))] == [
        yesterday,
        now.date().isoformat(),
    ]
    dep, _arr = _board(now + timedelta(minutes=10))
    aioclient_mock.post(ELVIRA_URL, side_effect=_boards({(ORIGIN, yesterday)}, [dep]))

    rows = (await _elvira_call(hass))["departures"]
    assert [row["platform"] for row in rows] == ["6"]


async def test_elvira_error_when_no_origin_board_arrives(hass, aioclient_mock, freezer) -> None:
    freezer.move_to("2026-10-19 00:30:00+02:00")
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, side_effect=_boards({ORIGIN}, []))

    assert await _elvira_call(hass) == _unavailable("HTTP 503")


async def test_elvira_error_when_todays_origin_board_fails(hass, aioclient_mock, freezer) -> None:
    """22:30 with three hours ahead asks today's and tomorrow's boards. When
    only tomorrow's arrives, its post-midnight trains are not the answer:
    every train of this evening would be missing without a word."""
    freezer.move_to("2026-10-19 22:30:00+02:00")
    await _setup(hass)
    now = dt_util.now(BUDAPEST).replace(microsecond=0)
    today = now.date().isoformat()
    assert [d.isoformat() for d in service_days(now, now + timedelta(minutes=180))] == [
        today,
        (now + timedelta(days=1)).date().isoformat(),
    ]
    tonight, _ = _board(now + timedelta(minutes=20))
    after_midnight, _ = _board(now + timedelta(minutes=110), code="900")
    aioclient_mock.post(
        ELVIRA_URL, side_effect=_boards({(ORIGIN, today)}, [tonight, after_midnight])
    )

    resp = await hass.services.async_call(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        {"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": 180},
        blocking=True,
        return_response=True,
    )
    assert resp == _unavailable("HTTP 503")


async def test_elvira_times_across_the_autumn_clock_change(hass, aioclient_mock, freezer) -> None:
    """2026-10-25 03:00 CEST is 02:00 CET. A train leaving 01:50 CEST and
    arriving 03:20 CET travels 150 minutes; wall clocks say 90."""
    freezer.move_to("2026-10-25 01:40:00+02:00")
    await _setup(hass)
    dep = {
        "trainId": "night",
        "code": "40400",
        "start": "2026-10-25T01:50:00",
        "actualOrEstimatedStart": "2026-10-25T01:58:00",
        "startTrack": "2",
        "services": [],
    }
    arr = {"trainId": "night", "code": "40400", "arrive": "2026-10-25T03:20:00"}

    async def side_effect(method, url, data):
        day = data["travelDate"][:10]
        on_day = day == "2026-10-25"
        details = {
            "departureScheduler": [dep] if on_day and data["stationNumberCode"] == ORIGIN else [],
            "arrivalScheduler": [arr] if on_day and data["stationNumberCode"] == DEST else [],
        }
        return AiohttpClientMockResponse(method, url, json={"stationSchedulerDetails": details})

    aioclient_mock.post(ELVIRA_URL, side_effect=side_effect)
    resp = await hass.services.async_call(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        {"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": 180},
        blocking=True,
        return_response=True,
    )

    (row,) = resp["departures"]
    assert row["travel"] == 142
    assert row["delay"] == 8 * 60
    assert row["dep"] == int(dt_util.parse_datetime("2026-10-24T23:58:00+00:00").timestamp())


async def test_services_refuse_while_the_entry_is_unloaded(
    hass, aioclient_mock, hass_client, mav_source
) -> None:
    """async_setup registers them for good; a disabled entry must not keep
    answering cards, building indexes or taking uploads."""
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await _elvira_call(hass)
    assert err.value.translation_key == "not_loaded"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True
        )
    assert err.value.translation_key == "not_loaded"
    client = await hass_client()
    resp = await client.post(mav.UPLOAD_URL, data=b"PK")
    assert resp.status == 409
    assert aioclient_mock.call_count == 0
    assert not mav_source.parent.exists()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.post(ELVIRA_URL, json={"stationSchedulerDetails": {}})
    assert await _elvira_call(hass) == {"departures": []}


def _station(n: int) -> str:
    return f"0055{n:05d}"


async def test_elvira_requests_are_rate_limited(
    hass, aioclient_mock, freezer, monkeypatch, caplog
) -> None:
    """Any logged-in user can call elvira_between and every new station pair
    is new boards: past the burst a board is refused (one log line, no
    request, not an outage), and the clock refills the bucket."""
    clock = [1000.0]
    monkeypatch.setattr(elvira, "_monotonic", lambda: clock[0])
    freezer.move_to(NOON)
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, json={"stationSchedulerDetails": {}})

    async def call(origin: str) -> dict:
        return await hass.services.async_call(
            DOMAIN,
            SERVICE_ELVIRA_BETWEEN,
            {"origin_code": origin, "dest_code": DEST, "minutes_after": 15},
            blocking=True,
            return_response=True,
        )

    # One service day at noon: the first call takes the destination board
    # too, every further origin one board.
    for n in range(elvira.ELVIRA_BURST - 1):
        assert await call(_station(n)) == {"departures": []}
    assert aioclient_mock.call_count == elvira.ELVIRA_BURST
    for n in range(100, 105):
        assert await call(_station(n)) == _unavailable("rate limited")
    assert aioclient_mock.call_count == elvira.ELVIRA_BURST
    limited = [m for m in _elvira_warnings(caplog) if "rate limit" in m]
    assert len(limited) == 1
    assert not [m for m in _elvira_warnings(caplog) if "unavailable" in m]

    clock[0] += 1 / elvira.ELVIRA_RATE
    assert await call(_station(200)) == {"departures": []}
    assert aioclient_mock.call_count == elvira.ELVIRA_BURST + 1


def test_elvira_rate_limit_leaves_room_for_cards() -> None:
    """Six MÁV cards on six different pairs near midnight (three service
    days, so six boards each) every ELVIRA_CACHE_TTL, and all of them at
    once when a dashboard opens."""
    boards = 6 * 2 * 3
    assert boards / elvira.ELVIRA_CACHE_TTL <= elvira.ELVIRA_RATE * 0.9
    assert boards <= elvira.ELVIRA_BURST
