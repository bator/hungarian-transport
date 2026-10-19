"""Config and options flow for Hungarian transport."""

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelector, TextSelectorType
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


def _key_field(result) -> vol.Optional:
    (field,) = result["data_schema"].schema
    assert field == CONF_API_KEY
    return field


# The frontend leaves a cleared optional field out of what it submits ({});
# an API client may send the empty string.
@pytest.mark.parametrize("cleared", [{}, {CONF_API_KEY: ""}])
async def test_options_flow_sets_and_clears_key(hass, aioclient_mock, cleared) -> None:
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
    result = await hass.config_entries.options.async_configure(result["flow_id"], cleared)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_API_KEY: ""}


async def test_key_field_is_masked_and_only_suggested(hass) -> None:
    """A password field, prefilled with the current key as a suggestion, not
    a default that would bring a cleared key back."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "current"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    field = _key_field(result)
    assert field.default is vol.UNDEFINED
    assert field.description == {"suggested_value": "current"}
    selector = result["data_schema"].schema[field]
    assert isinstance(selector, TextSelector)
    assert selector.config["type"] == TextSelectorType.PASSWORD


async def test_user_form_has_no_suggested_key(hass) -> None:
    result = await _start(hass)
    field = _key_field(result)
    assert field.default is vol.UNDEFINED
    assert not field.description
    assert isinstance(result["data_schema"].schema[field], TextSelector)


@pytest.mark.parametrize(("old", "new"), [("old-key", "new-key"), ("same-key", "same-key")])
async def test_options_save_reloads_the_entry_once(hass, aioclient_mock, old, new) -> None:
    """Saving the same key changes no option and fires no update listener;
    the entry still reloads (once), so a FUTÁR proxy that saw the key
    rejected asks again, as the futar_auth error tells the user."""
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: old})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(
        integration, "async_setup_entry", wraps=integration.async_setup_entry
    ) as setup_entry:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_API_KEY: new}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_API_KEY: new}
    assert setup_entry.call_count == 1
    assert entry.state is config_entries.ConfigEntryState.LOADED


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


async def test_reauth_unreachable_keeps_the_form(hass, aioclient_mock) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "old"})
    entry.add_to_hass(hass)
    aioclient_mock.get(SEARCH, status=503)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "maybe"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.options == {CONF_API_KEY: "old"}


@pytest.mark.parametrize("cleared", [{}, {CONF_API_KEY: "  "}])
async def test_reauth_with_no_key_removes_it(hass, aioclient_mock, cleared) -> None:
    """No key at all is a valid answer: FUTÁR access goes, nothing is asked upstream."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "old"})
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], cleared)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.options == {CONF_API_KEY: ""}
    assert aioclient_mock.call_count == 0


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
