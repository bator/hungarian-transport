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

from homeassistant.components import persistent_notification
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
    CONF_API_KEY,
    CONF_DAILY_MAV,
    CONF_MAVPLUSZ,
    CONF_PAGES_TIMETABLES,
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


async def test_setup_wires_the_idle_drop_timer_and_the_executor(hass: HomeAssistant, monkeypatch) -> None:
    """The realtime hub drops idle feeds on HA's timer (closed when HA
    stops), and ELVIRA's boards are decoded and settled in the executor."""
    made: dict[str, dict] = {}
    real_hub, real_boards = integration.realtime.RealtimeHub, integration.ElviraBoards

    def hub(*args, **kwargs):
        made["hub"] = kwargs
        made["hub_obj"] = real_hub(*args, **kwargs)
        return made["hub_obj"]

    def boards(fetch, **kwargs):
        made["boards"] = {**kwargs, "fetch": fetch}
        return real_boards(fetch, **kwargs)

    monkeypatch.setattr(integration.realtime, "RealtimeHub", hub)
    monkeypatch.setattr(integration, "ElviraBoards", boards)
    await _setup(hass)
    assert made["hub"].get("call_later") is not None
    assert made["boards"].get("executor") is not None
    assert made["boards"]["fetch"].keywords.get("executor") is not None
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert made["hub_obj"]._closed


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
    # Added again without a restart: the resource comes back.
    assert await hass.config_entries.async_unload(entry.entry_id)
    again = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    again.add_to_hass(hass)
    assert await hass.config_entries.async_setup(again.entry_id)
    await hass.async_block_till_done()
    assert [(item["type"], item["url"]) for item in _our_resources(hass)] == [("module", card_url)]


async def test_removal_takes_the_update_record_along(hass, hass_storage) -> None:
    entry = await _setup(hass)
    await integration.storage.async_record_daily_round(hass, "failed", None, None)
    await hass.async_block_till_done()
    assert integration.storage.STORAGE_KEY in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert integration.storage.STORAGE_KEY not in hass_storage
    assert "update_state" not in hass.data[DOMAIN]
    # A new entry starts from an empty record.
    assert await integration.storage.async_load(hass) == {}


async def test_removal_keeps_what_installed_the_mav_index(hass, hass_storage) -> None:
    """The MÁV index stays in www/ after a removal: so does the record of
    what installed it, and nothing else."""
    storage = integration.storage
    entry = await _setup(hass)
    await storage.async_record_mav_install(hass, storage.SOURCE_DAILY, "20261101.M2ChangeID", 1234)
    await storage.async_record_daily_round(hass, "installed", "20261101.M2ChangeID", None)
    install = (await storage.async_load(hass))[storage.MAV_INSTALL]
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[storage.STORAGE_KEY]["data"] == {storage.MAV_INSTALL: install}
    assert "update_state" not in hass.data[DOMAIN]
    assert await storage.async_load(hass) == {storage.MAV_INSTALL: install}


async def test_removal_keeps_both_install_records_and_drops_the_learnt_stations(hass, hass_storage) -> None:
    """The weekly Volán and city indexes stay in www/ as well: their record
    stays beside the MÁV one. The BKK stations learnt from FUTÁR go, also
    from an entry without a key (no planner this run wrote them)."""
    storage = integration.storage
    hass_storage[storage.STATIONS_KEY] = {
        "version": storage.STATIONS_VERSION, "minor_version": 1, "key": storage.STATIONS_KEY,
        "data": {"stations": {"BKK_CSF00940": {"ref": None, "poles": ["BKK_F00940"]}}},
    }
    entry = await _setup(hass)
    await storage.async_record_mav_install(hass, storage.SOURCE_DAILY, "20261101.M2ChangeID", 1234)
    await storage.async_record_timetables_install(hass, "volan", "20261110.volan", "ab" * 32, 5678)
    await storage.async_record_pages_round(hass, {"volan": ("installed", "20261110.volan")})
    state = await storage.async_load(hass)
    kept = {key: state[key] for key in (storage.MAV_INSTALL, storage.TIMETABLES_INSTALL)}
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[storage.STORAGE_KEY]["data"] == kept
    assert kept[storage.TIMETABLES_INSTALL]["volan"]["feed"] == "20261110.volan"
    assert storage.STATIONS_KEY not in hass_storage


