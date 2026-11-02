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
  and the manifest's feed and build time.

Every writer sends SIGNAL_TIMETABLES afterwards; the sensors read the
indexes again (only the ones whose file changed) and the record.
"""
from __future__ import annotations

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
# MAV_INSTALL sources; an index without a matching record is a release's
# (or the local updater's, timetables.py tells).
SOURCE_DAILY = "daily"
SOURCE_LOCAL = "local"
SOURCE_RELEASE = "release"


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
