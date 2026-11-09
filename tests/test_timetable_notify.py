"""Notifications about timetable updates (timetable_notify.py)."""

import gzip
import json

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components import persistent_notification
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.hungarian_transport import mav, mav_daily, storage, timetable_notify
from custom_components.hungarian_transport.const import (
    CONF_DAILY_MAV,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_UPDATES,
    DOMAIN,
)

from .test_mav_daily import NEW, OLD, _at, _index, _manifest, _serve

# The real round, before the fixtures below replace it.
_real_update = mav_daily.async_update

ON = {CONF_NOTIFY_UPDATES: True}
VOLAN = "volan:01662.20260921"
CITY = "miskolc:1.3;veszprem:20260831-1"


@pytest.fixture
def no_daily_mav_index():
    """Overrides the conftest fixture: some tests run the real updater."""
    yield


@pytest.fixture(autouse=True)
def small_feeds(monkeypatch):
    monkeypatch.setattr(mav, "MIN_TRAINS", 1)


@pytest.fixture(autouse=True)
def no_daily_by_default(monkeypatch):
    """No request to GitHub Pages unless a test serves one."""

    async def _up_to_date(hass, www):
        return mav_daily.UP_TO_DATE

    monkeypatch.setattr(mav_daily, "async_update", _up_to_date)


@pytest.fixture
def notes(monkeypatch):
    """Every persistent notification created or dismissed."""
    created: list[dict] = []
    dismissed: list[str] = []

    def create(hass, message, title=None, notification_id=None):
        created.append({"message": message, "title": title, "id": notification_id})

    def dismiss(hass, notification_id):
        dismissed.append(notification_id)

    monkeypatch.setattr(persistent_notification, "async_create", create)
    monkeypatch.setattr(persistent_notification, "async_dismiss", dismiss)
    return created, dismissed


