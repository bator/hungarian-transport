"""GTFS-Realtime delays and vehicle positions for the Volán, city and MÁV cards.

The cards' timetables (volan-index, city-index, the MÁV index) are static.
For the operators listed in realtime_feeds.json this module fetches their
public GTFS-Realtime feeds on demand, when a card calls the
`hungarian_transport.realtime` service, and answers with the delay at the
card's stops and the vehicle's position for the trips the card asks about.

Nothing is polled in the background: a feed is fetched only while a card
asks for it, at most once per its interval (60 s by default, the feeds' own
update cadence; see docs/realtime.md), and every card and browser shares the
one cached copy. The protobuf messages are read by the small decoder below:
Home Assistant does not ship the `protobuf` package (it is only a version
constraint in its package_constraints.txt), and the cards need a handful of
fields.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import csv
from dataclasses import dataclass, field
import io
import json
import logging
from pathlib import Path
import re
import struct
import time
from typing import Any
import zipfile
import zlib

import aiohttp

from . import gtfs_feed
from .responses import RT_NO_FEED, RT_UNAVAILABLE, error_answer

_LOGGER = logging.getLogger(__name__)

FEEDS_FILE = Path(__file__).parent / "realtime_feeds.json"
USER_AGENT = (
    "Hungarian-transport/1.0 (Home Assistant integration; realtime; "
    "+https://github.com/bator/hungarian-transport)"
)
# The feeds are regenerated about once a minute (vehicle positions every
# ~54 s, trip updates every ~60-70 s, measured 2026-11-02): asking more often
# only repeats the same bytes. Never below MIN_INTERVAL, whatever the table says.
DEFAULT_INTERVAL = 60
MIN_INTERVAL = 30
# A feed that failed is not asked again for this long.
FAIL_BACKOFF = 60
# The MÁV and GYSEV static feeds (train numbers) change weekly. A refresh
# that fails keeps the copy from before: its trip ids stay valid for the
# whole timetable period, and they only name trips for the live feeds,
# which are never kept past a failure.
STATIC_INTERVAL = 12 * 3600
STATIC_FAIL_BACKOFF = 15 * 60
FETCH_TIMEOUT = aiohttp.ClientTimeout(total=20)
# Decompressed size caps: the Volán intercity trip updates are ~1.9 MB.
MAX_FEED_BYTES = 8 * 1024 * 1024
MAX_STATIC_BYTES = 30 * 1024 * 1024
MAX_STATIC_MEMBER = 40 * 1024 * 1024
MAX_ENTITIES = 30000
MAX_STOP_UPDATES = 400
# Stop updates in one feed: the Volán intercity feed has ~56,000. A few bytes
# each on the wire but an object each in memory, so a feed over this is
# refused rather than kept.
MAX_FEED_STOP_UPDATES = 200000
MAX_STRING = 256
# Upstream fetches (cache misses only) per second and the burst on top: every
# logged-in user can call the service, and a loop over feed ids must not
# turn into a stream of requests to a volunteer's server.
FETCH_RATE = 0.5
FETCH_BURST = 30
# Delays outside this range are a feed glitch (a vehicle logged on to the
# wrong trip), not a prediction worth showing.
MIN_DELAY = -20 * 60
MAX_DELAY = 6 * 3600
# A position older than this is not where the vehicle is.
MAX_POSITION_AGE = 15 * 60
# A feed whose header is older than this is a stalled publisher still
# serving its last file, not live data.
MAX_FEED_AGE = 15 * 60
# Service request limits.
MAX_TRIPS = 40
MAX_STOPS = 120
# Trip id patterns whose route a source remembers (see _learn_routes).
MAX_ROUTE_KEYS = 20000

FEED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# OTP-exported city feeds (mvkzrt, VBUSZ, debrecen) carry the trip's start
# time in its id: 10842_1943_081900_AZN starts at 08:19.
_START_IN_TRIP_ID = re.compile(r"^[^_]+_[^_]+_(\d{2})(\d{2})(\d{2})(?:_|$)")
# The part before the start time is the trip's pattern (10842_1943): every
# trip of a pattern has the same route_id (VBUSZ, mvkzrt, debrecen checked
# 2026-11-09, no pattern on two routes).
_PATTERN_IN_TRIP_ID = re.compile(r"^([^_]+_[^_]+)_\d{6}(?:_|$)")
_DIGITS = re.compile(r"\d+")

TaskFactory = Callable[[Coroutine[Any, Any, Any]], "asyncio.Future[Any]"]
_monotonic = time.monotonic


class RtDecodeError(ValueError):
    """Not a GTFS-Realtime FeedMessage (truncated, or not protobuf at all)."""


# --------------------------------------------------------------------------
# Protobuf wire format: just enough of it for gtfs-realtime.proto.


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    end = len(buf)
    while True:
        if pos >= end or shift > 63:
            raise RtDecodeError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes):
    """(field number, wire type, value) of one message: an int for a varint,
    the bytes of a length-delimited field or of a fixed32/fixed64."""
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = _varint(buf, pos)
        elif wire == 2:
            size, pos = _varint(buf, pos)
            if pos + size > end:
                raise RtDecodeError("truncated field")
            val = buf[pos:pos + size]
            pos += size
        elif wire == 5:
            if pos + 4 > end:
                raise RtDecodeError("truncated fixed32")
            val = buf[pos:pos + 4]
            pos += 4
        elif wire == 1:
            if pos + 8 > end:
                raise RtDecodeError("truncated fixed64")
            val = buf[pos:pos + 8]
            pos += 8
        else:
            raise RtDecodeError(f"unsupported wire type {wire}")
        if num == 0:
            raise RtDecodeError("field number 0")
        yield num, wire, val


def _int64(value: int) -> int:
    """A varint read as int32/int64: negative numbers are ten-byte two's
    complement on the wire."""
    value &= (1 << 64) - 1
    return value - (1 << 64) if value >= 1 << 63 else value


def _text(value: Any) -> str:
    if not isinstance(value, bytes) or len(value) > MAX_STRING:
        return ""
    return value.decode("utf-8", "replace")


def _float(value: Any) -> float | None:
    if not isinstance(value, bytes) or len(value) != 4:
        return None
    out = struct.unpack("<f", value)[0]
    return out if out == out and abs(out) != float("inf") else None


@dataclass(slots=True)
class StopUpdate:
    """One StopTimeUpdate; a delay or time is None when the feed has none."""

    stop_id: str
    seq: int | None
    arr_delay: int | None
    arr_time: int | None
    dep_delay: int | None
    dep_time: int | None
    # 0 SCHEDULED, 1 SKIPPED, 2 NO_DATA.
    relationship: int = 0


@dataclass(slots=True)
class TripUpdate:
    trip_id: str
    start_date: str
    start_time: str
    route_id: str
    # TripDescriptor.schedule_relationship: 3 = CANCELED.
    relationship: int
    delay: int | None
    stops: list[StopUpdate]
    # route_id is one _fill_routes remembered for its pattern, not its own.
    route_learned: bool = False


@dataclass(slots=True)
class Vehicle:
    trip_id: str
    start_date: str
    route_id: str
    lat: float
    lon: float
    bearing: float | None
    speed: float | None
    timestamp: int | None
    stop_id: str
    current_seq: int | None
    status: int | None


@dataclass(slots=True)
class Alert:
    header: str
    start: int | None
    end: int | None
    routes: list[str]
    stops: list[str]
    trips: list[str]


@dataclass(slots=True)
class Feed:
    timestamp: int | None = None
    trip_updates: dict[str, TripUpdate] = field(default_factory=dict)
    vehicles: dict[str, Vehicle] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)


def _strip(value: str, prefix: str) -> str:
    return value[len(prefix):] if prefix and value.startswith(prefix) else value


def _trip_descriptor(buf: bytes, prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {"trip_id": "", "start_time": "", "start_date": "", "route_id": "", "rel": 0}
    for num, wire, val in _fields(buf):
        if num == 1 and wire == 2:
            out["trip_id"] = _strip(_text(val), prefix)
        elif num == 2 and wire == 2:
            out["start_time"] = _text(val)
        elif num == 3 and wire == 2:
            out["start_date"] = _text(val)
        elif num == 4 and wire == 0:
            out["rel"] = val
        elif num == 5 and wire == 2:
            out["route_id"] = _strip(_text(val), prefix)
    return out


def _stop_time_event(buf: bytes) -> tuple[int | None, int | None]:
    delay = when = None
    for num, wire, val in _fields(buf):
        if num == 1 and wire == 0:
            delay = _int64(val)
        elif num == 2 and wire == 0:
            when = _int64(val)
    return delay, when


def _stop_time_update(buf: bytes, prefix: str) -> StopUpdate:
    seq = None
    stop_id = ""
    arr = dep = (None, None)
    rel = 0
    for num, wire, val in _fields(buf):
        if num == 1 and wire == 0:
            seq = val
        elif num == 2 and wire == 2:
            arr = _stop_time_event(val)
        elif num == 3 and wire == 2:
            dep = _stop_time_event(val)
        elif num == 4 and wire == 2:
            stop_id = _strip(_text(val), prefix)
        elif num == 5 and wire == 0:
            rel = val
    return StopUpdate(stop_id, seq, arr[0], arr[1], dep[0], dep[1], rel)


def _trip_update(buf: bytes, prefix: str) -> TripUpdate | None:
    desc: dict[str, Any] | None = None
    stops: list[StopUpdate] = []
    delay = None
    for num, wire, val in _fields(buf):
        if num == 1 and wire == 2:
            desc = _trip_descriptor(val, prefix)
        elif num == 2 and wire == 2:
            if len(stops) < MAX_STOP_UPDATES:
                stops.append(_stop_time_update(val, prefix))
        elif num == 5 and wire == 0:
            delay = _int64(val)
    if not desc or not desc["trip_id"]:
        return None
    return TripUpdate(
        desc["trip_id"], desc["start_date"], desc["start_time"], desc["route_id"], desc["rel"], delay, stops
    )


def _vehicle_position(buf: bytes, prefix: str) -> Vehicle | None:
    desc: dict[str, Any] | None = None
    lat = lon = bearing = speed = None
    ts = seq = status = None
    stop_id = ""
    for num, wire, val in _fields(buf):
        if num == 1 and wire == 2:
            desc = _trip_descriptor(val, prefix)
        elif num == 2 and wire == 2:
            for pnum, pwire, pval in _fields(val):
                if pwire != 5:
                    continue
                if pnum == 1:
                    lat = _float(pval)
                elif pnum == 2:
                    lon = _float(pval)
                elif pnum == 3:
                    bearing = _float(pval)
                elif pnum == 5:
                    speed = _float(pval)
        elif num == 3 and wire == 0:
            seq = val
        elif num == 4 and wire == 0:
            status = val
        elif num == 5 and wire == 0:
            ts = val
        elif num == 7 and wire == 2:
            stop_id = _strip(_text(val), prefix)
    if not desc or not desc["trip_id"] or lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
        return None
    return Vehicle(
        desc["trip_id"], desc["start_date"], desc["route_id"], lat, lon, bearing, speed, ts, stop_id, seq, status
    )


def _translated(buf: bytes) -> str:
    first = ""
    for num, wire, val in _fields(buf):
        if num != 1 or wire != 2:
            continue
        text = lang = ""
        for tnum, twire, tval in _fields(val):
            if tnum == 1 and twire == 2:
                text = _text(tval)
            elif tnum == 2 and twire == 2:
                lang = _text(tval)
        if lang.lower().startswith("hu") and text:
            return text
        first = first or text
    return first


def _alert(buf: bytes, prefix: str) -> Alert:
    out = Alert("", None, None, [], [], [])
    for num, wire, val in _fields(buf):
        if num == 1 and wire == 2 and out.start is None and out.end is None:
            for rnum, rwire, rval in _fields(val):
                if rnum == 1 and rwire == 0:
                    out.start = rval
                elif rnum == 2 and rwire == 0:
                    out.end = rval
        elif num == 5 and wire == 2:
            for snum, swire, sval in _fields(val):
                if snum == 2 and swire == 2:
                    out.routes.append(_strip(_text(sval), prefix))
                elif snum == 4 and swire == 2:
                    trip = _trip_descriptor(sval, prefix)["trip_id"]
                    if trip:
                        out.trips.append(trip)
                elif snum == 5 and swire == 2:
                    out.stops.append(_strip(_text(sval), prefix))
        elif num == 10 and wire == 2:
            out.header = _translated(val)
    return out


def decode_feed(data: bytes, prefix: str = "") -> Feed:
    """A FeedMessage's trip updates, vehicle positions and alerts, keyed by
    trip id with the feed's own prefix (hkir:, 1:) taken off. Raises
    RtDecodeError on anything that is not a well-formed message."""
    if not isinstance(data, (bytes, bytearray)):
        raise RtDecodeError("not bytes")
    feed = Feed()
    count = 0
    stop_updates = 0
    has_header = False
    for num, wire, val in _fields(bytes(data)):
        if num == 1 and wire == 2:
            has_header = True
            for hnum, hwire, hval in _fields(val):
                if hnum == 3 and hwire == 0:
                    feed.timestamp = hval
        elif num == 2 and wire == 2:
            count += 1
            if count > MAX_ENTITIES:
                break
            deleted = False
            for enum, ewire, eval_ in _fields(val):
                if enum == 2 and ewire == 0:
                    deleted = bool(eval_)
                elif deleted:
                    continue
                elif enum == 3 and ewire == 2:
                    tu = _trip_update(eval_, prefix)
                    if tu is not None:
                        stop_updates += len(tu.stops)
                        if stop_updates > MAX_FEED_STOP_UPDATES:
                            raise RtDecodeError(f"over {MAX_FEED_STOP_UPDATES} stop updates")
                        feed.trip_updates[tu.trip_id] = tu
                elif enum == 4 and ewire == 2:
                    vp = _vehicle_position(eval_, prefix)
                    if vp is not None:
                        feed.vehicles[vp.trip_id] = vp
                elif enum == 5 and ewire == 2:
                    feed.alerts.append(_alert(eval_, prefix))
    if not has_header:
        raise RtDecodeError("no FeedHeader")
    return feed


def train_numbers_from_static(data: bytes, prefix: str = "") -> dict[str, list[str]]:
    """Train number -> trip ids (prefix taken off) from a GTFS zip's
    trips.txt trip_short_name (SZ 4510, InterCity 181), read as every
    builder reads a feed (gtfs_feed). A zip without trips.txt raises
    FileNotFoundError."""
    out: dict[str, list[str]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            real = gtfs_feed.member(zf, "trips.txt")
            if zf.getinfo(real).file_size > MAX_STATIC_MEMBER:
                raise ValueError("trips.txt too large")
            for row in gtfs_feed.csv_rows(zf, real):
                trip_id = _strip(row.get("trip_id") or "", prefix)
                found = _DIGITS.findall(row.get("trip_short_name") or "")
                if not trip_id or not found:
                    continue
                number = found[-1].lstrip("0") or found[-1]
                out.setdefault(number, []).append(trip_id)
    # A member that is corrupt, encrypted or packed with a method zipfile
    # lacks, or a CSV line it can't read: an outage like a bad download.
    except (zlib.error, EOFError, RuntimeError, NotImplementedError, csv.Error) as err:
        raise ValueError(f"unreadable static GTFS: {err or type(err).__name__}") from err
    return out


# --------------------------------------------------------------------------
# The feed table.


@dataclass(frozen=True, slots=True)
class Source:
    trip_updates: str
    vehicle_positions: str
    prefix: str
    static: str


@dataclass(frozen=True, slots=True)
class FeedConfig:
    feed_id: str
    sources: tuple[Source, ...]
    match: frozenset[str]
    interval: int
    trip_updates_interval: int


def _https(url: Any) -> str:
    text = str(url or "")
    return text if text.startswith("https://") else ""


def parse_feed_table(doc: dict[str, Any]) -> dict[str, FeedConfig]:
    """realtime_feeds.json as FeedConfigs; aliases point at another id.
    Malformed entries are skipped (and logged), not fatal."""
    out: dict[str, FeedConfig] = {}
    for feed_id, raw in (doc.get("feeds") or {}).items():
        try:
            if not FEED_ID_RE.match(feed_id):
                raise ValueError("bad id")
            sources = []
            for src in raw.get("sources") or []:
                tu, vp = _https(src.get("trip_updates")), _https(src.get("vehicle_positions"))
                if not tu and not vp:
                    raise ValueError("source without https URLs")
                sources.append(Source(tu, vp, str(src.get("prefix") or ""), _https(src.get("static"))))
            match = frozenset(str(m) for m in raw.get("match") or ()) & {"trip", "route_start", "train"}
            if not sources or not match:
                raise ValueError("no sources or match")
            interval = max(MIN_INTERVAL, int(raw.get("interval") or DEFAULT_INTERVAL))
            tu_interval = max(interval, int(raw.get("trip_updates_interval") or interval))
            out[feed_id] = FeedConfig(feed_id, tuple(sources), match, interval, tu_interval)
        except (AttributeError, TypeError, ValueError) as err:
            _LOGGER.warning("Skipping realtime feed %r in %s: %s", feed_id, FEEDS_FILE.name, err)
    for alias, target in (doc.get("aliases") or {}).items():
        if FEED_ID_RE.match(str(alias)) and target in out and alias not in out:
            out[alias] = out[target]
    return out


def load_feed_table(path: Path = FEEDS_FILE) -> dict[str, FeedConfig]:
    with path.open(encoding="utf-8") as fh:
        return parse_feed_table(json.load(fh))


# --------------------------------------------------------------------------
# Fetching and caching.


class RealtimeRateLimited(Exception):
    """Over FETCH_RATE: the feed was not asked for."""


@dataclass(slots=True)
class _Entry:
    at: float = -1e18
    ok: bool = False
    data: Any = None
    etag: str = ""
    last_modified: str = ""
    down: bool = False


class RealtimeHub:
    """On-demand GTFS-Realtime fetches, shared by every card and browser."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        executor: Callable[..., "asyncio.Future[Any]"] | None = None,
        task_factory: TaskFactory | None = None,
        feeds: dict[str, FeedConfig] | None = None,
    ) -> None:
        self._session = session
        self._executor = executor
        self._task_factory: TaskFactory = task_factory or asyncio.ensure_future
        self._feeds = feeds
        self._entries: dict[str, _Entry] = {}
        # Per vehicle positions URL: trip id pattern -> route (_learn_routes).
        self._routes: dict[str, dict[str, str]] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._tokens = float(FETCH_BURST)
        self._tokens_at = _monotonic()
        self._refused = 0
        self._refused_logged: float | None = None

    async def _run(self, func: Callable[..., Any], *args: Any) -> Any:
        if self._executor is None:
            return func(*args)
        return await self._executor(func, *args)

    async def feeds(self) -> dict[str, FeedConfig]:
        if self._feeds is None:
            try:
                self._feeds = await self._run(load_feed_table)
            except (OSError, ValueError) as err:
                _LOGGER.error("Cannot read %s: %s", FEEDS_FILE, err)
                self._feeds = {}
        return self._feeds

    def _take_token(self) -> None:
        now = _monotonic()
        self._tokens = min(float(FETCH_BURST), self._tokens + max(0.0, now - self._tokens_at) * FETCH_RATE)
        self._tokens_at = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return
        self._refused += 1
        if self._refused_logged is None or now - self._refused_logged >= 300:
            _LOGGER.warning(
                "Realtime feed requests over the integration's rate limit: %d refused since the last such line",
                self._refused,
            )
            self._refused = 0
            self._refused_logged = now
        raise RealtimeRateLimited

    async def _download(self, url: str, entry: _Entry, cap: int) -> tuple[bytes | None, str, str]:
        """(body, ETag, Last-Modified), body None for 304 Not Modified. The
        validators are the caller's to keep once the body parses."""
        headers = {"User-Agent": USER_AGENT, "Accept": "application/x-protobuf, application/octet-stream, */*"}
        # Conditional only while there is a copy the validators belong to:
        # they are stored with the copy (_refresh), so a body that does not
        # parse leaves those of the copy kept.
        held = entry.data is not None
        if held and entry.etag:
            headers["If-None-Match"] = entry.etag
        if held and entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        async with self._session.get(url, headers=headers, timeout=FETCH_TIMEOUT, allow_redirects=False) as resp:
            if resp.status == 304 and held:
                return None, entry.etag, entry.last_modified
            if resp.status != 200:
                raise ValueError(f"HTTP {resp.status}")
            length = getattr(resp, "content_length", None)
            if length is not None and length > cap:
                raise ValueError(f"{length} bytes, over {cap}")
            chunks = []
            total = 0
            async for chunk in resp.content.iter_chunked(65536):
                total += len(chunk)
                if total > cap:
                    raise ValueError(f"over {cap} bytes")
                chunks.append(chunk)
            return (
                b"".join(chunks),
                resp.headers.get("ETag", "") or "",
                resp.headers.get("Last-Modified", "") or "",
            )

    async def _cached(
        self,
        url: str,
        interval: float,
        backoff: float,
        cap: int,
        parse: Callable[[bytes], Any],
        what: str,
        keep: bool = False,
    ) -> Any:
        """The parsed body of url, at most one fetch per interval; None while
        it fails, or with keep the last good copy (one warning when it starts
        failing, one line when it answers again)."""
        entry = self._entries.setdefault(url, _Entry())
        age = _monotonic() - entry.at
        if age < (interval if entry.ok else backoff):
            return entry.data if entry.ok or keep else None
        task = self._inflight.get(url)
        if task is None:
            task = self._task_factory(self._refresh(url, entry, cap, parse, what, keep))
            self._inflight[url] = task
            task.add_done_callback(lambda _t: self._inflight.pop(url, None))
        return await asyncio.shield(task)

    async def _refresh(
        self, url: str, entry: _Entry, cap: int, parse: Callable[[bytes], Any], what: str, keep: bool = False
    ) -> Any:
        try:
            self._take_token()
        except RealtimeRateLimited:
            return entry.data if entry.ok or keep else None
        try:
            body, etag, last_modified = await self._download(url, entry, cap)
            data = entry.data if body is None else await self._run(parse, body)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError, zipfile.BadZipFile) as err:
            entry.at = _monotonic()
            entry.ok = False
            if not keep:
                entry.data = None
            if not entry.down:
                entry.down = True
                _LOGGER.warning(
                    "%s %s unavailable: %s%s", what, url, err or type(err).__name__,
                    "; keeping the copy from before" if entry.data is not None else "",
                )
            return entry.data
        entry.at = _monotonic()
        entry.ok = True
        entry.data = data
        entry.etag = etag
        entry.last_modified = last_modified
        if entry.down:
            entry.down = False
            _LOGGER.info("%s %s answers again", what, url)
        return data

    async def _feed(self, url: str, prefix: str, interval: int) -> Feed | None:
        if not url:
            return None
        return await self._cached(
            url, interval, FAIL_BACKOFF, MAX_FEED_BYTES, lambda body: decode_feed(body, prefix), "Realtime feed"
        )

    async def _trains(self, src: Source) -> dict[str, list[str]] | None:
        if not src.static:
            return None
        return await self._cached(
            src.static,
            STATIC_INTERVAL,
            STATIC_FAIL_BACKOFF,
            MAX_STATIC_BYTES,
            lambda body: train_numbers_from_static(body, src.prefix),
            "Realtime static feed",
            keep=True,
        )

    async def query(self, feed_id: str, trips: list[dict[str, Any]], now: float | None = None) -> dict[str, Any]:
        """The service's answer: per requested trip key, what the feed knows."""
        feeds = await self.feeds()
        cfg = feeds.get(feed_id)
        if cfg is None:
            return error_answer({"feed": feed_id, "trips": {}}, RT_NO_FEED)
        now = time.time() if now is None else now
        # Every source at once: MÁV has two, each up to FETCH_TIMEOUT cold.
        loaded = await asyncio.gather(*(self._source(cfg, src, now) for src in cfg.sources))
        if all(tu is None and vp is None for _src, tu, vp, _tr in loaded):
            return error_answer({"feed": feed_id, "trips": {}}, RT_UNAVAILABLE)
        stamps = [f.timestamp for _s, tu, vp, _t in loaded for f in (tu, vp) if f is not None and f.timestamp]
        out: dict[str, Any] = {}
        for req in trips:
            hit = _answer(cfg, loaded, req, now)
            if hit is not None:
                out[req["key"]] = hit
        return {"feed": feed_id, "updated": max(stamps) if stamps else None, "trips": out}


    async def _source(self, cfg: FeedConfig, src: Source, now: float):
        tu, vp, trains = await asyncio.gather(
            self._feed(src.trip_updates, src.prefix, cfg.trip_updates_interval),
            self._feed(src.vehicle_positions, src.prefix, cfg.interval),
            self._trains(src) if "train" in cfg.match else _none(),
        )
        if "route_start" in cfg.match and src.vehicle_positions:
            known = self._routes.setdefault(src.vehicle_positions, {})
            # A stalled positions feed still names the routes right.
            _learn_routes(known, vp)
            _fill_routes(known, tu)
        return src, _fresh(tu, now), _fresh(vp, now), trains


