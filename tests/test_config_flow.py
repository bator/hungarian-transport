"""Config and options flow for Hungarian transport."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.selector import BooleanSelector, TextSelector, TextSelectorType, TimeSelector
from homeassistant.setup import async_setup_component

import custom_components.hungarian_transport as integration
from custom_components.hungarian_transport.config_flow import CONF_REMOVE_KEY
from custom_components.hungarian_transport.const import (
    CONF_API_KEY,
    CONF_DAILY_MAV,
    CONF_DAILY_TIME,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_UPDATES,
    DOMAIN,
)
from custom_components.hungarian_transport.futar import BKK_API

SEARCH = f"{BKK_API}/search.json"
# What the options form saves besides the key when left as shown.
DEFAULTS = {
    CONF_DAILY_MAV: True,
    CONF_DAILY_TIME: "06:00:00",
    CONF_NOTIFY_UPDATES: False,
    CONF_NOTIFY_SERVICE: "",
}


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


async def test_user_flow_rate_limited(hass, aioclient_mock) -> None:
    """HTTP 429 used to say "Could not reach BKK FUTÁR": FUTÁR answered,
    it only refuses the key for a while."""
    aioclient_mock.get(SEARCH, status=429)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "busy"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "rate_limited"}


async def test_user_flow_error_status_is_not_a_valid_key(hass, aioclient_mock) -> None:
    """HTTP 200 with {"status": "ERROR"} used to accept the key."""
    aioclient_mock.get(SEARCH, json={"status": "ERROR", "code": 500})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "unknown"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize("lang", ["en", "hu"])
def test_form_errors_are_translated(lang) -> None:
    folder = Path(integration.__file__).parent / "translations"
    data = json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))
    for section in ("config", "options"):
        assert {"invalid_auth", "cannot_connect", "rate_limited"} <= set(data[section]["error"])


async def test_second_entry_is_refused(hass) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] in ("single_instance_allowed", "already_configured")


def _key_field(result) -> vol.Optional:
    field = next(iter(result["data_schema"].schema))
    assert field == CONF_API_KEY
    return field


# The frontend leaves a cleared optional field out of what it submits ({});
# an API client may send the empty string.
@pytest.mark.parametrize("empty", [{}, {CONF_API_KEY: ""}, {CONF_API_KEY: "  "}])
async def test_options_flow_sets_keeps_and_removes_key(hass, aioclient_mock, empty) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_API_KEY: "new-key"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {**DEFAULTS, CONF_API_KEY: "new-key"}
    assert aioclient_mock.call_count == 1

    # The field comes back empty: leaving it so keeps the key, unasked.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], empty)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {**DEFAULTS, CONF_API_KEY: "new-key"}
    assert aioclient_mock.call_count == 1

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**empty, CONF_REMOVE_KEY: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {**DEFAULTS, CONF_API_KEY: ""}


async def test_typed_key_and_remove_together_is_a_form_error(hass, aioclient_mock) -> None:
    """Saving either would drop the other silently: a new key ticked for
    removal used to save no key at all, unvalidated, and FUTÁR went away."""
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "current"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_API_KEY: "typed", CONF_REMOVE_KEY: True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "remove_and_new_key"}
    for lang in ("en", "hu"):
        strings = json.loads(
            (Path(integration.__file__).parent / "translations" / f"{lang}.json").read_text(encoding="utf-8")
        )
        assert strings["options"]["error"]["remove_and_new_key"]
    assert entry.options == {CONF_API_KEY: "current"}
    assert aioclient_mock.call_count == 0

    # The same form, answered either way, then saves.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_API_KEY: "typed"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {**DEFAULTS, CONF_API_KEY: "typed"}
    assert aioclient_mock.call_count == 1


async def test_stored_key_never_goes_to_the_form(hass) -> None:
    """The form data reaches the admin's browser: the stored key is neither
    a default nor a suggested value. The field is a password field, and a
    checkbox removes the key."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "current"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "current" not in repr(result)
    field = _key_field(result)
    assert field.default is vol.UNDEFINED
    assert not field.description
    selector = result["data_schema"].schema[field]
    assert isinstance(selector, TextSelector)
    assert selector.config["type"] == TextSelectorType.PASSWORD
    remove = next(k for k in result["data_schema"].schema if k == CONF_REMOVE_KEY)
    assert remove.default() is False
    assert isinstance(result["data_schema"].schema[remove], BooleanSelector)


async def test_no_remove_box_without_a_stored_key(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert list(result["data_schema"].schema) == [
        CONF_API_KEY, CONF_DAILY_MAV, CONF_DAILY_TIME, CONF_NOTIFY_UPDATES, CONF_NOTIFY_SERVICE
    ]


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
    assert entry.options == {**DEFAULTS, CONF_API_KEY: new}
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


async def test_options_turn_the_daily_mav_index_off_and_on(hass, aioclient_mock) -> None:
    """On by default; saving the box reloads the entry once, which starts or
    stops the daily updater. The key stays as it was."""
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "kept"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    daily = next(k for k in result["data_schema"].schema if k == CONF_DAILY_MAV)
    assert daily.default() is True
    assert isinstance(result["data_schema"].schema[daily], BooleanSelector)
    for value in (False, False, True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        with patch.object(
            integration, "async_setup_entry", wraps=integration.async_setup_entry
        ) as setup_entry:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_DAILY_MAV: value}
            )
            await hass.async_block_till_done()
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options == {**DEFAULTS, CONF_API_KEY: "kept", CONF_DAILY_MAV: value}
        assert setup_entry.call_count == 1
        # The form shows what is stored.
        result = await hass.config_entries.options.async_init(entry.entry_id)
        daily = next(k for k in result["data_schema"].schema if k == CONF_DAILY_MAV)
        assert daily.default() is value
        hass.config_entries.options.async_abort(result["flow_id"])
    assert aioclient_mock.call_count == 0


