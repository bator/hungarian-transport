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

from custom_components.hungarian_transport import gtfs_feed, mav, mav_daily, mav_index
from custom_components.hungarian_transport.const import CONF_DAILY_MAV, CONF_DAILY_TIME, DOMAIN

NEW = "20261101.M2ChangeID"
OLD = "20261025.M2ChangeID"


@pytest.fixture
def no_daily_mav_index():
    """Overrides the conftest fixture: these tests run the real updater."""
    yield


@pytest.fixture(autouse=True)
def small_feeds(monkeypatch):
    monkeypatch.setattr(mav, "MIN_TRAINS", 1)


# The line through the two stations (a stop-sequence variant), and a route
# that follows the rails between them (a pfaedle-fitted one).
STATION_LINE = [(47.18361, 18.42472), (47.46432, 19.02063)]
RAIL = [(47.18361, 18.42472), (47.3301, 18.7012), (47.46432, 19.02063)]


def _index(feed: str, schema: int = mav_index.SCHEMA, fitted: bool = False) -> bytes:
    data = {
        "v": schema,
        "feed": feed,
        "st": [["Székesfehérvár", 4718361, 1842472], ["Budapest-Kelenföld", 4746432, 1902063]],
        "p": [gtfs_feed.encode_polyline(RAIL if fitted else STATION_LINE)],
        "n": {"905": [[0, [0, 1]]]},
        "l": {},
    }
    # Compact, as mav_index.write: served_index.read_feed reads "feed" from the head.
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

    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED
    # Byte for byte what the manifest describes, served like a plain file.
    assert out.read_bytes() == raw
    assert out.stat().st_mode & 0o777 == 0o644
    assert not [p for p in card_assets.iterdir() if p.name.startswith(mav.INDEX_NAME + ".")]
    assert f"Installed the daily MÁV route index of feed {NEW}" in caplog.text
    # Requested from the fixed host only; the index with the manifest's
    # sha256, so a cache never pairs this manifest with another index.
    assert [str(c[1]) for c in aioclient_mock.mock_calls] == [
        mav_daily.MANIFEST_URL, f"{mav_daily.INDEX_URL}?sha={hashlib.sha256(raw).hexdigest()}"
    ]


async def test_missing_index_installs(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    raw = _index(NEW)
    _serve(aioclient_mock, raw)
    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED
    assert (card_assets / mav.INDEX_NAME).read_bytes() == raw


@pytest.mark.parametrize("current", [NEW, "20261101.Other", "20261105.M2ChangeID"])
async def test_older_or_equal_feed_skips(hass: HomeAssistant, aioclient_mock, card_assets, current) -> None:
    """The same feed (fitted, as the daily one), another of the same date (a
    local build of that day) or a newer one stays, and the index is not even
    downloaded."""
    out = card_assets / mav.INDEX_NAME
    before = _index(current, fitted=current == NEW)
    out.write_bytes(before)
    _serve(aioclient_mock, _index(NEW))

    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.UP_TO_DATE
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
    # www/ serves the local feed's own build.
    _built_here(hass, mav_source, out)
    raw = _index(NEW)
    _serve(aioclient_mock, raw)

    assert (await mav_daily.async_update(hass, card_assets)).result == expected
    assert out.read_bytes() == (raw if expected == mav_daily.INSTALLED else before)


def _built_here(hass: HomeAssistant, src: Path, out: Path, **extra) -> None:
    """The build state of an index this integration built from src."""
    state = mav.state_path(hass)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"source": mav._fingerprint(src), "index": mav._fingerprint(out), **extra}))


@pytest.mark.parametrize(
    "state",
    [
        {"failed": "fingerprint", "error": "audit rejected"},
        {"interrupted": {"count": 2, "error": "killed"}},
        {},
    ],
)
async def test_local_feed_without_its_index_does_not_win(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source, state
) -> None:
    """A local feed of the daily feed's date whose build failed, was
    interrupted or never ran has no index in www/: the daily one installs."""
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    _local_feed(mav_source, "20261101.Local")
    path = mav.state_path(hass)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "failed" in state:
        state = {**state, "failed": mav._fingerprint(mav_source)}
    if "interrupted" in state:
        state = {"interrupted": {**state["interrupted"], "source": mav._fingerprint(mav_source)}}
    path.write_text(json.dumps(state))
    raw = _index(NEW)
    _serve(aioclient_mock, raw)

    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED
    assert out.read_bytes() == raw


