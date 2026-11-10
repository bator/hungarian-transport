"""The FUTÁR proxy service keeps the BKK key in Home Assistant."""

import asyncio
from functools import partial
import json
from datetime import timedelta
import re
from pathlib import Path
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components import hungarian_transport as integration
from custom_components.hungarian_transport import cache as cache_mod, futar
from custom_components.hungarian_transport.const import CONF_API_KEY, DOMAIN, SERVICE_FUTAR
from custom_components.hungarian_transport.futar import BKK_API

from .test_cache import expiry_timers
from .test_upstream import FakeClock

SEARCH = f"{BKK_API}/search.json"


def _inject(monkeypatch, clock: FakeClock, **kwargs) -> FakeClock:
    """The clock (and kwargs) of the proxy the integration builds."""
    monkeypatch.setattr(
        integration,
        "FutarProxy",
        partial(futar.FutarProxy, monotonic=clock.monotonic, sleep=clock.sleep, **kwargs),
    )
    return clock


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    return _inject(monkeypatch, FakeClock())


async def _setup(hass, key="secret-key") -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options={CONF_API_KEY: key})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _call(hass, path="search.json", params=None):
    return hass.services.async_call(
        DOMAIN,
        SERVICE_FUTAR,
        {"path": path, "params": params or {"query": "Keleti"}},
        blocking=True,
        return_response=True,
    )


async def test_no_service_without_key(hass) -> None:
    await _setup(hass, key="")
    assert not hass.services.has_service(DOMAIN, SERVICE_FUTAR)


async def test_proxies_with_stored_key(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {"list": [1]}})
    await _setup(hass)

    resp = await _call(hass, params={"query": "Keleti", "key": "from-browser", "limit": 5})

    assert resp == {"data": {"status": "OK", "data": {"list": [1]}}}
    query = aioclient_mock.mock_calls[0][1].query
    assert query["key"] == "secret-key"
    assert query["query"] == "Keleti"
    assert query["limit"] == "5"
    assert query["version"] == "4"


async def test_unknown_params_are_dropped_and_share_the_cache(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    await _setup(hass)

    for i in range(5):
        await _call(hass, params={"query": "Keleti", "nonce": str(i)})
    assert aioclient_mock.call_count == 1
    assert "nonce" not in aioclient_mock.mock_calls[0][1].query


async def test_upstream_calls_are_rate_limited(hass, aioclient_mock, monkeypatch, caplog) -> None:
    """A stopped clock never refills: past the burst a call waits FUTAR_TOKEN_WAIT_SEC
    (here without really sleeping), then is refused, with its own log line, not as an outage."""
    _inject(monkeypatch, FakeClock(stopped=True))
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    await _setup(hass)

    results = [await _call(hass, params={"query": f"q{i}"}) for i in range(futar.FUTAR_BURST + 3)]
    assert aioclient_mock.call_count == futar.FUTAR_BURST
    # Its own error code: the card keeps quiet on it, unlike an outage.
    assert results[-3:] == [{"data": None, "error": "futar_rate_limited", "detail": "rate limited"}] * 3
    assert json.dumps(results[-1]) == '{"data": null, "error": "futar_rate_limited", "detail": "rate limited"}'
    messages = [r.getMessage() for r in caplog.records if r.name.endswith(".futar")]
    assert len(messages) == 1 and "rate limit" in messages[0]
    assert not any("unavailable" in m or "answers again" in m for m in messages)


async def test_shares_and_caches_identical_requests(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    await _setup(hass)

    await asyncio.gather(_call(hass), _call(hass), _call(hass))
    await _call(hass)
    assert aioclient_mock.call_count == 1

    await _call(hass, params={"query": "Deli"})
    assert aioclient_mock.call_count == 2


async def test_cached_answers_go_when_their_ttl_ends(hass, aioclient_mock, monkeypatch, clock) -> None:
    """Like ELVIRA's boards: once the dashboards close nobody polls again,
    so the answers go at the end of their TTL, and the timer goes with the
    entry or when Home Assistant stops."""
    caches = []
    monkeypatch.setattr(
        futar, "SharedTTLCache", lambda *args: caches.append(cache_mod.SharedTTLCache(*args)) or caches[-1]
    )
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = await _setup(hass)

    await _call(hass)
    (cache,) = caches
    assert len(cache) == 1 and len(expiry_timers(hass)) == 1
    clock.now += futar.FUTAR_CACHE_TTL
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=futar.FUTAR_CACHE_TTL), fire_all=True)
    await hass.async_block_till_done()
    assert len(cache) == 0 and expiry_timers(hass) == []

    await _call(hass)
    assert len(expiry_timers(hass)) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert expiry_timers(hass) == []

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _call(hass)
    assert len(expiry_timers(hass)) == 1
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert expiry_timers(hass) == []