async def _none() -> None:
    return None


def _fresh(feed: Feed | None, now: float) -> Feed | None:
    """The feed, or None when its header says it is MAX_FEED_AGE old."""
    if feed is None or not feed.timestamp or now - feed.timestamp <= MAX_FEED_AGE:
        return feed
    return None


# --------------------------------------------------------------------------
# Matching a card's trip to the feed's.


def _route_key(trip_id: str) -> str:
    """The pattern part of an OTP city trip id, else the whole id."""
    m = _PATTERN_IN_TRIP_ID.match(trip_id)
    return m.group(1) if m else trip_id


def _route_of(known: dict[str, str], trip_id: str) -> str:
    return known.get(_route_key(trip_id), "")


def _learn_routes(known: dict[str, str], vp: Feed | None) -> None:
    """Remember each vehicle's route by its trip id's pattern. The OTP city
    feeds name the route only in the vehicle's descriptor, so a trip update
    takes it from here while the positions feed is down, or for a trip
    whose own vehicle was never seen. A pattern seen on two routes is ""
    from then on: neither."""
    if vp is None:
        return
    if len(known) > MAX_ROUTE_KEYS:
        known.clear()
    for tid, v in vp.vehicles.items():
        if not v.route_id:
            continue
        key = _route_key(tid)
        known[key] = v.route_id if known.get(key, v.route_id) == v.route_id else ""