@pytest.mark.parametrize(
    "stored",
    [
        {"key": "hungarian_transport.bkk_stations"},
        {"version": 99, "minor_version": 1, "key": "hungarian_transport.bkk_stations", "data": {"stations": {}}},
    ],
    ids=["unreadable", "newer-version"],
)
async def test_a_broken_stations_record_does_not_stop_the_setup(hass, hass_storage, caplog, stored) -> None:
    """storage.async_load_stations: a record Home Assistant cannot read
    (a broken file, one a newer version wrote) is a warning, and the BKK
    boards start with no stations learnt."""
    from custom_components.hungarian_transport import mavplusz_board

    storage = integration.storage
    hass_storage[storage.STATIONS_KEY] = stored
    made = []
    executors = []
    real = mavplusz_board.PlannerBoards

    def boards(*args, **kwargs):
        made.append(kwargs["stations"])
        # A large planner board is decoded in HA's executor, not the loop.
        executors.append(kwargs.get("executor"))
        return real(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(integration.mavplusz_board, "PlannerBoards", boards)
        assert await async_setup_component(hass, "http", {})
        entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: "secret-key"})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is integration.ConfigEntryState.LOADED
    assert hass.services.has_service(DOMAIN, integration.SERVICE_FUTAR)
    assert made == [None]
    assert executors == [hass.async_add_executor_job]
    assert "Could not read the learnt BKK stations" in caplog.text


@pytest.mark.parametrize(
    ("daily", "pages", "made"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
async def test_no_daily_updater_with_the_daily_index_and_the_weekly_timetables_off(
    hass, monkeypatch, daily, pages, made
) -> None:
    """Either one on runs the daily rounds; both off, nothing is scheduled
    and no request goes to GitHub Pages."""
    created = []
    real = integration.mav_daily.DailyIndexUpdater

    def updater(*args, **kwargs):
        created.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(integration.mav_daily, "DailyIndexUpdater", updater)
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, options={CONF_DAILY_MAV: daily, CONF_PAGES_TIMETABLES: pages}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert bool(created) is made
    if made:
        (kwargs,) = created
        assert kwargs["mav"] is daily and (kwargs["timetables"] is not None) is pages


@pytest.mark.parametrize("on", [True, False], ids=["default-on", "turned-off"])
async def test_mavplusz_off_reads_a_trains_delay_from_the_feeds_only(hass, aioclient_mock, freezer, on) -> None:
    """CONF_MAVPLUSZ off (saved in the options: the entry reloads, the hub
    stays): a train's delay round reads the jbb.ghsq.de feeds and sends
    nothing to mavplusz.hu. On (the default) the planner is asked first."""
    from custom_components.hungarian_transport import mavplusz

    from .test_mavplusz import TrainPlanner
    from .test_realtime import FIX, FROZEN, GYSEV_STATIC, J, MAV_STATIC, _pb

    freezer.move_to(FROZEN)
    entry = await _setup(hass)
    if not on:
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_MAVPLUSZ: False})
        await hass.async_block_till_done()
        assert entry.state is integration.ConfigEntryState.LOADED
    # A planner that knows none of the trips: the feeds answer either way.
    aioclient_mock.post(mavplusz.URL, side_effect=TrainPlanner({}))
    aioclient_mock.get(J + "1_198/trip-updates.pb", content=_pb("mav-trip-updates"))
    aioclient_mock.get(J + "1_198/vehicle-positions.pb", content=_pb("mav-vehicle-positions"))
    aioclient_mock.get(MAV_STATIC, content=(FIX / "mav-static.zip").read_bytes())
    aioclient_mock.get(J + "1_134/trip-updates.pb", status=503)
    aioclient_mock.get(J + "1_134/vehicle-positions.pb", status=503)
    aioclient_mock.get(GYSEV_STATIC, status=503)
    req = {"key": "ic", "train": "822", "stops": ["005501024", "005506189"], "from": 0, "to": 1}
    resp = await hass.services.async_call(
        DOMAIN, SERVICE_REALTIME, {"feed": "mav", "trips": [req]}, blocking=True, return_response=True
    )
    hit = resp["trips"]["ic"]
    assert (hit["trip_id"], hit["delay"], hit["dest_delay"]) == ("35700507", 0, 600)
    planner_calls = [c for c in aioclient_mock.mock_calls if "mavplusz" in str(c[1])]
    assert len(planner_calls) == (1 if on else 0)


