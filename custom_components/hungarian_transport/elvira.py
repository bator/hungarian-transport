"""MÁV ELVIRA timetable for the Hungarian transport Lovelace card.

CORS blocks jegy.mav.hu from the browser, so the card calls
`hungarian_transport.elvira_between` here. StationInfo rows carry `startTrack`
(vágány) and `services` (helyjegy kötelező).

ElviraBoards is the board source: the StationInfo fetch (fetch_station in
Home Assistant, a fake in tests), the shared board cache, the rate limit,
the outage log and the clock. elvira_between plans the boards a window
needs (plan_window, next_boards), asks the source for them and settles the
answer (settle); all three are pure. Nothing here needs Home Assistant.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import logging
import re
import time
import unicodedata
from datetime import UTC, date, datetime, timedelta

import aiohttp
import zoneinfo

from .cache import CallLater, SharedTTLCache, TaskFactory

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
# MÁV timetables, service days and the naive times in StationInfo are Budapest
# wall-clock time, whatever time zone Home Assistant itself runs in.
MAV_TZ = zoneinfo.ZoneInfo("Europe/Budapest")
ELVIRA_CACHE_MAX = 64
# A station whose boards fail is remembered so the warning is not repeated
# every poll; the entry ends when it answers again, or after this long (a
# station nobody asks for any more would otherwise stay for good). Bounded
# like the board cache.
ELVIRA_DOWN_TTL = 6 * 3600
ELVIRA_DOWN_MAX = 64

# StationInfo with travelDate=now drops trains that already left the origin,
# including delayed ones FUTÁR still lists. Midnight keeps the day's rows
# (startTrack included) so Göcsej 967/955 still merge onto the hop.
ARRIVAL_GRACE = timedelta(minutes=2)
# StationInfo is per service day. Shortly after midnight, delayed trains of the
# previous day are still due; near midnight the look-ahead reaches tomorrow.
LATE_TRAIN_WINDOW = timedelta(hours=3)
# The longest a train can take from origin to destination and still be the
# same run. A Hungarian domestic run is under eight hours, delays included;
# the same train number a day later (the next run, or one in the opposite
# direction passing the destination first) is 24 hours on.
MAX_TRAVEL = timedelta(hours=12)
# Kelenföld midnight StationInfo truncates ~11:32 at 200 (moreResult=false).
# 500 reaches ~22:32 so evening origin overlay still has #3880 to copy.
# Far above a day of the busiest station: at 500 ELVIRA cuts Kelenföld's boards
# (about 460 rows a day) at 22:30, and its evening trains went missing.
STATIONINFO_MAX_COUNT = "2000"
ELVIRA_ROW_CAP = 500
# A train leaving by `latest` can arrive this much later: past midnight it is
# on tomorrow's arrival board (StationInfo is per calendar day at the station).
# The earliest a train leaves and still arrives past midnight is 5 h 10 min
# before it (Gyékényes 18:50 to Budapest-Keleti 00:24, MÁV GTFS of 2026-10-30,
# leaving out the IC round trip from Budapest-Keleti back to Nyugati).
ARRIVAL_WINDOW = timedelta(hours=6)
# Tomorrow's arrival board is asked for only when a train leaving by `latest`
# can get in past midnight: when it takes as long as the pair's longest run on
# today's boards, plus this much for a slower or late-running train. Without a
# run to go by, ARRIVAL_WINDOW.
LATE_RUN_MARGIN = timedelta(hours=1)
ELVIRA_TIMEOUT = aiohttp.ClientTimeout(total=30)
# elvira_between answers within this many seconds, whatever ELVIRA does: a
# caller (the card) can time its wait by it. The first round of boards is
# bounded by ELVIRA_TIMEOUT (a board shared with an earlier call is too: its
# request started before this call did); the second round, tomorrow's
# arrival board alone, gets what is left. A board that does not make it
# counts as one that did not arrive, and its request goes on into the cache
# for the next poll.
ELVIRA_CALL_BUDGET = 40
# StationInfo requests (cache misses only; shared requests count once) per
# second and the burst allowed on top. Any logged-in user can call
# elvira_between, and every new station pair is up to four POSTs to
# jegy-a.mav.hu (two stations, up to two service days): a loop over station
# codes must not get Home Assistant's address blocked. A card asks for its
# boards every ELVIRA_CACHE_TTL: six cards on six pairs around midnight are
# 24 boards (0.53/s), all of them at once when a dashboard opens.
ELVIRA_RATE = 1.0
ELVIRA_BURST = 60
# A refused board is logged at most this often, with the count since.
ELVIRA_RATE_LOG_EVERY = 300

# One StationInfo board: its departures and its arrivals.
Board = tuple[list[dict], list[dict]]
# A board's (station code, service day).
BoardKey = tuple[str, date]
# The StationInfo request behind ElviraBoards: a board, or ElviraError.
FetchBoard = Callable[[str, date], Awaitable[Board]]


class ElviraError(Exception):
    """ELVIRA could not be reached or answered with an HTTP error.

    A board fetch raises it instead of returning an empty board, so a result
    that never arrived does not sit in the shared cache for ELVIRA_CACHE_TTL
    seconds. elvira_between raises it only when today's origin board did not
    arrive.
    """


class ElviraRateLimited(ElviraError):
    """Over the integration's own limit; this board was not asked for. An
    ElviraError for the service response, but not an outage: it is logged on
    its own and does not touch the "unavailable / answers again" state."""


class RateLimit:
    """Token bucket in front of ELVIRA, shared by every caller."""

    def __init__(
        self,
        rate: float = ELVIRA_RATE,
        burst: int = ELVIRA_BURST,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate
        self._burst = burst
        self._monotonic = monotonic
        self._tokens = float(burst)
        self._at = monotonic()
        self._refused = 0
        self._logged_at: float | None = None

    def take(self) -> None:
        """One token, or ElviraRateLimited at once: the card polls again in
        ELVIRA_CACHE_TTL seconds anyway."""
        now = self._monotonic()
        self._tokens = min(float(self._burst), self._tokens + max(0.0, now - self._at) * self._rate)
        self._at = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return
        self._refused += 1
        if self._logged_at is None or now - self._logged_at >= ELVIRA_RATE_LOG_EVERY:
            _LOGGER.warning(
                "ELVIRA requests over the integration's rate limit (%.0f/s, burst %d): %d boards refused"
                " since the last such line; cards may miss MÁV rows",
                self._rate,
                self._burst,
                self._refused,
            )
            self._refused = 0
            self._logged_at = now
        raise ElviraRateLimited("rate limited")


class OutageLog:
    """One warning when a station's boards start failing, one line when they
    answer again; every card polls every 45 s, so not one line per poll.

    A failing station is kept for `ttl` at most (then a still failing one
    is warned about again), and for at most `max_entries` stations, the
    oldest dropped first."""

    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        ttl: float = ELVIRA_DOWN_TTL,
        max_entries: int = ELVIRA_DOWN_MAX,
    ) -> None:
        self._monotonic = monotonic
        self._ttl = ttl
        self._max = max_entries
        # Station code -> when its outage began.
        self.down: dict[str, float] = {}

    def report(self, code: str, err: Exception | None) -> None:
        """What the station's boards said this call: err, or None when it answered."""
        now = self._monotonic()
        down = self.down
        for stale in [c for c, since in down.items() if now - since >= self._ttl]:
            del down[stale]
        if err is not None and code not in down:
            while len(down) >= self._max:
                del down[min(down, key=down.get)]
            down[code] = now
            _LOGGER.warning(
                "ELVIRA StationInfo %s unavailable (%s); not logged again until it answers (or for %d h)",
                code,
                err,
                self._ttl // 3600,
            )
        elif err is None and code in down:
            del down[code]
            _LOGGER.info("ELVIRA StationInfo %s answers again", code)


