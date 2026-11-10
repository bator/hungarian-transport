"""The timetable sensors (sensor.py, timetables.py)."""

import gzip
import json
import os
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_FRIENDLY_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.hungarian_transport import mav, mav_daily, sensor, timetables
from custom_components.hungarian_transport.const import CONF_DAILY_MAV, DOMAIN

from .test_mav import _feed_zip
from .test_mav_daily import NEW, OLD, _index, _manifest, _serve

# The real round, before conftest's no_daily_mav_index replaces it.
_real_update = mav_daily.async_update

MAV_ID = "sensor.hungarian_transport_mav_route_index"
VOLAN_ID = "sensor.hungarian_transport_volan_timetable"
CITY_ID = "sensor.hungarian_transport_city_timetables"
DAILY_ID = "sensor.hungarian_transport_daily_mav_check"
VOLAN = "volan:01716.20261102"
CITY = "miskolc:1.3;veszprem:20260831-1"
NOW = "2026-11-02 05:00:00+00:00"
# The Volán index's gzip header time; the city index has none (mtime=0).
VOLAN_BUILT = int(dt_util.parse_datetime("2026-11-01T04:00:00+00:00").timestamp())
CITY_MTIME = int(dt_util.parse_datetime("2026-10-31T03:00:00+00:00").timestamp())
# When HACS unpacked the release's MÁV index.
MAV_MTIME = int(dt_util.parse_datetime("2026-10-28T05:00:00+00:00").timestamp())


@pytest.fixture(autouse=True)
def small_feeds(monkeypatch):
    monkeypatch.setattr(mav, "MIN_TRAINS", 1)


@pytest.fixture(autouse=True)
def now(freezer):
    freezer.move_to(NOW)


def _volan(feed: str = VOLAN, trips: int = 2) -> bytes:
    index = {
        "v": 27,
        "sh": {"dir": "volan-shapes-0123456789", "n": 16},
        "feed": feed,
        "s": [["hkir_1", "Aba, Bacsó tanya", "1"], ["hkir_2", "Aba [2,\"x\"]", "2"], ["hkir_3", "Abda", "3"]],
        "g": [4700000, 1800000, 4700100, 1800100, 4700200, 1800200],
        "c": [["1111100", "20261102", "20261231"]],
        "a": [["20261224"]],
        "r": [[]],
        "t": [[0, f"38{i:02d}", [0, 1, 2], [360, 370, 380], [0], 3] for i in range(trips)],
    }
    raw = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, mtime=VOLAN_BUILT)


def _city(feed: str = CITY) -> bytes:
    ops = [part.split(":", 1)[0] for part in feed.split(";")]
    index = {
        "v": 27,
        "feed": feed,
        "s": [[f"{op}:{n}", f"Stop {n}", str(n), 0] for op in ops for n in range(2)],
        "c": [["0000000", "20200101", "20991231"]],
        "a": [["20261102"]],
        "r": [[]],
        "t": [[0, "14", [0, 1], [958, 960]], [0, "1AV", [1, 0], [970, 972]], [0, "2", [2, 3], [980, 982]]],
        "o": [[op, op.title()] for op in ops],
    }
    raw = json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, mtime=0)


def _ship(www: Path, mav_raw: bytes | None = None) -> None:
    (www / timetables.VOLAN_INDEX).write_bytes(_volan())
    city = www / timetables.CITY_INDEX
    city.write_bytes(_city())
    os.utime(city, (CITY_MTIME, CITY_MTIME))
    out = www / mav.INDEX_NAME
    out.write_bytes(mav_raw if mav_raw is not None else _index(OLD, fitted=True))
    os.utime(out, (MAV_MTIME, MAV_MTIME))


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    # Local time is UTC: the next daily round reads as 06:00 UTC.
    await hass.config.async_set_time_zone("UTC")
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _later(hass: HomeAssistant, delta: timedelta) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + delta)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _first_round(hass: HomeAssistant) -> None:
    await _later(hass, timedelta(seconds=mav_daily.FIRST_DELAY + mav_daily.FIRST_JITTER + 1))


