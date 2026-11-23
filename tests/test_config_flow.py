"""Config and options flow for Hungarian transport."""

import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    TextSelector,
    TextSelectorType,
    TimeSelector,
)
from homeassistant.setup import async_setup_component

import custom_components.hungarian_transport as integration
from custom_components.hungarian_transport.config_flow import CONF_REMOVE_KEY, NO_SERVICE
from custom_components.hungarian_transport.const import (
    CONF_API_KEY,
    CONF_DAILY_MAV,
    CONF_DAILY_TIME,
    CONF_MAVPLUSZ,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_UPDATES,
    CONF_PAGES_TIMETABLES,
    DOMAIN,
)
from custom_components.hungarian_transport import mav_daily, weekly_timetables
from custom_components.hungarian_transport.futar import BKK_API

SEARCH = f"{BKK_API}/search.json"
# What the options form saves besides the key when left as shown.
DEFAULTS = {
    CONF_MAVPLUSZ: True,
    CONF_DAILY_MAV: True,
    CONF_PAGES_TIMETABLES: True,
    CONF_DAILY_TIME: "06:00:00",
    CONF_NOTIFY_UPDATES: False,
    CONF_NOTIFY_DAILY: False,
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
    assert "key_accepted_again" in data["config"]["abort"]


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
        CONF_API_KEY, CONF_MAVPLUSZ, CONF_DAILY_MAV, CONF_PAGES_TIMETABLES, CONF_DAILY_TIME, CONF_NOTIFY_UPDATES,
        CONF_NOTIFY_SERVICE, CONF_NOTIFY_DAILY,
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


@pytest.mark.parametrize(
    ("answer", "pending"),
    [
        ({CONF_API_KEY: "new-key"}, False),
        ({CONF_REMOVE_KEY: True}, False),
        # The stored key saved again, unchecked: the reauth stays.
        ({}, True),
    ],
    ids=["new-key", "removed", "kept"],
)
async def test_options_key_answers_a_pending_reauth(hass, aioclient_mock, answer, pending) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "old"})
    entry.add_to_hass(hass)
    other = MockConfigEntry(domain=DOMAIN, unique_id="other", options={CONF_API_KEY: "x"})
    other.add_to_hass(hass)
    # Started as the integration starts them, with their Repairs issues.
    entry.async_start_reauth(hass)
    other.async_start_reauth(hass)
    await hass.async_block_till_done()
    issues = ir.async_get(hass)

    def issue(e):
        return issues.async_get_issue("homeassistant", f"config_entry_reauth_{DOMAIN}_{e.entry_id}")

    assert issue(entry) is not None and issue(other) is not None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], answer)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": config_entries.SOURCE_REAUTH}
    )
    assert sorted(f["context"]["entry_id"] for f in flows) == sorted(
        [other.entry_id, *([entry.entry_id] if pending else [])]
    )
    # An answered reauth takes its Repairs issue along (HA 2024.10 does not).
    assert (issue(entry) is not None) == pending
    assert issue(other) is not None


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


@pytest.mark.parametrize("option", [CONF_MAVPLUSZ, CONF_PAGES_TIMETABLES])
async def test_options_turn_a_source_off_and_a_reauth_keeps_it(hass, aioclient_mock, option) -> None:
    """mavplusz.hu and the weekly timetables are on by default; turned off
    in the form they are saved off, the form shows them off, and a reauth's
    new key leaves them off."""
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "old"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _field(result, option).default() is True
    assert isinstance(result["data_schema"].schema[_field(result, option)], BooleanSelector)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {option: False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {**DEFAULTS, CONF_API_KEY: "old", option: False}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _field(result, option).default() is False
    hass.config_entries.options.async_abort(result["flow_id"])

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "new"})
    assert result["reason"] == "reauth_successful"
    assert entry.options == {**DEFAULTS, CONF_API_KEY: "new", option: False}


@pytest.mark.parametrize("lang", ["en", "hu"])
def test_mavplusz_option_says_what_goes_where(lang) -> None:
    folder = Path(integration.__file__).parent / "translations"
    init = json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))["options"]["step"]["init"]
    assert "mavplusz.hu" in init["data"][CONF_MAVPLUSZ]
    text = init["data_description"][CONF_MAVPLUSZ]
    for name in ("mavplusz.hu", "GTFS-Realtime", "jbb.ghsq.de", "gpspositions.net", "Transitous", "FUTÁR"):
        assert name in text
    # PlannerLimiter gates only new tokens: a request already sent when the
    # option is saved off still gets its answer, and the dialog says so.
    assert ("a request already sent gets its answer", "a már elküldött megkapja a válaszát")[lang == "hu"] in text


