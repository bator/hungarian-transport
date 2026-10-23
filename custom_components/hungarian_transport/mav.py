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
Releases ship an index too. At startup the newer feed wins: an index on disk
from a newer feed than the local one (by the date its feed_version starts
with) is kept, and so is any index while the local feed has expired. An
upload or the rebuild service always builds the local feed; that index then
stays until the next update unpacks a release's index over it.
A build that was interrupted (timeout, killed, out of memory) says nothing
about the feed and is not recorded so: the next start tries again. One that
is interrupted INTERRUPTED_LIMIT times in a row on the same feed (a host too
small for it) is not tried again at startup either, with a Repairs issue,
until the feed changes or the rebuild service builds it.
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
from datetime import date, datetime
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
from homeassistant.util import dt as dt_util

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
# _write_state's temp file next to the feed. Not "*.tmp": the index update
# script (scripts/update_volan_index.py, sweep_temps) deletes those in this
# folder, which could take this file between its write and its rename.
STATE_TEMP_RE = re.compile(re.escape(STATE_NAME) + r"\.[0-9a-f]{32}\.writing")
# mav_index.write puts "feed" right after "v", so the head of the file has it.
INDEX_FEED_RE = re.compile(rb'"feed":("(?:[^"\\]|\\.)*"|null)')
# State keys about the index on disk, the feed that last failed and the
# interruptions in a row of the current feed's build; a new build record
# ("building") keeps them, so a build that fails can put them back.
STATE_KEEP = ("source", "index", "feed_version", "failed", "error", "interrupted")
# Interruptions in a row of one feed's build after which startup stops trying
# it: an OOM kill on a small host, or the timeout, happens again at every
# start, each time costing BUILD_TIMEOUT of CPU or memory pressure.
INTERRUPTED_LIMIT = 2
# MÁV's feed_version starts with the feed's date: "20261022.M2ChangeID".
FEED_DATE_RE = re.compile(r"(\d{8})(?!\d)")
# What the startup check decides about the local feed.
BUILD = "build"
CURRENT = "current"
FAILED = "failed"
INTERRUPTED = "interrupted"
SUPERSEDED = "superseded"


class MavSourceMissing(Exception):
    """There is no MÁV GTFS zip at source_path()."""


