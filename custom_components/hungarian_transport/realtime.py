"""GTFS-Realtime delays and vehicle positions for the Volán, city and MÁV cards.

The cards' timetables (volan-index, city-index, the MÁV index) are static.
For the operators listed in realtime_feeds.json this module fetches their
public GTFS-Realtime feeds on demand, when a card calls the
`hungarian_transport.realtime` service, and answers with the delay at the
card's stops and the vehicle's position for the trips the card asks about.
Sources marked "mavplusz" ask MÁV's journey planner (mavplusz.py) first for
the trips a card names by trip_id, by route + start or by train number, and
read their feeds only as its backup.

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
from datetime import datetime, timedelta
import io
import json
import logging
from pathlib import Path
import re
import struct
import time
from typing import Any, NamedTuple
import zipfile
import zlib

import aiohttp

from . import gtfs_feed, mavplusz, upstream
from .cache import CallLater
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
# A decoded feed no card has asked for in this many of its intervals is
# dropped from memory (the Volán intercity one is tens of MB of objects);
# the next ask fetches it anew. Not the static feeds' train numbers (keep).
IDLE_INTERVALS = 5
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
# The same for mavplusz.hu's planner, in a bucket of its own: a user calling
# the service with trips no card asks for must not use up the tokens every
# card's feed refresh needs (nor a loop over feed ids the planner's).
MAV_RATE = FETCH_RATE
MAV_BURST = FETCH_BURST
# Delays outside this range are a feed glitch (a vehicle logged on to the
# wrong trip), not a prediction worth showing.
MIN_DELAY = -20 * 60
MAX_DELAY = 6 * 3600
# A position older than this is not where the vehicle is.
MAX_POSITION_AGE = 15 * 60
# Two trips whose predictions at a card's stops are no further apart than
# this (seconds) are one train listed twice (_pick).
SAME_PREDICTION = 30
# A feed whose header is older than this is a stalled publisher still
# serving its last file, not live data.
MAX_FEED_AGE = 15 * 60
# Service request limits.
MAX_TRIPS = 40
MAX_STOPS = 120
# A card's planner round (the route list and the trips, one request at a
# time for every card): at most one request's timeout, waiting included.
PLANNER_TIMEOUT = FETCH_TIMEOUT.total
# A planner request is begun only with at least this share of the round
# left: one begun late (the round spent waiting for other cards' requests)
# would be cut before a healthy planner answers, and a cut request backs
# off as an outage. Cut after this long, it did hang.
PLANNER_MIN_SHARE = 0.5
# The OutageLog key of mavplusz.hu (feed URLs are the others).
MAVPLUSZ_OUTAGE = "mavplusz"
# A route's trip list on mavplusz.hu (for route + start) is kept this long:
# it is the day's timetable, not live data.
ROUTE_INTERVAL = 3 * 3600
# Trip id patterns whose route a source remembers (see _learn_routes).
MAX_ROUTE_KEYS = 20000
# Rows of a static feed's calendar.txt and calendar_dates.txt read for
# TrainMap.runs: MÁV's has 9,987 calendar_dates rows for 338 services
# (2026-11-18). A feed with more is matched without its calendar.
MAX_CALENDAR_ROWS = 100000
# The planner's answer for a trip kept past its expiry, used only for a
# trip the request cap left out this time (_mavplusz): every card's trips
# get their turn, one poll in a few, and are answered from the planner in
# between, at most this many intervals old.
MAV_STALE_INTERVALS = 2
# Planner answers kept at most: the ones kept for hours (_mav_ttl) are
# trips a caller names, so a loop over made-up trip ids must not grow it.
# A board of 40 trains is about 80.
MAX_MAV_ANSWERS = 20000

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
    status: int | None


@dataclass(slots=True)
class Feed:
    timestamp: int | None = None
    trip_updates: dict[str, TripUpdate] = field(default_factory=dict)
    vehicles: dict[str, Vehicle] = field(default_factory=dict)
    # A trip id with runs on two service days (last night's run past
    # midnight beside today's): each run by (trip id, start_date) here too,
    # the first one seen also under the plain id.
    trip_days: dict[tuple[str, str], TripUpdate] = field(default_factory=dict)
    vehicle_days: dict[tuple[str, str], Vehicle] = field(default_factory=dict)

    def add_trip_update(self, tu: TripUpdate) -> None:
        _keep(self.trip_updates, self.trip_days, tu)

    def add_vehicle(self, v: Vehicle) -> None:
        _keep(self.vehicles, self.vehicle_days, v)

    def trip_update(self, trip_id: str, date: str = "") -> TripUpdate | None:
        """The trip's update; with a date, only that day's run (or one that
        names no day)."""
        return _run(self.trip_updates, self.trip_days, trip_id, date)

    def vehicle(self, trip_id: str, date: str = "") -> Vehicle | None:
        return _run(self.vehicles, self.vehicle_days, trip_id, date)


def _run(plain: dict[str, Any], days: dict[tuple[str, str], Any], trip_id: str, date: str) -> Any:
    """The run of trip_id on date. The days are only filled for an id with
    two runs in this feed, so the plain entry can be another day's run while
    the other feed holds this day's: today's trip update beside last night's
    vehicle is not one trip."""
    if not date:
        return plain.get(trip_id)
    item = days.get((trip_id, date)) if days else None
    if item is None:
        item = plain.get(trip_id)
    return item if item is None or not item.start_date or item.start_date == date else None


def _keep(plain: dict[str, Any], days: dict[tuple[str, str], Any], item: TripUpdate | Vehicle) -> None:
    tid = item.trip_id
    have = plain.get(tid)
    if have is None:
        plain[tid] = item
    elif have.start_date == item.start_date:
        # The same run again: the later one, as before.
        plain[tid] = item
        if (tid, item.start_date) in days:
            days[tid, item.start_date] = item
    else:
        days.setdefault((tid, have.start_date), have)
        days[tid, item.start_date] = item


def _strip(value: str, prefix: str) -> str:
    return value[len(prefix):] if prefix and value.startswith(prefix) else value


def _trip_descriptor(buf: bytes, prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {"trip_id": "", "start_date": "", "route_id": "", "rel": 0}
    for num, wire, val in _fields(buf):
        if num == 1 and wire == 2:
            out["trip_id"] = _strip(_text(val), prefix)
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
        desc["trip_id"], desc["start_date"], desc["route_id"], desc["rel"], delay, stops
    )


def _vehicle_position(buf: bytes, prefix: str) -> Vehicle | None:
    desc: dict[str, Any] | None = None
    lat = lon = bearing = speed = None
    ts = status = None
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
        desc["trip_id"], desc["start_date"], desc["route_id"], lat, lon, bearing, speed, ts, stop_id, status
    )


def decode_feed(data: bytes, prefix: str = "") -> Feed:
    """A FeedMessage's trip updates and vehicle positions, keyed by
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
                        feed.add_trip_update(tu)
                elif enum == 4 and ewire == 2:
                    vp = _vehicle_position(eval_, prefix)
                    if vp is not None:
                        feed.add_vehicle(vp)
                # Alerts (5) are not read: no card shows them.
    if not has_header:
        raise RtDecodeError("no FeedHeader")
    return feed


