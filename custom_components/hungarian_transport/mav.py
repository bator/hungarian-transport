"""Home Assistant side of the MÁV rail index.

MÁV publishes its GTFS only to registered users, so the feed is not shipped.
Put it at /config/hungarian-transport/mav-gtfs.zip (or POST it to
/api/hungarian_transport/mav_gtfs as an admin) and the integration builds
www/mav-index.json.gz from it: every stop pattern of every train, with its
route and stations. Each build is followed by an audit of every trip; an
index with fewer than MIN_TRAINS trains, or with fewer than MIN_OK of them
passing, is not written (the previous one stays) and a persistent
notification says so. An upload is built from its temp file and replaces the
feed only once its index was written, so a wrong feed never replaces a good one.

The index lives next to the card, which a HACS update replaces, so it is
rebuilt at startup whenever it is missing or the feed changed since it was
built. Which feed (size, mtime) built which index is kept in STATE_NAME next
to the feed, and so is a feed whose build failed: that one is not built again
at every start, a Repairs issue says so, and the rebuild service tries again.
The build (a minute of CPU over stop_times.txt) runs as a separate Python
process, not in the executor: an executor thread would hold the GIL against
the event loop right after startup, and the process gives the feed's memory
back on exit. It runs as a background task, so an upload whose client gives
up does not kill its build.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
from dataclasses import dataclass
import gzip
import json
import logging
import os
import re
import stat
import sys
import uuid
import zipfile
import zlib
from collections.abc import Callable
from functools import partial
from http import HTTPStatus
from pathlib import Path
from typing import Any

from aiohttp import web

from homeassistant.components import persistent_notification
from homeassistant.components.http import KEY_HASS_USER, HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
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
# The guard the build process applies before it writes an index.
MIN_OK = mav_index.MIN_OK
MIN_TRAINS = mav_index.MIN_TRAINS
NOTIFICATION_ID = "hungarian_transport_mav_index"
ISSUE_BUILD_FAILED = "mav_build_failed"
# Standard library only, so Home Assistant's own interpreter runs it.
BUILD_RUNNER = Path(mav_index.__file__)
BUILD_TIMEOUT = 30 * 60
# mav_index.write's temp file (tempfile.mkstemp): the index name, a dot and
# eight random characters. A killed build leaves it in www/, which is served.
TEMP_INDEX_RE = re.compile(re.escape(INDEX_NAME) + r"\.[a-z0-9_]{8}")
# An upload's own temp file next to the feed; one left behind (Home Assistant
# stopped during the upload or its build) is removed at the next setup.
UPLOAD_RE = re.compile(re.escape(SOURCE_NAME) + r"\.[0-9a-f]{32}\.upload")
# mav_index.write puts "feed" right after "v", so the head of the file has it.
INDEX_FEED_RE = re.compile(rb'"feed":("(?:[^"\\]|\\.)*"|null)')
# State keys about the index on disk and the feed that last failed; a new
# build record ("building") keeps them, so a build that fails can put them back.
STATE_KEEP = ("source", "index", "feed_version", "failed", "error")


class MavSourceMissing(Exception):
    """There is no MÁV GTFS zip at source_path()."""


class MavIndexRejected(RuntimeError):
    """The build finished, but its index failed the guard and was not written."""

    def __init__(self, reason: str, stats: dict[str, Any]) -> None:
        super().__init__(reason)
        self.stats = stats


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


def _index_feed(path: Path) -> str | None:
    """The feed_version an index file was built from ("feed"), or None."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(4096)
    except (OSError, EOFError, zlib.error):
        return None
    match = INDEX_FEED_RE.search(head)
    if match is None:
        return None
    try:
        feed = json.loads(match.group(1))
    except ValueError:
        return None
    return feed if isinstance(feed, str) else None


def _source_version(src: Path) -> str:
    """feed_info.txt's feed_version of the feed, "" when it has none."""
    try:
        with mav_index.Feed(src) as feed:
            return mav_index.feed_version(feed)
    except (OSError, ValueError, zipfile.BadZipFile, csv.Error):
        return ""


def _needs_build(src: Path, out: Path, state_file: Path) -> bool:
    """Whether the index is missing or was not built from the current feed.

    A feed whose build failed is not built again (the rebuild service and an
    upload force a build). A build that did not finish leaves "building" in
    the state file and is done again. An index this integration did not
    build (a release shipped it, or the update script restored it) is kept
    when its "feed" is the current feed's feed_version: its mtime says when
    it was unpacked, not which feed it came from.
    """
    source = _fingerprint(src)
    if source is None:
        return False
    state = _read_state(state_file)
    if state.get("failed") == source:
        return False
    index = _fingerprint(out)
    if index is None:
        return True
    if state.get("building"):
        return True
    if state.get("index") == index:
        return state.get("source") != source
    version = state.get("feed_version") if state.get("source") == source else _source_version(src)
    return not version or _index_feed(out) != version