async def test_local_feed_whose_index_is_another_build_does_not_win(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source
) -> None:
    """The state names an index, but not the one www/ serves now."""
    out = card_assets / mav.INDEX_NAME
    _local_feed(mav_source, "20261101.Local")
    out.write_bytes(_index("20261101.Local"))
    _built_here(hass, mav_source, out)
    out.write_bytes(_index(OLD))
    _serve(aioclient_mock, _index(NEW))
    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED


@pytest.mark.parametrize(
    ("served_fitted", "daily_fitted", "share", "expected"),
    [
        # A stop-sequence build of the feed (the update script's, a local
        # one): the daily pfaedle index of the same feed replaces it.
        (False, True, 0.98, mav_daily.INSTALLED),
        # A fitted one stays: same feed, same kind, same size.
        (True, True, 0.98, mav_daily.UP_TO_DATE),
        # The manifest says nothing about its kind: the download tells.
        (False, True, None, mav_daily.INSTALLED),
    ],
)
async def test_same_feed_rule(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source, served_fitted, daily_fitted, share, expected
) -> None:
    out = card_assets / mav.INDEX_NAME
    before = _index(NEW, fitted=served_fitted)
    out.write_bytes(before)
    _local_feed(mav_source, NEW)
    _built_here(hass, mav_source, out)
    raw = _index(NEW, fitted=daily_fitted)
    _serve(aioclient_mock, raw, _manifest(raw, fitted_share=share))

    assert (await mav_daily.async_update(hass, card_assets)).result == expected
    assert out.read_bytes() == (raw if expected == mav_daily.INSTALLED else before)
    # The startup check keeps whichever stayed: a build of the same feed
    # never replaces it.
    decision = mav._startup_decision(mav_source, out, mav.state_path(hass), dt_util.now().date())
    assert decision == mav.CURRENT


