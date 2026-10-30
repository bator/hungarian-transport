"""BKK FUTÁR proxy that keeps the Open Data key in Home Assistant.

The card used to call go.bkk.hu straight from the browser with `apiKey` from
the dashboard config, so every dashboard viewer could read the key. With the
key in the config entry, the card calls `hungarian_transport.futar` instead.
Only the endpoints the card uses are allowed, and identical requests from
several cards and browsers share one upstream call.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from .cache import SharedTTLCache, TaskFactory

_LOGGER = logging.getLogger(__name__)

BKK_API = "https://go.bkk.hu/api/query/v1/ws/otp/api/where"
FUTAR_PATHS = (
    "arrivals-and-departures-for-stop.json",
    "plan-trip.json",
    "route-details.json",
    "schedule-for-stop.json",
    "search.json",
    "trip-details.json",
)
# The parameters the card sends for each endpoint (src/lib/futar.js,
# departures.js, journey.js). Anything else is dropped before the cache key
# and the upstream call alike, so a caller cannot make every request unique
# with made-up parameters, nor have a different upstream answer cached under
# a shared key.
FUTAR_PARAMS: dict[str, frozenset[str]] = {
    "arrivals-and-departures-for-stop.json": frozenset({
        "stopId", "minutesAfter", "minutesBefore", "onlyDepartures", "includeReferences", "includeVehicleFromTrip",
    }),
    "plan-trip.json": frozenset({
        "fromPlace", "toPlace", "mode", "numItineraries", "searchWindow", "date", "time", "arriveBy",
    }),
    "route-details.json": frozenset({"routeId", "includeReferences"}),
    "schedule-for-stop.json": frozenset({"stopId", "includeReferences"}),
    "search.json": frozenset({"query", "limit"}),
    "trip-details.json": frozenset({"tripId"}),
}
FUTAR_PARAM_MAX_LEN = 256
# Upstream calls (cache misses only; shared requests count once) per second
# and the burst allowed on top. Every authenticated user can call the
# service, so this caps what they can spend of the configured key.
# One destination list on a cold cache is 73 calls (departures.js: 1 board,
# 40 trip-details, 32 route-details). A dashboard opened after a restart
# loads every card's list at once (six cards: about 440 calls in a minute),
# and a refused trip-details call drops that departure from the card, so
# the burst holds several whole lists; the rate still caps steady use.
FUTAR_RATE = 5.0
FUTAR_BURST = 400
# A call over the limit waits for a token this long at most (the summed
# sleeps, not the clock) before it is refused. The card swallows a refused
# call (an incomplete list, not an error), so waiting is the better answer.
# The upstream call gets what is left of CARD_TIMEOUT_SEC after the wait.
FUTAR_TOKEN_WAIT_SEC = 4.0
# A refused call is logged at most this often, with the count since.
FUTAR_RATE_LOG_EVERY = 300
# Departure boards refresh every 15 s at the earliest on the card.
FUTAR_CACHE_TTL = 15
FUTAR_CACHE_MAX = 256
# The card gives up on the service call after this long (src/lib/futar.js,
# "BKK timeout") and treats it as an outage. The upstream call's timeout is
# the rest of that budget after the token wait, less FUTAR_MARGIN_SEC for the
# websocket round trip: a slow plan-trip.json gets every second the card still
# waits for, and the worst case, a full token wait followed by an upstream
# call that times out, still answers before the card gives up.
CARD_TIMEOUT_SEC = 20
FUTAR_MARGIN_SEC = 2.0
# Never less than this, also when the event loop was slower than the budget.
FUTAR_MIN_TIMEOUT_SEC = 1.0
# After FUTÁR rejects the key, calls fail without asking it for this long;
# then one call asks again, so a 401 given by mistake (an upstream hiccup)
# does not switch FUTÁR off until Home Assistant restarts.
REJECTED_RETRY = 15 * 60
_monotonic = time.monotonic
_sleep = asyncio.sleep


def futar_timeout(waited: float = 0.0) -> aiohttp.ClientTimeout:
    """The upstream call's timeout after waited seconds of the card's budget."""
    total = CARD_TIMEOUT_SEC - FUTAR_MARGIN_SEC - max(waited, 0.0)
    return aiohttp.ClientTimeout(total=max(total, FUTAR_MIN_TIMEOUT_SEC))


class FutarError(Exception):
    """FUTÁR could not be reached or answered with an HTTP error."""


class FutarAuthError(FutarError):
    """FUTÁR rejected the API key."""


class FutarRateLimited(FutarError):
    """Too many upstream calls; this one was not sent. A FutarError for the
    service response, but not an outage: FutarProxy logs it on its own and it
    does not touch the "unavailable / answers again" state."""


class FutarUpstreamRateLimited(FutarRateLimited):
    """FUTÁR itself refused the call as one too many (HTTP 429) for the key.
    The card answers it like the integration's own limit (an older board for
    a while), not as an outage."""


class FutarBadRequest(FutarError):
    """The request was not sent: a parameter the card never sends like that
    (longer than FUTAR_PARAM_MAX_LEN). The caller's mistake, not an outage."""


def futar_params(path: str, params: dict[str, Any] | None) -> dict[str, str]:
    """params reduced to the ones the card uses for path, as strings.
    Raises FutarBadRequest for a value longer than FUTAR_PARAM_MAX_LEN."""
    allowed = FUTAR_PARAMS.get(path, frozenset())
    out = {str(k): str(v) for k, v in (params or {}).items() if str(k) in allowed}
    for name, value in sorted(out.items()):
        if len(value) > FUTAR_PARAM_MAX_LEN:
            raise FutarBadRequest(f"parameter {name} longer than {FUTAR_PARAM_MAX_LEN} characters")
    return out


