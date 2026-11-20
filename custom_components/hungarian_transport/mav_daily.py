"""The daily MÁV route index from GitHub Pages.

MÁV publishes a new GTFS almost every day; a release ships the index of
its release day. The repository's daily workflow (.github/workflows/mav-index-daily.yml)
builds the same pfaedle-matched, audited index every day and publishes it on
GitHub Pages with a manifest (scripts/mav_pages_site.py). This module
reads the manifest a while after startup and then once a day at the time
the options set (CONF_DAILY_TIME, 06:00 local time by default, plus up to
DAILY_JITTER), looks again later that day while the manifest's feed is not
the day's, and installs the index only when:

- the manifest is signed by the workflow's key: mav-index.json.sig next to
  it, one line of base64 of the ed25519 signature over the manifest's exact
  bytes, by one of PAGES_PUBLIC_KEYS (verify_signature, shared with
  weekly_timetables.py's timetables.json.sig). The manifest names the
  index's sha256, so the signature ties the index to the repository rather
  than to whoever answers for bator.github.io. A missing or bad signature
  installs nothing (check_signature: looked at again once, PAGES_SETTLE_DELAY
  later, like a mismatching download; FAILED after that). A well-formed one
  that none of the keys verifies ("unknown_key": signed by a key this
  version does not know: a newer key, another deploy's signature, or not
  the site's) is FAILED like any other, with the normal retries, until it
  has been so on more than UNKNOWN_KEY_DAYS consecutive days: then
  UNSUPPORTED, said once at info (update the integration). Only this
  manifest is signed: a local upload or build (mav.py) is the user's own;
- the manifest's format is the one this code reads and its index schema is
  mav_index.SCHEMA (else it says so once and installs nothing);
- its feed is the one of the index www/ serves and the same-feed rule
  (served_index.replaces) lets it replace that one: a pfaedle-fitted
  daily index replaces a stop-sequence build of its own feed, and is never
  replaced by one;
- or its feed is newer, by the date its feed_version names, than the
  index www/ serves (one of another feed of the same date stays: a local
  build of that day wins), and newer than the local feed (/config/
  hungarian-transport/mav-gtfs.zip) by served_index.superseded, the rule
  startup uses. A local feed of the same date, or one that cannot be dated, keeps
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
RETRY_LIMIT times until the next daily round. A round that finds a manifest
whose feed is not of today, or one older than STALE_MANIFEST, is looked at
again LATE_DELAY later, up to LATE_LIMIT times after a first or daily round
and only that day: the workflow builds the day's feed around midday or in the
afternoon, not before the 06:00 check. GitHub Pages' cache (Fastly,
max-age=600) keys on the path alone, ignoring a query and a request's
no-cache, so for up to PAGES_MAX_AGE after a deploy the manifest and the
index can be of two generations: the first download that does not match its
manifest (by size or sha256) is looked at again PAGES_SETTLE_DELAY later,
with a debug line, and is no round (nothing recorded, nothing notified, no
retry used up); a download mismatching again before one matched (or before
the next first or daily round) is a FAILED round, whichever manifest it is
of: two generations taking turns would otherwise settle for ever. The
options flow can turn it off (CONF_DAILY_MAV); then no request is made at
all. Each round returns a DailyRound, which the updater hands to
timetable_notify.py and records for the sensors. The same rounds then run
the weekly Volán and city indexes (weekly_timetables.py), also with the
daily MÁV index turned off.

The card fetches mav-index.json.gz with a day-stamped URL and "no-cache":
Home Assistant's static files answer with an ETag of the file's mtime and
size, so a replaced index is downloaded on the next load of the dashboard.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import hashlib
import logging
import random
import re
import secrets
import zlib
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import aiohttp
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from homeassistant.core import CALLBACK_TYPE, HassJob, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import jsonsafe, mav, mav_index, served_index, storage
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Fixed: only this host, over HTTPS, without redirects.
PAGES_BASE = "https://bator.github.io/hungarian-transport"
MANIFEST_URL = f"{PAGES_BASE}/mav-index.json"
INDEX_URL = f"{PAGES_BASE}/mav-index.json.gz"
# One line: the base64 of the ed25519 signature (64 bytes) over the exact
# bytes of the manifest, made by the Pages workflow (scripts/sign_manifest.py),
# as timetables.json.sig is for weekly_timetables.py's manifest.
SIGNATURE_URL = f"{MANIFEST_URL}.sig"
MAX_SIGNATURE = 1024
SIGNATURE_BYTES = 64
# The public halves (raw 32 bytes, base64) of the keys the Pages workflow
# signs its manifests with (mav-index.json, timetables.json), the current
# one first; the private half is a secret of that workflow only. GitHub
# Pages serves whatever the repository's workflow deploys, and anything that
# answers for bator.github.io (a hijacked DNS, a TLS interception the
# system trusts) could serve a manifest naming its own files with their own
# sha256: the signature ties the manifest, and through its sha256 every
# file, to a key. A new key goes into this list a release before the site
# signs with it, and the old one leaves a release after; a version that
# does not know the site's key says so (UNKNOWN_KEY) and installs nothing.
# A key this version does not know looks the same as another deploy's
# signature the cache paired with the manifest, or as a signature that is
# not the site's at all: such a signature is FAILED (retried as any
# failure, a day of the MÁV failure streak) on UNKNOWN_KEY_DAYS
# consecutive days, and only after that UNSUPPORTED (update the
# integration), neither retried nor a failure.
PAGES_PUBLIC_KEYS: tuple[str, ...] = ("iUfHMDrTTf+3dV7JUu8ktyihkUfpkeTQRQxym955kq8=",)  # gitleaks:allow (public keys)
UNKNOWN_KEY_DAYS = 3
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
# MÁV publishes the day's feed in the morning, by 10:00 UTC on most days,
# sometimes in the afternoon, and GitHub starts the workflow's crons (04:45
# and 14:20 UTC) hours late: Pages has the day's feed around midday local
# time, or later. A round that finds the manifest's feed older than today
# (or a manifest older than STALE_MANIFEST) looks again LATE_DELAY (two to
# three hours) later, up to LATE_LIMIT times a day, never past local
# midnight nor past the next daily round: from 06:00, the last look is in
# the afternoon. A look is one manifest request (about 1 kB); the index is
# downloaded only when the feed is newer.
STALE_MANIFEST = timedelta(hours=20)
LATE_DELAY = (2 * 60 * 60, 3 * 60 * 60)
LATE_LIMIT = 4
# GitHub Pages serves the site through Fastly with Cache-Control:
# max-age=600, cached by the path alone: neither a query (?sha=...) nor a
# request's no-cache reaches past it (2026-11-15: every variant the same
# object of the same Age, x-cache HIT). For up to this long after a deploy
# the manifest and the index can be of two generations, either way round.
PAGES_MAX_AGE = 600
# A download that does not match its manifest is looked at again this much
# later, when both are the deploy's, before it counts as a failed round.
PAGES_SETTLE_DELAY = PAGES_MAX_AGE + 60
FEED_RE = re.compile(r"\d{8}(?!\d)[A-Za-z0-9._:+-]{0,64}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

# What one round did.
INSTALLED = "installed"
UP_TO_DATE = "up_to_date"
LOCAL_WINS = "local_wins"
BUSY = "busy"
UNSUPPORTED = "unsupported"
FAILED = "failed"
# Only in what a round records for the sensors (storage.DAILY_ROUND):
# UP_TO_DATE because www/ serves an index of another feed that is not older
# (a local build, a release's newer one), not this manifest's own.
SUPERSEDED = "superseded"
# Only between async_update and the updater, never recorded nor handed on:
# the download did not match its manifest for the first time, which right
# after a deploy is the Pages cache (PAGES_MAX_AGE).
SETTLING = "settling"
_INSTALL = "install"
# Why a round did not install (DailyRound.reason, storage's records, the
# sensors' reason attributes): short words, else the error's text cut to
# MAX_REASON. Never a secret: the errors name URLs, files and feeds.
REASON_RELEASE_NEWER = "release_newer"
REASON_SERVED_NEWER = "served_newer"
REASON_BAD_SIGNATURE = "bad_signature"
UNKNOWN_KEY = "unknown_key"
REASON_NOT_FOUND = "not_found"
REASON_MISMATCH = "mismatch"
# A body past the limit _get was given where that limit is a cap, not the
# size a manifest names (a manifest, the MÁV index past MAX_INDEX): no
# mismatch that settles, FAILED with the normal retries. A file longer than
# its manifest says is a mismatch (weekly_timetables.py turns TooLarge into
# one there).
REASON_TOO_LARGE = "too_large"
MAX_REASON = 120


@dataclass(frozen=True)
class DailyRound:
    """What one round did (result), and what it saw of the manifest."""

    result: str
    # The manifest's feed and when its index was built; None before the
    # manifest was read (or without a built_at).
    feed: str | None = None
    built_at: datetime | None = None
    # Why the round FAILED or was UNSUPPORTED.
    error: str | None = None
    # INSTALLED: the installed index's trains and lines.
    trains: int | None = None
    lines: int | None = None
    # Why it FAILED, was UNSUPPORTED or SETTLING (reason_of).
    reason: str | None = None


class DailyIndexError(Exception):
    """The manifest or the index could not be fetched or did not check out."""


class _Unsupported(DailyIndexError):
    """A manifest format or index schema this version does not read."""


class _Mismatch(DailyIndexError):
    """The download is not the index the manifest describes (its size or
    sha256): another generation's, or a broken file."""