class TrainMap(dict):
    """Train number -> trip ids (prefix taken off), and the days each of
    those trips runs on by the static feed's calendar (runs). A dict, so
    the matching reads it as a plain map."""

    def __init__(self) -> None:
        super().__init__()
        # Trip id -> its service_id; per service_id its calendar.txt rows
        # (weekday bits, Monday the lowest; first and last day) and its
        # calendar_dates.txt days added and removed, YYYYMMDD as ints.
        self.service: dict[str, str] = {}
        self.weekly: dict[str, list[tuple[int, int, int]]] = {}
        self.added: dict[str, set[int]] = {}
        self.removed: dict[str, set[int]] = {}
        # False: no calendar read (none in the zip, or over the caps).
        self.calendar = False

    def runs(self, trip_id: str, date: str) -> bool | None:
        """Whether the trip runs on date (YYYYMMDD) by the calendar; None
        when that is not known (no calendar, a trip or date it lacks)."""
        svc = self.service.get(trip_id)
        if not self.calendar or svc is None or not (len(date) == 8 and date.isdigit()):
            return None
        day = int(date)
        if day in self.removed.get(svc, ()):
            return False
        if day in self.added.get(svc, ()):
            return True
        try:
            weekday = datetime.strptime(date, "%Y%m%d").weekday()
        except ValueError:
            return None
        return any(first <= day <= last and bits >> weekday & 1 for bits, first, last in self.weekly.get(svc, ()))


_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _day(value: str) -> int | None:
    return int(value) if len(value) == 8 and value.isdigit() else None


def _read_calendar(zf: zipfile.ZipFile, trains: TrainMap) -> None:
    """calendar.txt and calendar_dates.txt into trains, for the services its
    trips have. Neither in the zip, one over MAX_STATIC_MEMBER or the two
    over MAX_CALENDAR_ROWS: no calendar (runs is None), the trains are then
    asked for every trip of their number as before."""
    wanted = set(trains.service.values())
    members = []
    for name in ("calendar.txt", "calendar_dates.txt"):
        try:
            real = gtfs_feed.member(zf, name)
        except FileNotFoundError:
            continue
        if zf.getinfo(real).file_size > MAX_STATIC_MEMBER:
            return
        members.append((name, real))
    if not members:
        return
    rows = 0
    weekly: dict[str, list[tuple[int, int, int]]] = {}
    added: dict[str, set[int]] = {}
    removed: dict[str, set[int]] = {}
    for name, real in members:
        for row in gtfs_feed.csv_rows(zf, real):
            rows += 1
            if rows > MAX_CALENDAR_ROWS:
                return
            svc = row.get("service_id") or ""
            if svc not in wanted:
                continue
            if name == "calendar.txt":
                first, last = _day(row.get("start_date") or ""), _day(row.get("end_date") or "")
                if first is None or last is None:
                    continue
                bits = sum(1 << i for i, wd in enumerate(_WEEKDAYS) if row.get(wd) == "1")
                weekly.setdefault(svc, []).append((bits, first, last))
            else:
                day = _day(row.get("date") or "")
                kind = row.get("exception_type")
                if day is not None and kind in ("1", "2"):
                    (added if kind == "1" else removed).setdefault(svc, set()).add(day)
    trains.weekly, trains.added, trains.removed = weekly, added, removed
    trains.calendar = True


