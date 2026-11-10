"""Setup, static assets and the services' wiring (ELVIRA end to end; its
boards, planning and rows are tests/test_elvira.py)."""

import asyncio
from datetime import timedelta
import json
import logging
from pathlib import Path
import re

import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

import custom_components.hungarian_transport as integration
from custom_components.hungarian_transport import ISSUE_MISSING_ASSETS, elvira, mav, responses
from custom_components.hungarian_transport.assets import asset_tag
from custom_components.hungarian_transport.const import (
    DOMAIN,
    SERVICE_ELVIRA_BETWEEN,
    SERVICE_REALTIME,
    SERVICE_REBUILD_MAV,
)
from custom_components.hungarian_transport.elvira import ELVIRA_URL, service_days

from .test_cache import expiry_timers

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



def _lovelace_resources(hass: HomeAssistant):
    data = hass.data["lovelace"]
    # A dataclass from 2024.11, a dict before (the oldest supported release).
    return data["resources"] if isinstance(data, dict) else data.resources


def _our_resources(hass: HomeAssistant) -> list[dict]:
    return [
        item for item in _lovelace_resources(hass).async_items()
        if item["url"].startswith("/hungarian-transport/")
    ]


async def test_cards_are_a_lovelace_resource_too(hass, hass_client) -> None:
    """A page served while Home Assistant starts has no extra modules: the
    same URL as a storage-mode Lovelace resource still loads the cards."""
    assert await async_setup_component(hass, "lovelace", {})
    resources = _lovelace_resources(hass)
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    # Someone else's resource, and two stale ones of ours (an old version).
    await resources.async_create_item({"res_type": "module", "url": "/local/other-card.js"})
    await resources.async_create_item({"res_type": "js", "url": "/hungarian-transport/hungarian-transport.js?v=1.0"})
    await resources.async_create_item({"res_type": "module", "url": "/hungarian-transport/hungarian-transport-loader.js?v=0.9"})
    entry = await _setup(hass)
    card_url = hass.data[DOMAIN]["card_url"]
    ours = _our_resources(hass)
    assert [(item["type"], item["url"]) for item in ours] == [("module", card_url)]
    assert any(item["url"] == "/local/other-card.js" for item in resources.async_items())
    # The next start keeps the one resource as it is.
    item_id = ours[0]["id"]
    await integration.lovelace_resource.async_sync_resource(hass, card_url)
    assert [item["id"] for item in _our_resources(hass)] == [item_id]
    # Removing the integration removes its resource, not the others.
    await integration.async_remove_entry(hass, entry)
    assert _our_resources(hass) == []
    assert any(item["url"] == "/local/other-card.js" for item in resources.async_items())


