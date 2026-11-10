"""ELVIRA boards, planning and rows, without Home Assistant.

ElviraBoards is driven with a fake StationInfo request and a fixed clock;
the planner (plan_window, next_boards) and the rows (rows_from_stationinfo)
are pure. tests/test_init.py keeps the service's wiring end to end.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import json
import logging

import aiohttp
import pytest
import zoneinfo

from custom_components.hungarian_transport import elvira
from custom_components.hungarian_transport.elvira import (
    ELVIRA_CACHE_TTL,
    ELVIRA_DOWN_MAX,
    ELVIRA_DOWN_TTL,
    ELVIRA_ROW_CAP,
    ELVIRA_URL,
    STATIONINFO_MAX_COUNT,
    ElviraBoards,
    ElviraError,
    ElviraRateLimited,
    OutageLog,
    elvira_between,
    fetch_station,
    longest_run,
    next_boards,
    parse_elvira_services,
    plan_window,
    rows_from_stationinfo,
    service_days,
)

TZ = zoneinfo.ZoneInfo("Europe/Budapest")
ORIGIN = "005510009"
DEST = "005501016"
# Midday: one service day, so every call asks for exactly two boards.
NOON = "2026-10-19T12:00:00+02:00"
FMT = "%Y-%m-%dT%H:%M:%S"


class Clock:
    """Wall time and the monotonic clock, moved together."""

    def __init__(self, when: str) -> None:
        self.wall = datetime.fromisoformat(when)
        self.mono = 1000.0

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.mono

    def tick(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


class Upstream:
    """The StationInfo request behind ElviraBoards: the origin's
    `departures` and the destination's `arrivals` of the board's day, and
    HTTP 503 for the (station, day) boards in `failing` (a bare station code
    fails all its days). A board in `hold` waits for its event."""

    def __init__(self, departures=(), arrivals=(), failing=()) -> None:
        self.departures = list(departures)
        self.arrivals = list(arrivals)
        self.failing = set(failing)
        self.calls: list[tuple[str, str]] = []
        self.hold: dict[tuple[str, str], asyncio.Event] = {}

    def answer(self, code: str, day: date):
        iso = day.isoformat()
        if code in self.failing or (code, iso) in self.failing:
            return ElviraError("HTTP 503")
        deps = [dep for dep in self.departures if code == ORIGIN and dep["start"][:10] == iso]
        arrs = [arr for arr in self.arrivals if code == DEST and arr["arrive"][:10] == iso]
        return deps, arrs

    async def __call__(self, code: str, day: date):
        self.calls.append((code, day.isoformat()))
        gate = self.hold.get((code, day.isoformat()))
        if gate is not None:
            await gate.wait()
        board = self.answer(code, day)
        if isinstance(board, ElviraError):
            raise board
        return board


def _source(upstream: Upstream, clock: Clock, **kwargs) -> ElviraBoards:
    return ElviraBoards(upstream, now=clock.now, monotonic=clock.monotonic, **kwargs)


async def _between(source, minutes=15, origin=ORIGIN, dest=DEST, dest_name="", at=None):
    return await elvira_between(source, origin, dest, minutes, dest_name, at)


def _board(start, arrive=None, code="966"):
    dep = {
        "trainId": "t1",
        "code": code,
        "name": "GÖCSEJ",
        "start": start.strftime(FMT),
        "startTrack": "6",
        "services": [{"code": "3880"}],
    }
    arr = {"trainId": "t1", "code": code, "arrive": arrive.strftime(FMT)} if arrive else None
    return dep, arr


def _rounds(when, minutes, departures=(), arrivals=(), failing=(), at=None) -> list[list]:
    """The rounds next_boards asks for, each answered from these boards."""
    win = plan_window(datetime.fromisoformat(when), minutes, at)
    upstream = Upstream(departures, arrivals, failing)
    boards: dict = {}
    rounds = []
    while wanted := next_boards(win, ORIGIN, DEST, boards):
        assert len(rounds) < 3, rounds
        rounds.append([(code, day.isoformat()) for code, day in wanted])
        boards.update((key, upstream.answer(*key)) for key in wanted)
    return rounds


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == elvira.__name__ and r.levelno == logging.WARNING
    ]


async def _settle_tasks() -> None:
    """Let the cache's request tasks (a held board's too) finish."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending, return_exceptions=True)


# --- Board planning (pure) ---


def test_midday_asks_both_stations_today_with_whole_boards() -> None:
    assert _rounds(NOON, 15) == [[(ORIGIN, "2026-10-19"), (DEST, "2026-10-19")]]
    # 500 cuts Kelenföld's boards (about 460 rows a day) at 22:30.
    assert STATIONINFO_MAX_COUNT == "2000"
    assert ELVIRA_ROW_CAP == 500 and ELVIRA_ROW_CAP <= int(STATIONINFO_MAX_COUNT)


def test_evening_asks_tomorrows_arrivals_with_the_others_and_last() -> None:
    """A train leaving by 23:00 arrives past midnight however short its run;
    tomorrow's arrival board is the one the rate limit refuses first."""
    assert _rounds("2026-11-06T22:30:00+01:00", 30) == [
        [(ORIGIN, "2026-11-06"), (DEST, "2026-11-06"), (DEST, "2026-11-07")]
    ]


