"""Home Assistant side of the MÁV rail index.

MÁV publishes its GTFS only to registered users, so the feed is not shipped.
Put it at /config/hungarian-transport/mav-gtfs.zip (or POST it to
/api/hungarian_transport/mav_gtfs as an admin) and the integration builds
www/mav-index.json.gz from it: every stop pattern of every train, with its
route and stations. Each build is followed by an audit of every trip; when
fewer than MIN_OK of the trains pass, a persistent notification says so.

The index lives next to the card, which a HACS update replaces, so it is
rebuilt at startup whenever it is missing or the feed changed since it was
built. Which feed (size, mtime) built which index is kept in STATE_NAME next
to the feed. The build (a minute of CPU over stop_times.txt) runs as a
separate Python process, not in the executor: an executor thread would hold
the GIL against the event loop right after startup, and the process gives the
feed's memory back on exit. It runs as a background task, so an upload whose
client gives up does not kill a build that already replaced the feed.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import json
import logging
import os
import re
import stat
import sys
import uuid
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
STATE_NAME = "mav-index.build.json"
INDEX_NAME = "mav-index.json.gz"
UPLOAD_URL = "/api/hungarian_transport/mav_gtfs"
MAX_UPLOAD = 300 * 1024 * 1024
MIN_OK = 0.99
NOTIFICATION_ID = "hungarian_transport_mav_index"
# Standard library only, so Home Assistant's own interpreter runs it.
BUILD_RUNNER = Path(mav_index.__file__)
BUILD_TIMEOUT = 30 * 60
# mav_index.write's temp file (tempfile.mkstemp): the index name, a dot and
# eight random characters. A killed build leaves it in www/, which is served.
TEMP_INDEX_RE = re.compile(re.escape(INDEX_NAME) + r"\.[a-z0-9_]{8}")


class MavSourceMissing(Exception):
    """There is no MÁV GTFS zip at source_path()."""


def source_path(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(SOURCE_DIR, SOURCE_NAME))


def state_path(hass: HomeAssistant) -> Path:
    return source_path(hass).with_name(STATE_NAME)


def _fingerprint(path: Path) -> list[int] | None:
    """Size and mtime of a regular file; a new upload or copy changes them."""
    try:
        st = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return [st.st_size, st.st_mtime_ns]


def _read_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _needs_build(src: Path, out: Path, state_file: Path) -> bool:
    """Whether the index is missing or was not built from the current feed.

    A build that did not finish leaves "building" in the state file and is
    done again. An index this integration did not build (a release shipped
    it, or the update script restored it) is kept unless the feed is newer.
    """
    source = _fingerprint(src)
    if source is None:
        return False
    index = _fingerprint(out)
    if index is None:
        return True
    state = _read_state(state_file)
    if state.get("building"):
        return True
    if state.get("index") == index:
        return state.get("source") != source
    return index[1] < source[1]


def _remove_temp_indexes(out: Path) -> None:
    with contextlib.suppress(OSError):
        for path in out.parent.iterdir():
            if TEMP_INDEX_RE.fullmatch(path.name):
                path.unlink(missing_ok=True)


async def _build(hass: HomeAssistant, src: Path, out: Path) -> dict[str, Any]:
    """mav_index.build_and_audit in its own process; its stats dict."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(BUILD_RUNNER),
            str(src),
            str(out),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as err:
        # The interpreter did not start; not to be read as a missing feed.
        raise RuntimeError(f"could not start the index build: {err}") from err
    try:
        async with asyncio.timeout(BUILD_TIMEOUT):
            stdout, stderr = await proc.communicate()
    except BaseException as err:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        await hass.async_add_executor_job(_remove_temp_indexes, out)
        if isinstance(err, TimeoutError):
            raise RuntimeError(f"index build timed out after {BUILD_TIMEOUT}s") from None
        raise
    if proc.returncode != 0:
        tail = stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"index build exited {proc.returncode}: {tail[0]}")
    try:
        stats = json.loads(stdout)
    except ValueError as err:
        raise RuntimeError("index build returned no statistics") from err
    if not isinstance(stats, dict) or "audit" not in stats:
        raise RuntimeError("index build returned no statistics")
    return stats


@dataclass
class _Job:
    force: bool
    started: bool = False
    task: asyncio.Task[dict[str, Any] | None] | None = None


async def async_build(hass: HomeAssistant, www: Path, *, force: bool = False) -> dict[str, Any] | None:
    """Build and audit the index; None when it is already up to date.

    Raises MavSourceMissing without a feed. Builds run one at a time as
    background tasks: a caller that goes away (an upload whose client
    disconnected) does not cancel its build, and callers share a build that
    is queued but has not read the feed yet.
    """
    return await asyncio.shield(_build_task(hass, www, force))


def _build_task(hass: HomeAssistant, www: Path, force: bool) -> asyncio.Task[dict[str, Any] | None]:
    data = hass.data.setdefault(DOMAIN, {})
    queued: _Job | None = data.get("mav_queued")
    if queued is not None and queued.task is not None and not queued.started and (queued.force or not force):
        return queued.task
    job = data["mav_queued"] = _Job(force)
    job.task = hass.async_create_background_task(
        _run_job(hass, www, job), "hungarian_transport_mav_index"
    )
    return job.task


async def _run_job(hass: HomeAssistant, www: Path, job: _Job) -> dict[str, Any] | None:
    data = hass.data.setdefault(DOMAIN, {})
    lock: asyncio.Lock = data.setdefault("mav_lock", asyncio.Lock())
    async with lock:
        job.started = True
        if data.get("mav_queued") is job:
            del data["mav_queued"]
        try:
            return await _build_locked(hass, www, job.force)
        except MavSourceMissing:
            raise
        except RuntimeError as err:
            # The caller may be gone (a disconnected upload): log it here.
            _LOGGER.error("MÁV index build failed: %s", err)
            raise
        except Exception:
            _LOGGER.exception("MÁV index build failed")
            raise


async def _build_locked(hass: HomeAssistant, www: Path, force: bool) -> dict[str, Any] | None:
    src = source_path(hass)
    out = www / INDEX_NAME
    state_file = state_path(hass)
    source = await hass.async_add_executor_job(_fingerprint, src)
    if source is None:
        raise MavSourceMissing(str(src))
    if not force and not await hass.async_add_executor_job(_needs_build, src, out, state_file):
        return None
    # Until the build has finished, the index on disk does not match any feed.
    await hass.async_add_executor_job(_write_state, state_file, {"building": source})
    stats = await _build(hass, src, out)
    index = await hass.async_add_executor_job(_fingerprint, out)
    await hass.async_add_executor_job(
        _write_state, state_file, {"source": source, "index": index, "feed_version": stats.get("feed_version")}
    )
    hass.data.setdefault(DOMAIN, {})["mav"] = stats
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
    # The build task logs its own failure; a bad feed must not stop the integration.
    with contextlib.suppress(Exception):
        await async_build(hass, www)


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
            # Authenticated, but not allowed: 403, not 401.
            return self.json_message("Admin access required", HTTPStatus.FORBIDDEN)
        dest = source_path(hass)
        # Own temp file per upload: two at once must not write into one file.
        tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex}.upload")
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
            # A client that disconnects now cancels this handler, not the build.
            stats = await async_build(hass, self._www, force=True)
        except Exception as err:  # noqa: BLE001 - the build task logged it
            return self.json_message(f"Saved, but the index build failed: {err}", HTTPStatus.INTERNAL_SERVER_ERROR)
        return self.json({"received": size, **(stats or {})})