async def futar_get(
    session: aiohttp.ClientSession,
    api_key: str,
    path: str,
    params: dict[str, Any],
    timeout: aiohttp.ClientTimeout | None = None,
) -> dict:
    """One FUTÁR request, by default with the card's whole budget (futar_timeout).
    Raises FutarError; the key never appears in errors."""
    query = {str(k): str(v) for k, v in (params or {}).items() if k != "key"}
    query.update({"key": api_key, "version": "4", "appVersion": "apiary-1.0"})
    try:
        async with session.get(
            f"{BKK_API}/{path}", params=query, timeout=timeout or futar_timeout()
        ) as resp:
            if resp.status in (401, 403):
                raise FutarAuthError(f"HTTP {resp.status}")
            if resp.status == 429:
                raise FutarUpstreamRateLimited(f"HTTP {resp.status}")
            if resp.status // 100 != 2:
                raise FutarError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
        raise FutarError(type(err).__name__) from None
    if not isinstance(data, dict):
        raise FutarError("unexpected response")
    status = str(data.get("status") or "OK")
    if status in ("UNAUTHORIZED", "INVALID_API_KEY", "INVALID_KEY"):
        raise FutarAuthError(status)
    return data


class FutarProxy:
    """Short-lived cache and request sharing in front of futar_get.

    Once FUTÁR rejects the key, later calls fail with that error without
    asking upstream, until REJECTED_RETRY has passed and one call asks again.
    Saving a key in the options (the same one too) reloads the entry with a
    new proxy at once. An outage is logged once, not on every card poll.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        task_factory: TaskFactory | None = None,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._cache = SharedTTLCache(FUTAR_CACHE_TTL, FUTAR_CACHE_MAX, task_factory)
        self._rejected: str | None = None
        self._rejected_at = 0.0
        self._probing = False
        self._failing = False
        self._tokens = float(FUTAR_BURST)
        self._tokens_at = _monotonic()
        self._limited = 0
        self._limited_logged_at: float | None = None

    def _take_token(self) -> bool:
        now = _monotonic()
        self._tokens = min(float(FUTAR_BURST), self._tokens + max(0.0, now - self._tokens_at) * FUTAR_RATE)
        self._tokens_at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    async def _acquire(self) -> float:
        """A token, waiting up to FUTAR_TOKEN_WAIT_SEC for one; the seconds
        slept for it. FutarRateLimited after that."""
        slept = 0.0
        while not self._take_token():
            wait = max((1.0 - self._tokens) / FUTAR_RATE, 0.01)
            if slept + wait > FUTAR_TOKEN_WAIT_SEC:
                raise FutarRateLimited("rate limited")
            await _sleep(wait)
            slept += wait
        return slept

    def _note_rate_limited(self) -> None:
        self._limited += 1
        now = _monotonic()
        if self._limited_logged_at is None or now - self._limited_logged_at >= FUTAR_RATE_LOG_EVERY:
            _LOGGER.warning(
                "BKK FUTÁR calls over the integration's rate limit (%.0f/s, burst %d): %d refused"
                " since the last such line; cards may miss rows",
                FUTAR_RATE,
                FUTAR_BURST,
                self._limited,
            )
            self._limited = 0
            self._limited_logged_at = now

    async def _upstream(self, path: str, params: dict[str, str]) -> dict:
        started = _monotonic()
        try:
            slept = await self._acquire()
        except FutarRateLimited:
            # Counted here, once per refused request: every caller sharing
            # it gets the same error.
            self._note_rate_limited()
            raise
        # The summed sleeps, or the clock when a busy event loop took longer.
        waited = max(slept, _monotonic() - started)
        data = await futar_get(self._session, self._api_key, path, params, futar_timeout(waited))
        # Only an answer from FUTÁR itself ends an outage: a cache hit for
        # another request proves nothing, and clearing the state on one made
        # the log flap between "unavailable" and "answers again".
        if self._failing:
            self._failing = False
            _LOGGER.info("BKK FUTÁR answers again")
        return data

    async def get(self, path: str, params: dict[str, Any]) -> dict:
        # Before the rejected-key check: a call refused here never reaches
        # FUTÁR, so it cannot be the call that asks again.
        params = futar_params(path, params)
        probe = False
        if self._rejected is not None:
            if self._probing or _monotonic() - self._rejected_at < REJECTED_RETRY:
                raise FutarAuthError(self._rejected)
            # This call asks again; the others keep failing fast meanwhile.
            # Only another rejection starts a new REJECTED_RETRY: after a
            # timeout or our own rate limit, the next call asks again.
            probe = self._probing = True
        key = (path, tuple(sorted(params.items())))
        try:
            data = await self._cache.get(key, lambda: self._upstream(path, params))
        except FutarUpstreamRateLimited as err:
            # FUTÁR answers, but not this key for a while: not an outage
            # either. Said once, until FUTÁR answers a call again.
            if not self._failing:
                self._failing = True
                _LOGGER.warning(
                    "BKK FUTÁR refuses calls as too many for the configured key (%s); "
                    "not logged again until it answers",
                    err,
                )
            raise
        except FutarRateLimited:
            # Our own limit, not FUTÁR failing: no outage state, no "answers again".
            raise
        except FutarAuthError as err:
            if self._rejected is None:
                _LOGGER.warning("BKK FUTÁR rejected the configured key (%s)", err)
            self._rejected = str(err)
            self._rejected_at = _monotonic()
            raise
        except FutarError as err:
            if not self._failing:
                self._failing = True
                _LOGGER.warning(
                    "BKK FUTÁR unavailable (%s); not logged again until it answers", err
                )
            raise
        finally:
            if probe:
                self._probing = False
        if self._rejected is not None:
            _LOGGER.info("BKK FUTÁR accepts the configured key again")
            self._rejected = None
        return data