async def test_a_stop_sequence_daily_index_never_replaces_a_fitted_one(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    """A manifest that claims a fitted index is checked again on the
    download: of the same feed, a stop-sequence index loses."""
    out = card_assets / mav.INDEX_NAME
    before = _index(NEW, fitted=True)
    out.write_bytes(before)
    raw = _index(NEW) + b""
    big = json.loads(gzip.decompress(raw))
    big["st"].append(["Padding", 4700000, 1900000])
    big["pad"] = "".join(f"{i:05d}" for i in range(4000))
    raw = gzip.compress(json.dumps(big, separators=(",", ":")).encode(), mtime=0)
    _serve(aioclient_mock, raw, _manifest(raw, fitted_share=0.99))
    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.UP_TO_DATE
    assert out.read_bytes() == before


def test_the_same_feed_rule() -> None:
    rank = mav_index.IndexRank
    fitted, coarse = rank(True, 250_000), rank(False, 150_000)
    assert mav_index.same_feed_replaces(fitted, coarse)
    assert not mav_index.same_feed_replaces(coarse, fitted)
    # A build not made yet (kind and size unknown) replaces nothing.
    assert not mav_index.same_feed_replaces(rank(None, None), coarse)
    assert not mav_index.same_feed_replaces(rank(None, None), fitted)
    # Same kind: only clearly larger.
    assert not mav_index.same_feed_replaces(rank(True, 250_000 + mav_index.SAME_FEED_SLACK), fitted)
    assert mav_index.same_feed_replaces(rank(True, 250_001 + mav_index.SAME_FEED_SLACK), fitted)
    assert mav_index.same_feed_replaces(rank(False, 160_000), coarse)
    assert mav_index.same_feed_replaces(rank(True, 1), coarse)


def test_fitted_share_reads_the_index_alone() -> None:
    assert mav_index.fitted_share(json.loads(gzip.decompress(_index(NEW)))) == 0.0
    assert mav_index.fitted_share(json.loads(gzip.decompress(_index(NEW, fitted=True)))) == 1.0
    assert mav_index.fitted_share({"st": [], "p": [], "n": {}, "l": {}}) == 0.0


def test_fitted_share_matches_the_build(tmp_path) -> None:
    """The share read back from a written index is the build's own."""
    zpath = tmp_path / "feed.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("routes.txt", "route_id,route_type,route_short_name,route_long_name\nr1,2,,905\nr2,2,,906\n")
        z.writestr("trips.txt", "route_id,trip_id,shape_id\nr1,t1,s1\nr2,t2,\n")
        z.writestr("stops.txt", "stop_id,stop_name,stop_lat,stop_lon\na,A,47.18361,18.42472\nb,B,47.46432,19.02063\n")
        z.writestr("stop_times.txt", "trip_id,stop_id,stop_sequence\nt1,a,1\nt1,b,2\nt2,b,1\nt2,a,2\n")
        z.writestr(
            "shapes.txt",
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            + "".join(f"s1,{lat},{lon},{i}\n" for i, (lat, lon) in enumerate(RAIL)),
        )
        z.writestr("feed_info.txt", f"feed_version\n{NEW}\n")
    index = mav_index.build(zpath)
    stats = index["_stats"]
    out = tmp_path / "out.json.gz"
    mav_index.write(index, out)
    assert stats["fitted"] == 1 and stats["variants"] == 2
    assert mav_index.fitted_share(mav_index.read(out)) == stats["fitted"] / stats["variants"]


def test_check_index_rejects_a_second_gzip_member() -> None:
    raw = _index(NEW) + gzip.compress(b"<script>")
    with pytest.raises(mav_daily.DailyIndexError, match="after its gzip stream"):
        mav_daily.check_index(raw, _manifest(raw), 1)


def test_build_helpers(hass: HomeAssistant) -> None:
    lock = mav.build_lock(hass)
    assert mav.build_lock(hass) is lock
    assert not mav.build_pending(hass)


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
        assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.FAILED
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
        assert (await mav_daily.async_update(hass, card_assets)).result == expected
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
    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED
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
        assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.BUSY
    finally:
        lock.release()
    assert out.read_bytes() == before
    # Not even downloaded: the next round decides again.
    assert aioclient_mock.call_count == 1

    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED


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
        assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.BUSY
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
        assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.BUSY
    finally:
        never.set()
        await task
        del hass.data[DOMAIN]["mav_queued"]
    assert out.read_bytes() == before


async def test_upload_during_download_wins(hass: HomeAssistant, aioclient_mock, card_assets, mav_source) -> None:
    """A local feed of the same date built during the download is seen under the lock."""
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    raw = _index(NEW)
    local = _index("20261101.Upload")

    async def upload_arrives(method, url, data):
        _local_feed(mav_source, "20261101.Upload")
        out.write_bytes(local)
        _built_here(hass, mav_source, out)
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        return AiohttpClientMockResponse(method, url, response=raw)

    aioclient_mock.get(mav_daily.MANIFEST_URL, json=_manifest(raw))
    aioclient_mock.get(mav_daily.INDEX_URL, side_effect=upload_arrives)
    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.UP_TO_DATE
    assert out.read_bytes() == local


async def test_a_local_build_of_an_older_feed_date_during_the_download_loses(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source
) -> None:
    """A newer local feed, copied but not built: it keeps nothing; the next
    start builds it (the newer feed wins there)."""
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    raw = _index(NEW)

    async def copied(method, url, data):
        _local_feed(mav_source, "20261102.Copied")
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        return AiohttpClientMockResponse(method, url, response=raw)

    aioclient_mock.get(mav_daily.MANIFEST_URL, json=_manifest(raw))
    aioclient_mock.get(mav_daily.INDEX_URL, side_effect=copied)
    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED
    assert mav._startup_decision(mav_source, out, mav.state_path(hass), dt_util.now().date()) == mav.BUILD


async def test_startup_keeps_the_daily_index_over_an_older_feed(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source
) -> None:
    """After an install, the startup check (the newer feed wins) keeps it."""
    _local_feed(mav_source, "20261020.Local")
    _serve(aioclient_mock, _index(NEW))
    assert (await mav_daily.async_update(hass, card_assets)).result == mav_daily.INSTALLED
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


async def _at(hass: HomeAssistant, freezer, when: str) -> None:
    moment = dt_util.parse_datetime(when)
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
def budapest(hass: HomeAssistant, freezer):
    """Local time Europe/Budapest, 03:00 on 1 November 2026 (CET, +01:00)."""
    freezer.move_to("2026-11-01 03:00:00+01:00")
    return hass.config.async_set_time_zone("Europe/Budapest")


@pytest.fixture
def max_jitter(monkeypatch):
    """Every random delay at its upper end."""
    monkeypatch.setattr(mav_daily.random, "uniform", lambda a, b: b)


async def test_runs_after_startup_and_then_daily_at_the_chosen_time(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, max_jitter
) -> None:
    await budapest
    (card_assets / mav.INDEX_NAME).write_bytes(_index(NEW, fitted=True))
    _serve(aioclient_mock, _index(NEW, fitted=True))
    entry = await _setup(hass, {CONF_DAILY_TIME: "05:30:00"})

    # Not right at startup: the startup build goes first (10 + 10 minutes).
    await _at(hass, freezer, "2026-11-01 03:19:50+01:00")
    assert aioclient_mock.call_count == 0
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert aioclient_mock.call_count == 1

    # Then at 05:30 local time, plus up to DAILY_JITTER, once a day.
    await _at(hass, freezer, "2026-11-01 05:29:59+01:00")
    assert aioclient_mock.call_count == 1
    await _at(hass, freezer, "2026-11-01 05:30:00+01:00")
    await _at(hass, freezer, "2026-11-01 05:34:50+01:00")
    assert aioclient_mock.call_count == 1
    await _at(hass, freezer, "2026-11-01 05:35:01+01:00")
    assert aioclient_mock.call_count == 2
    await _at(hass, freezer, "2026-11-01 23:59:00+01:00")
    assert aioclient_mock.call_count == 2
    await _at(hass, freezer, "2026-11-02 05:30:00+01:00")
    await _at(hass, freezer, "2026-11-02 05:35:01+01:00")
    assert aioclient_mock.call_count == 3

    # Unloaded: no further round.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _at(hass, freezer, "2026-11-03 05:30:00+01:00")
    await _at(hass, freezer, "2026-11-03 05:40:00+01:00")
    assert aioclient_mock.call_count == 3


async def test_default_time_is_six_with_a_small_jitter(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, monkeypatch
) -> None:
    await budapest
    delays = []

    def uniform(a, b):
        delays.append((a, b))
        return b

    monkeypatch.setattr(mav_daily.random, "uniform", uniform)
    (card_assets / mav.INDEX_NAME).write_bytes(_index(NEW, fitted=True))
    _serve(aioclient_mock, _index(NEW, fitted=True))
    await _setup(hass, {})
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert aioclient_mock.call_count == 1
    await _at(hass, freezer, "2026-11-01 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-01 06:05:01+01:00")
    assert aioclient_mock.call_count == 2
    assert delays[-1] == (0, mav_daily.DAILY_JITTER) and mav_daily.DAILY_JITTER <= 5 * 60


async def test_a_new_time_in_the_options_reschedules(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, max_jitter
) -> None:
    await budapest
    (card_assets / mav.INDEX_NAME).write_bytes(_index(NEW, fitted=True))
    _serve(aioclient_mock, _index(NEW, fitted=True))
    entry = await _setup(hass, {CONF_DAILY_TIME: "05:30:00"})
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert aioclient_mock.call_count == 1

    # The update listener reloads the entry with the new time.
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_DAILY_TIME: "07:15:00"})
    await hass.async_block_till_done(wait_background_tasks=True)
    count = aioclient_mock.call_count
    await _at(hass, freezer, "2026-11-01 03:45:00+01:00")  # the reload's own first round
    count = aioclient_mock.call_count
    await _at(hass, freezer, "2026-11-01 05:30:00+01:00")
    await _at(hass, freezer, "2026-11-01 05:40:00+01:00")
    assert aioclient_mock.call_count == count
    await _at(hass, freezer, "2026-11-01 07:15:00+01:00")
    await _at(hass, freezer, "2026-11-01 07:20:01+01:00")
    assert aioclient_mock.call_count == count + 1