class TooLarge(DailyIndexError):
    """The body is longer than the limit _get was given: for a file whose
    manifest names its size, another generation's (or a broken one; the
    caller says so with a _Mismatch); else past a cap (REASON_TOO_LARGE)."""


class NotFound(DailyIndexError):
    """HTTP 404. For a file a manifest names (weekly_timetables.py: an
    index, a shapes folder), right after a deploy that is a cached manifest
    of the previous generation naming what the new site no longer has."""


class BadSignature(DailyIndexError):
    """A manifest's .sig is missing, malformed or does not verify."""


class UnknownKey(BadSignature):
    """A well-formed signature that none of PAGES_PUBLIC_KEYS verifies over
    the manifest: signed by a key this version does not know: a newer key,
    another deploy's signature (the cache pairs a new manifest with the
    old .sig), or not the site's. They cannot be told apart."""


def verify_signature(
    raw: bytes, signature: bytes, name: str, keys: str | Iterable[str] | None = None
) -> None:
    """Raise BadSignature unless signature (name.sig: one line, base64 of 64
    bytes) is the ed25519 signature of raw, the manifest name, by one of
    keys (base64 of a raw 32-byte public key, or several; by default
    PAGES_PUBLIC_KEYS); UnknownKey when it is well-formed but none of them
    verifies it."""
    what = f"{name}.sig"
    try:
        sig = base64.b64decode(signature.decode("ascii").strip(), validate=True)
    except (UnicodeDecodeError, binascii.Error) as err:
        raise BadSignature(f"{what} is not base64: {err}") from err
    if len(sig) != SIGNATURE_BYTES:
        raise BadSignature(f"{what} is {len(sig)} bytes, not an ed25519 signature")
    if keys is None:
        keys = PAGES_PUBLIC_KEYS
    for key in (keys,) if isinstance(keys, str) else keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(sig, raw)
        except InvalidSignature:
            continue
        return
    raise UnknownKey(
        f"{name} is signed by a key this version does not know ({what}): a newer key, another deploy's"
        " signature, or not the site's"
    )


