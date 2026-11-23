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
from collections.abc import Awaitable, Callable, Hashable
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
# FUTÁR's share of the card's budget for a request a fallback can answer
# while FUTÁR is failing (FutarProxy's `fallback`, which reserved a token of
# its own limit for it first: its reserve): a departure board answers within
# a second or two, so one that has not answered by then is the outage going
# on, and the rest of the card's 20 s is the fallback's (its `timeout`,
# mavplusz_board's PLANNER_TIMEOUT): the token wait, this and the fallback
# still answer before the card gives up. FUTÁR is cut short only while its
# outage stands (it failed, or answered HTTP 429, and has not answered
# since), and only when the
# token wait left room for this and the fallback both; otherwise nothing is
# reserved and FUTÁR keeps its whole budget (a slow answer is still an
# answer), as it does where nothing else could answer (a stop the fallback
# does not know, the fallback backing off or out of tokens). While FUTÁR
# answers, a request that then fails fast (no connection, an HTTP error, a
# rejected key) still has the fallback, with a token the fallback takes
# then; one that hangs for FUTÁR's whole budget has none, and starts the
# outage for the requests after it. The fallback is asked after FUTÁR failed
# only while its answer can still reach the card (FutarProxy._fall_back),
# whatever budget FUTÁR had: within FUTAR_FALLBACK_SLACK_SEC of the card's
# deadline less FUTAR_MARGIN_SEC, so a fallback FUTÁR was cut short for after
# a full token wait is not dropped for an event loop a little slower than
# asked (it eats into the margin).
FUTAR_FALLBACK_TIMEOUT_SEC = 8.0
FUTAR_FALLBACK_SLACK_SEC = 0.5
# While FUTÁR's outage stands, a request the fallback can answer (its
# reserve gives a token) asks the fallback first and FUTÁR not at all: a
# dashboard's every poll waited up to FUTAR_FALLBACK_TIMEOUT_SEC for a FUTÁR
# that was down, holding the fallback's tokens meanwhile, and FUTÁR's whole
# 18 s once they were used up. FUTÁR is asked by one request at most every
# FUTAR_OUTAGE_PROBE_SEC (the probe, as REJECTED_RETRY's after a rejected
# key), FUTÁR first as before (cut short, the fallback after it); its answer
# ends the outage, and every request asks FUTÁR first again. A probe for an
# answer the fallback holds in its caches (its cached) is answered from
# there at once, and asks FUTÁR behind it with its whole budget, nobody
# waiting for it (FutarProxy._probe_behind): cut short, FUTÁR had kept that
# card waiting 8 s for a FUTÁR most likely still down, and the cached answer
# could expire meanwhile, its request then refused for the share the
# dashboard used up. FUTÁR refusing the key's calls as too many (HTTP 429)
# starts the outage as well: the fallback first keeps the load off a FUTÁR
# that refuses it, and the probe asks again. It is logged apart from an
# outage (its own line, _RATE_LIMITED), so FUTÁR failing after it is still
# warned about. A request the
# fallback has no answer for (a stop it does not know, its share used up, an
# answer it could not give) asks FUTÁR as before, with what is left of the
# card's budget: nothing else could answer it. Any request sent to FUTÁR
# meanwhile (search, route details: paths the fallback does not serve) puts
# the next probe off as well: it asks FUTÁR all the same. So once the
# planner's share is used up, every board and trip detail of every card goes
# to FUTÁR with its whole budget, no probe then. They are not capped beyond
# FUTÁR's rate limit and the card's own gate (4 to 24 calls under way per
# dashboard): the proxy cannot tell a slow FUTÁR from a hanging one before
# the budget runs out, and a slow one still answers them, which a cap on the
# requests under way would refuse.
FUTAR_OUTAGE_PROBE_SEC = 30
# FUTÁR failing again within this long of its last "unavailable" or
# "answers again" line is not said at once, only if it still fails after
# that (upstream.OutageLog's settle): a FUTÁR failing every few seconds and
# answering in between logged a pair of lines every few seconds.
FUTAR_OUTAGE_LOG_SETTLE_SEC = 300
# The latest a fallback's answer may come, in seconds after the card asked.
_FALLBACK_DEADLINE_SEC = CARD_TIMEOUT_SEC - FUTAR_MARGIN_SEC + FUTAR_FALLBACK_SLACK_SEC
# FUTÁR's outage log keys: None its failing (no answer, an HTTP error), this
# its refusing the key's calls as too many (HTTP 429). Either starts the
# outage (the fallback first, the probe); each is warned about, and said to
# end, on its own.
_RATE_LIMITED = "rate limited"


