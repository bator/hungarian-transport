"""Delays and vehicle positions from mavplusz.hu, MÁV's journey planner.

mavplusz.hu runs OpenTripPlanner 2 with the MÁV and GYSEV trains, the
Volán, Paks and city (Debrecen, Miskolc, Veszprém) timetables and their
realtime updates; jbb.ghsq.de's GTFS-Realtime feeds for those operators are
exported from this planner, so its ids are theirs with the prefix kept: jbb
trip hkir_2036_1-3 of the hkir: feed is OTP trip "hkir:hkir_2036_1-3", stop
hkir_779365_6 is "hkir:hkir_779365_6", MÁV trip 36166415 of the 1: feeds is
"1:36166415" and platform 005510017_9 of Budapest-Keleti "1:005510017_9".

For the sources that opt in (realtime_feeds.json "mavplusz": true) the hub
asks the planner first, in one GraphQL request for just the trips a card
shows, and reads the jbb feeds only when it fails (see docs/realtime.md).
This module builds that request and turns the answer into a realtime.Feed
keyed as realtime.decode_feed keys a jbb feed, so realtime's matching reads
both alike. No Home Assistant imports: tests/test_mavplusz.py runs it bare.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
import dataclasses
from datetime import datetime
import json
import logging
import re
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

import aiohttp

from . import jsonsafe, realtime, upstream

_LOGGER = logging.getLogger(__name__)

URL = "https://mavplusz.hu/otp2-backend/otp/routers/default/index/graphql"
# Trips in one request, of the ones not cached yet; the rest are cut, never
# split into more requests. A call of realtime.MAX_TRIPS (40) rows can need
# more: a route + start row asks for its resolved trips and its card's own
# trip id. The hub asks the resolved ones first (realtime._query_mavplusz),
# and the ones cut go out on a later poll, while the others are cached.
MAX_TRIPS_PER_REQUEST = 40
# The vehicles of the trips on a realtime pattern (_RT_PATTERN), asked by
# their routes in a request of their own after the trips' (realtime's
# _mav_vehicles): every vehicle on every pattern of each route, about 250
# bytes a vehicle (975 bytes for the MÁV route 1:V3775_106 with 4 vehicles,
# 1.3 kB for 1:V3803_106 with 5, on 2026-11-18 at 22:15). Those sizes were
# measured at night only, so nothing here relies on them: the request has
# its own cap, far under the trips', and a route's size is learnt from its
# answers (RouteSizes). An answer over it, or any failure, only leaves the
# vehicles out that round; the delays never wait on it.
MAX_ROUTES_PER_REQUEST = 20
VEHICLE_MAX_BYTES = 256 * 1024
# What the routes of one request may add up to by RouteSizes' reckoning
# (three quarters of the cap: the reckoning is last time's), a route not
# seen yet counted as UNSEEN_ROUTE_BYTES (30 vehicles), and a route over
# HUGE_ROUTE_BYTES alone not asked until its size is forgotten.
VEHICLE_BUDGET_BYTES = VEHICLE_MAX_BYTES * 3 // 4
UNSEEN_ROUTE_BYTES = 8 * 1024
HUGE_ROUTE_BYTES = 64 * 1024
# A route's learnt size is kept this long (the day's traffic changes it),
# for so many routes at most.
ROUTE_SIZE_TTL = 3 * 3600
MAX_ROUTE_SIZES = 2000
# Decompressed answer cap. The answer is decoded on the event loop, which
# json.loads holds for as long as it runs (about 11 ms a MB), so the cap is
# near the largest real answer: a call is 210 bytes and a vehicle 250, and 40
# Miskolc city trips (17 calls each) were 134 KB on 2026-11-15; 40 trips of
# 100 calls with 15 vehicles on each pattern, more than any Volán trip has,
# would be 0.96 MiB. The route lists are 40 bytes a trip, 4 KB a route.
MAX_RESPONSE_BYTES = 1024 * 1024
BUDAPEST = ZoneInfo("Europe/Budapest")
_DATE = re.compile(r"^\d{8}$")
# Every request one Home Assistant sends the planner, the realtime hub's
# and the BKK boards' together (PlannerLimiter): one every two seconds with
# a burst of 30, as docs/realtime.md "Polling" says, however many cards,
# browsers and users ask.
PLANNER_RATE = 0.5
PLANNER_BURST = 30
# Of those, the BKK boards' and trip details' while FUTÁR fails
# (mavplusz_board.py): half of either. Any logged-in user can have FUTÁR's
# failures asked of the planner for stops no card shows; that uses up this
# share, never the delays every other card's refresh needs.
FALLBACK_RATE = 0.25
FALLBACK_BURST = 15
# Of those, the realtime hub's vehicle requests (one a round at most, after
# its trip request, for the trips on a realtime pattern): one every ten
# seconds with a burst of 6, where a feed's round is a minute. Refused,
# the trips have no vehicle that round, nothing else.
VEHICLE_RATE = 0.1
VEHICLE_BURST = 6
# stopRelationship.status -> VehiclePosition.VehicleStopStatus.
_STATUS = {"INCOMING_AT": 0, "STOPPED_AT": 1, "IN_TRANSIT_TO": 2}
# A vehicle's position, naming its trip. No vehicle ids: they are plate
# numbers, and nothing here needs them.
_POSITION_FIELDS = (
    "vehiclePositions { lat lon heading speed lastUpdated stopRelationship { status stop { gtfsId } } trip { gtfsId } }"
)
# Per trip: its route, its calls on the service date with their realtime
# times (seconds after serviceDay, an epoch), and its pattern's code and
# vehicles. A trip the realtime data changed (MODIFIED, some UPDATED) has
# a realtime pattern ("1:V3775_106::rt#2975") that lists no vehicle: they
# are on its route's scheduled patterns, asked by the route in a request of
# their own (build_vehicle_query).
_TRIP_FIELDS = (
    "gtfsId route { gtfsId } "
    "stoptimesForDate(serviceDate: $%s) { realtime realtimeState realtimeArrival realtimeDeparture "
    "arrivalDelay departureDelay serviceDay stop { gtfsId } } "
    "pattern { code " + _POSITION_FIELDS + " }"
)
_ROUTE_FIELDS = "patterns { " + _POSITION_FIELDS + " }"
# A realtime pattern's code: the scheduled one's route plus "::rt#".
_RT_PATTERN = "::rt#"


class TooLarge(ValueError):
    """An answer over the cap fetch() was given (read no further)."""


class _Refused(Exception):
    """A token bucket of PlannerLimiter was empty (never leaves it)."""


class PlannerLimiter:
    """The one rate limit and the one backoff of every request to the planner.

    The realtime hub and the BKK boards (mavplusz_board.PlannerBoards) ask
    the same server; with a bucket and a backoff each, they asked it twice
    as often as documented, and one kept asking while the other had found
    it down. Home Assistant makes one of these (__init__._planner_limiter)
    and hands it to both; each takes its tokens through a client().

    A request takes a token of the total; one of the BKK fallback also one
    of its share (FALLBACK_RATE), first, and the hub's vehicle requests one
    of theirs (VEHICLE_RATE), so a refused fallback or vehicle request
    never spends a token the delays need; every bucket is checked before
    any is taken from, so a request the total refuses spends no token of
    its share either. A token may be reserved ahead (PlannerClient.reserve)
    and refunded when its request never goes. After a failure nobody asks for
    the seconds the failing requester gives (the longer one wins); the
    vehicle requests never set one (realtime's _mav_vehicles).
    """

    def __init__(
        self,
        *,
        rate: float = PLANNER_RATE,
        burst: int = PLANNER_BURST,
        fallback_rate: float = FALLBACK_RATE,
        fallback_burst: int = FALLBACK_BURST,
        vehicle_rate: float = VEHICLE_RATE,
        vehicle_burst: int = VEHICLE_BURST,
        monotonic: upstream.Monotonic = upstream.monotonic,
    ) -> None:
        self._monotonic = monotonic
        self._retry_at = -1e18
        self._total = upstream.TokenBucket(
            rate,
            burst,
            refused=_Refused,
            log_refused=lambda count: _LOGGER.warning(
                "mavplusz.hu requests over the integration's rate limit (%.2f/s, burst %d): "
                "%d refused since the last such line",
                rate,
                burst,
                count,
            ),
            monotonic=monotonic,
        )
        self._fallback = upstream.TokenBucket(
            fallback_rate,
            fallback_burst,
            refused=_Refused,
            log_refused=lambda count: _LOGGER.warning(
                "mavplusz.hu requests for BKK boards while FUTÁR fails over the integration's rate limit "
                "(%.2f/s, burst %d): %d refused since the last such line",
                fallback_rate,
                fallback_burst,
                count,
            ),
            monotonic=monotonic,
        )
        # Refused vehicle requests are expected (a busy minute): debug only.
        self._vehicles = upstream.TokenBucket(
            vehicle_rate,
            vehicle_burst,
            refused=_Refused,
            log_refused=lambda count: _LOGGER.debug(
                "mavplusz.hu vehicle requests over their share (%.2f/s, burst %d): %d refused",
                vehicle_rate,
                vehicle_burst,
                count,
            ),
            monotonic=monotonic,
        )

    def backing_off(self) -> bool:
        """A request failed within the backoff it set: nobody asks now."""
        return self._monotonic() < self._retry_at

    def failed(self, seconds: float) -> None:
        """A request failed: no request for seconds (or the backoff already set, if longer)."""
        self._retry_at = max(self._retry_at, self._monotonic() + seconds)

    def _buckets(self, fallback: bool, vehicles: bool) -> list[upstream.TokenBucket]:
        return [b for b, on in ((self._fallback, fallback), (self._vehicles, vehicles), (self._total, True)) if on]

    def available(self, *, fallback: bool = False, vehicles: bool = False) -> bool:
        """Whether take() would let a request go now; takes nothing."""
        return not self.backing_off() and all(b.available() for b in self._buckets(fallback, vehicles))

    def take(self, *, fallback: bool = False, vehicles: bool = False, quiet: bool = False) -> bool:
        """Whether a request may go now; it then has its token(s). False
        while backing off (not counted as refused) or over a limit (logged,
        unless quiet: a token asked for ahead, PlannerClient.reserve). Every
        bucket is checked before any is taken from: a request one of them
        refuses spends no token of the others."""
        if self.backing_off():
            return False
        buckets = self._buckets(fallback, vehicles)
        for bucket in buckets:
            if not bucket.available():
                if not quiet:
                    try:
                        bucket.take()  # refused: counted and logged
                    except _Refused:
                        pass
                return False
        for bucket in buckets:
            bucket.take()
        return True

    def refund(self, *, fallback: bool = False, vehicles: bool = False) -> None:
        """The token(s) of a take() whose request was not sent go back."""
        for bucket in self._buckets(fallback, vehicles):
            bucket.refund()

    def client(
        self, refused: Callable[[], BaseException], *, fallback: bool = False, vehicles: bool = False
    ) -> PlannerClient:
        """The limiter as one requester sees it: take() raises refused()."""
        return PlannerClient(self, refused, fallback, vehicles)


class PlannerClient:
    """One requester's handle on the shared PlannerLimiter (its client())."""

    def __init__(
        self, limiter: PlannerLimiter, refused: Callable[[], BaseException], fallback: bool, vehicles: bool = False
    ) -> None:
        self.limiter = limiter
        self._refused = refused
        self._fallback = fallback
        self._vehicles = vehicles

    def take(self) -> None:
        """A token for one request now, or refused() at once."""
        if not self.limiter.take(fallback=self._fallback, vehicles=self._vehicles):
            raise self._refused()

    def available(self) -> bool:
        """Whether take() would get a token now (not backing off, every
        bucket of this requester's with one); takes none."""
        return self.limiter.available(fallback=self._fallback, vehicles=self._vehicles)

    def reserve(self) -> bool:
        """take() for a request that may follow (FUTÁR's fallback, asked
        for before FUTÁR is cut short): a refusal is no error and not
        logged. refund() gives the token(s) back if the request never goes."""
        return self.limiter.take(fallback=self._fallback, vehicles=self._vehicles, quiet=True)

    def refund(self) -> None:
        self.limiter.refund(fallback=self._fallback, vehicles=self._vehicles)

    def backing_off(self) -> bool:
        return self.limiter.backing_off()

    def failed(self, seconds: float) -> None:
        self.limiter.failed(seconds)


class TripKey(NamedTuple):
    """One trip to ask for: the planner's id, the service date (YYYYMMDD),
    and the feed prefix its ids are stripped of."""

    gtfs_id: str
    date: str
    prefix: str


class RouteKey(NamedTuple):
    """One route's trips to list: the planner's route id (prefix + the
    card's route), the service date, the feed prefix."""

    gtfs_id: str
    date: str
    prefix: str


class TripResult(NamedTuple):
    """What the planner said of one trip; both None for a trip it runs with
    no realtime data on that date. ends is the epoch of the run's last call
    that date (its realtime time, the scheduled one without realtime), None
    when it lists no call: the hub tells a past day's run that is over from
    one still on its way by it (realtime._query_mavplusz)."""

    trip_update: realtime.TripUpdate | None
    vehicle: realtime.Vehicle | None
    ends: int | None = None
    # The planner's route id (prefix kept) and whether the trip's pattern
    # is a realtime one (_RT_PATTERN), whose vehicles must be asked by the
    # route (build_vehicle_query).
    route: str = ""
    rt_pattern: bool = False


def gtfs_id(trip_id: str, prefix: str) -> str:
    """The planner's id of a card's trip id, with or without the prefix."""
    return prefix + realtime._strip(trip_id, prefix)


def service_date(wanted: Any, now: float) -> str:
    """The card's date when it is YYYYMMDD, else today in Budapest."""
    if isinstance(wanted, str) and _DATE.match(wanted):
        return wanted
    return datetime.fromtimestamp(now, BUDAPEST).strftime("%Y%m%d")


def build_query(trips: Sequence[TripKey]) -> dict[str, Any]:
    """The aliased GraphQL request body for trips: alias tN is trips[N].
    The ids and dates go in as variables, never into the query text."""
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
        parts.append(f"t{i}: trip(id: $t{i}) {{ {_TRIP_FIELDS % dates[t.date]} }}")
    return {"query": f"query({', '.join(decl)}) {{ {' '.join(parts)} }}", "variables": variables}


def build_vehicle_query(routes: Sequence[str]) -> dict[str, Any]:
    """The aliased request for the vehicles on every pattern of each route
    (planner route ids, prefix kept): alias rN is routes[N]."""
    decl = [f"$r{i}: String!" for i in range(len(routes))]
    parts = [f"r{i}: route(id: $r{i}) {{ {_ROUTE_FIELDS} }}" for i in range(len(routes))]
    variables = {f"r{i}": r for i, r in enumerate(routes)}
    return {"query": f"query({', '.join(decl)}) {{ {' '.join(parts)} }}", "variables": variables}


def parse_vehicles(doc: Any, routes: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """Per route of build_vehicle_query(routes): the raw positions on its
    patterns (none for a route the planner does not know). Errors and
    missing aliases as in parse_response."""
    data = _data(doc)
    out: dict[str, list[dict[str, Any]]] = {}
    for i, route in enumerate(routes):
        alias = f"r{i}"
        if alias not in data:
            raise ValueError(f"answer without {alias}")
        out[route] = _route_positions(data[alias])
    return out


def answer_sizes(doc: Any, routes: Sequence[str]) -> dict[str, int]:
    """Per route of a parse_vehicles answer: the bytes of its alias, as
    compact JSON (what RouteSizes learns)."""
    data = doc["data"]
    return {r: len(json.dumps(data[f"r{i}"], separators=(",", ":"))) for i, r in enumerate(routes)}


def needs_route_vehicles(res: TripResult | None) -> bool:
    """A live trip on a realtime pattern without its vehicle: its vehicle is
    on its route's scheduled patterns (build_vehicle_query)."""
    return bool(
        res is not None and res.trip_update is not None and res.vehicle is None and res.rt_pattern and res.route
    )


def add_vehicles(
    results: dict[TripKey, TripResult | None], positions: Sequence[dict[str, Any]]
) -> dict[TripKey, TripResult | None]:
    """results with a vehicle from positions (a route request's) for each
    trip needs_route_vehicles; one live run per vehicle, as parse_response."""
    out = dict(results)
    for key, res in results.items():
        if needs_route_vehicles(res):
            vehicle = _vehicle(positions, key, realtime._strip(res.route, key.prefix))
            if vehicle is not None:
                out[key] = res._replace(vehicle=vehicle)
    _one_run(out)
    return out


class RouteSizes:
    """What the routes' vehicle answers weighed, so a request of them stays
    under VEHICLE_MAX_BYTES whatever the hour: per route its last answer's
    bytes (answer_sizes), a route of an answer over the cap its share of
    the cap at least (one route alone over it is HUGE_ROUTE_BYTES and more,
    so it is left out), each forgotten after ROUTE_SIZE_TTL. pick() takes
    the least recently asked first, so every route gets its turn."""

    def __init__(self, monotonic: upstream.Monotonic = upstream.monotonic) -> None:
        self._monotonic = monotonic
        # route -> (bytes, when last asked); the least recently asked first.
        self._sizes: dict[str, tuple[int, float]] = {}

    def size(self, route: str) -> int:
        hit = self._sizes.get(route)
        if hit is None or self._monotonic() - hit[1] >= ROUTE_SIZE_TTL:
            return UNSEEN_ROUTE_BYTES
        return hit[0]

    def pick(self, routes: Sequence[str]) -> list[str]:
        """The routes of one request: no huge one, at most
        MAX_ROUTES_PER_REQUEST, together within VEHICLE_BUDGET_BYTES."""
        now = self._monotonic()
        for r in [r for r, (_b, at) in self._sizes.items() if now - at >= ROUTE_SIZE_TTL]:
            del self._sizes[r]
        wanted = sorted(dict.fromkeys(routes), key=lambda r: self._sizes[r][1] if r in self._sizes else -1e18)
        out: list[str] = []
        total = 0
        for route in wanted:
            size = self.size(route)
            if size > HUGE_ROUTE_BYTES or total + size > VEHICLE_BUDGET_BYTES:
                continue
            out.append(route)
            total += size
            if len(out) >= MAX_ROUTES_PER_REQUEST:
                break
        return out

    def asked(self, routes: Sequence[str], sizes: dict[str, int] | None = None, *, too_large: bool = False) -> None:
        """routes were asked: sizes is what each weighed; too_large, the
        answer went over VEHICLE_MAX_BYTES (each at least its share)."""
        now = self._monotonic()
        for route in routes:
            if too_large:
                # A route over the cap alone is huge whatever the cap.
                alone = HUGE_ROUTE_BYTES + 1 if len(routes) == 1 else 0
                size = max(self.size(route), VEHICLE_MAX_BYTES // len(routes) + 1, alone)
            elif sizes is not None and route in sizes:
                size = sizes[route]
            else:
                size = self.size(route)
            self._sizes.pop(route, None)
            self._sizes[route] = (size, now)
        while len(self._sizes) > MAX_ROUTE_SIZES:
            del self._sizes[next(iter(self._sizes))]


def build_route_query(routes: Sequence[RouteKey]) -> dict[str, Any]:
    """The aliased request listing each route's trip ids on its service
    date (alias rN is routes[N]): every pattern's tripsForDate. The OTP city
    trip ids carry their start (10842_1943_081900_AZN starts at 08:19), so
    the ids alone match a card's route + start, as on the jbb feeds."""
    dates: dict[str, str] = {}
    decl: list[str] = []
    parts: list[str] = []
    variables: dict[str, str] = {}
    for date in dict.fromkeys(r.date for r in routes):
        name = f"d{len(dates)}"
        dates[date] = name
        decl.append(f"${name}: String")
        variables[name] = date
    for i, r in enumerate(routes):
        decl.append(f"$r{i}: String!")
        variables[f"r{i}"] = r.gtfs_id
        parts.append(f"r{i}: route(id: $r{i}) {{ patterns {{ tripsForDate(serviceDate: ${dates[r.date]}) {{ gtfsId }} }} }}")
    return {"query": f"query({', '.join(decl)}) {{ {' '.join(parts)} }}", "variables": variables}


def parse_routes(doc: Any, routes: Sequence[RouteKey]) -> dict[RouteKey, tuple[str, ...] | None]:
    """Per route of build_route_query(routes): its trip ids that day (the
    planner's, prefix kept), or None for a route the planner does not know.
    Errors and missing aliases as in parse_response."""
    data = _data(doc)
    out: dict[RouteKey, tuple[str, ...] | None] = {}
    for i, key in enumerate(routes):
        alias = f"r{i}"
        if alias not in data:
            raise ValueError(f"answer without {alias}")
        route = data[alias]
        if route is None:
            out[key] = None
            continue
        if not isinstance(route, dict):
            raise ValueError("route is not an object")
        ids: list[str] = []
        patterns = route.get("patterns")
        for pattern in patterns if isinstance(patterns, list) else ():
            trips = pattern.get("tripsForDate") if isinstance(pattern, dict) else None
            for trip in trips if isinstance(trips, list) else ():
                gid = _gid(trip)
                if gid:
                    ids.append(gid)
        out[key] = tuple(dict.fromkeys(ids))
    return out


def trips_starting(trip_ids: Sequence[str], prefix: str, start: int) -> list[str]:
    """The trip ids whose start (HHMMSS in the id, realtime._start_minutes)
    is start minutes after midnight, modulo a day as on the jbb feeds."""
    out = []
    for gid in trip_ids:
        minutes = realtime._start_minutes(realtime._strip(gid, prefix))
        if minutes is not None and minutes % 1440 == start % 1440:
            out.append(gid)
    return out


async def fetch(
    session: aiohttp.ClientSession,
    body: dict[str, Any],
    *,
    user_agent: str | None = None,
    timeout: aiohttp.ClientTimeout | None = None,
    cap: int = MAX_RESPONSE_BYTES,
) -> Any:
    """POST body to the planner; the decoded JSON answer. ValueError on an
    HTTP error or a non-JSON answer, TooLarge (one) on an oversized one. user_agent and timeout
    default to realtime's (read per call: realtime imports this module)."""
    timeout = timeout or realtime.FETCH_TIMEOUT
    headers = {
        "User-Agent": user_agent or realtime.USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with session.post(
        URL, data=json.dumps(body).encode(), headers=headers, timeout=timeout, allow_redirects=False
    ) as resp:
        if resp.status != 200:
            raise ValueError(f"HTTP {resp.status}")
        length = getattr(resp, "content_length", None)
        if length is not None and length > cap:
            raise TooLarge(f"{length} bytes, over {cap}")
        chunks = []
        total = 0
        async for chunk in resp.content.iter_chunked(65536):
            total += len(chunk)
            if total > cap:
                raise TooLarge(f"over {cap} bytes")
            chunks.append(chunk)
    try:
        return jsonsafe.loads(b"".join(chunks))
    except RecursionError:
        # Nested deeper than the decoder goes: not an answer, and the
        # caller's failure path is the one for ValueError.
        raise ValueError("answer nested too deep") from None


def _str(value: Any) -> str:
    return value if isinstance(value, str) and len(value) <= realtime.MAX_STRING else ""


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if out == out and abs(out) != float("inf") else None


def _gid(obj: Any) -> str:
    return _str(obj.get("gtfsId")) if isinstance(obj, dict) else ""


def _epoch(day: int | None, seconds: int | None) -> int | None:
    return day + seconds if day is not None and seconds is not None else None


def _stop_update(st: dict[str, Any], prefix: str) -> realtime.StopUpdate:
    stop_id = realtime._strip(_gid(st.get("stop")), prefix)
    if st.get("realtime") is not True:
        # A scheduled time with delay 0 is no prediction.
        return realtime.StopUpdate(stop_id, None, None, None, None, None, 0)
    if st.get("realtimeState") == "CANCELED":
        return realtime.StopUpdate(stop_id, None, None, None, None, None, 1)
    day = _int(st.get("serviceDay"))
    return realtime.StopUpdate(
        stop_id,
        None,
        _int(st.get("arrivalDelay")),
        _epoch(day, _int(st.get("realtimeArrival"))),
        _int(st.get("departureDelay")),
        _epoch(day, _int(st.get("realtimeDeparture"))),
        0,
    )


def _positions(trip: Any) -> list[dict[str, Any]]:
    """The raw vehicle positions on an alias's pattern."""
    pattern = trip.get("pattern") if isinstance(trip, dict) else None
    positions = pattern.get("vehiclePositions") if isinstance(pattern, dict) else None
    return [vp for vp in positions if isinstance(vp, dict)] if isinstance(positions, list) else []


def _route_positions(route: Any) -> list[dict[str, Any]]:
    """The raw vehicle positions on every pattern of a route alias."""
    patterns = route.get("patterns") if isinstance(route, dict) else None
    return [vp for pattern in patterns if isinstance(pattern, dict) for vp in _positions({"pattern": pattern})] if isinstance(patterns, list) else []


def _rt_pattern(trip: dict[str, Any]) -> bool:
    pattern = trip.get("pattern")
    return isinstance(pattern, dict) and _RT_PATTERN in _str(pattern.get("code"))


def _vehicle(positions: Sequence[dict[str, Any]], key: TripKey, route_id: str) -> realtime.Vehicle | None:
    """The newest of the positions that name this very trip.

    Matched by trip id alone: OTP's VehiclePosition names its trip but not
    the trip's service date, so another day's run of the id cannot be told
    apart here. parse_trip only asks for one when the date's run has
    realtime calls, so a vehicle is attached only to a run that is live
    (and parse_response to one run of the id)."""
    best: realtime.Vehicle | None = None
    for vp in positions:
        if _gid(vp.get("trip")) != key.gtfs_id:
            continue
        lat, lon = _num(vp.get("lat")), _num(vp.get("lon"))
        if lat is None or lon is None:
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
            continue
        rel = vp.get("stopRelationship")
        rel = rel if isinstance(rel, dict) else {}
        ts = _int(vp.get("lastUpdated"))
        if best is not None and (ts is None or (best.timestamp is not None and ts <= best.timestamp)):
            continue
        best = realtime.Vehicle(
            realtime._strip(key.gtfs_id, key.prefix),
            key.date,
            route_id,
            lat,
            lon,
            _num(vp.get("heading")),
            _num(vp.get("speed")),
            ts,
            realtime._strip(_gid(rel.get("stop")), key.prefix),
            _STATUS.get(rel.get("status")),
        )
    return best


def parse_trip(trip: Any, key: TripKey, positions: Sequence[dict[str, Any]] | None = None) -> TripResult:
    """One alias's answer. A trip with no realtime call on the date has no
    update and no vehicle: the pattern's vehicle on this trip id is then
    another day's run of it. Every call CANCELED: the trip is canceled
    (TripDescriptor relationship 3); one CANCELED call among updated ones
    is that stop skipped (StopTimeUpdate relationship 1). positions are
    the vehicles to look for the trip's among, by default its own alias's
    pattern's (parse_response gives those of every alias)."""
    if not isinstance(trip, dict):
        raise ValueError("trip is not an object")
    route_gid = _gid(trip.get("route"))
    route_id = realtime._strip(route_gid, key.prefix)
    more = {"route": route_gid, "rt_pattern": _rt_pattern(trip)}
    calls = trip.get("stoptimesForDate")
    calls = [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []
    calls = calls[: realtime.MAX_STOP_UPDATES]
    ends = _ends(calls)
    if not any(c.get("realtime") is True or c.get("realtimeState") == "CANCELED" for c in calls):
        return TripResult(None, None, ends, **more)
    trip_id = realtime._strip(key.gtfs_id, key.prefix)
    if all(c.get("realtimeState") == "CANCELED" for c in calls):
        tu = realtime.TripUpdate(trip_id, key.date, route_id, 3, None, [])
        return TripResult(tu, None, ends, **more)
    stops = [_stop_update(c, key.prefix) for c in calls]
    tu = realtime.TripUpdate(trip_id, key.date, route_id, 0, None, stops)
    return TripResult(tu, _vehicle(_positions(trip) if positions is None else positions, key, route_id), ends, **more)


def _ends(calls: list[dict[str, Any]]) -> int | None:
    """The latest time of the calls: realtimeArrival and -Departure are the
    scheduled times on a call without realtime data."""
    times = [
        _epoch(_int(c.get("serviceDay")), _int(c.get(f)))
        for c in calls
        for f in ("realtimeArrival", "realtimeDeparture")
    ]
    return max((t for t in times if t is not None), default=None)


def _data(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("answer is not an object")
    if doc.get("errors"):
        first = doc["errors"][0] if isinstance(doc["errors"], list) and doc["errors"] else doc["errors"]
        msg = first.get("message") if isinstance(first, dict) else first
        raise ValueError(f"GraphQL error: {str(msg)[:200]}")
    data = doc.get("data")
    if not isinstance(data, dict):
        raise ValueError("answer without data")
    return data


def parse_response(doc: Any, trips: Sequence[TripKey]) -> dict[TripKey, TripResult | None]:
    """Per trip of build_query(trips): its TripResult, or None when the
    planner does not know the trip (a null alias). Any GraphQL error or a
    missing alias is a ValueError: the whole answer is not trusted.

    A vehicle is looked for on every alias's pattern, not only on its
    trip's own: a trip the realtime data changed has a realtime pattern
    without vehicles (today's run of 741 on 2026-11-18,
    tests/fixtures/realtime/mavplusz_trains.json, whose vehicle came under
    the alias of its scheduled run the day before). Each position names
    its trip, and goes to one live run of it (_one_run). A trip still
    without one there is on its route's scheduled patterns
    (mavplusz_rt_pattern.json): needs_route_vehicles, add_vehicles."""
    data = _data(doc)
    raw: list[tuple[TripKey, Any]] = []
    for i, key in enumerate(trips):
        alias = f"t{i}"
        if alias not in data:
            raise ValueError(f"answer without {alias}")
        raw.append((key, data[alias]))
    positions = [vp for _key, trip in raw for vp in _positions(trip)]
    out: dict[TripKey, TripResult | None] = {}
    for key, trip in raw:
        out[key] = None if trip is None else parse_trip(trip, key, positions)
    _one_run(out)
    return out


def _span(tu: realtime.TripUpdate | None) -> tuple[int, int] | None:
    times = [t for u in (tu.stops if tu else ()) for t in (u.arr_time, u.dep_time) if t is not None]
    return (min(times), max(times)) if times else None


def _one_run(out: dict[TripKey, TripResult | None]) -> None:
    """A trip id asked for two days that are both live (last night's run
    still on its way, today's begun) has its one vehicle on one of them:
    the run whose calls its timestamp falls among (MAX_POSITION_AGE either
    side), else the nearest one, the later day on a tie."""
    by_id: dict[str, list[TripKey]] = {}
    for key, res in out.items():
        if res is not None and res.vehicle is not None:
            by_id.setdefault(key.gtfs_id, []).append(key)
    for keys in by_id.values():
        if len(keys) < 2:
            continue

        def distance(key: TripKey) -> float:
            res = out[key]
            ts, span = res.vehicle.timestamp, _span(res.trip_update)
            if ts is None or span is None:
                return float("inf")
            first, last = span[0] - realtime.MAX_POSITION_AGE, span[1] + realtime.MAX_POSITION_AGE
            return 0.0 if first <= ts <= last else float(min(abs(ts - first), abs(ts - last)))

        best = min(sorted(keys, key=lambda k: k.date, reverse=True), key=distance)
        for key in keys:
            if key != best:
                out[key] = out[key]._replace(vehicle=None)


def to_feed(
    results: Sequence[TripResult | None], timestamp: int | None, *, route_learned: bool = False
) -> realtime.Feed:
    """A realtime.Feed of the trips the planner knows; timestamp is when it
    said so (the hub's wall clock at the request). route_learned marks the
    trip updates as the jbb feeds of a route_start feed have them (their
    route only from a vehicle), so a trip without its vehicle gets no
    current delay there either (realtime._answer, Miskolc)."""
    feed = realtime.Feed(timestamp=timestamp)
    for res in results:
        if res is None:
            continue
        if res.trip_update is not None:
            tu = res.trip_update
            if route_learned:
                tu = dataclasses.replace(tu, route_learned=True)
            feed.add_trip_update(tu)
        if res.vehicle is not None:
            feed.add_vehicle(res.vehicle)
    return feed