def reason_of(err: BaseException) -> str:
    """The reason a round records for err: a short word for the kinds the
    sensors tell apart, else the error's text, at most MAX_REASON long."""
    if isinstance(err, UnknownKey):
        return UNKNOWN_KEY
    if isinstance(err, BadSignature):
        return REASON_BAD_SIGNATURE
    if isinstance(err, NotFound):
        return REASON_NOT_FOUND
    if isinstance(err, _Mismatch):
        return REASON_MISMATCH
    if isinstance(err, TooLarge):
        return REASON_TOO_LARGE
    text = str(err) if isinstance(err, DailyIndexError) else f"{type(err).__name__}: {err}"
    return text[:MAX_REASON]


@dataclass(frozen=True)
class SignatureCheck:
    """Why a manifest's signature did not let it in: result is SETTLING (the
    first time: right after a deploy the cache can pair a new manifest with
    the old signature, or still answer 404 for it), UNSUPPORTED (a
    well-formed signature no key verifies, UNKNOWN_KEY, on more than
    UNKNOWN_KEY_DAYS consecutive days) or FAILED (anything else, an unknown
    key before that)."""

    result: str
    error: DailyIndexError

    @property
    def reason(self) -> str:
        """UNKNOWN_KEY, REASON_BAD_SIGNATURE for a signature that is missing
        or no signature, else the error's (HTTP 500, a timeout)."""
        if isinstance(self.error, UnknownKey):
            return UNKNOWN_KEY
        if isinstance(self.error, (BadSignature, TooLarge, NotFound)):
            return REASON_BAD_SIGNATURE
        return reason_of(self.error)