class FallbackTicket(Protocol):
    """A token the fallback reserved for one answer (FutarFallback.reserve)."""

    def release(self) -> None:
        """Gives the token back unless an answer spent it; idempotent."""


class FutarFallback(Protocol):
    """Another source for some of FUTÁR's answers (mavplusz_board.PlannerBoards).

    answer(path, params, ticket) is FUTÁR's answer for path in FUTÁR's own
    shape, or None when it has none; it never raises but for cancellation,
    and its request spends ticket's token when there is one. can_answer(path,
    params) says beforehand whether answer() may give one; reserve(path,
    params) is that and the token for it (answers sent in one request may
    share one), or None. timeout is the seconds
    answer() takes at most. reserve's `lasting` is the seconds from now
    before answer() is asked at the latest: an answer the fallback's caches
    hold needs no token only if it is still there by then. cached(path,
    params) says whether answer() would answer now from the fallback's
    caches, asking nothing. learn(path, params, data) is handed every answer
    FUTÁR gives (it reads what the fallback needs later, and never raises)."""

    paths: frozenset[str]
    timeout: float

    async def answer(
        self, path: str, params: dict[str, str], ticket: FallbackTicket | None = None
    ) -> dict[str, Any] | None: ...

    def can_answer(self, path: str, params: dict[str, str]) -> bool: ...

    def cached(self, path: str, params: dict[str, str]) -> bool: ...

    def reserve(self, path: str, params: dict[str, str], lasting: float = 0.0) -> FallbackTicket | None: ...

    def learn(self, path: str, params: dict[str, str], data: Any) -> None: ...


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


class FutarKeyRejected(FutarAuthError):
    """The key FUTÁR rejected, said without asking FUTÁR (within
    REJECTED_RETRY of the rejection): this call took no token."""


class FutarRateLimited(FutarError):
    """Too many upstream calls; this one was not sent. A FutarError for the
    service response, but not an outage: FutarProxy logs it on its own and it
    does not touch the "unavailable / answers again" state."""


class FutarUpstreamRateLimited(FutarRateLimited):
    """FUTÁR itself refused the call as one too many (HTTP 429) for the key.
    The card answers it like the integration's own limit (an older board for
    a while), not as an outage. The proxy asks its fallback first after one,
    as in an outage (FUTAR_OUTAGE_PROBE_SEC), and logs it on its own."""


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


class _Callers:
    """The callers of one request key in FutarProxy.get since its last
    failed request, and the fallback's tokens reserved for the requests
    they saw fail, released when the last of them is done."""

    def __init__(self) -> None:
        self.count = 0
        self.tickets: list[FallbackTicket] = []


class _FutarToken:
    """A token of FUTÁR's limit taken ahead in its outage, for the fallback
    asked first (FutarProxy._fallback_first): the fallback's answer spends
    it, else the FUTÁR request it is then handed to (use), else it goes
    back (give_back) once its callers are done (_First): no request was
    sent for it (they joined one under way, or the key was rejected
    meanwhile)."""

    def __init__(self, rate: upstream.TokenBucket) -> None:
        self._rate = rate
        self.held = True

    def use(self) -> bool:
        """Whether the token was still held (now spent by the caller's request)."""
        held, self.held = self.held, False
        return held

    def give_back(self) -> None:
        if self.held:
            self.held = False
            self._rate.refund()