class MavBuildInterrupted(RuntimeError):
    """The build did not finish for a reason outside the feed: it timed out,
    was killed, ran out of memory or did not start. Not recorded as a failed
    feed, so the next start tries again."""


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
    """Replace the state file. Only called under the build lock, so another
    temp file of it is one a stopped Home Assistant left behind."""
    with contextlib.suppress(OSError):
        for stale in path.parent.iterdir():
            if STATE_TEMP_RE.fullmatch(stale.name):
                stale.unlink(missing_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.writing")
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


def _interruptions(state: dict[str, Any], source: list[int]) -> tuple[int, str]:
    """How many builds of the feed with fingerprint source were interrupted
    in a row, and the last one's error."""
    record = state.get("interrupted")
    if not isinstance(record, dict) or record.get("source") != source:
        return 0, ""
    count = record.get("count")
    if not isinstance(count, int) or isinstance(count, bool):
        return 0, ""
    return count, str(record.get("error") or "")


def _interrupted_message(count: int, error: str) -> str:
    return f"the build was interrupted {count} times in a row, last: {error}"


@dataclass(frozen=True)
class _FeedInfo:
    """feed_info.txt's first row: feed_version, feed_start_date, feed_end_date."""

    version: str = ""
    start: str = ""
    end: str = ""


def _source_info(src: Path) -> _FeedInfo:
    """The feed's feed_info.txt; empty fields when it has none."""
    try:
        with mav_index.Feed(src) as feed:
            for row in feed.rows("feed_info.txt"):
                return _FeedInfo(
                    (row.get("feed_version") or "").strip(),
                    (row.get("feed_start_date") or "").strip(),
                    (row.get("feed_end_date") or "").strip(),
                )
    except (OSError, ValueError, zipfile.BadZipFile, csv.Error):
        pass
    return _FeedInfo()


def _gtfs_date(value: str | None) -> date | None:
    """The date a GTFS date (YYYYMMDD) or a feed_version starting with one names."""
    match = FEED_DATE_RE.match(value or "")
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _feed_date(info: _FeedInfo) -> date | None:
    """How recent a feed is: its feed_version's date, else its start, else its end."""
    for value in (info.version, info.start, info.end):
        if (day := _gtfs_date(value)) is not None:
            return day
    return None


def _today() -> date:
    """Today in Home Assistant's time zone: a feed ends at the end of its day."""
    return dt_util.now().date()


def _superseded(info: _FeedInfo, index_feed: str | None, today: date) -> bool:
    """Whether the index on disk, built from index_feed, beats the local feed.

    Only an index that names its feed can. It does when the local feed has
    expired, or when the index's feed_version dates a newer feed. A local feed
    that cannot be dated is built, as is one of the same date.
    """
    if not index_feed or index_feed == info.version:
        return False
    end = _gtfs_date(info.end)
    if end is not None and end < today:
        return True
    local, current = _feed_date(info), _gtfs_date(index_feed)
    return local is not None and current is not None and current > local


def _startup_decision(src: Path, out: Path, state_file: Path, today: date | None = None) -> str | None:
    """What startup does with the local feed: BUILD it, or why not.

    None without a feed. SUPERSEDED: the index on disk names a newer feed
    than the local one (a release shipped it), or the local feed has expired;
    the newer feed wins. FAILED and INTERRUPTED: the feed's build failed, or
    was interrupted INTERRUPTED_LIMIT times in a row; the rebuild service and
    an upload force a build. CURRENT: the index on disk is this feed's.

    A build that did not finish leaves "building" in the state file and is
    done again. An index this integration did not build (a release shipped
    it, or the update script restored it) is kept when its "feed" is the
    current feed's feed_version: its mtime says when it was unpacked, not
    which feed it came from.
    """
    source = _fingerprint(src)
    if source is None:
        return None
    state = _read_state(state_file)
    index = _fingerprint(out)
    own = state.get("source") == source
    # The state's feed_version names this feed's version without opening the zip.
    version = state.get("feed_version") if own else None
    index_feed: str | None = None
    if index is not None and not (own and state.get("index") == index):
        # The index on disk was not built from this feed: the newer one wins.
        index_feed = _index_feed(out)
        if not version or index_feed != version:
            info = _source_info(src)
            version = version or info.version
            if _superseded(info, index_feed, today or _today()):
                return SUPERSEDED
    if state.get("failed") == source:
        return FAILED
    if _interruptions(state, source)[0] >= INTERRUPTED_LIMIT:
        return INTERRUPTED
    if index is None or state.get("building"):
        return BUILD
    if state.get("index") == index:
        return BUILD if not own else CURRENT
    return CURRENT if version and index_feed == version else BUILD


def _needs_build(src: Path, out: Path, state_file: Path, today: date | None = None) -> bool:
    """Whether startup builds the local feed (see _startup_decision)."""
    return _startup_decision(src, out, state_file, today) == BUILD


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
        raise MavBuildInterrupted(f"could not start the index build: {err}") from err
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
            raise MavBuildInterrupted(f"index build timed out after {BUILD_TIMEOUT}s") from None
        raise
    if proc.returncode != 0:
        await hass.async_add_executor_job(_remove_temp_indexes, out)
        tail = stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["no output"]
        message = f"index build exited {proc.returncode}: {tail[0]}"
        # Killed by a signal (the OOM killer, a stop) or out of memory: the
        # host's state at the time, not something wrong with the feed.
        if proc.returncode < 0 or tail[0].startswith("MemoryError"):
            raise MavBuildInterrupted(message)
        raise RuntimeError(message)
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


async def _log_superseded(hass: HomeAssistant, src: Path, out: Path, source: list[int]) -> None:
    """Say once per feed and index why the local feed is not built."""
    index = await hass.async_add_executor_job(_fingerprint, out)
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("mav_superseded") == (source, index):
        return
    data["mav_superseded"] = (source, index)
    info = await hass.async_add_executor_job(_source_info, src)
    current = await hass.async_add_executor_job(_index_feed, out)
    end = _gtfs_date(info.end)
    why = f"expired on {end.isoformat()}" if end and end < _today() else "is older"
    _LOGGER.info(
        "Keeping the MÁV index of feed %s: the local feed %s (%s) %s. Upload it "
        "or run the rebuild_mav_index action to build it anyway",
        current, info.version or "without a feed_version", src, why,
    )


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
    if not job.force:
        decision = await hass.async_add_executor_job(_startup_decision, src, out, state_file, _today())
        if decision != BUILD:
            interrupted, last_error = _interruptions(state, source)
            if decision == FAILED:
                # Repairs issues do not outlive a restart; this feed still fails.
                _create_failed_issue(hass, str(state.get("error") or ""))
            elif decision == INTERRUPTED:
                _create_failed_issue(hass, _interrupted_message(interrupted, last_error))
            elif decision == SUPERSEDED:
                await _log_superseded(hass, src, out, source)
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
        if job.upload is None and isinstance(err, MavBuildInterrupted):
            # Says nothing about the feed: the next start tries again (an
            # earlier failure of it, too, is no longer known to hold), unless
            # this feed's build keeps being interrupted.
            count = _interruptions(previous, source)[0] + 1
            restored = {k: v for k, v in restored.items() if k not in ("failed", "error")}
            restored["interrupted"] = {"source": source, "count": count, "error": str(err)}
            if count >= INTERRUPTED_LIMIT:
                _create_failed_issue(hass, _interrupted_message(count, str(err)))
            else:
                ir.async_delete_issue(hass, DOMAIN, ISSUE_BUILD_FAILED)
        elif job.upload is None:
            # Not again at every start; the rebuild service tries again.
            restored = {k: v for k, v in restored.items() if k != "interrupted"}
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
        try:
            await hass.async_add_executor_job(fh.close)
            await hass.async_add_executor_job(mav_index.check_feed, tmp)
        except BaseException as err:
            # Until async_build takes it over, the temp file (up to
            # MAX_UPLOAD) is this handler's, also when a client that
            # disconnects cancels it here.
            await hass.async_add_executor_job(partial(tmp.unlink, missing_ok=True))
            if isinstance(err, ValueError):
                return self.json_message(str(err), HTTPStatus.BAD_REQUEST)
            raise
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
