"""The daily MÁV route index from GitHub Pages.

MÁV publishes a new GTFS almost every day; releases ship its index once a
week. The repository's daily workflow (.github/workflows/mav-index-daily.yml)
builds the same pfaedle-matched, audited index every morning and publishes it
on GitHub Pages with a manifest (scripts/mav_pages_site.py). This module
reads the manifest a while after startup and then once a day at the time
the options set (CONF_DAILY_TIME, 06:00 local time by default, plus up to
DAILY_JITTER), and installs the index only when:

- the manifest's format is the one this code reads and its index schema is
  mav_index.SCHEMA (else it says so once and installs nothing);
- its feed is the one of the index www/ serves and the same-feed rule
  (mav_index.same_feed_replaces) lets it replace that one: a pfaedle-fitted
  daily index replaces a stop-sequence build of its own feed, and is never
  replaced by one;
- or its feed is newer, by the date its feed_version starts with, than the
  index www/ serves (one of another feed of the same date stays: a local
  build of that day wins), and newer than the local feed (/config/
  hungarian-transport/mav-gtfs.zip) by mav._superseded, the rule startup
  uses. A local feed of the same date, or one that cannot be dated, keeps
  its index, but only while that index is the one served (by the build
  state's fingerprints, or its feed_version): a local feed whose build
  failed, was interrupted or refused by the audit keeps nothing;
- the download has the manifest's size and sha256, unpacks to the schema and
  feed the manifest names, and has at least MIN_TRAINS trains and lines whose
  references all resolve.

It is written with mav_index.write_bytes (temp file and rename) under the
build lock, never while a local build runs or is queued. A failed round
keeps the index on disk and logs once per kind of failure (a warning), not
at every round, and opens no Repairs issue. After a round that failed or
found a build running, the round is tried again RETRY_DELAY later, up to
RETRY_LIMIT times until the next daily round; a daily round that finds a
manifest older than STALE_MANIFEST (today's build is late) is tried once
more LATE_DELAY later. The index is fetched with the manifest's sha256 in its
query, so a cache in front of Pages (max-age=600) never pairs a new manifest
with an old index. The options flow can turn it off (CONF_DAILY_MAV); then
no request is made at all.

The card fetches mav-index.json.gz with a day-stamped URL and "no-cache":
Home Assistant's static files answer with an ETag of the file's mtime and
size, so a replaced index is downloaded on the next load of the dashboard.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import secrets
import zlib
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import mav, mav_index
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Fixed: only this host, over HTTPS, without redirects.
PAGES_BASE = "https://bator.github.io/hungarian-transport"
MANIFEST_URL = f"{PAGES_BASE}/mav-index.json"
INDEX_URL = f"{PAGES_BASE}/mav-index.json.gz"
# The manifest format scripts/mav_pages_site.py writes (MANIFEST_FORMAT).
MANIFEST_FORMAT = 1
MAX_MANIFEST = 64 * 1024
# The index is about 0.25 MB gzipped and 1 MB unpacked.
MAX_INDEX = 16 * 1024 * 1024
MAX_INDEX_JSON = 128 * 1024 * 1024
MANIFEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
INDEX_TIMEOUT = aiohttp.ClientTimeout(total=120)
# First check a while after startup (the startup build goes first), then
# once a day at the chosen time; the jitter spreads every installation's
# requests. A reload of the entry (an options save) starts a first round
# too: a time or an option changed takes effect without waiting a day.
FIRST_DELAY = 10 * 60
FIRST_JITTER = 10 * 60
DAILY_JITTER = 5 * 60
# After a FAILED or BUSY round (a transient error, the startup build holding
# the lock): again this much later, at most RETRY_LIMIT times a day.
RETRY_DELAY = 20 * 60
RETRY_LIMIT = 6
# The workflow builds before 06:00 local time; a daily round that finds a
# manifest older than this (the run was late) looks once more, LATE_DELAY
# (one to two hours) later.
STALE_MANIFEST = timedelta(hours=20)
LATE_DELAY = (60 * 60, 2 * 60 * 60)
FEED_RE = re.compile(r"\d{8}(?!\d)[A-Za-z0-9._:+-]{0,64}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

# What one round did.
INSTALLED = "installed"
UP_TO_DATE = "up_to_date"
LOCAL_WINS = "local_wins"
BUSY = "busy"
UNSUPPORTED = "unsupported"
FAILED = "failed"
_INSTALL = "install"


class DailyIndexError(Exception):
    """The manifest or the index could not be fetched or did not check out."""


class _Unsupported(DailyIndexError):
    """A manifest format or index schema this version does not read."""


async def _get(session: aiohttp.ClientSession, url: str, limit: int, timeout: aiohttp.ClientTimeout) -> bytes:
    """GET url's body, at most limit bytes; DailyIndexError otherwise."""
    what = url.split("?", 1)[0].rsplit("/", 1)[-1]
    try:
        async with session.get(
            url, timeout=timeout, allow_redirects=False, headers={"Cache-Control": "no-cache"}
        ) as res:
            if res.status != 200:
                raise DailyIndexError(f"{what}: HTTP {res.status}")
            length = (res.headers or {}).get("Content-Length")
            if length is not None and length.isdigit() and int(length) > limit:
                raise DailyIndexError(f"{what}: {length} bytes, more than {limit}")
            body = bytearray()
            async for chunk in res.content.iter_chunked(1 << 16):
                body.extend(chunk)
                if len(body) > limit:
                    raise DailyIndexError(f"{what}: more than {limit} bytes")
            return bytes(body)
    except TimeoutError as err:
        raise DailyIndexError(f"{what}: timed out") from err
    except aiohttp.ClientError as err:
        raise DailyIndexError(f"{what}: {type(err).__name__}: {err}") from err


