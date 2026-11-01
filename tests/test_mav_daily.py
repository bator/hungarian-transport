"""The daily MÁV route index from GitHub Pages (mav_daily.py)."""

import asyncio
import gzip
import hashlib
import io
import json
import logging
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.hungarian_transport import mav, mav_daily, mav_index
from custom_components.hungarian_transport.const import CONF_DAILY_MAV, DOMAIN

NEW = "20261101.M2ChangeID"
OLD = "20261025.M2ChangeID"


@pytest.fixture
def no_daily_mav_index():
    """Overrides the conftest fixture: these tests run the real updater."""
    yield


@pytest.fixture(autouse=True)
def small_feeds(monkeypatch):
    monkeypatch.setattr(mav, "MIN_TRAINS", 1)


def _index(feed: str, schema: int = mav_index.SCHEMA) -> bytes:
    data = {
        "v": schema,
        "feed": feed,
        "st": [["Székesfehérvár", 4718361, 1842472], ["Budapest-Kelenföld", 4746432, 1902063]],
        "p": [mav_index.encode_polyline([(47.18361, 18.42472), (47.46432, 19.02063)])],
        "n": {"905": [[0, [0, 1]]]},
        "l": {},
    }
    # Compact, as mav_index.write: mav._index_feed reads "feed" from the head.
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, mtime=0)


def _manifest(raw: bytes, feed: str = NEW, **over) -> dict:
    man = {
        "format": mav_daily.MANIFEST_FORMAT,
        "schema": mav_index.SCHEMA,
        "feed": feed,
        "built_at": "2026-11-01T04:52:10Z",
        "url": "mav-index.json.gz",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "digest": "0" * 64,
        "trains": 1,
        "lines": 0,
        "audit": {"trains": 1, "trains_ok": 1, "trips": 3, "trips_ok": 3, "ok_ratio": 1.0},
        "commit": "0" * 40,
    }
    man.update(over)
    return man


def _local_feed(path: Path, version: str, end: str = "20991231") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "feed_info.txt",
            f"feed_publisher_name,feed_version,feed_start_date,feed_end_date\nMÁV,{version},,{end}\n",
        )
    path.write_bytes(buf.getvalue())


def _serve(aioclient_mock, raw: bytes | None, man: dict | None = None, **index_kw) -> None:
    aioclient_mock.get(mav_daily.MANIFEST_URL, json=man if man is not None else _manifest(raw))
    if raw is not None:
        aioclient_mock.get(mav_daily.INDEX_URL, content=raw, **index_kw)


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == mav_daily.__name__ and r.levelno >= logging.WARNING
    ]


async def test_newer_feed_installs(hass: HomeAssistant, aioclient_mock, card_assets, caplog) -> None:
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    raw = _index(NEW)
    _serve(aioclient_mock, raw)
    caplog.set_level(logging.INFO)

    assert await mav_daily.async_update(hass, card_assets) == mav_daily.INSTALLED
    # Byte for byte what the manifest describes, served like a plain file.
    assert out.read_bytes() == raw
    assert out.stat().st_mode & 0o777 == 0o644
    assert not [p for p in card_assets.iterdir() if p.name.startswith(mav.INDEX_NAME + ".")]
    assert f"Installed the daily MÁV route index of feed {NEW}" in caplog.text
    # Requested from the fixed host only.
    assert [str(c[1]) for c in aioclient_mock.mock_calls] == [mav_daily.MANIFEST_URL, mav_daily.INDEX_URL]


