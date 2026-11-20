"""The FUTÁR proxy service keeps the BKK key in Home Assistant."""

import asyncio
from functools import partial
import json
from datetime import timedelta
import re
from pathlib import Path
import logging
import threading

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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


def test_param_values_are_strings_and_booleans_are_lowercase() -> None:
    """A YAML true goes to FUTÁR as the cards send it ("true"), not "True"."""
    params = {"stopId": "BKK_F01111", "includeReferences": True, "onlyDepartures": False, "minutesAfter": 30}
    data = integration.FUTAR_SERVICE_SCHEMA({"path": "arrivals-and-departures-for-stop.json", "params": params})
    assert data["params"] == {
        "stopId": "BKK_F01111", "includeReferences": "true", "onlyDepartures": "false", "minutesAfter": "30"
    }
    with pytest.raises(vol.Invalid):
        integration.FUTAR_SERVICE_SCHEMA({"path": "search.json", "params": {"query": ["a"]}})


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


def _reauth_flows(hass) -> list:
    return hass.config_entries.flow.async_progress_by_handler(
        DOMAIN, match_context={"source": config_entries.SOURCE_REAUTH}
    )


def _reauth_issue(hass, entry) -> ir.IssueEntry | None:
    """The Repairs issue Home Assistant raises with an entry's reauth."""
    return ir.async_get(hass).async_get_issue(
        "homeassistant", f"config_entry_reauth_{DOMAIN}_{entry.entry_id}"
    )


async def test_a_key_accepted_again_ends_the_reauth(hass, aioclient_mock, clock) -> None:
    """The reauth a mistaken 401 started goes once FUTÁR takes the key again:
    answered empty, it would remove a key that works. Its Repairs issue goes
    with it, which Home Assistant 2024.10 leaves to the integration."""
    aioclient_mock.get(SEARCH, status=401)
    entry = await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1
    assert _reauth_issue(hass, entry) is not None

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    clock.now += futar.REJECTED_RETRY + 1
    assert (await _call(hass, params={"query": "Deli"}))["data"]["status"] == "OK"
    await hass.async_block_till_done()
    assert _reauth_flows(hass) == []
    assert _reauth_issue(hass, entry) is None, "the reauth Repairs issue stayed"


async def test_a_key_that_works_after_a_reload_ends_the_reauth(hass, aioclient_mock) -> None:
    """A reload (the UI's Reload, the options saved) makes a new proxy that
    never saw the rejection; Home Assistant 2024.10 keeps the reauth open
    across it. The new proxy's first answer from FUTÁR ends it, or it would
    stay until answered, and answered empty it removes a key that works."""
    aioclient_mock.get(SEARCH, status=401)
    entry = await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"
    await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    if not _reauth_flows(hass):
        # A newer Home Assistant ends the reauth at the reload itself: the
        # one 2024.10 keeps, started again.
        entry.async_start_reauth(hass)
        await hass.async_block_till_done()
    assert len(_reauth_flows(hass)) == 1

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    assert (await _call(hass, params={"query": "Deli"}))["data"]["status"] == "OK"
    await hass.async_block_till_done()
    assert _reauth_flows(hass) == []
    assert _reauth_issue(hass, entry) is None, "the reauth Repairs issue stayed"