async def test_turned_off_makes_no_request(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest
) -> None:
    await budapest
    _serve(aioclient_mock, _index(NEW))
    await _setup(hass, {CONF_DAILY_MAV: False})
    for when in ("2026-11-01 03:30:00+01:00", "2026-11-01 06:00:00+01:00", "2026-11-01 06:10:00+01:00",
                 "2026-11-02 06:10:00+01:00"):
        await _at(hass, freezer, when)
    assert aioclient_mock.call_count == 0
    assert not (card_assets / mav.INDEX_NAME).exists()


async def test_an_unexpected_error_is_logged_once_and_retried(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch, caplog, freezer, budapest, max_jitter
) -> None:
    await budapest
    calls = []

    async def broken(hass, www):
        calls.append(www)
        raise KeyError("boom")

    monkeypatch.setattr(mav_daily, "async_update", broken)
    await _setup(hass, {})
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert len(calls) == 1
    # A failed round is tried again RETRY_DELAY later, RETRY_LIMIT times.
    for _ in range(mav_daily.RETRY_LIMIT + 1):
        _later(hass, mav_daily.RETRY_DELAY + 1)
        await hass.async_block_till_done(wait_background_tasks=True)
        freezer.tick(mav_daily.RETRY_DELAY + 1)
    assert len(calls) == 1 + mav_daily.RETRY_LIMIT
    # The daily round starts over, with its own retries.
    await _at(hass, freezer, "2026-11-01 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-01 06:05:01+01:00")
    assert len(calls) == 2 + mav_daily.RETRY_LIMIT
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


