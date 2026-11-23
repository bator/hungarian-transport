"""The weekly Volán and city indexes from GitHub Pages.

Releases are for code only; the timetable data moves on its own. The
repository's daily Pages workflow (.github/workflows/mav-index-daily.yml,
scripts/pages_timetables.py) rebuilds the Volán and city indexes once a week
and publishes them on the same site as the daily MÁV index, with a manifest,
timetables.json, and its signature, timetables.json.sig. This module reads
that manifest in the rounds of the daily MÁV check
(mav_daily.DailyIndexUpdater: a while after startup, then daily at
CONF_DAILY_TIME), after its MÁV part, and installs an index into www/ only
when:

- the manifest is signed by the workflow's key (verify_manifest: ed25519,
  one of mav_daily.PAGES_PUBLIC_KEYS, over the exact bytes; the private key
  is a secret of the repository's Pages workflow only; mav-index.json is
  signed the same way). A missing or bad signature installs nothing: the
  manifest names the sha256 of everything else, so the signature is what
  ties the whole download to the repository rather than to whoever answers
  for bator.github.io. A well-formed signature none of the keys verifies
  (reason "unknown_key": signed by a key this version does not know: a
  newer key, another deploy's signature, or not the site's), also when
  looked at again (SETTLING), is FAILED, with the normal retries, until it
  has been so on more than mav_daily.UNKNOWN_KEY_DAYS consecutive days:
  then UNSUPPORTED (an update of the integration may read it), said once at
  info, with no retries;
- the manifest's format is the one this code reads and its schema is
  INDEX_SCHEMA, the one the cards read (else it says so once and installs
  nothing: an older or newer integration publishes for other cards);
- its feed is newer than the one of the index www/ serves (decide): for the
  Volán index a later dated feed; for the city index, which joins one feed
  per operator, no operator on both sides has an older dated feed on Pages,
  and at least one has a later one, a feed that changed and cannot be dated
  on both sides, or the operator set differs (city_newer). An index missing
  from www/ is always installed. A date more than MAX_FEED_AHEAD ahead is
  no date: Pages' entry is refused, and a served one counts as older;
- when www/ serves a release's city index (neither one this module
  installed nor the local updater's copy), the entry names release_version
  (the stable release whose index the Pages build started from, "v1.10.24")
  and this version is newer than that release (a later stable one the site
  has not rebuilt against yet, or a -rev beta two patches up:
  release_newer): this version's index may hold what the Pages build knew
  nothing of (an operator it added, an undated feed it updated, which the
  operator rule above takes for news). Then only a Pages index newer by the
  updater's strict rule (updater_newer: every operator still there, none
  older, an undated one unchanged, one later) replaces it; else SUPERSEDED,
  reason "release_newer", until the site rebuilds against this release, or
  until an operator both have is dated more than RELEASE_STALE older in
  the served index than in the Pages one (release_stale: a -rev beta is
  never a baseline, and would wait for the next stable release): then the
  operator rule decides. A
  version not newer than release_version (one that has not updated to it
  yet, or that release itself), or an entry without one, goes by the
  operator rule: the build knew this version's index or a newer one. The
  Volán index is one dated feed: newer is newer;
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
hash, immutable) is written as www/.ht-staging-<dir>-<key> and renamed into
place first (a name outside <stem>-shapes-*, which the local updater's
prune_shapes removes at once when it has a dot: it would take the folder
from under a download) (kept when it is already there and verifies), then
the index replaces the old one by a temp file and a rename
(mav_index.write_bytes), its mtime set to the entry's built_at (the sensors'
build time: the index's gzip header has none). <key> is of the entry's feed
and sha256: a round that failed keeps the shards it verified there, and so
does one a stop cancelled during the download or after it, before the
commit (an unload, a reload, Home Assistant stopping), and the next try of
the same entry downloads only the rest. The card fetches the
index with a day-stamped URL and keeps the one it loaded until midnight or
a reload, asking for that index's shards all day: a shapes folder the
served index no longer names is marked retired (.retired) and removed
SHAPES_GRACE later, as scripts/gtfs_compact.prune_shapes does. A round
that reads a signed manifest of this format prunes the stem of each index
it decides about, installed or not (also one the manifest leaves out); one
that stops before (the manifest or its signature failed, or is
UNSUPPORTED) or whose download fails prunes nothing, and the next such
round, or the next setup of the entry (prune_all, the option on or off),
does; a staging folder goes once it is TEMP_MIN_AGE old.

Downloads are bounded: the manifest MAX_MANIFEST, its signature
MAX_SIGNATURE, an index MAX_INDEX (read into memory to check it), a shard
MAX_SHARD, written to disk as it comes, at most CONCURRENCY at a time, and
an index with its shards MAX_TOTAL. GitHub Pages' cache
(mav_daily.PAGES_MAX_AGE) can pair a manifest with the other generation's
files right after a deploy: the first download of a file that does not
match its manifest (size, sha256, a 404 of a file it names, a signature
that does not verify) is looked at again mav_daily.PAGES_SETTLE_DELAY later,
quietly (SETTLING); a mismatch again before a download matched, of
whichever manifest, is FAILED (two generations taking turns would settle
for ever). A failed round keeps www/ as it is, logs once per kind of
failure (with a traceback when it is a crash) and is tried again
RETRY_DELAY later, at most RETRY_LIMIT times until the next first or daily
round. A round records for each index its result and, for one that did not
install, its reason (mav_daily.reason_of; storage.PAGES_ROUND, the sensors'
pages_reason). The options flow can turn it off (CONF_PAGES_TIMETABLES); then
no request is made at all.

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
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import aiohttp
from awesomeversion import AwesomeVersion
from awesomeversion.exceptions import AwesomeVersionException

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import IntegrationNotFound, async_get_integration
from homeassistant.util import dt as dt_util

from . import jsonsafe, mav, mav_daily, mav_index, served_index, storage, timetables
from .const import DOMAIN, INDEX_SCHEMA

_LOGGER = logging.getLogger(__name__)

MANIFEST_URL = f"{mav_daily.PAGES_BASE}/timetables.json"
# One line: the base64 of the ed25519 signature (64 bytes) over the exact
# bytes of timetables.json, made by the Pages workflow.
SIGNATURE_URL = f"{MANIFEST_URL}.sig"
MAX_SIGNATURE = mav_daily.MAX_SIGNATURE
# The keys the manifests are signed with (mav_daily.PAGES_PUBLIC_KEYS, the
# current one first, read there at every check).
PAGES_PUBLIC_KEYS = mav_daily.PAGES_PUBLIC_KEYS
PAGES_PUBLIC_KEY = PAGES_PUBLIC_KEYS[0]
# The manifest format scripts/pages_timetables.py writes. Fields it gained
# later (release_feed, release_version) are optional: older versions read it
# as before.
MANIFEST_FORMAT = 1
# The indexes the manifest may name, and the file each is in www/.
INDEXES = {"volan": timetables.VOLAN_INDEX, "city": timetables.CITY_INDEX}
MAX_MANIFEST = 64 * 1024
# The Volán index is about 8 MB gzipped; the city one 1.9 MB gzipped, 16.3 MB
# unpacked (November 2026).
MAX_INDEX = 48 * 1024 * 1024
MAX_SHARD = 8 * 1024 * 1024
MAX_TOTAL = 160 * 1024 * 1024
MAX_SHARDS = 256
# What an index may unpack to while it is checked (tens of MB today).
MAX_INDEX_JSON = 1024 * 1024 * 1024
MAX_FEED = 4096
# A feed version dated further ahead than this is no date (check_entry
# refuses it on Pages; served, it counts as older than any dated feed). A
# feed_version names the day a feed was published or starts: operators
# publish their timetables weeks, at most a few months, ahead (December's
# timetable change in November). A year and a month allows the next year's
# change published early; a date past it is a typo or an end date taken for
# the version ("bkk:20271231", "...29991231"), and taken at its word it
# would freeze the index: no real build is newer until that day.
MAX_FEED_AHEAD = timedelta(days=400)
# decide's release rule keeps a newer release's city index from a Pages
# build of an older baseline until the site rebuilds against this release.
# A -rev beta's index is never a baseline (only a stable release starts
# one), so on a beta that wait lasts until the next stable release, while
# the served feeds age: once an operator both indexes have is dated more
# than this older in the served one than on Pages (release_stale), the
# operator rule decides again. Pages rebuilds the city index weekly.
RELEASE_STALE = timedelta(days=21)
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
# A temp folder or file younger than this may be another writer's (the
# local updater building right now, a round downloading); an older one is
# a leftover.
TEMP_MIN_AGE = 3600
# The folder a round downloads shards into, and moves a broken folder of the
# same name out to, in www/: www/STAGING<dir>-<hex>.
STAGING = ".ht-staging-"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# An entry's release_version: a release tag (scripts/pages_timetables.py
# takes it from the stable release its baseline came from).
RELEASE_VERSION_RE = re.compile(r"v?\d{1,6}\.\d{1,6}\.\d{1,6}(?:-[0-9A-Za-z.]{1,32})?")
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
# Why an index was not installed (IndexResult.reason): mav_daily's words.
RELEASE_NEWER = mav_daily.REASON_RELEASE_NEWER
SERVED_NEWER = mav_daily.REASON_SERVED_NEWER
UNKNOWN_KEY = mav_daily.UNKNOWN_KEY

# The filesystem calls that move and remove folders in www/, here so the
# tests can watch them without patching the os and shutil modules.
_replace = os.replace
_rmtree = shutil.rmtree


@dataclass(frozen=True)
class IndexResult:
    """What a round did for one index, and the manifest's feed for it."""

    result: str
    feed: str | None = None
    error: str | None = None
    # INSTALLED again what the last install already was (the same sha256,
    # or feed and size): after a HACS update put the release's older index
    # back. Installed and recorded, but no news.
    again: bool = False
    # Why it was not installed (SUPERSEDED, FAILED, UNSUPPORTED, SETTLING):
    # mav_daily.reason_of, RELEASE_NEWER or SERVED_NEWER.
    reason: str | None = None


