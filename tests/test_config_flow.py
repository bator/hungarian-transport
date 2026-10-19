"""Config and options flow for Hungarian transport."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

import custom_components.hungarian_transport as integration
from custom_components.hungarian_transport.const import CONF_API_KEY, DOMAIN
from custom_components.hungarian_transport.futar import BKK_API

SEARCH = f"{BKK_API}/search.json"


async def _start(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_user_flow_without_key(hass) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hungarian transport"
    assert result["options"] == {CONF_API_KEY: ""}


async def test_user_flow_with_valid_key(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: " secret-key "}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_API_KEY: "secret-key"}
    assert aioclient_mock.mock_calls[0][1].query["key"] == "secret-key"


async def test_user_flow_rejected_key(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, status=401)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_unreachable(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, status=503)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "maybe"}
    )

    assert result["errors"] == {"base": "cannot_connect"}


async def test_second_entry_is_refused(hass) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("single_instance_allowed", "already_configured")


async def test_options_flow_sets_and_clears_key(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_API_KEY: "new-key"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_API_KEY: "new-key"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_API_KEY: ""}
    )
    assert entry.options == {CONF_API_KEY: ""}


async def test_reauth_flow_replaces_the_key(hass, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "old"})
    entry.add_to_hass(hass)
    aioclient_mock.get(SEARCH, status=401)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "still-wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: " new-key "}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.options == {CONF_API_KEY: "new-key"}


@pytest.mark.parametrize(("old", "new"), [("old", "new-key"), ("same-key", "same-key")])
async def test_reauth_reloads_the_entry_once(hass, aioclient_mock, old, new) -> None:
    """The update listener reloads the entry; the flow must not reload it a
    second time. The same key accepted again fires no listener, and still
    reloads once, so the proxy that saw it rejected is replaced."""
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: old})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reauth_flow(hass)
    with patch.object(
        integration, "async_setup_entry", wraps=integration.async_setup_entry
    ) as setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: new}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.options == {CONF_API_KEY: new}
    assert setup_entry.call_count == 1
    assert entry.state is config_entries.ConfigEntryState.LOADED