async def test_only_the_first_answer_of_a_proxy_looks_for_a_reauth(monkeypatch) -> None:
    """The key is known to work after the first answer: the reauth flows
    are not looked up on every upstream call, only again after a rejection."""
    clock = FakeClock()
    rejected = False

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
        if rejected:
            raise futar.FutarAuthError("HTTP 401")
        return {"status": "OK", "data": {}}

    monkeypatch.setattr(futar, "futar_get", fake_get)
    accepted: list[bool] = []
    proxy = futar.FutarProxy(None, "secret-key", monotonic=clock.monotonic, on_accepted=lambda: accepted.append(True))
    for query in ("A", "B", "C"):
        await proxy.get("search.json", {"query": query})
    assert accepted == [True]
    rejected = True
    with pytest.raises(futar.FutarAuthError):
        await proxy.get("search.json", {"query": "D"})
    rejected = False
    clock.now += futar.REJECTED_RETRY
    await proxy.get("search.json", {"query": "E"})
    await proxy.get("search.json", {"query": "F"})
    assert accepted == [True, True]


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

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
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

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
        calls.append(path)
        if path == "plan-trip.json":
            await gate.wait()
            return {"status": "OK", "data": {}}
        if path == "trip-details.json":
            raise futar.FutarAuthError("HTTP 401")
        return {"status": "OK", "data": {}}

    monkeypatch.setattr(futar, "futar_get", fake_get)
    recovered: list[bool] = []
    proxy = futar.FutarProxy(
        None, "secret-key", monotonic=clock.monotonic, on_accepted=lambda: recovered.append(True)
    )
    slow = asyncio.ensure_future(proxy.get("plan-trip.json", {"fromPlace": "a", "toPlace": "b"}))
    await asyncio.sleep(0)
    with pytest.raises(futar.FutarAuthError):
        await proxy.get("trip-details.json", {"tripId": "1"})
    gate.set()
    assert (await slow)["status"] == "OK"

    assert proxy._rejected == "HTTP 401"
    assert recovered == []
    with pytest.raises(futar.FutarAuthError):
        await proxy.get("search.json", {"query": "Keleti"})
    assert calls == ["plan-trip.json", "trip-details.json"]
    messages = [r.getMessage() for r in caplog.records if r.name.endswith(".futar")]
    assert messages == ["BKK FUTÁR rejected the configured key (HTTP 401)"]

    # The probe after REJECTED_RETRY was sent after the rejection: it clears it.
    clock.now += futar.REJECTED_RETRY
    assert (await proxy.get("search.json", {"query": "Keleti"}))["status"] == "OK"
    assert proxy._rejected is None
    assert recovered == [True]
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


async def test_an_answer_over_the_byte_cap_is_an_outage_not_read_whole(hass, aioclient_mock, monkeypatch) -> None:
    """The answer used to be read whole into memory however large it was.
    Past FUTAR_MAX_BYTES it is refused, as an outage, while being read."""
    monkeypatch.setattr(futar, "FUTAR_MAX_BYTES", 100)
    monkeypatch.setattr(futar, "FUTAR_READ_CHUNK", 16)
    aioclient_mock.get(SEARCH, params={"query": "Big"}, json={"status": "OK", "data": {"list": ["x" * 200]}})
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {"list": ["x" * 20]}})
    await _setup(hass)

    resp = await _call(hass, params={"query": "Big"})
    assert resp == {"data": None, "error": "futar_unavailable", "detail": "answer over 100 bytes"}
    assert (await _call(hass, params={"query": "Small"}))["data"]["data"] == {"list": ["x" * 20]}


def _schedule_answer(times: int) -> str:
    """A schedule-for-stop.json-like answer as FUTÁR sends it, with a key
    given twice (the last one counts) and text that is not ASCII."""
    entry = '{"stopId": "BKK_F0%04d", "headsign": "Örs vezér tere", "arrivalTime": %d, "stopId": "BKK_%04d"}'
    rows = ", ".join(entry % (i, 1_800_000_000 + i, i) for i in range(times))
    return '{"status": "OK", "data": {"entry": {"schedules": [%s]}, "references": {"trips": {}}}}' % rows