async def test_yaml_mode_resources_are_left_alone(hass) -> None:
    from homeassistant.components.lovelace import const as lovelace_const

    key = "resource_mode" if hasattr(lovelace_const, "CONF_RESOURCE_MODE") else "mode"
    assert await async_setup_component(hass, "lovelace", {"lovelace": {key: "yaml"}})
    await _setup(hass)
    assert integration.lovelace_resource._storage_resources(hass) is None
    assert [item for item in _lovelace_resources(hass).async_items() if "hungarian" in item["url"]] == []


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
    dep["endStation"] = {"name": "Szombathely"}
    aioclient_mock.post(
        ELVIRA_URL,
        json={"stationSchedulerDetails": {"departureScheduler": [dep], "arrivalScheduler": [arr]}},
    )

    resp = await hass.services.async_call(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        {"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": 60, "dest_name": "Kelenföld"},
        blocking=True,
        return_response=True,
    )

    assert "dest_error" not in resp
    rows = resp["departures"]
    assert len(rows) == 1
    # The card's destination heads the row; the train's own terminus comes along.
    assert rows[0]["head"] == "Kelenföld" and rows[0]["end"] == "Szombathely"
    assert rows[0]["booking"] is True
    assert rows[0]["platform"] == "6"
    assert rows[0]["travel"] == 62
    assert rows[0]["dep"] == int(start.timestamp())
    # Service days (travelDate) are Budapest dates too.
    asked = {call[2]["travelDate"][:10] for call in aioclient_mock.mock_calls}
    expected = {d.isoformat() for d in service_days(now, now + timedelta(minutes=60))}
    assert asked == expected


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"origin_code": "12345", "dest_code": DEST}, "invalid_station_code"),
        # ten digits are not a code with a prefix: not silently cut to nine
        ({"origin_code": "1" + DEST, "dest_code": DEST}, "invalid_station_code"),
        ({"origin_code": ORIGIN, "dest_code": ORIGIN}, "same_station"),
        # dest_name heads every row: an unbounded one is the response times
        # the row count.
        ({"origin_code": ORIGIN, "dest_code": DEST, "dest_name": "x" * 101}, vol.Invalid),
        # A planner's start is within a day before and eight days after now.
        ({"origin_code": ORIGIN, "dest_code": DEST, "at": timedelta(days=9)}, vol.Invalid),
        ({"origin_code": ORIGIN, "dest_code": DEST, "at": timedelta(days=-2)}, vol.Invalid),
        # A YAML .inf is out of range too, not an OverflowError out of the schema.
        ({"origin_code": ORIGIN, "dest_code": DEST, "at": float("inf")}, vol.Invalid),
        ({"origin_code": ORIGIN, "dest_code": DEST, "at": float("-inf")}, vol.Invalid),
    ],
)
async def test_elvira_service_rejects_bad_input(hass, aioclient_mock, data, error) -> None:
    await _setup(hass)
    if isinstance(data.get("at"), timedelta):
        data = dict(data, at=int((dt_util.utcnow() + data["at"]).timestamp()))

    with pytest.raises(ServiceValidationError if isinstance(error, str) else error) as err:
        await hass.services.async_call(
            DOMAIN, SERVICE_ELVIRA_BETWEEN, data, blocking=True, return_response=True
        )

    if isinstance(error, str):
        assert err.value.translation_key == error
    assert aioclient_mock.call_count == 0


async def _elvira_call(hass, origin=ORIGIN, dest=DEST):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        {"origin_code": origin, "dest_code": dest, "minutes_after": 15},
        blocking=True,
        return_response=True,
    )


def _unavailable(detail: str) -> dict:
    return {"departures": [], "error": "elvira_unavailable", "detail": detail}


def _elvira_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name.endswith(".elvira") and r.levelno == logging.WARNING
    ]


@pytest.mark.parametrize(
    ("mock", "detail"),
    [
        ({"status": 503}, "HTTP 503"),
        ({"exc": asyncio.TimeoutError()}, "TimeoutError"),
        ({"json": {"message": "Hiba"}}, "unexpected response"),
    ],
)
async def test_elvira_outage_is_an_answer_not_an_error_line_per_poll(
    hass, aioclient_mock, hass_ws_client, caplog, mock, detail
) -> None:
    """Cards call the service over the websocket, which logs a failed call
    at ERROR: an outage answers successfully, with the error inside, and
    each station that went down is one warning."""
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, **mock)
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
        assert msg["result"]["response"] == _unavailable(detail)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(_elvira_warnings(caplog)) == 2


async def test_elvira_rate_limit_and_destination_outage_are_answer_codes(
    hass, aioclient_mock, freezer
) -> None:
    """The integration's own limit is elvira_rate_limited, not MÁV being
    down; a destination board that did not arrive is a dest_error beside
    the rows."""
    freezer.move_to(NOON)
    await _setup(hass)
    down = "005599999"

    async def side_effect(method, url, data):
        if data["stationNumberCode"] == down:
            return AiohttpClientMockResponse(method, url, status=503)
        return AiohttpClientMockResponse(method, url, json={"stationSchedulerDetails": {}})

    aioclient_mock.post(ELVIRA_URL, side_effect=side_effect)
    assert await _elvira_call(hass, dest=down) == {"departures": [], "dest_error": "elvira_unavailable"}

    # Every request is a token, the failed one too; at noon one board each
    # (the destination's is cached) until the burst is gone: the frozen
    # clock refills nothing.
    n = 0
    while aioclient_mock.call_count < elvira.ELVIRA_BURST:
        assert "error" not in await _elvira_call(hass, origin=f"0055{n:05d}")
        n += 1
        assert n <= elvira.ELVIRA_BURST
    assert await _elvira_call(hass, origin="005500999") == {
        "departures": [],
        "error": "elvira_rate_limited",
        "detail": "rate limited",
    }
    # Today's origin board is cached; the new destination's is refused.
    assert await _elvira_call(hass, origin="005500000", dest="005599998") == {
        "departures": [],
        "dest_error": "elvira_rate_limited",
    }

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