async def test_the_planner_limiter_reads_the_mavplusz_option_per_request(hass: HomeAssistant) -> None:
    """The limiter every planner request takes its token from (the hub's
    and the BKK boards') reads "Ask mavplusz.hu" itself: once the option
    is saved off it hands out no token, so a request already on its way
    (a round queued for the hub's lock) is not sent; on again, it does."""
    entry = await _setup(hass)
    limiter = hass.data[DOMAIN]["planner_limiter"]
    assert limiter.available() and limiter.take()
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_MAVPLUSZ: False})
    await hass.async_block_till_done()
    assert hass.data[DOMAIN]["planner_limiter"] is limiter
    assert not limiter.available() and not limiter.take() and not limiter.take(fallback=True)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_MAVPLUSZ: True})
    await hass.async_block_till_done()
    assert limiter.available() and limiter.take(fallback=True)


@pytest.mark.parametrize("on", [True, False], ids=["default-on", "turned-off"])
async def test_mavplusz_off_leaves_a_failing_futar_board_to_transitous(
    hass, aioclient_mock, monkeypatch, on
) -> None:
    """CONF_MAVPLUSZ off: no planner behind the FUTÁR proxy, so a board and
    a trip's details FUTÁR fails for answer FUTÁR's error (the card reads
    Transitous then) and no BKK id goes to mavplusz.hu."""
    from custom_components.hungarian_transport import futar, mavplusz, mavplusz_board as mb

    from .test_futar import _call, _inject
    from .test_mavplusz_board import BOARD, BOARD_PARAMS, FIRST_TRIP, TRIP, WALL, Planner
    from .test_upstream import FakeClock

    made = []
    real = mb.PlannerBoards

    def boards(*a, **k):
        # The fixture's wall time, through the planner's own clock: the
        # global time.time stays Home Assistant's own during the test.
        made.append(1)
        return real(*a, **dict(k, wall=lambda: WALL))

    monkeypatch.setattr(integration.mavplusz_board, "PlannerBoards", boards)
    _inject(monkeypatch, FakeClock())
    aioclient_mock.get(f"{futar.BKK_API}/{BOARD}", status=503)
    aioclient_mock.get(f"{futar.BKK_API}/{TRIP}", status=503)
    aioclient_mock.post(mavplusz.URL, side_effect=Planner())
    assert await async_setup_component(hass, "http", {})
    options = {CONF_API_KEY: "secret-key"} if on else {CONF_API_KEY: "secret-key", CONF_MAVPLUSZ: False}
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    board = await _call(hass, path=BOARD, params=BOARD_PARAMS)
    trip = await _call(hass, path=TRIP, params={"tripId": FIRST_TRIP})
    planner_calls = [c for c in aioclient_mock.mock_calls if "mavplusz" in str(c[1])]
    if on:
        assert made == [1]
        assert board["data"]["source"] == "mavplusz"
        # Its departures, due at the fixture's wall time (none at today's).
        assert board["data"]["data"]["entry"]["stopTimes"]
        assert planner_calls
    else:
        assert made == []
        assert board["error"] == trip["error"] == responses.FUTAR_UNAVAILABLE
        assert board["data"] is None and trip["data"] is None
        assert planner_calls == []


async def test_removal_dismisses_the_integrations_notifications(hass, monkeypatch) -> None:
    entry = await _setup(hass)
    dismissed: list[str] = []
    monkeypatch.setattr(persistent_notification, "async_dismiss", lambda _hass, nid: dismissed.append(nid))
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    notify = integration.timetable_notify
    assert sorted(dismissed) == sorted([
        notify.NOTIFICATION_SHIPPED,
        notify.NOTIFICATION_MAV_DAILY,
        notify.NOTIFICATION_DAILY_FAILED,
        notify.NOTIFICATION_PAGES,
        mav.NOTIFICATION_ID,
    ])


async def test_removal_takes_the_mav_build_issue_along(hass) -> None:
    """A failed MÁV build's Repairs issue ends with the next build; once the
    integration is removed none comes, so the removal ends it."""
    entry = await _setup(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        mav.ISSUE_BUILD_FAILED,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=mav.ISSUE_BUILD_FAILED,
    )
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, mav.ISSUE_BUILD_FAILED) is None