def _fill_routes(known: dict[str, str], tu: Feed | None) -> None:
    """The remembered route of each trip update that names none.

    Filled in on the cached copy: a pattern that becomes "" later keeps
    its route on the trips filled before until the trip updates are fetched
    again, at most trip_updates_interval on, and a vehicle's own route wins
    in _find_trip anyway. Not worth keeping a second, unfilled copy for:
    no pattern was on two routes when this was written (_PATTERN_IN_TRIP_ID)."""
    if tu is None or not known:
        return
    for tid, t in tu.trip_updates.items():
        if not t.route_id:
            t.route_id = _route_of(known, tid)
            t.route_learned = bool(t.route_id)


def _start_minutes(trip_id: str) -> int | None:
    m = _START_IN_TRIP_ID.match(trip_id)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _stop_hit(rt_stop: str, wanted: str) -> bool:
    """A feed stop is the card's stop, or one of its platforms (hkir_334172_2
    of hkir_334172; 005501289_4 of station 005501289)."""
    return bool(wanted) and (rt_stop == wanted or rt_stop.startswith(wanted + "_"))


def _has_stop(tu: TripUpdate | None, stops: list[str]) -> bool:
    if tu is None or not stops:
        return False
    return any(_stop_hit(u.stop_id, s) for u in tu.stops for s in stops)