def _doc(*parts: str) -> str:
    """A doc of the repository, its wrapping undone."""
    text = Path(__file__).parents[1].joinpath(*parts).read_text(encoding="utf-8")
    return " ".join(text.split())


def test_readme_names_mavplusz_and_links_its_opt_out() -> None:
    """The README's Data table names mavplusz.hu where the code asks it
    first (the live delays, the BKK boards while FUTÁR fails) and links the
    option that turns it off, as it links the Pages opt-outs."""
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
    rows = {row.split(" | ")[0]: row for row in readme.splitlines() if row.startswith("| ")}
    live = next(row for name, row in rows.items() if "Live delays and positions outside Budapest" in name)
    bkk = next(row for name, row in rows.items() if name.startswith("| Budapest buses"))
    for row in (live, bkk):
        assert "mavplusz.hu" in row and "[opt out][manual-settings]" in row
    assert "gpspositions.net" in live
    link = re.search(r"^\[manual-settings\]: (\S+)$", readme, re.M)[1]
    assert link.endswith("docs/manual/README.md#31-settings")
    assert "### 3.1 Settings" in _doc("docs", "manual", "README.md")


@pytest.mark.parametrize(
    ("lang", "sent"),
    [("README.md", "one already sent gets its answer"), ("README.hu.md", "a már elküldött megkapja a válaszát")],
)
def test_manual_says_what_the_mavplusz_option_stops(lang, sent) -> None:
    """Saving the option off stops a request still waiting for its token,
    not one already sent (PlannerLimiter gates only new tokens), and the
    feeds read then are not all on jbb.ghsq.de (Orosháza's is not)."""
    manual = _doc("docs", "manual", lang)
    assert sent in manual
    assert "on its way when you save stops" not in manual
    assert "már úton lévő kérés sem megy el" not in manual
    assert "jbb.ghsq.de" not in manual


def test_timetables_privacy_names_every_request() -> None:
    """The privacy paragraph lists the signatures fetched with each
    manifest, the settle re-read and the retries."""
    doc = _doc("docs", "timetables.md")
    privacy = doc[doc.index("**Privacy.**") : doc.index("**Notifications.**")]
    for url in (mav_daily.SIGNATURE_URL, weekly_timetables.SIGNATURE_URL):
        assert f"`{url.rsplit('/', 1)[1]}`" in privacy
    assert f"{mav_daily.PAGES_SETTLE_DELAY // 60} minutes later" in privacy
    assert f"up to {mav_daily.RETRY_LIMIT} times {mav_daily.RETRY_DELAY // 60} minutes apart" in privacy
    assert f"up to {weekly_timetables.RETRY_LIMIT} times {weekly_timetables.RETRY_DELAY // 60} minutes apart" in privacy


def _reason_words(module) -> dict[str, str]:
    """A module's reason words (DailyRound.reason, IndexResult.reason, the
    sensors' reason and pages_reason): its REASON_* constants, and
    UNKNOWN_KEY, the one not named so."""
    return {
        name: value
        for name, value in vars(module).items()
        if (name.startswith("REASON_") or name == "UNKNOWN_KEY") and isinstance(value, str)
    }


def test_timetables_doc_lists_every_reason_word() -> None:
    """docs/timetables.md "Timetable sensors" names each reason word a
    sensor can show, and only those: a new one in mav_daily.py or
    weekly_timetables.py fails here until the doc says what it means, and
    one the code dropped fails until the doc drops it."""
    words = _reason_words(mav_daily) | _reason_words(weekly_timetables)
    assert {"REASON_BAD_SIGNATURE", "UNKNOWN_KEY"} <= words.keys()
    # The paragraph's wrapping is free: compare its words, not its lines.
    text = _doc("docs", "timetables.md")
    start = text.index("`pages_reason` says why an index was not installed:")
    end = text.index("else the error's text", start)
    # The results the paragraph names along the way are not reasons.
    results = {mav_daily.SUPERSEDED, mav_daily.FAILED, "pages_reason"}
    listed = set(re.findall(r"`([a-z_]+)`", text[start:end])) - results
    assert listed == set(words.values()), (listed, words)
    assert f"at most {mav_daily.MAX_REASON} characters" in text[end:end + 80]


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
    # Nothing stored: the list shows "none".
    assert service.description == {"suggested_value": NO_SERVICE}
    selector = schema[service]
    assert isinstance(selector, SelectSelector)
    assert selector.config["custom_value"] is True
    assert selector.config["mode"] == "dropdown"
    assert selector.config["translation_key"] == CONF_NOTIFY_SERVICE
    assert selector.config["options"][0] == {"value": NO_SERVICE, "label": "— none —"}