async def check_signature(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    url: str,
    raw: bytes,
    name: str,
    may_settle: Callable[[], bool],
) -> SignatureCheck | None:
    """None when the signature at url verifies raw, the manifest name; else
    why not. may_settle says whether a signature that does not match may
    settle (once per cycle and scope, as a download that does not match).
    A signature of a key this version does not know that does not settle
    counts its day (storage.async_unknown_key_day): FAILED on the first
    UNKNOWN_KEY_DAYS consecutive days, UNSUPPORTED after them. One that
    verifies ends those days."""
    try:
        sig = await _get(session, url, MAX_SIGNATURE, MANIFEST_TIMEOUT)
        verify_signature(raw, sig, name)
    except (BadSignature, TooLarge, NotFound) as err:
        if may_settle():
            return SignatureCheck(SETTLING, err)
        if isinstance(err, UnknownKey):
            days = await storage.async_unknown_key_day(hass, name, _today())
            return SignatureCheck(UNSUPPORTED if days > UNKNOWN_KEY_DAYS else FAILED, err)
        return SignatureCheck(FAILED, err)
    except DailyIndexError as err:
        return SignatureCheck(FAILED, err)
    await storage.async_clear_unknown_key(hass, name)
    return None


def signature_message(check: SignatureCheck) -> str:
    """What the log says of check: for an unknown key that is UNSUPPORTED,
    since when."""
    if check.result == UNSUPPORTED and isinstance(check.error, UnknownKey):
        return f"{check.error}; so on more than {UNKNOWN_KEY_DAYS} days in a row"
    return str(check.error)


# What a round may raise in the normal course of things: a download or a
# check that failed, a file that could not be written, the network. Anything
# else is a bug, logged with its traceback (once, like every failure).
EXPECTED_ERRORS: tuple[type[BaseException], ...] = (
    DailyIndexError, OSError, aiohttp.ClientError, asyncio.TimeoutError,
)


async def _get(
    session: aiohttp.ClientSession,
    url: str,
    limit: int,
    timeout: aiohttp.ClientTimeout,
    sink: Callable[[bytes], Awaitable[None]] | None = None,
) -> bytes:
    """GET url's body, at most limit bytes (TooLarge past it); DailyIndexError
    otherwise. With a sink (weekly_timetables.py writes shards to disk) each
    chunk goes to it as it comes, and b"" is returned: only one chunk is in
    memory at a time."""
    what = url.split("?", 1)[0].rsplit("/", 1)[-1]
    try:
        # no-cache for a cache between here and Pages; Pages' own ignores
        # it (PAGES_MAX_AGE).
        async with session.get(
            url, timeout=timeout, allow_redirects=False, headers={"Cache-Control": "no-cache"}
        ) as res:
            if 300 <= res.status < 400:
                # Redirects are not followed: the files are taken from
                # PAGES_BASE only. GitHub Pages redirects every github.io URL
                # once the repository has a custom domain; this says so,
                # rather than a bare status, until a version reads it there.
                where = str((res.headers or {}).get("Location", ""))[:200]
                raise DailyIndexError(
                    f"{what}: HTTP {res.status}, redirected to {where or 'nowhere'!r}; redirects are not "
                    f"followed (only {PAGES_BASE} is read; a custom GitHub Pages domain needs an update "
                    "of Hungarian transport)"
                )
            if res.status == 404:
                raise NotFound(f"{what}: HTTP 404")
            if res.status != 200:
                raise DailyIndexError(f"{what}: HTTP {res.status}")
            length = (res.headers or {}).get("Content-Length")
            if length is not None and length.isdigit() and int(length) > limit:
                raise TooLarge(f"{what}: {length} bytes, more than {limit}")
            body = bytearray()
            size = 0
            async for chunk in res.content.iter_chunked(1 << 16):
                size += len(chunk)
                if size > limit:
                    raise TooLarge(f"{what}: more than {limit} bytes")
                if sink is None:
                    body.extend(chunk)
                else:
                    await sink(chunk)
            return bytes(body)
    except TimeoutError as err:
        raise DailyIndexError(f"{what}: timed out") from err
    except aiohttp.ClientError as err:
        raise DailyIndexError(f"{what}: {type(err).__name__}: {err}") from err