class _First:
    """The fallback asked first for one request key in FUTÁR's outage
    (FutarProxy._fallback_first), shared by the callers asking at once: its
    task, the callers awaiting or using its answer (waiters: each until it
    is done, its FUTÁR request after it included), and the token of FUTÁR's
    limit taken for it. The token is the task's while it runs: its fallback
    request is sent on it, whoever is left to read the answer (before, the
    caller that started it gave it back on leaving meanwhile, and the
    fallback was asked on no token of FUTÁR's). Then the answer spends it, else the FUTÁR request a waiter
    hands it to (_upstream), else it goes back once the task and every
    waiter are done: no request was sent for it."""

    def __init__(self) -> None:
        self.task: asyncio.Future[Any] | None = None
        self.waiters = 0
        self.token: _FutarToken | None = None

    def leave(self) -> None:
        """A waiter is done."""
        self.waiters -= 1
        if not self.waiters and self.task is not None and self.task.done() and self.token is not None:
            self.token.give_back()


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
    (falls_back) and the fallback can (its reserve, or can_answer), in time
    for the card; FUTÁR has FUTAR_FALLBACK_TIMEOUT_SEC at most where the
    fallback reserved a token for its answer beforehand, which it does only
    while FUTÁR's outage stands (_reserve); meanwhile the fallback is asked
    first, and FUTÁR by one probe every FUTAR_OUTAGE_PROBE_SEC
    (_fallback_first), behind the fallback's answer where its caches hold
    one (_probe_behind). When it has no answer either, FUTÁR's error stands.
    A call failing fast on a rejected key, or answered by the fallback
    first, takes a token of FUTÁR's limit before the fallback is asked; one
    that asks FUTÁR after the fallback first (no answer) asks it with that
    token, so every request takes one at most. The callers of one key
    asking at once in FUTÁR's outage share the fallback asked first (one
    token for them all), and a key FUTÁR's cache answers, or whose FUTÁR
    request is under way, is shared as outside the outage, for no token;
    a token taken ahead that no request spent goes back. `on_accepted` is
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
        self._task_factory: TaskFactory = task_factory or asyncio.ensure_future
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
        # FUTÁR's "unavailable / answers again" state (key None), and its
        # refusing the key's calls (_RATE_LIMITED): either is its outage.
        self._outages = upstream.OutageLog(_LOGGER, monotonic=monotonic, settle=FUTAR_OUTAGE_LOG_SETTLE_SEC)
        # When a request was last sent to FUTÁR, or failed, and whether a
        # probe of its outage is under way (_fallback_first).
        self._futar_asked_at = -1e18
        self._outage_probing = False
        # The probe asking FUTÁR behind the fallback's cached answer, and its
        # request key, while it runs (_probe_behind).
        self._probe_task: asyncio.Future[Any] | None = None
        self._probe_key: Hashable | None = None
        # Request key -> its callers in get() since its last failed request
        # (_Callers): the callers of a failed request hold the fallback's
        # token reserved for it until the last of them is done.
        self._callers: dict[Hashable, _Callers] = {}
        # Request key -> the fallback asked first for it in FUTÁR's outage
        # (_fallback_first, _First), shared by the callers asking at once,
        # as one FUTÁR request is outside the outage.
        self._firsts: dict[Hashable, _First] = {}

    async def _upstream(
        self,
        path: str,
        params: dict[str, str],
        key: Hashable,
        spent: float = 0.0,
        fallback: bool = True,
        token: _FutarToken | None = None,
    ) -> dict:
        """FUTÁR's answer, after spent seconds of the card's budget went
        elsewhere (the fallback asked first); with fallback False, nothing
        is reserved for one (it was asked already). With token still held,
        the request spends FUTÁR's token _fallback_first took, and takes
        none here."""
        started = self._monotonic()
        # Refused here, once per refused request (and counted once): every
        # caller sharing it gets the same error. Not for a request that took
        # its token before the fallback was asked first: one request, one
        # token; taking a second one here spent FUTÁR's limit twice as fast
        # in its outage (every card request the planner could not take, its
        # share used up), so its requests waited, then were refused, sooner.
        slept = 0.0 if token is not None and token.use() else await self._rate.acquire()
        # The summed sleeps, or the clock when a busy event loop took longer.
        waited = max(slept, self._monotonic() - started) + spent
        rejections = self._rejections
        timeout = futar_timeout(waited)
        # FUTÁR is cut short only with the fallback's token in hand: without
        # it the fallback might refuse once FUTÁR has timed out (the other
        # requests of a dashboard took its share meanwhile).
        ticket = self._reserve(path, params, waited) if fallback else None
        if ticket is not None:
            timeout = aiohttp.ClientTimeout(total=min(timeout.total, FUTAR_FALLBACK_TIMEOUT_SEC))
        self._futar_asked_at = self._monotonic()
        try:
            data = await futar_get(self._session, self._api_key, path, params, timeout, executor=self._executor)
        except FutarError as err:
            self._futar_asked_at = self._monotonic()
            # The callers that saw this failure: those after it are another
            # request's, and do not hold its token.
            callers = self._callers.pop(key, None)
            if ticket is not None:
                if callers is not None and callers.count:
                    # For get()'s fallback (every caller sharing this request
                    # sees it); the last of them to finish releases it.
                    err.fallback_ticket = ticket  # type: ignore[attr-defined]
                    callers.tickets.append(ticket)
                else:
                    # Every caller has gone (cancelled): nobody falls back.
                    ticket.release()
            raise
        except BaseException:
            if ticket is not None:
                ticket.release()
            raise
        if ticket is not None:
            ticket.release()
        if self._fallback is not None:
            self._fallback.learn(path, params, data)
        # Only an answer from FUTÁR itself ends an outage: a cache hit for
        # another request proves nothing, and clearing the state on one made
        # the log flap between "unavailable" and "answers again".
        self._outages.answered(None, "BKK FUTÁR answers again")
        self._outages.answered(_RATE_LIMITED, "BKK FUTÁR takes the configured key's calls again")
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

    def _reserve(self, path: str, params: dict[str, str], waited: float) -> FallbackTicket | None:
        """The fallback's token for a request FUTÁR is cut short for: only
        while FUTÁR's outage stands (it failed, or answered HTTP 429, and has
        not answered since),
        and only when FUTÁR's share and the fallback's still answer before
        the card gives up after waited seconds of token wait; else None, and
        FUTÁR keeps its whole budget."""
        fallback = self._fallback
        if fallback is None or path not in fallback.paths or not self._down():
            return None
        if waited + FUTAR_FALLBACK_TIMEOUT_SEC + fallback.timeout > _FALLBACK_DEADLINE_SEC:
            return None
        # A cached answer is the fallback's for no token only if it is still
        # there once FUTÁR has failed: FUTÁR's cut, then the fallback's time.
        return fallback.reserve(path, params, FUTAR_FALLBACK_TIMEOUT_SEC + fallback.timeout)

    def _down(self) -> bool:
        """Whether FUTÁR's outage stands: it failed, or refused the key's
        calls as too many, and has not answered since."""
        return self._outages.is_down(None) or self._outages.is_down(_RATE_LIMITED)

    @property
    def tokens(self) -> float:
        """The tokens of FUTÁR's rate limit there are now (it refills with
        the clock, and takes none): what a request spent of it is the
        difference across it, which the tests count."""
        return self._rate.tokens()

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
        started = self._monotonic()
        key = (path, tuple(sorted(params.items())))
        # The callers of key until each is done (its fallback included): the
        # fallback's token reserved for a request goes back when the last of
        # the callers that saw it fail is, or at once when none is left when
        # FUTÁR fails (_upstream).
        callers = self._callers.get(key)
        if callers is None:
            callers = self._callers[key] = _Callers()
        callers.count += 1
        # While FUTÁR's outage stands: the fallback first, FUTÁR asked by
        # the probe (one at a time, FUTAR_OUTAGE_PROBE_SEC after FUTÁR was
        # last asked) and where the fallback has no answer. Not for a key
        # FUTÁR's cache answers, or whose FUTÁR request is under way: those
        # are shared as outside the outage, for no token (the fallback
        # first took one for each caller, and one each of the fallback's).
        # The probe's request behind the fallback's answer is not one to
        # join: nobody waits for its whole budget.
        probe = first = False
        if (
            self._in_outage(path)
            and not (self._cache.pending(key) and key != self._probe_key)
            and not self._cache.fresh(key)[0]
        ):
            if not self._outage_probing and self._monotonic() - self._futar_asked_at >= FUTAR_OUTAGE_PROBE_SEC:
                if self._fallback is not None and self._fallback.cached(path, params):
                    self._probe_behind(path, params, key)
                    first = True
                else:
                    probe = self._outage_probing = True
            else:
                first = True
        tried = False
        token: _FutarToken | None = None
        joined: _First | None = None
        try:
            try:
                if first:
                    joined = self._join_first(path, params, key)
                    data, tried, token = await asyncio.shield(joined.task)  # type: ignore[arg-type]
                    if data is not None:
                        return data
                return await self._get(path, params, key, self._monotonic() - started, not tried, token)
            except FutarError as err:
                if tried:
                    # The fallback was asked first and had no answer.
                    raise
                ticket: FallbackTicket | None = getattr(err, "fallback_ticket", None)
                data = await self._fall_back(err, path, params, started, ticket)
                if data is None:
                    raise
                return data
        finally:
            if probe:
                self._outage_probing = False
            if joined is not None:
                # FUTÁR's token taken for it goes back if no request spent it.
                joined.leave()
            callers.count -= 1
            if not callers.count:
                if self._callers.get(key) is callers:
                    del self._callers[key]
                for ticket in callers.tickets:
                    ticket.release()

    def _in_outage(self, path: str) -> bool:
        """Whether FUTÁR's outage stands (it failed, and has not answered
        since) for a path the fallback serves; not while the key is
        rejected (that fails fast, and falls back, on its own)."""
        fallback = self._fallback
        return (
            fallback is not None
            and path in fallback.paths
            and self._rejected is None
            and self._down()
        )

    def _join_first(self, path: str, params: dict[str, str], key: Hashable) -> _First:
        """_fallback_first for key, shared by the callers asking at once:
        one token of FUTÁR's limit and one of the fallback's for them all,
        as one FUTÁR request outside the outage. The caller is one of its
        waiters until it leave()s (get's finally); awaiting its task under
        asyncio.shield, a caller giving up does not cancel it for the
        others. A task that is done already (its entry goes on the loop's
        next turn) is not joined: its answer was the callers' before."""
        first = self._firsts.get(key)
        if first is None or first.task is None or first.task.done():
            first = self._firsts[key] = _First()
            task = first.task = self._task_factory(self._fallback_first(path, params, first))

            def done(_task: asyncio.Future[Any], first: _First = first) -> None:
                if self._firsts.get(key) is first:
                    del self._firsts[key]

            task.add_done_callback(done)
        first.waiters += 1
        return first

    def _probe_behind(self, path: str, params: dict[str, str], key: Hashable) -> None:
        """The probe of FUTÁR's outage for a request the fallback answers
        from its caches: the caller is answered from there (the fallback
        first), and FUTÁR is asked behind it, with its whole budget and a
        token of FUTÁR's limit of its own, as any request sent to it; its
        answer goes in FUTÁR's cache and ends the outage, its failure is
        logged as any. Nobody waits for it: the callers of key meanwhile
        ask the fallback first, not join it (get). One at a time, as the
        probe (_outage_probing)."""
        self._outage_probing = True
        self._probe_key = key

        async def probe() -> None:
            try:
                await self._get(path, params, key, 0.0, False)
            except FutarError:
                pass  # said by _get (the outage, a rejected key), as for any request

        task = self._probe_task = self._task_factory(probe())

        def done(_task: asyncio.Future[Any]) -> None:
            if self._probe_task is task:
                self._probe_task = None
                self._probe_key = None
                self._outage_probing = False

        task.add_done_callback(done)

    async def _fallback_first(
        self, path: str, params: dict[str, str], first: _First
    ) -> tuple[dict[str, Any] | None, bool, _FutarToken | None]:
        """The fallback's answer, asked before FUTÁR during its outage,
        whether it was asked, and FUTÁR's token taken for it, if one was:
        not where it cannot answer (can_answer, then its token: reserve),
        which then asks FUTÁR as before. FUTÁR is not asked, so FUTÁR's
        token is taken first (FutarRateLimited when there is none), as for
        a rejected key: the fallback is no way around FUTÁR's limit. Where
        FUTÁR is asked after all (no reserve, no answer), it is with that
        token (_upstream's token), not another one; where no request is
        sent for it after all (the callers joined one under way, or the key
        was rejected meanwhile), it goes back once first's waiters are done.
        It is this task's while it runs: a caller leaving while the
        fallback's request is under way does not give it back."""
        fallback = self._fallback
        if fallback is None or not fallback.can_answer(path, params):
            return None, False, None
        await self._rate.acquire()
        token = first.token = _FutarToken(self._rate)
        data: dict[str, Any] | None = None
        tried = False
        try:
            ticket = fallback.reserve(path, params)
            if ticket is not None:
                try:
                    data, tried = await fallback.answer(path, params, ticket), True
                finally:
                    ticket.release()
        finally:
            if data is not None:
                token.use()  # the fallback's answer spent it
            elif not first.waiters:
                token.give_back()  # every caller gave up meanwhile
        return data, tried, token

    async def _fall_back(
        self, err: FutarError, path: str, params: dict[str, str], started: float, ticket: FallbackTicket | None
    ) -> dict[str, Any] | None:
        """The fallback's answer after FUTÁR failed with err, or None (FUTÁR's
        error stands): not for an error it does not answer (falls_back), or
        an answer that would reach the card after it gave up (started is
        when this caller asked, so a caller that joined a request already
        under way is timed from its own ask). The fallback spends the token
        reserved before FUTÁR was asked (ticket), else takes one of its own
        if it can; what it cannot answer it says at once (None)."""
        fallback = self._fallback
        if fallback is None or not falls_back(err) or path not in fallback.paths:
            return None
        if isinstance(err, FutarKeyRejected):
            # Not asked of FUTÁR, so no token taken: the fallback takes
            # FUTÁR's, or a rejected key would make it the BKK source
            # with no limit but its own; not for one it cannot answer.
            if ticket is None and not fallback.can_answer(path, params):
                return None
            try:
                await self._rate.acquire()
            except FutarRateLimited:
                return None
        if self._monotonic() - started + fallback.timeout > _FALLBACK_DEADLINE_SEC:
            # FUTÁR had (nearly) the whole budget: the card has given up by then.
            return None
        return await fallback.answer(path, params, ticket)

    async def _get(
        self,
        path: str,
        params: dict[str, str],
        key: Hashable,
        spent: float = 0.0,
        fallback: bool = True,
        token: _FutarToken | None = None,
    ) -> dict:
        """FUTÁR's answer (cached, shared), spent seconds after the caller
        asked; fallback and token as _upstream's."""
        probe = False
        if self._rejected is not None:
            if self._probing or self._monotonic() - self._rejected_at < REJECTED_RETRY:
                raise FutarKeyRejected(self._rejected)
            # This call asks again; the others keep failing fast meanwhile.
            # Only another rejection starts a new REJECTED_RETRY: after a
            # timeout or our own rate limit, the next call asks again.
            probe = self._probing = True
        try:
            data = await self._cache.get(key, lambda: self._upstream(path, params, key, spent, fallback, token))
        except FutarUpstreamRateLimited as err:
            # FUTÁR answers, but not this key for a while: not FUTÁR failing,
            # logged on its own (FUTÁR failing after it is warned about too),
            # said once until FUTÁR answers a call again. The fallback is
            # asked first meanwhile, as in an outage (_down).
            self._outages.failed(
                _RATE_LIMITED,
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