@dataclass(frozen=True)
class TimetablesRound:
    """One round: per index name, its IndexResult."""

    results: Mapping[str, IndexResult] = field(default_factory=dict)
    built_at: datetime | None = None

    @property
    def installed(self) -> list[str]:
        return [name for name, res in self.results.items() if res.result == INSTALLED]

    @property
    def news(self) -> list[str]:
        """The installed indexes that are not the last install again."""
        return [name for name, res in self.results.items() if res.result == INSTALLED and not res.again]

    @property
    def settling(self) -> bool:
        return any(res.result == SETTLING for res in self.results.values())

    @property
    def failed(self) -> bool:
        return any(res.result == FAILED for res in self.results.values())


class _Unsupported(mav_daily.DailyIndexError):
    """A manifest format or index schema this version does not read."""


class _Mismatch(mav_daily._Mismatch):
    """A download is not the file its manifest entry describes."""


# timetables.json.sig is missing, malformed or does not verify.
_BadSignature = mav_daily.BadSignature


# What a download of the other generation looks like right after a deploy
# (mav_daily.PAGES_MAX_AGE): not the file the manifest describes (also longer
# than it says: the index and the shards turn mav_daily.TooLarge into
# _Mismatch), or gone (a cached manifest naming a shapes folder or an index
# of the previous site). A manifest past MAX_MANIFEST is no mismatch: it
# names no size, and is FAILED (reason mav_daily.REASON_TOO_LARGE).
_MISMATCHES = (_Mismatch, mav_daily.NotFound)