async def test_missing_index_installs(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    raw = _index(NEW)
    _serve(aioclient_mock, raw)
    assert await mav_daily.async_update(hass, card_assets) == mav_daily.INSTALLED
    assert (card_assets / mav.INDEX_NAME).read_bytes() == raw


@pytest.mark.parametrize("current", [NEW, "20261101.Other", "20261105.M2ChangeID"])
async def test_older_or_equal_feed_skips(hass: HomeAssistant, aioclient_mock, card_assets, current) -> None:
    """The same feed, another of the same date (a local build of that day)
    or a newer one stays, and the index is not even downloaded."""
    out = card_assets / mav.INDEX_NAME
    before = _index(current)
    out.write_bytes(before)
    _serve(aioclient_mock, _index(NEW))

    assert await mav_daily.async_update(hass, card_assets) == mav_daily.UP_TO_DATE
    assert out.read_bytes() == before
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize(
    ("local", "end", "expected"),
    [
        ("20261102.Local", "20991231", mav_daily.LOCAL_WINS),
        ("20261101.Local", "20991231", mav_daily.LOCAL_WINS),
        ("no-date", "20991231", mav_daily.LOCAL_WINS),
        ("20261020.Local", "20991231", mav_daily.INSTALLED),
        # An expired local feed loses even to an undated comparison.
        ("no-date", "20200101", mav_daily.INSTALLED),
    ],
)
async def test_local_feed_wins_unless_older(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source, local, end, expected
) -> None:
    out = card_assets / mav.INDEX_NAME
    before = _index(OLD)
    out.write_bytes(before)
    _local_feed(mav_source, local, end)
    raw = _index(NEW)
    _serve(aioclient_mock, raw)

    assert await mav_daily.async_update(hass, card_assets) == expected
    assert out.read_bytes() == (raw if expected == mav_daily.INSTALLED else before)


def _bad_cases():
    raw = _index(NEW)
    return {
        "sha mismatch": (raw, _manifest(raw, sha256="f" * 64), {}),
        "size mismatch": (raw, _manifest(raw, size=len(raw) + 1), {}),
        "oversize manifest": (raw, _manifest(raw, size=mav_daily.MAX_INDEX + 1), {}),
        "oversize body": (raw, _manifest(raw), {"headers": {"Content-Length": str(mav_daily.MAX_INDEX + 1)}}),
        "index schema": (_index(NEW, schema=1), _manifest(_index(NEW, schema=1)), {}),
        "index feed": (_index(OLD), _manifest(_index(OLD), feed=NEW), {}),
        "not gzip": (b"<html>", _manifest(b"<html>"), {}),
        "undated feed": (raw, _manifest(raw, feed="latest"), {}),
        "index 404": (raw, _manifest(raw), {"status": 404}),
        "index timeout": (raw, _manifest(raw), {"exc": asyncio.TimeoutError()}),
    }


@pytest.mark.parametrize("case", list(_bad_cases()))
async def test_bad_download_keeps_the_index_and_logs_once(
    hass: HomeAssistant, aioclient_mock, card_assets, caplog, case
) -> None:
    raw, man, index_kw = _bad_cases()[case]
    out = card_assets / mav.INDEX_NAME
    before = _index(OLD)
    out.write_bytes(before)
    _serve(aioclient_mock, raw, man, **index_kw)

    for _ in range(3):
        assert await mav_daily.async_update(hass, card_assets) == mav_daily.FAILED
    assert out.read_bytes() == before
    assert len(_warnings(caplog)) == 1, _warnings(caplog)
    assert not [p for p in card_assets.iterdir() if p.name != mav.INDEX_NAME and p.name.startswith(mav.INDEX_NAME)]
    assert "Traceback" not in caplog.text


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"status": 404}, mav_daily.FAILED),
        ({"exc": asyncio.TimeoutError()}, mav_daily.FAILED),
        ({"text": "not json"}, mav_daily.FAILED),
        ({"json": _manifest(b"x", schema=mav_index.SCHEMA + 1)}, mav_daily.UNSUPPORTED),
        ({"json": _manifest(b"x", format=mav_daily.MANIFEST_FORMAT + 1)}, mav_daily.UNSUPPORTED),
        ({"status": 301, "headers": {"Location": "http://example.com/"}}, mav_daily.FAILED),
    ],
)
async def test_bad_manifest_keeps_the_index_and_logs_once(
    hass: HomeAssistant, aioclient_mock, card_assets, caplog, kw, expected
) -> None:
    out = card_assets / mav.INDEX_NAME
    before = _index(OLD)
    out.write_bytes(before)
    aioclient_mock.get(mav_daily.MANIFEST_URL, **kw)
    caplog.set_level(logging.INFO)

    for _ in range(3):
        assert await mav_daily.async_update(hass, card_assets) == expected
    assert out.read_bytes() == before
    assert aioclient_mock.call_count == 3
    said = [r for r in caplog.records if r.name == mav_daily.__name__ and r.levelno >= logging.INFO]
    assert len(said) == 1, [r.getMessage() for r in said]
    # A newer schema is no fault: info, not a warning.
    assert (said[0].levelno == logging.INFO) == (expected == mav_daily.UNSUPPORTED)