def _dashboards(hass: HomeAssistant) -> dict:
    data = hass.data["lovelace"]
    return data["dashboards"] if isinstance(data, dict) else data.dashboards


def _card_key_issue(hass: HomeAssistant):
    return ir.async_get(hass).async_get_issue(DOMAIN, integration.lovelace_resource.ISSUE_CARD_API_KEY)


def _view(*cards) -> dict:
    return {"views": [{"type": "sections", "sections": [{"type": "grid", "cards": list(cards)}]}]}


async def test_card_level_key_on_a_dashboard_is_a_repairs_issue(hass) -> None:
    """Any logged-in user can read a card's own key from lovelace/config:
    a Repairs issue names the dashboards holding one, follows their saves,
    and goes with the last such card or with the integration."""
    from homeassistant.components.lovelace.dashboard import LovelaceStorage

    assert await async_setup_component(hass, "lovelace", {})
    default = _dashboards(hass)[None]
    hub = LovelaceStorage(hass, {"id": "hub", "url_path": "dashboard-hub", "mode": "storage"})
    _dashboards(hass)["dashboard-hub"] = hub
    await default.async_save(_view({"type": "custom:hungarian-transport-card", "apiKey": "card-key"}))
    # Someone else's card with an apiKey is not ours to report.
    await hub.async_save(_view({"type": "custom:other-card", "apiKey": "theirs"}))
    entry = await _setup(hass)
    issue = _card_key_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"dashboards": "lovelace"}
    assert issue.severity is ir.IssueSeverity.WARNING

    # Nested in a stack, under its alias: found once the dashboard is saved.
    stack = {"type": "vertical-stack", "cards": [{"type": "custom:bkk-stop-card-plan", "apiKey": "k"}]}
    await hub.async_save(_view(stack))
    await hass.async_block_till_done()
    assert _card_key_issue(hass).translation_placeholders == {"dashboards": "dashboard-hub, lovelace"}

    await default.async_save(_view({"type": "custom:hungarian-transport-card", "apiKey": "  "}))
    await hub.async_save(_view({"type": "custom:bkk-stop-card-plan"}))
    await hass.async_block_till_done()
    assert _card_key_issue(hass) is None

    await default.async_save(_view({"type": "custom:hungarian-transport-card", "apiKey": "card-key"}))
    await hass.async_block_till_done()
    assert _card_key_issue(hass) is not None
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert _card_key_issue(hass) is None


async def test_card_key_check_skips_what_it_cannot_read(hass, monkeypatch) -> None:
    """Lovelace's dashboards are its internals: a dashboard that cannot be
    read is skipped, and a check that fails as a whole changes nothing."""
    lovelace = integration.lovelace_resource
    assert await async_setup_component(hass, "lovelace", {})

    async def unreadable(_force):
        raise RuntimeError("changed internals")

    default = _dashboards(hass)[None]
    await default.async_save(_view({"type": "custom:hungarian-transport-card", "apiKey": "card-key"}))
    monkeypatch.setattr(default, "async_load", unreadable)
    await _setup(hass)
    assert _card_key_issue(hass) is None

    ir.async_create_issue(
        hass, DOMAIN, lovelace.ISSUE_CARD_API_KEY, is_fixable=False,
        severity=ir.IssueSeverity.WARNING, translation_key=lovelace.ISSUE_CARD_API_KEY,
    )

    async def broken(_hass):
        raise AttributeError("no dashboards")

    monkeypatch.setattr(lovelace, "async_dashboards_with_card_key", broken)
    await lovelace.async_check_card_keys(hass)
    assert _card_key_issue(hass) is not None


def test_card_api_key_is_found_only_on_our_cards() -> None:
    found = integration.lovelace_resource.has_card_api_key
    assert found({"type": "hungarian-transit-stop-card-plan", "apiKey": "k"})
    assert found([{"type": "conditional", "card": {"type": "custom:bkk-stop-card-r3", "apiKey": "k"}}])
    assert not found({"type": "custom:hungarian-transport-card", "apiKey": ""})
    assert not found({"type": "custom:hungarian-transport-card", "apiKey": 5})
    assert not found({"type": "custom:other", "apiKey": "k", "cards": [{"type": "custom:hungarian-transport-card"}]})


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
        ({"origin_code": ORIGIN, "dest_code": DEST, "minutes_after": float("inf")}, vol.Invalid),
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