def check_manifest(raw: bytes) -> dict[str, Any]:
    """The manifest's fields this module uses, checked."""
    try:
        man = jsonsafe.loads(raw)
    except (ValueError, RecursionError) as err:
        raise DailyIndexError(f"manifest is not JSON: {err}") from err
    if not isinstance(man, dict):
        raise DailyIndexError("manifest is not a JSON object")
    if man.get("format") != MANIFEST_FORMAT:
        raise _Unsupported(f"manifest format {man.get('format')!r}, this version reads {MANIFEST_FORMAT}")
    if man.get("schema") != mav_index.SCHEMA:
        raise _Unsupported(f"index schema {man.get('schema')!r}, this version reads {mav_index.SCHEMA}")
    feed, size, sha = man.get("feed"), man.get("size"), man.get("sha256")
    if not isinstance(feed, str) or not FEED_RE.fullmatch(feed) or served_index.feed_date(feed) is None:
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
        raise _Mismatch(f"index is {len(raw)} bytes, the manifest says {man['size']}")
    if hashlib.sha256(raw).hexdigest() != man["sha256"]:
        raise _Mismatch("index sha256 does not match the manifest")
    try:
        unzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
        text = unzip.decompress(raw, MAX_INDEX_JSON)
        if unzip.unconsumed_tail or not unzip.eof:
            raise DailyIndexError("index is truncated or unpacks to too much")
        if unzip.unused_data:
            # Another gzip member (or junk) after the first: checked would be
            # only the first, served would be the whole file.
            raise DailyIndexError("index has data after its gzip stream")
        # In the executor: jsonsafe refuses what nests deep enough to kill it.
        index = jsonsafe.loads(text)
    except (zlib.error, ValueError, RecursionError) as err:
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
    """When the manifest's index was built (its built_at, UTC), or None: also
    for one that looks like a time but is none ("2026-02-30T10:00:00Z", which
    parse_datetime raises on)."""
    value = man.get("built_at")
    try:
        parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        return None
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

    Of the same feed, the same-feed rule decides (served_index.replaces):
    UP_TO_DATE unless the daily index beats out. Of another feed:
    UP_TO_DATE when out is of one of the same date or newer; LOCAL_WINS when
    the local feed at src is not older (served_index.superseded, as at
    startup: same date, undated or newer keeps it; an expired one does not)
    and its index is the one out holds: out is of the feed's feed_version,
    or this integration built it from the feed as it is now (a feed whose
    build failed, was interrupted or was refused by the audit has none, and
    keeps nothing from the daily index).
    """
    current = served_index.read_feed(out)
    if current == feed:
        new = rank or served_index.LOCAL_BUILD
        return _INSTALL if served_index.replaces(new, out) else UP_TO_DATE
    if not served_index.newer_feed(feed, current):
        return UP_TO_DATE
    if src.is_file():
        info = served_index.source_info(src)
        if not served_index.superseded(info, feed, today) and (
            (info.version and current == info.version)
            or mav.serves_local_build(src, out, state_file or src.with_name(mav.STATE_NAME))
        ):
            return LOCAL_WINS
    return _INSTALL


# The failure last logged as a warning, kept across rounds: only this module reads it.
_LOGGED = "mav_daily_logged"
# The scopes (the index, the manifest's signature) that did not match their
# manifest once and settled; a scope leaves it when its download matched (or
# a round needed none), every scope at a first or daily round. While a
# scope is in it, a mismatch of that scope is a FAILED round: two
# generations taking turns (A, B, A, B) would otherwise settle for ever.
_SETTLED = "mav_daily_settled"
_INDEX = "index"
_SIGNATURE = "signature"


def _may_settle(hass: HomeAssistant, scope: str = _INDEX) -> bool:
    """Whether a download of scope that did not match its manifest may
    settle: the first one since that scope matched, or since the cycle
    began."""
    settled = hass.data.setdefault(DOMAIN, {}).setdefault(_SETTLED, set())
    if scope in settled:
        return False
    settled.add(scope)
    return True


def _matched(hass: HomeAssistant, scope: str | None = None) -> None:
    """scope matched (None: a new cycle, every scope)."""
    data = hass.data.setdefault(DOMAIN, {})
    if scope is None:
        data.pop(_SETTLED, None)
    else:
        data.setdefault(_SETTLED, set()).discard(scope)


def _log_failure(
    hass: HomeAssistant, message: str, unsupported: bool = False, exc_info: BaseException | None = None
) -> None:
    """Once per kind of failure; the same one again goes to debug. exc_info:
    a crash (no EXPECTED_ERRORS), logged with its traceback the first time."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_LOGGED) == message:
        _LOGGER.debug("Daily MÁV route index: %s", message)
        return
    data[_LOGGED] = message
    if unsupported:
        _LOGGER.info("Daily MÁV route index skipped: %s. Update Hungarian transport to use it", message)
    else:
        _LOGGER.warning(
            "Daily MÁV route index not updated: %s. The current index stays; trying again later", message,
            exc_info=exc_info,
        )