def verify_manifest(raw: bytes, signature: bytes, key: str | tuple[str, ...] | None = None) -> None:
    """Raise _BadSignature unless signature (timetables.json.sig: one line,
    base64 of 64 bytes) is the ed25519 signature of raw by key (base64 of
    the raw 32-byte public key, or several; mav_daily.PAGES_PUBLIC_KEYS by
    default); mav_daily.UnknownKey, one, when it is well-formed but no key
    verifies it."""
    mav_daily.verify_signature(raw, signature, "timetables.json", key)


def _today() -> date:
    return dt_util.utcnow().date()


def _day(version: str | None, today: date) -> date | None:
    """The date a feed version names (None when it names none); date.min
    when that is more than MAX_FEED_AHEAD after today: no real date, older
    than any."""
    day = served_index.feed_date(version)
    if day is not None and day > today + MAX_FEED_AHEAD:
        return date.min
    return day


def _too_far(version: str | None, today: date) -> bool:
    return _day(version, today) == date.min


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


def check_entry(name: str, entry: Any, today: date | None = None) -> dict[str, Any]:
    """One index's manifest entry, checked and reduced to what is used:
    feed, size, sha256, shapes (None, or dir, n and files name ->
    (size, sha256)), release_feed and release_version (None when absent)
    and built_at (a POSIX time, None when absent or not a time)."""
    today = today or _today()
    if not isinstance(entry, dict):
        raise mav_daily.DailyIndexError(f"{name}: manifest entry is not an object")
    feed, size, sha = entry.get("feed"), entry.get("size"), entry.get("sha256")
    if not isinstance(feed, str) or not feed or len(feed) > MAX_FEED:
        raise mav_daily.DailyIndexError(f"{name}: manifest names no feed")
    if name == "city":
        feeds = timetables.city_feeds(feed)
        if not feeds:
            raise mav_daily.DailyIndexError(f"{name}: manifest feed {feed[:80]!r} names no operator")
    elif served_index.feed_date(feed) is None:
        raise mav_daily.DailyIndexError(f"{name}: manifest names no dated feed: {feed[:80]!r}")
    else:
        feeds = {name: feed}
    far = [f"{op}:{version}"[:80] for op, version in feeds.items() if _too_far(version, today)]
    if far:
        raise mav_daily.DailyIndexError(
            f"{name}: manifest feed {far[0]!r} is dated more than {MAX_FEED_AHEAD.days} days ahead"
        )
    release_feed = entry.get("release_feed")
    if release_feed is not None and (
        not isinstance(release_feed, str) or not release_feed or len(release_feed) > MAX_FEED
    ):
        raise mav_daily.DailyIndexError(f"{name}: manifest release_feed {str(release_feed)[:80]!r} is no feed")
    release_version = entry.get("release_version")
    if release_version is not None and (
        not isinstance(release_version, str) or not RELEASE_VERSION_RE.fullmatch(release_version)
    ):
        raise mav_daily.DailyIndexError(
            f"{name}: manifest release_version {str(release_version)[:80]!r} is no release tag"
        )
    built = mav_daily.built_at({"built_at": entry.get("built_at")})
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
    return {
        "feed": feed, "size": size, "sha256": sha, "shapes": checked, "release_feed": release_feed,
        "release_version": release_version, "built_at": built.timestamp() if built is not None else None,
    }


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


def city_newer(pages: str, installed: str | None, today: date | None = None) -> bool:
    """Whether the city index of feed pages replaces one of installed: no
    operator on both sides has an older dated feed in pages, and one has a
    later one, a feed that changed and is not dated on both sides, or the
    operator set differs. Nothing replaces an index by the same feed. An
    installed date more than MAX_FEED_AHEAD ahead is older than any (_day)."""
    if installed is None:
        return True
    today = today or _today()
    new, old = timetables.city_feeds(pages), timetables.city_feeds(installed)
    if not new or new == old:
        return False
    changed = set(new) != set(old)
    for op in new.keys() & old.keys():
        day, old_day = _day(new[op], today), _day(old[op], today)
        if day is not None and old_day is not None:
            if day < old_day:
                return False
            changed |= day > old_day
        elif new[op] != old[op]:
            changed = True
    return changed


def updater_newer(pages: str, durable: str | None, today: date | None = None) -> bool:
    """Whether pages beats the local updater's durable copy of feed durable
    by the updater's own rule (scripts/update_volan_index.served_feeds_newer):
    every operator of the copy is in pages, of those dated on both sides
    none is older and one later, and an undated one is the same feed. With
    today (decide's strict rule for a served index), a date of durable more
    than MAX_FEED_AHEAD ahead is older than any (_day), as the operator rule
    takes it."""
    old, new = timetables.city_feeds(durable), timetables.city_feeds(pages)
    if not old:
        return False
    later = False
    for op, version in old.items():
        other = new.get(op)
        if other is None:
            return False
        if today is None:
            day, other_day = served_index.feed_date(version), served_index.feed_date(other)
        else:
            day, other_day = _day(version, today), _day(other, today)
        if day is None or other_day is None:
            if other != version:
                return False
            continue
        if other_day < day:
            return False
        later |= other_day > day
    return later