def check_manifest(raw: bytes) -> dict[str, Any]:
    """The manifest's fields this module uses, checked."""
    try:
        man = json.loads(raw)
    except ValueError as err:
        raise DailyIndexError(f"manifest is not JSON: {err}") from err
    if not isinstance(man, dict):
        raise DailyIndexError("manifest is not a JSON object")
    if man.get("format") != MANIFEST_FORMAT:
        raise _Unsupported(f"manifest format {man.get('format')!r}, this version reads {MANIFEST_FORMAT}")
    if man.get("schema") != mav_index.SCHEMA:
        raise _Unsupported(f"index schema {man.get('schema')!r}, this version reads {mav_index.SCHEMA}")
    feed, size, sha = man.get("feed"), man.get("size"), man.get("sha256")
    if not isinstance(feed, str) or not FEED_RE.fullmatch(feed) or mav._gtfs_date(feed) is None:
        raise DailyIndexError(f"manifest names no dated feed: {feed!r}")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_INDEX:
        raise DailyIndexError(f"manifest size {size!r} out of range")
    if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
        raise DailyIndexError("manifest has no sha256")
    return man


def check_index(raw: bytes, man: dict[str, Any], min_trains: int) -> tuple[int, mav_index.IndexRank]:
    """Raise DailyIndexError unless raw is the index man describes; its
    trains and lines, and its rank for the same-feed rule."""
    if len(raw) != man["size"]:
        raise DailyIndexError(f"index is {len(raw)} bytes, the manifest says {man['size']}")
    if hashlib.sha256(raw).hexdigest() != man["sha256"]:
        raise DailyIndexError("index sha256 does not match the manifest")
    try:
        unzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
        text = unzip.decompress(raw, MAX_INDEX_JSON)
        if unzip.unconsumed_tail or not unzip.eof:
            raise DailyIndexError("index is truncated or unpacks to too much")
        if unzip.unused_data:
            # Another gzip member (or junk) after the first: checked would be
            # only the first, served would be the whole file.
            raise DailyIndexError("index has data after its gzip stream")
        index = json.loads(text)
    except (zlib.error, ValueError) as err:
        raise DailyIndexError(f"index does not unpack: {err}") from err
    if not isinstance(index, dict):
        raise DailyIndexError("index is not a JSON object")
    if index.get("v") != mav_index.SCHEMA:
        raise DailyIndexError(f"index schema {index.get('v')!r}, the manifest says {mav_index.SCHEMA}")
    if index.get("feed") != man["feed"]:
        raise DailyIndexError(f"index is of feed {index.get('feed')!r}, the manifest names {man['feed']!r}")
    stations, polylines = index.get("st"), index.get("p")
    numbers, lines = index.get("n"), index.get("l")
    if not (isinstance(stations, list) and isinstance(polylines, list)
            and isinstance(numbers, dict) and isinstance(lines, dict)):
        raise DailyIndexError("index lacks its tables")
    if len(numbers) + len(lines) < min_trains:
        raise DailyIndexError(f"index has only {len(numbers)} trains and {len(lines)} lines")
    if not all(isinstance(p, str) for p in polylines) or not all(
        isinstance(s, list) and len(s) == 3 and isinstance(s[0], str) for s in stations
    ):
        raise DailyIndexError("index has a malformed station or route")
    n_st, n_p = len(stations), len(polylines)
    for table in (numbers, lines):
        for variants in table.values():
            if not isinstance(variants, list) or not variants:
                raise DailyIndexError("index has a train without variants")
            for var in variants:
                if not (isinstance(var, list) and len(var) >= 2 and isinstance(var[0], int)
                        and 0 <= var[0] < n_p and isinstance(var[1], list) and len(var[1]) >= 2
                        and all(isinstance(i, int) and 0 <= i < n_st for i in var[1])):
                    raise DailyIndexError("index has a variant that does not resolve")
    return len(numbers) + len(lines), mav_index.rank(index, len(raw))


