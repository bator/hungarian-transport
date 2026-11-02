"""Sensors: which timetables the cards use, and the daily MÁV check.

- MÁV route index: the served index's feed date; its feed, source (what
  installed it: the daily index, a local build or upload, else a release
  or the local updater script), install time, trains, lines, fitted share
  and, when known, the audit's ok ratio.
- Volán timetable: the Volán index's feed date; feed, trips, stops, build
  time and source (a release, or the local updater script when its durable
  copy is this index).
- City timetables: the city index's build time; operators, trips, and the
  per-operator feeds when there are only a few.
- Daily MÁV check: when the daily MÁV index last ran (mav_daily.py); its
  result, the manifest's feed and build time, the next round and the
  failure streak in days. Result "disabled" when it is turned off.

Data: timetables.TimetableStatus (entry.runtime_data).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import storage
from .const import DOMAIN
from .timetables import MAX_LISTED_FEEDS, TimetableStatus, city_feeds, feed_date

DISABLED = "disabled"


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return dt_util.as_utc(value).isoformat()
    return value if isinstance(value, str) else None


def _compact(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """Attributes without the unknown ones."""
    return {key: value for key, value in attrs.items() if value is not None}


def _mav_source(status: TimetableStatus) -> tuple[str, str | None, float | None]:
    """What installed the served MÁV index, when (UTC ISO) and its audit's
    ok ratio when known."""
    info = status.indexes.get("mav") or {}
    record = status.record.get(storage.MAV_INSTALL)
    if (
        isinstance(record, dict)
        and record.get("feed") == info.get("feed")
        and record.get("size") == info.get("size")
        and record.get("source") in (storage.SOURCE_DAILY, storage.SOURCE_LOCAL)
    ):
        return record["source"], record.get("installed_at"), record.get("audit_ok_ratio")
    # Not installed by this integration: the updater script's (its durable
    # copy is this index) or a release's; installed when the file was written.
    source = storage.SOURCE_LOCAL if info.get("durable") else storage.SOURCE_RELEASE
    return source, _iso(info.get("mtime")), None


def _mav_value(status: TimetableStatus) -> date | None:
    return (status.indexes.get("mav") or {}).get("date")


def _mav_attrs(status: TimetableStatus) -> dict[str, Any]:
    info = status.indexes.get("mav") or {}
    source, installed_at, ok_ratio = _mav_source(status)
    return _compact(
        {
            "feed_version": info.get("feed"),
            "source": source,
            "installed_at": installed_at,
            "trains": info.get("trains"),
            "lines": info.get("lines"),
            "fitted_share": info.get("fitted_share"),
            "audit_ok_ratio": ok_ratio,
        }
    )


def _volan_value(status: TimetableStatus) -> date | None:
    return feed_date((status.indexes.get("volan") or {}).get("feed"))


def _volan_attrs(status: TimetableStatus) -> dict[str, Any]:
    info = status.indexes.get("volan") or {}
    return _compact(
        {
            "feed_version": info.get("feed"),
            "trips": info.get("trips"),
            "stops": info.get("stops"),
            "updated_at": _iso(info.get("updated_at")),
            "source": "local_updater" if info.get("durable") else storage.SOURCE_RELEASE,
        }
    )


def _city_value(status: TimetableStatus) -> datetime | None:
    return (status.indexes.get("city") or {}).get("updated_at")


def _city_attrs(status: TimetableStatus) -> dict[str, Any]:
    info = status.indexes.get("city") or {}
    feeds = city_feeds(info.get("feed"))
    return _compact(
        {
            "operators": info.get("operators") if info.get("operators") is not None else len(feeds),
            "trips": info.get("trips"),
            "feeds": feeds if 0 < len(feeds) <= MAX_LISTED_FEEDS else None,
        }
    )


def _round(status: TimetableStatus) -> dict[str, Any]:
    record = status.record.get(storage.DAILY_ROUND)
    return record if isinstance(record, dict) else {}


def _daily_value(status: TimetableStatus) -> datetime | None:
    if not status.daily_enabled:
        return None
    at = _round(status).get("at")
    return dt_util.parse_datetime(at) if isinstance(at, str) else None


def _daily_attrs(status: TimetableStatus) -> dict[str, Any]:
    streak = status.record.get("daily_failures")
    days = streak.get("days") if isinstance(streak, dict) else None
    if not status.daily_enabled:
        return {"result": DISABLED}
    last = _round(status)
    return _compact(
        {
            "result": last.get("result"),
            "manifest_feed": last.get("manifest_feed"),
            "manifest_built_at": last.get("manifest_built_at"),
            "next_run": _iso(status.next_run),
            "failure_streak": days if isinstance(days, int) and not isinstance(days, bool) else 0,
        }
    )


@dataclass(frozen=True, kw_only=True)
class TimetableSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TimetableStatus], Any]
    attrs_fn: Callable[[TimetableStatus], dict[str, Any]]
    # The index the sensor shows; unavailable while it is missing.
    index: str | None = None


SENSORS = (
    TimetableSensorDescription(
        key="mav_route_index",
        translation_key="mav_route_index",
        device_class=SensorDeviceClass.DATE,
        index="mav",
        value_fn=_mav_value,
        attrs_fn=_mav_attrs,
    ),
    TimetableSensorDescription(
        key="volan_timetable",
        translation_key="volan_timetable",
        device_class=SensorDeviceClass.DATE,
        index="volan",
        value_fn=_volan_value,
        attrs_fn=_volan_attrs,
    ),
    TimetableSensorDescription(
        key="city_timetables",
        translation_key="city_timetables",
        device_class=SensorDeviceClass.TIMESTAMP,
        index="city",
        value_fn=_city_value,
        attrs_fn=_city_attrs,
    ),
    TimetableSensorDescription(
        key="daily_mav_check",
        translation_key="daily_mav_check",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_daily_value,
        attrs_fn=_daily_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    status: TimetableStatus = entry.runtime_data
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Hungarian transport",
        entry_type=DeviceEntryType.SERVICE,
    )
    async_add_entities(TimetableSensor(status, entry, device, description) for description in SENSORS)


class TimetableSensor(SensorEntity):
    """One of SENSORS; updated by TimetableStatus, never polled."""

    entity_description: TimetableSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        status: TimetableStatus,
        entry: ConfigEntry,
        device: DeviceInfo,
        description: TimetableSensorDescription,
    ) -> None:
        self.entity_description = description
        self._status = status
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device

    @property
    def available(self) -> bool:
        index = self.entity_description.index
        return index is None or self._status.indexes.get(index) is not None

    @property
    def native_value(self) -> date | datetime | None:
        return self.entity_description.value_fn(self._status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.entity_description.attrs_fn(self._status)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._status.async_add_listener(self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()
