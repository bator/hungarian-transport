"""The integration's record in .storage (hungarian_transport.updates).

Shared by the update notifications (timetable_notify.py) and the timetable
sensors (sensor.py, timetables.py). Besides what the notifications keep,
it holds:

- MAV_INSTALL: what installed the MÁV route index last, when this
  integration did (the daily index, mav_daily.py; a local build or upload,
  mav.py): its source, feed, gzipped size and time. An index www/ serves
  that is not this one (another feed or size) came with a release, or from
  the local updater script;
- DAILY_ROUND: the last round of the daily MÁV index: when, its result,
  and the manifest's feed and build time;
- TIMETABLES_INSTALL: per index ("volan", "city"), what the weekly
  timetables from GitHub Pages (weekly_timetables.py) installed last: its
  feed, sha256, gzipped size and time. An index www/ serves that is not
  this one (another feed or size) came with a release, or from the local
  updater script;
- PAGES_ROUND: per index, the last round of the weekly timetables that
  settled it: when, its result and the manifest's feed.

Every writer sends SIGNAL_TIMETABLES afterwards; the sensors read the
indexes again (only the ones whose file changed) and the record.

A second record (hungarian_transport.bkk_stations) keeps the child poles
of FUTÁR's combined stations that the BKK boards learnt from FUTÁR
(mavplusz_board.PlannerBoards.stations_data), so the boards of those
stations have a fallback from the planner right after a restart during a
FUTÁR outage. It is written a while after a change (STATIONS_SAVE_DELAY;
Home Assistant writes what is pending when it stops) and read back through
mavplusz_board.restore_stations, which keeps it within its limits.
"""
from __future__ import annotations

from collections.abc import Callable
import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.updates"
STORAGE_VERSION = 1
SIGNAL_TIMETABLES = f"{DOMAIN}_timetables_updated"
MAV_INSTALL = "mav_install"
DAILY_ROUND = "mav_daily_round"
TIMETABLES_INSTALL = "timetables_install"
PAGES_ROUND = "pages_timetables_round"
# MAV_INSTALL sources; an index without a matching record is a release's
# (or the local updater's, timetables.py tells).
SOURCE_DAILY = "daily"
SOURCE_LOCAL = "local"
SOURCE_RELEASE = "release"
# What the Volán and city sensors call an index TIMETABLES_INSTALL describes.
SOURCE_PAGES = "pages"
STATIONS_KEY = f"{DOMAIN}.bkk_stations"
STATIONS_VERSION = 1
# A FUTÁR answer can name a new pole every few seconds while a station is
# first shown: one write for a few minutes of them.
STATIONS_SAVE_DELAY = 300


def store(hass: HomeAssistant) -> Store:
    data = hass.data.setdefault(DOMAIN, {})
    if "update_store" not in data:
        data["update_store"] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    return data["update_store"]


async def async_load(hass: HomeAssistant) -> dict[str, Any]:
    """The stored record, loaded once per Home Assistant run."""
    data = hass.data.setdefault(DOMAIN, {})
    if "update_state" not in data:
        try:
            stored = await store(hass).async_load()
        except Exception as err:  # noqa: BLE001 - a broken file starts over
            _LOGGER.warning("Could not read the timetable update record: %s", err)
            stored = None
        data["update_state"] = stored if isinstance(stored, dict) else {}
    return data["update_state"]


def stations_store(hass: HomeAssistant) -> Store:
    data = hass.data.setdefault(DOMAIN, {})
    if "stations_store" not in data:
        data["stations_store"] = Store(hass, STATIONS_VERSION, STATIONS_KEY)
    return data["stations_store"]


async def async_load_stations(hass: HomeAssistant) -> Any:
    """The BKK stations' record as stored (None for none, or a broken file:
    mavplusz_board.restore_stations checks what it keeps)."""
    try:
        return await stations_store(hass).async_load()
    except Exception as err:  # noqa: BLE001 - a broken file starts over
        _LOGGER.warning("Could not read the learnt BKK stations: %s", err)
        return None


