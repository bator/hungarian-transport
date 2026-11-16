"""The weekly Volán and city indexes from GitHub Pages.

Releases are for code only; the timetable data moves on its own. The
repository's daily Pages workflow (.github/workflows/mav-index-daily.yml,
scripts/pages_timetables.py) rebuilds the Volán and city indexes once a week
and publishes them on the same site as the daily MÁV index, with a manifest,
timetables.json. This module reads that manifest in the rounds of the daily
MÁV check (mav_daily.DailyIndexUpdater: a while after startup, then daily at
CONF_DAILY_TIME), after its MÁV part, and installs an index into www/ only
when:

- the manifest's format is the one this code reads and its schema is
  INDEX_SCHEMA, the one the cards read (else it says so once and installs
  nothing: an older or newer integration publishes for other cards);
- its feed is newer than the one of the index www/ serves (decide): for the
  Volán index a later dated feed (served_index.newer_feed); for the city
  index, which joins one feed per operator, no operator on both sides has
  an older dated feed on Pages, and at least one has a later one, a feed
  that changed and cannot be dated on both sides, or the operator set
  differs. An index missing from www/ is always installed;
- and, when the local updater script (scripts/update_volan_index.py) keeps
  a durable copy of the index in /config/hungarian-transport, the Pages
  index is newer than that copy by the script's own rule
  (served_feeds_newer: every operator of the copy still there, none older,
  one later, an undated one unchanged). Otherwise the script would put its
  copy back on its next run, and the two would take turns: the local
  rebuild wins (LOCAL_WINS);
- everything downloaded is what the manifest describes: the index by size
  and sha256, one gzip member whose head has the schema, the feed and the
  shapes folder ("sh") the manifest names; every shard of that folder by
  size and sha256. Nothing in www/ changes before all of it checked out.

The install is atomic for the card: the shapes folder (named by a content
hash, immutable) is written as www/<dir>.tmp<hex> and renamed into place
first (kept when it is already there and verifies), then the index replaces
the old one by a temp file and a rename (mav_index.write_bytes). The card
fetches the index with a day-stamped URL and keeps the one it loaded until
midnight or a reload, asking for that index's shards all day: a shapes
folder the served index no longer names is marked retired (.retired) and
removed SHAPES_GRACE later, as scripts/gtfs_compact.prune_shapes does. A
round prunes both stems every time, the index installed or not.

Downloads are bounded: the manifest MAX_MANIFEST, an index MAX_INDEX (read
into memory to check it), a shard MAX_SHARD, written to disk as it comes,
at most CONCURRENCY at a time, and an index with its shards MAX_TOTAL.
GitHub Pages' cache (mav_daily.PAGES_MAX_AGE) can pair a manifest with the
other generation's files right after a deploy: the first download of an
index entry that does not match its manifest (size, sha256) is looked at
again mav_daily.PAGES_SETTLE_DELAY later, quietly (SETTLING); the same
entry mismatching again is FAILED. A failed round keeps www/ as it is,
logs once per kind of failure and is tried again RETRY_DELAY later, at most
RETRY_LIMIT times until the next first or daily round. The options flow can
turn it off (CONF_PAGES_TIMETABLES); then no request is made at all.

Nothing else in the integration writes these files; the rounds take
write_lock all the same, so a retry and a daily round never write at once.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import logging
import os
import re
import secrets
import shutil
import time as time_mod
import zlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

import aiohttp

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later

from . import jsonsafe, mav, mav_daily, mav_index, served_index, storage, timetables
from .const import DOMAIN, INDEX_SCHEMA

_LOGGER = logging.getLogger(__name__)

MANIFEST_URL = f"{mav_daily.PAGES_BASE}/timetables.json"
# The manifest format scripts/pages_timetables.py writes.
MANIFEST_FORMAT = 1
# The indexes the manifest may name, and the file each is in www/.
INDEXES = {"volan": timetables.VOLAN_INDEX, "city": timetables.CITY_INDEX}
MAX_MANIFEST = 64 * 1024
# The Volán index is about 8 MB gzipped, the city one about 15 MB.
MAX_INDEX = 48 * 1024 * 1024
MAX_SHARD = 8 * 1024 * 1024
MAX_TOTAL = 160 * 1024 * 1024
MAX_SHARDS = 256
# What an index may unpack to while it is checked (tens of MB today).
MAX_INDEX_JSON = 1024 * 1024 * 1024
MAX_FEED = 4096
MANIFEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
INDEX_TIMEOUT = aiohttp.ClientTimeout(total=300)
SHARD_TIMEOUT = aiohttp.ClientTimeout(total=120)
CONCURRENCY = 4
# A shard is written to disk whenever this much of it has come.
FLUSH_BYTES = 1024 * 1024
UNPACK_CHUNK = 1024 * 1024
# After a FAILED round: again this much later, at most RETRY_LIMIT times
# until the next first or daily round. The data changes once a week.
RETRY_DELAY = 30 * 60
RETRY_LIMIT = 3
# scripts/gtfs_compact.py SHAPES_GRACE_S and SHAPES_RETIRED: a dashboard
# left open asks for its index's shards until midnight.
SHAPES_GRACE = 26 * 3600
SHAPES_RETIRED = ".retired"
# A temp folder or file ("." in the name) younger than this may be another
# writer's (the local updater building right now); an older one is a leftover.
TEMP_MIN_AGE = 3600
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# The head every index of scripts/gtfs_compact.write_index starts with:
# {"v":N,"sh":{"dir":...,"n":N},"feed":... ("sh" only with shapes).
HEAD_RE = re.compile(rb'\{"v":(\d{1,6}),(?:"sh":(\{"dir":"[^"\\]{1,128}","n":\d{1,4}\}),)?"feed":')

# What a round did for an index; the ones mav_daily has mean the same here.
INSTALLED = mav_daily.INSTALLED
UP_TO_DATE = mav_daily.UP_TO_DATE
LOCAL_WINS = mav_daily.LOCAL_WINS
SUPERSEDED = mav_daily.SUPERSEDED
UNSUPPORTED = mav_daily.UNSUPPORTED
FAILED = mav_daily.FAILED
# The manifest has no entry for the index (never built, or dropped).
NOT_PUBLISHED = "not_published"
# Only between async_update and PagesTimetables, never recorded: the
# download did not match its manifest for the first time (PAGES_MAX_AGE).
SETTLING = mav_daily.SETTLING
_INSTALL = "install"


@dataclass(frozen=True)
class IndexResult:
    """What a round did for one index, and the manifest's feed for it."""

    result: str
    feed: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TimetablesRound:
    """One round: per index name, its IndexResult."""

    results: Mapping[str, IndexResult] = field(default_factory=dict)
    built_at: datetime | None = None

    @property
    def installed(self) -> list[str]:
        return [name for name, res in self.results.items() if res.result == INSTALLED]

    @property
    def settling(self) -> bool:
        return any(res.result == SETTLING for res in self.results.values())

    @property
    def failed(self) -> bool:
        return any(res.result == FAILED for res in self.results.values())


