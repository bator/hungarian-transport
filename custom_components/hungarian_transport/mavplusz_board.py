"""BKK departure boards and trip details from mavplusz.hu when FUTÁR fails.

mavplusz.hu, MÁV's OpenTripPlanner 2 (mavplusz.py), carries BKK's timetable
and realtime updates as its feed "BKK", with FUTÁR's ids: FUTÁR stop
BKK_F00940 is the planner's "BKK:F00940", trip BKK_C96610561 is
"BKK:C96610561" (checked 2026-11-18). When FUTÁR cannot answer a card's
departure board or trip details (an outage, a timeout, FUTÁR's own HTTP 429,
a rejected key), futar.FutarProxy asks PlannerBoards instead, which answers
in FUTÁR's own shape, so the card renders it unchanged. The answer carries
"source": "mavplusz" at its top level for diagnostics. When the planner
cannot answer either, the proxy raises FUTÁR's error, and the card fills the
board from Transitous as it does today.

The planner is a public service run for MÁV's own site, so it is asked
sparingly: one board request per stop per BOARD_TTL whatever the cards'
horizons (the widest window is asked and each card's is cut from it), the
trip details of many cards in one aliased request, the integration's one
planner rate limit and backoff (mavplusz.PlannerLimiter, shared with the
realtime hub) with a smaller share of it for this fallback, of which a
token is reserved before FUTÁR is cut short (reserve), and nothing at all
for what it cannot answer (can_answer): a stop it answered null for
(PROBE_TTL), a request that failed for its own sake (an answer over the
cap, a GraphQL error, one of the wrong shape: REQUEST_FAIL_TTL, and no
shared backoff), the limiter backing off or out of tokens. No Home
Assistant imports: tests/test_mavplusz_board.py runs it bare.

Stations: a GTFS parent station (BKK_CS056233, Keleti's metro) is a stop
to the planner too, whose board holds its child stops' departures (checked
2026-11-18). So are FUTÁR's combined stations (BKK_CSF…) that BKK's GTFS
has (38 of them, BKK_CSF00062 Batthyány tér among them): one board request
asks the planner, and its answer says whether it knows the station, kept
for PROBE_TTL. Those it does not know (BKK_CSF01131, the Keleti preset in
src/config.js; BKK_CS007896, the Kelenföld one) have the boards of their
child poles merged instead: the poles FUTÁR's answers name (learn), kept
across restarts (stations_data, restore_stations; storage.py writes them),
else SEED_STATIONS. A learnt station or pole not seen for STATION_MAX_AGE
goes, and the least recently seen goes first at the limits.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
import logging
import re
import time
from typing import Any, NamedTuple

import aiohttp

from . import mavplusz, realtime, upstream
from .cache import CallLater, SharedTTLCache, TaskFactory
from .futar import FUTAR_CACHE_TTL

_LOGGER = logging.getLogger(__name__)

BOARD_PATH = "arrivals-and-departures-for-stop.json"
TRIP_PATH = "trip-details.json"
PATHS = frozenset({BOARD_PATH, TRIP_PATH})
FUTAR_PREFIX = "BKK_"
PLANNER_PREFIX = "BKK:"
USER_AGENT = (
    "Hungarian-transport/1.0 (Home Assistant integration; BKK boards when FUTÁR fails; "
    "+https://github.com/bator/hungarian-transport)"
)
# A stop's board is asked at most this often, for every card and horizon
# together: the cards poll every 20 s at the earliest, FUTÁR's own answers
# are cached as long.
BOARD_TTL = FUTAR_CACHE_TTL
# A trip's details hold its timetable and the delays: the card reads them for
# where a trip goes and how long the ride is, and keeps them for the session.
TRIP_TTL = 60
# The widest window any card asks for (src/config.js MAX_MINUTES_AFTER).
BOARD_WINDOW_MIN = 480
DEFAULT_MINUTES_AFTER = 180
# Departures per pattern: OTP's numberOfDepartures counts them per pattern
# (and per child stop of a station), not per board (checked 2026-11-18). A
# card shows up to 40 rows (config.js maxDepartureRows; 80 at its widest
# horizon) and may keep only the ones to its destination, so a stop of one
# dense pattern (trams 4 and 6, one every 3-4 minutes) needs some 60 to fill
# its three hours. FUTÁR itself stops at about 60.
DEPARTURES_PER_PATTERN = 60
# The board is never cut to its earliest rows across every pattern: a hub's
# dense metro lines would then fill it within minutes (Deák tér's earliest
# 80 of 360 rows spanned 42 minutes, Keleti's 22, on 2026-11-19), and a
# card's three hours or one destination would have no rows. Each route and
# headsign keeps its share instead (_fair_cut): up to ROWS_PER_PATTERN of
# its earliest, the card's widest page of one destination; past the row cap
# the patterns with the most rows give way first. The stored board holds
# what a BOARD_MAX_BYTES answer can (about 770 bytes a planner stop time);
# a card's answer, cut to its horizon, at most RENDER_MAX_ROWS rows (some
# 400 bytes each with their references), some 160 kB.
ROWS_PER_PATTERN = 80
STOP_TIME_BYTES = 769
RENDER_MAX_ROWS = 400
# A stop time is about 770 bytes of answer: 20 patterns of 60 are 0.9 MB,
# and a FUTÁR station's child poles (MAX_CHILD_STOPS, CHILD_DEPARTURES each
# pattern) up to 1.1 MB, so a board answer may be twice the trip details'
# cap. It is decoded on the event loop, some 11 ms a MB, once per stop per
# BOARD_TTL while FUTÁR fails.
BOARD_MAX_BYTES = 2 * 1024 * 1024
BOARD_MAX_ROWS = BOARD_MAX_BYTES // STOP_TIME_BYTES
MAX_CHILD_STOPS = 24
CHILD_DEPARTURES = 20
# Trip details asked within this long of each other go in one request.
TRIP_BATCH_WAIT = 0.05
MAX_TRIPS_PER_REQUEST = mavplusz.MAX_TRIPS_PER_REQUEST
# The planner's share of the card's 20 s: FUTÁR gets
# futar.FUTAR_FALLBACK_TIMEOUT_SEC first, after a token wait of up to
# futar.FUTAR_TOKEN_WAIT_SEC. A trip's details wait TRIP_BATCH_WAIT for
# their batch and the request gets the rest: a trip never waits for an
# earlier batch's request, so this is each trip's whole wait.
PLANNER_TIMEOUT = aiohttp.ClientTimeout(total=6)
TRIP_TIMEOUT = aiohttp.ClientTimeout(total=PLANNER_TIMEOUT.total - TRIP_BATCH_WAIT)
# After the planner failed (no answer, a timeout, HTTP 5xx or 429) nobody
# asks it for this long, the realtime hub neither (the shared backoff).
FAIL_BACKOFF = 30
# A request that failed for its own sake (an answer over its cap, a GraphQL
# error, one of the wrong shape, another HTTP 4xx) is the same request's
# answer the next time too: that stop's board, or those trips' details, are
# not asked for this long, and the planner is not backed off for it.
REQUEST_FAIL_TTL = 600
MAX_FAILING = 2048
# The service dates the boards gave (the planner's and FUTÁR's own), per
# FUTÁR trip id, for its trip details (a trip after midnight runs on the
# day before).
MAX_SERVICE_DATES = 4000
# A trip no board dated, asked before this hour in Budapest, is asked for
# yesterday's service day and today's (the night buses of the day before
# run until about 4:30), and the run under way is the answer.
NIGHT_END_HOUR = 5
# Stations (GTFS parent stations BKK_CS…, FUTÁR's combined ones BKK_CSF…)
# and their child poles learnt from FUTÁR's answers: so many stations at
# most, the least recently seen first out; a station or pole FUTÁR has not
# named for STATION_MAX_AGE goes (a pole moved, a station rebuilt). A
# sighting is news for the store once a SEEN_RESOLUTION.
STATION_PREFIX = "BKK_CS"
COMBINED_PREFIX = "BKK_CSF"
MAX_STATIONS = 512
STATION_MAX_AGE = 30 * 86400
SEEN_RESOLUTION = 86400
# Whether the planner knows a stop or station (its board answered, or a
# null stop), kept this long: the GTFS changes with the timetable.
PROBE_TTL = 12 * 3600
MAX_PROBED = 4096
# A trip's shape (OTP2's tripGeometry, a Google encoded polyline of 1e5,
# FUTÁR's own format; checked 2026-11-19): a long bus line's is some 3 kB.
MAX_POLYLINE_CHARS = 100_000
# The poles of the card's preset hubs the planner does not know, until
# FUTÁR names their own (learn). Keleti (BKK_CSF01131): every BKK stop the
# planner names "Budapest, Keleti pályaudvar M" (or "... M (Garay utca)",
# F01131 itself) with a mode, all within some 300 m of each other, by
# stops(name: "Keleti pályaudvar") on 2026-11-18. The metro's are a GTFS
# station of their own (BKK_CS056233), as in FUTÁR.
SEED_STATIONS: dict[str, dict[str, Any]] = {
    "BKK_CSF01131": {
        "ref": {"id": "BKK_CSF01131", "name": "Keleti pályaudvar"},
        "poles": (
            "BKK_F01131", "BKK_F01154", "BKK_F01159", "BKK_F01160", "BKK_F01311", "BKK_F01314",
            "BKK_007950", "BKK_009160", "BKK_009161", "BKK_009162", "BKK_009163", "BKK_009321",
            "BKK_061349", "BKK_061350", "BKK_061351",
        ),
    },
}
# FUTÁR's ad-hoc trips (a replacement bus, a trip added for the day) have
# ids of ten or more digits that no timetable, the planner's included, has.
_AD_HOC_TRIP = re.compile(r"^BKK_\d{10,}$")
_DATE = re.compile(r"^\d{8}$")
# A service day the planner can mean (2000-2100) and seconds after it a
# stop time can have (a day early to two days on): anything else is a
# broken or hostile answer, not a time to turn into a date.
_DAY_MIN = 946684800
_DAY_MAX = 4102444800
_SECONDS_MIN = -86400
_SECONDS_MAX = 2 * 86400
# OTP2's serviceDay is noon less 12 hours, local time: on the day clocks
# change that is 23:00 or 01:00 of the day before or the day itself, the
# noon it is reckoned from is always the day.
_NOON = 12 * 3600
# The planner names a Budapest stop "Budapest, Orczy tér", FUTÁR "Orczy tér";
# the card matches destinations by name.
_CITY_PREFIX = "Budapest, "
# OTP's transit mode -> FUTÁR's route type (src/lib/geo.js vehicleKind).
_ROUTE_TYPE = {
    "BUS": "BUS",
    "TROLLEYBUS": "TROLLEYBUS",
    "TRAM": "TRAM",
    "SUBWAY": "SUBWAY",
    "FERRY": "FERRY",
    "RAIL": "RAIL",
    "COACH": "COACH",
}
# GTFS extended route type 109: suburban railway, FUTÁR's HÉV.
_SUBURBAN = 109
_BOOKING = ("CALL_AGENCY", "COORDINATE_WITH_DRIVER")

_STOP_FIELDS = "gtfsId name lat lon platformCode parentStation { gtfsId }"
_GEOMETRY_FIELDS = "tripGeometry { points length }"
_ROUTE_FIELDS = "gtfsId shortName longName mode type color textColor"
_TRIP_REF_FIELDS = f"gtfsId tripHeadsign tripShortName wheelchairAccessible bikesAllowed route {{ {_ROUTE_FIELDS} }}"
_BOARD_FIELDS = (
    f"{_STOP_FIELDS} routes {{ gtfsId }} "
    "stoptimesWithoutPatterns(startTime: $start, timeRange: $range, numberOfDepartures: $n, omitNonPickups: true) { "
    "realtime realtimeState scheduledArrival realtimeArrival scheduledDeparture realtimeDeparture "
    f"serviceDay headsign pickupType stop {{ {_STOP_FIELDS} }} trip {{ {_TRIP_REF_FIELDS} }} }}"
)
_BOARD_QUERY = f"query($id: String!, $start: Long!, $range: Int!, $n: Int!) {{ stop(id: $id) {{ {_BOARD_FIELDS} }} }}"
_TRIP_STOPTIMES = (
    "stoptimesForDate(serviceDate: $%s) { realtime realtimeState scheduledArrival realtimeArrival "
    f"scheduledDeparture realtimeDeparture serviceDay headsign stop {{ {_STOP_FIELDS} }} }}"
)

Wall = Callable[[], float]


class PlannerError(Exception):
    """The planner did not answer (not asked, failed, or an answer not to
    trust). Never leaves this module: the proxy then raises FUTÁR's error."""