def test_after_midnight_todays_origin_board_comes_first() -> None:
    """With the rate limit's last token left it goes to the board the call
    cannot do without."""
    assert _rounds("2026-10-19T00:30:00+02:00", 15) == [
        [(ORIGIN, "2026-10-19"), (ORIGIN, "2026-10-18"), (DEST, "2026-10-18"), (DEST, "2026-10-19")]
    ]


@pytest.mark.parametrize(
    ("moment", "minutes", "run", "tomorrow", "rounds"),
    [
        # Today's 62-minute run says a train leaving by 18:00 is in by 19:02.
        ("2026-10-19T15:00:00+02:00", 180, True, False, 1),
        # Leaving by 23:00 it can arrive past midnight, however short the
        # run: asked for with the other boards, not after them.
        ("2026-10-19T22:30:00+02:00", 30, True, True, 1),
        # No run on today's boards to go by: ARRIVAL_WINDOW reaches midnight,
        # which only today's boards could have ruled out.
        ("2026-10-19T15:00:00+02:00", 180, False, True, 2),
    ],
)
def test_tomorrows_arrivals_only_when_a_train_can_arrive_by_then(
    moment, minutes, run, tomorrow, rounds
) -> None:
    """Tomorrow's destination board is several MB every ELVIRA_CACHE_TTL; it
    holds only the trains arriving past midnight."""
    start = datetime.fromisoformat(moment).replace(hour=12)
    dep, arr = _board(start, start + timedelta(minutes=62))
    asked = _rounds(moment, minutes, [dep], [arr] if run else [])
    expected = {(ORIGIN, "2026-10-19"), (DEST, "2026-10-19")}
    if tomorrow:
        expected.add((DEST, "2026-10-20"))
    assert {key for keys in asked for key in keys} == expected
    # Each round is one more ELVIRA round trip on a cold call.
    assert len(asked) == rounds


def test_no_second_round_without_todays_origin_board() -> None:
    """The call fails without it: no several-MB board for nothing."""
    assert _rounds("2026-10-19T15:00:00+02:00", 180, failing={(ORIGIN, "2026-10-19")}) == [
        [(ORIGIN, "2026-10-19"), (DEST, "2026-10-19")]
    ]


def test_at_moves_the_window_and_never_before_the_clock() -> None:
    clock = datetime.fromisoformat(NOON)
    at = (clock + timedelta(days=1)).replace(hour=7)
    win = plan_window(clock, 60, at.timestamp())
    assert (win.now, win.later, win.days) == (at, True, [at.date()])
    assert _rounds(NOON, 60, at=at.timestamp()) == [[(ORIGIN, "2026-10-20"), (DEST, "2026-10-20")]]
    # The card's clock may be behind: an hour ago is now.
    win = plan_window(clock, 60, (clock - timedelta(hours=1)).timestamp())
    assert (win.now, win.later) == (clock, False)


@pytest.mark.parametrize(
    ("days", "yesterday"),
    [
        # Tomorrow 01:30: a late train of today's can still be due then.
        (1, True),
        # Three days ahead nothing of the day before runs yet: no estimates,
        # and its board ends at its midnight.
        (3, False),
    ],
)
def test_later_start_after_midnight_asks_the_day_before_only_when_it_runs(days, yesterday) -> None:
    at = (datetime.fromisoformat(NOON) + timedelta(days=days)).replace(hour=1, minute=30)
    asked = {key for keys in _rounds(NOON, 60, at=at.timestamp()) for key in keys}
    expected = {(code, at.date().isoformat()) for code in (ORIGIN, DEST)}
    if yesterday:
        expected |= {(code, (at - timedelta(days=1)).date().isoformat()) for code in (ORIGIN, DEST)}
    assert asked == expected


def test_service_days() -> None:
    evening = datetime(2026, 9, 30, 22, 30, tzinfo=TZ)
    # The look-ahead past midnight also asks tomorrow's board.
    assert service_days(evening, evening + timedelta(minutes=180)) == [date(2026, 9, 30), date(2026, 10, 1)]
    night = datetime(2026, 10, 1, 0, 40, tzinfo=TZ)
    # After midnight yesterday's board still holds delayed trains.
    assert service_days(night, night + timedelta(minutes=60)) == [date(2026, 9, 30), date(2026, 10, 1)]
    # A later start after a later midnight asks no day before it: nothing of it runs yet.
    assert service_days(night, night + timedelta(minutes=60), night.date() - timedelta(days=2)) == [date(2026, 10, 1)]
    assert service_days(night, night + timedelta(minutes=60), night.date() - timedelta(days=1)) == [
        date(2026, 9, 30),
        date(2026, 10, 1),
    ]
    day = datetime(2026, 9, 30, 17, 20, tzinfo=TZ)
    assert service_days(day, day + timedelta(minutes=180)) == [date(2026, 9, 30)]