def volan_newer(pages: str, installed: str | None, today: date | None = None) -> bool:
    """served_index.newer_feed, but an installed date more than
    MAX_FEED_AHEAD ahead is older than any (_day)."""
    today = today or _today()
    day, other = _day(pages, today), _day(installed, today)
    return day is not None and (other is None or day > other)


def release_stale(pages: str, served: str | None, today: date | None = None) -> bool:
    """Whether the served city index (a release's) is behind the Pages one
    of feed pages by more than RELEASE_STALE: an operator in both, dated on
    both sides (_day), has a Pages feed more than that later. Only the
    operators both have count: one only the served index has (a beta added
    it, fresh) says nothing of how old the others are, nor one only Pages
    has; and an operator dated ahead (a timetable published early, the same
    on both sides) holds back no other. False when no operator is dated on
    both sides."""
    today = today or _today()
    new, old = timetables.city_feeds(pages), timetables.city_feeds(served)
    for op in new.keys() & old.keys():
        day, old_day = _day(new[op], today), _day(old[op], today)
        if day is not None and old_day is not None and day - old_day > RELEASE_STALE:
            return True
    return False


def release_newer(running: Any, release_version: str | None) -> bool:
    """Whether running, the version of this integration (an AwesomeVersion,
    "1.10.26-rev.1"), is newer than release_version, the tag of the stable
    release a Pages build started from ("v1.10.24"). A -rev beta orders
    before its own stable version and after the one before (semver; the
    release workflow numbers a -rev series two patches up). False when
    either is unknown or they do not compare: the operator rule decides."""
    if running is None or not release_version:
        return False
    try:
        return AwesomeVersion(str(running)) > AwesomeVersion(release_version.removeprefix("v"))
    except AwesomeVersionException:
        return False


def _same_file(record: Any, feed: str | None, size: int) -> bool:
    return isinstance(record, Mapping) and record.get("feed") == feed and record.get("size") == size