class _RequestFailed(PlannerError):
    """This request failed for its own sake, not the planner (REQUEST_FAIL_TTL)."""


class _Ticket:
    """A planner token reserved for one fallback answer (PlannerBoards.reserve):
    the request it is handed to spends it (spend), else release() gives it
    back to the limiter."""

    def __init__(self, rate: mavplusz.PlannerClient) -> None:
        self._rate = rate
        self._held = True

    def spend(self) -> bool:
        """Whether the token was still held (now spent by the caller's request)."""
        held, self._held = self._held, False
        return held

    def release(self) -> None:
        if self._held:
            self._held = False
            self._rate.refund()


class _Board(NamedTuple):
    """One stop's board as the planner gave it, before a card's horizon cuts
    it: departures in time order, and the references they name."""

    stop_id: str
    asked: float
    route_ids: tuple[str, ...]
    stop_times: tuple[dict[str, Any], ...]
    routes: dict[str, dict[str, Any]]
    trips: dict[str, dict[str, Any]]
    stops: dict[str, dict[str, Any]]


def is_station(stop: str) -> bool:
    """Whether a FUTÁR stop id is a station: a GTFS parent station
    (BKK_CS…) or one of FUTÁR's combined stations (BKK_CSF…)."""
    return stop.startswith(STATION_PREFIX)


def is_combined(stop: str) -> bool:
    """Whether a FUTÁR stop id is one of FUTÁR's combined stations, most of
    which the planner does not know (their poles are asked first)."""
    return stop.startswith(COMBINED_PREFIX)


def planner_id(futar_id: Any) -> str | None:
    """The planner's id of a FUTÁR stop or trip id; None for another feed's."""
    text = str(futar_id or "")
    if not text.startswith(FUTAR_PREFIX) or len(text) <= len(FUTAR_PREFIX) or len(text) > 64:
        return None
    return PLANNER_PREFIX + text[len(FUTAR_PREFIX):]


