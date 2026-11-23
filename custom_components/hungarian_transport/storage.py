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
  the manifest's feed and build time, and why it did not install (reason,
  mav_daily.reason_of) when it failed or was unsupported. A look that is
  still settling (no round) leaves it as it is and adds settling, its
  reason, and settling_at, until the next round replaces the record;
- TIMETABLES_INSTALL: per index ("volan", "city"), what the weekly
  timetables from GitHub Pages (weekly_timetables.py) installed last: its
  feed, sha256, gzipped size and time. An index www/ serves that is not
  this one (another feed or size) came with a release, or from the local
  updater script;
- PAGES_ROUND: per index, the last round of the weekly timetables that
  settled it: when, its result, the manifest's feed and, for a result that
  is not an install, why (reason: "release_newer", "served_newer",
  "bad_signature", "unknown_key", "not_found", "mismatch", "too_large" or
  the error's text, at most mav_daily.MAX_REASON long). A round that is still settling
  (a download did not match its manifest for the first time) leaves the
  settled record as it is and adds settling, its reason, until the next
  round that settles replaces the record.

- UNKNOWN_KEY_SEEN: per signed manifest ("mav-index.json",
  "timetables.json"), on how many consecutive days a round found it signed
  by a key this version does not know (mav_daily.check_signature): days,
  and the last such day. A signature that verifies removes it.

Every writer sends SIGNAL_TIMETABLES afterwards; the sensors read the
indexes again (only the ones whose file changed) and the record.

A second record (hungarian_transport.bkk_stations) keeps the child poles
of FUTÁR's combined stations that the BKK boards learnt from FUTÁR
(mavplusz_board.PlannerBoards.stations_data), so the boards of those
stations have a fallback from the planner right after a restart during a
FUTÁR outage. It is written a while after a change (STATIONS_SAVE_DELAY;
Home Assistant writes what is pending when it stops) and at once when the
entry unloads (a reload, a removal: nothing pending outlives the planner
that learnt it), and read back through mavplusz_board.restore_stations,
which keeps it within its limits.
"""
from __future__ import annotations

from collections.abc import Callable
import logging
from datetime import date, datetime, timedelta
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
UNKNOWN_KEY_SEEN = "unknown_key_seen"
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


async def async_save_stations(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Write data now, in place of a delayed write still pending (the entry
    unloading); a failure is logged, never raised (the unload goes on)."""
    try:
        await stations_store(hass).async_save(data)
    except Exception as err:  # noqa: BLE001 - only the fallback's head start
        _LOGGER.warning("Could not write the learnt BKK stations: %s", err)


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
    hass: HomeAssistant,
    result: str,
    manifest_feed: str | None,
    manifest_built_at: datetime | None,
    reason: str | None = None,
) -> None:
    """A round of the daily MÁV index ended with result (for reason, when it
    did not go through)."""
    record: dict[str, Any] = {
        "at": dt_util.utcnow().isoformat(),
        "result": result,
        "manifest_feed": manifest_feed,
        "manifest_built_at": manifest_built_at.isoformat() if manifest_built_at else None,
    }
    if reason:
        record["reason"] = reason
    await _async_put(hass, DAILY_ROUND, record)


async def async_record_daily_settling(hass: HomeAssistant, reason: str) -> None:
    """A look of the daily MÁV index is settling (for reason): the last
    round's record stays, with settling added; the next round's record
    replaces it."""
    current = (await async_load(hass)).get(DAILY_ROUND)
    record = dict(current) if isinstance(current, dict) else {}
    record.update(settling=reason, settling_at=dt_util.utcnow().isoformat())
    await _async_put(hass, DAILY_ROUND, record)


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


async def async_record_pages_round(
    hass: HomeAssistant,
    results: dict[str, tuple[str, str | None] | tuple[str, str | None, str | None]],
    settling: dict[str, str] | None = None,
) -> None:
    """A round of the weekly timetables settled these indexes: name ->
    (result, the manifest's feed for it[, why: the reason, None for none]).
    settling: the indexes it left settling, name -> reason; their settled
    records stay, with settling added (a record that later settles drops
    it)."""
    if not results and not settling:
        return
    at = dt_util.utcnow().isoformat()
    records: dict[str, dict[str, Any]] = {}
    for name, (result, feed, *why) in results.items():
        record: dict[str, Any] = {"at": at, "result": result, "manifest_feed": feed}
        if why and why[0]:
            record["reason"] = why[0]
        records[name] = record
    if settling:
        current = (await async_load(hass)).get(PAGES_ROUND)
        current = current if isinstance(current, dict) else {}
        for name, reason in settling.items():
            last = current.get(name)
            records[name] = {**(last if isinstance(last, dict) else {}), "settling": reason, "settling_at": at}
    await _async_merge(hass, PAGES_ROUND, records)


async def async_unknown_key_day(hass: HomeAssistant, manifest: str, today: date) -> int:
    """A round found manifest signed by a key this version does not know,
    today: on how many consecutive days that has been so, today included
    (a day without it starts over). Written once a day; a failure is
    logged, never raised (then 1)."""
    try:
        state = await async_load(hass)
        seen = state.get(UNKNOWN_KEY_SEEN)
        seen = dict(seen) if isinstance(seen, dict) else {}
        record = seen.get(manifest)
        record = record if isinstance(record, dict) else {}
        days, last = record.get("days"), record.get("last")
        days = days if isinstance(days, int) and not isinstance(days, bool) and days > 0 else 0
        if last == today.isoformat() and days:
            return days
        days = days + 1 if last == (today - timedelta(days=1)).isoformat() else 1
        seen[manifest] = {"days": days, "last": today.isoformat()}
        state[UNKNOWN_KEY_SEEN] = seen
        await async_save(hass, state)
    except Exception as err:  # noqa: BLE001 - only how long it has been so
        _LOGGER.warning("Could not record the timetable status: %s", err)
        return 1
    return days


async def async_clear_unknown_key(hass: HomeAssistant, manifest: str) -> None:
    """manifest's signature verified: its unknown-key days end. Written
    only when there were any; a failure is logged, never raised."""
    try:
        state = await async_load(hass)
        seen = state.get(UNKNOWN_KEY_SEEN)
        if not isinstance(seen, dict) or manifest not in seen:
            return
        seen = {name: record for name, record in seen.items() if name != manifest}
        if seen:
            state[UNKNOWN_KEY_SEEN] = seen
        else:
            state.pop(UNKNOWN_KEY_SEEN, None)
        await async_save(hass, state)
    except Exception as err:  # noqa: BLE001 - only how long it has been so
        _LOGGER.warning("Could not record the timetable status: %s", err)
