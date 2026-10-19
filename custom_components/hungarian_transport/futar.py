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
FUTAR_RATE = 5.0
FUTAR_BURST = 60
# Departure boards refresh every 15 s at the earliest on the card.
FUTAR_CACHE_TTL = 15
FUTAR_CACHE_MAX = 256
FUTAR_TIMEOUT = aiohttp.ClientTimeout(total=20)
# After FUTÁR rejects the key, calls fail without asking it for this long;
# then one call asks again, so a 401 given by mistake (an upstream hiccup)
# does not switch FUTÁR off until Home Assistant restarts.
REJECTED_RETRY = 15 * 60
_monotonic = time.monotonic


class FutarError(Exception):
    """FUTÁR could not be reached or answered with an HTTP error."""


class FutarAuthError(FutarError):
    """FUTÁR rejected the API key."""


class FutarRateLimited(FutarError):
    """Too many upstream calls; this one was not sent."""


def futar_params(path: str, params: dict[str, Any] | None) -> dict[str, str]:
    """params reduced to the ones the card uses for path, as strings.
    Raises FutarError for a value longer than FUTAR_PARAM_MAX_LEN."""
    allowed = FUTAR_PARAMS.get(path, frozenset())
    out = {str(k): str(v) for k, v in (params or {}).items() if str(k) in allowed}
    if any(len(v) > FUTAR_PARAM_MAX_LEN for v in out.values()):
        raise FutarError("parameter too long")
    return out


async def futar_get(
    session: aiohttp.ClientSession, api_key: str, path: str, params: dict[str, Any]
) -> dict:
    """One FUTÁR request. Raises FutarError; the key never appears in errors."""
    query = {str(k): str(v) for k, v in (params or {}).items() if k != "key"}
    query.update({"key": api_key, "version": "4", "appVersion": "apiary-1.0"})
    try:
        async with session.get(
            f"{BKK_API}/{path}", params=query, timeout=FUTAR_TIMEOUT
        ) as resp:
            if resp.status in (401, 403):
                raise FutarAuthError(f"HTTP {resp.status}")
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
        self._failing = False
        self._tokens = float(FUTAR_BURST)
        self._tokens_at = _monotonic()

    def _take_token(self) -> bool:
        now = _monotonic()
        self._tokens = min(float(FUTAR_BURST), self._tokens + max(0.0, now - self._tokens_at) * FUTAR_RATE)
        self._tokens_at = now
        if self._tokens < 1.0:
            return False
        self._tokens -= 1.0
        return True

    async def _upstream(self, path: str, params: dict[str, str]) -> dict:
        if not self._take_token():
            raise FutarRateLimited("rate limited")
        return await futar_get(self._session, self._api_key, path, params)

    async def get(self, path: str, params: dict[str, Any]) -> dict:
        if self._rejected is not None:
            if _monotonic() - self._rejected_at < REJECTED_RETRY:
                raise FutarAuthError(self._rejected)
            # This call asks again; the others keep failing fast meanwhile.
            self._rejected_at = _monotonic()
        params = futar_params(path, params)
        key = (path, tuple(sorted(params.items())))
        try:
            data = await self._cache.get(key, lambda: self._upstream(path, params))
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
        if self._rejected is not None:
            _LOGGER.info("BKK FUTÁR accepts the configured key again")
            self._rejected = None
        if self._failing:
            self._failing = False
            _LOGGER.info("BKK FUTÁR answers again")
        return data
