"""Notifications about timetable updates (options: CONF_NOTIFY_UPDATES).

What notifies, only with the option on:

- the daily MÁV route index was installed (mav_daily.py). MÁV publishes
  almost every day, so this one calls the service only with
  CONF_NOTIFY_DAILY on; otherwise it is the persistent notification alone;
- after an update of the integration, the indexes it ships (Volán, city,
  MÁV) are of other feeds than the ones it last saw. The feeds seen are
  recorded at every setup of the entry, notifications on or off, so the
  first run (and turning notifications on later) only records. Within one
  version the files change only by the integration itself (the daily index,
  a local MÁV build): recorded, never notified. A shipped MÁV index older
  than the one last seen (the daily index of a later day) is no news either;
- the daily MÁV index failed on FAILED_DAYS consecutive days: once per
  streak, dismissed when a round goes through again. A day with only BUSY
  or UNSUPPORTED rounds neither counts nor breaks the streak; a day without
  any round (Home Assistant off) breaks it. A round that went through while
  the site's manifest was built more than STALE_PAGES ago counts as a
  failure: a daily workflow that stopped would otherwise stay quiet. The
  notification, which does not outlive a restart, is created again at setup
  while its streak is open.

Each creates a persistent notification with a stable id per kind (a new one
replaces the old) and, when CONF_NOTIFY_SERVICE names one, calls that
service with {"title", "message"} as well (notify.* services and scripts
with title / message fields). The call waits until Home Assistant has
started: at setup, scripts and notify.mobile_app_* may not be registered
yet. A service that is missing or fails is logged, never raised. Texts are
Hungarian when Home Assistant's language is, else English.
"""
from __future__ import annotations