def _clear_failure(hass: HomeAssistant) -> None:
    hass.data.setdefault(DOMAIN, {}).pop(_LOGGED, None)


def _crashed(hass: HomeAssistant, err: Exception, feed: str | None = None, built: datetime | None = None) -> DailyRound:
    """A round that raised: FAILED, logged once like any failure; one that
    is not of EXPECTED_ERRORS with its traceback (a bug to report)."""
    message = f"{type(err).__name__}: {err}"
    expected = isinstance(err, EXPECTED_ERRORS)
    _log_failure(hass, message, exc_info=None if expected else err)
    if expected:
        _LOGGER.debug("Daily MÁV route index round failed", exc_info=err)
    return DailyRound(FAILED, feed, built, error=message, reason=message[:MAX_REASON])


async def async_update(hass: HomeAssistant, www: Path) -> DailyRound:
    """One round: fetch the manifest and, when its feed wins, install its index."""
    session = async_get_clientsession(hass)
    out = www / mav.INDEX_NAME
    src = mav.source_path(hass)
    state_file = mav.state_path(hass)
    try:
        raw = await _get(session, MANIFEST_URL, MAX_MANIFEST, MANIFEST_TIMEOUT)
    except DailyIndexError as err:
        _log_failure(hass, str(err))
        return DailyRound(FAILED, error=str(err), reason=reason_of(err))
    unsigned = await _signed(hass, session, raw)
    if unsigned is not None:
        return unsigned
    try:
        man = check_manifest(raw)
    except _Unsupported as err:
        _log_failure(hass, str(err), unsupported=True)
        return DailyRound(UNSUPPORTED, error=str(err), reason=reason_of(err))
    except DailyIndexError as err:
        _log_failure(hass, str(err))
        return DailyRound(FAILED, error=str(err), reason=reason_of(err))
    # When this round's manifest was built (timetable_notify.py tells a
    # stale site; DailyIndexUpdater looks again later).
    built = built_at(man)
    try:
        return await _install(hass, session, man, built, out, src, state_file)
    except asyncio.CancelledError:
        raise
    except Exception as err:  # noqa: BLE001 - a round that crashed is a failed one
        return _crashed(hass, err, man["feed"], built)


async def _signed(hass: HomeAssistant, session: aiohttp.ClientSession, raw: bytes) -> DailyRound | None:
    """None when mav-index.json.sig verifies raw; else the round, nothing
    installed (check_signature): SETTLING the first time, with a debug line;
    then FAILED, a warning once (also for a key this version does not know:
    a newer key, another deploy's signature, or not the site's, on its
    first UNKNOWN_KEY_DAYS days), or UNSUPPORTED for one known for longer,
    said once at info."""
    check = await check_signature(
        hass, session, SIGNATURE_URL, raw, "mav-index.json", partial(_may_settle, hass, _SIGNATURE)
    )
    if check is None:
        _matched(hass, _SIGNATURE)
        return None
    err = check.error
    if check.result == SETTLING:
        _LOGGER.debug(
            "Daily MÁV route index: %s; the GitHub Pages cache may still hold another generation, looking again"
            " in %d s", err, PAGES_SETTLE_DELAY,
        )
    elif check.result == UNSUPPORTED:
        _log_failure(hass, signature_message(check), unsupported=True)
    else:
        _log_failure(hass, signature_message(check))
    return DailyRound(check.result, error=str(err), reason=check.reason)


