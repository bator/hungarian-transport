"""Guarding a shared upstream: a token bucket and a once-per-outage log.

ELVIRA, FUTÁR and the realtime feeds are asked on behalf of every card on
every open dashboard, and any logged-in user can call the services. Each
upstream module configures these two instead of writing its own:

- TokenBucket caps the requests Home Assistant sends. What happens when it
  is empty is the owner's policy: refuse at once (take, or acquire with no
  max_wait), wait up to max_wait seconds for a token (acquire), or serve the
  copy it has (take, catching the refusal).
- OutageLog says an outage once: one warning when an upstream (or one key
  of it: a station, a feed URL) starts failing, one info line when it
  answers again, not one line per card poll.

Both run on the clock their owner was given at construction (monotonic,
and sleep where a caller waits). Nothing here needs Home Assistant.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
import logging
import time

Monotonic = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


def monotonic() -> float:
    """time.monotonic, looked up on every call (a frozen test clock patches it)."""
    return time.monotonic()


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


class TokenBucket:
    """rate tokens a second, at most burst of them, shared by every caller.

    An empty bucket raises refused() (the owner's "not sent" error). A
    refusal is counted and logged through log_refused(count) at most every
    log_every seconds, with the count since the last such line: a loop over
    the service must not become a loop in the log either.
    """

    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        refused: Callable[[], BaseException],
        log_refused: Callable[[int], None],
        log_every: float = 300,
        max_wait: float = 0.0,
        monotonic: Monotonic = monotonic,
        sleep: Sleep = sleep,
    ) -> None:
        self._rate = rate
        self._burst = burst
        self._refused = refused
        self._log_refused = log_refused
        self._log_every = log_every
        self._max_wait = max_wait
        self._monotonic = monotonic
        self._sleep = sleep
        self._tokens = float(burst)
        self._at = monotonic()
        self._count = 0
        self._logged_at: float | None = None

    def _try(self) -> bool:
        now = self._monotonic()
        self._tokens = min(float(self._burst), self._tokens + max(0.0, now - self._at) * self._rate)
        self._at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    def _refuse(self) -> None:
        self._count += 1
        now = self._monotonic()
        if self._logged_at is None or now - self._logged_at >= self._log_every:
            self._log_refused(self._count)
            self._count = 0
            self._logged_at = now
        raise self._refused()

    def take(self) -> None:
        """One token now, or refused() at once; never waits, whatever max_wait."""
        if not self._try():
            self._refuse()

    async def acquire(self) -> float:
        """One token, waiting up to max_wait seconds (the summed sleeps, not
        the clock) for it; the seconds slept. refused() after that."""
        slept = 0.0
        while not self._try():
            wait = max((1.0 - self._tokens) / self._rate, 0.01)
            if slept + wait > self._max_wait:
                self._refuse()
            await self._sleep(wait)
            slept += wait
        return slept


class OutageLog:
    """One warning when a key starts failing, one info line when it answers
    again, on logger (the upstream module's own). Key None is an upstream
    with one state.

    With ttl, a key is kept that long at most (then a still failing one is
    warned about again, and one nobody asks for any more does not stay for
    good); with max_entries, at most that many keys, the oldest dropped first.
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        monotonic: Monotonic = monotonic,
        ttl: float | None = None,
        max_entries: int | None = None,
    ) -> None:
        self._logger = logger
        self._monotonic = monotonic
        self._ttl = ttl
        self._max = max_entries
        # Key -> when its outage began.
        self.down: dict[Hashable, float] = {}

    def _purge(self) -> float:
        now = self._monotonic()
        if self._ttl is not None:
            for stale in [k for k, since in self.down.items() if now - since >= self._ttl]:
                del self.down[stale]
        return now

    def failed(self, key: Hashable, message: str, *args: object) -> None:
        """key failed: message % args as a warning, unless its outage was said."""
        now = self._purge()
        if key in self.down:
            return
        if self._max is not None:
            while self.down and len(self.down) >= self._max:
                del self.down[min(self.down, key=self.down.__getitem__)]
        self.down[key] = now
        self._logger.warning(message, *args)

    def answered(self, key: Hashable, message: str, *args: object) -> None:
        """key answered: message % args as info, if it was down."""
        self._purge()
        if self.down.pop(key, None) is not None:
            self._logger.info(message, *args)