def test_rate_limit_leaves_room_for_cards() -> None:
    """Six MÁV cards on six different pairs, at the time of day with the
    most service days, every ELVIRA_CACHE_TTL, and all of them at once when
    a dashboard opens."""
    start = datetime(2026, 10, 21, tzinfo=TZ)
    # minutes_after is at most 480 (the service schema).
    days = max(
        len(service_days(now, now + timedelta(minutes=480)))
        for now in (start + timedelta(minutes=m) for m in range(0, 24 * 60, 5))
    )
    assert days == 2
    boards = 6 * 2 * days
    assert boards / ELVIRA_CACHE_TTL <= elvira.ELVIRA_RATE * 0.9
    assert boards <= elvira.ELVIRA_BURST


# --- The board source: elvira_between over a fake StationInfo ---


async def test_rows_from_the_origin_and_destination_boards() -> None:
    clock = Clock(NOON)
    start = clock.wall + timedelta(minutes=30)
    dep, arr = _board(start, start + timedelta(minutes=62))
    dep["endStation"] = {"name": "Szombathely"}
    upstream = Upstream([dep], [arr])

    rows, dest_failure = await _between(_source(upstream, clock), 60, dest_name="Kelenföld")

    assert dest_failure is None
    assert [(r["head"], r["end"], r["booking"], r["platform"], r["travel"], r["dep"]) for r in rows] == [
        ("Kelenföld", "Szombathely", True, "6", 62, int(start.timestamp()))
    ]
    assert upstream.calls == [(ORIGIN, "2026-10-19"), (DEST, "2026-10-19")]


async def test_bad_codes_ask_nothing() -> None:
    upstream = Upstream()
    source = _source(upstream, Clock(NOON))
    for origin, dest in (("12345", DEST), (ORIGIN, ORIGIN)):
        assert await _between(source, origin=origin, dest=dest) == ([], None)
    assert upstream.calls == []


async def test_concurrent_calls_share_the_boards() -> None:
    clock = Clock(NOON)
    upstream = Upstream()
    source = _source(upstream, clock)

    await asyncio.gather(_between(source), _between(source), _between(source))
    assert len(upstream.calls) == 2
    # Within the cache TTL the boards are not fetched again; after it they are.
    await _between(source)
    assert len(upstream.calls) == 2
    clock.tick(ELVIRA_CACHE_TTL)
    await _between(source)
    assert len(upstream.calls) == 4


async def test_at_gets_that_time_s_trains() -> None:
    """A planner search for tomorrow 07:00 gets tomorrow's trains, not the
    next hour from now; a time already past starts from now."""
    clock = Clock(NOON)
    at = (clock.wall + timedelta(days=1)).replace(hour=7)
    dep, arr = _board(at + timedelta(minutes=20), at + timedelta(minutes=82))
    soon, _ = _board(clock.wall + timedelta(minutes=20), code="968")
    source = _source(Upstream([dep, soon], [arr]), clock)

    rows, _ = await _between(source, 60, at=int(at.timestamp()))
    assert [(r["trainNumber"], r["dep"], r["travel"]) for r in rows] == [
        ("966", int((at + timedelta(minutes=20)).timestamp()), 62)
    ]
    rows, _ = await _between(source, 60, at=int((clock.wall - timedelta(hours=1)).timestamp()))
    assert [r["trainNumber"] for r in rows] == ["968"]


async def test_later_start_leaves_out_trains_leaving_before_it() -> None:
    """Against the clock a train that left and arrives later is under way;
    before a later `at` it is one the search starts after."""
    clock = Clock(NOON)
    at = (clock.wall + timedelta(days=3)).replace(hour=10)
    early, early_arr = _board(at - timedelta(minutes=30), at + timedelta(minutes=20), code="900")
    soon, soon_arr = _board(at + timedelta(minutes=1), at + timedelta(minutes=61), code="901")
    later, later_arr = _board(at + timedelta(minutes=30), at + timedelta(minutes=90), code="902")
    for i, row in enumerate((early, early_arr, soon, soon_arr, later, later_arr)):
        row["trainId"] = f"t{i // 2}"
    source = _source(Upstream([early, soon, later], [early_arr, soon_arr, later_arr]), clock)

    rows, _ = await _between(source, 60, at=int(at.timestamp()))
    assert [(r["trainNumber"], r["destHit"]) for r in rows] == [("901", True), ("902", True)]


async def test_times_across_the_autumn_clock_change() -> None:
    """2026-10-25 03:00 CEST is 02:00 CET. A train leaving 01:50 CEST and
    arriving 03:20 CET travels 150 minutes; wall clocks say 90."""
    dep = {
        "trainId": "night",
        "code": "40400",
        "start": "2026-10-25T01:50:00",
        "actualOrEstimatedStart": "2026-10-25T01:58:00",
        "startTrack": "2",
        "services": [],
    }
    arr = {"trainId": "night", "code": "40400", "arrive": "2026-10-25T03:20:00"}
    source = _source(Upstream([dep], [arr]), Clock("2026-10-25T01:40:00+02:00"))

    (row,), _ = await _between(source, 180)
    assert row["travel"] == 142
    assert row["delay"] == 8 * 60
    assert row["dep"] == int(datetime(2026, 10, 24, 23, 58, tzinfo=UTC).timestamp())