def futar_id(gtfs_id: Any) -> str:
    """FUTÁR's id of a planner id of the BKK feed; "" for another feed's."""
    text = gtfs_id if isinstance(gtfs_id, str) else ""
    if not text.startswith(PLANNER_PREFIX) or len(text) <= len(PLANNER_PREFIX) or len(text) > 64:
        return ""
    return FUTAR_PREFIX + text[len(PLANNER_PREFIX):]


def build_board_query(stop: str, start: int, window_min: int = BOARD_WINDOW_MIN) -> dict[str, Any]:
    """The request for one stop's (or station's: OTP's stop() answers both)
    departures from start (epoch seconds) for window_min minutes. The id
    goes in as a variable, never into the query text."""
    return {
        "query": _BOARD_QUERY,
        "variables": {"id": stop, "start": int(start), "range": int(window_min) * 60, "n": DEPARTURES_PER_PATTERN},
    }


def build_station_query(stops: Sequence[str], start: int, window_min: int = BOARD_WINDOW_MIN) -> dict[str, Any]:
    """The aliased request for the boards of several stops (alias sN is
    stops[N]), CHILD_DEPARTURES a pattern each: a FUTÁR station's poles."""
    decl = ["$start: Long!", "$range: Int!", "$n: Int!"]
    parts = []
    variables: dict[str, Any] = {"start": int(start), "range": int(window_min) * 60, "n": CHILD_DEPARTURES}
    for i, stop in enumerate(stops):
        decl.append(f"$s{i}: String!")
        variables[f"s{i}"] = stop
        parts.append(f"s{i}: stop(id: $s{i}) {{ {_BOARD_FIELDS} }}")
    return {"query": f"query({', '.join(decl)}) {{ {' '.join(parts)} }}", "variables": variables}


class TripKey(NamedTuple):
    """One trip's details to ask for: the planner's id and the service date."""

    gtfs_id: str
    date: str


def build_trip_query(trips: Sequence[TripKey]) -> dict[str, Any]:
    """The aliased request for several trips' details: alias tN is trips[N]."""
    dates: dict[str, str] = {}
    decl: list[str] = []
    parts: list[str] = []
    variables: dict[str, str] = {}
    for date in dict.fromkeys(t.date for t in trips):
        name = f"d{len(dates)}"
        dates[date] = name
        decl.append(f"${name}: String")
        variables[name] = date
    for i, t in enumerate(trips):
        decl.append(f"$t{i}: String!")
        variables[f"t{i}"] = t.gtfs_id
        parts.append(
            f"t{i}: trip(id: $t{i}) {{ {_TRIP_REF_FIELDS} {_GEOMETRY_FIELDS} {_TRIP_STOPTIMES % dates[t.date]} }}"
        )
    return {"query": f"query({', '.join(decl)}) {{ {' '.join(parts)} }}", "variables": variables}


def _str(value: Any) -> str:
    return mavplusz._str(value)


def _int(value: Any) -> int | None:
    return mavplusz._int(value)


def _day(value: Any) -> int | None:
    day = _int(value)
    return day if day is not None and _DAY_MIN <= day <= _DAY_MAX else None


def _seconds(value: Any) -> int | None:
    seconds = _int(value)
    return seconds if seconds is not None and _SECONDS_MIN <= seconds <= _SECONDS_MAX else None


def _epoch(day: int | None, seconds: int | None) -> int | None:
    return day + seconds if day is not None and seconds is not None else None


def _name(value: Any) -> str:
    name = _str(value)
    return name[len(_CITY_PREFIX):] if name.startswith(_CITY_PREFIX) else name


def _date(service_day: int | None) -> str:
    """The date (YYYYMMDD) of an OTP2 serviceDay, "" for none."""
    if service_day is None or not _DAY_MIN <= service_day <= _DAY_MAX:
        return ""
    try:
        return datetime.fromtimestamp(service_day + _NOON, mavplusz.BUDAPEST).strftime("%Y%m%d")
    except (ValueError, OverflowError, OSError):
        return ""


def _route_type(route: dict[str, Any]) -> str:
    gtfs_type = _int(route.get("type"))
    if gtfs_type == _SUBURBAN:
        return "SUBURBAN_RAILWAY"
    mode = _str(route.get("mode")).upper()
    return _ROUTE_TYPE.get(mode, mode or "BUS")


def _color(value: Any) -> str:
    text = _str(value).lstrip("#")
    return text if len(text) == 6 and all(c in "0123456789abcdefABCDEF" for c in text) else ""


def _route_ref(route: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(route, dict):
        return None
    rid = futar_id(route.get("gtfsId"))
    if not rid:
        return None
    ref: dict[str, Any] = {
        "id": rid,
        "shortName": _str(route.get("shortName")),
        "longName": _str(route.get("longName")),
        "type": _route_type(route),
        "agencyId": "BKK",
    }
    for key in ("color", "textColor"):
        color = _color(route.get(key))
        if color:
            ref[key] = color
    return rid, ref


def _trip_ref(trip: dict[str, Any], tid: str, rid: str) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "id": tid,
        "routeId": rid,
        "tripHeadsign": _str(trip.get("tripHeadsign")),
        "wheelchairAccessible": trip.get("wheelchairAccessible") == "POSSIBLE",
        "bikesAllowed": trip.get("bikesAllowed") == "ALLOWED",
    }
    short = _str(trip.get("tripShortName"))
    if short:
        ref["tripShortName"] = short
    return ref


