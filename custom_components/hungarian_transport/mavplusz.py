"""Delays and vehicle positions from mavplusz.hu, MÁV's journey planner.

mavplusz.hu runs OpenTripPlanner 2 with the Volán, Paks and city (Debrecen,
Miskolc, Veszprém) timetables and their realtime updates; jbb.ghsq.de's
GTFS-Realtime feeds for those operators are exported from this planner, so
its ids are theirs with the prefix kept: jbb trip hkir_2036_1-3 of the hkir:
feed is OTP trip "hkir:hkir_2036_1-3", stop hkir_779365_6 is
"hkir:hkir_779365_6".

For the sources that opt in (realtime_feeds.json "mavplusz": true) the hub
asks the planner first, in one GraphQL request for just the trips a card
shows, and reads the jbb feeds only when it fails (see docs/realtime.md).
This module builds that request and turns the answer into a realtime.Feed
keyed as realtime.decode_feed keys a jbb feed, so realtime's matching reads
both alike. No Home Assistant imports: tests/test_mavplusz.py runs it bare.
"""
from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from datetime import datetime
import json
import re
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

import aiohttp

from . import realtime

URL = "https://mavplusz.hu/otp2-backend/otp/routers/default/index/graphql"
# Trips in one request. The service takes at most realtime.MAX_TRIPS (40)
# trips a call, so one request always covers a call; a longer list (the hub
# used directly) is cut here, never split into more requests.
MAX_TRIPS_PER_REQUEST = 40
# Decompressed answer cap: 40 long trips are ~250 KB.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
BUDAPEST = ZoneInfo("Europe/Budapest")
_DATE = re.compile(r"^\d{8}$")
# stopRelationship.status -> VehiclePosition.VehicleStopStatus.
_STATUS = {"INCOMING_AT": 0, "STOPPED_AT": 1, "IN_TRANSIT_TO": 2}
# Per trip: its route, its calls on the service date with their realtime
# times (seconds after serviceDay, an epoch), and the positions of its
# pattern's vehicles (each names its own trip). No vehicle ids: they are
# plate numbers, and nothing here needs them.
_TRIP_FIELDS = (
    "gtfsId route { gtfsId } "
    "stoptimesForDate(serviceDate: $%s) { realtime realtimeState realtimeArrival realtimeDeparture "
    "arrivalDelay departureDelay serviceDay stop { gtfsId } } "
    "pattern { vehiclePositions { lat lon heading speed lastUpdated "
    "stopRelationship { status stop { gtfsId } } trip { gtfsId } } }"
)


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
    no realtime data on that date."""

    trip_update: realtime.TripUpdate | None
    vehicle: realtime.Vehicle | None


def gtfs_id(trip_id: str, prefix: str) -> str:
    """The planner's id of a card's trip id, with or without the prefix."""
    return prefix + realtime._strip(trip_id, prefix)


def service_date(wanted: Any, now: float) -> str:
    """The card's date when it is YYYYMMDD, else today in Budapest."""
    if isinstance(wanted, str) and _DATE.match(wanted):
        return wanted
    return datetime.fromtimestamp(now, BUDAPEST).strftime("%Y%m%d")


def build_query(trips: Sequence[TripKey]) -> dict[str, Any]:
    """The aliased GraphQL request body for trips: alias tN is trips[N]. The
    ids and dates go in as variables, never into the query text."""
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
    HTTP error, an oversized or a non-JSON answer. user_agent and timeout
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
            raise ValueError(f"{length} bytes, over {cap}")
        chunks = []
        total = 0
        async for chunk in resp.content.iter_chunked(65536):
            total += len(chunk)
            if total > cap:
                raise ValueError(f"over {cap} bytes")
            chunks.append(chunk)
    return json.loads(b"".join(chunks))


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


def _vehicle(trip: dict[str, Any], key: TripKey, route_id: str) -> realtime.Vehicle | None:
    """The newest position of the pattern's vehicle on this very trip."""
    pattern = trip.get("pattern")
    positions = pattern.get("vehiclePositions") if isinstance(pattern, dict) else None
    best: realtime.Vehicle | None = None
    for vp in positions if isinstance(positions, list) else ():
        if not isinstance(vp, dict) or _gid(vp.get("trip")) != key.gtfs_id:
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
            None,
            _STATUS.get(rel.get("status")),
        )
    return best


def parse_trip(trip: Any, key: TripKey) -> TripResult:
    """One alias's answer. A trip with no realtime call on the date has no
    update and no vehicle: the pattern's vehicle on this trip id is then
    another day's run of it. Every call CANCELED: the trip is canceled
    (TripDescriptor relationship 3); one CANCELED call among updated ones
    is that stop skipped (StopTimeUpdate relationship 1)."""
    if not isinstance(trip, dict):
        raise ValueError("trip is not an object")
    route_id = realtime._strip(_gid(trip.get("route")), key.prefix)
    calls = trip.get("stoptimesForDate")
    calls = [c for c in calls if isinstance(c, dict)] if isinstance(calls, list) else []
    calls = calls[: realtime.MAX_STOP_UPDATES]
    if not any(c.get("realtime") is True or c.get("realtimeState") == "CANCELED" for c in calls):
        return TripResult(None, None)
    trip_id = realtime._strip(key.gtfs_id, key.prefix)
    if all(c.get("realtimeState") == "CANCELED" for c in calls):
        tu = realtime.TripUpdate(trip_id, key.date, "", route_id, 3, None, [])
        return TripResult(tu, None)
    stops = [_stop_update(c, key.prefix) for c in calls]
    tu = realtime.TripUpdate(trip_id, key.date, "", route_id, 0, None, stops)
    return TripResult(tu, _vehicle(trip, key, route_id))


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
    missing alias is a ValueError: the whole answer is not trusted."""
    data = _data(doc)
    out: dict[TripKey, TripResult | None] = {}
    for i, key in enumerate(trips):
        alias = f"t{i}"
        if alias not in data:
            raise ValueError(f"answer without {alias}")
        trip = data[alias]
        out[key] = None if trip is None else parse_trip(trip, key)
    return out


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
            feed.trip_updates[tu.trip_id] = tu
        if res.vehicle is not None:
            feed.vehicles[res.vehicle.trip_id] = res.vehicle
    return feed