async def test_a_large_answer_is_decoded_off_the_event_loop_with_the_same_result(hass, aioclient_mock, monkeypatch) -> None:
    """json.loads used to run on the event loop for every answer, a parent
    station's day of several MB too. An answer of FUTAR_EXECUTOR_BYTES or
    more is decoded in the executor, with a Python hook per object (the
    C decoder alone holds the GIL against the loop); a smaller one stays on
    the loop. Either way the data is what json.loads makes of it."""
    loop_thread = threading.get_ident()
    threads: list[int] = []
    real_object = futar._object

    def spy(pairs):
        threads.append(threading.get_ident())
        return real_object(pairs)

    monkeypatch.setattr(futar, "_object", spy)
    large, small = _schedule_answer(6000), _schedule_answer(20)
    assert len(large.encode()) >= futar.FUTAR_EXECUTOR_BYTES > len(small.encode())
    aioclient_mock.get(SEARCH, params={"query": "Large"}, text=large)
    aioclient_mock.get(SEARCH, text=small)
    session = async_get_clientsession(hass)

    assert await futar.futar_get(session, "secret-key", "search.json", {"query": "Large"}) == json.loads(large)
    assert threads and all(thread != loop_thread for thread in threads)

    threads.clear()
    assert await futar.futar_get(session, "secret-key", "search.json", {"query": "Small"}) == json.loads(small)
    assert threads == []

    # The executor given is the one used (Home Assistant's in production).
    used: list[str] = []

    async def executor(func, *args):
        used.append(func.__name__)
        return func(*args)

    proxy = futar.FutarProxy(session, "secret-key", executor=executor)
    assert await proxy.get("search.json", {"query": "Large"}) == json.loads(large)
    assert used == ["_decode"]


async def test_a_large_answer_reaches_the_decoder_as_its_one_buffer_emptied_once_decoded(hass, aioclient_mock) -> None:
    """The chunks read used to stay alive, joined into a second copy of the
    body, while the executor decoded it: an 8 MB answer was held twice. Now
    the decoder gets the one buffer the chunks went into, and empties it
    once it is text."""
    large = _schedule_answer(6000)
    aioclient_mock.get(SEARCH, text=large)
    seen: list = []

    async def executor(func, *args):
        seen.append((type(args[0]), len(args[0])))
        result = func(*args)
        seen.append(len(args[0]))
        return result

    session = async_get_clientsession(hass)
    data = await futar.futar_get(session, "secret-key", "search.json", {"query": "Large"}, executor=executor)
    assert data == json.loads(large)
    assert seen == [(bytearray, len(large.encode())), 0]


async def test_the_proxy_decodes_in_home_assistants_executor(hass, aioclient_mock, monkeypatch) -> None:
    made: dict = {}
    real = integration.FutarProxy

    def proxy(*args, **kwargs):
        made.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(integration, "FutarProxy", proxy)
    await _setup(hass)
    assert made["executor"] == hass.async_add_executor_job


@pytest.mark.parametrize("size", [100_000, 600_000])
async def test_an_answer_nested_too_deep_is_an_outage_not_a_failed_call(hass, aioclient_mock, size) -> None:
    """json.loads raises RecursionError, not ValueError, on an answer nested
    deeper than it goes: the service call failed with that instead of the
    card's "unavailable" answer. On the event loop and in the executor alike."""
    assert (size >= futar.FUTAR_EXECUTOR_BYTES) == (size == 600_000)
    aioclient_mock.get(SEARCH, text="[" * size)
    await _setup(hass)
    assert await _call(hass) == {"data": None, "error": "futar_unavailable", "detail": "RecursionError"}


class _Ticket:
    def release(self) -> None:
        pass


class _BoardFallback:
    """A fallback answering every departure board at once (FutarFallback)."""

    paths = frozenset({"arrivals-and-departures-for-stop.json"})
    timeout = 6.0

    def __init__(self) -> None:
        self.asked = 0

    async def answer(self, path, params, ticket=None):
        self.asked += 1
        return {"status": "OK", "from": "fallback"}

    def can_answer(self, path, params) -> bool:
        return True

    def reserve(self, path, params):
        return _Ticket()

    def learn(self, path, params, data) -> None:
        pass


