"""ELVIRA boards waiting for a turn among ELVIRA_FETCHES (elvira.py): what a
call answers while its boards wait, the order they get a turn in, and the
turn held while a board is decoded."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
import logging
import random
import threading
import time

import pytest

from custom_components.hungarian_transport import elvira
from custom_components.hungarian_transport.elvira import (
    ELVIRA_FETCHES,
    MAV_TZ,
    ElviraBoards,
    ElviraBusy,
    ElviraError,
    ElviraRateLimited,
    fetch_station,
)

# Midday: one service day and no second round, so a call asks for exactly
# two boards, its origin's and its destination's of today.
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=MAV_TZ)
TODAY = NOON.date()


class Clock:
    def __init__(self) -> None:
        self.mono = 1000.0

    def monotonic(self) -> float:
        return self.mono


def _source(fetch, clock: Clock | None = None, **kwargs) -> ElviraBoards:
    clock = clock or Clock()
    return ElviraBoards(fetch, now=lambda: NOON, monotonic=clock.monotonic, burst=10_000, **kwargs)


def _pair(i: int) -> tuple[str, str]:
    return f"0055{1000 + 2 * i:05d}", f"0055{1001 + 2 * i:05d}"


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


async def _settle_tasks() -> None:
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending, return_exceptions=True)


async def _spin(times: int = 10) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


async def test_a_call_whose_boards_wait_for_a_turn_answers_busy_and_the_boards_are_still_asked(
    monkeypatch, caplog
) -> None:
    """The board's ELVIRA_TIMEOUT used to run from when it was asked: one
    queued behind slower ones timed out without being asked, was logged as
    ELVIRA being unavailable, and never reached the cache. Now the call
    answers within its first round's ELVIRA_TIMEOUT that it is busy (an
    ElviraRateLimited, elvira_rate_limited for the card: no outage), and
    its boards are asked once they get a turn, into the cache."""
    asked: list[str] = []
    gate = asyncio.Event()
    origin, dest = _pair(0)

    async def fetch(code: str, day: date, max_count: str):
        asked.append(code)
        if code not in (origin, dest):
            await gate.wait()
        return [], []

    source = _source(fetch)
    # Slow boards take every turn.
    slow_codes = [f"0055100{i:02d}" for i in range(ELVIRA_FETCHES)]
    slow = asyncio.gather(*(source.board(code, TODAY) for code in slow_codes))
    await _spin()
    monkeypatch.setattr(elvira, "ELVIRA_TIMEOUT_SEC", 0.05)
    with pytest.raises(ElviraBusy) as err:
        await asyncio.wait_for(source.between(origin, dest, 15), 1)
    assert isinstance(err.value, ElviraRateLimited)
    assert asked == slow_codes and _warnings(caplog) == []

    gate.set()
    assert await slow == [([], [])] * ELVIRA_FETCHES
    await _settle_tasks()
    assert sorted(asked[ELVIRA_FETCHES:]) == [origin, dest]
    # Both boards are in the cache: the next poll asks for nothing.
    assert await source.between(origin, dest, 15) == ([], None)
    assert len(asked) == ELVIRA_FETCHES + 2 and _warnings(caplog) == []
    source.close()


async def test_a_board_asked_at_once_and_still_unanswered_at_the_budget_is_elvira_timing_out(
    monkeypatch, caplog
) -> None:
    """No wait for a turn: ELVIRA itself is slow, an outage."""
    monkeypatch.setattr(elvira, "ELVIRA_TIMEOUT_SEC", 0.05)

    async def fetch(code: str, day: date, max_count: str):
        await asyncio.sleep(1)
        return [], []

    source = _source(fetch)
    origin, dest = _pair(0)
    with pytest.raises(ElviraError) as err:
        await asyncio.wait_for(source.between(origin, dest, 15), 1)
    assert type(err.value) is ElviraError and str(err.value) == "TimeoutError"
    assert any("unavailable (TimeoutError)" in line for line in _warnings(caplog))
    source.close()


async def test_today_s_origin_boards_get_the_free_turns_first() -> None:
    """A call fails without today's origin board and only lacks travel times
    without its destination's: under load the origin boards go first, in
    the order asked, so the cards degrade to dest_error rather than fail. A
    board shared with a call whose origin it is waits as one."""
    asked: list[str] = []
    gates = {f"0055100{i:02d}": asyncio.Event() for i in range(ELVIRA_FETCHES)}

    async def fetch(code: str, day: date, max_count: str):
        asked.append(code)
        if code in gates:
            await gates[code].wait()
        return [], []

    source = _source(fetch)
    slow = asyncio.gather(*(source.board(code, TODAY) for code in gates))
    await _spin()
    (a_origin, a_dest), (b_origin, b_dest) = _pair(0), _pair(1)
    other = "005519999"
    calls = [
        asyncio.ensure_future(source.between(a_origin, a_dest, 15)),
        asyncio.ensure_future(source.between(b_origin, b_dest, 15)),
    ]
    await _spin()
    # b_dest is this call's origin.
    calls.append(asyncio.ensure_future(source.between(b_dest, other, 15)))
    await _spin()
    assert asked == list(gates)
    for gate in gates.values():
        gate.set()
    await asyncio.gather(slow, *calls)
    assert asked[ELVIRA_FETCHES:] == [a_origin, b_origin, b_dest, a_dest, other]
    source.close()


async def test_a_steady_overload_does_not_fail_the_same_cards_on_every_poll(monkeypatch, caplog) -> None:
    """Six cards on six pairs, polling as often as the cache keeps a board
    (ELVIRA_CACHE_TTL), and ELVIRA answering only seven of their twelve
    boards within a call's wait. The boards queued last used to time out
    without being asked on every poll, the same cards failing (and logged
    as ELVIRA unavailable) for good. Now today's origin boards go first, so
    no card fails, and a board asked after its call stopped waiting is in
    the cache for the next poll, so every card has its whole answer at
    least every other poll."""
    monkeypatch.setattr(elvira, "ELVIRA_TIMEOUT_SEC", 0.6)
    poll = elvira.ELVIRA_CACHE_TTL
    clock = Clock()
    # The requests ELVIRA has not answered yet, in the order they were sent.
    requests: list[asyncio.Future] = []

    async def fetch(code: str, day: date, max_count: str):
        request = asyncio.get_running_loop().create_future()
        requests.append(request)
        await request
        return [], []

    async def answer_one() -> None:
        request = requests.pop(0)
        if not request.done():
            request.set_result(None)
        await _spin()

    # A call waits this long; a board's own ELVIRA_TIMEOUT is longer, as a
    # board that gets its turn late starts it late.
    source = _source(fetch, clock, call_budget=0.3)
    pairs = [_pair(i) for i in range(6)]
    answers: list[list[str]] = []
    for _ in range(4):
        polled = asyncio.gather(*(source.between(o, d, 15) for o, d in pairs), return_exceptions=True)
        await _spin(30)
        # ELVIRA answers seven boards while the cards wait.
        for _ in range(7):
            await answer_one()
        results = await polled
        answers.append([
            "X" if isinstance(r, BaseException) else "d" if r[1] is not None else "." for r in results
        ])
        # The rest answer later, before the next poll.
        clock.mono += poll / 2
        while requests:
            await answer_one()
        await _settle_tasks()
        clock.mono += poll / 2
    assert all("X" not in polled for polled in answers), answers
    for card in range(len(pairs)):
        seen = [polled[card] for polled in answers]
        assert all("." in pair for pair in zip(seen, seen[1:])), (card, answers)
    assert not any("unavailable" in line for line in _warnings(caplog))
    source.close()


class _Content:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    async def iter_chunked(self, size: int):
        await asyncio.sleep(0.01)
        yield self.raw


class _Response:
    status = 200
    content_length = None

    def __init__(self) -> None:
        self.content = _Content(b'{"stationSchedulerDetails": {}}')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False


class _Session:
    def post(self, url, *, json, headers, timeout):
        return _Response()


async def test_a_decoding_that_outlives_its_timeout_keeps_its_turn(monkeypatch) -> None:
    """The executor's thread decodes on when the board's timeout cancels the
    wait for it; giving the turn back then let more than ELVIRA_FETCHES
    bodies be decoded, and held in memory, at once. The turn is held until
    the decoding ends (fetch_station, as Home Assistant wires it)."""
    monkeypatch.setattr(elvira, "ELVIRA_TIMEOUT_SEC", 0.1)
    lock = threading.Lock()
    decoding = 0
    most = 0
    real_parse = elvira._parse_board

    def slow_parse(body):
        nonlocal decoding, most
        with lock:
            decoding += 1
            most = max(most, decoding)
        time.sleep(0.3)
        with lock:
            decoding -= 1
        return real_parse(body)

    monkeypatch.setattr(elvira, "_parse_board", slow_parse)
    session = _Session()
    source = _source(lambda code, day, max_count: fetch_station(session, code, day, max_count))

    async def asked_later(i: int):
        # ELVIRA_FETCHES at a time, each group as the one before it times out.
        await asyncio.sleep(0.1 * (i // ELVIRA_FETCHES))
        return await source.board(f"0055{i:05d}", TODAY)

    results = await asyncio.gather(*(asked_later(i) for i in range(3 * ELVIRA_FETCHES)), return_exceptions=True)
    assert most == ELVIRA_FETCHES
    assert [str(r) for r in results] == ["TimeoutError"] * (3 * ELVIRA_FETCHES)
    source.close()


async def test_the_turns_survive_cancelled_calls_and_requests() -> None:
    """Callers giving up and requests cancelled (Home Assistant stopping),
    while waiting for a turn or holding one: no turn is lost or doubled."""
    rnd = random.Random(7)
    under_way = 0
    most = 0

    async def fetch(code: str, day: date, max_count: str):
        nonlocal under_way, most
        under_way += 1
        most = max(most, under_way)
        try:
            if rnd.random() < 0.1:
                raise ElviraError("HTTP 500")
            await asyncio.sleep(rnd.uniform(0, 0.02))
            return [], []
        finally:
            under_way -= 1

    source = _source(fetch)

    async def caller() -> None:
        task = asyncio.ensure_future(source.board(f"0055{rnd.randint(0, 300):05d}", TODAY))
        if rnd.random() < 0.3:
            await asyncio.sleep(rnd.uniform(0, 0.02))
            task.cancel()
        try:
            await task
        except (ElviraError, asyncio.CancelledError):
            pass

    for wave in range(10):
        await asyncio.gather(*(caller() for _ in range(50)))
        if wave % 3 == 0:
            requests = list(source._cache._inflight.values())
            for request in requests[::2]:
                request.cancel()
            await _spin()
    await _settle_tasks()
    assert most <= ELVIRA_FETCHES
    assert source._free_turns == ELVIRA_FETCHES and not source._turns
    assert all(turn.future.done() for turn in source._waiting)
    assert await asyncio.wait_for(source.board("005599999", TODAY), 1) == ([], [])
    source.close()
