"""The upstream guard (upstream.py) on a fake clock, without Home Assistant."""

import logging

import pytest

from custom_components.hungarian_transport import upstream

_LOGGER = logging.getLogger("custom_components.hungarian_transport.test_guard")


class FakeClock:
    """monotonic and sleep for an owner's constructor. A sleep moves the
    clock by its seconds (plus busy, an event loop slower than asked), or
    not at all with stopped: nothing refills then."""

    def __init__(self, now: float = 1000.0, *, stopped: bool = False, busy: float = 0.0) -> None:
        self.now = now
        self.stopped = stopped
        self.busy = busy
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if not self.stopped:
            self.now += seconds + self.busy


class Refused(Exception):
    pass


def _bucket(clock: FakeClock, refusals: list[int], rate=2.0, burst=3, max_wait=0.0, log_every=300):
    return upstream.TokenBucket(
        rate,
        burst,
        refused=lambda: Refused("rate limited"),
        log_refused=refusals.append,
        log_every=log_every,
        max_wait=max_wait,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


# --- TokenBucket ---


def test_take_refuses_at_once_and_the_clock_refills() -> None:
    clock = FakeClock()
    refusals: list[int] = []
    bucket = _bucket(clock, refusals)
    for _ in range(3):
        bucket.take()
    with pytest.raises(Refused, match="rate limited"):
        bucket.take()
    assert clock.slept == [] and refusals == [1]

    clock.now += 0.5  # one token at 2/s
    bucket.take()
    with pytest.raises(Refused):
        bucket.take()


def test_refill_stops_at_the_burst() -> None:
    clock = FakeClock()
    bucket = _bucket(clock, [])
    bucket.take()
    clock.now += 3600
    for _ in range(3):
        bucket.take()
    with pytest.raises(Refused):
        bucket.take()


def test_refusals_are_logged_at_most_every_log_every_with_the_count() -> None:
    clock = FakeClock()
    refusals: list[int] = []
    bucket = _bucket(clock, refusals, rate=1e-9, burst=0, log_every=300)
    for _ in range(4):
        with pytest.raises(Refused):
            bucket.take()
    assert refusals == [1]
    clock.now += 299
    with pytest.raises(Refused):
        bucket.take()
    assert refusals == [1]
    clock.now += 1
    with pytest.raises(Refused):
        bucket.take()
    # The four since the first line, this one included.
    assert refusals == [1, 5]


async def test_acquire_waits_for_a_token_and_says_how_long() -> None:
    clock = FakeClock()
    bucket = _bucket(clock, [], max_wait=4.0)
    assert [await bucket.acquire() for _ in range(3)] == [0.0, 0.0, 0.0]
    # Empty: half a second for the next token at 2/s.
    assert await bucket.acquire() == pytest.approx(0.5)
    assert clock.slept == [pytest.approx(0.5)]


async def test_acquire_refuses_after_max_wait_of_summed_sleeps() -> None:
    """A clock that does not move never refills: the call sleeps as long as
    it may, then is refused, logged like a refusal at once."""
    clock = FakeClock(stopped=True)
    refusals: list[int] = []
    bucket = _bucket(clock, refusals, burst=0, max_wait=4.0)
    with pytest.raises(Refused):
        await bucket.acquire()
    assert 3.5 < sum(clock.slept) <= 4.0
    assert refusals == [1]


async def test_acquire_without_max_wait_refuses_at_once() -> None:
    clock = FakeClock()
    bucket = _bucket(clock, [], burst=0)
    with pytest.raises(Refused):
        await bucket.acquire()
    assert clock.slept == []


async def test_a_busy_loop_does_not_count_against_max_wait() -> None:
    """max_wait counts the sleeps asked for; the owner measures the clock
    itself (FUTÁR's upstream timeout)."""
    clock = FakeClock(busy=2.0)
    bucket = _bucket(clock, [], burst=1, max_wait=1.0)
    bucket.take()
    assert await bucket.acquire() == pytest.approx(0.5)
    assert clock.now == pytest.approx(1002.5)


# --- OutageLog ---


def _lines(caplog) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == _LOGGER.name]


def test_an_outage_is_said_once_and_its_end_once(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER.name)
    log = upstream.OutageLog(_LOGGER)
    log.answered(None, "up")  # never down: nothing to say
    for n in range(3):
        log.failed(None, "down (%s)", n)
    log.answered(None, "up")
    log.answered(None, "up")
    log.failed(None, "down (%s)", "again")
    assert _lines(caplog) == [
        (logging.WARNING, "down (0)"),
        (logging.INFO, "up"),
        (logging.WARNING, "down (again)"),
    ]


def test_keys_have_their_own_outages(caplog) -> None:
    caplog.set_level(logging.INFO, logger=_LOGGER.name)
    log = upstream.OutageLog(_LOGGER)
    log.failed("a", "%s down", "a")
    log.failed("b", "%s down", "b")
    log.failed("a", "%s down", "a")
    log.answered("b", "%s up", "b")
    assert _lines(caplog) == [
        (logging.WARNING, "a down"),
        (logging.WARNING, "b down"),
        (logging.INFO, "b up"),
    ]
    assert list(log.down) == ["a"]


def test_ttl_and_max_entries_bound_the_outages(caplog) -> None:
    clock = FakeClock(0.0)
    log = upstream.OutageLog(_LOGGER, monotonic=clock.monotonic, ttl=100, max_entries=2)
    log.failed("a", "a down")
    clock.now = 99
    log.failed("a", "a down")
    assert len(_lines(caplog)) == 1
    # Still failing after ttl: said again.
    clock.now = 100
    log.failed("a", "a down")
    assert len(_lines(caplog)) == 2 and log.down == {"a": 100}
    # The oldest goes first.
    clock.now = 101
    log.failed("b", "b down")
    clock.now = 102
    log.failed("c", "c down")
    assert log.down == {"b": 101, "c": 102}
    # Stale keys go on any report, of any key.
    clock.now = 250
    log.answered("x", "x up")
    assert log.down == {}