async def _install(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    man: dict[str, Any],
    built: datetime | None,
    out: Path,
    src: Path,
    state_file: Path,
) -> DailyRound:
    """The rest of a round, once the manifest checked out."""
    feed = man["feed"]

    def done(result: str, error: str | None = None, reason: str | None = None, **installed: int) -> DailyRound:
        return DailyRound(result, feed, built, error, reason=reason, **installed)

    decision = await hass.async_add_executor_job(
        partial(decide, feed, out, src, _today(), state_file=state_file, rank=manifest_rank(man))
    )
    if decision != _INSTALL:
        _clear_failure(hass)
        _matched(hass, _INDEX)
        _LOGGER.debug("Daily MÁV route index of feed %s: %s", feed, decision)
        return done(decision)
    if mav.build_pending(hass):
        _LOGGER.debug("Daily MÁV route index of feed %s: a local build runs; trying again later", feed)
        return done(BUSY)
    try:
        raw = await _get(session, INDEX_URL, MAX_INDEX, INDEX_TIMEOUT)
        count, rank = await hass.async_add_executor_job(check_index, raw, man, mav.MIN_TRAINS)
    except _Mismatch as err:
        if not _may_settle(hass):
            _log_failure(hass, str(err))
            return done(FAILED, str(err), reason_of(err))
        # Right after a deploy the Pages cache can still hold the other
        # generation of one of the two files (PAGES_MAX_AGE).
        _LOGGER.debug(
            "Daily MÁV route index of feed %s: %s; the GitHub Pages cache may still hold another"
            " generation, looking again in %d s", feed, err, PAGES_SETTLE_DELAY,
        )
        return done(SETTLING, str(err), reason_of(err))
    except DailyIndexError as err:
        _log_failure(hass, str(err))
        return done(FAILED, str(err), reason_of(err))
    _matched(hass, _INDEX)
    # Checked again with nothing awaited before the lock is taken: a build
    # (an upload) that started during the download goes first.
    if mav.build_pending(hass):
        _LOGGER.debug("Daily MÁV route index of feed %s: a local build runs; trying again later", feed)
        return done(BUSY)
    async with mav.build_lock(hass):
        # The index or the local feed may have changed during the download;
        # the rank is the downloaded index's own now.
        decision = await hass.async_add_executor_job(
            partial(decide, feed, out, src, _today(), state_file=state_file, rank=rank)
        )
        if decision != _INSTALL:
            _clear_failure(hass)
            return done(decision)
        try:
            await hass.async_add_executor_job(mav_index.write_bytes, raw, out, secrets.token_hex(4))
        except OSError as err:
            message = f"could not write {out}: {err}"
            _log_failure(hass, message)
            return done(FAILED, message, message[:MAX_REASON])
    _clear_failure(hass)
    audit = man.get("audit") if isinstance(man.get("audit"), dict) else {}
    await storage.async_record_mav_install(hass, storage.SOURCE_DAILY, feed, len(raw), audit.get("ok_ratio"))
    _LOGGER.info(
        "Installed the daily MÁV route index of feed %s (%s trains and lines, built %s, audit %s/%s trains)",
        feed, count, man.get("built_at"), audit.get("trains_ok"), audit.get("trains"),
    )
    # What timetable_notify.py tells about it.
    return done(
        INSTALLED,
        trains=man["trains"] if isinstance(man.get("trains"), int) else count,
        lines=man["lines"] if isinstance(man.get("lines"), int) else 0,
    )


def _today() -> date:
    """Today in Home Assistant's time zone: a feed ends at the end of its day."""
    return dt_util.now().date()