def _date_ok(start_date: str, wanted: str) -> bool:
    return not wanted or not start_date or start_date == wanted


def _pick(cands: list[tuple[str, TripUpdate | None, Vehicle | None]], stops: list[str]):
    if len(cands) > 1 and stops:
        with_stop = [c for c in cands if _has_stop(c[1], stops)]
        if with_stop:
            cands = with_stop
    return cands[0] if len(cands) == 1 else None


def _find_trip(cfg: FeedConfig, loaded, req: dict[str, Any]):
    """(trip id, TripUpdate, Vehicle) of the request's trip, or None."""
    wanted_date = req.get("date") or ""
    stops = req.get("stops") or []
    if "trip" in cfg.match and req.get("trip_id"):
        raw = req["trip_id"]
        for src, tu, vp, _tr in loaded:
            for tid in dict.fromkeys((raw, _strip(raw, src.prefix))):
                t = tu.trip_updates.get(tid) if tu else None
                v = vp.vehicles.get(tid) if vp else None
                start = (t.start_date if t else "") or (v.start_date if v else "")
                if (t or v) and _date_ok(start, wanted_date):
                    return tid, t, v
    if "route_start" in cfg.match and req.get("route") and req.get("start") is not None:
        route = req["route"]
        start = int(req["start"]) % 1440
        cands = []
        for _src, tu, vp, _tr in loaded:
            ids = set(tu.trip_updates if tu else ()) | set(vp.vehicles if vp else ())
            for tid in ids:
                t = tu.trip_updates.get(tid) if tu else None
                v = vp.vehicles.get(tid) if vp else None
                rid = (v.route_id if v else "") or (t.route_id if t else "")
                sm = _start_minutes(tid)
                sd = (t.start_date if t else "") or (v.start_date if v else "")
                if rid == route and sm is not None and sm % 1440 == start and _date_ok(sd, wanted_date):
                    cands.append((tid, t, v))
        hit = _pick(sorted(cands, key=lambda c: c[0]), stops)
        if hit:
            return hit
    if "train" in cfg.match and req.get("train"):
        number = req["train"].lstrip("0") or req["train"]
        cands = []
        for _src, tu, vp, trains in loaded:
            for tid in (trains or {}).get(number, ()):
                t = tu.trip_updates.get(tid) if tu else None
                v = vp.vehicles.get(tid) if vp else None
                sd = (t.start_date if t else "") or (v.start_date if v else "")
                if (t or v) and _date_ok(sd, wanted_date):
                    cands.append((tid, t, v))
        hit = _pick(sorted(cands, key=lambda c: c[0]), stops)
        if hit:
            return hit
    return None


