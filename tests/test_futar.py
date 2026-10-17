"""The FUTÁR proxy service keeps the BKK key in Home Assistant."""

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component

from custom_components.hungarian_transport.const import CONF_API_KEY, DOMAIN, SERVICE_FUTAR
from custom_components.hungarian_transport.futar import BKK_API

SEARCH = f"{BKK_API}/search.json"


async def _setup(hass, key="secret-key") -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: key})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _call(hass, path="search.json", params=None):
    return hass.services.async_call(
        DOMAIN,
        SERVICE_FUTAR,
        {"path": path, "params": params or {"query": "Keleti"}},
        blocking=True,
        return_response=True,
    )


async def test_no_service_without_key(hass) -> None:
    await _setup(hass, key="")
    assert not hass.services.has_service(DOMAIN, SERVICE_FUTAR)


async def test_proxies_with_stored_key(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {"list": [1]}})
    await _setup(hass)

    resp = await _call(hass, params={"query": "Keleti", "key": "from-browser", "limit": 5})

    assert resp == {"data": {"status": "OK", "data": {"list": [1]}}}
    query = aioclient_mock.mock_calls[0][1].query
    assert query["key"] == "secret-key"
    assert query["query"] == "Keleti"
    assert query["limit"] == "5"
    assert query["version"] == "4"


async def test_shares_and_caches_identical_requests(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    await _setup(hass)

    await asyncio.gather(_call(hass), _call(hass), _call(hass))
    await _call(hass)
    assert aioclient_mock.call_count == 1

    await _call(hass, params={"query": "Deli"})
    assert aioclient_mock.call_count == 2


async def test_only_card_endpoints_are_allowed(hass, aioclient_mock) -> None:
    await _setup(hass)

    with pytest.raises(vol.Invalid):
        await _call(hass, path="../admin.json")
    assert aioclient_mock.call_count == 0


async def test_rejected_key_raises_translated_error(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, status=403)
    await _setup(hass)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass)

    assert err.value.translation_key == "futar_auth"
    assert "secret-key" not in str(err.value)


async def test_service_follows_options(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = await _setup(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_FUTAR)

    hass.config_entries.async_update_entry(entry, options={CONF_API_KEY: ""})
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_FUTAR)

    hass.config_entries.async_update_entry(entry, options={CONF_API_KEY: "other"})
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_FUTAR)
    await _call(hass)
    assert aioclient_mock.mock_calls[-1][1].query["key"] == "other"