import gzip
import json
import logging
import zlib
from collections.abc import Mapping
from datetime import time, timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import mav, mav_daily, storage
from .const import (
    CONF_DAILY_TIME,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_UPDATES,
    DEFAULT_DAILY_TIME,
    DEFAULT_NOTIFY_DAILY,
    DEFAULT_NOTIFY_UPDATES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = storage.STORAGE_KEY
STORAGE_VERSION = storage.STORAGE_VERSION
NOTIFICATION_MAV_DAILY = f"{DOMAIN}_mav_daily"
NOTIFICATION_SHIPPED = f"{DOMAIN}_shipped_indexes"
NOTIFICATION_DAILY_FAILED = f"{DOMAIN}_mav_daily_failed"
FAILED_DAYS = 3
# A manifest built longer ago than this: the daily workflow has stopped.
STALE_PAGES = timedelta(days=3)
# The indexes a release ships in www/, in the order a message names them.
SHIPPED = {"volan": "volan-index.json.gz", "city": "city-index.json.gz", "mav": mav.INDEX_NAME}
# "feed" is the index's second key; the city index joins every feed's version.
FEED_HEAD = 256 * 1024
# A round that says nothing about failing or not (mav_daily).
_NEUTRAL = (mav_daily.BUSY, mav_daily.UNSUPPORTED)

TEXT = {
    "en": {
        "mav_daily_title": "Hungarian transport: new MÁV timetable",
        "mav_daily_message": "The daily MÁV route index of the {date} timetable is installed: "
                             "{trains} trains, {lines} lines.",
        "shipped_title": "Hungarian transport: timetables updated",
        "shipped_message": "This version of Hungarian transport ships new timetables: {names}.",
        "failed_title": "Hungarian transport: daily MÁV index not updating",
        "failed_message": "The daily MÁV route index could not be updated on {days} days in a row "
                          "(last error: {error}). The current index stays in use.",
        "stale_error": "the index on GitHub Pages was last built on {date}",
        "volan": "Volán buses",
        "city": "city lines",
        "mav": "MÁV trains ({date} timetable)",
        "unknown": "unknown",
    },
    "hu": {
        "mav_daily_title": "Hungarian transport: új MÁV menetrend",
        "mav_daily_message": "Telepítve a napi MÁV útvonal-index ({date} menetrend): "
                             "{trains} vonat, {lines} vonal.",
        "shipped_title": "Hungarian transport: frissült menetrendek",
        "shipped_message": "A Hungarian transport új verziója friss menetrendeket hozott: {names}.",
        "failed_title": "Hungarian transport: a napi MÁV index nem frissül",
        "failed_message": "A napi MÁV útvonal-index {days} egymást követő napon nem frissült "
                          "(utolsó hiba: {error}). A jelenlegi index marad használatban.",
        "stale_error": "a GitHub Pages indexe utoljára ekkor készült: {date}",
        "volan": "Volán buszok",
        "city": "városi járatok",
        "mav": "MÁV vonatok ({date} menetrend)",
        "unknown": "ismeretlen",
    },
}


def parse_daily_time(value: Any) -> time:
    """The options' daily check time ("HH:MM" or "HH:MM:SS"), 06:00 otherwise."""
    parsed = dt_util.parse_time(str(value)) if value else None
    return parsed or dt_util.parse_time(DEFAULT_DAILY_TIME) or time(6)


def check_service(hass: HomeAssistant, value: str) -> tuple[str, str | None]:
    """The service as Home Assistant names it (lowercase), and an error key
    for the options form: None for "" or an existing service."""
    if not value:
        return "", None
    try:
        value = cv.service(value)
    except vol.Invalid:
        return value, "invalid_service"
    domain, name = value.split(".", 1)
    if not hass.services.has_service(domain, name):
        return value, "service_not_found"
    return value, None


def _text(hass: HomeAssistant) -> dict[str, str]:
    return TEXT["hu" if str(hass.config.language or "").lower().startswith("hu") else "en"]


def _feed_day(feed: str | None, text: dict[str, str]) -> str:
    day = mav._gtfs_date(feed)
    return day.isoformat() if day is not None else (feed or text["unknown"])


def _head_feed(path: Path) -> str | None:
    """An index's "feed" from the head of its gzip, or None."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(FEED_HEAD)
    except (OSError, EOFError, zlib.error):
        return None
    match = mav.INDEX_FEED_RE.search(head)
    if match is None:
        return None
    try:
        feed = json.loads(match.group(1))
    except ValueError:
        return None
    return feed if isinstance(feed, str) else None


def shipped_feeds(www: Path) -> dict[str, str | None]:
    return {key: (_head_feed(www / name) if (www / name).is_file() else None) for key, name in SHIPPED.items()}


def changed_indexes(seen: Mapping[str, Any] | None, version: str, feeds: Mapping[str, str | None]) -> list[str]:
    """The shipped indexes that changed since seen (None on the first run)."""
    if not isinstance(seen, Mapping) or not isinstance(seen.get("feeds"), Mapping):
        return []
    if seen.get("version") == version:
        return []
    old_feeds = seen["feeds"]
    changed = []
    for key in SHIPPED:
        new, old = feeds.get(key), old_feeds.get(key)
        if new is None or new == old:
            continue
        if key == "mav" and old is not None:
            new_day, old_day = mav._gtfs_date(new), mav._gtfs_date(old)
            if new_day is not None and old_day is not None and new_day <= old_day:
                continue
        changed.append(key)
    return changed


# The record lives in storage.py, shared with the timetable sensors.
_store = storage.store
_load = storage.async_load
_save = storage.async_save


class UpdateNotifier:
    """Notifications for one setup of the entry, with its options."""

    def __init__(self, hass: HomeAssistant, options: Mapping[str, Any]) -> None:
        self._hass = hass
        self.enabled = bool(options.get(CONF_NOTIFY_UPDATES, DEFAULT_NOTIFY_UPDATES))
        self.service = str(options.get(CONF_NOTIFY_SERVICE) or "").strip()
        self.notify_daily = bool(options.get(CONF_NOTIFY_DAILY, DEFAULT_NOTIFY_DAILY))
        self.daily_time = parse_daily_time(options.get(CONF_DAILY_TIME, DEFAULT_DAILY_TIME))
        self._waiting: list[CALLBACK_TYPE] = []

    @callback
    def stop(self) -> None:
        """The entry unloads: no service call still waiting for the start."""
        for unsub in self._waiting:
            unsub()
        self._waiting.clear()

    async def async_notify(self, notification_id: str, title: str, message: str, *, service: bool = True) -> None:
        """The persistent notification, and the extra service when set (and
        service is True), once Home Assistant has started."""
        hass = self._hass
        persistent_notification.async_create(hass, message, title=title, notification_id=notification_id)
        if not self.service or not service:
            return
        unsub: CALLBACK_TYPE | None = None
        called = False

        @callback
        def _started(_hass: HomeAssistant) -> None:
            nonlocal called
            called = True
            if unsub in self._waiting:
                self._waiting.remove(unsub)
            self._call_service(title, message)

        # Right away when Home Assistant runs; else at EVENT_HOMEASSISTANT_STARTED,
        # once every integration (script, mobile_app) has registered its services.
        unsub = async_at_started(hass, _started)
        if not called:
            self._waiting.append(unsub)

    @callback
    def _call_service(self, title: str, message: str) -> None:
        hass = self._hass
        if check_service(hass, self.service)[1] is not None:
            _LOGGER.warning(
                "Notification service %s is not available; only the persistent notification was created",
                self.service,
            )
            return
        domain, name = self.service.split(".", 1)

        async def _call() -> None:
            try:
                await hass.services.async_call(domain, name, {"title": title, "message": message}, blocking=True)
            except Exception as err:  # noqa: BLE001 - a notification never breaks an update
                _LOGGER.warning("Notification service %s failed: %s", self.service, err)

        # Not awaited: a script may take its time (delays, waits).
        hass.async_create_background_task(_call(), f"{DOMAIN}_notify_service")

    async def async_check_shipped(self, www: Path, version: str) -> list[str]:
        """Record the shipped indexes' feeds; notify about the changed ones."""
        hass = self._hass
        feeds = await hass.async_add_executor_job(shipped_feeds, www)
        state = await _load(hass)
        changed = changed_indexes(state.get("shipped"), version, feeds)
        new = {"version": version, "feeds": feeds}
        if state.get("shipped") != new:
            state["shipped"] = new
            await _save(hass, state)
        if changed and self.enabled:
            text = _text(hass)
            names = ", ".join(
                text[key].format(date=_feed_day(feeds.get(key), text)) for key in changed
            )
            await self.async_notify(
                NOTIFICATION_SHIPPED, text["shipped_title"], text["shipped_message"].format(names=names)
            )
        return changed

    async def async_restore(self) -> None:
        """Setup: the failure notification of a streak still open again
        (persistent notifications do not outlive a restart); no service call."""
        if not self.enabled:
            return
        streak = (await _load(self._hass)).get("daily_failures") or {}
        if isinstance(streak, dict) and streak.get("notified"):
            title, message = self._failed_text(streak)
            persistent_notification.async_create(
                self._hass, message, title=title, notification_id=NOTIFICATION_DAILY_FAILED
            )

    def _failed_text(self, streak: Mapping[str, Any]) -> tuple[str, str]:
        text = _text(self._hass)
        return text["failed_title"], text["failed_message"].format(
            days=streak.get("days", FAILED_DAYS), error=streak.get("error") or text["unknown"]
        )

    def _stale_error(self, result: str) -> str | None:
        """Why a round that went through still counts as a failure: the
        site's manifest is older than STALE_PAGES."""
        if result not in (mav_daily.UP_TO_DATE, mav_daily.INSTALLED):
            return None
        built = self._hass.data.get(DOMAIN, {}).get("mav_daily_built_at")
        if built is None or dt_util.utcnow() - built <= STALE_PAGES:
            return None
        return _text(self._hass)["stale_error"].format(date=dt_util.as_local(built).date().isoformat())

    async def async_daily_result(self, result: str) -> None:
        """After a round of the daily MÁV index (mav_daily.DailyIndexUpdater)."""
        hass = self._hass
        text = _text(hass)
        if result == mav_daily.INSTALLED and self.enabled:
            info = hass.data.get(DOMAIN, {}).get("mav_daily_installed") or {}
            await self.async_notify(
                NOTIFICATION_MAV_DAILY,
                text["mav_daily_title"],
                text["mav_daily_message"].format(
                    date=_feed_day(info.get("feed"), text),
                    trains=info.get("trains", text["unknown"]),
                    lines=info.get("lines", text["unknown"]),
                ),
                service=self.notify_daily,
            )
        error = self._stale_error(result)
        if error is None and result == mav_daily.FAILED:
            error = hass.data.get(DOMAIN, {}).get("mav_daily_error") or text["unknown"]
        state = await _load(hass)
        streak = dict(state.get("daily_failures") or {})
        today = dt_util.now().date()
        yesterday = (today - timedelta(days=1)).isoformat()
        if result in _NEUTRAL:
            # Neither a failure nor a success: a day of these bridges a
            # streak that was open yesterday.
            bridges = yesterday in (streak.get("last"), streak.get("bridge"))
            if streak and bridges and streak.get("bridge") != today.isoformat():
                streak["bridge"] = today.isoformat()
                state["daily_failures"] = streak
                await _save(hass, state)
            return
        if error is None:
            if not streak:
                return
            if streak.get("notified"):
                persistent_notification.async_dismiss(hass, NOTIFICATION_DAILY_FAILED)
            state.pop("daily_failures", None)
            await _save(hass, state)
            return
        if streak.get("last") != today.isoformat():
            if yesterday in (streak.get("last"), streak.get("bridge")):
                streak["days"] = int(streak.get("days") or 0) + 1
            else:
                # The first failure, or one after a day without a round
                # (Home Assistant off): a new streak, and a new notice.
                if streak.get("notified"):
                    persistent_notification.async_dismiss(hass, NOTIFICATION_DAILY_FAILED)
                streak = {"days": 1}
            streak["last"] = today.isoformat()
            streak.pop("bridge", None)
        streak["error"] = error
        if streak["days"] >= FAILED_DAYS and not streak.get("notified") and self.enabled:
            await self.async_notify(NOTIFICATION_DAILY_FAILED, *self._failed_text(streak))
            streak["notified"] = True
        if state.get("daily_failures") != streak:
            state["daily_failures"] = streak
            await _save(hass, state)