def _remove_temp_indexes(out: Path) -> None:
    with contextlib.suppress(OSError):
        for path in out.parent.iterdir():
            if TEMP_INDEX_RE.fullmatch(path.name):
                path.unlink(missing_ok=True)


def _remove_uploads(folder: Path) -> None:
    with contextlib.suppress(OSError):
        for path in folder.iterdir():
            if UPLOAD_RE.fullmatch(path.name):
                path.unlink(missing_ok=True)


async def async_remove_stale_uploads(hass: HomeAssistant) -> None:
    """Setup, before the upload view exists: no upload can be under way."""
    await hass.async_add_executor_job(_remove_uploads, source_path(hass).parent)


async def _build(hass: HomeAssistant, src: Path, out: Path) -> dict[str, Any]:
    """mav_index.build_and_audit in its own process; its stats dict.

    A temp index left in www/ (served there) by a killed process goes
    before the build starts, and after one that failed.
    """
    await hass.async_add_executor_job(_remove_temp_indexes, out)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(BUILD_RUNNER),
            str(src),
            str(out),
            "--min-trains",
            str(MIN_TRAINS),
            "--min-ok",
            str(MIN_OK),
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
        await hass.async_add_executor_job(_remove_temp_indexes, out)
        tail = stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"index build exited {proc.returncode}: {tail[0]}")
    try:
        stats = json.loads(stdout)
    except ValueError as err:
        raise RuntimeError("index build returned no statistics") from err
    # A rejected index may lack its audit: too few trains to audit.
    if not isinstance(stats, dict) or not ("audit" in stats or stats.get("rejected")):
        raise RuntimeError("index build returned no statistics")
    return stats


@dataclass
class _Job:
    force: bool
    # An upload's temp file, built instead of the feed and moved onto it
    # once its index is written.
    upload: Path | None = None
    started: bool = False
    task: asyncio.Task[dict[str, Any] | None] | None = None


async def async_build(
    hass: HomeAssistant, www: Path, *, force: bool = False, upload: Path | None = None
) -> dict[str, Any] | None:
    """Build and audit the index; None when it is already up to date.

    Raises MavSourceMissing without a feed, MavIndexRejected when the index
    failed the guard. Builds run one at a time as background tasks: a caller
    that goes away (an upload whose client disconnected) does not cancel its
    build, and callers share a build of the feed that is queued but has not
    read it yet. An upload (always built) is never shared.
    """
    return await asyncio.shield(_build_task(hass, www, force, upload))


def _build_task(
    hass: HomeAssistant, www: Path, force: bool, upload: Path | None = None
) -> asyncio.Task[dict[str, Any] | None]:
    data = hass.data.setdefault(DOMAIN, {})
    queued: _Job | None = data.get("mav_queued")
    if (
        upload is None
        and queued is not None
        and queued.task is not None
        and not queued.task.done()
        and not queued.started
    ):
        # It reads the feed when it starts: one build serves every caller,
        # forced when any of them asked for that.
        queued.force |= force
        return queued.task
    job = _Job(force or upload is not None, upload)
    if upload is None:
        data["mav_queued"] = job
    job.task = hass.async_create_background_task(
        _run_job(hass, www, job), "hungarian_transport_mav_index"
    )
    return job.task


async def _run_job(hass: HomeAssistant, www: Path, job: _Job) -> dict[str, Any] | None:
    data = hass.data.setdefault(DOMAIN, {})
    lock: asyncio.Lock = data.setdefault("mav_lock", asyncio.Lock())
    try:
        async with lock:
            job.started = True
            if data.get("mav_queued") is job:
                del data["mav_queued"]
            try:
                return await _build_locked(hass, www, job)
            except MavSourceMissing:
                raise
            except RuntimeError as err:
                # The caller may be gone (a disconnected upload): log it here.
                _LOGGER.error("MÁV index build failed: %s", err)
                raise
            except Exception:
                _LOGGER.exception("MÁV index build failed")
                raise
    finally:
        # Cancelled before it started (Home Assistant is stopping): a later
        # request must not get this task back.
        if data.get("mav_queued") is job:
            del data["mav_queued"]


def _create_failed_issue(hass: HomeAssistant, error: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_BUILD_FAILED,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_BUILD_FAILED,
        translation_placeholders={"path": str(source_path(hass)), "error": error},
    )