@pytest.fixture
def script_calls(hass: HomeAssistant) -> list[dict]:
    calls: list[dict] = []

    async def handle(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("script", "ertesit", handle)
    return calls


@pytest.fixture
def budapest(hass: HomeAssistant, freezer):
    freezer.move_to("2026-11-01 03:00:00+01:00")
    return hass.config.async_set_time_zone("Europe/Budapest")


def _write_index(path, feed: str) -> None:
    raw = json.dumps({"v": 27, "feed": feed, "s": [], "t": []}, separators=(",", ":")).encode()
    path.write_bytes(gzip.compress(raw, mtime=0))


def _ship(www, volan: str = VOLAN, city: str = CITY, mav_feed: str | None = OLD) -> None:
    _write_index(www / "volan-index.json.gz", volan)
    _write_index(www / "city-index.json.gz", city)
    if mav_feed is not None:
        (www / mav.INDEX_NAME).write_bytes(_index(mav_feed))


def _seen(hass_storage, version: str, feeds: dict) -> None:
    hass_storage[timetable_notify.STORAGE_KEY] = {
        "version": timetable_notify.STORAGE_VERSION,
        "minor_version": 1,
        "key": timetable_notify.STORAGE_KEY,
        "data": {"shipped": {"version": version, "feeds": feeds}},
    }


async def _setup(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _set_state(hass: HomeAssistant, state: CoreState) -> None:
    """Home Assistant's core state (set_state where the harness has it)."""
    if hasattr(hass, "set_state"):
        hass.set_state(state)
    else:
        hass.state = state


def _ids(created: list[dict]) -> list[str]:
    return [n["id"] for n in created]


# ---- the daily MÁV index installed -------------------------------------------


@pytest.mark.parametrize("daily", [True, False])
async def test_installed_daily_index_notifies_with_the_extra_service(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, notes, script_calls, monkeypatch, daily
) -> None:
    """MÁV publishes almost daily: the service hears about a daily install
    only with CONF_NOTIFY_DAILY on; the persistent notification always."""
    await budapest
    monkeypatch.setattr(mav_daily, "async_update", _real_update)
    _ship(card_assets)
    raw = _index(NEW)
    _serve(aioclient_mock, raw, _manifest(raw, trains=812, lines=97))
    await _setup(hass, {**ON, CONF_NOTIFY_SERVICE: "script.ertesit", CONF_NOTIFY_DAILY: daily})
    created, _ = notes
    assert created == []

    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert (card_assets / mav.INDEX_NAME).read_bytes() == raw
    assert created == [{
        "id": timetable_notify.NOTIFICATION_MAV_DAILY,
        "title": "Hungarian transport: new MÁV timetable",
        "message": "The daily MÁV route index of the 2026-11-01 timetable is installed: 812 trains, 97 lines.",
    }]
    assert script_calls == ([{"title": created[0]["title"], "message": created[0]["message"]}] if daily else [])


async def test_installed_daily_index_in_hungarian(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, notes, monkeypatch
) -> None:
    await budapest
    hass.config.language = "hu"
    monkeypatch.setattr(mav_daily, "async_update", _real_update)
    raw = _index(NEW)
    _serve(aioclient_mock, raw, _manifest(raw, trains=812, lines=97))
    await _setup(hass, ON)
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    created, _ = notes
    assert [(n["id"], n["title"], n["message"]) for n in created] == [(
        timetable_notify.NOTIFICATION_MAV_DAILY,
        "Hungarian transport: új MÁV menetrend",
        "Telepítve a napi MÁV útvonal-index (2026-11-01 menetrend): 812 vonat, 97 vonal.",
    )]


async def test_nothing_notifies_when_turned_off(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, notes, script_calls, monkeypatch,
    hass_storage,
) -> None:
    await budapest
    monkeypatch.setattr(mav_daily, "async_update", _real_update)
    _seen(hass_storage, "0.0.1", {"volan": "volan:old", "city": CITY, "mav": OLD})
    _ship(card_assets)
    raw = _index(NEW)
    _serve(aioclient_mock, raw)
    await _setup(hass, {CONF_NOTIFY_UPDATES: False, CONF_NOTIFY_SERVICE: "script.ertesit"})
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert (card_assets / mav.INDEX_NAME).read_bytes() == raw
    assert notes == ([], [])
    assert script_calls == []
    # Off, the shipped feeds are recorded all the same.
    assert hass_storage[timetable_notify.STORAGE_KEY]["data"]["shipped"]["feeds"]["volan"] == VOLAN


async def test_up_to_date_round_does_not_notify(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, notes, monkeypatch
) -> None:
    await budapest
    monkeypatch.setattr(mav_daily, "async_update", _real_update)
    (card_assets / mav.INDEX_NAME).write_bytes(_index(NEW, fitted=True))
    _serve(aioclient_mock, _index(NEW, fitted=True))
    await _setup(hass, ON)
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert aioclient_mock.call_count == 1
    assert notes == ([], [])


# ---- the extra service --------------------------------------------------------


async def test_missing_service_still_creates_the_persistent_notification(
    hass: HomeAssistant, card_assets, notes, caplog
) -> None:
    notifier = timetable_notify.UpdateNotifier(hass, {**ON, CONF_NOTIFY_SERVICE: "notify.gone"})
    await notifier.async_notify("x_id", "Title", "Message")
    await hass.async_block_till_done(wait_background_tasks=True)
    created, _ = notes
    assert _ids(created) == ["x_id"]
    assert "Notification service notify.gone is not available" in caplog.text


async def test_failing_service_is_logged_not_raised(hass: HomeAssistant, notes, caplog) -> None:
    async def broken(call: ServiceCall) -> None:
        raise RuntimeError("push gateway down")

    hass.services.async_register("notify", "mobile_app_iphone", broken)
    notifier = timetable_notify.UpdateNotifier(hass, {**ON, CONF_NOTIFY_SERVICE: "notify.mobile_app_iphone"})
    await notifier.async_notify("x_id", "Title", "Message")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _ids(notes[0]) == ["x_id"]
    assert "Notification service notify.mobile_app_iphone failed: push gateway down" in caplog.text


async def test_notify_service_gets_title_and_message(hass: HomeAssistant, notes) -> None:
    calls = []

    async def handle(call: ServiceCall) -> None:
        calls.append((call.domain, call.service, dict(call.data)))

    hass.services.async_register("notify", "mobile_app_iphone", handle)
    notifier = timetable_notify.UpdateNotifier(hass, {**ON, CONF_NOTIFY_SERVICE: "notify.mobile_app_iphone"})
    await notifier.async_notify("x_id", "Title", "Message")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [("notify", "mobile_app_iphone", {"title": "Title", "message": "Message"})]


# ---- the shipped indexes -----------------------------------------------------


async def test_first_run_only_records(hass: HomeAssistant, card_assets, notes, hass_storage) -> None:
    _ship(card_assets)
    await _setup(hass, ON)
    assert notes == ([], [])
    shipped = hass_storage[timetable_notify.STORAGE_KEY]["data"]["shipped"]
    assert shipped["feeds"] == {"volan": VOLAN, "city": CITY, "mav": OLD}
    assert shipped["version"]


async def test_changed_shipped_indexes_notify_once(
    hass: HomeAssistant, card_assets, notes, hass_storage, script_calls
) -> None:
    _seen(hass_storage, "0.0.1", {"volan": "volan:01662.20260914", "city": CITY, "mav": "20261020.M2ChangeID"})
    _ship(card_assets)
    entry = await _setup(hass, {**ON, CONF_NOTIFY_SERVICE: "script.ertesit"})
    created, _ = notes
    assert created == [{
        "id": timetable_notify.NOTIFICATION_SHIPPED,
        "title": "Hungarian transport: timetables updated",
        "message": "This version of Hungarian transport ships new timetables: "
                   "Volán buses, MÁV trains (2026-10-25 timetable).",
    }]
    assert script_calls == [{"title": created[0]["title"], "message": created[0]["message"]}]

    # The same version again (a reload, a restart): recorded, no news.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(created) == 1
    assert hass_storage[timetable_notify.STORAGE_KEY]["data"]["shipped"]["feeds"]["volan"] == VOLAN


async def test_changes_within_a_version_do_not_notify(hass: HomeAssistant, card_assets, notes) -> None:
    """The daily index or a local build replaced the MÁV index: not shipped."""
    _ship(card_assets)
    entry = await _setup(hass, ON)
    (card_assets / mav.INDEX_NAME).write_bytes(_index(NEW))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert notes == ([], [])


async def test_a_shipped_mav_index_older_than_the_one_seen_is_no_news(
    hass: HomeAssistant, card_assets, notes, hass_storage
) -> None:
    _seen(hass_storage, "0.0.1", {"volan": VOLAN, "city": "miskolc:1.2", "mav": NEW})
    _ship(card_assets, mav_feed=OLD)
    hass.config.language = "hu-HU"
    await _setup(hass, ON)
    created, _ = notes
    assert [(n["id"], n["message"]) for n in created] == [(
        timetable_notify.NOTIFICATION_SHIPPED,
        "A Hungarian transport új verziója friss menetrendeket hozott: városi járatok.",
    )]


async def test_a_shipped_mav_index_older_than_the_daily_one_installed_is_no_news(
    hass: HomeAssistant, card_assets, notes, hass_storage
) -> None:
    """The daily index installed since the last setup was seen too: a release
    whose index is older replaces it, which is not news."""
    _seen(hass_storage, "0.0.1", {"volan": VOLAN, "city": CITY, "mav": "20261020.M2ChangeID"})
    hass_storage[timetable_notify.STORAGE_KEY]["data"][storage.MAV_INSTALL] = {
        "source": storage.SOURCE_DAILY, "feed": NEW, "size": 1, "installed_at": "2026-11-01T05:00:00+00:00",
    }
    _ship(card_assets, mav_feed=OLD)
    await _setup(hass, ON)
    assert notes == ([], [])


def test_a_shipped_mav_index_newer_than_the_installed_one_is_news() -> None:
    seen = {"version": "0.0.1", "feeds": {"mav": "20261020.M2ChangeID"}}
    assert timetable_notify.changed_indexes(seen, "0.0.2", {"mav": NEW}, installed_mav=OLD) == ["mav"]
    assert timetable_notify.changed_indexes(seen, "0.0.2", {"mav": OLD}, installed_mav=OLD) == []
    assert timetable_notify.changed_indexes(seen, "0.0.2", {"mav": OLD}, installed_mav=None) == ["mav"]


async def test_a_new_version_with_the_same_feeds_is_no_news(hass: HomeAssistant, card_assets, notes, hass_storage):
    _seen(hass_storage, "0.0.1", {"volan": VOLAN, "city": CITY, "mav": OLD})
    _ship(card_assets)
    await _setup(hass, ON)
    assert notes == ([], [])


async def test_an_unreadable_record_starts_over(hass: HomeAssistant, card_assets, notes, hass_storage) -> None:
    hass_storage[timetable_notify.STORAGE_KEY] = {
        "version": timetable_notify.STORAGE_VERSION, "minor_version": 1,
        "key": timetable_notify.STORAGE_KEY, "data": ["not", "a", "record"],
    }
    _ship(card_assets)
    entry = await _setup(hass, ON)
    assert entry.state.name == "LOADED"
    assert notes == ([], [])


def test_the_city_feed_is_read_beyond_the_build_check_head(tmp_path) -> None:
    long_city = ";".join(f"feed{k}:{k:05d}.20261101" for k in range(400))
    assert len(long_city) > 4096
    _write_index(tmp_path / "city-index.json.gz", long_city)
    assert timetable_notify.shipped_feeds(tmp_path)["city"] == long_city


# ---- the daily index failing --------------------------------------------------


async def test_repeated_daily_failure_notifies_once_and_clears(
    hass: HomeAssistant, card_assets, freezer, budapest, notes, monkeypatch, hass_storage
) -> None:
    await budapest
    # Every round of a day (retries too) has that day's result.
    plan = {"01": mav_daily.FAILED, "02": mav_daily.FAILED, "03": mav_daily.BUSY, "04": mav_daily.FAILED,
            "05": mav_daily.FAILED, "06": mav_daily.UP_TO_DATE}
    rounds = []

    async def update(hass, www):
        hass.data[DOMAIN]["mav_daily_error"] = "mav-index.json: HTTP 404"
        rounds.append(dt_util.now().date().isoformat())
        return plan[f"{dt_util.now().day:02d}"]

    monkeypatch.setattr(mav_daily, "async_update", update)
    await _setup(hass, ON)
    created, dismissed = notes

    # Rounds and retries on day 1 count as one day; day 3's rounds were kept
    # busy by a local build, which neither counts nor breaks the streak.
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    await _at(hass, freezer, "2026-11-01 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-01 06:05:01+01:00")
    await _at(hass, freezer, "2026-11-01 06:25:02+01:00")
    assert rounds.count("2026-11-01") >= 3
    for day in ("02", "03", "04"):
        assert created == []
        await _at(hass, freezer, f"2026-11-{day} 06:00:00+01:00")
        await _at(hass, freezer, f"2026-11-{day} 06:05:01+01:00")
    assert created == [{
        "id": timetable_notify.NOTIFICATION_DAILY_FAILED,
        "title": "Hungarian transport: daily MÁV index not updating",
        "message": "The daily MÁV route index could not be updated on 3 days in a row "
                   "(last error: mav-index.json: HTTP 404). The current index stays in use.",
    }]
    record = hass_storage[timetable_notify.STORAGE_KEY]["data"]["daily_failures"]
    assert record == {"days": 3, "last": "2026-11-04", "notified": True, "error": "mav-index.json: HTTP 404"}

    # Once per streak, not daily.
    await _at(hass, freezer, "2026-11-05 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-05 06:05:01+01:00")
    assert len(created) == 1

    # A round that goes through again ends the streak and dismisses it.
    await _at(hass, freezer, "2026-11-06 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-06 06:05:01+01:00")
    assert dismissed == [timetable_notify.NOTIFICATION_DAILY_FAILED]
    assert "daily_failures" not in hass_storage[timetable_notify.STORAGE_KEY]["data"]
    assert len(created) == 1


async def test_failure_streak_counts_days_not_rounds(hass: HomeAssistant, freezer, budapest, notes) -> None:
    await budapest
    notifier = timetable_notify.UpdateNotifier(hass, ON)
    for _ in range(5):
        await notifier.async_daily_result(mav_daily.FAILED)
    freezer.move_to("2026-11-02 06:00:00+01:00")
    await notifier.async_daily_result(mav_daily.FAILED)
    assert notes == ([], [])
    freezer.move_to("2026-11-03 06:00:00+01:00")
    await notifier.async_daily_result(mav_daily.FAILED)
    assert _ids(notes[0]) == [timetable_notify.NOTIFICATION_DAILY_FAILED]
    freezer.move_to("2026-11-04 06:00:00+01:00")
    await notifier.async_daily_result(mav_daily.FAILED)
    assert len(notes[0]) == 1


async def test_an_unsupported_manifest_is_not_a_failure(hass: HomeAssistant, freezer, budapest, notes) -> None:
    await budapest
    notifier = timetable_notify.UpdateNotifier(hass, ON)
    for day in ("01", "02", "03", "04"):
        freezer.move_to(f"2026-11-{day} 06:00:00+01:00")
        await notifier.async_daily_result(mav_daily.UNSUPPORTED)
    assert notes == ([], [])


async def test_daily_mav_off_still_checks_the_shipped_indexes(
    hass: HomeAssistant, card_assets, notes, hass_storage
) -> None:
    _seen(hass_storage, "0.0.1", {"volan": "volan:old", "city": CITY, "mav": OLD})
    _ship(card_assets)
    await _setup(hass, {**ON, CONF_DAILY_MAV: False})
    assert _ids(notes[0]) == [timetable_notify.NOTIFICATION_SHIPPED]



async def test_failure_days_must_be_consecutive(hass: HomeAssistant, freezer, budapest, notes) -> None:
    """Three failures two weeks apart (Home Assistant off between) are no
    streak: a day without any round starts it over."""
    await budapest
    notifier = timetable_notify.UpdateNotifier(hass, ON)
    for day in ("2026-11-01", "2026-11-08", "2026-11-15"):
        freezer.move_to(f"{day} 06:00:00+01:00")
        await notifier.async_daily_result(mav_daily.FAILED)
    assert notes == ([], [])
    for day in ("2026-11-16", "2026-11-17"):
        freezer.move_to(f"{day} 06:00:00+01:00")
        await notifier.async_daily_result(mav_daily.FAILED)
    assert _ids(notes[0]) == [timetable_notify.NOTIFICATION_DAILY_FAILED]
    assert "on 3 days in a row" in notes[0][0]["message"]


async def test_busy_days_bridge_a_streak(hass: HomeAssistant, freezer, budapest, notes) -> None:
    await budapest
    notifier = timetable_notify.UpdateNotifier(hass, ON)
    for day, result in (("01", mav_daily.FAILED), ("02", mav_daily.BUSY), ("03", mav_daily.BUSY),
                        ("04", mav_daily.FAILED), ("05", mav_daily.FAILED)):
        freezer.move_to(f"2026-11-{day} 06:00:00+01:00")
        await notifier.async_daily_result(result)
    assert _ids(notes[0]) == [timetable_notify.NOTIFICATION_DAILY_FAILED]


async def test_a_stale_site_counts_as_a_failure(hass: HomeAssistant, freezer, budapest, notes) -> None:
    """The daily workflow stopped: every round is UP_TO_DATE against a
    manifest built days ago. Once it is older than STALE_PAGES, that is a
    failure day, so the streak notification shows it."""
    await budapest
    notifier = timetable_notify.UpdateNotifier(hass, ON)
    built = dt_util.parse_datetime("2026-10-28T04:00:00Z")
    hass.data.setdefault(DOMAIN, {})["mav_daily_built_at"] = built
    for day in ("2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"):
        freezer.move_to(f"{day} 06:00:00+01:00")
        await notifier.async_daily_result(mav_daily.UP_TO_DATE)
    # 30.10 is within STALE_PAGES; 31.10, 01.11, 02.11 are not.
    assert _ids(notes[0]) == [timetable_notify.NOTIFICATION_DAILY_FAILED]
    assert "last built on 2026-10-28" in notes[0][0]["message"]
    # A fresh manifest ends the streak.
    hass.data[DOMAIN]["mav_daily_built_at"] = dt_util.utcnow()
    freezer.move_to("2026-11-03 06:00:00+01:00")
    await notifier.async_daily_result(mav_daily.UP_TO_DATE)
    assert notes[1] == [timetable_notify.NOTIFICATION_DAILY_FAILED]


async def test_the_daily_round_records_when_the_manifest_was_built(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    raw = _index(NEW)
    _serve(aioclient_mock, raw, _manifest(raw, built_at="2026-10-28T04:00:00Z"))
    await _real_update(hass, card_assets)
    assert hass.data[DOMAIN]["mav_daily_built_at"] == dt_util.parse_datetime("2026-10-28T04:00:00Z")
    aioclient_mock.clear_requests()
    aioclient_mock.get(mav_daily.MANIFEST_URL, status=503)
    await _real_update(hass, card_assets)
    assert "mav_daily_built_at" not in hass.data[DOMAIN]


async def test_an_open_streak_notification_comes_back_after_a_restart(
    hass: HomeAssistant, card_assets, notes, hass_storage, script_calls
) -> None:
    """Persistent notifications do not outlive a restart; the record says it
    was shown, so setup shows it again (without calling the service)."""
    hass_storage[timetable_notify.STORAGE_KEY] = {
        "version": timetable_notify.STORAGE_VERSION, "minor_version": 1, "key": timetable_notify.STORAGE_KEY,
        "data": {"daily_failures": {"days": 4, "last": "2026-11-04", "notified": True, "error": "HTTP 404"}},
    }
    await _setup(hass, {**ON, CONF_NOTIFY_SERVICE: "script.ertesit"})
    created, _ = notes
    assert created == [{
        "id": timetable_notify.NOTIFICATION_DAILY_FAILED,
        "title": "Hungarian transport: daily MÁV index not updating",
        "message": "The daily MÁV route index could not be updated on 4 days in a row "
                   "(last error: HTTP 404). The current index stays in use.",
    }]
    assert script_calls == []


async def test_turning_the_daily_index_off_ends_an_open_streak(
    hass: HomeAssistant, card_assets, notes, hass_storage
) -> None:
    """No round can close the streak with the daily index off: setup ends it
    (and its notice) instead of showing the notice again at every setup."""
    hass_storage[timetable_notify.STORAGE_KEY] = {
        "version": timetable_notify.STORAGE_VERSION, "minor_version": 1, "key": timetable_notify.STORAGE_KEY,
        "data": {"daily_failures": {"days": 4, "last": "2026-11-04", "notified": True, "error": "HTTP 404"}},
    }
    entry = await _setup(hass, {**ON, CONF_DAILY_MAV: False})
    created, dismissed = notes
    assert created == []
    assert dismissed == [timetable_notify.NOTIFICATION_DAILY_FAILED]
    assert "daily_failures" not in hass_storage[timetable_notify.STORAGE_KEY]["data"]

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert created == []
    assert dismissed == [timetable_notify.NOTIFICATION_DAILY_FAILED]


async def test_the_shipped_notification_calls_the_service_once_started(
    hass: HomeAssistant, card_assets, notes, hass_storage, caplog
) -> None:
    """At startup the entry is set up before scripts and mobile_app register
    their services: the persistent notification is created at once, the
    service called at EVENT_HOMEASSISTANT_STARTED."""
    _seen(hass_storage, "0.0.1", {"volan": "volan:old", "city": CITY, "mav": OLD})
    _ship(card_assets)
    _set_state(hass, CoreState.starting)
    await _setup(hass, {**ON, CONF_NOTIFY_SERVICE: "script.ertesit"})
    created, _ = notes
    assert _ids(created) == [timetable_notify.NOTIFICATION_SHIPPED]
    calls = []

    async def handle(call: ServiceCall) -> None:
        calls.append(dict(call.data))

    hass.services.async_register("script", "ertesit", handle)  # the script integration, later
    assert calls == []
    _set_state(hass, CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [{"title": created[0]["title"], "message": created[0]["message"]}]
    assert "Notification service script.ertesit is not available" not in caplog.text


async def test_a_waiting_service_call_is_dropped_when_the_entry_unloads(
    hass: HomeAssistant, card_assets, notes, hass_storage, script_calls
) -> None:
    _seen(hass_storage, "0.0.1", {"volan": "volan:old", "city": CITY, "mav": OLD})
    _ship(card_assets)
    _set_state(hass, CoreState.starting)
    entry = await _setup(hass, {**ON, CONF_NOTIFY_SERVICE: "script.ertesit"})
    assert await hass.config_entries.async_unload(entry.entry_id)
    _set_state(hass, CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert script_calls == []