def _plausible(delay: int | None) -> int | None:
    if delay is None or not MIN_DELAY <= delay <= MAX_DELAY:
        return None
    return int(delay)


def _match_stops(
    tu: TripUpdate, stops: list[str], upto: int, repeat: frozenset[int] = frozenset()
) -> list[StopUpdate | None]:
    """The update of each of stops[: upto + 1], in trip order. A stop the
    trip calls at once (not in repeat, the card's indices of the stops its
    trip calls at more than once) with one update is certain: it sets the
    order wherever its update is. Any other stop takes its first update
    after the previous stop's (a loop's later call its own update), or its
    first one when there is none after it (a feed that dropped the calls
    already served)."""
    ups = tu.stops
    out: list[StopUpdate | None] = []
    pos = 0
    for i, s in enumerate(stops[: upto + 1]):
        calls = [k for k, u in enumerate(ups) if _stop_hit(u.stop_id, s)]
        if not calls:
            out.append(None)
            continue
        if len(calls) == 1 and i not in repeat:
            k = calls[0]
            pos = k + 1
        else:
            k = next((c for c in calls if c >= pos), None)
            if k is None:
                k = calls[0]
            else:
                pos = k + 1
        out.append(ups[k])
    return out


def _at_stop(
    tu: TripUpdate, stops: list[str], index: int, arriving: bool, repeat: frozenset[int] = frozenset()
) -> tuple[int | None, int | None, int | None]:
    """(delay, predicted epoch, relationship) at stops[index]: its own
    update, else the nearest earlier stop's delay (GTFS-RT delays propagate
    down the trip). SKIPPED stops are passed over, but a SKIPPED update of
    the stop itself is relationship 1 (the vehicle passes it by); a NO_DATA
    update, its own or the one it would take the delay from, is
    relationship 2. Neither has a delay. 0 when a delay or time was found."""
    if not 0 <= index < len(stops):
        return None, None, None
    by_stop = _match_stops(tu, stops, index, repeat)
    own = by_stop[index]
    if own is not None and own.relationship == 1:
        return None, None, 1
    for j in range(index, -1, -1):
        u = by_stop[j]
        if u is None or u.relationship == 1:
            continue
        if u.relationship == 2:
            return None, None, 2
        first, second = ((u.arr_delay, u.dep_delay) if arriving else (u.dep_delay, u.arr_delay))
        raw = first if first is not None else second
        delay = _plausible(raw)
        when = None
        if j == index and (raw is None or delay is not None):
            # The predicted time, unless its delay was a glitch.
            when = (u.arr_time if arriving else u.dep_time) or (u.dep_time if arriving else u.arr_time)
        if delay is None and when is None:
            continue
        return delay, when, 0
    return None, None, None