async def test_only_card_endpoints_are_allowed(hass, aioclient_mock) -> None:
    await _setup(hass)

    with pytest.raises(vol.Invalid):
        await _call(hass, path="../admin.json")
    assert aioclient_mock.call_count == 0


REJECTED = {"data": None, "error": "futar_auth", "detail": "HTTP 403"}


async def test_rejected_key_is_an_error_in_the_response(hass, aioclient_mock) -> None:
    """Not an exception: the websocket API would log one at ERROR on every
    card poll. The key never appears in the response."""
    aioclient_mock.get(SEARCH, status=403)
    await _setup(hass)

    resp = await _call(hass)
    assert resp == REJECTED
    assert "secret-key" not in str(resp)

    # A rejected key starts one reauth flow, however many cards poll, and
    # is not sent upstream again while the reauth is pending.
    await hass.async_block_till_done()
    assert await _call(hass, params={"query": "Deli"}) == REJECTED
    assert aioclient_mock.call_count == 1
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH
    assert flows[0]["step_id"] == "reauth_confirm"


async def test_service_follows_options(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    entry = await _setup(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_FUTAR)

    hass.config_entries.async_update_entry(entry, options={CONF_API_KEY: ""})
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_FUTAR)

    hass.config_entries.async_update_entry(entry, options={CONF_API_KEY: "other"})
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_FUTAR)
    await _call(hass)
    assert aioclient_mock.mock_calls[-1][1].query["key"] == "other"


async def test_new_key_after_rejection_reaches_futar(hass, aioclient_mock) -> None:
    """The fail-fast state belongs to the proxy of the rejected key: the
    reload with a new key asks FUTÁR again."""
    aioclient_mock.get(SEARCH, status=401)
    entry = await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    hass.config_entries.async_update_entry(entry, options={CONF_API_KEY: "fresh-key"})
    await hass.async_block_till_done()
    assert (await _call(hass))["data"]["status"] == "OK"
    assert aioclient_mock.mock_calls[-1][1].query["key"] == "fresh-key"


