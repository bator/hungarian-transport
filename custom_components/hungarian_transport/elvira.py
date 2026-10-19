"""MÁV ELVIRA timetable for the Hungarian transport Lovelace card.

CORS blocks jegy.mav.hu from the browser, so the card calls
`hungarian_transport.elvira_between` here. StationInfo rows carry `startTrack`
(vágány) and `services` (helyjegy kötelező).
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

import aiohttp
import zoneinfo

from .cache import SharedTTLCache

_LOGGER = logging.getLogger(__name__)

ELVIRA_URL = "https://jegy-a.mav.hu/IK_API_PROD/api/InformationApi/GetTimetable"
ELVIRA_CACHE_TTL = 45
ELVIRA_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, text/plain, */*",
    "language": "hu",
    "Origin": "https://jegy.mav.hu",
    "Referer": "https://jegy.mav.hu/",
}
DOMAIN = "hungarian_transport"
# MÁV timetables, service days and the naive times in StationInfo are Budapest
# wall-clock time, whatever time zone Home Assistant itself runs in.
MAV_TZ = zoneinfo.ZoneInfo("Europe/Budapest")
ELVIRA_CACHE_MAX = 64

# StationInfo with travelDate=now drops trains that already left the origin,
# including delayed ones FUTÁR still lists. Midnight keeps the day's rows
# (startTrack included) so Göcsej 967/955 still merge onto the hop.
ARRIVAL_GRACE = timedelta(minutes=2)
# StationInfo is per service day. Shortly after midnight, delayed trains of the
# previous day are still due; near midnight the look-ahead reaches tomorrow.
LATE_TRAIN_WINDOW = timedelta(hours=3)
# Kelenföld midnight StationInfo truncates ~11:32 at 200 (moreResult=false).
# 500 reaches ~22:32 so evening origin overlay still has #3880 to copy.
STATIONINFO_MAX_COUNT = "500"
ELVIRA_ROW_CAP = 500
ELVIRA_TIMEOUT = aiohttp.ClientTimeout(total=30)


class ElviraError(Exception):
    """ELVIRA could not be reached or answered with an HTTP error.

    A board fetch raises it instead of returning an empty board, so a result
    that never arrived does not sit in the shared cache for ELVIRA_CACHE_TTL
    seconds. elvira_between raises it only when no origin board arrived.
    """


def station_code_digits(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) >= 9:
        return digits[-9:]
    return digits


def _iso_dt(value, tz):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _fold_hu(text: str) -> str:
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return norm.encode("ascii", "ignore").decode("ascii").lower()


# MÁV IK: 3880 = helyjegy kötelező (Å). 94345 = helyjegy váltható, not required.
_BOOKING_REQUIRED_RE = re.compile(
    r"(?:SZOLGALTATAS[#:]|#|\b)3880\b",
    re.IGNORECASE,
)
# 8163379 = Országbérlettel igénybe vehető.
_NATIONAL_PASS_RE = re.compile(r"(?:SZOLGALTATAS[#:]|#|\b)8163379\b", re.IGNORECASE)
# 8586013 = Belföldi utazásra a kijelölt viszonylaton helyjegy váltása nélkül
# igénybe vehető 2. kocsiosztályon (part of an IC run is open without one).
_RESERVATION_FREE_RE = re.compile(r"(?:SZOLGALTATAS[#:]|#|\b)8586013\b", re.IGNORECASE)


def parse_elvira_services(row: dict) -> dict:
    flags = {
        "booking": False,
        "bikesAllowed": False,
        "wheelchair": False,
        "nationalPass": False,
        "reservationFreeSection": False,
    }
    for svc in row.get("services") or []:
        svc = svc or {}
        blob = " ".join(
            str(svc.get(key) or "")
            for key in ("description", "name", "shortName", "code", "key", "id")
        )
        low = _fold_hu(blob)
        required = _BOOKING_REQUIRED_RE.search(blob) or (
            "helyjegy" in low and "kotelezo" in low
        )
        if required:
            flags["booking"] = True
        # "Kerékpár nem szállítható" is a service too; it must not light the bike icon.
        if "kerekpar" in low and "nem szallithato" not in low:
            flags["bikesAllowed"] = True
        if "kerekesszek" in low:
            flags["wheelchair"] = True
        if _NATIONAL_PASS_RE.search(blob) or (
            "orszagberlet" in low and "igenybe veheto" in low and "nem veheto" not in low
        ):
            flags["nationalPass"] = True
        if _RESERVATION_FREE_RE.search(blob) or (
            "helyjegy valtasa nelkul" in low and "igenybe veheto" in low
        ):
            flags["reservationFreeSection"] = True
    return flags


def track_from_elvira_row(row: dict) -> str:
    for key in ("startTrack", "endTrack"):
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        text = str(raw).strip()
        if text and text.lower() != "none":
            return text
    return ""


def _label_from_row(row: dict) -> str:
    jel = ((row.get("viszonylatiJel") or {}).get("jel") or "").strip()
    if jel:
        return jel
    name = str(row.get("name") or row.get("fullShortType") or "").strip()
    if name:
        return name
    return str(row.get("code") or "IC")


def service_days(now: datetime, latest: datetime) -> list[date]:
    """Service days whose StationInfo can hold a departure in [now, latest]."""
    days = []
    if (now - LATE_TRAIN_WINDOW).date() < now.date():
        days.append(now.date() - timedelta(days=1))
    day = now.date()
    while day <= latest.date():
        days.append(day)
        day += timedelta(days=1)
    return days


async def _fetch_station(session, station_code: str, day: date) -> tuple[list, list]:
    payload = {
        "type": "StationInfo",
        "travelDate": f"{day.isoformat()}T00:00:00",
        "stationNumberCode": station_code,
        "minCount": "0",
        "maxCount": STATIONINFO_MAX_COUNT,
    }
    try:
        async with session.post(
            ELVIRA_URL,
            json=payload,
            headers=ELVIRA_HEADERS,
            timeout=ELVIRA_TIMEOUT,
        ) as resp:
            if resp.status // 100 != 2:
                raise ElviraError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        raise ElviraError(type(err).__name__) from None
    if not isinstance(data, dict):
        raise ElviraError("unexpected response")
    details = data.get("stationSchedulerDetails")
    if not isinstance(details, dict):
        details = {}
    return (
        list(details.get("departureScheduler") or []),
        list(details.get("arrivalScheduler") or []),
    )


async def _station_legs(session, hass, station_code: str, day: date):
    """StationInfo for one station and service day, cached and shared.

    Every card on every open dashboard polls this service, so concurrent calls
    for the same board share one request.
    """
    data = hass.data.setdefault(DOMAIN, {})
    cache = data.get("elvira_cache")
    if cache is None:
        cache = data["elvira_cache"] = SharedTTLCache(
            ELVIRA_CACHE_TTL,
            ELVIRA_CACHE_MAX,
            task_factory=lambda coro: hass.async_create_background_task(
                coro, "hungarian_transport_elvira", eager_start=False
            ),
        )
    return await cache.get(
        (station_code, day.isoformat()),
        lambda: _fetch_station(session, station_code, day),
    )


def rows_from_stationinfo(odep, darr, tz, now, latest, dest_name=""):
    """Origin departures with optional dest hit (travel time only).

    Booking and startTrack come from the origin StationInfo row. A missing dest
    board (Kelenföld truncates ~11:36, or the board failed and arrives here
    empty) must not drop evening ICs. The card overlays these onto FUTÁR/MOTIS
    hop rows and must not append unmatched origin-only trains (GEMENC Baja, …).
    """
    dest_by_id: dict[str, list] = {}
    dest_by_code: dict[str, list] = {}
    for row in darr or []:
        arr = _iso_dt(
            row.get("actualOrEstimatedArrive") or row.get("arrive") or row.get("start"),
            tz,
        )
        if arr is None:
            continue
        tid = str(row.get("trainId") or "")
        code = str(row.get("code") or "").strip()
        if tid:
            dest_by_id.setdefault(tid, []).append((arr, row))
        if code:
            dest_by_code.setdefault(code, []).append((arr, row))

    def dest_hit(tid: str, code: str, start: datetime):
        # Train numbers repeat every day, and a truncated dest board
        # (Kelenföld ~11:36) can still list a morning run of the same `code`.
        # Only the first arrival after this departure belongs to this run.
        for cands in (dest_by_id.get(tid) or [], dest_by_code.get(code) or []):
            after = [c for c in cands if c[0] >= start]
            if after:
                return min(after, key=lambda c: c[0])
        return None, None

    out = []
    seen = set()
    for row in odep or []:
        start = _iso_dt(row.get("actualOrEstimatedStart") or row.get("start"), tz)
        if start is None:
            continue
        tid = str(row.get("trainId") or "")
        code = str(row.get("code") or "").strip()
        run = (tid or code, row.get("start") or start.isoformat())
        if run in seen:
            continue
        seen.add(run)
        arr, hit = dest_hit(tid, code, start)
        # An arrival that already happened is a stale match; keep the origin
        # row and its booking without travel time.
        if arr is not None and arr < now - ARRIVAL_GRACE:
            hit = None
            arr = None
        if arr is None and start < now - ARRIVAL_GRACE:
            continue
        if start > latest and (arr is None or arr > latest):
            continue
        travel = None
        if arr is not None:
            travel = int((arr - start).total_seconds() / 60)
            if travel < 1 or travel > 1440:
                travel = None
        flags = parse_elvira_services(row)
        sched = _iso_dt(row.get("start"), tz) or start
        delay = int((start - sched).total_seconds()) if sched is not None else 0
        out.append({
            "tripId": "elvira:" + (tid or code or str(int(sched.timestamp()))),
            "label": _label_from_row(row),
            "head": dest_name or ((row.get("endStation") or {}).get("name") or ""),
            "dep": int(start.timestamp()),
            "sched": int(sched.timestamp()),
            "platform": track_from_elvira_row(row),
            "vehicle": "rail",
            "rawType": "RAIL",
            "trainNumber": code,
            "travel": travel,
            "destHit": bool(hit),
            "booking": bool(flags.get("booking")),
            "bikesAllowed": bool(flags.get("bikesAllowed")),
            "wheelchair": bool(flags.get("wheelchair")),
            "nationalPass": bool(flags.get("nationalPass")),
            "reservationFreeSection": bool(flags.get("reservationFreeSection")),
            "color": "#4477aa",
            "text": "#ffffff",
            "delay": delay,
        })
    out.sort(key=lambda item: item.get("dep") or 0)
    return out[:ELVIRA_ROW_CAP]


def _log_outage(hass, code: str, err: Exception | None) -> None:
    """One warning when a station's boards start failing, one line when they
    answer again; every card polls every 45 s, so not one line per poll."""
    down: set[str] = hass.data.setdefault(DOMAIN, {}).setdefault("elvira_down", set())
    if err is not None and code not in down:
        down.add(code)
        _LOGGER.warning(
            "ELVIRA StationInfo %s unavailable (%s); not logged again until it answers",
            code,
            err,
        )
    elif err is None and code in down:
        down.discard(code)
        _LOGGER.info("ELVIRA StationInfo %s answers again", code)


async def elvira_between(session, hass, origin_code, dest_code, minutes_after=180, dest_name=""):
    """Origin departures towards dest, for the card to overlay on its rows.

    Every station and service day is its own StationInfo board, and the rows
    come from the origin boards only: ElviraError when none of them arrived.
    A failed destination board, or a failed board of yesterday or tomorrow
    next to one that arrived, counts as empty. The rows then lack a travel
    time or a few late trains, instead of every platform and booking flag.
    """
    origin_code = station_code_digits(origin_code)
    dest_code = station_code_digits(dest_code)
    if len(origin_code) < 9 or len(dest_code) < 9 or origin_code == dest_code:
        return []
    tz = MAV_TZ
    now = datetime.now(tz)
    latest = now + timedelta(minutes=int(minutes_after or 180))
    days = service_days(now, latest)
    boards = await asyncio.gather(
        *(_station_legs(session, hass, code, day) for code in (origin_code, dest_code) for day in days),
        return_exceptions=True,
    )
    for board in boards:
        # Only an unreachable board is an empty one; a bug or a cancellation is not.
        if isinstance(board, BaseException) and not isinstance(board, ElviraError):
            raise board
    origin, dest = boards[: len(days)], boards[len(days):]
    for code, part in ((origin_code, origin), (dest_code, dest)):
        _log_outage(hass, code, next((b for b in part if isinstance(b, ElviraError)), None))
    arrived = [board for board in origin if not isinstance(board, ElviraError)]
    if not arrived:
        raise ElviraError(str(origin[0]))
    odep = [row for dep, _arr in arrived for row in dep]
    darr = [row for board in dest if not isinstance(board, ElviraError) for row in board[1]]
    return rows_from_stationinfo(odep, darr, tz, now, latest, dest_name)