async def test_reauth_keeps_the_daily_mav_choice(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "old", CONF_DAILY_MAV: False}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new"})
    assert result["reason"] == "reauth_successful"
    assert entry.options == {CONF_API_KEY: "new", CONF_DAILY_MAV: False}


@pytest.mark.parametrize("lang", ["en", "hu"])
def test_daily_mav_option_is_translated(lang) -> None:
    folder = Path(integration.__file__).parent / "translations"
    init = json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))["options"]["step"]["init"]
    assert init["data"][CONF_DAILY_MAV]
    assert "bator.github.io" in init["data_description"][CONF_DAILY_MAV]


def _field(result, name):
    return next(k for k in result["data_schema"].schema if k == name)


async def test_options_form_has_the_notification_fields(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    assert _field(result, CONF_NOTIFY_UPDATES).default() is False
    assert isinstance(schema[_field(result, CONF_NOTIFY_UPDATES)], BooleanSelector)
    assert _field(result, CONF_DAILY_TIME).default() == "06:00:00"
    assert isinstance(schema[_field(result, CONF_DAILY_TIME)], TimeSelector)
    # A suggested value, not a default: clearing the field must clear it.
    service = _field(result, CONF_NOTIFY_SERVICE)
    assert service.default is vol.UNDEFINED
    assert service.description == {"suggested_value": ""}
    assert isinstance(schema[service], TextSelector)


async def test_options_save_notifications_and_keep_other_options(hass) -> None:
    hass.services.async_register("script", "ertesit", lambda call: None)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        options={CONF_API_KEY: "kept", CONF_DAILY_MAV: False, "future_option": 7},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DAILY_MAV: False, CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: " script.ertesit ",
         CONF_DAILY_TIME: "05:45"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_API_KEY: "kept", CONF_DAILY_MAV: False, "future_option": 7, CONF_NOTIFY_UPDATES: True,
        CONF_NOTIFY_SERVICE: "script.ertesit", CONF_DAILY_TIME: "05:45:00",
    }

    # The form shows what is stored; a cleared service field clears it.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _field(result, CONF_NOTIFY_SERVICE).description == {"suggested_value": "script.ertesit"}
    assert _field(result, CONF_DAILY_TIME).default() == "05:45:00"
    assert _field(result, CONF_NOTIFY_UPDATES).default() is True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_DAILY_MAV: False, CONF_NOTIFY_UPDATES: True, CONF_DAILY_TIME: "05:45:00"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_SERVICE] == ""
    assert entry.options["future_option"] == 7


@pytest.mark.parametrize(
    ("service", "error"),
    [
        ("ertesit", "invalid_service"),
        ("script.ertesit.extra", "invalid_service"),
        ("notify mobile", "invalid_service"),
        ("script.nincs_ilyen", "service_not_found"),
        ("notify.mobile_app_nope", "service_not_found"),
    ],
)
async def test_options_refuse_a_bad_notify_service(hass, service, error) -> None:
    hass.services.async_register("script", "ertesit", lambda call: None)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: service}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_NOTIFY_SERVICE: error}
    # The form keeps what was typed.
    assert _field(result, CONF_NOTIFY_SERVICE).description == {"suggested_value": service}
    assert _field(result, CONF_NOTIFY_UPDATES).default() is True
    assert entry.options == {CONF_API_KEY: ""}


async def test_options_service_is_stored_as_home_assistant_names_it(hass) -> None:
    hass.services.async_register("script", "ertesit", lambda call: None)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_SERVICE: "Script.Ertesit"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_SERVICE] == "script.ertesit"


async def test_options_refuse_a_bad_time(hass) -> None:
    """The time selector refuses it before the step sees it."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], {CONF_DAILY_TIME: "25:99"})
    assert entry.options == {CONF_API_KEY: ""}


async def test_reauth_keeps_the_notification_options(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    options = {CONF_API_KEY: "old", CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: "script.ertesit",
               CONF_DAILY_TIME: "05:00:00"}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options)
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new"})
    assert result["reason"] == "reauth_successful"
    assert entry.options == {**options, CONF_API_KEY: "new"}


@pytest.mark.parametrize("lang", ["en", "hu"])
def test_notification_options_are_translated(lang) -> None:
    folder = Path(integration.__file__).parent / "translations"
    options = json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))["options"]
    init = options["step"]["init"]
    for key in (CONF_NOTIFY_UPDATES, CONF_NOTIFY_SERVICE, CONF_DAILY_TIME):
        assert init["data"][key]
        assert init["data_description"][key]
    for error in ("invalid_service", "service_not_found"):
        assert options["error"][error]
    assert init["data"][CONF_NOTIFY_UPDATES] == {
        "en": "Notify about timetable updates", "hu": "Értesítés a menetrend-frissítésekről"
    }[lang]
