"""BKK FUTÁR proxy that keeps the Open Data key in Home Assistant.

The card used to call go.bkk.hu straight from the browser with `apiKey` from
the dashboard config, so every dashboard viewer could read the key. With the
key in the config entry, the card calls `hungarian_transport.futar` instead.
Only the endpoints the card uses are allowed, and identical requests from
several cards and browsers share one upstream call.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import aiohttp

from . import jsonsafe, upstream
from .cache import CallLater, SharedTTLCache, TaskFactory

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
# An answer is read into memory before it is decoded: one larger than this is
# refused while it is read, as an outage, so this caps one answer's decoding
# and FUTAR_RATE how often it can come. It is not lower because the largest
# answer the cards ask for is not small: schedule-for-stop.json of a parent
# station (departures.js, its child stops) holds the whole day of every child
# stop, about 200 bytes a stop time and 230 a trip reference, so a hub with
# 6000-8000 departures a day answers an estimated 2.5-3.5 MB. A cap of 4 MB
# would leave such a hub too little margin, and a refused answer fails the
# same way on every try. The other answers are far smaller: route-details and
# plan-trip tens to a few hundred kB, a stop's board less than that.
FUTAR_MAX_BYTES = 8 * 1024 * 1024
FUTAR_READ_CHUNK = 65536
# An answer this large or larger is decoded in the executor (_decode); a
# smaller one, nearly every answer, on the event loop, where a few ms of
# json.loads cost less than the hand-off to a thread. json.loads takes about
# 10 ms per MB on a laptop, several times that on a Raspberry Pi, and holds
# the GIL while it runs in C, so the executor alone would not keep the loop
# going: the hook _decode gives it is called back for every object, and the
# interpreter switches to the loop in between. On a 7.4 MB body that cut the
# loop's longest stall from about 107 ms to about 20 ms, for a decoding a
# fifth to a third longer (elvira._parse_board does the same).
FUTAR_EXECUTOR_BYTES = 512 * 1024
# executor(func, *args): func(*args) off the event loop (Home Assistant's
# hass.async_add_executor_job); the loop's default executor without one.
Executor = Callable[..., Awaitable[Any]]
# FUTÁR's share of the card's budget for a path a fallback serves
# (FutarProxy's `fallback`): a departure board answers within a second or
# two, so one that has not answered by then is an outage, and the rest of
# the card's 20 s is the fallback's (mavplusz_board.PLANNER_TIMEOUT): the
# token wait, this and the fallback still answer before the card gives up.
FUTAR_FALLBACK_TIMEOUT_SEC = 8.0


class FutarFallback(Protocol):
    """Another source for some of FUTÁR's answers (mavplusz_board.PlannerBoards).

    answer(path, params) is FUTÁR's answer for path in FUTÁR's own shape, or
    None when it has none; it never raises but for cancellation."""

    paths: frozenset[str]

    async def answer(self, path: str, params: dict[str, str]) -> dict[str, Any] | None: ...


def futar_timeout(waited: float = 0.0) -> aiohttp.ClientTimeout:
    """The upstream call's timeout after waited seconds of the card's budget."""
    total = CARD_TIMEOUT_SEC - FUTAR_MARGIN_SEC - max(waited, 0.0)
    return aiohttp.ClientTimeout(total=max(total, FUTAR_MIN_TIMEOUT_SEC))


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """An object of the answer, the same dict json.loads makes of it. Being
    Python, the call is where the interpreter can switch to the event loop
    while a large answer is decoded in the executor (FUTAR_EXECUTOR_BYTES)."""
    return dict(pairs)


def _decode(body: bytearray) -> Any:
    """A large answer, decoded with _object called back for every object.
    The body is emptied once it is decoded to text, before the text is
    decoded to objects (as elvira._parse_board does): it is not needed past
    there. Its nesting is checked first: this runs in Home Assistant's
    executor, whose threads die on JSON nested some 9000 deep (jsonsafe)."""
    jsonsafe.check_depth(body)
    text = body.decode(json.detect_encoding(body), "surrogatepass")
    body.clear()
    return json.loads(text, object_pairs_hook=_object)


def _in_executor(executor: Executor | None, func: Callable[..., Any], *args: Any) -> Awaitable[Any]:
    if executor is not None:
        return executor(func, *args)
    return asyncio.get_running_loop().run_in_executor(None, func, *args)


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


class FutarStatusError(FutarError):
    """FUTÁR answered HTTP 2xx, but with a status other than OK in the body
    (the card throws on those too). Not cached, and not an outage: FUTÁR
    answered, so it neither starts nor ends the "unavailable" state."""


class FutarBadRequest(FutarError):
    """The request was not sent: a parameter the card never sends like that
    (longer than FUTAR_PARAM_MAX_LEN). The caller's mistake, not an outage."""