async def test_answers_within_its_budget() -> None:
    """The second round (tomorrow's arrival board) gets what is left of the
    call budget: one past it is a destination board that did not arrive,
    and it still lands in the cache for the next poll."""
    clock = Clock("2026-10-19T15:00:00+02:00")
    dep, _arr = _board(clock.wall + timedelta(minutes=30))
    upstream = Upstream([dep])
    release = asyncio.Event()
    # Without a run on today's boards the second round asks for it, and
    # ELVIRA takes its time.
    upstream.hold[(DEST, "2026-10-20")] = release
    source = _source(upstream, clock, call_budget=0.05)

    rows, dest_failure = await _between(source, 180)
    assert [r["trainNumber"] for r in rows] == ["966"]
    assert isinstance(dest_failure, ElviraError) and str(dest_failure) == "TimeoutError"
    assert (DEST, "2026-10-20") in upstream.calls

    # The board's request went on without the answer.
    release.set()
    await _settle_tasks()
    calls = len(upstream.calls)
    assert (await _between(source, 180))[1] is None
    assert len(upstream.calls) == calls


async def test_outage_is_an_error_logged_once_and_not_cached(caplog) -> None:
    """The shared cache must not hold a failed board for ELVIRA_CACHE_TTL as
    'no trains'; every card polls, but a station going down is one warning."""
    upstream = Upstream(failing={ORIGIN, DEST})
    source = _source(upstream, Clock(NOON))

    for polls in (1, 2, 3):
        with pytest.raises(ElviraError, match="HTTP 503") as err:
            await _between(source)
        assert type(err.value) is ElviraError
        assert len(upstream.calls) == 2 * polls
    assert len(_warnings(caplog)) == 2


async def test_failed_destination_board_counts_as_empty(caplog) -> None:
    """The origin board alone carries platform and booking; a destination
    outage only costs the travel time, and is logged once, not per poll."""
    caplog.set_level(logging.INFO, logger=elvira.__name__)
    clock = Clock(NOON)
    dep, _arr = _board(clock.wall + timedelta(minutes=10))
    upstream = Upstream([dep], failing={DEST})
    source = _source(upstream, clock)

    for _ in range(3):
        rows, dest_failure = await _between(source)
        assert [(r["platform"], r["booking"], r["travel"], r["destHit"]) for r in rows] == [
            ("6", True, None, False)
        ]
        # No destHit can be trusted: a MÁV card lists only those rows.
        assert type(dest_failure) is ElviraError
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and DEST in warnings[0] and "HTTP 503" in warnings[0]

    upstream.failing.clear()
    caplog.clear()
    assert (await _between(source))[1] is None
    await _between(source)
    assert [r.getMessage() for r in caplog.records if "answers again" in r.getMessage()] == [
        f"ELVIRA StationInfo {DEST} answers again"
    ]


async def test_failed_extra_day_board_counts_as_empty() -> None:
    """Just after midnight yesterday's board is asked for its late trains;
    when only that one fails, today's departures still arrive."""
    clock = Clock("2026-10-19T00:30:00+02:00")
    dep, _arr = _board(clock.wall + timedelta(minutes=10))
    source = _source(Upstream([dep], failing={(ORIGIN, "2026-10-18")}), clock)

    rows, dest_failure = await _between(source)
    assert [row["platform"] for row in rows] == ["6"]
    assert dest_failure is None


@pytest.mark.parametrize(
    ("late", "dest_error"),
    [
        # Yesterday's origin board has nothing left to leave: its arrivals
        # are no destHit of any row, so no dest_error for them.
        (False, False),
        # A train of yesterday's still to leave (23:50, 40 minutes late):
        # its arrival is on yesterday's destination board, which failed.
        (True, True),
    ],
)
async def test_failed_yesterdays_dest_board_matters_only_for_yesterdays_trains(late, dest_error) -> None:
    """A train on today's origin board arrives on today's or tomorrow's
    destination board, never yesterday's."""
    clock = Clock("2026-10-19T00:30:00+02:00")
    midnight = clock.wall.replace(hour=0, minute=0)
    dep, arr = _board(clock.wall + timedelta(minutes=10), clock.wall + timedelta(minutes=50))
    old_dep, _arr = _board(midnight - timedelta(minutes=10), code="955")
    old_dep["trainId"] = "t0"
    if late:
        old_dep["actualOrEstimatedStart"] = (midnight + timedelta(minutes=30)).strftime(FMT)
    source = _source(Upstream([old_dep, dep], [arr], failing={(DEST, "2026-10-18")}), clock)

    rows, dest_failure = await _between(source)
    assert [row["trainNumber"] for row in rows] == (["955", "966"] if late else ["966"])
    assert rows[-1]["destHit"] is True
    assert (dest_failure is not None) is dest_error