async def test_failure_logs_again_after_a_success(hass: HomeAssistant, aioclient_mock, card_assets, caplog) -> None:
    aioclient_mock.get(mav_daily.MANIFEST_URL, status=503)
    await mav_daily.async_update(hass, card_assets)
    aioclient_mock.clear_requests()
    _serve(aioclient_mock, _index(NEW))
    assert await mav_daily.async_update(hass, card_assets) == mav_daily.INSTALLED
    aioclient_mock.clear_requests()
    aioclient_mock.get(mav_daily.MANIFEST_URL, status=503)
    await mav_daily.async_update(hass, card_assets)
    assert len(_warnings(caplog)) == 2


async def test_no_write_while_a_build_runs(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    out = card_assets / mav.INDEX_NAME
    before = _index(OLD)
    out.write_bytes(before)
    _serve(aioclient_mock, _index(NEW))
    lock = hass.data.setdefault(DOMAIN, {}).setdefault("mav_lock", asyncio.Lock())

    await lock.acquire()
    try:
        assert await mav_daily.async_update(hass, card_assets) == mav_daily.BUSY
    finally:
        lock.release()
    assert out.read_bytes() == before
    # Not even downloaded: the next round decides again.
    assert aioclient_mock.call_count == 1

    assert await mav_daily.async_update(hass, card_assets) == mav_daily.INSTALLED


async def test_no_write_when_a_build_starts_during_the_download(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    out = card_assets / mav.INDEX_NAME
    before = _index(OLD)
    out.write_bytes(before)
    raw = _index(NEW)
    lock = hass.data.setdefault(DOMAIN, {}).setdefault("mav_lock", asyncio.Lock())

    async def build_starts(method, url, data):
        await lock.acquire()
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        return AiohttpClientMockResponse(method, url, response=raw)

    aioclient_mock.get(mav_daily.MANIFEST_URL, json=_manifest(raw))
    aioclient_mock.get(mav_daily.INDEX_URL, side_effect=build_starts)
    try:
        assert await mav_daily.async_update(hass, card_assets) == mav_daily.BUSY
    finally:
        lock.release()
    assert out.read_bytes() == before


async def test_no_write_while_a_build_is_queued(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    out = card_assets / mav.INDEX_NAME
    before = _index(OLD)
    out.write_bytes(before)
    _serve(aioclient_mock, _index(NEW))
    never = asyncio.Event()
    task = hass.async_create_task(never.wait())
    hass.data.setdefault(DOMAIN, {})["mav_queued"] = mav._Job(force=False, task=task)
    try:
        assert await mav_daily.async_update(hass, card_assets) == mav_daily.BUSY
    finally:
        never.set()
        await task
        del hass.data[DOMAIN]["mav_queued"]
    assert out.read_bytes() == before


async def test_upload_during_download_wins(hass: HomeAssistant, aioclient_mock, card_assets, mav_source) -> None:
    """A newer local feed that arrived during the download is seen under the lock."""
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    raw = _index(NEW)

    async def upload_arrives(method, url, data):
        _local_feed(mav_source, "20261102.Upload")
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        return AiohttpClientMockResponse(method, url, response=raw)

    aioclient_mock.get(mav_daily.MANIFEST_URL, json=_manifest(raw))
    aioclient_mock.get(mav_daily.INDEX_URL, side_effect=upload_arrives)
    assert await mav_daily.async_update(hass, card_assets) == mav_daily.LOCAL_WINS
    assert out.read_bytes() == _index(OLD)


async def test_startup_keeps_the_daily_index_over_an_older_feed(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source
) -> None:
    """After an install, the startup check (the newer feed wins) keeps it."""
    _local_feed(mav_source, "20261020.Local")
    _serve(aioclient_mock, _index(NEW))
    assert await mav_daily.async_update(hass, card_assets) == mav_daily.INSTALLED
    out = card_assets / mav.INDEX_NAME
    decision = mav._startup_decision(mav_source, out, mav.state_path(hass), dt_util.now().date())
    assert decision == mav.SUPERSEDED


async def _setup(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


def _later(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))


async def test_runs_after_startup_and_then_every_interval(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    """Each round is scheduled from the loop's clock, which the fired time
    does not move: the offsets below count from setup."""
    (card_assets / mav.INDEX_NAME).write_bytes(_index(NEW))
    _serve(aioclient_mock, _index(NEW))
    entry = await _setup(hass, {})

    # Not right at startup: the startup build goes first.
    _later(hass, mav_daily.FIRST_DELAY - 60)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 0
    _later(hass, mav_daily.FIRST_DELAY + mav_daily.FIRST_JITTER + 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 1

    _later(hass, mav_daily.INTERVAL - mav_daily.INTERVAL_JITTER - 60)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 1
    _later(hass, mav_daily.INTERVAL + mav_daily.INTERVAL_JITTER + 60)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 2

    # Unloaded: no further round.
    assert await hass.config_entries.async_unload(entry.entry_id)
    _later(hass, 3 * mav_daily.INTERVAL)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 2


async def test_turned_off_makes_no_request(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    _serve(aioclient_mock, _index(NEW))
    await _setup(hass, {CONF_DAILY_MAV: False})
    for k in range(1, 4):
        _later(hass, k * (mav_daily.INTERVAL + mav_daily.FIRST_DELAY + mav_daily.FIRST_JITTER))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == 0
    assert not (card_assets / mav.INDEX_NAME).exists()


async def test_an_unexpected_error_is_logged_once_and_retried(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch, caplog
) -> None:
    calls = []

    async def broken(hass, www):
        calls.append(www)
        raise KeyError("boom")

    monkeypatch.setattr(mav_daily, "async_update", broken)
    await _setup(hass, {})
    _later(hass, mav_daily.FIRST_DELAY + mav_daily.FIRST_JITTER + 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    _later(hass, mav_daily.INTERVAL + mav_daily.INTERVAL_JITTER + 60)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 2
    assert len(_warnings(caplog)) == 1


def test_check_index_rejects_dangling_references() -> None:
    data = json.loads(gzip.decompress(_index(NEW)))
    data["n"]["905"][0][1] = [0, 7]
    raw = gzip.compress(json.dumps(data).encode())
    with pytest.raises(mav_daily.DailyIndexError, match="does not resolve"):
        mav_daily.check_index(raw, _manifest(raw), 1)


def test_check_index_rejects_too_few_trains() -> None:
    raw = _index(NEW)
    with pytest.raises(mav_daily.DailyIndexError, match="only 1 trains"):
        mav_daily.check_index(raw, _manifest(raw), 50)


def test_check_index_caps_what_it_unpacks(monkeypatch) -> None:
    raw = gzip.compress(b"[" + b" " * 10_000 + b"]")
    monkeypatch.setattr(mav_daily, "MAX_INDEX_JSON", 1000)
    with pytest.raises(mav_daily.DailyIndexError, match="too much"):
        mav_daily.check_index(raw, _manifest(raw), 1)


def test_the_urls_are_fixed_https_on_github_pages() -> None:
    for url in (mav_daily.MANIFEST_URL, mav_daily.INDEX_URL):
        assert url.startswith("https://bator.github.io/hungarian-transport/")


def test_the_manifest_format_matches_the_site_script() -> None:
    """mav_daily reads the manifest scripts/mav_pages_site.py writes."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "mav_pages_site.py"
    assert f"MANIFEST_FORMAT = {mav_daily.MANIFEST_FORMAT}\n" in script.read_text(encoding="utf-8")
    assert f'MANIFEST_NAME = "{mav_daily.MANIFEST_URL.rsplit("/", 1)[1]}"' in script.read_text(encoding="utf-8")
    assert f'INDEX_NAME = "{mav_daily.INDEX_URL.rsplit("/", 1)[1]}"' in script.read_text(encoding="utf-8")