def train_numbers_from_static(data: bytes, prefix: str = "") -> TrainMap:
    """Train number -> trip ids (prefix taken off) from a GTFS zip's
    trips.txt trip_short_name (SZ 4510, InterCity 181), read as every
    builder reads a feed (gtfs_feed), with the days they run on
    (TrainMap.runs). A zip without trips.txt raises FileNotFoundError."""
    out = TrainMap()
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
                if row.get("service_id"):
                    out.service[trip_id] = row["service_id"]
            _read_calendar(zf, out)
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
    # Ask mavplusz.hu first for the trips a card names by trip_id, route +
    # start or train number, these feeds only when it fails (mavplusz.py,
    # docs/realtime.md).
    mavplusz: bool = False


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
                sources.append(
                    Source(
                        tu,
                        vp,
                        str(src.get("prefix") or ""),
                        _https(src.get("static")),
                        src.get("mavplusz") is True,
                    )
                )
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
    # When a card last asked for it, its interval and keep (_drop_idle).
    asked: float = -1e18
    interval: float = 0.0
    keep: bool = False


class _MavAnswer(NamedTuple):
    """The planner's answer for one (trip, date) in RealtimeHub._mav_cache:
    monotonic expiry, until when it is kept for a trip the request cap left
    out (_mavplusz), when it was asked (monotonic), the wall time of the
    request, and the trip's result (None: one the planner does not know)."""

    expiry: float
    keep: float
    asked: float
    at: float
    result: mavplusz.TripResult | None