async def test_failed_extra_day_board_is_not_an_outage(caplog) -> None:
    """Between 00:00 and 03:00 yesterday's boards are asked for too. When
    only those fail, the stations answer: no "unavailable" warning (and no
    "answers again" later). Today's board failing is still one."""
    clock = Clock("2026-10-19T00:30:00+02:00")
    dep, _arr = _board(clock.wall + timedelta(minutes=10))
    upstream = Upstream([dep], failing={(ORIGIN, "2026-10-18"), (DEST, "2026-10-18")})
    source = _source(upstream, clock)

    rows, dest_failure = await _between(source)
    assert len(rows) == 1
    # Without yesterday's origin board no row is a train of yesterday's, so
    # yesterday's arrivals are nobody's destHit.
    assert dest_failure is None
    assert _warnings(caplog) == []

    upstream.failing = {DEST}
    clock.tick(ELVIRA_CACHE_TTL + 1)
    await _between(source)
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and DEST in warnings[0]


async def test_error_when_no_origin_board_arrives() -> None:
    source = _source(Upstream(failing={ORIGIN}), Clock("2026-10-19T00:30:00+02:00"))
    with pytest.raises(ElviraError, match="HTTP 503"):
        await _between(source)


async def test_error_when_todays_origin_board_fails() -> None:
    """22:30 with three hours ahead asks today's and tomorrow's boards. When
    only tomorrow's arrives, its post-midnight trains are not the answer:
    every train of this evening would be missing without a word."""
    clock = Clock("2026-10-19T22:30:00+02:00")
    tonight, _ = _board(clock.wall + timedelta(minutes=20))
    after_midnight, _ = _board(clock.wall + timedelta(minutes=110), code="900")
    source = _source(Upstream([tonight, after_midnight], failing={(ORIGIN, "2026-10-19")}), clock)
    with pytest.raises(ElviraError, match="HTTP 503"):
        await _between(source, 180)


async def test_requests_are_rate_limited(caplog) -> None:
    """Any logged-in user can call elvira_between and every new station pair
    is new boards: past the burst a board is refused (one log line, no
    request, not an outage), and the clock refills the bucket."""
    clock = Clock(NOON)
    upstream = Upstream()
    source = _source(upstream, clock)

    # One service day at noon: the first call takes the destination board
    # too, every further origin one board.
    for n in range(elvira.ELVIRA_BURST - 1):
        assert await _between(source, origin=f"0055{n:05d}") == ([], None)
    assert len(upstream.calls) == elvira.ELVIRA_BURST
    for n in range(100, 105):
        with pytest.raises(ElviraRateLimited, match="rate limited"):
            await _between(source, origin=f"0055{n:05d}")
    assert len(upstream.calls) == elvira.ELVIRA_BURST
    assert len([m for m in _warnings(caplog) if "rate limit" in m]) == 1
    assert not [m for m in _warnings(caplog) if "unavailable" in m]

    clock.mono += 1 / elvira.ELVIRA_RATE
    assert await _between(source, origin="005500200") == ([], None)
    assert len(upstream.calls) == elvira.ELVIRA_BURST + 1


async def test_station_answering_again_is_logged_despite_a_refused_board(caplog) -> None:
    """A station that was down and now answers some boards, while the rate
    limit refuses another, has answered: one "answers again" line, not
    silence until every board of a later call gets through."""
    caplog.set_level(logging.INFO, logger=elvira.__name__)
    # Two service days after midnight: four boards per call, fetched origin
    # today, origin yesterday, destination yesterday, destination today.
    upstream = Upstream(failing={ORIGIN, DEST})
    source = _source(upstream, Clock("2026-10-19T00:30:00+02:00"), burst=7)
    with pytest.raises(ElviraError):
        await _between(source)
    assert len([m for m in _warnings(caplog) if "unavailable" in m]) == 2

    upstream.failing.clear()
    rows, dest_failure = await _between(source)
    assert rows == [] and type(dest_failure) is ElviraRateLimited
    assert len(upstream.calls) == 4 + 3
    again = [r.getMessage() for r in caplog.records if "answers again" in r.getMessage()]
    assert sorted(again) == sorted(f"ELVIRA StationInfo {code} answers again" for code in (ORIGIN, DEST))


async def test_todays_origin_board_gets_the_last_token() -> None:
    """After midnight a call needs yesterday's boards too; with one token
    left it goes to today's origin board, without which the call fails."""
    upstream = Upstream()
    source = _source(upstream, Clock("2026-10-21T00:30:00+02:00"), burst=1)

    rows, dest_failure = await _between(source)
    assert rows == [] and type(dest_failure) is ElviraRateLimited
    assert upstream.calls == [(ORIGIN, "2026-10-21")]


async def test_boards_leave_memory_by_the_cache_timer_and_close_stops_it() -> None:
    """Megabytes per board: the cache's expiry timer (call_later) drops them
    once nobody polls; close() (Home Assistant stopping) cancels it."""
    timers: list[tuple[float, object]] = []
    cancelled: list[bool] = []

    def call_later(delay, action):
        timers.append((delay, action))
        return lambda: cancelled.append(True)

    clock = Clock(NOON)
    upstream = Upstream()
    source = _source(upstream, clock, call_later=call_later)
    await _between(source)
    assert [delay for delay, _ in timers] == [ELVIRA_CACHE_TTL]

    clock.tick(ELVIRA_CACHE_TTL)
    timers[0][1]()
    # Dropped by the timer: the next call asks again.
    clock.tick(-ELVIRA_CACHE_TTL)
    await _between(source)
    assert len(upstream.calls) == 4

    source.close()
    assert cancelled == [True]