async def test_entities_ids_names_device_and_states(hass: HomeAssistant, card_assets) -> None:
    _ship(card_assets)
    entry = await _setup(hass)

    registry = er.async_get(hass)
    ids = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
    assert ids == {MAV_ID, VOLAN_ID, CITY_ID, DAILY_ID}
    for entity_id, key, name, device_class in (
        (MAV_ID, "mav_route_index", "MÁV route index", SensorDeviceClass.DATE),
        (VOLAN_ID, "volan_timetable", "Volán timetable", SensorDeviceClass.DATE),
        (CITY_ID, "city_timetables", "City timetables", SensorDeviceClass.TIMESTAMP),
        (DAILY_ID, "daily_mav_check", "Daily MÁV check", SensorDeviceClass.TIMESTAMP),
    ):
        state = hass.states.get(entity_id)
        assert state.attributes[ATTR_FRIENDLY_NAME] == f"Hungarian transport {name}"
        assert state.attributes[ATTR_DEVICE_CLASS] == device_class
        assert registry.async_get(entity_id).unique_id == f"{entry.entry_id}_{key}"

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].name == "Hungarian transport"
    assert devices[0].entry_type is dr.DeviceEntryType.SERVICE

    state = hass.states.get(MAV_ID)
    assert state.state == "2026-10-25"
    assert {k: v for k, v in state.attributes.items() if k not in (ATTR_FRIENDLY_NAME, ATTR_DEVICE_CLASS, "icon")} == {
        "feed_version": OLD,
        "source": "release",
        "installed_at": "2026-10-28T05:00:00+00:00",
        "trains": 1,
        "lines": 0,
        "fitted_share": 1.0,
    }

    state = hass.states.get(VOLAN_ID)
    assert state.state == "2026-11-02"
    assert state.attributes["feed_version"] == VOLAN
    assert state.attributes["trips"] == 2
    assert state.attributes["stops"] == 3
    assert state.attributes["updated_at"] == "2026-11-01T04:00:00+00:00"
    assert state.attributes["source"] == "release"

    state = hass.states.get(CITY_ID)
    assert state.state == "2026-10-31T03:00:00+00:00"
    assert state.attributes["operators"] == 2
    assert state.attributes["trips"] == 3
    assert state.attributes["feeds"] == {"miskolc": "1.3", "veszprem": "20260831-1"}

    # Before the first round of the daily index: unknown, the round pending.
    state = hass.states.get(DAILY_ID)
    assert state.state == STATE_UNKNOWN
    assert "result" not in state.attributes
    assert state.attributes["failure_streak"] == 0
    assert dt_util.parse_datetime(state.attributes["next_run"]) > dt_util.utcnow()


async def test_missing_indexes_are_unavailable(hass: HomeAssistant) -> None:
    await _setup(hass)
    for entity_id in (MAV_ID, VOLAN_ID, CITY_ID):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert hass.states.get(DAILY_ID).state == STATE_UNKNOWN