def _options(result) -> list[dict[str, str]]:
    return result["data_schema"].schema[_field(result, CONF_NOTIFY_SERVICE)].config["options"]


async def test_options_list_scripts_and_notify_services(hass) -> None:
    for name in ("ertesit", "nevtelen", "reload", "turn_on", "turn_off", "toggle"):
        hass.services.async_register("script", name, lambda call: None)
    hass.states.async_set("script.ertesit", "off", {"friendly_name": "Központi értesítés"})
    for name in ("mobile_app_iphone", "discord", "notify", "send_message", "persistent_notification"):
        hass.services.async_register("notify", name, lambda call: None)
    hass.services.async_register("light", "turn_on", lambda call: None)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # "none" first, the rest by label.
    assert _options(result) == [
        {"value": NO_SERVICE, "label": "— none —"},
        {"value": "notify.discord", "label": "discord (notify)"},
        {"value": "script.ertesit", "label": "Központi értesítés (script.ertesit)"},
        {"value": "notify.mobile_app_iphone", "label": "mobile_app_iphone (notify)"},
        {"value": "script.nevtelen", "label": "nevtelen (script)"},
    ]


async def test_options_list_keeps_a_stored_service_that_is_gone(hass) -> None:
    hass.services.async_register("script", "ertesit", lambda call: None)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        options={CONF_API_KEY: "", CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: "notify.mobile_app_old"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _field(result, CONF_NOTIFY_SERVICE).description == {"suggested_value": "notify.mobile_app_old"}
    assert [o["value"] for o in _options(result)] == [NO_SERVICE, "script.ertesit", "notify.mobile_app_old"]
    assert {"value": "notify.mobile_app_old", "label": "notify.mobile_app_old (missing)"} in _options(result)

    # A stored service that still exists is listed once.
    hass.services.async_register("notify", "mobile_app_old", lambda call: None)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert [o["value"] for o in _options(result)].count("notify.mobile_app_old") == 1


@pytest.mark.parametrize("picked", [NO_SERVICE, f" {NO_SERVICE} ", ""])
async def test_options_none_stores_no_service(hass, picked) -> None:
    hass.services.async_register("script", "ertesit", lambda call: None)
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        options={CONF_API_KEY: "", CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: "script.ertesit"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: picked}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_SERVICE] == ""


async def test_options_typed_service_not_in_the_list(hass) -> None:
    """A typed service is checked like before; one Home Assistant has is
    stored even when the list does not show it."""
    hass.services.async_register("tts", "speak", lambda call: None)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "tts.speak" not in [o["value"] for o in _options(result)]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_SERVICE: "notify.mobile_app_nope"}
    )
    assert result["errors"] == {CONF_NOTIFY_SERVICE: "service_not_found"}
    # Shown again, and listed so the form can show it.
    assert "notify.mobile_app_nope" in [o["value"] for o in _options(result)]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_SERVICE: "TTS.Speak"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_SERVICE] == "tts.speak"


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
        CONF_NOTIFY_DAILY: False, CONF_NOTIFY_SERVICE: "script.ertesit", CONF_DAILY_TIME: "05:45:00",
        CONF_PAGES_TIMETABLES: True, CONF_MAVPLUSZ: True,
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
        # Only the list's own "none" entry means no service (the manual).
        ("None", "invalid_service"),
        ("NONE", "invalid_service"),
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
    for key in (CONF_NOTIFY_UPDATES, CONF_NOTIFY_DAILY, CONF_NOTIFY_SERVICE, CONF_DAILY_TIME):
        assert init["data"][key]
        assert init["data_description"][key]
    for error in ("invalid_service", "service_not_found"):
        assert options["error"][error]
    selector = json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))["selector"]
    assert selector[CONF_NOTIFY_SERVICE]["options"][NO_SERVICE] == {"en": "— none —", "hu": "— nincs —"}[lang]
    assert init["data"][CONF_NOTIFY_UPDATES] == {
        "en": "Notify about timetable updates", "hu": "Értesítés a menetrend-frissítésekről"
    }[lang]


