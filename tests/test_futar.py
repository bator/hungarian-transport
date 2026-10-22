"""The FUTÁR proxy service keeps the BKK key in Home Assistant."""

import asyncio
import re
from pathlib import Path
import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.setup import async_setup_component

from custom_components.hungarian_transport import futar
from custom_components.hungarian_transport.const import CONF_API_KEY, DOMAIN, SERVICE_FUTAR
from custom_components.hungarian_transport.futar import BKK_API

SEARCH = f"{BKK_API}/search.json"


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
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(futar, "_monotonic", lambda: 5000.0)
    monkeypatch.setattr(futar, "_sleep", no_sleep)
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    await _setup(hass)

    results = [await _call(hass, params={"query": f"q{i}"}) for i in range(futar.FUTAR_BURST + 3)]
    assert aioclient_mock.call_count == futar.FUTAR_BURST
    # Its own error code: the card keeps quiet on it, unlike an outage.
    assert results[-3:] == [{"data": None, "error": "futar_rate_limited", "detail": "rate limited"}] * 3
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


async def test_rejection_is_asked_again_after_a_while(hass, aioclient_mock, monkeypatch) -> None:
    """A 401 FUTÁR gave by mistake must not switch FUTÁR off until a restart:
    after REJECTED_RETRY one call asks again."""
    clock = [1000.0]
    monkeypatch.setattr(futar, "_monotonic", lambda: clock[0])
    aioclient_mock.get(SEARCH, status=401)
    await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    clock[0] += futar.REJECTED_RETRY - 1
    assert (await _call(hass, params={"query": "Deli"}))["error"] == "futar_auth"
    assert aioclient_mock.call_count == 0

    clock[0] += 2
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
    clock = [1000.0]
    monkeypatch.setattr(futar, "_monotonic", lambda: clock[0])
    aioclient_mock.get(SEARCH, status=401)
    await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"
    clock[0] += futar.REJECTED_RETRY + 1

    # Refused before any request: not the call that asks again.
    too_long = await _call(hass, params={"query": "x" * (futar.FUTAR_PARAM_MAX_LEN + 1)})
    assert too_long["detail"] == "parameter too long"
    assert aioclient_mock.call_count == 1

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, status=503)
    assert (await _call(hass, params={"query": "A"}))["error"] == "futar_unavailable"
    assert aioclient_mock.call_count == 1

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(futar, "_sleep", no_sleep)
    monkeypatch.setattr(futar, "FUTAR_BURST", 0)
    monkeypatch.setattr(futar, "FUTAR_RATE", 1e-9)
    assert (await _call(hass, params={"query": "B"}))["error"] == "futar_rate_limited"
    monkeypatch.undo()
    monkeypatch.setattr(futar, "_monotonic", lambda: clock[0])
    clock[0] += 1  # a token again

    aioclient_mock.clear_requests()
    aioclient_mock.get(SEARCH, json={"status": "OK", "data": {}})
    assert (await _call(hass, params={"query": "C"}))["data"]["status"] == "OK"
    assert aioclient_mock.call_count == 1


async def test_a_probe_rejected_again_restarts_the_window(hass, aioclient_mock, monkeypatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(futar, "_monotonic", lambda: clock[0])
    aioclient_mock.get(SEARCH, status=401)
    await _setup(hass)
    assert (await _call(hass))["error"] == "futar_auth"
    clock[0] += futar.REJECTED_RETRY + 1
    assert (await _call(hass, params={"query": "A"}))["error"] == "futar_auth"
    assert aioclient_mock.call_count == 2

    clock[0] += futar.REJECTED_RETRY - 1
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


async def test_worst_case_answers_before_the_card_gives_up(monkeypatch) -> None:
    """A full token wait followed by an upstream call that times out must
    answer inside the card's own timeout, or a cold start with an empty
    bucket shows "BKK timeout" (an outage) instead of a refused call."""
    card = (Path(__file__).parents[1] / "src" / "lib" / "futar.js").read_text(encoding="utf-8")
    match = re.search(r"new Error\('BKK timeout'\)\)\s*,\s*(\d+)\s*\)", card)
    assert match, "the card's FUTÁR timeout moved: update CARD_TIMEOUT_SEC"
    assert int(match.group(1)) == futar.CARD_TIMEOUT_SEC * 1000

    slept: list[float] = []

    async def record_sleep(seconds):
        slept.append(seconds)

    # A stopped clock never refills: the call waits as long as it may.
    monkeypatch.setattr(futar, "_monotonic", lambda: 5000.0)
    monkeypatch.setattr(futar, "_sleep", record_sleep)
    proxy = futar.FutarProxy(None, "secret-key")
    proxy._tokens = 0.0
    with pytest.raises(futar.FutarRateLimited):
        await proxy._acquire()
    worst = sum(slept) + futar.futar_timeout(sum(slept)).total
    assert sum(slept) <= futar.FUTAR_TOKEN_WAIT_SEC
    # Room for the websocket round trip and the event loop.
    assert futar.FUTAR_MARGIN_SEC >= 2
    assert worst <= futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC
    longest_wait = futar.FUTAR_TOKEN_WAIT_SEC
    assert longest_wait + futar.futar_timeout(longest_wait).total <= futar.CARD_TIMEOUT_SEC - futar.FUTAR_MARGIN_SEC


@pytest.mark.parametrize(
    ("debt", "busy", "timeout"),
    [(None, 0.0, 18.0), (19.0, 0.0, 14.0), (19.0, 2.0, 12.0)],
    ids=["no-wait", "token-wait", "busy-loop"],
)
async def test_upstream_timeout_is_what_is_left_of_the_card_budget(monkeypatch, debt, busy, timeout) -> None:
    """A fixed 12 s timeout failed a plan-trip.json answering in 12-20 s,
    which the card (20 s) still waits for. The upstream call gets the card's
    budget less the token wait (the clock when the loop was slower) and the
    margin."""
    clock = [5000.0]

    async def advancing_sleep(seconds):
        clock[0] += seconds + busy

    seen: list[float] = []

    async def fake_get(session, api_key, path, params, timeout=None):
        seen.append(timeout.total)
        return {"status": "OK", "data": {}}

    monkeypatch.setattr(futar, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(futar, "_sleep", advancing_sleep)
    monkeypatch.setattr(futar, "futar_get", fake_get)
    proxy = futar.FutarProxy(None, "secret-key")
    if debt is not None:
        # A token is 20 short: exactly FUTAR_TOKEN_WAIT_SEC (4 s) of waiting at 5/s.
        proxy._tokens = -debt
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