async def test_options_key_only(hass, aioclient_mock) -> None:
    """No version stored the key in entry.data; a stray one there is ignored."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, data={CONF_API_KEY: "stray"}, options={}
    )
    assert await async_setup_component(hass, "http", {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_FUTAR)


async def test_rejection_is_asked_again_after_a_while(hass, aioclient_mock, clock) -> None:
    """A 401 FUTÁR gave by mistake must not switch FUTÁR off until a restart:
    after REJECTED_RETRY one call asks again."""
    aioclient_mock.get(SEARCH, status=401)
    await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    clock.now += futar.REJECTED_RETRY - 1
    assert (await _call(hass, params={"query": "Deli"}))["error"] == "futar_auth"
    assert aioclient_mock.call_count == 0

    clock.now += 2
    assert (await _call(hass, params={"query": "Deli"}))["data"]["status"] == "OK"
    assert aioclient_mock.call_count == 1
    # Accepted again: no more fail-fast.
    assert (await _call(hass, params={"query": "Nyugati"}))["data"]["status"] == "OK"


async def test_failed_probe_does_not_restart_the_rejection_window(
    hass, aioclient_mock, monkeypatch
) -> None:
    """The call that asks again after REJECTED_RETRY can fail for another
    reason: a refused parameter, our own rate limit, a FUTÁR outage. None of
    them is a rejection, so the next call asks again instead of failing fast
    for another REJECTED_RETRY."""
    # One token: the 401 and the 503 below each take it.
    clock = _inject(monkeypatch, FakeClock(), burst=1)
    aioclient_mock.get(SEARCH, status=401)
    await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"
    clock.now += futar.REJECTED_RETRY + 1

    # Refused before any request: not the call that asks again.
    too_long = await _call(hass, params={"query": "x" * (futar.FUTAR_PARAM_MAX_LEN + 1)})
    assert too_long["error"] == "futar_bad_request"
    assert aioclient_mock.call_count == 1

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, status=503)
    assert (await _call(hass, params={"query": "A"}))["error"] == "futar_unavailable"
    assert aioclient_mock.call_count == 1

    # The bucket is empty and the clock stopped: refused after the wait.
    clock.stopped = True
    assert (await _call(hass, params={"query": "B"}))["error"] == "futar_rate_limited"
    clock.stopped = False
    clock.now += 1  # a token again

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    assert (await _call(hass, params={"query": "C"}))["data"]["status"] == "OK"
    assert aioclient_mock.call_count == 1


async def test_a_probe_rejected_again_restarts_the_window(hass, aioclient_mock, clock) -> None:
    aioclient_mock.get(SEARCH, status=401)
    await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"
    clock.now += futar.REJECTED_RETRY + 1
    assert (await _call(hass, params={"query": "A"}))["error"] == "futar_auth"
    assert aioclient_mock.call_count == 2

    clock.now += futar.REJECTED_RETRY - 1
    assert (await _call(hass, params={"query": "B"}))["error"] == "futar_auth"
    assert aioclient_mock.call_count == 2


async def test_same_key_saved_in_options_asks_futar_again(hass, aioclient_mock) -> None:
    """The futar_auth advice: saving the key in the options (the same one,
    once FUTÁR takes it again) reloads the entry and its proxy."""
    aioclient_mock.get(SEARCH, status=401)
    entry = await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_API_KEY: "secret-key"}
    )
    await hass.async_block_till_done()
    assert (await _call(hass))["data"]["status"] == "OK"


async def test_outage_is_logged_once_not_per_poll(hass, aioclient_mock, caplog) -> None:
    aioclient_mock.get(SEARCH, status=503)
    await _setup(hass)

    for query in ("A", "B", "C"):
        resp = await _call(hass, params={"query": query})
        assert resp == {"data": None, "error": "futar_unavailable", "detail": "HTTP 503"}
    records = [r for r in caplog.records if r.name.endswith(".futar")]
    assert [r.levelno for r in records] == [logging.WARNING]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    caplog.clear()
    await _call(hass, params={"query": "D"})
    await _call(hass, params={"query": "E"})
    assert [r.getMessage() for r in caplog.records if r.name.endswith(".futar")] == [
        "BKK FUTÁR answers again"
    ]


async def test_worst_case_answers_before_the_card_gives_up() -> None:
    """A full token wait followed by an upstream call that times out must
    answer inside the card's own timeout, or a cold start with an empty
    bucket shows "BKK timeout" (an outage) instead of a refused call."""
    card = (Path(__file__).parents[1] / "src" / "lib" / "futar.js").read_text(encoding="utf-8")
    match = re.search(r"new Error\('BKK timeout'\)\)\s*,\s*(\d+)\s*\)", card)
    assert match, "the card's FUTÁR timeout moved: update CARD_TIMEOUT_SEC"
    assert int(match.group(1)) == futar.CARD_TIMEOUT_SEC * 1000

    # An empty bucket on a stopped clock never refills: the call waits as
    # long as it may.
    clock = FakeClock(stopped=True)
    proxy = futar.FutarProxy(None, "secret-key", monotonic=clock.monotonic, sleep=clock.sleep, burst=0)
    with pytest.raises(futar.FutarRateLimited):
        await proxy.get("plan-trip.json", {"fromPlace": "a", "toPlace": "b"})
    slept = clock.slept
    worst = sum(slept) + futar.futar_timeout(sum(slept)).total
    assert sum(slept) <= futar.FUTAR_TOKEN_WAIT_SEC
    # Room for the websocket round trip and the event loop.
    assert futar.FUTAR_MARGIN_SEC >= 2
    assert worst <= futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    longest_wait = futar.FUTAR_TOKEN_WAIT_SEC
    assert longest_wait + futar.futar_timeout(longest_wait).total <= futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC


@pytest.mark.parametrize(
    ("drained", "busy", "timeout"),
    [(False, 0.0, 18.0), (True, 0.0, 17.8), (True, 2.0, 15.8)],
    ids=["no-wait", "token-wait", "busy-loop"],
)
async def test_upstream_timeout_is_what_is_left_of_the_card_budget(monkeypatch, drained, busy, timeout) -> None:
    """A fixed 12 s timeout failed a plan-trip.json answering in 12-20 s,
    which the card (20 s) still waits for. The upstream call gets the card's
    budget less the token wait (the clock when the loop was slower) and the
    margin."""
    clock = FakeClock(busy=busy)
    seen: list[float] = []

    async def fake_get(session, api_key, path, params, timeout=None):
        seen.append(timeout.total)
        return {"status": "OK", "data": {}}

    monkeypatch.setattr(futar, "futar_get", fake_get)
    proxy = futar.FutarProxy(None, "secret-key", monotonic=clock.monotonic, sleep=clock.sleep, burst=1)
    if drained:
        # The one token goes: the next call waits 0.2 s for one at 5/s.
        await proxy.get("search.json", {"query": "first"})
        seen.clear()
    await proxy.get("plan-trip.json", {"fromPlace": "a", "toPlace": "b"})
    assert seen == [pytest.approx(timeout)]
    assert futar.futar_timeout().total == futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    assert futar.futar_timeout(30).total == futar.FUTAR_MIN_TIMEOUT_SEC


async def test_cache_hit_does_not_end_an_outage(hass, aioclient_mock, caplog) -> None:
    """Only an upstream answer clears the outage state: a cached board of
    another request used to log "answers again" between two failures."""
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    await _setup(hass)
    assert (await _call(hass, params={"query": "cached"}))["data"]["status"] == "OK"

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, status=503)
    caplog.clear()
    assert (await _call(hass, params={"query": "A"}))["error"] == "futar_unavailable"
    # Served from the cache, FUTÁR not asked: still an outage.
    assert (await _call(hass, params={"query": "cached"}))["data"]["status"] == "OK"
    assert aioclient_mock.call_count == 1
    assert (await _call(hass, params={"query": "B"}))["error"] == "futar_unavailable"
    messages = [r.getMessage() for r in caplog.records if r.name.endswith(".futar")]
    assert len(messages) == 1 and "unavailable" in messages[0]

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    caplog.clear()
    await _call(hass, params={"query": "C"})
    assert [r.getMessage() for r in caplog.records if r.name.endswith(".futar")] == [
        "BKK FUTÁR answers again"
    ]


async def test_upstream_429_is_a_rate_limit_not_an_outage(hass, aioclient_mock, caplog) -> None:
    """FUTÁR refusing the key's calls as too many (HTTP 429) used to come back
    as futar_unavailable: the card showed the "BKK down" banner instead of
    keeping its last board for a while, as it does for futar_rate_limited."""
    aioclient_mock.get(SEARCH, status=429)
    await _setup(hass)

    for query in ("A", "B"):
        resp = await _call(hass, params={"query": query})
        assert resp == {"data": None, "error": "futar_rate_limited", "detail": "HTTP 429"}
    messages = [r.getMessage() for r in caplog.records if r.name.endswith(".futar")]
    assert len(messages) == 1 and "too many" in messages[0] and "unavailable" not in messages[0]

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    caplog.clear()
    assert (await _call(hass, params={"query": "C"}))["data"]["status"] == "OK"
    assert [r.getMessage() for r in caplog.records if r.name.endswith(".futar")] == [
        "BKK FUTÁR answers again"
    ]


async def test_too_long_parameter_is_a_bad_request_not_an_outage(hass, aioclient_mock, caplog) -> None:
    """A parameter longer than FUTAR_PARAM_MAX_LEN is never sent. It used to
    come back as futar_unavailable, which the card shows as "BKK down"."""
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    await _setup(hass)

    resp = await _call(hass, params={"query": "x" * (futar.FUTAR_PARAM_MAX_LEN + 1)})
    assert resp == {
        "data": None,
        "error": "futar_bad_request",
        "detail": f"parameter query longer than {futar.FUTAR_PARAM_MAX_LEN} characters",
    }
    assert aioclient_mock.call_count == 0
    assert not [r for r in caplog.records if r.name.endswith(".futar")]
    # Exactly the limit is fine.
    resp = await _call(hass, params={"query": "x" * futar.FUTAR_PARAM_MAX_LEN})
    assert resp["data"]["status"] == "OK"


async def test_answer_to_a_request_sent_before_a_rejection_keeps_it(monkeypatch, caplog) -> None:
    """A slow call sent before FUTÁR rejected the key used to clear the
    rejection when it succeeded: the log flapped ("rejected", "accepts
    again", "rejected") and the next call spent a 401 instead of failing
    fast for REJECTED_RETRY. Only an answer to a call sent after the latest
    rejection (the probe) clears it."""
    clock = FakeClock()
    gate = asyncio.Event()
    calls: list[str] = []

    async def fake_get(session, api_key, path, params, timeout=None):
        calls.append(path)
        if path == "plan-trip.json":
            await gate.wait()
            return {"status": "OK", "data": {}}
        if path == "trip-details.json":
            raise futar.FutarAuthError("HTTP 401")
        return {"status": "OK", "data": {}}

    monkeypatch.setattr(futar, "futar_get", fake_get)
    proxy = futar.FutarProxy(None, "secret-key", monotonic=clock.monotonic)
    slow = asyncio.ensure_future(proxy.get("plan-trip.json", {"fromPlace": "a", "toPlace": "b"}))
    await asyncio.sleep(0)
    with pytest.raises(futar.FutarAuthError):
        await proxy.get("trip-details.json", {"tripId": "1"})
    gate.set()
    assert (await slow)["status"] == "OK"

    assert proxy._rejected == "HTTP 401"
    with pytest.raises(futar.FutarAuthError):
        await proxy.get("search.json", {"query": "Keleti"})
    assert calls == ["plan-trip.json", "trip-details.json"]
    messages = [r.getMessage() for r in caplog.records if r.name.endswith(".futar")]
    assert messages == ["BKK FUTÁR rejected the configured key (HTTP 401)"]

    # The probe after REJECTED_RETRY was sent after the rejection: it clears it.
    clock.now += futar.REJECTED_RETRY
    assert (await proxy.get("search.json", {"query": "Keleti"}))["status"] == "OK"
    assert proxy._rejected is None
    assert [r.getMessage() for r in caplog.records if r.name.endswith(".futar")][-1] == (
        "BKK FUTÁR accepts the configured key again"
    )


async def test_error_status_in_a_200_is_an_error_not_data(hass, aioclient_mock, caplog) -> None:
    """HTTP 200 with a status other than OK used to come back as data: it was
    cached, cleared the outage state and passed key validation, while the
    card throws on it. It is an error now, never cached, and not an outage
    either (FUTÁR answered)."""
    aioclient_mock.get(SEARCH, status=503)
    await _setup(hass)
    assert (await _call(hass, params={"query": "A"}))["error"] == "futar_unavailable"

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "ERROR", "code": 500, "text": "boom"})
    caplog.clear()
    for _ in range(2):
        resp = await _call(hass, params={"query": "B"})
        assert resp == {"data": None, "error": "futar_unavailable", "detail": "status ERROR"}
    # Not cached: both calls reached FUTÁR.
    assert aioclient_mock.call_count == 2
    # Neither "answers again" nor a second "unavailable" line.
    assert not [r for r in caplog.records if r.name.endswith(".futar")]

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    assert (await _call(hass, params={"query": "C"}))["data"]["status"] == "OK"
    assert [r.getMessage() for r in caplog.records if r.name.endswith(".futar")] == [
        "BKK FUTÁR answers again"
    ]


async def test_missing_status_is_ok_like_on_the_card(hass, aioclient_mock) -> None:
    aioclient_mock.get(SEARCH, json={"data": {"list": []}})
    await _setup(hass)
    assert (await _call(hass))["data"] == {"data": {"list": []}}