def _notify_rejected(hass: HomeAssistant, stats: dict[str, Any]) -> None:
    feed = stats.get("feed_version") or "the feed"
    message = f"The MÁV route index from {feed} was not written: {stats['rejected']}."
    audit = stats.get("audit")
    if audit and audit["ok_ratio"] < MIN_OK:
        message += (
            f" {audit['trips_bad']} of {audit['trips']} trips have a station off their route. "
            "First failing trains: " + ", ".join(w["train"] for w in stats.get("audit_worst") or [])
            + "."
        )
    message += " The previous index stays in use."
    persistent_notification.async_create(
        hass, message, title="Hungarian transport", notification_id=NOTIFICATION_ID
    )


async def _build_locked(hass: HomeAssistant, www: Path, job: _Job) -> dict[str, Any] | None:
    feed = source_path(hass)
    src = job.upload or feed
    out = www / INDEX_NAME
    state_file = state_path(hass)
    source = await hass.async_add_executor_job(_fingerprint, src)
    if source is None:
        raise MavSourceMissing(str(src))
    state = await hass.async_add_executor_job(_read_state, state_file)
    if not job.force and not await hass.async_add_executor_job(_needs_build, src, out, state_file):
        if state.get("failed") == source:
            # Repairs issues do not outlive a restart; this feed still fails.
            _create_failed_issue(hass, str(state.get("error") or ""))
        return None
    previous = {key: state[key] for key in STATE_KEEP if key in state}
    index_before = await hass.async_add_executor_job(_fingerprint, out)
    # Until the build has finished, the index on disk may not match any feed.
    await hass.async_add_executor_job(_write_state, state_file, {**previous, "building": source})
    try:
        stats = await _build(hass, src, out)
        if stats.get("rejected"):
            _notify_rejected(hass, stats)
            raise MavIndexRejected(stats["rejected"], stats)
    except Exception as err:
        # Nothing was written: what the state said about the index still holds.
        unchanged = index_before == await hass.async_add_executor_job(_fingerprint, out)
        restored = previous if unchanged else {}
        if job.upload is None:
            # Not again at every start; the rebuild service tries again.
            restored = {**restored, "failed": source, "error": str(err)}
            _create_failed_issue(hass, str(err))
        else:
            await hass.async_add_executor_job(partial(job.upload.unlink, missing_ok=True))
        await hass.async_add_executor_job(_write_state, state_file, restored)
        raise
    if job.upload is not None:
        # A rename keeps size and mtime: the fingerprint is the feed's now.
        await hass.async_add_executor_job(os.replace, job.upload, feed)
    index = await hass.async_add_executor_job(_fingerprint, out)
    await hass.async_add_executor_job(
        _write_state, state_file, {"source": source, "index": index, "feed_version": stats.get("feed_version")}
    )
    ir.async_delete_issue(hass, DOMAIN, ISSUE_BUILD_FAILED)
    persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
    hass.data.setdefault(DOMAIN, {})["mav"] = stats
    audit = stats["audit"]
    _LOGGER.info(
        "MÁV index %s: %s trains, %s variants (%s on the station line), audit %s/%s trains",
        stats["feed_version"], stats["trains"] + stats["lines"], stats["variants"],
        stats["stop_lines"], audit["trains_ok"], audit["trains"],
    )
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

    def __init__(self, www: Path, loaded: Callable[[HomeAssistant], bool]) -> None:
        self._www = www
        self._loaded = loaded

    async def post(self, request: web.Request) -> web.Response:
        hass = request.app[KEY_HASS]
        if not request[KEY_HASS_USER].is_admin:
            # Authenticated, but not allowed: 403, not 401.
            return self.json_message("Admin access required", HTTPStatus.FORBIDDEN)
        if not self._loaded(hass):
            # The view outlives a disabled entry; the services refuse too.
            return self.json_message("Hungarian transport is not loaded", HTTPStatus.CONFLICT)
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
        try:
            # Built from the temp file, which becomes the feed only once its
            # index is written. A client that disconnects now cancels this
            # handler, not the build.
            stats = await async_build(hass, self._www, upload=tmp)
        except MavIndexRejected as err:
            return self.json(
                {"message": f"Not saved, the index failed its check: {err}", **err.stats},
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        except Exception as err:  # noqa: BLE001 - the build task logged it
            return self.json_message(
                f"Not saved, the index build failed: {err}", HTTPStatus.INTERNAL_SERVER_ERROR
            )
        return self.json({"received": size, **(stats or {})})