def falls_back(err: FutarError) -> bool:
    """Whether a fallback may answer instead of FUTÁR after err: FUTÁR not
    answering (an outage, a timeout, an HTTP error), refusing the key, or
    refusing the call as one too many for it (HTTP 429). Not the
    integration's own rate limit (the fallback would take the load the
    limit holds back), a request never sent, or an error status FUTÁR gave
    for this very request."""
    if isinstance(err, (FutarAuthError, FutarUpstreamRateLimited)):
        return True
    return not isinstance(err, (FutarRateLimited, FutarStatusError, FutarBadRequest))


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
    *,
    executor: Executor | None = None,
) -> dict:
    """One FUTÁR request, by default with the card's whole budget (futar_timeout).
    An answer of FUTAR_EXECUTOR_BYTES or more is decoded in `executor`.
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
            length = getattr(resp, "content_length", None)
            if length is not None and length > FUTAR_MAX_BYTES:
                raise FutarError(f"answer over {FUTAR_MAX_BYTES} bytes")
            # One buffer, not chunks joined into a second copy of the body
            # that the chunks outlive while it is decoded.
            body = bytearray()
            async for chunk in resp.content.iter_chunked(FUTAR_READ_CHUNK):
                body += chunk
                if len(body) > FUTAR_MAX_BYTES:
                    raise FutarError(f"answer over {FUTAR_MAX_BYTES} bytes")
        if len(body) < FUTAR_EXECUTOR_BYTES:
            data = jsonsafe.loads(body)
        else:
            data = await _in_executor(executor, _decode, body)
    # RecursionError: an answer nested deeper than the decoder goes (a broken
    # or hostile one), which is no answer either, not a failed service call.
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RecursionError) as err:
        raise FutarError(type(err).__name__) from None
    if not isinstance(data, dict):
        raise FutarError("unexpected response")
    # Like the card (src/lib/futar.js): a missing status is OK, any other
    # status an error, never data to cache or a key that works.
    status = str(data.get("status") or "OK")
    if status in ("UNAUTHORIZED", "INVALID_API_KEY", "INVALID_KEY"):
        raise FutarAuthError(status)
    if status != "OK":
        raise FutarStatusError(f"status {status[:64]}")
    return data


class FutarProxy:
    """Short-lived cache and request sharing in front of futar_get.

    Once FUTÁR rejects the key, later calls fail with that error without
    asking upstream, until REJECTED_RETRY has passed and one call asks again.
    Saving a key in the options (the same one too) reloads the entry with a
    new proxy at once. An outage is logged once, not on every card poll.

    `monotonic` is the clock of the cache, the rate limit and the rejected
    key; `sleep` is the one a call waits for a token with. `burst` is the
    rate limit's (FUTAR_BURST). `executor` decodes the large answers
    (futar_get). `fallback` answers a path it serves when FUTÁR cannot
    (falls_back), FUTÁR then having FUTAR_FALLBACK_TIMEOUT_SEC at most;
    when it has no answer either, FUTÁR's error stands. `on_accepted` is
    called when FUTÁR first accepts the key, and again whenever it accepts it after rejecting it
    (the rejection was a mistake): a reauth started before this proxy (by
    the one a reload replaced) is answered by a key that works as well.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        task_factory: TaskFactory | None = None,
        call_later: CallLater | None = None,
        *,
        monotonic: upstream.Monotonic = upstream.monotonic,
        sleep: upstream.Sleep = upstream.sleep,
        burst: int = FUTAR_BURST,
        on_accepted: Callable[[], None] | None = None,
        executor: Executor | None = None,
        fallback: FutarFallback | None = None,
    ) -> None:
        self._session = session
        self._fallback = fallback
        self._executor = executor
        self._on_accepted = on_accepted
        # Whether FUTÁR has accepted the key since this proxy was made.
        self._accepted = False
        self._api_key = api_key
        self._monotonic = monotonic
        self._cache = SharedTTLCache(FUTAR_CACHE_TTL, FUTAR_CACHE_MAX, task_factory, call_later, monotonic)
        self._rejected: str | None = None
        self._rejected_at = 0.0
        # Counts rejections: an answer to a request sent before the latest
        # one says nothing about the key now.
        self._rejections = 0
        self._probing = False
        # A call over the limit waits for a token: the card swallows a
        # refused call (an incomplete list), so waiting is the better answer.
        self._rate = upstream.TokenBucket(
            FUTAR_RATE,
            burst,
            refused=lambda: FutarRateLimited("rate limited"),
            log_refused=lambda count: _LOGGER.warning(
                "BKK FUTÁR calls over the integration's rate limit (%.0f/s, burst %d): %d refused"
                " since the last such line; cards may miss rows",
                FUTAR_RATE,
                burst,
                count,
            ),
            log_every=FUTAR_RATE_LOG_EVERY,
            max_wait=FUTAR_TOKEN_WAIT_SEC,
            monotonic=monotonic,
            sleep=sleep,
        )
        # FUTÁR has one "unavailable / answers again" state (key None).
        self._outages = upstream.OutageLog(_LOGGER, monotonic=monotonic)

    async def _upstream(self, path: str, params: dict[str, str]) -> dict:
        started = self._monotonic()
        # Refused here, once per refused request (and counted once): every
        # caller sharing it gets the same error.
        slept = await self._rate.acquire()
        # The summed sleeps, or the clock when a busy event loop took longer.
        waited = max(slept, self._monotonic() - started)
        rejections = self._rejections
        timeout = futar_timeout(waited)
        if self._fallback is not None and path in self._fallback.paths:
            timeout = aiohttp.ClientTimeout(total=min(timeout.total, FUTAR_FALLBACK_TIMEOUT_SEC))
        data = await futar_get(self._session, self._api_key, path, params, timeout, executor=self._executor)
        # Only an answer from FUTÁR itself ends an outage: a cache hit for
        # another request proves nothing, and clearing the state on one made
        # the log flap between "unavailable" and "answers again".
        self._outages.answered(None, "BKK FUTÁR answers again")
        # Only a request sent after the latest rejection proves the key works
        # (again): one sent before it (a slow plan-trip.json) cleared it, so
        # the log flapped and the next call spent a 401 instead of failing fast.
        if rejections == self._rejections:
            accepted = not self._accepted
            if self._rejected is not None:
                _LOGGER.info("BKK FUTÁR accepts the configured key again")
                self._rejected = None
                accepted = True
            self._accepted = True
            if accepted and self._on_accepted is not None:
                self._on_accepted()
        return data

    @property
    def key_rejected(self) -> bool:
        """FUTÁR rejected the key and has not accepted it since (the
        handler asks for a new key also when a fallback answered)."""
        return self._rejected is not None

    def close(self) -> None:
        """The cache's expiry timer goes (entry unloaded, Home Assistant
        stopping), the fallback's too."""
        self._cache.close()
        close = getattr(self._fallback, "close", None)
        if close is not None:
            close()

    async def get(self, path: str, params: dict[str, Any]) -> dict:
        # Before the rejected-key check: a call refused here never reaches
        # FUTÁR, so it cannot be the call that asks again; nor the fallback.
        params = futar_params(path, params)
        try:
            return await self._get(path, params)
        except FutarError as err:
            if self._fallback is None or path not in self._fallback.paths or not falls_back(err):
                raise
            data = await self._fallback.answer(path, params)
            if data is None:
                raise
            return data

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        probe = False
        if self._rejected is not None:
            if self._probing or self._monotonic() - self._rejected_at < REJECTED_RETRY:
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
            self._outages.failed(
                None,
                "BKK FUTÁR refuses calls as too many for the configured key (%s); "
                "not logged again until it answers",
                err,
            )
            raise
        except FutarRateLimited:
            # Our own limit, not FUTÁR failing: no outage state, no "answers again".
            raise
        except FutarStatusError:
            # FUTÁR answered, with an error for this request: not an outage.
            raise
        except FutarAuthError as err:
            if self._rejected is None:
                _LOGGER.warning("BKK FUTÁR rejected the configured key (%s)", err)
            self._rejected = str(err)
            self._rejected_at = self._monotonic()
            self._rejections += 1
            raise
        except FutarError as err:
            self._outages.failed(
                None, "BKK FUTÁR unavailable (%s); not logged again until it answers", err
            )
            raise
        finally:
            if probe:
                self._probing = False
        return data