@callback
def async_save_stations_later(hass: HomeAssistant, data: Callable[[], dict[str, Any]]) -> None:
    """Write data() (read at write time) STATIONS_SAVE_DELAY from now, or
    when Home Assistant stops, once for every change meanwhile."""
    stations_store(hass).async_delay_save(data, STATIONS_SAVE_DELAY)


async def async_save(hass: HomeAssistant, state: dict[str, Any]) -> None:
    await store(hass).async_save(state)


@callback
def async_signal(hass: HomeAssistant) -> None:
    """The indexes or the daily round changed: the sensors refresh."""
    async_dispatcher_send(hass, SIGNAL_TIMETABLES)


async def _async_put(hass: HomeAssistant, key: str, record: dict[str, Any]) -> None:
    """Store record under key and signal; a failure is logged, never raised
    (the index is installed either way)."""
    try:
        state = await async_load(hass)
        state[key] = record
        await async_save(hass, state)
    except Exception as err:  # noqa: BLE001 - only the sensors use it
        _LOGGER.warning("Could not record the timetable status: %s", err)
    async_signal(hass)


async def async_record_mav_install(
    hass: HomeAssistant,
    source: str,
    feed: str | None,
    size: int | None,
    audit_ok_ratio: float | None = None,
) -> None:
    """This integration installed the MÁV index of feed (gzipped size bytes)."""
    record: dict[str, Any] = {
        "source": source,
        "feed": feed,
        "size": size,
        "installed_at": dt_util.utcnow().isoformat(),
    }
    if isinstance(audit_ok_ratio, (int, float)) and not isinstance(audit_ok_ratio, bool):
        record["audit_ok_ratio"] = audit_ok_ratio
    await _async_put(hass, MAV_INSTALL, record)


async def async_record_daily_round(
    hass: HomeAssistant, result: str, manifest_feed: str | None, manifest_built_at: datetime | None
) -> None:
    """A round of the daily MÁV index ended with result."""
    await _async_put(
        hass,
        DAILY_ROUND,
        {
            "at": dt_util.utcnow().isoformat(),
            "result": result,
            "manifest_feed": manifest_feed,
            "manifest_built_at": manifest_built_at.isoformat() if manifest_built_at else None,
        },
    )


async def _async_merge(hass: HomeAssistant, key: str, records: dict[str, dict[str, Any]]) -> None:
    """Merge records (index name: its record) into the mapping under key and
    signal; like _async_put, a failure is logged, never raised."""
    try:
        state = await async_load(hass)
        current = state.get(key)
        state[key] = {**(current if isinstance(current, dict) else {}), **records}
        await async_save(hass, state)
    except Exception as err:  # noqa: BLE001 - only the sensors use it
        _LOGGER.warning("Could not record the timetable status: %s", err)
    async_signal(hass)


async def async_record_timetables_install(
    hass: HomeAssistant, name: str, feed: str, sha256: str, size: int
) -> None:
    """The weekly timetables installed the index name ("volan", "city") of
    feed, sha256 and gzipped size from GitHub Pages."""
    await _async_merge(
        hass,
        TIMETABLES_INSTALL,
        {name: {"feed": feed, "sha256": sha256, "size": size, "installed_at": dt_util.utcnow().isoformat()}},
    )


async def async_record_pages_round(hass: HomeAssistant, results: dict[str, tuple[str, str | None]]) -> None:
    """A round of the weekly timetables settled these indexes: name ->
    (result, the manifest's feed for it)."""
    if not results:
        return
    at = dt_util.utcnow().isoformat()
    await _async_merge(
        hass,
        PAGES_ROUND,
        {name: {"at": at, "result": result, "manifest_feed": feed} for name, (result, feed) in results.items()},
    )