def _stop_ref(stop: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(stop, dict):
        return None
    sid = futar_id(stop.get("gtfsId"))
    if not sid:
        return None
    ref: dict[str, Any] = {"id": sid, "name": _name(stop.get("name"))}
    lat, lon = mavplusz._num(stop.get("lat")), mavplusz._num(stop.get("lon"))
    if lat is not None and lon is not None:
        ref["lat"], ref["lon"] = lat, lon
    platform = _str(stop.get("platformCode"))
    if platform:
        ref["platformCode"] = platform
    parent_station = stop.get("parentStation")
    parent = futar_id(parent_station.get("gtfsId")) if isinstance(parent_station, dict) else ""
    if parent:
        ref["parentStationId"] = parent
    return sid, ref


def _times(st: dict[str, Any]) -> dict[str, int]:
    """FUTÁR's time fields of one planner stop time: the timetable's always,
    the predicted ones only for a realtime trip (FUTÁR leaves them off a
    scheduled one, and the card shows a delay only where they are)."""
    day = _day(st.get("serviceDay"))
    out: dict[str, int] = {}
    pairs = (
        ("arrivalTime", "scheduledArrival", "predictedArrivalTime", "realtimeArrival"),
        ("departureTime", "scheduledDeparture", "predictedDepartureTime", "realtimeDeparture"),
    )
    live = st.get("realtime") is True
    for plan_key, plan_field, pred_key, pred_field in pairs:
        plan = _epoch(day, _seconds(st.get(plan_field)))
        if plan is not None:
            out[plan_key] = plan
        pred = _epoch(day, _seconds(st.get(pred_field))) if live else None
        if pred is not None:
            out[pred_key] = pred
    return out


def _data(doc: Any) -> dict[str, Any]:
    return mavplusz._data(doc)


def _aliased(doc: Any, aliases: Sequence[str]) -> tuple[dict[str, Any], set[str]]:
    """The data of an aliased answer and the aliases its GraphQL errors
    name (an error's path starts with its alias: OTP's DataFetchingException
    of a bad id or date, checked 2026-11-19), whose data is not to be
    trusted. An error naming none of them, every alias failing, or no data
    is a ValueError: the whole answer is not."""
    if not isinstance(doc, dict):
        raise ValueError("answer is not an object")
    errors = doc.get("errors")
    failed: set[str] = set()
    if errors:
        for error in errors if isinstance(errors, list) else [errors]:
            path = error.get("path") if isinstance(error, dict) else None
            head = path[0] if isinstance(path, list) and path else None
            if not isinstance(head, str) or head not in aliases:
                _data(doc)  # an error of the whole request: raises it
                raise ValueError("GraphQL error")
            failed.add(head)
        if len(failed) == len(aliases):
            raise ValueError(f"GraphQL error on every alias ({len(failed)})")
        doc = {"data": doc.get("data")}
    data = _data(doc)
    for alias in aliases:
        if alias not in data:
            raise ValueError(f"answer without {alias}")
    return data, failed


def _group(row: dict[str, Any], trips: dict[str, dict[str, Any]]) -> tuple[str, str]:
    trip = trips.get(row["tripId"], {})
    return trip.get("routeId", ""), row.get("stopHeadsign") or trip.get("tripHeadsign", "")


def _fair_cut(
    rows: list[dict[str, Any]], trips: dict[str, dict[str, Any]], cap: int, per_group: int = ROWS_PER_PATTERN
) -> list[dict[str, Any]]:
    """rows (in time order) cut so that each route and headsign keeps its
    earliest ones, per_group at most, and cap in all: past the cap each
    keeps the same number k (the most that fit; one with fewer keeps all),
    and the rows left over go to the earliest of the next rank. Time order
    stays."""
    rank: list[int] = []
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        key = _group(row, trips)
        rank.append(seen.get(key, 0))
        seen[key] = rank[-1] + 1
    sizes = list(seen.values())

    def kept(k: int) -> int:
        return sum(min(n, k) for n in sizes)

    k = per_group
    if kept(k) > cap:
        lo, hi = 0, per_group
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if kept(mid) <= cap else (lo, mid - 1)
        k = lo
    room = cap - kept(k)
    out = []
    for row, r in zip(rows, rank):
        if r < k:
            out.append(row)
        elif r == k and k < per_group and room > 0:
            out.append(row)
            room -= 1
    return out


def _polyline(geometry: Any) -> dict[str, Any] | None:
    """FUTÁR's trip polyline ({points, length}) of OTP2's tripGeometry, the
    same Google encoding of 1e5 (src/lib/geo.js decodePolyline); None for
    none or anything else."""
    if not isinstance(geometry, dict):
        return None
    points = geometry.get("points")
    if not isinstance(points, str) or not 0 < len(points) <= MAX_POLYLINE_CHARS:
        return None
    if not all(63 <= ord(c) <= 126 for c in points):
        return None
    out: dict[str, Any] = {"points": points}
    length = _int(geometry.get("length"))
    if length is not None and length >= 0:
        out["length"] = length
    return out


def parse_board(
    doc: Any, stop: str, asked: float, *, children: int | None = None, own: dict[str, Any] | None = None
) -> _Board | None:
    """The board of build_board_query's answer for FUTÁR stop id `stop`,
    or with `children` of build_station_query's for that many child poles
    of FUTÁR station `stop` (own: its reference as FUTÁR gave it), merged;
    None when the planner knows none of them. A pole a GraphQL error names
    is left out; any other error, or an answer of the wrong shape, is a
    ValueError, also when the poles left are all null (that says nothing of
    whether the planner knows the station)."""
    if children is None:
        nodes = [_data(doc).get("stop")]
    else:
        aliases = [f"s{i}" for i in range(children)]
        data, failed = _aliased(doc, aliases)
        nodes = [None if alias in failed else data[alias] for alias in aliases]
        if failed and all(node is None for node in nodes):
            raise ValueError("GraphQL errors and no pole known")
    if all(node is None for node in nodes):
        return None
    routes: dict[str, dict[str, Any]] = {}
    trips: dict[str, dict[str, Any]] = {}
    stops: dict[str, dict[str, Any]] = {}
    if own is not None:
        stops[stop] = dict(own, id=stop)
    route_ids: list[str] = []
    rows: list[dict[str, Any]] = []
    for node in nodes:
        if node is None:
            continue
        if not isinstance(node, dict):
            raise ValueError("stop is not an object")
        ref = _stop_ref(node)
        if children is None:
            if ref is not None:
                stops[stop] = dict(ref[1], id=stop)
        elif ref is not None:
            stops.setdefault(stop, {"id": stop, "name": ref[1]["name"]})
            # The card tells a station's poles by FUTÁR's station id.
            stops.setdefault(ref[0], dict(ref[1], parentStationId=stop))
        _board_rows(node, stop, children is not None, rows, routes, trips, stops)
        served = node.get("routes")
        if isinstance(served, list):
            route_ids += [futar_id(r.get("gtfsId")) for r in served if isinstance(r, dict)]
    rows.sort(key=lambda r: r.get("predictedDepartureTime") or r["departureTime"])
    route_ids = [r for r in route_ids if r] or list(routes)
    rows = _fair_cut(rows, trips, BOARD_MAX_ROWS)
    return _Board(stop, asked, tuple(dict.fromkeys(route_ids)), tuple(rows), routes, trips, stops)


def _board_rows(
    node: dict[str, Any],
    stop: str,
    pole_of_station: bool,
    rows: list[dict[str, Any]],
    routes: dict[str, dict[str, Any]],
    trips: dict[str, dict[str, Any]],
    stops: dict[str, dict[str, Any]],
) -> None:
    """One planner stop's departures into rows and the references."""
    calls = node.get("stoptimesWithoutPatterns")
    for st in calls if isinstance(calls, list) else ():
        if not isinstance(st, dict) or st.get("realtimeState") == "CANCELED":
            # FUTÁR lists no departure for a cancelled trip either.
            continue
        trip = st.get("trip")
        if not isinstance(trip, dict):
            continue
        tid = futar_id(trip.get("gtfsId"))
        route = _route_ref(trip.get("route"))
        times = _times(st)
        if not tid or route is None or "departureTime" not in times:
            continue
        rid, rref = route
        routes.setdefault(rid, rref)
        trips.setdefault(tid, _trip_ref(trip, tid, rid))
        pole = _stop_ref(st.get("stop"))
        sid = stop
        if pole is not None:
            sid = pole[0]
            stops.setdefault(sid, dict(pole[1], parentStationId=stop) if pole_of_station else pole[1])
        row: dict[str, Any] = {
            "stopId": sid,
            "tripId": tid,
            "serviceDate": _date(_day(st.get("serviceDay"))),
            "stopHeadsign": _str(st.get("headsign")),
            "wheelchairAccessible": trips[tid]["wheelchairAccessible"],
            "mayRequireBooking": st.get("pickupType") in _BOOKING,
        }
        row.update(times)
        rows.append(row)


def _minutes(value: Any) -> int:
    try:
        minutes = int(str(value))
    except ValueError:
        return DEFAULT_MINUTES_AFTER
    return min(max(minutes, 1), BOARD_WINDOW_MIN)


def _envelope(entry: dict[str, Any], refs: dict[str, Any], now: float) -> dict[str, Any]:
    return {
        "version": 4,
        "status": "OK",
        "code": 200,
        "text": "OK",
        "currentTime": int(now * 1000),
        "source": "mavplusz",
        "data": {
            "limitExceeded": False,
            "entry": entry,
            "references": dict({"agencies": {}, "alerts": {}}, **refs),
        },
    }


def render_board(board: _Board, minutes_after: Any, now: float) -> dict[str, Any]:
    """A FUTÁR arrivals-and-departures-for-stop.json answer of board for a
    card's horizon at wall time now: the departures still due (a minute's
    grace, like the card's) up to minutes_after minutes from now, each route
    and headsign its share of RENDER_MAX_ROWS (_fair_cut), and only the
    references they name."""
    latest = now + _minutes(minutes_after) * 60
    due = []
    for st in board.stop_times:
        dep = st.get("predictedDepartureTime") or st["departureTime"]
        if now - 60 <= dep <= latest:
            due.append(st)
    times = [dict(st) for st in _fair_cut(due, board.trips, RENDER_MAX_ROWS)]
    trips = {st["tripId"]: board.trips[st["tripId"]] for st in times}
    routes = {t["routeId"]: board.routes[t["routeId"]] for t in trips.values()}
    named = {board.stop_id} | {st["stopId"] for st in times}
    stops = {sid: ref for sid, ref in board.stops.items() if sid in named}
    entry = {
        "stopId": board.stop_id,
        "routeIds": list(board.route_ids),
        "alertIds": [],
        "nearbyStopIds": [],
        "stopTimes": times,
    }
    return _envelope(entry, {"routes": routes, "trips": trips, "stops": stops}, now)


def parse_trip(trip: Any, tid: str, date: str, now: float) -> dict[str, Any] | None:
    """A FUTÁR trip-details.json answer of one alias of build_trip_query;
    None for a trip the planner does not know or that does not run on date."""
    if trip is None:
        return None
    if not isinstance(trip, dict):
        raise ValueError("trip is not an object")
    route = _route_ref(trip.get("route"))
    calls = trip.get("stoptimesForDate")
    calls = [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []
    if route is None or not calls:
        return None
    rid, rref = route
    stops: dict[str, dict[str, Any]] = {}
    times: list[dict[str, Any]] = []
    for seq, st in enumerate(calls[: realtime.MAX_STOP_UPDATES]):
        pole = _stop_ref(st.get("stop"))
        if pole is None:
            continue
        clock = _times(st)
        if not clock:
            # No time the card could read (a broken or hostile answer).
            continue
        stops.setdefault(pole[0], pole[1])
        row: dict[str, Any] = {"stopId": pole[0], "stopSequence": seq, "stopHeadsign": _str(st.get("headsign"))}
        row.update(clock)
        times.append(row)
    if not times:
        return None
    entry: dict[str, Any] = {"tripId": tid, "serviceDate": date, "stopTimes": times, "alertIds": []}
    polyline = _polyline(trip.get("tripGeometry"))
    if polyline is not None:
        # The card draws the ride from it, and keeps the details for the page.
        entry["polyline"] = polyline
    refs = {"routes": {rid: rref}, "trips": {tid: _trip_ref(trip, tid, rid)}, "stops": stops}
    return _envelope(entry, refs, now)


def _span_distance(doc: dict[str, Any], now: float) -> float:
    """How far now is from a trip-details answer's run (0 within it)."""
    entry = doc["data"]["entry"]
    times = [
        t for st in entry["stopTimes"] for t in (st.get("departureTime"), st.get("arrivalTime")) if t is not None
    ]
    if not times:
        return float("inf")
    first, last = min(times), max(times)
    return 0.0 if first <= now <= last else min(abs(now - first), abs(now - last))


class _Batch:
    """The trip details asked within TRIP_BATCH_WAIT of each other: a future
    per key, and the tokens reserved for them (one request spends one)."""

    def __init__(self) -> None:
        self.futures: dict[TripKey, asyncio.Future[dict[str, Any] | None]] = {}
        self.tickets: list[_Ticket] = []


class PlannerBoards:
    """The fallback futar.FutarProxy asks when FUTÁR fails (its `fallback`).

    answer(path, params, ticket) is FUTÁR's answer for a board or a trip's
    details from the planner, or None when the planner cannot give one (not
    a BKK id, not known, not asked: rate limit or backoff, or it failed); its
    request spends ticket's token when it has one. It never raises but for
    cancellation. can_answer(path, params) says beforehand whether answer()
    may, and reserve(path, params) also takes the token for it (a _Ticket,
    or None): FUTÁR is cut short only with one. learn(path, params, data)
    reads FUTÁR's own good answers for what the planner lacks.

    `limiter` is the integration's one planner limit (mavplusz.PlannerLimiter,
    shared with the realtime hub; one of its own without it), of which this
    fallback has the smaller share. `monotonic` is the clock of the caches
    (and of the limiter made here), `wall` the epoch clock of the times
    asked and of when a station was last seen, `sleep` the one the trip
    details' batch waits with. `stations` is what stations_data() gave
    before (Home Assistant's store, read through restore_stations), and
    `on_stations_changed` is called whenever learn() changes it, for the
    store to write it later. After close() nothing is learnt any more (a
    FUTÁR answer landing after the entry was unloaded or removed)."""

    paths = PATHS
    # The seconds answer() takes at most (futar.FutarProxy times its fallback by it).
    timeout = PLANNER_TIMEOUT.total

    def __init__(
        self,
        session: aiohttp.ClientSession,
        task_factory: TaskFactory | None = None,
        call_later: CallLater | None = None,
        *,
        monotonic: upstream.Monotonic = upstream.monotonic,
        wall: Wall | None = None,
        sleep: upstream.Sleep = upstream.sleep,
        limiter: mavplusz.PlannerLimiter | None = None,
        stations: Any = None,
        on_stations_changed: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._monotonic = monotonic
        self._wall = wall or (lambda: time.time())
        self._sleep = sleep
        self._task_factory: TaskFactory = task_factory or asyncio.ensure_future
        self._boards = SharedTTLCache(BOARD_TTL, 256, task_factory, call_later, monotonic)
        self._trips = SharedTTLCache(TRIP_TTL, 1024, task_factory, call_later, monotonic)
        # When each board was last asked for, answered or not: one request
        # per stop per BOARD_TTL, also while the requests fail.
        self._asked: dict[Any, float] = {}
        self._dates: dict[str, str] = {}
        # FUTÁR station id -> its reference, child poles (pole -> epoch last
        # seen) and when it was last seen, least recently seen first (learn).
        self._stations: dict[str, dict[str, Any]] = restore_stations(stations, self._wall())
        self._pruned_at = self._monotonic()
        self._on_stations_changed = on_stations_changed
        self._closed = False
        # A stop id, or (station, its poles), -> (whether the planner knows
        # it, monotonic when its board said so).
        self._probed: dict[Any, tuple[bool, float]] = {}
        # ("board", stop) or ("trip", planner trip id) -> monotonic when its
        # request failed for its own sake (REQUEST_FAIL_TTL).
        self._failing: dict[tuple[str, str], float] = {}
        # The trip details batch still taking keys (TRIP_BATCH_WAIT), if any.
        self._open: _Batch | None = None
        # The batches' tasks, held until done (the loop keeps only a weak one).
        self._sending: set[asyncio.Future[Any]] = set()
        self._rate = (limiter or mavplusz.PlannerLimiter(monotonic=monotonic)).client(
            lambda: PlannerError("rate limited"), fallback=True
        )
        self._outages = upstream.OutageLog(_LOGGER, monotonic=monotonic)

    def close(self) -> None:
        """The caches' timers go, and learn() is a no-op from now on."""
        self._closed = True
        self._boards.close()
        self._trips.close()

    def can_answer(self, path: str, params: dict[str, str]) -> bool:
        """Whether answer() may give FUTÁR's answer for path: a BKK stop the
        planner has not said it does not know, a FUTÁR station it knows, has
        not said it does not know, or whose poles are known, a timetable
        trip, none of whose requests failed for its own sake lately, and
        the planner neither backing off nor out of this fallback's tokens.
        Not for the rest: FUTÁR then has its whole budget, since nothing
        else could answer."""
        if not self._rate.available():
            return False
        if path == BOARD_PATH:
            return self._board_ids(str(params.get("stopId") or "")) is not None
        if path == TRIP_PATH:
            gid = self._trip_id(params)
            return gid is not None and not self._is_failing(("trip", gid))
        return False

    def reserve(self, path: str, params: dict[str, str]) -> _Ticket | None:
        """can_answer() and the token answer()'s request will spend, taken
        now (FUTÁR is cut short only with it: the requests of a whole
        dashboard cannot have spent it meanwhile); None when either is not
        there. The caller release()s it whatever happens; a request that
        spent it makes that a no-op."""
        if self._closed or not self.can_answer(path, params) or not self._rate.reserve():
            return None
        return _Ticket(self._rate)

    def learn(self, path: str, params: dict[str, str], data: Any) -> None:
        """What FUTÁR's own answer tells that the planner cannot: the poles
        of FUTÁR's combined stations (any answer's stop references naming
        one as their parent), of a station whose board this is (its
        departures, and the references naming it) and the service date of
        each departure on a board, for the trip's details later. A GTFS
        parent station (BKK_CS…) is learnt from its own board only: nearly
        every stop has one, and a route's details would name hundreds of
        them, pushing out the stations the cards show."""
        if self._closed:
            return
        body = data.get("data") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            return
        refs = body.get("references")
        stops = refs.get("stops") if isinstance(refs, dict) else None
        stops = stops if isinstance(stops, dict) else {}
        changed = False
        now = int(self._wall())
        board = str(params.get("stopId") or "") if path == BOARD_PATH else ""
        for ref in stops.values():
            if not isinstance(ref, dict):
                continue
            parent = ref.get("parentStationId")
            if isinstance(parent, str) and (is_combined(parent) or parent == board):
                changed |= self._learn_pole(parent, ref.get("id"), now)
        if path == BOARD_PATH:
            changed |= self._learn_board(params, body, stops, now)
        if self._monotonic() - self._pruned_at >= SEEN_RESOLUTION:
            self._pruned_at = self._monotonic()
            changed |= _prune(self._stations, now)
        if changed and self._on_stations_changed is not None:
            self._on_stations_changed()

    def _learn_board(self, params: dict[str, str], body: dict[str, Any], stops: dict[str, Any], now: int) -> bool:
        """learn() of a board: its departures' dates and a station's poles
        and reference. Whether the stations changed."""
        changed = False
        stop = str(params.get("stopId") or "")
        entry = body.get("entry")
        times = entry.get("stopTimes") if isinstance(entry, dict) else None
        for st in times if isinstance(times, list) else ():
            if not isinstance(st, dict):
                continue
            tid, date = st.get("tripId"), _futar_date(st.get("serviceDate"))
            if isinstance(tid, str) and date and planner_id(tid) is not None:
                self._remember_date(tid, date)
            changed |= self._learn_pole(stop, st.get("stopId"), now)
        own = stops.get(stop)
        station = self._stations.get(stop)
        if station is not None and isinstance(own, dict):
            ref = _station_ref(stop, own)
            if ref != station["ref"]:
                station["ref"] = ref
                changed = True
        return changed

    def _learn_pole(self, station: Any, pole: Any, now: int | None = None) -> bool:
        """A sighting of pole of station: both move to the most recently
        seen end (at the limits the least recently seen goes). Whether this
        is news for the store: a station or a pole not known before, or one
        not seen for SEEN_RESOLUTION."""
        if not isinstance(station, str) or not is_station(station) or planner_id(station) is None:
            return False
        if not isinstance(pole, str) or is_station(pole) or planner_id(pole) is None:
            return False
        now = int(self._wall()) if now is None else now
        known = self._stations.pop(station, None)
        changed = known is None or now - known["seen"] >= SEEN_RESOLUTION
        if known is None:
            while len(self._stations) >= MAX_STATIONS:
                del self._stations[next(iter(self._stations))]
            known = {"ref": None, "poles": {}, "seen": now}
        known["seen"] = max(known["seen"], now)
        self._stations[station] = known
        poles = known["poles"]
        seen = poles.pop(pole, None)
        if seen is None:
            while len(poles) >= MAX_CHILD_STOPS:
                del poles[next(iter(poles))]
            changed = True
        elif now - seen >= SEEN_RESOLUTION:
            changed = True
        poles[pole] = max(seen or now, now)
        return changed

    def stations_data(self) -> dict[str, Any]:
        """The learnt stations as plain JSON, for Home Assistant's store
        (restore_stations reads it back): station id -> its reference, its
        poles (the least recently seen first; read by older versions too),
        when each was last seen and when the station was; the least recently
        seen station first. Those unseen for STATION_MAX_AGE go first."""
        _prune(self._stations, int(self._wall()))
        return {
            "stations": {
                sid: {"ref": st["ref"], "poles": list(st["poles"]), "seen": dict(st["poles"]), "at": st["seen"]}
                for sid, st in self._stations.items()
            }
        }

    def _remember_date(self, tid: str, date: str) -> None:
        self._dates.pop(tid, None)
        self._dates[tid] = date
        while len(self._dates) > MAX_SERVICE_DATES:
            del self._dates[next(iter(self._dates))]

    def _poles(self, stop: str) -> tuple[str, ...]:
        """The planner ids of a station's poles, learnt or seeded."""
        station = self._stations.get(stop) or SEED_STATIONS.get(stop)
        return tuple(p for p in (planner_id(p) for p in station["poles"]) if p) if station else ()

    def _board_ids(self, stop: str) -> tuple[str, ...] | None:
        """The planner stops a FUTÁR stop's board is asked of: the stop
        itself (a GTFS station, BKK_CS… or BKK_CSF…, answers for its child
        stops) while the planner has not said it does not know it, else a
        station's poles (learnt from FUTÁR, else SEED_STATIONS') unless the
        planner knew none of them either. A FUTÁR combined station (most of
        them the planner does not know) is asked by its poles first. None
        when there is none, or the stop's board failed for its own sake
        within REQUEST_FAIL_TTL."""
        gid = planner_id(stop)
        if gid is None or self._is_failing(("board", stop)):
            return None
        known = self._known(stop)
        if known:
            return (gid,)
        poles = self._poles(stop) if is_station(stop) else ()
        if poles and self._known((stop, poles)) is not False and (known is False or is_combined(stop)):
            return poles
        return None if known is False else (gid,)

    def _known(self, key: Any) -> bool | None:
        """Whether the planner knows stop (or the poles of (station, poles)),
        as its board said within PROBE_TTL; None when not asked (or so long ago)."""
        hit = self._probed.get(key)
        if hit is None:
            return None
        if self._monotonic() - hit[1] >= PROBE_TTL:
            del self._probed[key]
            return None
        return hit[0]

    def _probe_result(self, key: Any, known: bool) -> None:
        self._probed.pop(key, None)
        self._probed[key] = (known, self._monotonic())
        while len(self._probed) > MAX_PROBED:
            del self._probed[next(iter(self._probed))]

    def _is_failing(self, key: tuple[str, str]) -> bool:
        at = self._failing.get(key)
        if at is None:
            return False
        if self._monotonic() - at >= REQUEST_FAIL_TTL:
            del self._failing[key]
            return False
        return True

    def _fail(self, key: tuple[str, str]) -> None:
        """key's request failed for its own sake: not asked for REQUEST_FAIL_TTL."""
        self._failing.pop(key, None)
        self._failing[key] = self._monotonic()
        while len(self._failing) > MAX_FAILING:
            del self._failing[next(iter(self._failing))]

    @staticmethod
    def _trip_id(params: dict[str, str]) -> str | None:
        tid = str(params.get("tripId") or "")
        gid = planner_id(tid)
        return gid if gid is not None and not _AD_HOC_TRIP.match(tid) else None

    async def answer(
        self, path: str, params: dict[str, str], ticket: _Ticket | None = None
    ) -> dict[str, Any] | None:
        try:
            if path == BOARD_PATH:
                return await self._board(params, ticket)
            if path == TRIP_PATH:
                return await self._trip(params, ticket)
        except PlannerError as err:
            _LOGGER.debug("mavplusz.hu gave no %s: %s", path, err)
        return None

    async def _board(self, params: dict[str, str], ticket: _Ticket | None) -> dict[str, Any] | None:
        stop = str(params.get("stopId") or "")
        ids = self._board_ids(stop)
        if ids is None:
            return None
        key = (stop, ids)
        board = await self._boards.get(key, lambda: self._fetch_board(stop, ids, ticket))
        if board is None:
            return None
        return render_board(board, params.get("minutesAfter"), self._wall())

    async def _fetch_board(self, stop: str, ids: tuple[str, ...], ticket: _Ticket | None) -> _Board | None:
        now = self._monotonic()
        for old in [k for k, at in self._asked.items() if now - at >= BOARD_TTL]:
            del self._asked[old]
        key = (stop, ids)
        if key in self._asked:
            raise PlannerError("asked for this stop within BOARD_TTL")
        self._asked[key] = now
        wall = self._wall()
        # A FUTÁR station's poles, or one planner stop (a stop, a station).
        poles = ids != (planner_id(stop),)
        body = build_station_query(ids, int(wall)) if poles else build_board_query(ids[0], int(wall))
        try:
            doc = await self._post(body, BOARD_MAX_BYTES, PLANNER_TIMEOUT, (ticket,) if ticket else ())
            if poles:
                own = (self._stations.get(stop) or SEED_STATIONS.get(stop) or {}).get("ref")
                board = parse_board(doc, stop, wall, children=len(ids), own=own)
            else:
                board = parse_board(doc, stop, wall)
        except _RequestFailed:
            self._fail(("board", stop))
            raise
        except (ValueError, KeyError, TypeError, OverflowError, OSError) as err:
            self._fail(("board", stop))
            raise self._request_failed(err) from None
        # The planner's null says it does not know the stop (or any of the
        # station's poles): FUTÁR keeps its whole budget there for a while.
        self._probe_result((stop, ids) if poles else stop, board is not None)
        if board is not None:
            for st in board.stop_times:
                if st["serviceDate"]:
                    self._remember_date(st["tripId"], st["serviceDate"])
        return board

    def _dates_to_try(self) -> list[str]:
        """The service dates of a trip no board dated: today in Budapest,
        and before NIGHT_END_HOUR yesterday too."""
        local = datetime.fromtimestamp(self._wall(), mavplusz.BUDAPEST)
        today = local.strftime("%Y%m%d")
        if local.hour >= NIGHT_END_HOUR:
            return [today]
        return [(local.date() - timedelta(days=1)).strftime("%Y%m%d"), today]

    async def _trip(self, params: dict[str, str], ticket: _Ticket | None) -> dict[str, Any] | None:
        tid = str(params.get("tripId") or "")
        gid = self._trip_id(params)
        if gid is None or self._is_failing(("trip", gid)):
            return None
        known = self._dates.get(tid)
        dates = [known] if known else self._dates_to_try()
        keys = [TripKey(gid, d) for d in dates]
        results = await asyncio.gather(
            *(self._trips.get(k, lambda k=k: self._batched(k, ticket)) for k in keys), return_exceptions=True
        )
        failed: PlannerError | None = None
        answers: list[tuple[str, dict[str, Any]]] = []
        for date, res in zip(dates, results):
            if isinstance(res, PlannerError):
                failed = failed or res
            elif isinstance(res, BaseException):
                raise res
            elif res is not None:
                answers.append((date, res))
        if not answers:
            if failed is not None:
                raise failed
            return None
        if len(answers) == 1:
            return answers[0][1]
        # Both days run the trip: the run under way (or nearest) is the one.
        now = self._wall()
        date, best = min(answers, key=lambda a: _span_distance(a[1], now))
        self._remember_date(tid, date)
        return best

    async def _batched(self, key: TripKey, ticket: _Ticket | None) -> dict[str, Any] | None:
        """key's details, in the batch still taking keys or a new one: never
        behind a request already under way (each has its own deadline)."""
        batch = self._open
        if batch is None:
            batch = self._open = _Batch()
            task = self._task_factory(self._send(batch))
            self._sending.add(task)
            task.add_done_callback(self._sending.discard)
        if ticket is not None and ticket not in batch.tickets:
            batch.tickets.append(ticket)
        fut = batch.futures.get(key)
        if fut is None:
            fut = batch.futures[key] = asyncio.get_running_loop().create_future()
        return await asyncio.shield(fut)

    async def _send(self, batch: _Batch) -> None:
        """Sends one batch after TRIP_BATCH_WAIT: MAX_TRIPS_PER_REQUEST a
        request, the requests side by side."""
        try:
            try:
                await self._sleep(TRIP_BATCH_WAIT)
            finally:
                if self._open is batch:
                    self._open = None
            keys = list(batch.futures)
            chunks = [keys[i : i + MAX_TRIPS_PER_REQUEST] for i in range(0, len(keys), MAX_TRIPS_PER_REQUEST)]
            await asyncio.gather(*(self._send_chunk(chunk, batch) for chunk in chunks))
        finally:
            # Cancelled (Home Assistant stopping): nobody answers the rest.
            for fut in batch.futures.values():
                if not fut.done():
                    fut.set_exception(PlannerError("not asked"))

    async def _send_chunk(self, keys: list[TripKey], batch: _Batch) -> None:
        futures = [batch.futures[k] for k in keys]
        try:
            doc = await self._post(build_trip_query(keys), mavplusz.MAX_RESPONSE_BYTES, TRIP_TIMEOUT, batch.tickets)
            data, failed = _aliased(doc, [f"t{i}" for i in range(len(keys))])
            now = self._wall()
            results = []
            for i, key in enumerate(keys):
                alias = f"t{i}"
                # A trip a GraphQL error names (a bad id or date) is no
                # answer, the others are.
                results.append(None if alias in failed else parse_trip(data[alias], futar_id(key.gtfs_id), key.date, now))
            for i, key in enumerate(keys):
                if f"t{i}" in failed:
                    self._fail(("trip", key.gtfs_id))
        except PlannerError as err:
            if isinstance(err, _RequestFailed):
                for key in keys:
                    self._fail(("trip", key.gtfs_id))
            for fut in futures:
                if not fut.done():
                    fut.set_exception(err)
            return
        except (ValueError, KeyError, TypeError, OverflowError, OSError) as err:
            failed_err = self._request_failed(err)
            for key in keys:
                self._fail(("trip", key.gtfs_id))
            for fut in futures:
                if not fut.done():
                    fut.set_exception(failed_err)
            return
        for fut, result in zip(futures, results):
            if not fut.done():
                fut.set_result(result)

    async def _post(
        self, body: dict[str, Any], cap: int, timeout: aiohttp.ClientTimeout, tickets: Sequence[_Ticket] = ()
    ) -> Any:
        """One planner request: its decoded answer, or PlannerError when it
        is not made (backoff, rate limit) or fails. It spends a token
        reserved for it (tickets) if one is still held, else takes one. The
        planner failing (no answer, a timeout, HTTP 5xx or 429) backs every
        planner request off and is logged once per outage; a failure of
        this request alone (_RequestFailed: an answer over cap, another HTTP
        status, no JSON) is neither."""
        if self._rate.backing_off():
            raise PlannerError("backing off after a failure")
        if not any(t.spend() for t in tickets):
            self._rate.take()
        try:
            doc = await mavplusz.fetch(self._session, body, user_agent=USER_AGENT, timeout=timeout, cap=cap)
        except mavplusz.TooLarge as err:
            raise self._request_failed(err) from None
        except ValueError as err:
            status = _http_status(err)
            if status is not None and (status >= 500 or status == 429):
                raise self._failed(err) from None
            raise self._request_failed(err) from None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise self._failed(err) from None
        self._outages.answered(None, "mavplusz.hu answers for BKK boards again")
        return doc

    def _failed(self, err: BaseException) -> PlannerError:
        # The integration's one backoff: the realtime hub does not ask either.
        self._rate.failed(FAIL_BACKOFF)
        detail = str(err) or type(err).__name__
        self._outages.failed(
            None,
            "mavplusz.hu unavailable for BKK boards while FUTÁR fails (%s); not logged again until it answers",
            detail,
        )
        return PlannerError(detail)

    @staticmethod
    def _request_failed(err: BaseException) -> _RequestFailed:
        detail = str(err)[:200] or type(err).__name__
        _LOGGER.debug("mavplusz.hu request for a BKK board failed (%s); not asked again for a while", detail)
        return _RequestFailed(detail)


_HTTP_STATUS = re.compile(r"^HTTP (\d{3})$")


def _http_status(err: BaseException) -> int | None:
    """The HTTP status of mavplusz.fetch's ValueError for one ("HTTP 502"), else None."""
    match = _HTTP_STATUS.match(str(err))
    return int(match.group(1)) if match else None


def _prune(stations: dict[str, dict[str, Any]], now: int) -> bool:
    """Drops the poles, and the stations, not seen for STATION_MAX_AGE;
    whether any went."""
    changed = False
    for sid in list(stations):
        st = stations[sid]
        old = [p for p, seen in st["poles"].items() if now - seen > STATION_MAX_AGE]
        for pole in old:
            del st["poles"][pole]
        changed |= bool(old)
        if not st["poles"] or now - st["seen"] > STATION_MAX_AGE:
            del stations[sid]
            changed = True
    return changed


def _station_ref(stop: str, own: dict[str, Any]) -> dict[str, Any]:
    """A FUTÁR station's reference as the merged board gives it, from
    FUTÁR's own: its id, name and position."""
    ref: dict[str, Any] = {"id": stop, "name": _str(own.get("name"))}
    lat, lon = mavplusz._num(own.get("lat")), mavplusz._num(own.get("lon"))
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
        ref["lat"], ref["lon"] = lat, lon
    return ref


def restore_stations(data: Any, now: float | None = None) -> dict[str, dict[str, Any]]:
    """The learnt stations of a stations_data() from before (Home
    Assistant's store), as PlannerBoards keeps them, at epoch now (the
    clock's by default); anything that is not a FUTÁR station of FUTÁR
    poles is left out, and the limits hold (MAX_STATIONS and
    MAX_CHILD_STOPS, the most recently seen kept; STATION_MAX_AGE), so a
    broken or edited file is no worse than none. A record of before the
    seen times (poles alone) counts as seen now: it ages from the update on."""
    stamp = int(time.time() if now is None else now)
    stations = data.get("stations") if isinstance(data, dict) else None
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(stations, dict):
        return out
    rows = []
    for order, (sid, st) in enumerate(stations.items()):
        if not isinstance(sid, str) or not is_station(sid) or planner_id(sid) is None or not isinstance(st, dict):
            continue
        raw = st.get("poles")
        seen = st.get("seen") if isinstance(st.get("seen"), dict) else {}
        poles: dict[str, int] = {}
        for p in raw if isinstance(raw, list) else ():
            if isinstance(p, str) and not is_station(p) and planner_id(p) is not None and p not in poles:
                poles[p] = _seen(seen.get(p), stamp)
        if not poles:
            continue
        # The least recently seen first (stable: the file's order for a tie).
        ordered = sorted(poles.items(), key=lambda item: item[1])[-MAX_CHILD_STOPS:]
        ref = st.get("ref")
        at = _seen(st.get("at"), max(at for _p, at in ordered))
        rows.append((at, order, sid, {
            "ref": _station_ref(sid, ref) if isinstance(ref, dict) else None,
            "poles": dict(ordered),
            "seen": at,
        }))
    rows.sort(key=lambda row: (row[0], row[1]))
    for _at, _order, sid, st in rows[-MAX_STATIONS:]:
        out[sid] = st
    _prune(out, stamp)
    return out


def _seen(value: Any, default: int) -> int:
    """A stored last-seen epoch, or default for none (an older record) or a
    broken one."""
    seen = _int(value)
    if seen is None or not _DAY_MIN <= seen <= _DAY_MAX:
        return default
    return seen


def _futar_date(value: Any) -> str:
    """A FUTÁR stop time's serviceDate as YYYYMMDD ("" for none): FUTÁR
    gives the date, an epoch in milliseconds is read as one too."""
    if isinstance(value, str):
        return value if _DATE.match(value) else ""
    ms = _int(value)
    return _date(ms // 1000) if ms is not None else ""
