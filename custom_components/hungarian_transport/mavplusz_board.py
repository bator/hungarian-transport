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
trip details of many cards in one aliased request, a token bucket of its
own, and a pause after a failure. No Home Assistant imports:
tests/test_mavplusz_board.py runs it bare.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
import logging
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
# Departures per pattern (the planner counts them so); the merged board is
# cut at BOARD_MAX_ROWS, the most rows a card shows (config.js
# maxDepartureRows), FUTÁR itself stops at about 60.
DEPARTURES_PER_PATTERN = 20
BOARD_MAX_ROWS = 80
# Trip details asked within this long of each other go in one request.
TRIP_BATCH_WAIT = 0.05
MAX_TRIPS_PER_REQUEST = mavplusz.MAX_TRIPS_PER_REQUEST
# Requests a second and the burst on top, of this module's own (realtime.py
# has one of its own for the planner too): a destination list on a cold
# cache is one board and a request or two of trip details.
PLANNER_RATE = 0.5
PLANNER_BURST = 30
# The planner's share of the card's 20 s: FUTÁR gets
# futar.FUTAR_FALLBACK_TIMEOUT_SEC first, after a token wait of up to
# futar.FUTAR_TOKEN_WAIT_SEC.
PLANNER_TIMEOUT = aiohttp.ClientTimeout(total=6)
# After a failed request the planner is not asked for this long.
FAIL_BACKOFF = 30
# The service dates the boards gave, per FUTÁR trip id, for its trip
# details (a trip after midnight runs on the day before).
MAX_SERVICE_DATES = 4000
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
_ROUTE_FIELDS = "gtfsId shortName longName mode type color textColor"
_TRIP_REF_FIELDS = f"gtfsId tripHeadsign tripShortName wheelchairAccessible bikesAllowed route {{ {_ROUTE_FIELDS} }}"
_BOARD_QUERY = (
    "query($id: String!, $start: Long!, $range: Int!, $n: Int!) { stop(id: $id) { "
    f"{_STOP_FIELDS} routes {{ gtfsId }} "
    "stoptimesWithoutPatterns(startTime: $start, timeRange: $range, numberOfDepartures: $n, omitNonPickups: true) { "
    "realtime realtimeState scheduledArrival realtimeArrival scheduledDeparture realtimeDeparture "
    f"serviceDay headsign pickupType stop {{ {_STOP_FIELDS} }} trip {{ {_TRIP_REF_FIELDS} }} }} }} }}"
)
_TRIP_STOPTIMES = (
    "stoptimesForDate(serviceDate: $%s) { realtime realtimeState scheduledArrival realtimeArrival "
    f"scheduledDeparture realtimeDeparture serviceDay headsign stop {{ {_STOP_FIELDS} }} }}"
)

Wall = Callable[[], float]


class PlannerError(Exception):
    """The planner did not answer (not asked, failed, or an answer not to
    trust). Never leaves this module: the proxy then raises FUTÁR's error."""


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
        parts.append(f"t{i}: trip(id: $t{i}) {{ {_TRIP_REF_FIELDS} {_TRIP_STOPTIMES % dates[t.date]} }}")
    return {"query": f"query({', '.join(decl)}) {{ {' '.join(parts)} }}", "variables": variables}


def _str(value: Any) -> str:
    return mavplusz._str(value)


def _int(value: Any) -> int | None:
    return mavplusz._int(value)


def _epoch(day: int | None, seconds: int | None) -> int | None:
    return day + seconds if day is not None and seconds is not None else None


def _name(value: Any) -> str:
    name = _str(value)
    return name[len(_CITY_PREFIX):] if name.startswith(_CITY_PREFIX) else name


def _date(service_day: int | None) -> str:
    if service_day is None:
        return ""
    return datetime.fromtimestamp(service_day, mavplusz.BUDAPEST).strftime("%Y%m%d")


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
    day = _int(st.get("serviceDay"))
    out: dict[str, int] = {}
    pairs = (
        ("arrivalTime", "scheduledArrival", "predictedArrivalTime", "realtimeArrival"),
        ("departureTime", "scheduledDeparture", "predictedDepartureTime", "realtimeDeparture"),
    )
    live = st.get("realtime") is True
    for plan_key, plan_field, pred_key, pred_field in pairs:
        plan = _epoch(day, _int(st.get(plan_field)))
        if plan is not None:
            out[plan_key] = plan
        pred = _epoch(day, _int(st.get(pred_field))) if live else None
        if pred is not None:
            out[pred_key] = pred
    return out


def _data(doc: Any) -> dict[str, Any]:
    return mavplusz._data(doc)