class RealtimeHub:
    """On-demand GTFS-Realtime fetches, shared by every card and browser.

    `monotonic` is the clock of the fetch intervals and the rate limit,
    `wall` (epoch seconds) the one a feed's and a position's age is read by.
    Its own request sharing (_cached), not cache.SharedTTLCache: a feed's
    entry outlives its interval (the validators and the last good copy),
    a failure is cached too (for its backoff), and a refused fetch serves
    the copy without starting a new interval.

    `call_later` (cache.hass_call_later in Home Assistant) drops the idle
    copies (_drop_idle) once no card asks any more: every call does too,
    but none comes after the dashboards close. close() stops that timer
    for good (cache.close_on_stop).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        executor: Callable[..., "asyncio.Future[Any]"] | None = None,
        task_factory: TaskFactory | None = None,
        feeds: dict[str, FeedConfig] | None = None,
        monotonic: upstream.Monotonic = upstream.monotonic,
        wall: Callable[[], float] | None = None,
        call_later: CallLater | None = None,
    ) -> None:
        self._session = session
        self._executor = executor
        self._task_factory: TaskFactory = task_factory or asyncio.ensure_future
        self._feeds = feeds
        self._entries: dict[str, _Entry] = {}
        # Per vehicle positions URL: trip id pattern -> route (_learn_routes).
        self._routes: dict[str, dict[str, str]] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._monotonic = monotonic
        # Looked up per call, so a frozen test clock reaches it too.
        self._wall = wall or (lambda: time.time())
        # A refused fetch serves the copy there is (_refresh).
        self._rate = upstream.TokenBucket(
            FETCH_RATE,
            FETCH_BURST,
            refused=RealtimeRateLimited,
            log_refused=lambda count: _LOGGER.warning(
                "Realtime feed requests over the integration's rate limit: %d refused since the last such line",
                count,
            ),
            monotonic=monotonic,
        )
        # The planner's own (MAV_RATE): a refused request reads the feeds.
        self._mav_rate = upstream.TokenBucket(
            MAV_RATE,
            MAV_BURST,
            refused=RealtimeRateLimited,
            log_refused=lambda count: _LOGGER.warning(
                "Realtime planner requests over the integration's rate limit: %d refused since the last such line",
                count,
            ),
            monotonic=monotonic,
        )
        self._call_later = call_later
        self._cancel_drop: Callable[[], None] | None = None
        self._closed = False
        # Per feed URL, and MAVPLUSZ_OUTAGE for mavplusz.hu.
        self._outages = upstream.OutageLog(_LOGGER, monotonic=monotonic)
        # mavplusz.hu answers per (trip, date): _MavAnswer. One request at a
        # time, so cards polling together share it.
        self._mav_cache: dict[tuple[str, str], _MavAnswer] = {}
        self._mav_lock = asyncio.Lock()
        # Per (route, date): (monotonic expiry, its trip ids or None).
        self._mav_route_cache: dict[tuple[str, str], tuple[float, tuple[str, ...] | None]] = {}
        self._mav_retry_at = -1e18
        # The task whose planner request was cancelled in flight (_mav_post),
        # until its bounded() reads whether the round's deadline did it.
        self._mav_cut: asyncio.Task[Any] | None = None

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
        now = self._monotonic()
        entry.asked = now
        entry.interval = interval
        entry.keep = keep
        self._drop_idle(now)
        age = now - entry.at
        if age < (interval if entry.ok else backoff):
            return entry.data if entry.ok or keep else None
        task = self._inflight.get(url)
        if task is None:
            task = self._task_factory(self._refresh(url, entry, cap, parse, what, keep))
            self._inflight[url] = task
            task.add_done_callback(lambda _t: self._inflight.pop(url, None))
        return await asyncio.shield(task)

    def _drop_idle(self, now: float) -> None:
        """Drop the copies no card has asked for in IDLE_INTERVALS of their
        interval, with their validators; keep entries are never dropped."""
        for url, entry in self._entries.items():
            if (
                entry.data is not None and not entry.keep
                and now - entry.asked > IDLE_INTERVALS * entry.interval
                and url not in self._inflight
            ):
                entry.data = None
                entry.ok = False
                entry.etag = entry.last_modified = ""

    def close(self) -> None:
        """No idle-drop timer from now on: the pending one is cancelled and
        none is started again. Calls still drop idle copies."""
        self._closed = True
        if self._cancel_drop is not None:
            self._cancel_drop()
            self._cancel_drop = None

    def _schedule_drop(self) -> None:
        """One timer at a time, for when the first copy held goes idle (not
        sooner than MIN_INTERVAL: one being fetched waits for the next)."""
        if self._call_later is None or self._closed or self._cancel_drop is not None:
            return
        due = [
            entry.asked + IDLE_INTERVALS * entry.interval
            for entry in self._entries.values()
            if entry.data is not None and not entry.keep
        ]
        if due:
            delay = max(float(MIN_INTERVAL), min(due) - self._monotonic())
            self._cancel_drop = self._call_later(delay, self._drop_timer)

    def _drop_timer(self) -> None:
        self._cancel_drop = None
        self._drop_idle(self._monotonic())
        self._schedule_drop()

    async def _refresh(
        self, url: str, entry: _Entry, cap: int, parse: Callable[[bytes], Any], what: str, keep: bool = False
    ) -> Any:
        try:
            self._rate.take()
        except RealtimeRateLimited:
            return entry.data if entry.ok or keep else None
        try:
            body, etag, last_modified = await self._download(url, entry, cap)
            data = entry.data if body is None else await self._run(parse, body)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError, zipfile.BadZipFile) as err:
            entry.at = self._monotonic()
            entry.ok = False
            if not keep:
                entry.data = None
            self._outages.failed(
                url, "%s %s unavailable: %s%s", what, url, err or type(err).__name__,
                "; keeping the copy from before" if entry.data is not None else "",
            )
            return entry.data
        entry.at = self._monotonic()
        entry.ok = True
        entry.data = data
        entry.etag = etag
        entry.last_modified = last_modified
        self._outages.answered(url, "%s %s answers again", what, url)
        self._schedule_drop()
        return data

    async def _feed(self, url: str, prefix: str, interval: int) -> Feed | None:
        if not url:
            return None
        return await self._cached(
            url, interval, FAIL_BACKOFF, MAX_FEED_BYTES, lambda body: decode_feed(body, prefix), "Realtime feed"
        )

    async def _trains(self, src: Source) -> TrainMap | None:
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

    async def query(self, feed_id: str, trips: list[dict[str, Any]]) -> dict[str, Any]:
        """The service's answer: per requested trip key, what the feed knows."""
        # Here, not only in _cached: a call the planner answers reads no
        # feed, and the copies an outage of it loaded must go all the same.
        self._drop_idle(self._monotonic())
        feeds = await self.feeds()
        cfg = feeds.get(feed_id)
        if cfg is None:
            return error_answer({"feed": feed_id, "trips": {}}, RT_NO_FEED)
        now = self._wall()
        if any(s.mavplusz for s in cfg.sources) and any(_planner_request(cfg, r) for r in trips):
            return await self._query_mavplusz(feed_id, cfg, trips, now)
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

    async def _query_mavplusz(
        self, feed_id: str, cfg: FeedConfig, trips: list[dict[str, Any]], now: float
    ) -> dict[str, Any]:
        """query() for a feed with a mavplusz source and a trip_id, a
        route + start or a train to ask for. Per mavplusz source: route +
        start requests are resolved to the planner's trip ids (_mav_routes),
        train numbers to the static feed's (_trains, the map the feeds are
        matched with), then the planner's answer for those and the requested
        trip ids. Its jbb feeds are read only when the planner failed (an
        error, the rate limit, its backoff), knew none of the trips, left a
        route + start request unresolved, did not answer every trip of a
        train (cut, or none known), the source's train numbers are not
        loaded, or a request has neither a trip id, a route + start nor a
        train on a feed matching some other way. Other sources as in
        query()."""
        by_trip = "trip" in cfg.match
        by_route = "route_start" in cfg.match
        by_train = "train" in cfg.match
        mav_idx = [i for i, src in enumerate(cfg.sources) if src.mavplusz]
        dates = [mavplusz.service_date(r.get("date"), now) for r in trips]
        # Train number -> the static feed's trip ids, per source: the map the
        # feeds are matched with (cached for hours, kept through a failed
        # refresh), so a cold one is loaded once for both paths.
        maps: dict[int, TrainMap | None] = {}
        if by_train and any(r.get("train") for r in trips):
            maps = dict(zip(mav_idx, await asyncio.gather(*(self._trains(cfg.sources[i]) for i in mav_idx))))
        # A train without a date is today's run or last night's still on its
        # way (_find_trip): every trip id of its number on both days, but
        # for the ones the static calendar says do not run that day. The
        # planner lists a trip's scheduled calls on any day, run or not, so
        # only the calendar tells a variant of another weekday from a run.
        today, yesterday = _train_days(now)
        train_keys: dict[tuple[int, int], list[mavplusz.TripKey]] = {}
        for i in mav_idx:
            numbers = maps.get(i) or TrainMap()
            prefix = cfg.sources[i].prefix
            for n, r in enumerate(trips):
                ids = numbers.get(_train_number(r["train"]), ()) if r.get("train") else ()
                days = (dates[n],) if r.get("date") else (today, yesterday)
                keys = [
                    mavplusz.TripKey(mavplusz.gtfs_id(t, prefix), d, prefix)
                    for d in days for t in ids if numbers.runs(t, d) is not False
                ]
                if keys:
                    train_keys[i, n] = keys
        # Route + start -> the planner's candidate trips, per source.
        route_keys: dict[tuple[int, int], mavplusz.RouteKey] = {}
        if by_route:
            for i in mav_idx:
                prefix = cfg.sources[i].prefix
                for n, r in enumerate(trips):
                    if r.get("route") and r.get("start") is not None:
                        route_keys[i, n] = mavplusz.RouteKey(prefix + _strip(str(r["route"]), prefix), dates[n], prefix)
        # The whole planner round, the wait for _mav_lock included: past it
        # the jbb feeds answer. A request is begun only with half of it left or more
        # (_mav_post, PLANNER_MIN_SHARE), so one cut by it did hang.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PLANNER_TIMEOUT

        async def bounded(coro: Coroutine[Any, Any, Any]) -> Any:
            left = deadline - loop.time()
            if left <= 0:
                # Spent on the route list: no request is begun only to be
                # cut at once.
                coro.close()
                _LOGGER.debug("Realtime source %s: no answer within %s s", mavplusz.URL, PLANNER_TIMEOUT)
                return None
            task = asyncio.current_task()
            try:
                # In this task (not wait_for's), so _mav_post can say its
                # request was the one cut.
                async with asyncio.timeout(left):
                    return await coro
            except TimeoutError:
                if self._mav_cut is task:
                    # The request itself hung, which aiohttp's own timeout
                    # (as long as the round) never got to report: back off as
                    # from any failure, rather than post again every poll.
                    self._mav_failed(f"no answer within {PLANNER_TIMEOUT} s")
                else:
                    _LOGGER.debug("Realtime source %s: no answer within %s s", mavplusz.URL, PLANNER_TIMEOUT)
                return None
            finally:
                if self._mav_cut is task:
                    self._mav_cut = None

        listed = (
            await bounded(self._mav_routes(list(dict.fromkeys(route_keys.values())), deadline)) if route_keys else {}
        )
        cands: dict[tuple[int, int], list[mavplusz.TripKey]] = {}
        for (i, n), rk in route_keys.items():
            ids = (listed or {}).get(rk)
            if ids:
                found = mavplusz.trips_starting(ids, rk.prefix, int(trips[n]["start"]))
                if found:
                    cands[i, n] = [mavplusz.TripKey(g, rk.date, rk.prefix) for g in found]
        by_src: dict[int, list[mavplusz.TripKey]] = {}
        # The trips a request names by trip id or with a date.
        named: set[mavplusz.TripKey] = set()
        by_id: list[mavplusz.TripKey] = []
        for i in mav_idx:
            prefix = cfg.sources[i].prefix
            keys = [
                mavplusz.TripKey(mavplusz.gtfs_id(r["trip_id"], prefix), dates[n], prefix)
                for n, r in enumerate(trips)
                if by_trip and r.get("trip_id")
            ]
            named.update(keys)
            by_id += keys
            named.update(k for n, r in enumerate(trips) if r.get("date") for k in train_keys.get((i, n), ()))
            keys += [k for n in range(len(trips)) for k in cands.get((i, n), ())]
            keys += [k for n in range(len(trips)) for k in train_keys.get((i, n), ())]
            by_src[i] = list(dict.fromkeys(keys))
        # Last night's runs the train rule added (no request names them):
        # used only while still on their way (gone).
        late = {k for ks in train_keys.values() for k in ks if k.date == yesterday} - named
        # What each request needs answered at once, in this order on a tie
        # (_mavplusz takes the least recently asked first): the resolved
        # trips (a card's own trip id on a route + start feed is often not
        # the planner's: Veszprém, Miskolc), the trip ids, every trip of a
        # train (its variants are told apart by the stops, so one asked
        # without the others is no answer).
        groups = [ks for ks in cands.values()]
        groups += [[k] for k in dict.fromkeys(by_id)]
        groups += [ks for ks in train_keys.values()]
        answered = await bounded(self._mavplusz(groups, cfg.interval, deadline)) if groups else {}

        def gone(k: mavplusz.TripKey, res: mavplusz.TripResult | None) -> mavplusz.TripResult | None:
            """Last night's run of a train, over for MAX_POSITION_AGE: the
            planner keeps a finished run's realtime data for hours, which
            would answer for the train's run today."""
            if k in late and res is not None and res.ends is not None and res.ends + MAX_POSITION_AGE < now:
                return mavplusz.TripResult(None, None, res.ends)
            return res

        planner: dict[int, tuple[Source, Feed, Feed, TrainMap | None]] = {}
        for i, keys in by_src.items():
            got = [(answered[k][0], gone(k, answered[k][1])) for k in keys if k in answered] if answered is not None else []
            if any(res is not None for _at, res in got):
                feed = mavplusz.to_feed(
                    [res for _at, res in got], int(max(at for at, _res in got)), route_learned=by_route
                )
                planner[i] = (cfg.sources[i], feed, feed, maps.get(i))

        def covered(i: int, n: int) -> bool:
            """The planner answers trips[n] for source i: it knows its trip
            id, or resolved its route + start and answered for one of those
            trips (one cut from the request is matched on the feeds)."""
            mine = planner.get(i)
            if mine is None:
                return False
            if by_trip and trips[n].get("trip_id") and _knows(mine, trips[n]):
                return True
            got = answered or {}
            if any(k in got and got[k][1] is not None for k in cands.get((i, n), ())):
                return True
            # A train: every trip of its number answered (its variants are
            # told apart by the stops, so none may be missing), one known.
            keys = train_keys.get((i, n), ())
            if not keys or not any(k in got and got[k][1] is not None for k in keys):
                return False
            missing = [k for k in keys if k not in got]
            if not missing:
                return True
            # Or only last night's runs missing while today's calls at the
            # card's stops live: _find_trip takes a trip's run today over
            # its run last night, so these could not change the answer.
            stops = trips[n].get("stops") or []
            return all(k in late for k in missing) and any(
                k.date == today and k in got and got[k][1] is not None and _has_stop(got[k][1].trip_update, stops)
                for k in keys
            )

        def needs_feeds(i: int, n: int) -> bool:
            """trips[n] is matched on source i's feeds: a route + start the
            planner did not resolve, a train whose trips it did not all
            answer or whose numbers are not loaded (the feeds then match
            it as before, the static map being the same), or a request with
            neither a trip id nor a route + start nor a train on a feed
            matching some other way. A trip id the planner lacks among ones
            it knows is not, nor a train number the static feed lacks (the
            feeds could not match it either)."""
            r = trips[n]
            if covered(i, n):
                return False
            if (i, n) in route_keys or (i, n) in train_keys:
                return True
            if by_train and r.get("train"):
                return not maps.get(i)
            return not (by_trip and r.get("trip_id")) and bool(cfg.match - {"trip", "route_start"})

        # A source the planner had nothing to ask of (no train of the call
        # in its static feed: GYSEV's for a MÁV train) is not read either.
        jbb_idx = [
            i for i in range(len(cfg.sources))
            if (i not in planner and (i not in by_src or by_src[i]))
            or any(needs_feeds(i, n) for n in range(len(trips)))
        ]
        jbb = dict(zip(jbb_idx, await asyncio.gather(*(self._source(cfg, cfg.sources[i], now) for i in jbb_idx))))
        if not planner and jbb and all(tu is None and vp is None for _src, tu, vp, _tr in jbb.values()):
            return error_answer({"feed": feed_id, "trips": {}}, RT_UNAVAILABLE)
        used = [*planner.values(), *jbb.values()]
        stamps = [f.timestamp for _s, tu, vp, _t in used for f in (tu, vp) if f is not None and f.timestamp]
        out: dict[str, Any] = {}
        for n, req in enumerate(trips):
            loaded = []
            for i in range(len(cfg.sources)):
                mine, theirs = planner.get(i), jbb.get(i)
                if mine is not None and (theirs is None or covered(i, n)):
                    loaded.append(mine)
                elif theirs is not None:
                    loaded.append(theirs)
            # Two trips of the line at that minute: the one in the feeds must
            # call at the card's stops, the other may be the card's.
            strict = any(len(cands.get((i, n), ())) > 1 for i in mav_idx)
            hit = _answer(cfg, loaded, req, now, strict=strict)
            if hit is not None:
                out[req["key"]] = hit
        return {"feed": feed_id, "updated": max(stamps) if stamps else None, "trips": out}

    async def _mav_post(
        self, body: dict[str, Any], parse: Callable[[Any], Any], deadline: float | None = None
    ) -> Any:
        """One planner request (the caller holds _mav_lock): parse(answer),
        or None when it is not made (the rate limit, the backoff after a
        failure, under PLANNER_MIN_SHARE of the round left before the
        event loop's `deadline`) or fails, logged once per outage."""
        if self._monotonic() < self._mav_retry_at:
            return None
        if deadline is not None:
            left = deadline - asyncio.get_running_loop().time()
            if left < PLANNER_TIMEOUT * PLANNER_MIN_SHARE:
                # The round went on waiting for other cards' requests: the
                # feeds answer this one, and the planner is no worse for it.
                _LOGGER.debug("Realtime source %s: %.1f s of the round left, not asked", mavplusz.URL, left)
                return None
        try:
            self._mav_rate.take()
        except RealtimeRateLimited:
            return None
        try:
            result = parse(await mavplusz.fetch(self._session, body))
        except asyncio.CancelledError:
            # The round's deadline (bounded) or a caller gone: which one,
            # only bounded knows.
            self._mav_cut = asyncio.current_task()
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError, TypeError) as err:
            self._mav_failed(err or type(err).__name__)
            return None
        self._outages.answered(MAVPLUSZ_OUTAGE, "Realtime source %s answers again", mavplusz.URL)
        return result

    def _mav_failed(self, err: Any) -> None:
        """A planner request failed: no other for FAIL_BACKOFF, one log line
        per outage."""
        self._mav_retry_at = self._monotonic() + FAIL_BACKOFF
        self._outages.failed(
            MAVPLUSZ_OUTAGE, "Realtime source %s unavailable: %s; reading the GTFS-Realtime feeds",
            mavplusz.URL, err,
        )

    async def _mav_routes(
        self, keys: list[mavplusz.RouteKey], deadline: float | None = None
    ) -> dict[mavplusz.RouteKey, tuple[str, ...] | None] | None:
        """Per route key: the planner's trip ids that day (None: no such
        route), cached for ROUTE_INTERVAL, the rest in one request of at
        most MAX_TRIPS_PER_REQUEST routes. None when that request is not
        made or fails."""
        async with self._mav_lock:
            mono = self._monotonic()
            for k in [k for k, (exp, _ids) in self._mav_route_cache.items() if exp <= mono]:
                del self._mav_route_cache[k]
            out: dict[mavplusz.RouteKey, tuple[str, ...] | None] = {}
            todo = []
            for k in keys:
                hit = self._mav_route_cache.get((k.gtfs_id, k.date))
                if hit is not None:
                    out[k] = hit[1]
                else:
                    todo.append(k)
            todo = todo[: mavplusz.MAX_TRIPS_PER_REQUEST]
            if not todo:
                return out
            results = await self._mav_post(
                mavplusz.build_route_query(todo), lambda doc: mavplusz.parse_routes(doc, todo), deadline
            )
            if results is None:
                return None
            expiry = self._monotonic() + ROUTE_INTERVAL
            for k, ids in results.items():
                self._mav_route_cache[(k.gtfs_id, k.date)] = (expiry, ids)
                out[k] = ids
            return out

    async def _mavplusz(
        self, groups: list[list[mavplusz.TripKey]], interval: int, deadline: float | None = None
    ) -> dict[mavplusz.TripKey, tuple[float, mavplusz.TripResult | None]] | None:
        """Per key of groups: (wall time of the answer, its result), from
        the cache while within its expiry (_mav_ttl), the rest in one
        request of at most MAX_TRIPS_PER_REQUEST trips. A group (a train's
        trips) goes in whole or not at all, the least recently asked groups
        first, so every card's trips get their turn on a board of more; a
        key left for a later request has its last answer while it is kept
        (MAV_STALE_INTERVALS), else it is not in the result. None when the
        request is not made (the rate limit, the backoff after a failure)
        or fails: the caller reads the jbb feeds."""
        async with self._mav_lock:
            mono = self._monotonic()
            for k in [k for k, a in self._mav_cache.items() if a.keep <= mono]:
                del self._mav_cache[k]
            out: dict[mavplusz.TripKey, tuple[float, mavplusz.TripResult | None]] = {}
            waiting: list[tuple[float, int, list[mavplusz.TripKey]]] = []
            for order, group in enumerate(groups):
                due = []
                for k in dict.fromkeys(group):
                    hit = self._mav_cache.get((k.gtfs_id, k.date))
                    if hit is not None and hit.expiry > mono:
                        out[k] = (hit.at, hit.result)
                    else:
                        due.append(k)
                if due:
                    # Never asked (or so long ago it is gone) is the oldest.
                    asked = [self._mav_cache[(k.gtfs_id, k.date)].asked for k in due if (k.gtfs_id, k.date) in self._mav_cache]
                    waiting.append((min(asked) if len(asked) == len(due) else -1e18, order, due))
            if not waiting:
                return out
            waiting.sort(key=lambda w: (w[0], w[1]))
            cap = mavplusz.MAX_TRIPS_PER_REQUEST
            todo: dict[mavplusz.TripKey, None] = {}
            for _asked, _order, due in waiting:
                new = [k for k in due if k not in todo]
                if len(todo) + len(new) <= cap:
                    todo.update(dict.fromkeys(new))
                elif not todo:
                    # One group over the whole cap: its first ones.
                    todo.update(dict.fromkeys(new[:cap]))
            keys = list(todo)
            at = self._wall()
            results = await self._mav_post(
                mavplusz.build_query(keys), lambda doc: mavplusz.parse_response(doc, keys), deadline
            )
            if results is None:
                return None
            mono = self._monotonic()
            for k, res in results.items():
                ttl = self._mav_ttl(k, res, at, interval)
                stale = interval * MAV_STALE_INTERVALS if ttl == interval else 0
                self._mav_cache[(k.gtfs_id, k.date)] = _MavAnswer(mono + ttl, mono + ttl + stale, mono, at, res)
                out[k] = (at, res)
            if len(self._mav_cache) > MAX_MAV_ANSWERS:
                # The least recently asked go first, down to three quarters
                # (not one at a time): a board's are asked every few polls.
                ranked = sorted(self._mav_cache, key=lambda c: self._mav_cache[c].asked)
                for c in ranked[: len(ranked) - MAX_MAV_ANSWERS * 3 // 4]:
                    del self._mav_cache[c]
            for _asked, _order, due in waiting:
                for k in due:
                    hit = self._mav_cache.get((k.gtfs_id, k.date))
                    if k not in out and hit is not None:
                        out[k] = (hit.at, hit.result)
            return out

    @staticmethod
    def _mav_ttl(k: mavplusz.TripKey, res: mavplusz.TripResult | None, at: float, interval: int) -> int:
        """How long the planner's answer for k is kept: interval, or
        ROUTE_INTERVAL for one that no longer changes. That is a trip it
        does not know or that has no call that day, a run without realtime
        data whose last call is longer ago than any delay, and a past day's
        run over for MAX_POSITION_AGE (_query_mavplusz's gone: last night's
        runs of every train asked by number are asked once in a while, not
        every minute all day)."""
        if res is None or res.ends is None:
            return ROUTE_INTERVAL
        if res.trip_update is None and res.ends + MAX_DELAY < at:
            return ROUTE_INTERVAL
        if k.date < mavplusz.service_date(None, at) and res.ends + MAX_POSITION_AGE < at:
            return ROUTE_INTERVAL
        return interval

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


def _planner_request(cfg: FeedConfig, req: dict[str, Any]) -> bool:
    """A request mavplusz.hu can answer: a trip id, a route + start or a
    train number, on a feed matching that way."""
    return bool(
        ("trip" in cfg.match and req.get("trip_id"))
        or ("route_start" in cfg.match and req.get("route") and req.get("start") is not None)
        or ("train" in cfg.match and req.get("train"))
    )


def _train_number(train: str) -> str:
    """A card's train number as the static map keys it: leading zeros off."""
    return train.lstrip("0") or train


def _train_days(now: float) -> tuple[str, str]:
    """(today, yesterday) in Budapest, YYYYMMDD: the runs a train request
    without a date can mean."""
    day = datetime.fromtimestamp(now, mavplusz.BUDAPEST).date()
    return day.strftime("%Y%m%d"), (day - timedelta(days=1)).strftime("%Y%m%d")


def _knows(entry, req: dict[str, Any]) -> bool:
    """A loaded source's feed has the request's trip id (either form)."""
    src, tu, vp, _tr = entry
    raw = req.get("trip_id") or ""
    return any(tid in tu.trip_updates or tid in vp.vehicles for tid in (raw, _strip(raw, src.prefix)))


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
    for t in [*tu.trip_updates.values(), *tu.trip_days.values()]:
        if not t.route_id:
            t.route_id = _route_of(known, t.trip_id)
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


def _same_answer(a: TripUpdate, b: TripUpdate, req: dict[str, Any]) -> bool:
    """Two trips give the card the same answer: at its boarding and its
    destination stop (from, to) the same relationship, and delays and
    times SAME_PREDICTION apart at most (a value one of them lacks is a
    difference). False for a request with neither."""
    stops = req.get("stops") or []
    repeat = frozenset(int(i) for i in req.get("repeat") or ())
    points = [(int(req[k]), k == "to") for k in ("from", "to") if req.get(k) is not None]
    if not points or a.relationship != b.relationship:
        return False
    for index, arriving in points:
        x, y = _at_stop(a, stops, index, arriving, repeat), _at_stop(b, stops, index, arriving, repeat)
        if x[2] != y[2]:
            return False
        for va, vb in zip(x[:2], y[:2]):
            if (va is None) != (vb is None) or (va is not None and abs(va - vb) > SAME_PREDICTION):
                return False
    return True


def _pick(
    cands: list[tuple[str, TripUpdate | None, Vehicle | None]],
    stops: list[str],
    strict: bool = False,
    req: dict[str, Any] | None = None,
):
    """The one candidate, or the one of several calling at the card's
    stops. strict: a trip not among cands may be the card's (the planner
    listed two at that minute), so even a single one must call there.

    Several calling there that give req the same answer are one train
    listed twice (741 on 2026-11-18: an UPDATED and a MODIFIED trip of the
    same calls and delays): the one with a vehicle, else the first by trip
    id. Ones that differ are no answer."""
    if len(cands) > 1 or strict:
        with_stop = [c for c in cands if _has_stop(c[1], stops)]
        if with_stop or strict:
            cands = with_stop
        if req is not None and len(with_stop) > 1 and all(
            _same_answer(a[1], b[1], req) for n, a in enumerate(with_stop) for b in with_stop[n + 1:]
        ):
            cands = [min(with_stop, key=lambda c: (c[2] is None, c[0]))]
    return cands[0] if len(cands) == 1 else None


def _find_trip(cfg: FeedConfig, loaded, req: dict[str, Any], now: float, strict: bool = False):
    """(trip id, TripUpdate, Vehicle) of the request's trip, or None;
    strict as in _pick, for a route + start."""
    wanted_date = req.get("date") or ""
    stops = req.get("stops") or []
    if "trip" in cfg.match and req.get("trip_id"):
        raw = req["trip_id"]
        for src, tu, vp, _tr in loaded:
            for tid in dict.fromkeys((raw, _strip(raw, src.prefix))):
                t = tu.trip_update(tid, wanted_date) if tu else None
                v = vp.vehicle(tid, wanted_date) if vp else None
                if t or v:
                    return tid, t, v
    if "route_start" in cfg.match and req.get("route") and req.get("start") is not None:
        route = req["route"]
        start = int(req["start"]) % 1440
        cands = []
        for _src, tu, vp, _tr in loaded:
            ids = set(tu.trip_updates if tu else ()) | set(vp.vehicles if vp else ())
            for tid in ids:
                t = tu.trip_update(tid, wanted_date) if tu else None
                v = vp.vehicle(tid, wanted_date) if vp else None
                rid = (v.route_id if v else "") or (t.route_id if t else "")
                sm = _start_minutes(tid)
                if rid == route and sm is not None and sm % 1440 == start:
                    cands.append((tid, t, v))
        hit = _pick(sorted(cands, key=lambda c: c[0]), stops, strict, req)
        if hit:
            return hit
    if "train" in cfg.match and req.get("train"):
        number = _train_number(req["train"])
        # The cards send no date for a train: today's run, or last night's
        # still on its way, never another day's of the same trip id.
        days = (wanted_date,) if wanted_date else _train_days(now)
        cands = []
        for _src, tu, vp, trains in loaded:
            for tid in (trains or {}).get(number, ()):
                # One day's run from both feeds: today's update and last
                # night's vehicle are two trains.
                for d in days:
                    t = tu.trip_update(tid, d) if tu else None
                    v = vp.vehicle(tid, d) if vp else None
                    if t or v:
                        cands.append((tid, t, v))
                        break
        hit = _pick(sorted(cands, key=lambda c: c[0]), stops, req=req)
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


def _answer(
    cfg: FeedConfig, loaded, req: dict[str, Any], now: float, *, strict: bool = False
) -> dict[str, Any] | None:
    found = _find_trip(cfg, loaded, req, now, strict)
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