def _current_delay(tu: TripUpdate, vehicle: Vehicle | None) -> int | None:
    """The trip's delay where the vehicle is now, for a feed whose stop ids
    are not the card's (Miskolc): its current stop's update, else the
    trip-level delay, else the first update's."""
    if vehicle is not None and vehicle.stop_id:
        for u in tu.stops:
            if u.stop_id == vehicle.stop_id and u.relationship == 0:
                d = _plausible(u.dep_delay if u.dep_delay is not None else u.arr_delay)
                if d is not None:
                    return d
    if tu.delay is not None:
        return _plausible(tu.delay)
    for u in tu.stops:
        if u.relationship == 0:
            d = _plausible(u.dep_delay if u.dep_delay is not None else u.arr_delay)
            if d is not None:
                return d
    return None


def _vehicle_json(v: Vehicle | None, now: float) -> dict[str, Any] | None:
    if v is None:
        return None
    if v.timestamp and now - v.timestamp > MAX_POSITION_AGE:
        return None
    return {
        "lat": round(v.lat, 6),
        "lon": round(v.lon, 6),
        "bearing": round(v.bearing, 1) if v.bearing is not None else None,
        "speed": round(v.speed, 1) if v.speed is not None else None,
        "timestamp": v.timestamp,
        "stop_id": v.stop_id,
        "status": v.status,
    }