async def test_options_save_other_fields_while_the_stored_service_is_gone(hass) -> None:
    """A stored service Home Assistant no longer has (a removed script) does
    not block the form: it stays stored, marked missing in the list; only a
    newly picked missing one is refused."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN,
        options={CONF_API_KEY: "", CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: "script.gone"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {"value": "script.gone", "label": "script.gone (missing)"} in _options(result)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: "script.gone", CONF_DAILY_TIME: "07:00"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_DAILY_TIME] == "07:00:00"
    assert entry.options[CONF_NOTIFY_SERVICE] == "script.gone"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_SERVICE: "script.other_gone"}
    )
    assert result["errors"] == {CONF_NOTIFY_SERVICE: "service_not_found"}


async def test_options_missing_label_in_hungarian(hass) -> None:
    hass.config.language = "hu"
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "", CONF_NOTIFY_SERVICE: "script.gone"}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {"value": "script.gone", "label": "script.gone (hiányzik)"} in _options(result)


async def test_options_script_label_follows_a_renamed_entity(hass) -> None:
    """The script service is the script's unique_id; its entity_id may have
    been renamed, so the name comes through the entity registry."""
    from homeassistant.helpers import entity_registry as er

    hass.services.async_register("script", "ertesit", lambda call: None)
    registry = er.async_get(hass)
    registry.async_get_or_create("script", "script", "ertesit", suggested_object_id="kozponti")
    assert registry.async_get_entity_id("script", "script", "ertesit") == "script.kozponti"
    hass.states.async_set("script.kozponti", "off", {"friendly_name": "Központi értesítés"})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {"value": "script.ertesit", "label": "Központi értesítés (script.ertesit)"} in _options(result)


async def test_options_list_sorts_accented_labels_with_their_letter(hass) -> None:
    for name, friendly in (("a", "Fűtés"), ("b", "Értesítés"), ("c", "Ébresztő"), ("d", "Zár"), ("e", "Ajtó")):
        hass.services.async_register("script", name, lambda call: None)
        hass.states.async_set(f"script.{name}", "off", {"friendly_name": friendly})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    labels = [o["label"].split(" (")[0] for o in _options(result)[1:]]
    assert labels == ["Ajtó", "Ébresztő", "Értesítés", "Fűtés", "Zár"]


async def test_options_daily_install_notification_switch(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: ""})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert _field(result, CONF_NOTIFY_DAILY).default() is False
    assert isinstance(result["data_schema"].schema[_field(result, CONF_NOTIFY_DAILY)], BooleanSelector)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY_UPDATES: True, CONF_NOTIFY_DAILY: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_NOTIFY_DAILY] is True


@pytest.mark.parametrize(
    ("check", "reason", "saved"),
    [(None, "reauth_successful", "new-key"), ("invalid_auth", "key_accepted_again", "secret-key")],
    ids=["new-key-works", "new-key-refused"],
)
async def test_reauth_answered_while_the_stored_key_works_again(
    hass, aioclient_mock, monkeypatch, check, reason, saved
) -> None:
    """The typed key is checked for up to 18 s; meanwhile a card's call can
    find the stored key working again, which ends the reauth. Ended under
    the running step, the step's answer raised UnknownFlow (Home Assistant
    2024.10 even after the new key was saved). The step ends the flow
    itself instead: with the new key when it works, else with the stored
    one, which works."""
    from custom_components.hungarian_transport import config_flow, futar

    from .test_futar import _call, _inject, _setup
    from .test_upstream import FakeClock

    clock = _inject(monkeypatch, FakeClock())
    aioclient_mock.get(SEARCH, status=401)
    entry = await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    async def check_meanwhile(_hass, api_key):
        aioclient_mock.clear_requests()
        aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
        clock.now += futar.REJECTED_RETRY + 1
        assert (await _call(hass, params={"query": "Deli"}))["data"]["status"] == "OK"
        return check

    monkeypatch.setattr(config_flow, "_validate_key", check_meanwhile)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_API_KEY: "new-key"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert entry.options[CONF_API_KEY] == saved
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


def test_the_mavplusz_option_says_what_it_asks_for_and_when() -> None:
    """The Volán buses the planner answers for are the intercity ones (the
    local ones are Volánbusz's), and the Budapest ids go to it only while
    FUTÁR does not answer, in both languages."""
    folder = Path(integration.__file__).parent / "translations"
    text = {
        lang: json.loads((folder / f"{lang}.json").read_text(encoding="utf-8"))["options"]["step"]["init"][
            "data_description"
        ]["mavplusz"]
        for lang in ("en", "hu")
    }
    assert "a Volán helyközi és a Volánbusz helyi járatai" in text["hu"]
    assert "amíg a BKK FUTÁR nem válaszol" in text["hu"] and "hibás" not in text["hu"]
    assert "Volán intercity and Volánbusz local" in text["en"]
    assert "while BKK FUTÁR does not answer" in text["en"]