async def test_a_transient_error_is_retried_the_same_morning(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, max_jitter
) -> None:
    """A 503 at the daily round costs RETRY_DELAY, not a day."""
    await budapest
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    raw = _index(NEW)
    _serve(aioclient_mock, raw)
    await _setup(hass, {})
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert out.read_bytes() == raw
    # The next feed: the daily round gets a 503, the retry the manifest.
    out.write_bytes(_index(OLD))
    aioclient_mock.clear_requests()
    aioclient_mock.get(mav_daily.MANIFEST_URL, status=503)
    await _at(hass, freezer, "2026-11-01 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-01 06:05:01+01:00")
    assert aioclient_mock.call_count == 1
    aioclient_mock.clear_requests()
    _serve(aioclient_mock, raw)
    await _at(hass, freezer, "2026-11-01 06:24:59+01:00")
    assert aioclient_mock.call_count == 0
    await _at(hass, freezer, "2026-11-01 06:25:02+01:00")
    assert out.read_bytes() == raw


async def test_busy_at_the_daily_round_is_retried_after_the_build(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, monkeypatch, max_jitter
) -> None:
    """Home Assistant started at 05:55: the 06:00 round replaces the first
    one and finds the startup build holding the lock; a retry installs."""
    freezer.move_to("2026-11-01 05:55:00+01:00")
    await hass.config.async_set_time_zone("Europe/Budapest")
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    raw = _index(NEW)
    _serve(aioclient_mock, raw)
    results = []
    real = mav_daily.async_update

    async def spy(h, w):
        done = await real(h, w)
        results.append(done.result)
        return done

    monkeypatch.setattr(mav_daily, "async_update", spy)
    lock = mav.build_lock(hass)
    await _setup(hass, {})
    await lock.acquire()
    await _at(hass, freezer, "2026-11-01 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-01 06:05:01+01:00")
    assert results == [mav_daily.BUSY]
    lock.release()
    await _at(hass, freezer, "2026-11-01 06:25:02+01:00")
    assert results == [mav_daily.BUSY, mav_daily.INSTALLED]
    assert out.read_bytes() == raw
    # Done: no more retries, and the replaced first round does not come back.
    await _at(hass, freezer, "2026-11-01 08:00:00+01:00")
    assert results == [mav_daily.BUSY, mav_daily.INSTALLED]


