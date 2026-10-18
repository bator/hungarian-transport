"""Home Assistant side of the MÁV rail index.

MÁV publishes its GTFS only to registered users, so the feed is not shipped.
Put it at /config/hungarian-transport/mav-gtfs.zip (or POST it to
/api/hungarian_transport/mav_gtfs as an admin) and the integration builds
www/mav-index.json.gz from it: every stop pattern of every train, with its
route and stations. Each build is followed by an audit of every trip; when
fewer than MIN_OK of the trains pass, a persistent notification says so.

The index lives next to the card, which a HACS update replaces, so it is
rebuilt at startup whenever it is missing or older than the feed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import Any

from aiohttp import web

from homeassistant.components import persistent_notification
from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS

from . import mav_index
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SOURCE_DIR = "hungarian-transport"
SOURCE_NAME = "mav-gtfs.zip"
INDEX_NAME = "mav-index.json.gz"
UPLOAD_URL = "/api/hungarian_transport/mav_gtfs"
MAX_UPLOAD = 300 * 1024 * 1024
MIN_OK = 0.99
NOTIFICATION_ID = "hungarian_transport_mav_index"


def source_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(SOURCE_DIR, SOURCE_NAME))


def _needs_build(src: Path, out: Path) -> bool:
    if not src.is_file():
        return False
    return not out.is_file() or out.stat().st_mtime < src.stat().st_mtime


def _build(src: Path, out: Path) -> dict[str, Any]:
    mav_index.check_feed(src)
    index = mav_index.build(src)
    stats = dict(index["_stats"])
    stats["bytes"] = mav_index.write(index, out)
    report = mav_index.audit(src, index)
    stats["audit"] = {k: v for k, v in report.items() if k != "worst"}
    stats["audit_worst"] = report["worst"][:5]
    return stats


async def async_build(hass: HomeAssistant, www: Path, *, force: bool = False) -> dict[str, Any] | None:
    """Build and audit the index; None when it is already up to date."""
    data = hass.data.setdefault(DOMAIN, {})
    lock: asyncio.Lock = data.setdefault("mav_lock", asyncio.Lock())
    src = source_path(hass)
    out = www / INDEX_NAME
    async with lock:
        if not await hass.async_add_executor_job(src.is_file):
            raise FileNotFoundError(str(src))
        if not force and not await hass.async_add_executor_job(_needs_build, src, out):
            return None
        stats = await hass.async_add_executor_job(_build, src, out)
    data["mav"] = stats
    audit = stats["audit"]
    _LOGGER.info(
        "MÁV index %s: %s trains, %s variants (%s on the station line), audit %s/%s trains",
        stats["feed_version"], stats["trains"] + stats["lines"], stats["variants"],
        stats["stop_lines"], audit["trains_ok"], audit["trains"],
    )
    if audit["ok_ratio"] < MIN_OK:
        persistent_notification.async_create(
            hass,
            (
                f"The MÁV route index from {stats['feed_version'] or 'the feed'} failed its check: "
                f"{audit['trains_ok']} of {audit['trains']} trains pass, "
                f"{audit['trips_bad']} of {audit['trips']} trips have a station off their route. "
                "First failing trains: "
                + ", ".join(w["train"] for w in stats["audit_worst"])
            ),
            title="Hungarian transport",
            notification_id=NOTIFICATION_ID,
        )
    else:
        persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
    return stats


async def async_build_if_needed(hass: HomeAssistant, www: Path) -> None:
    """Startup: rebuild a missing or stale index, quietly when there is no feed."""
    try:
        await async_build(hass, www)
    except FileNotFoundError:
        return
    except Exception:  # noqa: BLE001 - a bad feed must not stop the integration
        _LOGGER.exception("MÁV index build failed")


class MavGtfsUploadView(HomeAssistantView):
    """Admin-only upload of the MÁV GTFS zip; builds and audits the index.

    The body is the zip itself:
    curl -X POST -H "Authorization: Bearer TOKEN" --data-binary @mav.zip URL
    """

    url = UPLOAD_URL
    name = "api:hungarian_transport:mav_gtfs"
    requires_auth = True

    def __init__(self, www: Path) -> None:
        self._www = www

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        if not request[KEY_HASS_USER].is_admin:
            return self.json_message("Admin access required", HTTPStatus.UNAUTHORIZED)
        dest = source_path(hass)
        tmp = dest.with_name(dest.name + ".upload")
        await hass.async_add_executor_job(partial(dest.parent.mkdir, parents=True, exist_ok=True))
        size = 0
        fh = await hass.async_add_executor_job(partial(open, tmp, "wb"))
        try:
            async for chunk in request.content.iter_chunked(1 << 20):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise web.HTTPRequestEntityTooLarge(max_size=MAX_UPLOAD, actual_size=size)
                await hass.async_add_executor_job(fh.write, chunk)
        except BaseException:
            await hass.async_add_executor_job(fh.close)
            await hass.async_add_executor_job(partial(tmp.unlink, missing_ok=True))
            raise
        await hass.async_add_executor_job(fh.close)
        try:
            await hass.async_add_executor_job(mav_index.check_feed, tmp)
        except ValueError as err:
            await hass.async_add_executor_job(partial(tmp.unlink, missing_ok=True))
            return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
        await hass.async_add_executor_job(os.replace, tmp, dest)
        try:
            stats = await async_build(hass, self._www, force=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("MÁV index build failed after upload")
            return self.json_message(f"Saved, but the index build failed: {err}", HTTPStatus.INTERNAL_SERVER_ERROR)
        return self.json({"received": size, **(stats or {})})
