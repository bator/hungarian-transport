"""Setup, static assets and the ELVIRA service."""

import asyncio
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.hungarian_transport.const import DOMAIN, SERVICE_ELVIRA_BETWEEN
from custom_components.hungarian_transport.elvira import ELVIRA_URL, service_days

ORIGIN = "005510009"
BUDAPEST = dt_util.get_time_zone("Europe/Budapest")
DEST = "005501016"


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


async def test_setup_fails_without_assets(hass, card_assets) -> None:
    for asset in card_assets.iterdir():
        asset.unlink()
    card_assets.rmdir()
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


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


async def test_elvira_service_returns_rows(hass, aioclient_mock) -> None:
    """StationInfo times are naive Budapest time, whatever HA's own time zone is."""
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


async def test_elvira_service_shares_concurrent_requests(hass, aioclient_mock) -> None:
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