# --- The StationInfo request (fetch_station) ---


class _Response:
    def __init__(self, status=200, body=None, exc=None) -> None:
        self.status = status
        self._body = body
        self._exc = exc

    async def __aenter__(self):
        if self._exc is not None:
            raise self._exc
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def json(self, content_type=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, *, json, headers, timeout):
        self.posts.append((url, json))
        return self.response


async def test_fetch_station_asks_for_a_whole_service_day() -> None:
    session = _Session(_Response(body={"stationSchedulerDetails": {}}))
    assert await fetch_station(session, ORIGIN, date(2026, 11, 6)) == ([], [])
    ((url, payload),) = session.posts
    assert url == ELVIRA_URL
    assert payload == {
        "type": "StationInfo",
        "travelDate": "2026-11-06T00:00:00",
        "stationNumberCode": ORIGIN,
        "minCount": "0",
        "maxCount": "2000",
    }


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (_Response(status=503), "HTTP 503"),
        (_Response(exc=asyncio.TimeoutError()), "TimeoutError"),
        (_Response(exc=aiohttp.ClientConnectionError()), "ClientConnectionError"),
        (_Response(body=json.JSONDecodeError("x", "", 0)), "JSONDecodeError"),
        (_Response(body=[1, 2]), "unexpected response"),
        (_Response(body="nope"), "unexpected response"),
        # A 200 without stationSchedulerDetails is an error page or a
        # changed API, not a day without trains.
        (_Response(body={}), "unexpected response"),
        (_Response(body={"stationSchedulerDetails": None}), "unexpected response"),
        (_Response(body={"message": "Hiba"}), "unexpected response"),
    ],
)
async def test_fetch_station_failures_are_elvira_errors(response, detail) -> None:
    with pytest.raises(ElviraError) as err:
        await fetch_station(_Session(response), ORIGIN, date(2026, 10, 19))
    assert type(err.value) is ElviraError and str(err.value) == detail


async def test_malformed_rows_are_skipped() -> None:
    now = datetime.fromisoformat(NOON)
    dep, _arr = _board(now + timedelta(minutes=10))
    odd = dict(dep, trainId="t2", code="968", viszonylatiJel="IC", endStation="x", services=["3880", None])
    details = {"departureScheduler": [1, "row", None, dep, odd], "arrivalScheduler": {"not": "a list"}}
    session = _Session(_Response(body={"stationSchedulerDetails": details}))

    odep, darr = await fetch_station(session, ORIGIN, now.date())
    assert darr == []
    rows = rows_from_stationinfo(odep, darr, TZ, now, now + timedelta(minutes=15))
    assert [(row["trainNumber"], row["booking"]) for row in rows] == [("966", True), ("968", False)]
    assert rows[1]["label"] == "GÖCSEJ" and rows[1]["head"] == "" and rows[1]["end"] == ""


# --- Rows and service flags (pure) ---


def _svc(**kwargs):
    return {"services": [kwargs]}


@pytest.mark.parametrize(
    ("row", "flag", "expected"),
    [
        (_svc(code="3880", description=""), "booking", True),
        (_svc(key="SZOLGALTATAS#3880"), "booking", True),
        (_svc(description="Helyjegy váltása kötelező"), "booking", True),
        # Helyjegy váltható, not required.
        (_svc(code="94345", description="Helyjegy váltható."), "booking", False),
        (_svc(code="13880"), "booking", False),
        (_svc(code="SZOLGALTATAS#8163379", description="Országbérlettel igénybe vehető"), "nationalPass", True),
        (_svc(code="SZOLGALTATAS#3880", description="Helyjegy váltása kötelező"), "nationalPass", False),
        (
            _svc(
                code="SZOLGALTATAS#8586013",
                description="Belföldi utazásra a kijelölt viszonylaton helyjegy váltása nélkül igénybe vehető 2. kocsiosztályon.",
            ),
            "reservationFreeSection",
            True,
        ),
        (_svc(code="SZOLGALTATAS#3857", description="Kerékpár nem szállítható"), "bikesAllowed", False),
        (_svc(code="SZOLGALTATAS#8595413", description="Kerékpár a megjelölt helyen szállítható."), "bikesAllowed", True),
    ],
)
def test_service_flags(row, flag, expected) -> None:
    assert parse_elvira_services(row)[flag] is expected


def test_train_kind_for_a_row_with_only_a_number() -> None:
    assert elvira._kind_from_row({"kind": {"name": "sebesvonat", "sortName": None}}) == "S"
    assert elvira._kind_from_row({"kind": {"name": "személyvonat", "sortName": "Sz"}}) == "Sz"
    assert elvira._kind_from_row({"kind": {"name": "InterCity", "sortName": "IC"}}) == "IC"
    assert elvira._kind_from_row({"kind": None}) == ""