def _behind(done: DailyRound) -> bool:
    """Whether the round saw a manifest that is not yet the day's: its feed is
    of an earlier day than today (local time), or it was built more than
    STALE_MANIFEST ago."""
    day = served_index.feed_date(done.feed)
    if day is not None and day < _today():
        return True
    return done.built_at is not None and dt_util.utcnow() - done.built_at > STALE_MANIFEST


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
    RETRY_LIMIT times since the last first or daily round. A round that went
    through while the manifest's feed was not of today (or the manifest
    older than STALE_MANIFEST) is looked at again LATE_DELAY later, at most
    LATE_LIMIT times since the last first or daily round, only the same
    local day and before the next daily time. A SETTLING one is no round:
    it is looked at again PAGES_SETTLE_DELAY later, using up no retry, and
    neither handed to on_result nor recorded. One round is pending at a
    time: the daily time replaces a retry or a late look still waiting.
    on_result gets each round's DailyRound (timetable_notify.py); then the
    round is recorded for the sensors (storage.DAILY_ROUND), and next_run
    says when the next one is due.

    The weekly Volán and city timetables (weekly_timetables.py) share the
    schedule: timetables, when given, is started after the MÁV part of every
    first and daily round (not after its retries, late looks or settling
    looks, which are MÁV's own), in a task of its own, with retries of its
    own. A first or daily time that comes while a retry or a late look still
    runs (a slow download) is that round: its retries and late looks start
    over at once, and the timetables round starts when it ends. With mav
    False (the daily MÁV index turned off) the rounds run the timetables
    alone and make no MÁV request."""

    def __init__(
        self,
        hass: HomeAssistant,
        www: Path,
        daily_time: time = time(6),
        on_result: Callable[[DailyRound], Awaitable[None]] | None = None,
        *,
        mav: bool = True,
        timetables: Any = None,
    ) -> None:
        self._hass = hass
        self._www = www
        self._time = daily_time
        self._on_result = on_result
        self._mav = mav
        # weekly_timetables.PagesTimetables (not imported: it imports this module).
        self._timetables = timetables
        self._unsub: CALLBACK_TYPE | None = None
        self._pending_at: datetime | None = None
        self._task: asyncio.Task[Any] | None = None
        self._stopped = False
        self._next = _FIRST
        self._retries = 0
        self._late = 0
        # A first or daily time came while the round of a retry or a late
        # look ran: that round starts the timetables round when it ends.
        self._cycle_pending = False
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
            self._pending_at = None
            if self._task is not None and not self._task.done():
                self._task.cancel()
            if self._timetables is not None:
                self._timetables.stop()

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
        self._pending_at = dt_util.utcnow() + timedelta(seconds=delay)
        storage.async_signal(self._hass)

    @property
    def next_run(self) -> datetime | None:
        """When the next round is due (UTC): the one pending, else the next
        daily time (without its jitter); None once stopped."""
        if self._stopped:
            return None
        if self._pending_at is not None:
            return self._pending_at
        return dt_util.as_utc(self._next_daily(dt_util.now()))

    def _next_daily(self, now: datetime) -> datetime:
        """The next daily time after now (local, without its jitter)."""
        day = now.date()
        at = datetime.combine(day, self._time, tzinfo=now.tzinfo)
        if at <= now:
            at = datetime.combine(day + timedelta(days=1), self._time, tzinfo=now.tzinfo)
        return at

    @callback
    def _fire(self, _now: Any) -> None:
        self._unsub = None
        self._pending_at = None
        kind = self._next
        if kind in (_FIRST, _DAILY):
            # A new cycle: its own retries, late looks and settling.
            self._retries = 0
            self._late = 0
            _matched(self._hass)
        if self._task is not None and not self._task.done():
            # The previous round still runs (a slow download): it is this
            # one. When it was a retry or a late look, the day's timetables
            # round would be lost with it: it starts when that one ends.
            if kind in (_FIRST, _DAILY):
                self._cycle_pending = True
            return
        self._task = self._hass.async_create_background_task(self._run(kind), "hungarian_transport_mav_daily")

    @callback
    def _after(self, done: DailyRound) -> None:
        """Schedule a retry or a late look after a round, unless another
        round is already pending (the daily time came during this one)."""
        if self._stopped or self._unsub is not None:
            return
        result = done.result
        if result in (BUSY, FAILED):
            if self._retries < RETRY_LIMIT:
                self._retries += 1
                self._schedule(RETRY_DELAY, _RETRY)
            return
        if result == UNSUPPORTED or self._late >= LATE_LIMIT or not _behind(done):
            return
        delay = random.uniform(*LATE_DELAY)
        now = dt_util.now()
        at = now + timedelta(seconds=delay)
        if at.date() != now.date() or at >= self._next_daily(now):
            # The daily round looks first; and after midnight a feed of the
            # day is not out before morning.
            return
        self._late += 1
        self._schedule(delay, _LATE)

    async def _run(self, kind: str = _DAILY) -> None:
        if self._mav:
            await self._run_mav()
        cycle = kind in (_FIRST, _DAILY) or self._cycle_pending
        self._cycle_pending = False
        if self._timetables is not None and cycle and not self._stopped:
            # After the MÁV part, failed or not: its own round in its own
            # task, its own retries. This task ends here, so a MÁV retry,
            # late or settling look is never dropped for a slow download.
            self._timetables.start_round()

    async def _run_mav(self) -> None:
        try:
            done = await async_update(self._hass, self._www)
        except asyncio.CancelledError:
            # Stopped (unloaded, Home Assistant stopping): no next round.
            raise
        except Exception as err:  # noqa: BLE001 - logged once, the next round tries again
            done = _crashed(self._hass, err)
        if done.result == SETTLING:
            if not self._stopped and self._unsub is None:
                self._schedule(PAGES_SETTLE_DELAY, _RETRY)
            return
        self._after(done)
        if self._on_result is not None:
            try:
                await self._on_result(done)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a notification never stops the updater
                _LOGGER.warning("Timetable update notification failed", exc_info=True)
        await self._record(done)

    async def _record(self, done: DailyRound) -> None:
        """The round for the sensors, after on_result (its failure streak)."""
        result, feed = done.result, done.feed
        if result == UP_TO_DATE and feed:
            served = await self._hass.async_add_executor_job(served_index.read_feed, self._www / mav.INDEX_NAME)
            if served and served != feed:
                result = SUPERSEDED
        await storage.async_record_daily_round(self._hass, result, feed, done.built_at, done.reason)