def station_code_digits(value: str) -> str:
    """The digits of a station code (BKK_005501016 -> 005501016); the caller
    checks there are nine, so a stray extra digit is refused, not dropped."""
    return re.sub(r"\D", "", str(value or ""))


def _iso_dt(value, tz):
    """An ELVIRA time as an aware UTC datetime; a naive one is tz wall time.

    UTC, because Python compares and subtracts two datetimes that share a
    tzinfo by their wall clocks: across the autumn change 01:50 CEST to
    03:20 CET would be 90 minutes instead of 150.
    """
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


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
    services = row.get("services")
    for svc in services if isinstance(services, list) else []:
        if not isinstance(svc, dict):
            continue
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


def _end_station(row: dict) -> str:
    end = row.get("endStation")
    return str((end.get("name") if isinstance(end, dict) else "") or "")


def _label_from_row(row: dict) -> str:
    sign = row.get("viszonylatiJel")
    jel = str((sign.get("jel") if isinstance(sign, dict) else "") or "").strip()
    if jel:
        return jel
    name = str(row.get("name") or row.get("fullShortType") or "").strip()
    if name:
        return name
    return str(row.get("code") or "IC")


# ELVIRA's train kinds that come without a short name.
_KIND_LABELS = {"sebesvonat": "S", "szemelyvonat": "Sz", "gyorsvonat": "Gy"}