EVENING = datetime(2026, 9, 30, 17, 20, tzinfo=TZ)
START = EVENING + timedelta(minutes=40)
GOCSEJ = {
    "trainId": "2975814",
    "code": "966",
    "name": "GÖCSEJ",
    "start": START.strftime(FMT),
    "actualOrEstimatedStart": START.strftime(FMT),
    "startTrack": "6",
    "endStation": {"name": "Szombathely"},
    "services": [{"code": "3880", "description": "Helyjegy váltása kötelező"}],
}
GEMENC = {
    "trainId": "1",
    "code": "800",
    "name": "GEMENC",
    "start": (EVENING + timedelta(minutes=50)).strftime(FMT),
    "startTrack": "3",
    "endStation": {"name": "Baja"},
    "services": [{"code": "3880"}],
}
G43 = {
    "trainId": "2",
    "code": "3511",
    "name": "G43",
    "start": (EVENING + timedelta(minutes=55)).strftime(FMT),
    "startTrack": "5",
    "endStation": {"name": "Budapest-Déli"},
    "services": [{"code": "94345", "description": "Helyjegy váltható."}],
}
GOCSEJ_ARR = [{"trainId": "2975814", "code": "966", "arrive": (START + timedelta(minutes=62)).strftime(FMT)}]


def _rows(odep, darr, now=EVENING, minutes=180, dest_name="", later=False):
    return rows_from_stationinfo(odep, darr, TZ, now, now + timedelta(minutes=minutes), dest_name, later)


def test_origin_rows_without_a_destination_board() -> None:
    by_num = {r["trainNumber"]: r for r in _rows([GOCSEJ, GEMENC, G43], [], dest_name="Kelenföld")}
    assert list(by_num) == ["966", "800", "3511"]
    gocsej = by_num["966"]
    assert (gocsej["booking"], gocsej["nationalPass"], gocsej["destHit"], gocsej["travel"], gocsej["platform"]) == (
        True,
        False,
        False,
        None,
        "6",
    )
    # Every row carries its train's terminus; head stays the card's destination.
    assert (gocsej["end"], gocsej["head"], by_num["3511"]["end"]) == ("Szombathely", "Kelenföld", "Budapest-Déli")
    assert by_num["3511"]["booking"] is False


def test_a_non_string_start_is_skipped_not_a_type_error() -> None:
    """The estimated start identifies the run instead."""
    odd = dict(G43, start={"x": 1}, actualOrEstimatedStart=G43["start"])
    assert [r["trainNumber"] for r in _rows([odd], [])] == ["3511"]


def test_destination_hit_fills_the_travel_time() -> None:
    (row,) = _rows([GOCSEJ], GOCSEJ_ARR, dest_name="Kelenföld")
    assert (row["destHit"], row["travel"], row["booking"]) == (True, 62, True)


def test_a_train_under_way_stays_a_row_against_the_clock_not_before_a_later_start() -> None:
    gone = dict(GOCSEJ, start=(EVENING - timedelta(minutes=20)).strftime(FMT))
    gone["actualOrEstimatedStart"] = gone["start"]
    assert [r["trainNumber"] for r in _rows([gone], GOCSEJ_ARR)] == ["966"]
    assert _rows([gone], GOCSEJ_ARR, later=True) == []
    assert len(_rows([GOCSEJ], GOCSEJ_ARR, later=True)) == 1


def test_longest_run() -> None:
    assert longest_run([GOCSEJ, GEMENC], GOCSEJ_ARR, TZ) == timedelta(minutes=62)
    assert longest_run([GEMENC], GOCSEJ_ARR, TZ) is None


def test_a_past_arrival_of_the_same_code_is_no_destination_hit() -> None:
    stale = [{"trainId": "morning-other", "code": "966", "arrive": EVENING.replace(hour=8, minute=10).strftime(FMT)}]
    (row,) = _rows([GOCSEJ], stale, dest_name="Kelenföld")
    assert (row["trainNumber"], row["destHit"], row["travel"], row["booking"], row["platform"]) == (
        "966",
        False,
        None,
        True,
        "6",
    )


def test_a_repeated_code_matches_the_run_arriving_after_midnight() -> None:
    evening = datetime(2026, 9, 30, 22, 30, tzinfo=TZ)
    late_start = (evening + timedelta(minutes=50)).strftime(FMT)
    late = dict(GOCSEJ, start=late_start, actualOrEstimatedStart=late_start)
    two_runs = [
        {"trainId": "morning-run", "code": "966", "arrive": evening.replace(hour=9).strftime(FMT)},
        {
            "trainId": "night-run",
            "code": "966",
            "arrive": (evening + timedelta(minutes=120)).strftime(FMT),
        },
    ]
    (row,) = _rows([late], two_runs, now=evening, dest_name="Szombathely")
    assert (row["destHit"], row["travel"]) == (True, 70)