async def test_daily_install_refreshes_both_sensors(
    hass: HomeAssistant, card_assets, aioclient_mock, monkeypatch
) -> None:
    monkeypatch.setattr(mav_daily, "async_update", _real_update)
    _ship(card_assets, _index(OLD))
    raw = _index(NEW, fitted=True)
    _serve(aioclient_mock, raw, _manifest(raw, fitted_share=0.98))
    await _setup(hass)
    assert hass.states.get(MAV_ID).state == "2026-10-25"

    await _first_round(hass)

    state = hass.states.get(MAV_ID)
    assert state.state == "2026-11-01"
    assert state.attributes["feed_version"] == NEW
    assert state.attributes["source"] == "daily"
    assert state.attributes["installed_at"] == dt_util.utcnow().isoformat()
    assert state.attributes["audit_ok_ratio"] == 1.0
    assert state.attributes["fitted_share"] == 1.0

    state = hass.states.get(DAILY_ID)
    assert dt_util.parse_datetime(state.state) == dt_util.utcnow()
    assert state.attributes["result"] == mav_daily.INSTALLED
    assert state.attributes["manifest_feed"] == NEW
    assert state.attributes["manifest_built_at"] == "2026-11-01T04:52:10+00:00"
    assert state.attributes["failure_streak"] == 0
    # The next daily round, 06:00 local time (UTC in the test config).
    assert state.attributes["next_run"] == "2026-11-02T06:00:00+00:00"

    # A restart keeps the record: the daily index is still the one served.
    assert await hass.config_entries.async_reload(hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(MAV_ID).attributes["source"] == "daily"
    assert hass.states.get(DAILY_ID).attributes["result"] == mav_daily.INSTALLED


@pytest.mark.parametrize(
    ("served", "manifest_status", "result", "streak"),
    [
        (_index(NEW, fitted=True), 200, mav_daily.UP_TO_DATE, 0),
        # www/ serves a newer feed than the manifest's: kept.
        (_index("20261105.M2ChangeID", fitted=True), 200, mav_daily.SUPERSEDED, 0),
        (_index(OLD), 404, mav_daily.FAILED, 1),
    ],
)
async def test_daily_check_results(
    hass: HomeAssistant, card_assets, aioclient_mock, monkeypatch, served, manifest_status, result, streak
) -> None:
    monkeypatch.setattr(mav_daily, "async_update", _real_update)
    _ship(card_assets, served)
    raw = _index(NEW, fitted=True)
    if manifest_status == 200:
        _serve(aioclient_mock, raw, _manifest(raw, fitted_share=0.98))
    else:
        aioclient_mock.get(mav_daily.MANIFEST_URL, status=manifest_status)
    await _setup(hass)
    await _first_round(hass)

    state = hass.states.get(DAILY_ID)
    assert dt_util.parse_datetime(state.state) == dt_util.utcnow()
    assert state.attributes["result"] == result
    assert state.attributes["failure_streak"] == streak
    if result == mav_daily.FAILED:
        assert "manifest_feed" not in state.attributes
        # Retried RETRY_DELAY later.
        assert dt_util.parse_datetime(state.attributes["next_run"]) == dt_util.utcnow() + timedelta(
            seconds=mav_daily.RETRY_DELAY
        )
    else:
        assert state.attributes["manifest_feed"] == NEW
    # Nothing installed: the served index is still the release's.
    assert hass.states.get(MAV_ID).attributes["source"] == "release"


async def test_daily_check_disabled(hass: HomeAssistant, card_assets) -> None:
    _ship(card_assets)
    await _setup(hass, {CONF_DAILY_MAV: False})
    await _first_round(hass)
    state = hass.states.get(DAILY_ID)
    assert state.state == STATE_UNKNOWN
    assert state.attributes["result"] == "disabled"
    assert "next_run" not in state.attributes


async def test_file_change_is_read_on_the_poll_and_only_then(
    hass: HomeAssistant, card_assets, monkeypatch
) -> None:
    _ship(card_assets)
    reads: list[str] = []
    scan = timetables.scan_index

    def counting_scan(path, **kw):
        reads.append(path.name)
        return scan(path, **kw)

    monkeypatch.setattr(timetables, "scan_index", counting_scan)
    await _setup(hass)
    assert sorted(reads) == [timetables.CITY_INDEX, timetables.VOLAN_INDEX]

    # Nothing changed: the poll reads nothing again.
    await _later(hass, timetables.POLL_INTERVAL)
    assert len(reads) == 2

    # The local updater replaced the Volán index: read at the next poll.
    (card_assets / timetables.VOLAN_INDEX).write_bytes(_volan("volan:01720.20261109", trips=5))
    await _later(hass, timetables.POLL_INTERVAL * 2)
    assert reads[2:] == [timetables.VOLAN_INDEX]
    state = hass.states.get(VOLAN_ID)
    assert state.state == "2026-11-09"
    assert state.attributes["trips"] == 5


async def test_indexes_are_read_in_the_executor(hass: HomeAssistant, card_assets, monkeypatch) -> None:
    _ship(card_assets)
    threads: list[bool] = []
    read_mav, scan = timetables.read_mav, timetables.scan_index

    def on_loop() -> None:
        threads.append(threading.current_thread() is threading.main_thread())

    monkeypatch.setattr(timetables, "read_mav", lambda p: (on_loop(), read_mav(p))[1])
    monkeypatch.setattr(timetables, "scan_index", lambda p, **kw: (on_loop(), scan(p, **kw))[1])
    await _setup(hass)
    assert threads == [False, False, False]


async def test_local_build_is_source_local(hass: HomeAssistant, card_assets, mav_source) -> None:
    """mav.py's startup build records its index and signals the sensors."""
    _ship(card_assets)
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(_feed_zip("20261103.Local"))
    await _setup(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get(MAV_ID)
    assert state.state == "2026-11-03"
    assert state.attributes["source"] == "local"
    assert state.attributes["installed_at"] == dt_util.utcnow().isoformat()
    assert state.attributes["trains"] == 2
    assert state.attributes["audit_ok_ratio"] == 1.0
    # One of the three variants follows the line through its stations.
    assert state.attributes["fitted_share"] == 0.6667


async def test_updater_copies_make_the_source_local(hass: HomeAssistant, card_assets, mav_source) -> None:
    """The updater script keeps a durable copy of what it built."""
    _ship(card_assets)
    durable = mav_source.parent
    durable.mkdir(parents=True, exist_ok=True)
    (durable / timetables.VOLAN_INDEX).write_bytes((card_assets / timetables.VOLAN_INDEX).read_bytes())
    (durable / mav.INDEX_NAME).write_bytes((card_assets / mav.INDEX_NAME).read_bytes())
    await _setup(hass)
    assert hass.states.get(VOLAN_ID).attributes["source"] == "local_updater"
    state = hass.states.get(MAV_ID)
    assert state.attributes["source"] == "local"
    assert state.attributes["installed_at"] == "2026-10-28T05:00:00+00:00"


@pytest.mark.parametrize("chunk", [1, 5, 64, timetables.CHUNK])
def test_scan_matches_a_full_read(tmp_path: Path, monkeypatch, chunk) -> None:
    """Counted on the stream, across any chunk boundary, as a full parse."""
    monkeypatch.setattr(timetables, "CHUNK", chunk)
    for name, raw, ops in (("volan.json.gz", _volan(trips=7), False), ("city.json.gz", _city(), True)):
        path = tmp_path / name
        path.write_bytes(raw)
        full = json.loads(gzip.decompress(raw))
        info = timetables.scan_index(path, operators=ops)
        assert info["feed"] == full["feed"]
        assert info["trips"] == len(full["t"])
        assert info["stops"] == len(full["s"])
        if ops:
            assert info["operators"] == len(full["o"])


def test_scan_of_a_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.json.gz"
    path.write_bytes(_volan()[:200])
    assert timetables.scan_index(path) is None
    path.write_bytes(b"not gzip")
    assert timetables.scan_index(path) is None


@pytest.mark.parametrize("lang", ["en", "hu"])
def test_every_sensor_has_a_name_and_an_icon(lang) -> None:
    pkg = Path(sensor.__file__).parent
    strings = json.loads((pkg / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
    icons = json.loads((pkg / "icons.json").read_text(encoding="utf-8"))
    keys = {d.translation_key for d in sensor.SENSORS}
    assert set(strings["entity"]["sensor"]) == keys
    assert set(icons["entity"]["sensor"]) == keys
    assert all(strings["entity"]["sensor"][k]["name"] for k in keys)