async def test_a_mixed_deploy_is_retried_and_asks_for_the_manifest_s_index(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, max_jitter
) -> None:
    """Right after a deploy a cache may still serve the old index for the
    new manifest: FAILED, retried with the sha256 in the URL."""
    await budapest
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(OLD))
    old_gen, new_gen = _index("20261031.M2ChangeID"), _index(NEW)
    sha = hashlib.sha256(new_gen).hexdigest()
    # The first answer is the stale generation, the next the right one.
    answers = [old_gen, new_gen]

    async def cache(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        assert str(url).endswith(f"?sha={sha}")
        return AiohttpClientMockResponse(method, url, response=answers.pop(0))

    await _setup(hass, {})
    aioclient_mock.get(mav_daily.MANIFEST_URL, json=_manifest(new_gen))
    aioclient_mock.get(mav_daily.INDEX_URL, side_effect=cache)
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    assert out.read_bytes() == _index(OLD)
    await _at(hass, freezer, "2026-11-01 03:40:02+01:00")
    assert out.read_bytes() == new_gen


async def test_a_late_manifest_is_looked_at_once_more(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, budapest, max_jitter
) -> None:
    """At 06:00 the manifest is yesterday's (the workflow ran late): one more
    round LATE_DELAY later picks up today's."""
    await budapest
    out = card_assets / mav.INDEX_NAME
    yesterday = _index("20261031.M2ChangeID", fitted=True)
    out.write_bytes(yesterday)
    _serve(aioclient_mock, yesterday, _manifest(yesterday, feed="20261031.M2ChangeID",
                                                built_at="2026-10-31T03:50:00Z"))
    await _setup(hass, {})
    await _at(hass, freezer, "2026-11-01 03:20:01+01:00")
    await _at(hass, freezer, "2026-11-01 06:00:00+01:00")
    await _at(hass, freezer, "2026-11-01 06:05:01+01:00")
    assert aioclient_mock.call_count == 2
    aioclient_mock.clear_requests()
    raw = _index(NEW)
    _serve(aioclient_mock, raw, _manifest(raw, built_at="2026-11-01T06:30:00Z"))
    await _at(hass, freezer, "2026-11-01 08:05:00+01:00")
    assert aioclient_mock.call_count == 0
    await _at(hass, freezer, "2026-11-01 08:05:02+01:00")
    assert out.read_bytes() == raw
    # Once a day only.
    await _at(hass, freezer, "2026-11-01 12:00:00+01:00")
    assert aioclient_mock.call_count == 2


async def test_startup_keeps_an_index_of_the_same_feed_when_the_zip_is_copied_again(
    hass: HomeAssistant, card_assets, mav_source
) -> None:
    """The update script downloads the same feed again (a new mtime): the
    index of that feed_version stays at startup (the same-feed rule), it is
    not built again over a fitted one."""
    out = card_assets / mav.INDEX_NAME
    out.write_bytes(_index(NEW, fitted=True))
    _local_feed(mav_source, NEW)
    _built_here(hass, mav_source, out)
    _local_feed(mav_source, NEW, end="20991230")  # same feed_version, another file
    state = mav.state_path(hass)
    assert mav._startup_decision(mav_source, out, state, dt_util.now().date()) == mav.CURRENT
    # Another feed_version is built as before.
    _local_feed(mav_source, "20261102.M2ChangeID")
    assert mav._startup_decision(mav_source, out, state, dt_util.now().date()) == mav.BUILD