def test_a_delayed_train_of_yesterday_is_listed_once_with_its_delay() -> None:
    night = datetime(2026, 10, 1, 0, 40, tzinfo=TZ)
    delayed = dict(
        GOCSEJ,
        trainId="",
        start=(night - timedelta(minutes=50)).strftime(FMT),
        actualOrEstimatedStart=(night + timedelta(minutes=5)).strftime(FMT),
    )
    rows = _rows([delayed, delayed], [], now=night, minutes=60)
    assert [row["delay"] for row in rows] == [55 * 60]


def test_rows_across_the_autumn_clock_change() -> None:
    """2026-10-25 03:00 CEST becomes 02:00 CET: travel and delay are real
    minutes, and a train before now in the repeated hour is gone."""
    night = datetime(2026, 10, 25, 1, 40, tzinfo=TZ)
    train = dict(
        GOCSEJ,
        trainId="dst-night",
        code="40400",
        start="2026-10-25T01:50:00",
        actualOrEstimatedStart="2026-10-25T01:55:00+02:00",
    )
    arrival = [{"trainId": "dst-night", "code": "40400", "arrive": "2026-10-25T03:20:00"}]
    (row,) = _rows([train], arrival, now=night, dest_name="Szeged")
    assert (row["travel"], row["delay"]) == (145, 5 * 60)
    assert row["dep"] == int(datetime(2026, 10, 24, 23, 55, tzinfo=UTC).timestamp())

    # 02:30 CET is after 02:40 CEST, although its wall clock is earlier.
    after_change = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=TZ)
    left = dict(GOCSEJ, trainId="left", code="40402", start="2026-10-25T02:40:00+02:00")
    left["actualOrEstimatedStart"] = left["start"]
    due = dict(GOCSEJ, trainId="due", code="40404", start="2026-10-25T02:40:00+01:00")
    due["actualOrEstimatedStart"] = due["start"]
    assert [r["trainNumber"] for r in _rows([left, due], [], now=after_change, minutes=60)] == ["40404"]


def test_the_same_train_number_tomorrow_is_not_this_run_s_arrival() -> None:
    """Train 2345 runs the other way: it passes the destination at 17:10 and
    the origin at 17:40. Tomorrow's 17:10 at the destination is not its
    arrival: no destHit, no 23.5-hour travel time."""
    eve = datetime(2026, 10, 21, 17, 0, tzinfo=TZ)
    back = _rows(
        [{"trainId": "T-today", "code": "2345", "start": eve.replace(minute=40).isoformat()}],
        [
            {"trainId": "T-today", "code": "2345", "arrive": eve.replace(minute=10).isoformat()},
            {"trainId": "T-tomorrow", "code": "2345", "arrive": (eve + timedelta(days=1)).replace(minute=10).isoformat()},
        ],
        now=eve,
        minutes=480,
    )
    assert [(r["destHit"], r["travel"]) for r in back] == [(False, None)]
    # The code fallback still pairs today's arrival (85 min), not tomorrow's.
    fwd = _rows(
        [{"trainId": "A", "code": "2346", "start": eve.replace(minute=40).isoformat()}],
        [
            {"trainId": "B", "code": "2346", "arrive": eve.replace(hour=19, minute=5).isoformat()},
            {"trainId": "C", "code": "2346", "arrive": (eve + timedelta(days=1)).replace(hour=19, minute=5).isoformat()},
        ],
        now=eve,
        minutes=480,
    )
    assert [(r["destHit"], r["travel"]) for r in fwd] == [(True, 85)]


# --- The outage log (pure) ---


def test_outage_log_is_bounded_in_time_and_size(caplog) -> None:
    caplog.set_level(logging.INFO, logger=elvira.__name__)
    clock = [0.0]
    log = OutageLog(lambda: clock[0])
    boom = RuntimeError("timeout")

    def report(code, err, at):
        clock[0] = at
        log.report(code, err)

    def unavailable():
        return [m for m in caplog.messages if "unavailable" in m]

    report("005510009", boom, 0.0)
    report("005510009", boom, 45.0)
    # A failing station is warned about once, not every poll.
    assert len(unavailable()) == 1 and list(log.down) == ["005510009"]
    # After ELVIRA_DOWN_TTL a station that never answered is forgotten and warned about again.
    report("005510009", boom, ELVIRA_DOWN_TTL + 1.0)
    assert len(unavailable()) == 2 and log.down["005510009"] == ELVIRA_DOWN_TTL + 1.0
    # An answer clears it with one line.
    report("005510009", None, ELVIRA_DOWN_TTL + 50.0)
    assert "005510009" not in log.down and "answers again" in caplog.messages[-1]
    # Stale entries go even when only other stations are polled.
    report("008000001", boom, 100.0)
    report("008000002", None, ELVIRA_DOWN_TTL + 100.0)
    assert "008000001" not in log.down
    # At most ELVIRA_DOWN_MAX stations are remembered, the oldest dropped first.
    for i in range(ELVIRA_DOWN_MAX + 10):
        report(f"0090{i:05d}", boom, 200_000.0 + i)
    assert len(log.down) == ELVIRA_DOWN_MAX
    assert "009000000" not in log.down and f"0090{ELVIRA_DOWN_MAX + 9:05d}" in log.down
