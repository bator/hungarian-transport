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
  answers again, not one line per card poll; with settle, an upstream
  failing and answering by turns is not a pair of lines every few seconds
  either.

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

    def tokens(self) -> float:
        """The tokens there are now (refilled with the clock); takes none,
        counts nothing."""
        now = self._monotonic()
        self._tokens = min(float(self._burst), self._tokens + max(0.0, now - self._at) * self._rate)
        self._at = now
        return self._tokens

    def available(self) -> bool:
        """Whether take() would get a token now; takes none, counts nothing."""
        return self.tokens() >= 1.0

    def refund(self) -> None:
        """A token taken for a request that was not sent goes back."""
        self._tokens = min(float(self._burst), self._tokens + 1.0)

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

    With settle, a key that fails again within settle seconds of its last
    line (the warning or the info line) is not warned about at once: FUTÁR
    failing and answering every few seconds logged a warning and an info
    line every few seconds, 58 pairs in ten minutes. Such a failure is said
    once settle seconds have passed since that line and it still stands (on
    the next failed, is_down or answered call of any key, so a log nobody
    asks has nothing to say either), and its end only if it was said: an
    outage that began within settle of the last line and ended before it was
    over is the one said before, still under way. So the lines still
    alternate, every outage lasting settle is warned about (settle late at
    most), every one warned about is said to end (at once, as without
    settle), and a key failing by turns logs a pair every settle seconds at
    most. is_down does not wait for any of it: a key is down from the
    failure that made it so until it answers, said or not.
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        monotonic: Monotonic = monotonic,
        ttl: float | None = None,
        max_entries: int | None = None,
        settle: float | None = None,
    ) -> None:
        self._logger = logger
        self._monotonic = monotonic
        self._ttl = ttl
        self._max = max_entries
        self._settle = settle
        # Key -> when its outage began.
        self.down: dict[Hashable, float] = {}
        # With settle: key -> when its last line was logged, kept for settle
        # seconds; and the keys down but not said yet -> their warning
        # (message, args, exc_info), every one of them in down.
        self._said_at: dict[Hashable, float] = {}
        self._unsaid: dict[Hashable, tuple[str, tuple[object, ...], BaseException | None]] = {}

    def _purge(self) -> float:
        now = self._monotonic()
        if self._ttl is not None:
            for stale in [k for k, since in self.down.items() if now - since >= self._ttl]:
                del self.down[stale]
                self._unsaid.pop(stale, None)
        if self._settle is not None:
            # An outage not said yet that still stands settle seconds after
            # the key's last line is said now; then the lines older than
            # settle are forgotten (they hold nothing back any more).
            for key in [k for k in self._unsaid if now - self._said_at.get(k, -1e18) >= self._settle]:
                message, args, exc_info = self._unsaid.pop(key)
                self._said_at[key] = now
                self._logger.warning(message, *args, exc_info=exc_info)
            for old in [k for k, at in self._said_at.items() if now - at >= self._settle]:
                del self._said_at[old]
        return now

    def failed(
        self, key: Hashable, message: str, *args: object, exc_info: BaseException | None = None
    ) -> None:
        """key failed: message % args as a warning, unless its outage was
        said (or, with settle, its last line is under settle seconds old:
        then said once settle has passed, if it still stands); with
        exc_info, that error's traceback too (a bug's)."""
        now = self._purge()
        if key in self.down:
            return
        if self._max is not None:
            while self.down and len(self.down) >= self._max:
                oldest = min(self.down, key=self.down.__getitem__)
                del self.down[oldest]
                self._unsaid.pop(oldest, None)
            # A key dropped here only has its outage said sooner (_purge).
            while self._said_at and key not in self._said_at and len(self._said_at) >= self._max:
                del self._said_at[min(self._said_at, key=self._said_at.__getitem__)]
        self.down[key] = now
        if key in self._said_at:
            # Within settle of its last line (_purge forgot the older ones).
            self._unsaid[key] = (message, args, exc_info)
            self._logger.debug(message, *args)
            return
        if self._settle is not None:
            self._said_at[key] = now
        self._logger.warning(message, *args, exc_info=exc_info)

    def is_down(self, key: Hashable) -> bool:
        """Whether key failed and has not answered since (within ttl, when
        there is one), its outage said or not (settle)."""
        self._purge()
        return key in self.down

    def answered(self, key: Hashable, message: str, *args: object) -> None:
        """key answered: message % args as info, if it was down and that was
        said (settle)."""
        now = self._purge()
        if self.down.pop(key, None) is None:
            return
        if self._unsaid.pop(key, None) is not None:
            # Its failure was never said: the outage said before goes on
            # answering, and so does the log.
            self._logger.debug(message, *args)
            return
        if self._settle is not None:
            self._said_at[key] = now
        self._logger.info(message, *args)