def manifest_rank(man: dict[str, Any]) -> mav_index.IndexRank:
    """The daily index's rank by its manifest, before it is downloaded:
    fitted by its fitted_share. Without one it is taken as fitted (the site
    publishes only indexes the release guard, MAV_MIN_FITTED, passed); the
    download is ranked by its own content before it is written."""
    share = man.get("fitted_share")
    if isinstance(share, (int, float)) and not isinstance(share, bool):
        return mav_index.IndexRank(share >= mav_index.FITTED_MIN, man["size"])
    return mav_index.IndexRank(True, man["size"])


def built_at(man: dict[str, Any]) -> datetime | None:
    """When the manifest's index was built (its built_at, UTC), or None."""
    value = man.get("built_at")
    parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    if parsed is None or parsed.tzinfo is None:
        return None
    return parsed


def decide(
    feed: str,
    out: Path,
    src: Path,
    today: date,
    *,
    state_file: Path | None = None,
    rank: mav_index.IndexRank | None = None,
) -> str:
    """Whether the daily index of feed (of rank, by default of unknown kind
    and size) replaces what out holds.

    Of the same feed, the same-feed rule decides (mav_index.same_feed_replaces):
    UP_TO_DATE unless the daily index beats out. Of another feed:
    UP_TO_DATE when out is of one of the same date or newer; LOCAL_WINS when
    the local feed at src is not older (mav._superseded, as at startup: same
    date, undated or newer keeps it; an expired one does not) and its index
    is the one out holds (_local_served).
    """
    current = mav._index_feed(out) if out.is_file() else None
    if current == feed:
        new = rank or mav_index.IndexRank(None, None)
        return UP_TO_DATE if mav._same_feed_keeps(out, new) else _INSTALL
    new_day, current_day = mav._gtfs_date(feed), mav._gtfs_date(current)
    if new_day is None or (current_day is not None and new_day <= current_day):
        return UP_TO_DATE
    if mav._fingerprint(src) is not None:
        info = mav._source_info(src)
        if not mav._superseded(info, feed, today) and _local_served(
            src, out, state_file or src.with_name(mav.STATE_NAME), info, current
        ):
            return LOCAL_WINS
    return _INSTALL


def _local_served(src: Path, out: Path, state_file: Path, info: Any, current: str | None) -> bool:
    """Whether out is the local feed's index: this integration built it from
    the feed as it is now (the build state's fingerprints), or it is of the
    feed's feed_version. A feed whose build failed, was interrupted or was
    refused by the audit has none, and keeps nothing from the daily index."""
    if info.version and current == info.version:
        return True
    state = mav._read_state(state_file)
    index = mav._fingerprint(out)
    return (
        not state.get("building")
        and index is not None
        and state.get("source") == mav._fingerprint(src)
        and state.get("index") == index
    )