def decide_why(
    name: str,
    feed: str,
    www: Path,
    durable: Path,
    *,
    release_version: str | None = None,
    running: Any = None,
    pages_install: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> tuple[str, str | None]:
    """Whether the Pages index name of feed replaces what www/ serves:
    _INSTALL, else UP_TO_DATE (the same feed), LOCAL_WINS (the local
    updater's durable copy in durable/ is not older by its own rule) or
    SUPERSEDED; with why it was not installed when that is SUPERSEDED:
    SERVED_NEWER (www/ serves an index Pages' does not beat by the operator
    rule), RELEASE_NEWER (www/ serves the city index of a release newer
    than the one the Pages build started from, release_version, and Pages'
    is not newer by the strict rule, and the served one is not
    release_stale). running is this integration's
    version; pages_install what this module last installed
    (storage.TIMETABLES_INSTALL): the served index is that install when its
    feed and size are the record's."""
    today = today or _today()
    out = www / INDEXES[name]
    if not out.is_file():
        return _INSTALL, None
    current = served_index.read_feed(out)
    if current == feed:
        return UP_TO_DATE, None
    copy = durable / INDEXES[name]
    kept = None
    # A served index that does not read is replaced: the updater would put
    # its copy back anyway, and until then the cards have one.
    if current is not None and copy.is_file():
        kept = served_index.read_feed(copy)
        if kept is not None and kept != feed and not updater_newer(feed, kept):
            return LOCAL_WINS, None
    if name == "city" and current is not None and release_newer(running, release_version):
        size = out.stat().st_size
        local = kept == current and copy.stat().st_size == size
        if not local and not _same_file(pages_install, current, size):
            # This version's own index (or a later release's) is served, and
            # the Pages build started from an older release: it may lack an
            # operator this one added or an undated feed it updated, which
            # the operator rule takes for news. Only a strictly newer one
            # replaces it; the site rebuilds against this release within
            # days of its release, a beta's index is never its baseline:
            # once the served feeds are RELEASE_STALE behind Pages', the
            # operator rule decides (a beta would otherwise wait for the
            # next stable release while its feeds run out).
            if updater_newer(feed, current, today):
                return _INSTALL, None
            if not release_stale(feed, current, today):
                return SUPERSEDED, RELEASE_NEWER
    newer = city_newer(feed, current, today) if name == "city" else volan_newer(feed, current, today)
    return (_INSTALL, None) if newer else (SUPERSEDED, SERVED_NEWER)


def decide(name: str, feed: str, www: Path, durable: Path, **kwargs: Any) -> str:
    """decide_why without the why."""
    return decide_why(name, feed, www, durable, **kwargs)[0]


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
    Staging folders of this module, the updater's temp folders and temp
    indexes of mav_index.write_bytes go once they are TEMP_MIN_AGE old.
    Nothing else when www/ serves no such index: what it would keep is not
    known."""
    now = time_mod.time() if now is None else now
    prefix = INDEXES[name].removesuffix("-index.json.gz") + "-shapes-"
    for staging in www.glob(STAGING + prefix + "*"):
        try:
            if staging.is_dir() and now - staging.stat().st_mtime >= TEMP_MIN_AGE:
                _rmtree(staging, ignore_errors=True)
        except OSError:
            pass
    out = www / INDEXES[name]
    if not out.is_file():
        return
    for entry in www.glob(prefix + "*"):
        try:
            if not entry.is_dir():
                continue
            if "." in entry.name[len(prefix):]:
                if now - entry.stat().st_mtime >= TEMP_MIN_AGE:
                    _rmtree(entry, ignore_errors=True)
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
                _rmtree(entry, ignore_errors=True)
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


def _verify_file(path: Path, size: int, sha: str) -> bool:
    try:
        return path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == sha
    except OSError:
        return False


def _verify_folder(folder: Path, files: Mapping[str, tuple[int, str]]) -> bool:
    """Whether folder holds exactly these shards, by size and sha256."""
    return all(_verify_file(folder / shard, size, sha) for shard, (size, sha) in files.items())


def staging_key(entry: Mapping[str, Any]) -> str:
    """The staging folder's suffix for a manifest entry: of its feed and
    sha256, so a try of the same entry again finds the shards the last one
    verified."""
    return hashlib.sha256(f"{entry['feed']}\n{entry['sha256']}".encode()).hexdigest()[:16]


def _staging(www: Path, shapes: Mapping[str, Any] | None, key: str) -> tuple[Path | None, list[str]]:
    """The temp folder the shards are downloaded into and the shards still
    to download; (None, []) when there are none or www/ has the folder
    already, verified. A folder of the same key a failed try left keeps the
    shards that verify; it is touched, so prune takes it for no leftover."""
    if shapes is None:
        return None, []
    final = www / shapes["dir"]
    if final.is_dir() and _verify_folder(final, shapes["files"]):
        return None, []
    tmp = www / f"{STAGING}{shapes['dir']}-{key}"
    if tmp.is_symlink() or (tmp.exists() and not tmp.is_dir()):
        tmp.unlink()
    tmp.mkdir(mode=0o755, exist_ok=True)
    os.chmod(tmp, 0o755)
    os.utime(tmp)
    missing = [shard for shard, (size, sha) in shapes["files"].items() if not _verify_file(tmp / shard, size, sha)]
    return tmp, missing


def _commit(www: Path, name: str, raw: bytes, shapes: Mapping[str, Any] | None, tmp: Path | None,
            token: str, built: float | None = None, now: float | None = None) -> None:
    """Put the shapes folder in place, then the index, its mtime built (the
    entry's built_at; not one in the future); then prune. DailyIndexError
    naming the step that failed; the folder the served index names is in
    place again when the swap of a broken one fails."""
    if tmp is not None:
        final = www / shapes["dir"]
        try:
            if final.is_dir():
                if _verify_folder(final, shapes["files"]):
                    _rmtree(tmp, ignore_errors=True)
                else:
                    # A broken copy of the same name: swapped for the verified one.
                    old = www / f"{STAGING}{shapes['dir']}-{token}-old"
                    _replace(final, old)
                    try:
                        _replace(tmp, final)
                    except OSError:
                        # The served index names it: a broken copy beats none.
                        _replace(old, final)
                        raise
                    _rmtree(old, ignore_errors=True)
            else:
                _replace(tmp, final)
        except OSError as err:
            raise mav_daily.DailyIndexError(f"could not put the shapes folder {shapes['dir']} in place: {err}") from err
    out = www / INDEXES[name]
    try:
        mav_index.write_bytes(raw, out, token)
    except OSError as err:
        raise mav_daily.DailyIndexError(f"could not write {INDEXES[name]}: {err}") from err
    if built is not None and built <= time_mod.time():
        try:
            os.utime(out, (built, built))
        except OSError as err:
            _LOGGER.debug("Could not stamp %s with its build time: %s", out, err)
    prune(www, name, shapes["dir"] if shapes else None, now)


def _prune_served(www: Path, name: str) -> None:
    prune(www, name, shapes_folder(www / INDEXES[name]))


def prune_all(www: Path) -> None:
    """prune for both indexes, keeping the folders the served ones name: at
    setup, so retired folders go also with the weekly timetables off."""
    for name in INDEXES:
        _prune_served(www, name)


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

    def _discard(self) -> None:
        if self._fh is not None:
            self._fh.close()
        # What came of it is not the shard: a retry downloads it again.
        self._path.unlink(missing_ok=True)

    async def abort(self) -> None:
        await self._hass.async_add_executor_job(self._discard)


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
        await writer.abort()
        raise _Mismatch(f"{path.name} is {writer.size} bytes, the manifest says {size}")
    if digest != sha:
        await writer.abort()
        raise _Mismatch(f"{path.name} sha256 does not match the manifest")


async def _download_shards(
    hass: HomeAssistant, session: aiohttp.ClientSession, shapes: Mapping[str, Any], tmp: Path,
    names: list[str] | None = None,
) -> None:
    """The shards names (by default every one) into tmp, CONCURRENCY at a
    time; the first failure stops the others and is raised. A shard that did
    not check out is not left in tmp; one that did stays for a retry.

    The downloads run in a task of their own, which the caller waits for: a
    stop (a cancel of the round) is raised here whatever the shards do. In
    the round's own task a TaskGroup can swallow it: a cancel that comes
    while a failed shard's TaskGroup aborts the others is, on Python 3.12
    (Home Assistant 2024.10), taken for the TaskGroup's own, and the round
    went on to the next index after an unload or a reload. On a cancel the
    downloads are cancelled and waited for (the shards that verified stay),
    then the cancel is raised."""
    gate = asyncio.Semaphore(CONCURRENCY)
    wanted = shapes["files"] if names is None else {shard: shapes["files"][shard] for shard in names}

    async def one(shard: str, size: int, sha: str) -> None:
        async with gate:
            url = f"{mav_daily.PAGES_BASE}/{shapes['dir']}/{shard}"
            await _download_shard(hass, session, url, tmp / shard, size, sha)

    async def every() -> None:
        try:
            async with asyncio.TaskGroup() as group:
                for shard, (size, sha) in wanted.items():
                    group.create_task(one(shard, size, sha))
        except BaseExceptionGroup as errors:
            # Mismatches first: the round settles rather than fails.
            found = [e for e in errors.exceptions if isinstance(e, _MISMATCHES)] or list(errors.exceptions)
            raise found[0] from None

    work = hass.loop.create_task(every(), name="hungarian_transport_pages_shards")
    try:
        await asyncio.wait([work])
    except asyncio.CancelledError:
        while not work.done():
            work.cancel()
            try:
                await asyncio.wait([work])
            except asyncio.CancelledError:
                continue
        if not work.cancelled():
            work.exception()
        raise
    work.result()


# hass.data[DOMAIN] keys, only this module reads them.
_LOGGED = "pages_timetables_logged"
_SETTLED = "pages_timetables_settled"
_LOCK = "pages_timetables_lock"
# The settling scope of timetables.json.sig (the indexes' are their names).
_SIGNATURE = "signature"


def write_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Held by every round while it reads and writes the Volán and city files."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_LOCK, asyncio.Lock())


# The _log_failure scopes that are no index: what failed, for the warning.
_ROUND_SCOPES = {"manifest": "their manifest, timetables.json", "round": "the round crashed"}


def _log_failure(
    hass: HomeAssistant, scope: str, message: str, unsupported: bool = False, exc_info: BaseException | None = None
) -> None:
    """Once per kind of failure of scope (an index, "manifest": the manifest
    or its signature, "round": a round that crashed); the same one again
    goes to debug. exc_info: a crash (not of
    mav_daily.EXPECTED_ERRORS), logged with its traceback the first time."""
    logged = hass.data.setdefault(DOMAIN, {}).setdefault(_LOGGED, {})
    if logged.get(scope) == message:
        _LOGGER.debug("Weekly timetables from GitHub Pages: %s", message)
        return
    logged[scope] = message
    if unsupported:
        _LOGGER.info("Weekly timetables from GitHub Pages skipped: %s. Update Hungarian transport to use them",
                     message)
    elif scope in _ROUND_SCOPES:
        _LOGGER.warning(
            "Weekly timetables from GitHub Pages not installed (%s): %s. The current indexes stay; trying again"
            " later", _ROUND_SCOPES[scope], message, exc_info=exc_info,
        )
    else:
        _LOGGER.warning(
            "Weekly %s timetable from GitHub Pages not installed: %s. The current index stays; trying again later",
            scope, message, exc_info=exc_info,
        )


def _clear_failure(hass: HomeAssistant, scope: str) -> None:
    hass.data.setdefault(DOMAIN, {}).setdefault(_LOGGED, {}).pop(scope, None)


def _crash(hass: HomeAssistant, scope: str, err: Exception) -> str:
    """A round (scope "round") or an index that raised: logged once as a
    failure, with its traceback unless it is one of the expected errors."""
    message = f"{type(err).__name__}: {err}"
    expected = isinstance(err, mav_daily.EXPECTED_ERRORS)
    _log_failure(hass, scope, message, exc_info=None if expected else err)
    if expected:
        _LOGGER.debug("Weekly %s timetable round failed", scope, exc_info=err)
    return message


def _may_settle(hass: HomeAssistant, scope: str) -> bool:
    """Whether a download of scope (an index, the signature) that did not
    match its manifest may settle: the first since one matched, or since the
    cycle began (new_cycle). The next is FAILED, of whichever manifest: two
    generations taking turns (A, B, A, B) would otherwise settle for ever."""
    settled = hass.data.setdefault(DOMAIN, {}).setdefault(_SETTLED, set())
    if scope in settled:
        return False
    settled.add(scope)
    return True


def _matched(hass: HomeAssistant, scope: str) -> None:
    hass.data.setdefault(DOMAIN, {}).setdefault(_SETTLED, set()).discard(scope)


def new_cycle(hass: HomeAssistant) -> None:
    """A first or daily round: a mismatch may settle again."""
    hass.data.setdefault(DOMAIN, {}).pop(_SETTLED, None)


def _every(result: str, error: str, reason: str | None = None) -> TimetablesRound:
    return TimetablesRound({name: IndexResult(result, error=error, reason=reason) for name in INDEXES})


async def _signed(hass: HomeAssistant, session: aiohttp.ClientSession, raw: bytes) -> TimetablesRound | None:
    """None when timetables.json.sig verifies raw; else the round
    (mav_daily.check_signature): SETTLING the first time (right after a
    deploy the cache can pair the new manifest with the old signature, or
    still answer 404 for it); after that FAILED, also for a well-formed
    signature no key verifies (UNKNOWN_KEY: signed by a key this version
    does not know: a newer key, another deploy's signature, or not the
    site's), until that has been so on more than mav_daily.UNKNOWN_KEY_DAYS
    consecutive days: then UNSUPPORTED, said once at info and not retried.
    Nothing is installed without a signature."""
    check = await mav_daily.check_signature(
        hass, session, SIGNATURE_URL, raw, "timetables.json", partial(_may_settle, hass, _SIGNATURE)
    )
    if check is None:
        _matched(hass, _SIGNATURE)
        return None
    err = check.error
    if check.result == SETTLING:
        _LOGGER.debug(
            "Weekly timetables from GitHub Pages: %s; the GitHub Pages cache may still hold another"
            " generation, looking again in %d s", err, mav_daily.PAGES_SETTLE_DELAY,
        )
    elif check.result == UNSUPPORTED:
        _log_failure(hass, "manifest", mav_daily.signature_message(check), unsupported=True)
    else:
        _log_failure(hass, "manifest", mav_daily.signature_message(check))
    return _every(check.result, str(err), check.reason)


async def async_update(hass: HomeAssistant, www: Path) -> TimetablesRound:
    """One round: the manifest and its signature, then each index it names
    that wins."""
    session = async_get_clientsession(hass)
    try:
        raw = await mav_daily._get(session, MANIFEST_URL, MAX_MANIFEST, MANIFEST_TIMEOUT)
    except mav_daily.DailyIndexError as err:
        _log_failure(hass, "manifest", str(err))
        return _every(FAILED, str(err), mav_daily.reason_of(err))
    unsigned = await _signed(hass, session, raw)
    if unsigned is not None:
        return unsigned
    try:
        man = check_manifest(raw)
    except _Unsupported as err:
        _log_failure(hass, "manifest", str(err), unsupported=True)
        return _every(UNSUPPORTED, str(err), mav_daily.reason_of(err))
    except mav_daily.DailyIndexError as err:
        _log_failure(hass, "manifest", str(err))
        return _every(FAILED, str(err), mav_daily.reason_of(err))
    _clear_failure(hass, "manifest")
    # Before anything is installed: a round that installs reports it.
    built = mav_daily.built_at(man)
    durable = mav.source_path(hass).parent
    running = await _running_version(hass)
    results: dict[str, IndexResult] = {}
    async with write_lock(hass):
        for name in INDEXES:
            entry = man["indexes"].get(name)
            try:
                if entry is None:
                    await hass.async_add_executor_job(_prune_served, www, name)
                    results[name] = IndexResult(NOT_PUBLISHED)
                    continue
                results[name] = await _update_index(hass, session, name, entry, www, durable, running)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a crashed index is a failed one
                message = _crash(hass, name, err)
                results[name] = IndexResult(
                    FAILED, entry.get("feed") if isinstance(entry, dict) else None, message,
                    reason=message[:mav_daily.MAX_REASON],
                )
    return TimetablesRound(results, built)


# This integration's manifest.json on disk, and what _version_on_disk read
# of it: (mtime_ns, size, version).
_MANIFEST_FILE = Path(__file__).with_name("manifest.json")
_version_cache: dict[Path, tuple[int, int, Any]] = {}


def _version_on_disk(path: Path) -> Any:
    """The version manifest.json at path names, an AwesomeVersion; None
    when it cannot be read or names none. Read again only when its mtime or
    size changed."""
    try:
        st = path.stat()
    except OSError:
        return None
    cached = _version_cache.get(path)
    if cached is not None and cached[:2] == (st.st_mtime_ns, st.st_size):
        return cached[2]
    version: Any = None
    try:
        if st.st_size <= MAX_MANIFEST:
            data = jsonsafe.loads(path.read_bytes())
            value = data.get("version") if isinstance(data, dict) else None
            if isinstance(value, str) and value:
                version = AwesomeVersion(value)
    except (OSError, ValueError, RecursionError) as err:
        _LOGGER.debug("Could not read the version of %s: %s", path, err)
    _version_cache[path] = (st.st_mtime_ns, st.st_size, version)
    return version


async def _running_version(hass: HomeAssistant) -> Any:
    """This integration's version, an AwesomeVersion (its manifest.json,
    which the release workflow sets to the tag); None when unknown. Read
    from the file on disk, not the integration Home Assistant loaded: a
    HACS update replaces custom_components/ and www/ at once, before the
    restart that loads the new code, and decide's release rule is about the
    city index www/ serves, which is already the new release's. The loaded
    version when the file does not read."""
    version = await hass.async_add_executor_job(_version_on_disk, _MANIFEST_FILE)
    if version is not None:
        return version
    try:
        return (await async_get_integration(hass, DOMAIN)).version
    except IntegrationNotFound:
        return None


# Shared with the daily MÁV install (mav_daily.wait_out).
_wait_out = mav_daily.wait_out


async def _update_index(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    name: str,
    raw_entry: Any,
    www: Path,
    durable: Path,
    running: Any = None,
) -> IndexResult:
    try:
        entry = check_entry(name, raw_entry)
    except mav_daily.DailyIndexError as err:
        _log_failure(hass, name, str(err))
        return IndexResult(FAILED, error=str(err), reason=mav_daily.reason_of(err))
    feed = entry["feed"]

    def failed(err: Exception) -> IndexResult:
        _log_failure(hass, name, str(err))
        return IndexResult(FAILED, feed, str(err), reason=mav_daily.reason_of(err))

    installs = (await storage.async_load(hass)).get(storage.TIMETABLES_INSTALL)
    last = installs.get(name) if isinstance(installs, dict) else None
    last = last if isinstance(last, dict) else None
    decide_now = partial(
        decide_why, name, feed, www, durable,
        release_version=entry["release_version"], running=running, pages_install=last,
    )
    decision, why = await hass.async_add_executor_job(decide_now)
    if decision != _INSTALL:
        await hass.async_add_executor_job(_prune_served, www, name)
        _clear_failure(hass, name)
        _matched(hass, name)
        _LOGGER.debug("Weekly %s timetable of feed %s: %s (%s)", name, feed[:80], decision, why)
        return IndexResult(decision, feed, reason=why)
    token = secrets.token_hex(4)
    tmp: Path | None = None
    # A download that failed keeps the shards it verified for the next try;
    # so does one a stop cancelled (an unload, a reload, Home Assistant
    # stopping), during the download or after it, before the commit: it is a
    # try that did not finish, and the next round of the same entry finds
    # them. prune removes the folder once it is
    # TEMP_MIN_AGE old, at the latest at the next setup or round after that.
    keep = False
    try:
        try:
            try:
                raw = await mav_daily._get(
                    session, f"{mav_daily.PAGES_BASE}/{INDEXES[name]}", entry["size"], INDEX_TIMEOUT
                )
            except mav_daily.TooLarge as err:
                # Longer than the manifest says: another generation's file
                # (or a broken one), as a shard is (_download_shard).
                raise _Mismatch(str(err)) from err
            await hass.async_add_executor_job(check_index, raw, entry)
            tmp, missing = await hass.async_add_executor_job(_staging, www, entry["shapes"], staging_key(entry))
            if missing:
                await _download_shards(hass, session, entry["shapes"], tmp, missing)
        except asyncio.CancelledError:
            keep = True
            raise
        except _MISMATCHES as err:
            keep = True
            if not _may_settle(hass, name):
                return failed(err)
            # Right after a deploy the Pages cache can still hold the other
            # generation of a file (mav_daily.PAGES_MAX_AGE).
            _LOGGER.debug(
                "Weekly %s timetable of feed %s: %s; the GitHub Pages cache may still hold another"
                " generation, looking again in %d s", name, feed[:80], err, mav_daily.PAGES_SETTLE_DELAY,
            )
            return IndexResult(SETTLING, feed, str(err), reason=mav_daily.reason_of(err))
        except mav_daily.DailyIndexError as err:
            keep = True
            return failed(err)
        _matched(hass, name)
        # www/ may have changed during the download (the local updater).
        try:
            decision, why = await hass.async_add_executor_job(decide_now)
        except asyncio.CancelledError:
            # Stopped after the download: the shards checked out, the next
            # try of the same entry needs none of them again.
            keep = True
            raise
        if decision != _INSTALL:
            await hass.async_add_executor_job(_prune_served, www, name)
            _clear_failure(hass, name)
            return IndexResult(decision, feed, reason=why)

        # The commit runs in the executor as a plain future
        # (loop.run_in_executor), neither a task nor an executor job of
        # Home Assistant's: those a stop cancels (Home Assistant's own stop
        # cancels every executor job a background task started, and at its
        # last stage every task it still tracks), and a cancelled one is
        # done while its thread goes on, so the cleanup below would run
        # beside the commit and the record would be lost. Nothing cancels
        # this future: the round waits for it through every cancel
        # meanwhile (_wait_out, which takes each cancel back, so that Home
        # Assistant's stop waits for the round), then for the record the
        # same way, and only then cleans up and raises the cancel.
        commit = hass.loop.run_in_executor(
            None, partial(_commit, www, name, raw, entry["shapes"], tmp, token, entry["built_at"])
        )
        cancelled = await _wait_out(commit)
        error = commit.exception()
        if error is None:
            tmp = None
            # With the commit, never without it: an index of this module's
            # without its record reads as a release's (decide's release
            # rule would keep it from every later build).
            record = hass.loop.create_task(
                storage.async_record_timetables_install(hass, name, feed, entry["sha256"], entry["size"]),
                name=f"hungarian_transport_pages_{name}_record",
            )
            cancelled = await _wait_out(record) or cancelled
        if cancelled:
            raise asyncio.CancelledError
        if isinstance(error, mav_daily.DailyIndexError):
            return failed(error)
        if error is not None:
            raise error
    finally:
        if tmp is not None and not keep:
            await hass.async_add_executor_job(partial(_rmtree, tmp, ignore_errors=True))
    _clear_failure(hass, name)
    again = isinstance(last, dict) and (
        last.get("sha256") == entry["sha256"] or (last.get("feed"), last.get("size")) == (feed, entry["size"])
    )
    _LOGGER.info(
        "Installed the weekly %s timetable of feed %s from GitHub Pages%s",
        name, feed[:200], " again" if again else "",
    )
    return IndexResult(INSTALLED, feed, again=again)


class PagesTimetables:
    """The rounds of the weekly timetables: started by
    mav_daily.DailyIndexUpdater after the MÁV part of its first and daily
    rounds (start_round), in a task of their own, so a slow download never
    holds up the MÁV updater's next round; with retries of their own.

    A round with a FAILED index is tried again RETRY_DELAY later, at most
    RETRY_LIMIT times since the last first or daily round; one that is
    SETTLING is looked at again mav_daily.PAGES_SETTLE_DELAY later, using
    up no retry (once: a mismatch again is FAILED, _may_settle). A first or
    daily round replaces a retry still waiting, and starts no second task
    beside one still running. on_result gets each round
    (timetable_notify.py); then the indexes it settled are recorded for the
    sensors (storage.PAGES_ROUND, with each one's reason; an index still
    settling is marked so on its last settled record). stop() cancels a
    round: one cancelled during its commit (by stop(), or by Home
    Assistant's own stop, which then waits for it) waits for the commit and
    its install record (storage.TIMETABLES_INSTALL), and reports nothing
    else."""

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

    @callback
    def start_round(self) -> None:
        """A first or daily round, in the background: its own retries from
        now on. One still running (a slow download) is this one."""
        if self._stopped:
            return
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._retries = 0
        new_cycle(self._hass)
        self._start()

    @callback
    def _start(self) -> None:
        if self._running or (self._task is not None and not self._task.done()):
            return
        self._task = self._hass.async_create_background_task(self._round(), "hungarian_transport_pages_timetables")

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
        if not self._stopped:
            self._start()

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
            message = _crash(self._hass, "round", err)
            done = _every(FAILED, message, message[:mav_daily.MAX_REASON])
        else:
            # The next crash is news again.
            _clear_failure(self._hass, "round")
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
            {
                name: (res.result, res.feed, res.reason)
                for name, res in done.results.items()
                if res.result != SETTLING
            },
            {name: res.reason or SETTLING for name, res in done.results.items() if res.result == SETTLING},
        )