def _answer(cfg: FeedConfig, loaded, req: dict[str, Any], now: float) -> dict[str, Any] | None:
    found = _find_trip(cfg, loaded, req)
    if found is None:
        return None
    tid, tu, vp = found
    stops = req.get("stops") or []
    src = req.get("from")
    dst = req.get("to")
    delay = when = dest_delay = dest_when = board_rel = dest_rel = None
    approx = False
    # TripDescriptor CANCELED (3) or DELETED (7): the trip does not run.
    canceled = bool(tu and tu.relationship in (3, 7))
    vehicle = _vehicle_json(vp, now)
    if tu is not None and not canceled:
        repeat = frozenset(int(i) for i in req.get("repeat") or ())
        if src is not None:
            delay, when, board_rel = _at_stop(tu, stops, int(src), False, repeat)
        if dst is not None:
            dest_delay, dest_when, dest_rel = _at_stop(tu, stops, int(dst), True, repeat)
        # A trip matched on a learned route has no vehicle naming it here
        # (the positions feed is down, stale, or without it). Its updates
        # then say nothing of where it is: mvkzrt keeps the stops already
        # served at delay 0, so the first one is an on-time it never said.
        unplaced = tu.route_learned and vehicle is None and not (vp and vp.route_id)
        if delay is None and when is None and board_rel is None and not unplaced:
            # No update at or before the boarding stop the card knows (a
            # feed with other stop ids, or a sparse one): the delay where
            # the vehicle is now, marked approximate. Not when the feed
            # said it skips the stop or has no prediction there.
            delay = _current_delay(tu, vp if vehicle is not None else None)
            approx = delay is not None
    skipped = board_rel == 1 or dest_rel == 1
    if (
        delay is None and when is None and dest_delay is None and dest_when is None
        and vehicle is None and not canceled and not skipped
    ):
        return None
    return {
        "trip_id": tid,
        "delay": delay,
        "time": when,
        "dest_delay": dest_delay,
        "dest_time": dest_when,
        "approx": approx,
        "canceled": canceled,
        "skipped": skipped,
        "vehicle": vehicle,
    }