def parse_board(doc: Any, stop: str, asked: float) -> _Board | None:
    """The board of build_board_query's answer for FUTÁR stop id `stop`;
    None when the planner does not know the stop. A GraphQL error or an
    answer of the wrong shape is a ValueError."""
    node = _data(doc).get("stop")
    if node is None:
        return None
    if not isinstance(node, dict):
        raise ValueError("stop is not an object")
    routes: dict[str, dict[str, Any]] = {}
    trips: dict[str, dict[str, Any]] = {}
    stops: dict[str, dict[str, Any]] = {}
    own = _stop_ref(node)
    if own is not None:
        stops[stop] = dict(own[1], id=stop)
    rows: list[dict[str, Any]] = []
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
            stops.setdefault(sid, pole[1])
        row: dict[str, Any] = {
            "stopId": sid,
            "tripId": tid,
            "serviceDate": _date(_int(st.get("serviceDay"))),
            "stopHeadsign": _str(st.get("headsign")),
            "wheelchairAccessible": trips[tid]["wheelchairAccessible"],
            "mayRequireBooking": st.get("pickupType") in _BOOKING,
        }
        row.update(times)
        rows.append(row)
    rows.sort(key=lambda r: r.get("predictedDepartureTime") or r["departureTime"])
    served = node.get("routes")
    route_ids = [futar_id(r.get("gtfsId")) for r in served if isinstance(r, dict)] if isinstance(served, list) else []
    route_ids = [r for r in route_ids if r] or list(routes)
    return _Board(stop, asked, tuple(dict.fromkeys(route_ids)), tuple(rows[:BOARD_MAX_ROWS]), routes, trips, stops)


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
    grace, like the card's) up to minutes_after minutes from now, and only
    the references they name."""
    latest = now + _minutes(minutes_after) * 60
    times = []
    for st in board.stop_times:
        dep = st.get("predictedDepartureTime") or st["departureTime"]
        if now - 60 <= dep <= latest:
            times.append(dict(st))
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
        stops.setdefault(pole[0], pole[1])
        row: dict[str, Any] = {"stopId": pole[0], "stopSequence": seq, "stopHeadsign": _str(st.get("headsign"))}
        row.update(_times(st))
        times.append(row)
    if not times:
        return None
    entry = {"tripId": tid, "serviceDate": date, "stopTimes": times, "alertIds": []}
    refs = {"routes": {rid: rref}, "trips": {tid: _trip_ref(trip, tid, rid)}, "stops": stops}
    return _envelope(entry, refs, now)


class PlannerBoards:
    """The fallback futar.FutarProxy asks when FUTÁR fails (its `fallback`).

    answer(path, params) is FUTÁR's answer for a board or a trip's details
    from the planner, or None when the planner cannot give one (not a BKK
    id, not known, not asked: rate limit or backoff, or it failed). It never
    raises but for cancellation. `monotonic` is the clock of the caches, the
    rate limit and the backoff, `wall` the epoch clock of the times asked,
    `sleep` the one the trip details' batch waits with."""

    paths = PATHS

    def __init__(
        self,
        session: aiohttp.ClientSession,
        task_factory: TaskFactory | None = None,
        call_later: CallLater | None = None,
        *,
        monotonic: upstream.Monotonic = upstream.monotonic,
        wall: Wall | None = None,
        sleep: upstream.Sleep = upstream.sleep,
        burst: int = PLANNER_BURST,
    ) -> None:
        self._session = session
        self._monotonic = monotonic
        self._wall = wall or (lambda: time.time())
        self._sleep = sleep
        self._task_factory: TaskFactory = task_factory or asyncio.ensure_future
        self._boards = SharedTTLCache(BOARD_TTL, 256, task_factory, call_later, monotonic)
        self._trips = SharedTTLCache(TRIP_TTL, 1024, task_factory, call_later, monotonic)
        # When each stop's board was last asked for, answered or not: one
        # request per stop per BOARD_TTL, also while the requests fail.
        self._asked: dict[str, float] = {}
        self._dates: dict[str, str] = {}
        self._pending: dict[TripKey, asyncio.Future[dict[str, Any] | None]] = {}
        self._flusher: asyncio.Future[Any] | None = None
        self._retry_at = -1e18
        self._rate = upstream.TokenBucket(
            PLANNER_RATE,
            burst,
            refused=lambda: PlannerError("rate limited"),
            log_refused=lambda count: _LOGGER.warning(
                "mavplusz.hu requests for BKK boards over the integration's rate limit: "
                "%d refused since the last such line",
                count,
            ),
            monotonic=monotonic,
        )
        self._outages = upstream.OutageLog(_LOGGER, monotonic=monotonic)

    def close(self) -> None:
        self._boards.close()
        self._trips.close()

    async def answer(self, path: str, params: dict[str, str]) -> dict[str, Any] | None:
        try:
            if path == BOARD_PATH:
                return await self._board(params)
            if path == TRIP_PATH:
                return await self._trip(params)
        except PlannerError as err:
            _LOGGER.debug("mavplusz.hu gave no %s: %s", path, err)
        return None

    async def _board(self, params: dict[str, str]) -> dict[str, Any] | None:
        stop = str(params.get("stopId") or "")
        gid = planner_id(stop)
        if gid is None:
            return None
        board = await self._boards.get(gid, lambda: self._fetch_board(stop, gid))
        if board is None:
            return None
        return render_board(board, params.get("minutesAfter"), self._wall())

    async def _fetch_board(self, stop: str, gid: str) -> _Board | None:
        now = self._monotonic()
        for old in [k for k, at in self._asked.items() if now - at >= BOARD_TTL]:
            del self._asked[old]
        if gid in self._asked:
            raise PlannerError("asked for this stop within BOARD_TTL")
        self._asked[gid] = now
        wall = self._wall()
        doc = await self._post(build_board_query(gid, int(wall)))
        try:
            board = parse_board(doc, stop, wall)
        except (ValueError, KeyError, TypeError) as err:
            raise self._failed(err) from None
        if board is not None:
            for st in board.stop_times:
                if st["serviceDate"]:
                    self._dates.pop(st["tripId"], None)
                    self._dates[st["tripId"]] = st["serviceDate"]
            while len(self._dates) > MAX_SERVICE_DATES:
                del self._dates[next(iter(self._dates))]
        return board

    async def _trip(self, params: dict[str, str]) -> dict[str, Any] | None:
        tid = str(params.get("tripId") or "")
        gid = planner_id(tid)
        if gid is None:
            return None
        date = self._dates.get(tid) or mavplusz.service_date(None, self._wall())
        key = TripKey(gid, date)
        return await self._trips.get(key, lambda: self._batched(key))

    async def _batched(self, key: TripKey) -> dict[str, Any] | None:
        fut = self._pending.get(key)
        if fut is None:
            fut = asyncio.get_running_loop().create_future()
            self._pending[key] = fut
        if self._flusher is None:
            self._flusher = self._task_factory(self._flush())
        return await asyncio.shield(fut)

    async def _flush(self) -> None:
        """Sends the waiting trips, MAX_TRIPS_PER_REQUEST a request, until
        none is left; the ones asked while a request runs go in the next."""
        try:
            await self._sleep(TRIP_BATCH_WAIT)
            while self._pending:
                keys = list(self._pending)[:MAX_TRIPS_PER_REQUEST]
                futures = [self._pending.pop(k) for k in keys]
                try:
                    doc = await self._post(build_trip_query(keys))
                    data = _data(doc)
                    now = self._wall()
                    results = []
                    for i, key in enumerate(keys):
                        alias = f"t{i}"
                        if alias not in data:
                            raise ValueError(f"answer without {alias}")
                        results.append(parse_trip(data[alias], futar_id(key.gtfs_id), key.date, now))
                except PlannerError as err:
                    for fut in futures:
                        if not fut.done():
                            fut.set_exception(err)
                    continue
                except (ValueError, KeyError, TypeError) as err:
                    failed = self._failed(err)
                    for fut in futures:
                        if not fut.done():
                            fut.set_exception(failed)
                    continue
                for fut, result in zip(futures, results):
                    if not fut.done():
                        fut.set_result(result)
        finally:
            self._flusher = None
            # Cancelled (Home Assistant stopping): nobody answers the rest.
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(PlannerError("not asked"))
            self._pending.clear()

    async def _post(self, body: dict[str, Any]) -> Any:
        """One planner request: its decoded answer, or PlannerError when it
        is not made (backoff, rate limit) or fails, logged once per outage."""
        if self._monotonic() < self._retry_at:
            raise PlannerError("backing off after a failure")
        self._rate.take()
        try:
            doc = await mavplusz.fetch(self._session, body, user_agent=USER_AGENT, timeout=PLANNER_TIMEOUT)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as err:
            raise self._failed(err) from None
        self._outages.answered(None, "mavplusz.hu answers for BKK boards again")
        return doc

    def _failed(self, err: BaseException) -> PlannerError:
        self._retry_at = self._monotonic() + FAIL_BACKOFF
        detail = str(err) or type(err).__name__
        self._outages.failed(
            None,
            "mavplusz.hu unavailable for BKK boards while FUTÁR fails (%s); not logged again until it answers",
            detail,
        )
        return PlannerError(detail)