def _kind_from_row(row: dict) -> str:
    """The train's kind (IC, S, Sz), for a row whose label is only its number."""
    kind = row.get("kind")
    if not isinstance(kind, dict):
        return ""
    short = str(kind.get("sortName") or "").strip()
    return short or _KIND_LABELS.get(_fold_hu(str(kind.get("name") or "")), "")


def service_days(now: datetime, latest: datetime, today: date | None = None) -> list[date]:
    """Service days whose StationInfo can hold a departure in [now, latest].

    The day before now's holds one only for a late train of it, and only
    a day that already runs (today, or before: `today`, when now is a
    planner's later start) has a late train: a day ahead has no estimates,
    and its own board ends at its midnight (StationInfo is per calendar day
    at the station)."""
    days = []
    yesterday = now.date() - timedelta(days=1)
    if (now - LATE_TRAIN_WINDOW).date() < now.date() and (today is None or yesterday <= today):
        days.append(yesterday)
    day = now.date()
    while day <= latest.date():
        days.append(day)
        day += timedelta(days=1)
    return days


async def fetch_station(session: aiohttp.ClientSession, station_code: str, day: date) -> Board:
    """The StationInfo board of one station and service day (the POST
    ElviraBoards makes in Home Assistant, with its session)."""
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
    details = data.get("stationSchedulerDetails") if isinstance(data, dict) else None
    if not isinstance(details, dict):
        # An error page or a changed API, not a day without trains.
        raise ElviraError("unexpected response")
    return _board_rows(details.get("departureScheduler")), _board_rows(details.get("arrivalScheduler"))


def _board_rows(rows) -> list[dict]:
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _dest_matcher(darr, tz):
    """dest_hit(trainId, code, start): this run's (arrival, row) on the
    destination's arrival boards, (None, None) without one."""
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
        # Only the first arrival after this departure, within MAX_TRAVEL,
        # belongs to this run: tomorrow's board lists the same number again,
        # and for a train that passes the destination before the origin
        # (the opposite direction) that is the only arrival after it.
        for cands in (dest_by_id.get(tid) or [], dest_by_code.get(code) or []):
            after = [c for c in cands if start <= c[0] <= start + MAX_TRAVEL]
            if after:
                return min(after, key=lambda c: c[0])
        return None, None

    return dest_hit


def longest_run(odep, darr, tz) -> timedelta | None:
    """The longest origin-to-destination run on these boards, whatever the
    time of day; None when no train of the origin's reaches the destination."""
    dest_hit = _dest_matcher(darr, tz)
    longest = None
    for row in odep or []:
        start = _iso_dt(row.get("actualOrEstimatedStart") or row.get("start"), tz)
        if start is None:
            continue
        arr, _hit = dest_hit(str(row.get("trainId") or ""), str(row.get("code") or "").strip(), start)
        if arr is not None and (longest is None or arr - start > longest):
            longest = arr - start
    return longest