async def test_a_failure_within_the_settle_is_unsaid_but_an_outage_at_once(monkeypatch, caplog) -> None:
    """FUTÁR failing again within FUTAR_OUTAGE_LOG_SETTLE_SEC of its "answers
    again" line is not warned about at once (it flapped: a pair of lines
    every few seconds), but its outage stands at once all the same: the
    next board asks the fallback first, FUTÁR not at all. The warning comes
    once the settle has passed and FUTÁR still fails, and its end after it."""
    caplog.set_level(logging.INFO, logger=futar.__name__)
    clock = FakeClock(stopped=True)
    up = [False]
    asked: list[str] = []

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
        asked.append(params.get("stopId", params.get("query")))
        if not up[0]:
            raise futar.FutarError("HTTP 503")
        return {"status": "OK", "data": {}, "from": "futar"}

    monkeypatch.setattr(futar, "futar_get", fake_get)
    fallback = _BoardFallback()
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep, fallback=fallback)
    board = "arrivals-and-departures-for-stop.json"

    def lines() -> list[tuple[int, str]]:
        return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == futar.__name__]

    with pytest.raises(futar.FutarError):
        await proxy.get("search.json", {"query": "A"})
    up[0] = True
    await proxy.get("search.json", {"query": "B"})
    assert [level for level, _m in lines()] == [logging.WARNING, logging.INFO]
    clock.now += 10
    up[0] = False
    with pytest.raises(futar.FutarError):
        await proxy.get("search.json", {"query": "C"})
    assert len(lines()) == 2
    # Down for the breaker though nothing was said: the fallback first.
    assert (await proxy.get(board, {"stopId": "BKK_F00001"}))["from"] == "fallback"
    assert asked == ["A", "B", "C"] and fallback.asked == 1
    # The settle over, FUTÁR still failing: said now, on the next request.
    clock.now += futar.FUTAR_OUTAGE_LOG_SETTLE_SEC
    assert (await proxy.get(board, {"stopId": "BKK_F00002"}))["from"] == "fallback"
    assert lines()[2:] == [(logging.WARNING, "BKK FUTÁR unavailable (HTTP 503); not logged again until it answers")]
    up[0] = True
    await proxy.get("search.json", {"query": "D"})
    assert lines()[3:] == [(logging.INFO, "BKK FUTÁR answers again")]


async def test_without_a_fallback_a_failure_within_the_settle_is_said_on_a_later_failure(monkeypatch, caplog) -> None:
    """With mavplusz off (no fallback) nothing asks whether FUTÁR is down,
    so the settle's held-back warning is said by the next failure or answer
    itself: once the settle has passed and FUTÁR still fails, the warning
    comes on the next failing request, and its end after it."""
    caplog.set_level(logging.INFO, logger=futar.__name__)
    clock = FakeClock(stopped=True)
    up = [False]

    async def fake_get(session, api_key, path, params, timeout=None, *, executor=None):
        if not up[0]:
            raise futar.FutarError("HTTP 503")
        return {"status": "OK", "data": {}, "from": "futar"}

    monkeypatch.setattr(futar, "futar_get", fake_get)
    proxy = futar.FutarProxy(None, "k", monotonic=clock.monotonic, sleep=clock.sleep)
    board = "arrivals-and-departures-for-stop.json"

    def lines() -> list[tuple[int, str]]:
        return [(r.levelno, r.getMessage()) for r in caplog.records if r.name == futar.__name__]

    with pytest.raises(futar.FutarError):
        await proxy.get(board, {"stopId": "BKK_F00001"})
    up[0] = True
    await proxy.get(board, {"stopId": "BKK_F00002"})
    clock.now += 10
    up[0] = False
    with pytest.raises(futar.FutarError):
        await proxy.get(board, {"stopId": "BKK_F00003"})
    assert [level for level, _m in lines()] == [logging.WARNING, logging.INFO]
    clock.now += futar.FUTAR_OUTAGE_LOG_SETTLE_SEC - 20
    with pytest.raises(futar.FutarError):
        await proxy.get(board, {"stopId": "BKK_F00004"})
    assert len(lines()) == 2  # FUTÁR's last line is not settle seconds old yet
    clock.now += 10
    with pytest.raises(futar.FutarError):
        await proxy.get(board, {"stopId": "BKK_F00005"})
    assert lines()[2:] == [(logging.WARNING, "BKK FUTÁR unavailable (HTTP 503); not logged again until it answers")]
    up[0] = True
    await proxy.get(board, {"stopId": "BKK_F00006"})
    assert lines()[3:] == [(logging.INFO, "BKK FUTÁR answers again")]