class _Unsupported(mav_daily.DailyIndexError):
    """A manifest format or index schema this version does not read."""


class _Mismatch(mav_daily.DailyIndexError):
    """A download is not the file its manifest entry describes."""


def _sized(value: Any, limit: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= limit


def check_manifest(raw: bytes) -> dict[str, Any]:
    """The manifest, its format and schema checked; its "indexes" a dict."""
    try:
        man = jsonsafe.loads(raw)
    except (ValueError, RecursionError) as err:
        raise mav_daily.DailyIndexError(f"timetables.json is not JSON: {err}") from err
    if not isinstance(man, dict):
        raise mav_daily.DailyIndexError("timetables.json is not a JSON object")
    if man.get("format") != MANIFEST_FORMAT:
        raise _Unsupported(f"manifest format {man.get('format')!r}, this version reads {MANIFEST_FORMAT}")
    if man.get("schema") != INDEX_SCHEMA:
        raise _Unsupported(f"index schema {man.get('schema')!r}, this version reads {INDEX_SCHEMA}")
    indexes = man.get("indexes", {})
    if not isinstance(indexes, dict):
        raise mav_daily.DailyIndexError("timetables.json has no indexes")
    return man


def check_entry(name: str, entry: Any) -> dict[str, Any]:
    """One index's manifest entry, checked and reduced to what is used:
    feed, size, sha256 and shapes (None, or dir, n and files name ->
    (size, sha256))."""
    if not isinstance(entry, dict):
        raise mav_daily.DailyIndexError(f"{name}: manifest entry is not an object")
    feed, size, sha = entry.get("feed"), entry.get("size"), entry.get("sha256")
    if not isinstance(feed, str) or not feed or len(feed) > MAX_FEED:
        raise mav_daily.DailyIndexError(f"{name}: manifest names no feed")
    if name == "city":
        if not timetables.city_feeds(feed):
            raise mav_daily.DailyIndexError(f"{name}: manifest feed {feed[:80]!r} names no operator")
    elif served_index.feed_date(feed) is None:
        raise mav_daily.DailyIndexError(f"{name}: manifest names no dated feed: {feed[:80]!r}")
    if not _sized(size, MAX_INDEX):
        raise mav_daily.DailyIndexError(f"{name}: manifest size {size!r} out of range")
    if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
        raise mav_daily.DailyIndexError(f"{name}: manifest has no sha256")
    shapes = entry.get("shapes")
    checked: dict[str, Any] | None = None
    if shapes is not None:
        if not isinstance(shapes, dict):
            raise mav_daily.DailyIndexError(f"{name}: manifest shapes is not an object")
        stem = INDEXES[name].removesuffix("-index.json.gz")
        folder, count, files = shapes.get("dir"), shapes.get("n"), shapes.get("files")
        # A content hash, never a "." (prune_shapes takes those for temp folders).
        if not isinstance(folder, str) or not re.fullmatch(rf"{stem}-shapes-[0-9a-f]{{6,64}}", folder):
            raise mav_daily.DailyIndexError(f"{name}: manifest shapes folder {folder!r} is not one of {stem}")
        if not _sized(count, MAX_SHARDS) or not isinstance(files, dict):
            raise mav_daily.DailyIndexError(f"{name}: manifest shapes lack their shard list")
        names = {f"{k:02d}.json.gz" for k in range(count)}
        if set(files) != names:
            raise mav_daily.DailyIndexError(f"{name}: manifest lists other shards than 00..{count - 1:02d}")
        shards: dict[str, tuple[int, str]] = {}
        for shard, info in sorted(files.items()):
            if not (isinstance(info, dict) and _sized(info.get("size"), MAX_SHARD)
                    and isinstance(info.get("sha256"), str) and SHA256_RE.fullmatch(info["sha256"])):
                raise mav_daily.DailyIndexError(f"{name}: manifest shard {shard} has no size or sha256")
            shards[shard] = (info["size"], info["sha256"])
        if size + sum(s for s, _ in shards.values()) > MAX_TOTAL:
            raise mav_daily.DailyIndexError(f"{name}: index and shapes are more than {MAX_TOTAL} bytes")
        checked = {"dir": folder, "n": count, "files": shards}
    return {"feed": feed, "size": size, "sha256": sha, "shapes": checked}


def check_index(raw: bytes, entry: Mapping[str, Any]) -> None:
    """Raise unless raw is the index entry describes: its size and sha256
    (_Mismatch), one gzip member, and a head of INDEX_SCHEMA, entry's feed
    and entry's shapes folder. Unpacked chunk by chunk, nothing kept but
    the head."""
    if len(raw) != entry["size"]:
        raise _Mismatch(f"index is {len(raw)} bytes, the manifest says {entry['size']}")
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise _Mismatch("index sha256 does not match the manifest")
    unzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
    head = bytearray()
    total = 0
    pos = 0
    pending = b""
    try:
        while not unzip.eof:
            if not pending:
                if pos >= len(raw):
                    # Input used up: whatever zlib still holds back.
                    out = unzip.decompress(b"", UNPACK_CHUNK)
                    if not out:
                        break
                else:
                    pending = raw[pos:pos + UNPACK_CHUNK]
                    pos += UNPACK_CHUNK
                    continue
            else:
                out = unzip.decompress(pending, UNPACK_CHUNK)
                pending = unzip.unconsumed_tail
            total += len(out)
            if total > MAX_INDEX_JSON:
                raise mav_daily.DailyIndexError(f"index unpacks to more than {MAX_INDEX_JSON} bytes")
            if len(head) < served_index.HEAD_BYTES:
                head += out[: served_index.HEAD_BYTES - len(head)]
    except zlib.error as err:
        raise mav_daily.DailyIndexError(f"index does not unpack: {err}") from err
    if not unzip.eof:
        raise mav_daily.DailyIndexError("index is truncated")
    if unzip.unused_data or pending or pos < len(raw):
        # Another gzip member (or junk) after the first: checked would be
        # only the first, served the whole file.
        raise mav_daily.DailyIndexError("index has data after its gzip stream")
    match = HEAD_RE.match(bytes(head))
    if match is None:
        raise mav_daily.DailyIndexError("index does not start with its schema and feed")
    if int(match.group(1)) != INDEX_SCHEMA:
        raise mav_daily.DailyIndexError(f"index schema {match.group(1).decode()}, the manifest says {INDEX_SCHEMA}")
    feed = served_index.head_feed(bytes(head))
    if feed != entry["feed"]:
        raise mav_daily.DailyIndexError(f"index is of feed {str(feed)[:80]!r}, the manifest names {entry['feed'][:80]!r}")
    shapes = entry["shapes"]
    sh = jsonsafe.loads(match.group(2)) if match.group(2) else None
    want = {"dir": shapes["dir"], "n": shapes["n"]} if shapes else None
    if sh != want:
        raise mav_daily.DailyIndexError(f"index names shapes {sh!r}, the manifest {want!r}")


def city_newer(pages: str, installed: str | None) -> bool:
    """Whether the city index of feed pages replaces one of installed: no
    operator on both sides has an older dated feed in pages, and one has a
    later one, a feed that changed and is not dated on both sides, or the
    operator set differs. Nothing replaces an index by the same feed."""
    if installed is None:
        return True
    new, old = timetables.city_feeds(pages), timetables.city_feeds(installed)
    if not new or new == old:
        return False
    changed = set(new) != set(old)
    for op in new.keys() & old.keys():
        day, old_day = served_index.feed_date(new[op]), served_index.feed_date(old[op])
        if day is not None and old_day is not None:
            if day < old_day:
                return False
            changed |= day > old_day
        elif new[op] != old[op]:
            changed = True
    return changed


def updater_newer(pages: str, durable: str | None) -> bool:
    """Whether pages beats the local updater's durable copy of feed durable
    by the updater's own rule (scripts/update_volan_index.served_feeds_newer):
    every operator of the copy is in pages, of those dated on both sides
    none is older and one later, and an undated one is the same feed."""
    old, new = timetables.city_feeds(durable), timetables.city_feeds(pages)
    if not old:
        return False
    later = False
    for op, version in old.items():
        other = new.get(op)
        if other is None:
            return False
        day, other_day = served_index.feed_date(version), served_index.feed_date(other)
        if day is None or other_day is None:
            if other != version:
                return False
            continue
        if other_day < day:
            return False
        later |= other_day > day
    return later


def decide(name: str, feed: str, www: Path, durable: Path) -> str:
    """Whether the Pages index name of feed replaces what www/ serves:
    _INSTALL, else UP_TO_DATE (the same feed), LOCAL_WINS (the local
    updater's durable copy in durable/ is not older by its own rule) or
    SUPERSEDED (www/ serves another index Pages' does not beat)."""
    out = www / INDEXES[name]
    if not out.is_file():
        return _INSTALL
    current = served_index.read_feed(out)
    if current == feed:
        return UP_TO_DATE
    copy = durable / INDEXES[name]
    # A served index that does not read is replaced: the updater would put
    # its copy back anyway, and until then the cards have one.
    if current is not None and copy.is_file():
        kept = served_index.read_feed(copy)
        if kept is not None and kept != feed and not updater_newer(feed, kept):
            return LOCAL_WINS
    newer = city_newer(feed, current) if name == "city" else served_index.newer_feed(feed, current)
    return _INSTALL if newer else SUPERSEDED


def shapes_folder(path: Path) -> str | None:
    """The shapes folder an index file names in its head, or None."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(512)
    except (OSError, EOFError, zlib.error):
        return None
    match = HEAD_RE.match(head)
    if match is None or not match.group(2):
        return None
    sh = jsonsafe.loads(match.group(2))
    return sh.get("dir") if isinstance(sh, dict) else None


def prune(www: Path, name: str, keep: str | None, now: float | None = None) -> None:
    """Retire and later remove the shapes folders of name's stem that the
    served index does not use (scripts/gtfs_compact.prune_shapes): the
    first prune that finds one unused marks it (SHAPES_RETIRED), one past
    SHAPES_GRACE removes it, and a folder in use again loses its mark.
    Temp folders, and temp indexes of mav_index.write_bytes, go once they
    are TEMP_MIN_AGE old. Nothing when www/ serves no such index: what it
    would keep is not known."""
    out = www / INDEXES[name]
    if not out.is_file():
        return
    now = time_mod.time() if now is None else now
    prefix = INDEXES[name].removesuffix("-index.json.gz") + "-shapes-"
    for entry in www.glob(prefix + "*"):
        try:
            if not entry.is_dir():
                continue
            if "." in entry.name[len(prefix):]:
                if now - entry.stat().st_mtime >= TEMP_MIN_AGE:
                    shutil.rmtree(entry, ignore_errors=True)
                continue
            marker = entry / SHAPES_RETIRED
            if entry.name == keep:
                marker.unlink(missing_ok=True)
                continue
            try:
                retired = marker.stat().st_mtime
            except FileNotFoundError:
                marker.touch()
                os.utime(marker, (now, now))
                continue
            if now - retired >= SHAPES_GRACE:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError as err:
            _LOGGER.debug("Could not prune %s: %s", entry, err)
    for temp in www.glob(INDEXES[name] + ".*"):
        try:
            if mav_index.TEMP_SUFFIX_RE.fullmatch(temp.name[len(INDEXES[name]) + 1:]) and (
                now - temp.stat().st_mtime >= TEMP_MIN_AGE
            ):
                temp.unlink()
        except OSError:
            pass


def _verify_folder(folder: Path, files: Mapping[str, tuple[int, str]]) -> bool:
    """Whether folder holds exactly these shards, by size and sha256."""
    try:
        for shard, (size, sha) in files.items():
            path = folder / shard
            if path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                return False
    except OSError:
        return False
    return True


def _staging(www: Path, shapes: Mapping[str, Any] | None, token: str) -> Path | None:
    """The temp folder the shards are downloaded into, or None when there
    are none or www/ has the folder already, verified."""
    if shapes is None:
        return None
    final = www / shapes["dir"]
    if final.is_dir() and _verify_folder(final, shapes["files"]):
        return None
    tmp = www / f"{shapes['dir']}.tmp{token}"
    tmp.mkdir(mode=0o755)
    os.chmod(tmp, 0o755)
    return tmp


def _commit(www: Path, name: str, raw: bytes, shapes: Mapping[str, Any] | None, tmp: Path | None,
            token: str, now: float | None = None) -> None:
    """Put the shapes folder in place, then the index; then prune."""
    if tmp is not None:
        final = www / shapes["dir"]
        if final.is_dir():
            if _verify_folder(final, shapes["files"]):
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                # A broken copy of the same name: swapped for the verified one.
                old = www / f"{shapes['dir']}.old{token}"
                os.replace(final, old)
                os.replace(tmp, final)
                shutil.rmtree(old, ignore_errors=True)
        else:
            os.replace(tmp, final)
    mav_index.write_bytes(raw, www / INDEXES[name], token)
    prune(www, name, shapes["dir"] if shapes else None, now)


def _prune_served(www: Path, name: str) -> None:
    prune(www, name, shapes_folder(www / INDEXES[name]))


class _ShardWriter:
    """One shard, streamed to disk in the executor, hashed on the way."""

    def __init__(self, hass: HomeAssistant, path: Path) -> None:
        self._hass = hass
        self._path = path
        self._buf = bytearray()
        self._sha = hashlib.sha256()
        self._fh: Any = None
        self.size = 0

    def _write(self, data: bytes) -> None:
        if self._fh is None:
            self._fh = self._path.open("wb")
        self._fh.write(data)
        self._sha.update(data)

    def _close(self) -> None:
        if self._fh is None:
            self._fh = self._path.open("wb")
        self._fh.close()

    async def add(self, chunk: bytes) -> None:
        self._buf += chunk
        self.size += len(chunk)
        if len(self._buf) >= FLUSH_BYTES:
            data, self._buf = bytes(self._buf), bytearray()
            await self._hass.async_add_executor_job(self._write, data)

    async def finish(self) -> str:
        data, self._buf = bytes(self._buf), bytearray()
        await self._hass.async_add_executor_job(self._write, data)
        await self._hass.async_add_executor_job(self._close)
        return self._sha.hexdigest()

    async def abort(self) -> None:
        if self._fh is not None:
            await self._hass.async_add_executor_job(self._fh.close)


async def _download_shard(
    hass: HomeAssistant, session: aiohttp.ClientSession, url: str, path: Path, size: int, sha: str
) -> None:
    writer = _ShardWriter(hass, path)
    try:
        await mav_daily._get(session, url, size, SHARD_TIMEOUT, sink=writer.add)
        digest = await writer.finish()
    except mav_daily.TooLarge as err:
        await writer.abort()
        raise _Mismatch(str(err)) from err
    except BaseException:
        await writer.abort()
        raise
    if writer.size != size:
        raise _Mismatch(f"{path.name} is {writer.size} bytes, the manifest says {size}")
    if digest != sha:
        raise _Mismatch(f"{path.name} sha256 does not match the manifest")


async def _download_shards(
    hass: HomeAssistant, session: aiohttp.ClientSession, shapes: Mapping[str, Any], tmp: Path
) -> None:
    """Every shard into tmp, CONCURRENCY at a time; the first failure stops
    the others and is raised."""
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(shard: str, size: int, sha: str) -> None:
        async with gate:
            url = f"{mav_daily.PAGES_BASE}/{shapes['dir']}/{shard}"
            await _download_shard(hass, session, url, tmp / shard, size, sha)

    try:
        async with asyncio.TaskGroup() as group:
            for shard, (size, sha) in shapes["files"].items():
                group.create_task(one(shard, size, sha))
    except BaseExceptionGroup as errors:
        # Mismatches first: the round settles rather than fails.
        found = [e for e in errors.exceptions if isinstance(e, _Mismatch)] or list(errors.exceptions)
        raise found[0] from None


# hass.data[DOMAIN] keys, only this module reads them.
_LOGGED = "pages_timetables_logged"
_MISMATCHED = "pages_timetables_mismatched"
_LOCK = "pages_timetables_lock"


def write_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Held by every round while it reads and writes the Volán and city files."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_LOCK, asyncio.Lock())


def _log_failure(hass: HomeAssistant, scope: str, message: str, unsupported: bool = False) -> None:
    """Once per kind of failure of scope (the manifest, an index); the same
    one again goes to debug."""
    logged = hass.data.setdefault(DOMAIN, {}).setdefault(_LOGGED, {})
    if logged.get(scope) == message:
        _LOGGER.debug("Weekly timetables from GitHub Pages: %s", message)
        return
    logged[scope] = message
    if unsupported:
        _LOGGER.info("Weekly timetables from GitHub Pages skipped: %s. Update Hungarian transport to use them",
                     message)
    else:
        _LOGGER.warning(
            "Weekly %s timetable from GitHub Pages not installed: %s. The current index stays; trying again later",
            scope, message,
        )


def _clear_failure(hass: HomeAssistant, scope: str) -> None:
    hass.data.setdefault(DOMAIN, {}).setdefault(_LOGGED, {}).pop(scope, None)


def _first_mismatch(hass: HomeAssistant, name: str, sha: str) -> bool:
    """Whether this manifest entry's download did not match it for the first time."""
    seen = hass.data.setdefault(DOMAIN, {}).setdefault(_MISMATCHED, {})
    if seen.get(name) == sha:
        return False
    seen[name] = sha
    return True


async def async_update(hass: HomeAssistant, www: Path) -> TimetablesRound:
    """One round: the manifest, then each index it names that wins."""
    session = async_get_clientsession(hass)
    try:
        man = check_manifest(await mav_daily._get(session, MANIFEST_URL, MAX_MANIFEST, MANIFEST_TIMEOUT))
    except _Unsupported as err:
        _log_failure(hass, "manifest", str(err), unsupported=True)
        return TimetablesRound({name: IndexResult(UNSUPPORTED, error=str(err)) for name in INDEXES})
    except mav_daily.DailyIndexError as err:
        _log_failure(hass, "manifest", str(err))
        return TimetablesRound({name: IndexResult(FAILED, error=str(err)) for name in INDEXES})
    _clear_failure(hass, "manifest")
    durable = mav.source_path(hass).parent
    results: dict[str, IndexResult] = {}
    async with write_lock(hass):
        for name in INDEXES:
            entry = man["indexes"].get(name)
            try:
                if entry is None:
                    await hass.async_add_executor_job(_prune_served, www, name)
                    results[name] = IndexResult(NOT_PUBLISHED)
                    continue
                results[name] = await _update_index(hass, session, name, entry, www, durable)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a crashed index is a failed one
                message = f"{type(err).__name__}: {err}"
                _log_failure(hass, name, message)
                _LOGGER.debug("Weekly %s timetable round failed", name, exc_info=err)
                results[name] = IndexResult(FAILED, entry.get("feed") if isinstance(entry, dict) else None, message)
    return TimetablesRound(results, mav_daily.built_at(man))


async def _update_index(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    name: str,
    raw_entry: Any,
    www: Path,
    durable: Path,
) -> IndexResult:
    try:
        entry = check_entry(name, raw_entry)
    except mav_daily.DailyIndexError as err:
        _log_failure(hass, name, str(err))
        return IndexResult(FAILED, error=str(err))
    feed = entry["feed"]

    def failed(err: Exception) -> IndexResult:
        _log_failure(hass, name, str(err))
        return IndexResult(FAILED, feed, str(err))

    decision = await hass.async_add_executor_job(decide, name, feed, www, durable)
    if decision != _INSTALL:
        await hass.async_add_executor_job(_prune_served, www, name)
        _clear_failure(hass, name)
        _LOGGER.debug("Weekly %s timetable of feed %s: %s", name, feed[:80], decision)
        return IndexResult(decision, feed)
    token = secrets.token_hex(4)
    tmp: Path | None = None
    try:
        try:
            raw = await mav_daily._get(
                session, f"{mav_daily.PAGES_BASE}/{INDEXES[name]}", entry["size"], INDEX_TIMEOUT
            )
            await hass.async_add_executor_job(check_index, raw, entry)
            tmp = await hass.async_add_executor_job(_staging, www, entry["shapes"], token)
            if tmp is not None:
                await _download_shards(hass, session, entry["shapes"], tmp)
        except (_Mismatch, mav_daily.TooLarge) as err:
            if not _first_mismatch(hass, name, entry["sha256"]):
                return failed(err)
            # Right after a deploy the Pages cache can still hold the other
            # generation of a file (mav_daily.PAGES_MAX_AGE).
            _LOGGER.debug(
                "Weekly %s timetable of feed %s: %s; the GitHub Pages cache may still hold another"
                " generation, looking again in %d s", name, feed[:80], err, mav_daily.PAGES_SETTLE_DELAY,
            )
            return IndexResult(SETTLING, feed, str(err))
        except mav_daily.DailyIndexError as err:
            return failed(err)
        # www/ may have changed during the download (the local updater).
        decision = await hass.async_add_executor_job(decide, name, feed, www, durable)
        if decision != _INSTALL:
            _clear_failure(hass, name)
            return IndexResult(decision, feed)
        try:
            await hass.async_add_executor_job(_commit, www, name, raw, entry["shapes"], tmp, token)
        except OSError as err:
            return failed(mav_daily.DailyIndexError(f"could not write {INDEXES[name]}: {err}"))
        tmp = None
    finally:
        if tmp is not None:
            await hass.async_add_executor_job(partial(shutil.rmtree, tmp, ignore_errors=True))
    _clear_failure(hass, name)
    await storage.async_record_timetables_install(hass, name, feed, entry["sha256"], entry["size"])
    _LOGGER.info("Installed the weekly %s timetable of feed %s from GitHub Pages", name, feed[:200])
    return IndexResult(INSTALLED, feed)


class PagesTimetables:
    """The rounds of the weekly timetables: run by mav_daily.DailyIndexUpdater
    in its first and daily rounds (async_run), with retries of its own.

    A round with a FAILED index is tried again RETRY_DELAY later, at most
    RETRY_LIMIT times since the last first or daily round; one that is
    SETTLING is looked at again mav_daily.PAGES_SETTLE_DELAY later, using
    up no retry. A first or daily round replaces a retry still waiting.
    on_result gets each round (timetable_notify.py); then the indexes it
    settled are recorded for the sensors (storage.PAGES_ROUND)."""

    def __init__(
        self,
        hass: HomeAssistant,
        www: Path,
        on_result: Callable[[TimetablesRound], Awaitable[None]] | None = None,
    ) -> None:
        self._hass = hass
        self._www = www
        self._on_result = on_result
        self._retries = 0
        self._unsub: CALLBACK_TYPE | None = None
        self._task: asyncio.Task[Any] | None = None
        self._running = False
        self._stopped = False
        self._job = HassJob(self._fire, "hungarian_transport_pages_timetables", cancel_on_shutdown=True)

    @callback
    def stop(self) -> None:
        self._stopped = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def async_run(self) -> None:
        """A first or daily round: its own retries from now on."""
        if self._stopped:
            return
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._retries = 0
        await self._round()

    @callback
    def _schedule(self, delay: float) -> None:
        if self._stopped:
            return
        if self._unsub is not None:
            self._unsub()
        self._unsub = async_call_later(self._hass, delay, self._job)

    @callback
    def _fire(self, _now: Any) -> None:
        self._unsub = None
        if self._stopped or self._running:
            return
        self._task = self._hass.async_create_background_task(self._round(), "hungarian_transport_pages_timetables")

    async def _round(self) -> None:
        if self._running:
            # The previous round still runs (a slow download): it is this one.
            return
        self._running = True
        try:
            done = await async_update(self._hass, self._www)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - logged once, the next round tries again
            message = f"{type(err).__name__}: {err}"
            _log_failure(self._hass, "round", message)
            done = TimetablesRound({name: IndexResult(FAILED, error=message) for name in INDEXES})
        finally:
            self._running = False
        if self._stopped:
            return
        if done.settling:
            self._schedule(mav_daily.PAGES_SETTLE_DELAY)
        elif done.failed and self._retries < RETRY_LIMIT:
            self._retries += 1
            self._schedule(RETRY_DELAY)
        if self._on_result is not None:
            try:
                await self._on_result(done)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a notification never stops the rounds
                _LOGGER.warning("Timetable update notification failed", exc_info=True)
        await storage.async_record_pages_round(
            self._hass,
            {name: (res.result, res.feed) for name, res in done.results.items() if res.result != SETTLING},
        )