def rows_from_stationinfo(odep, darr, tz, now, latest, dest_name="", later=False):
    """Origin departures with optional dest hit (travel time only).

    `later`: now is a planner's start ahead of the clock (elvira_between's
    `at`). A train leaving before it is then no row even when it arrives
    after it: not one under way, but one the search starts after.

    Booking and startTrack come from the origin StationInfo row. A MÁV card lists
    the rows with a dest hit; other cards overlay all of them onto their FUTÁR or
    MOTIS rows (a failed dest board arrives here empty and must not drop their
    booking flags) and must not append origin-only trains (GEMENC Baja, …). Times
    are compared and subtracted in UTC (see _iso_dt). `head` is dest_name when
    given, else the terminus; `end` is always the terminus.
    """
    now = now.astimezone(UTC)
    latest = latest.astimezone(UTC)
    dest_hit = _dest_matcher(darr, tz)
    out = []
    seen = set()
    for row in odep or []:
        start = _iso_dt(row.get("actualOrEstimatedStart") or row.get("start"), tz)
        if start is None:
            continue
        tid = str(row.get("trainId") or "")
        code = str(row.get("code") or "").strip()
        raw_start = row.get("start")
        run = (tid or code, raw_start if isinstance(raw_start, str) and raw_start else start.isoformat())
        if run in seen:
            continue
        seen.add(run)
        arr, hit = dest_hit(tid, code, start)
        # An arrival that already happened is a stale match; keep the origin
        # row and its booking without travel time.
        if arr is not None and arr < now - ARRIVAL_GRACE:
            hit = None
            arr = None
        if start < now - ARRIVAL_GRACE and (arr is None or later):
            continue
        if start > latest and (arr is None or arr > latest):
            continue
        travel = None
        if arr is not None:
            travel = int((arr - start).total_seconds() / 60)
            if travel < 1:
                travel = None
        flags = parse_elvira_services(row)
        sched = _iso_dt(row.get("start"), tz) or start
        delay = int((start - sched).total_seconds()) if sched is not None else 0
        out.append({
            "tripId": "elvira:" + (tid or code or str(int(sched.timestamp()))),
            "label": _label_from_row(row),
            "railKind": _kind_from_row(row),
            "head": dest_name or _end_station(row),
            "end": _end_station(row),
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


class ElviraBoards:
    """The source of elvira_between's boards, and what lives across calls.

    Every card on every open dashboard polls the service, so a board is
    cached for ELVIRA_CACHE_TTL and concurrent calls for it share one
    request; a miss takes a rate limit token first. The outage log and the
    clock (`now`: wall time, any time zone; `monotonic`: the cache, the rate
    limit, the outage log and the call budget) are the source's too.
    `fetch` is the StationInfo request (fetch_station with a session in
    Home Assistant). `task_factory` and `call_later` are the cache's
    (cache.SharedTTLCache); close() stops its expiry timer for good.
    """

    def __init__(
        self,
        fetch: FetchBoard,
        *,
        task_factory: TaskFactory | None = None,
        call_later: CallLater | None = None,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        burst: int = ELVIRA_BURST,
        call_budget: float = ELVIRA_CALL_BUDGET,
    ) -> None:
        self._fetch = fetch
        self._cache = SharedTTLCache(
            ELVIRA_CACHE_TTL, ELVIRA_CACHE_MAX, task_factory, call_later, monotonic
        )
        self._rate = RateLimit(ELVIRA_RATE, burst, monotonic)
        self.outages = OutageLog(monotonic)
        self.now = now or (lambda: datetime.now(MAV_TZ))
        self.monotonic = monotonic
        self.call_budget = call_budget

    async def board(self, station_code: str, day: date) -> Board:
        """StationInfo for one station and service day, cached and shared."""

        def fetch():
            # The token goes on the miss itself, in the order the boards are
            # asked for (see elvira_between), not when the request task starts.
            self._rate.take()
            return self._fetch(station_code, day)

        return await self._cache.get((station_code, day.isoformat()), fetch)

    def close(self) -> None:
        self._cache.close()


async def _fetch_boards(source: ElviraBoards, wanted: list[BoardKey]) -> list:
    """The boards for these (station, day) pairs, an ElviraError in place of
    each one that did not arrive."""
    results = await asyncio.gather(
        *(source.board(code, day) for code, day in wanted),
        return_exceptions=True,
    )
    for board in results:
        # Only an unreachable board is an empty one; a bug or a cancellation is not.
        if isinstance(board, BaseException) and not isinstance(board, ElviraError):
            raise board
    return results


@dataclass(frozen=True)
class Window:
    """The departures a call asks for, from `now` to `latest` (Budapest time)."""

    now: datetime
    latest: datetime
    # now is a planner's start ahead of the clock (rows_from_stationinfo).
    later: bool
    # The origin's service days (service_days).
    days: list[date]

    @property
    def tomorrow(self) -> date:
        """The day after latest's: its arrival board holds only the trains
        that get in past midnight."""
        return self.latest.date() + timedelta(days=1)


def plan_window(clock: datetime, minutes_after: int = 180, at: float | None = None) -> Window:
    """The window of a call at `clock`. `at` (epoch seconds) starts it later,
    for a planner's timed search; never before the clock, since a train that
    left is gone."""
    tz = MAV_TZ
    now = clock = clock.astimezone(tz)
    if at is not None:
        now = max(now, datetime.fromtimestamp(at, tz))
    # Real minutes ahead, not wall-clock ones, on the night the clocks change.
    latest = (now.astimezone(UTC) + timedelta(minutes=int(minutes_after or 180))).astimezone(tz)
    return Window(now, latest, now > clock, service_days(now, latest, clock.date()))


def next_boards(
    win: Window, origin: str, dest: str, boards: Mapping[BoardKey, Board | ElviraError]
) -> list[BoardKey]:
    """The boards still to ask for, in the order to ask (the rate limit's
    tokens go in this order); [] once the answer can be settled. `boards`:
    the ones asked for so far, an ElviraError for each that did not arrive.

    The first round is every service day's board of both stations, today's
    origin board first: the call fails without it, so with the rate limit's
    last token left it is the board that gets it. Tomorrow's arrival board
    last, the board the rate limit refuses first.
    """
    today = win.now.date()
    tomorrow = win.tomorrow
    # Tomorrow's arrival board only holds trains that get in past midnight;
    # it is several MB every ELVIRA_CACHE_TTL. Even the shortest run gets a
    # train leaving by `latest` in past midnight when LATE_RUN_MARGIN does:
    # that board comes with the others. Otherwise today's boards say how long
    # this pair's trains take, and a second round asks for it if needed.
    dest_days = list(win.days)
    if (win.latest + LATE_RUN_MARGIN).date() >= tomorrow:
        dest_days.append(tomorrow)
    first = [(origin, today)] + [
        (code, day)
        for code, code_days in ((origin, win.days), (dest, dest_days))
        for day in code_days
        if (code, day) != (origin, today)
    ]
    missing = [key for key in first if key not in boards]
    if missing:
        return missing
    if (
        (dest, tomorrow) in boards
        or (win.latest + ARRIVAL_WINDOW).date() < tomorrow
        or isinstance(boards[(origin, today)], ElviraError)
    ):
        return []
    arrived = {key: board for key, board in boards.items() if not isinstance(board, ElviraError)}
    odep = [row for day in win.days for row in arrived.get((origin, day), ([], []))[0]]
    darr = [row for day in win.days for row in arrived.get((dest, day), ([], []))[1]]
    run = longest_run(odep, darr, MAV_TZ)
    reach = ARRIVAL_WINDOW if run is None else min(ARRIVAL_WINDOW, run + LATE_RUN_MARGIN)
    return [(dest, tomorrow)] if (win.latest + reach).date() >= tomorrow else []


def _dest_board_needed(day: date, now: datetime, origin: list, days: list[date], tz) -> bool:
    """Whether the destination's board of this day can hold the arrival of
    a row. One before today's only can for a train of an origin board of
    before today still to leave (a late one): a train of today's origin
    board arrives today or later, and one that already left is no row."""
    if day >= now.date():
        return True
    return any(
        start is not None and start >= now - ARRIVAL_GRACE
        for board, origin_day in zip(origin, days)
        if origin_day <= day and not isinstance(board, ElviraError)
        for row in board[0]
        for start in (_iso_dt(row.get("actualOrEstimatedStart") or row.get("start"), tz),)
    )


@dataclass(frozen=True)
class Answer:
    """What a call's boards add up to (settle)."""

    rows: list[dict]
    # The ElviraError of a destination board the window needed and did not
    # get (an outage before a refusal); None when every one arrived.
    dest_failure: ElviraError | None
    # Today's origin board did not arrive: the call fails with it, no rows.
    origin_failure: ElviraError | None
    # Per station heard from: its outage, or None when it answered
    # (OutageLog.report). A station whose boards were all refused is not here.
    stations: list[tuple[str, ElviraError | None]]
    # Boards of a station that answered which did not arrive anyway
    # (yesterday's, tomorrow's): only a few late trains are missing.
    missed: list[tuple[str, date, ElviraError]]


def settle(
    win: Window,
    origin: str,
    dest: str,
    boards: Mapping[BoardKey, Board | ElviraError],
    dest_name: str = "",
) -> Answer:
    """The rows come from the origin boards only: none, and origin_failure,
    when today's did not arrive, since without it every train of the day
    would be missing. A failed destination board, or a failed board of
    yesterday or tomorrow, counts as empty. The rows then lack a travel time
    or a few late trains, instead of every platform and booking flag.

    Yesterday's destination board is needed only while yesterday's origin
    board has a train still to leave (_dest_board_needed). Without a needed
    one the rows' destHit cannot be trusted, and a MÁV card lists only the
    destHit rows: dest_failure.
    """
    now = win.now
    days = win.days
    dest_days = [day for day in [*days, win.tomorrow] if (dest, day) in boards]
    origin_boards = [boards[(origin, day)] for day in days]
    dest_boards = [boards[(dest, day)] for day in dest_days]
    stations: list[tuple[str, ElviraError | None]] = []
    missed: list[tuple[str, date, ElviraError]] = []
    for code, part, part_days in ((origin, origin_boards, days), (dest, dest_boards, dest_days)):
        errors = [b for b in part if isinstance(b, ElviraError)]
        outages = [b for b in errors if not isinstance(b, ElviraRateLimited)]
        today_board = part[part_days.index(now.date())]
        if isinstance(today_board, ElviraError) and not isinstance(today_board, ElviraRateLimited):
            # Today's board is the one the rows need.
            outage = today_board
        elif len(errors) < len(part):
            # A board arrived: the station answers, even when yesterday's or
            # tomorrow's did not (only a few late trains are missing then).
            outage = None
            missed += [
                (code, day, board)
                for board, day in zip(part, part_days)
                if isinstance(board, ElviraError) and not isinstance(board, ElviraRateLimited)
            ]
        elif outages:
            outage = outages[0]
        else:
            # Every board refused here, none asked: nothing heard from the
            # station either way.
            continue
        stations.append((code, outage))
    today = origin_boards[days.index(now.date())]
    dest_failed = [
        board
        for board, day in zip(dest_boards, dest_days)
        if isinstance(board, ElviraError) and _dest_board_needed(day, now, origin_boards, days, MAV_TZ)
    ]
    dest_failure = next(
        (b for b in dest_failed if not isinstance(b, ElviraRateLimited)),
        dest_failed[0] if dest_failed else None,
    )
    if isinstance(today, ElviraError):
        return Answer([], dest_failure, today, stations, missed)
    odep = [row for board in origin_boards if not isinstance(board, ElviraError) for row in board[0]]
    darr = [row for board in dest_boards if not isinstance(board, ElviraError) for row in board[1]]
    rows = rows_from_stationinfo(odep, darr, MAV_TZ, now, win.latest, dest_name, win.later)
    return Answer(rows, dest_failure, None, stations, missed)


async def elvira_between(
    source: ElviraBoards, origin_code, dest_code, minutes_after=180, dest_name="", at=None
):
    """Origin departures towards dest: a MÁV card's rows, an overlay for others.

    Returns (rows, dest_failure) (settle); ElviraError when today's origin
    board did not arrive. `at` (epoch seconds) starts the window later
    (plan_window): a later start gets the trains leaving from it on
    (ARRIVAL_GRACE before it, as for now), not the ones leaving before it
    that arrive after it.

    Answers within source.call_budget seconds (ELVIRA_CALL_BUDGET).
    """
    origin_code = station_code_digits(origin_code)
    dest_code = station_code_digits(dest_code)
    if len(origin_code) < 9 or len(dest_code) < 9 or origin_code == dest_code:
        return [], None
    deadline = source.monotonic() + source.call_budget
    win = plan_window(source.now(), minutes_after, at)
    boards: dict[BoardKey, Board | ElviraError] = {}
    wanted = next_boards(win, origin_code, dest_code, boards)
    boards.update(zip(wanted, await _fetch_boards(source, wanted)))
    # A second round, tomorrow's arrival board alone, gets what is left of
    # the budget.
    wanted = next_boards(win, origin_code, dest_code, boards)
    if wanted:
        try:
            async with asyncio.timeout(deadline - source.monotonic()):
                boards.update(zip(wanted, await _fetch_boards(source, wanted)))
        except TimeoutError:
            # Its request goes on (the cache shields it); this answer
            # does without it, and says so in dest_error.
            boards.update((key, ElviraError("TimeoutError")) for key in wanted)
    answer = settle(win, origin_code, dest_code, boards, dest_name)
    for code, outage in answer.stations:
        for day, err in ((day, err) for missed, day, err in answer.missed if missed == code):
            _LOGGER.debug("ELVIRA StationInfo %s for %s unavailable (%s)", code, day, err)
        source.outages.report(code, outage)
    if answer.origin_failure is not None:
        # A rate limit stays one for the service response.
        raise type(answer.origin_failure)(str(answer.origin_failure))
    return answer.rows, answer.dest_failure