@pytest.mark.parametrize("starting", [False, True], ids=["reload", "startup"])
async def test_services_answer_without_an_error_while_the_entry_comes_up(
    hass, aioclient_mock, caplog, monkeypatch, starting
) -> None:
    """A reload (an options save) or Home Assistant's startup is no outage:
    the cards' polls get an error code in the answer, not an exception the
    websocket API logs at ERROR."""
    entry = await _setup(hass)
    answers: list[tuple] = []
    gate = asyncio.Event()
    real_setup_entry = integration.async_setup_entry

    async def slow_setup_entry(hass, entry):
        # The window: async_setup_entry still reading the indexes.
        answers.append((await _elvira_call(hass), await _realtime_call(hass)))
        gate.set()
        return await real_setup_entry(hass, entry)

    monkeypatch.setattr(integration, "async_setup_entry", slow_setup_entry)
    if starting:
        assert await hass.config_entries.async_unload(entry.entry_id)
        _set_state(hass, CoreState.not_running)
        # Before the entry's setup has begun, as well as during it.
        answers.append((await _elvira_call(hass), await _realtime_call(hass)))
        assert await hass.config_entries.async_setup(entry.entry_id)
        _set_state(hass, CoreState.running)
    else:
        assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert gate.is_set()
    assert len(answers) == (2 if starting else 1)
    for elvira_answer, rt_answer in answers:
        # As JSON, the cards' view: the same keys in the same order.
        assert json.dumps(elvira_answer) == json.dumps(
            {"departures": [], "error": "elvira_unavailable", "detail": integration.NOT_READY}
        )
        assert json.dumps(rt_answer) == '{"feed": "volan", "trips": {}, "error": "rt_unavailable"}'
    assert aioclient_mock.call_count == 0
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_services_refuse_while_home_assistant_starts_with_the_entry_disabled(hass) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_set_disabled_by(
        entry.entry_id, ConfigEntryDisabler.USER
    )
    _set_state(hass, CoreState.not_running)
    try:
        with pytest.raises(ServiceValidationError) as err:
            await _elvira_call(hass)
    finally:
        _set_state(hass, CoreState.running)
    assert err.value.translation_key == "not_loaded"


async def test_elvira_expiry_timer_goes_when_home_assistant_stops(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(NOON)
    await _setup(hass)
    aioclient_mock.post(ELVIRA_URL, json={"stationSchedulerDetails": {}})
    await _elvira_call(hass)
    assert len(expiry_timers(hass)) == 1

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert expiry_timers(hass) == []


async def _realtime_call(hass):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_REALTIME,
        {"feed": "volan", "trips": [{"key": "a"}]},
        blocking=True,
        return_response=True,
    )


def _set_state(hass: HomeAssistant, state: CoreState) -> None:
    """Home Assistant's core state (set_state where the harness has it)."""
    if hasattr(hass, "set_state"):
        hass.set_state(state)
    else:
        hass.state = state


_PKG = Path(integration.__file__).parent


@pytest.mark.parametrize("lang", ["en", "hu"])
def test_exception_translations_are_raised_and_error_codes_documented(lang) -> None:
    """exceptions.* is only read for a raised ServiceValidationError or
    HomeAssistantError; a service response's "error" code is not looked up
    there, so it is explained in the service description instead."""
    strings = json.loads((_PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
    # sensor.py's translation keys are entity names (tests/test_sensor.py).
    source = "\n".join(p.read_text(encoding="utf-8") for p in _PKG.glob("*.py") if p.name != "sensor.py")
    raised = set(re.findall(r'translation_key="(\w+)"', source))
    assert set(strings["exceptions"]) == raised
    # A card's response codes come from one table (responses.py), each
    # explained in its own service's description; one written out by hand
    # would slip past it.
    assert not re.search(r'"(?:error|dest_error)": "', source)
    for service, codes in responses.CODES.items():
        for code in codes:
            assert code in strings["services"][service]["description"], (service, code)