def _log_failure(hass: HomeAssistant, message: str, unsupported: bool = False) -> None:
    """Once per kind of failure; the same one again goes to debug."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("mav_daily_error") == message:
        _LOGGER.debug("Daily MÁV route index: %s", message)
        return
    data["mav_daily_error"] = message
    if unsupported:
        _LOGGER.info("Daily MÁV route index skipped: %s. Update Hungarian transport to use it", message)
    else:
        _LOGGER.warning(
            "Daily MÁV route index not updated: %s. The current index stays; trying again later", message
        )


def _clear_failure(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {}).pop("mav_daily_error", None)


async def async_update(hass: HomeAssistant, www: Path) -> str:
    """One round: fetch the manifest and, when its feed wins, install its index."""
    session = async_get_clientsession(hass)
    out = www / mav.INDEX_NAME
    src = mav.source_path(hass)
    state_file = mav.state_path(hass)
    data = hass.data.setdefault(DOMAIN, {})
    # When this round's manifest was built (timetable_notify.py tells a
    # stale site; DailyIndexUpdater looks again later): this round's only.
    data.pop("mav_daily_built_at", None)
    try:
        man = check_manifest(await _get(session, MANIFEST_URL, MAX_MANIFEST, MANIFEST_TIMEOUT))
    except _Unsupported as err:
        _log_failure(hass, str(err), unsupported=True)
        return UNSUPPORTED
    except DailyIndexError as err:
        _log_failure(hass, str(err))
        return FAILED
    data["mav_daily_built_at"] = built_at(man)
    feed = man["feed"]
    decision = await hass.async_add_executor_job(
        partial(decide, feed, out, src, mav._today(), state_file=state_file, rank=manifest_rank(man))
    )
    if decision != _INSTALL:
        _clear_failure(hass)
        _LOGGER.debug("Daily MÁV route index of feed %s: %s", feed, decision)
        return decision
    if mav.build_pending(hass):
        _LOGGER.debug("Daily MÁV route index of feed %s: a local build runs; trying again later", feed)
        return BUSY
    try:
        # The sha256 in the query: a cache in front of Pages never serves
        # another generation's index for this manifest.
        raw = await _get(session, f"{INDEX_URL}?sha={man['sha256']}", MAX_INDEX, INDEX_TIMEOUT)
        count, rank = await hass.async_add_executor_job(check_index, raw, man, mav.MIN_TRAINS)
    except DailyIndexError as err:
        _log_failure(hass, str(err))
        return FAILED
    # Checked again with nothing awaited before the lock is taken: a build
    # (an upload) that started during the download goes first.
    if mav.build_pending(hass):
        _LOGGER.debug("Daily MÁV route index of feed %s: a local build runs; trying again later", feed)
        return BUSY
    async with mav.build_lock(hass):
        # The index or the local feed may have changed during the download;
        # the rank is the downloaded index's own now.
        decision = await hass.async_add_executor_job(
            partial(decide, feed, out, src, mav._today(), state_file=state_file, rank=rank)
        )
        if decision != _INSTALL:
            _clear_failure(hass)
            return decision
        try:
            await hass.async_add_executor_job(mav_index.write_bytes, raw, out, secrets.token_hex(4))
        except OSError as err:
            _log_failure(hass, f"could not write {out}: {err}")
            return FAILED
    _clear_failure(hass)
    # What timetable_notify.py tells about it.
    data["mav_daily_installed"] = {
        "feed": feed,
        "trains": man["trains"] if isinstance(man.get("trains"), int) else count,
        "lines": man["lines"] if isinstance(man.get("lines"), int) else 0,
    }
    audit = man.get("audit") if isinstance(man.get("audit"), dict) else {}
    _LOGGER.info(
        "Installed the daily MÁV route index of feed %s (%s trains and lines, built %s, audit %s/%s trains)",
        feed, count, man.get("built_at"), audit.get("trains_ok"), audit.get("trains"),
    )
    return INSTALLED


# What scheduled a round.
_FIRST = "first"
_DAILY = "daily"
_RETRY = "retry"
_LATE = "late"


class DailyIndexUpdater:
    """Runs async_update FIRST_DELAY (plus up to FIRST_JITTER) after Home
    Assistant started, or after a reload of the entry (an options save), then
    every day at daily_time, local time, plus up to DAILY_JITTER, until
    stopped. A new time in the options reloads the entry, which stops this
    updater and starts one with the new time.

    A round that FAILED or was BUSY is tried again RETRY_DELAY later, at most
    RETRY_LIMIT times since the last first or daily round. A daily round
    (or one of its retries) that went through while the manifest was older
    than STALE_MANIFEST is tried once more, LATE_DELAY later. One round is
    pending at a time: the daily time replaces a retry still waiting.
    on_result gets each round's result (timetable_notify.py)."""

    def __init__(
        self,
        hass: HomeAssistant,
        www: Path,
        daily_time: time = time(6),
        on_result: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._hass = hass
        self._www = www
        self._time = daily_time
        self._on_result = on_result
        self._unsub: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[Any] | None = None
        self._stopped = False
        self._next = _FIRST
        self._cycle = _FIRST
        self._retries = 0
        self._late_done = False
        self._job = HassJob(self._fire, "hungarian_transport_mav_daily", cancel_on_shutdown=True)

    @callback
    def start(self) -> Callable[[], None]:
        unsub_started = async_at_started(self._hass, self._at_started)
        unsub_daily = async_track_time_change(
            self._hass, self._at_time, hour=self._time.hour, minute=self._time.minute, second=self._time.second
        )

        @callback
        def stop() -> None:
            self._stopped = True
            unsub_started()
            unsub_daily()
            if self._unsub is not None:
                self._unsub()
                self._unsub = None
            if self._task is not None and not self._task.done():
                self._task.cancel()

        return stop

    async def _at_started(self, _hass: HomeAssistant) -> None:
        self._schedule(FIRST_DELAY + random.uniform(0, FIRST_JITTER), _FIRST)

    @callback
    def _at_time(self, _now: Any) -> None:
        self._schedule(random.uniform(0, DAILY_JITTER), _DAILY)

    @callback
    def _schedule(self, delay: float, kind: str) -> None:
        if self._stopped:
            return
        # One round pending at a time: the daily time replaces a first round
        # or a retry still waiting, and the other way round.
        if self._unsub is not None:
            self._unsub()
        self._next = kind
        self._unsub = async_call_later(self._hass, delay, self._job)

    @callback
    def _fire(self, _now: Any) -> None:
        self._unsub = None
        if self._task is not None and not self._task.done():
            # The previous round still runs (a slow download): it is this one.
            return
        kind = self._next
        if kind in (_FIRST, _DAILY):
            # A new cycle: its own retries, and at most one late look.
            self._cycle = kind
            self._retries = 0
            self._late_done = False
        self._task = self._hass.async_create_background_task(self._run(), "hungarian_transport_mav_daily")

    @callback
    def _after(self, result: str) -> None:
        """Schedule a retry or a late look after a round, unless another
        round is already pending (the daily time came during this one)."""
        if self._stopped or self._unsub is not None:
            return
        if result in (BUSY, FAILED):
            if self._retries < RETRY_LIMIT:
                self._retries += 1
                self._schedule(RETRY_DELAY, _RETRY)
            return
        if self._cycle != _DAILY or self._late_done or result == UNSUPPORTED:
            return
        built = self._hass.data.get(DOMAIN, {}).get("mav_daily_built_at")
        if built is not None and dt_util.utcnow() - built > STALE_MANIFEST:
            self._late_done = True
            self._schedule(random.uniform(*LATE_DELAY), _LATE)

    async def _run(self) -> None:
        try:
            result = await async_update(self._hass, self._www)
        except asyncio.CancelledError:
            # Stopped (unloaded, Home Assistant stopping): no next round.
            raise
        except Exception as err:  # noqa: BLE001 - logged once, the next round tries again
            _log_failure(self._hass, f"{type(err).__name__}: {err}")
            _LOGGER.debug("Daily MÁV route index round failed", exc_info=True)
            result = FAILED
        self._after(result)
        if self._on_result is None:
            return
        try:
            await self._on_result(result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a notification never stops the updater
            _LOGGER.warning("Timetable update notification failed", exc_info=True)
