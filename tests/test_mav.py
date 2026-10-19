"""MÁV route index: upload, build at startup, rebuild service, audit."""

import asyncio
import gzip
import io
import json
import math
import os
import signal
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_get_persistent_notifications,
)

from homeassistant.components.http import KEY_HASS_USER
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.http import KEY_HASS
from homeassistant.setup import async_setup_component

from custom_components.hungarian_transport import mav, mav_index
from custom_components.hungarian_transport.const import DOMAIN, SERVICE_REBUILD_MAV

SZF = (47.18361, 18.42472)
MID = (47.31570, 18.78800)
KEL = (47.46432, 19.02063)
SZOMBATHELY = (47.23510, 16.63160)


@pytest.fixture(autouse=True)
def small_feeds(monkeypatch):
    """The test feed has two trains; real ones have hundreds."""
    monkeypatch.setattr(mav, "MIN_TRAINS", 1)


def _rails(a, b, n=30):
    """A bending line between two stations, like a pfaedle shape."""
    out = []
    for i in range(n):
        t = i / (n - 1)
        bend = 0.01 * math.sin(t * math.pi)
        out.append((a[0] + (b[0] - a[0]) * t + bend, a[1] + (b[1] - a[1]) * t))
    return out


def _feed_zip(version: str = "test-feed", route_type: str = "2") -> bytes:
    """Train 905 runs Szombathely - Kelenföld on one day and Székesfehérvár -
    Kelenföld on another (two stop patterns); a third trip has a shape that
    misses its middle station and must fall back to the station line."""
    long_line = _rails(SZOMBATHELY, SZF) + _rails(SZF, MID)[1:] + _rails(MID, KEL)[1:]
    short_line = _rails(SZF, MID) + _rails(MID, KEL)[1:]
    files = {
        "agency.txt": "agency_id,agency_name\nMAV,MÁV\n",
        "feed_info.txt": f"feed_publisher_name,feed_version\nMÁV,{version}\n",
        "routes.txt": (
            "route_id,route_short_name,route_long_name,route_type\n"
            f"R905,,IC 905 BAKONY,{route_type}\nR858,,IC 858,{route_type}\n"
        ),
        "trips.txt": "route_id,service_id,trip_id,shape_id\nR905,S1,T1,long\nR905,S2,T2,short\nR858,S1,T3,chord\n",
        "stops.txt": (
            "stop_id,stop_name,stop_lat,stop_lon\n"
            f"1,Szombathely,{SZOMBATHELY[0]},{SZOMBATHELY[1]}\n"
            f"2,Székesfehérvár,{SZF[0]},{SZF[1]}\n"
            f"3,Martonvásár,{MID[0]},{MID[1]}\n"
            f"4,Budapest-Kelenföld,{KEL[0]},{KEL[1]}\n"
        ),
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,06:00:00,06:00:00,1,1\nT1,07:30:00,07:30:00,2,2\nT1,07:50:00,07:50:00,3,3\nT1,08:10:00,08:10:00,4,4\n"
            "T2,07:30:00,07:30:00,2,1\nT2,07:50:00,07:50:00,3,2\nT2,08:10:00,08:10:00,4,3\n"
            "T3,09:00:00,09:00:00,2,1\nT3,09:20:00,09:20:00,3,2\nT3,09:40:00,09:40:00,4,3\n"
        ),
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        + "".join(f"long,{la:.6f},{lo:.6f},{i}\n" for i, (la, lo) in enumerate(long_line))
        + "".join(f"short,{la:.6f},{lo:.6f},{i}\n" for i, (la, lo) in enumerate(short_line))
        + f"chord,{SZF[0]},{SZF[1]},0\nchord,{KEL[0]},{KEL[1]},1\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


async def _setup(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def test_index_keeps_every_stop_pattern_and_passes_its_audit(tmp_path: Path) -> None:
    feed = tmp_path / "mav.zip"
    feed.write_bytes(_feed_zip())
    index = mav_index.build(feed)
    variants = index["n"]["905"]
    names = [[index["st"][i][0] for i in v[1]] for v in variants]
    assert names == [
        ["Szombathely", "Székesfehérvár", "Martonvásár", "Budapest-Kelenföld"],
        ["Székesfehérvár", "Martonvásár", "Budapest-Kelenföld"],
    ]
    # The 858 chord misses Martonvásár by kilometres: station line instead.
    assert index["_stats"]["stop_lines"] == 1
    assert len(mav_index.decode_polyline(index["p"][index["n"]["858"][0][0]])) == 3
    report = mav_index.audit(feed, index)
    assert report["trips"] == 3 and report["trips_bad"] == 0 and report["ok_ratio"] == 1.0


def test_audit_after_build_reads_the_feed_once(tmp_path: Path, monkeypatch) -> None:
    """build hands its parsed feed to audit; stop_times.txt (the big one) is
    streamed once, not twice, and an index read back from disk still audits."""
    feed = tmp_path / "mav.zip"
    feed.write_bytes(_feed_zip())
    reads: list[str] = []
    real_rows = mav_index.Feed.rows

    def counting_rows(self, name):
        reads.append(name)
        return real_rows(self, name)

    monkeypatch.setattr(mav_index.Feed, "rows", counting_rows)
    index = mav_index.build(feed)
    report = mav_index.audit(feed, index)
    assert report["ok_ratio"] == 1.0
    assert reads.count("stop_times.txt") == 1

    out = tmp_path / "mav-index.json.gz"
    mav_index.write(index, out)
    reads.clear()
    report = mav_index.audit(feed, mav_index.read(out))
    assert report["ok_ratio"] == 1.0 and reads.count("stop_times.txt") == 1


def test_build_runner_is_a_standalone_process(tmp_path: Path) -> None:
    """Home Assistant runs mav_index.py as its own interpreter: stdlib only,
    stats as JSON on stdout, a one-line reason on stderr when it fails."""
    feed = tmp_path / "mav.zip"
    feed.write_bytes(_feed_zip())
    out = tmp_path / "mav-index.json.gz"
    cmd = [sys.executable, "-I", str(mav.BUILD_RUNNER), str(feed), str(out), "--min-trains", "1"]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    stats = json.loads(done.stdout)
    assert stats["trains"] == 2 and stats["audit"]["ok_ratio"] == 1.0 and stats["written"]
    assert "_loaded" not in json.loads(gzip.decompress(out.read_bytes()))

    # The default guard wants a real feed's trains: nothing written, and
    # the stats say why.
    out.unlink()
    done = subprocess.run(cmd[:-2], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    stats = json.loads(done.stdout)
    assert stats["written"] is False and "only 2 trains" in stats["rejected"]
    assert not out.exists()

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    done = subprocess.run(cmd[:3] + [str(bad), str(out)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert "not a GTFS zip" in done.stderr


def test_card_choice_serves_both_stations(tmp_path: Path) -> None:
    feed = tmp_path / "mav.zip"
    feed.write_bytes(_feed_zip())
    index = mav_index.build(feed)
    var, b, a = mav_index.pick_variant(index, ("n", "905"), "Székesfehérvár", "Kelenföld vasútállomás")
    assert var is not None and b >= 0 and a > b
    var, b, a = mav_index.pick_variant(index, ("n", "905"), "Szombathely", "Budapest-Kelenföld")
    assert [index["st"][i][0] for i in var[1]][0] == "Szombathely" and a == 3


async def test_upload_builds_and_audits(hass: HomeAssistant, hass_client, card_assets, mav_source) -> None:
    await _setup(hass)
    client = await hass_client()
    resp = await client.post(mav.UPLOAD_URL, data=_feed_zip())
    assert resp.status == 200, await resp.text()
    body = await resp.json()
    assert body["trains"] == 2 and body["audit"]["ok_ratio"] == 1.0
    assert mav_source.is_file()
    index = json.loads(gzip.decompress((card_assets / "mav-index.json.gz").read_bytes()))
    assert index["v"] == mav_index.SCHEMA and "905" in index["n"]
    assert "_stats" not in index


async def test_upload_rejects_non_admins_and_bad_files(
    hass: HomeAssistant, hass_client, hass_read_only_access_token, mav_source
) -> None:
    await _setup(hass)
    reader = await hass_client(hass_read_only_access_token)
    resp = await reader.post(mav.UPLOAD_URL, data=_feed_zip())
    # Authenticated but not an admin: forbidden, not unauthorized.
    assert resp.status == 403
    admin = await hass_client()
    resp = await admin.post(mav.UPLOAD_URL, data=b"not a zip")
    assert resp.status == 400
    assert not mav_source.exists()


async def test_rebuild_service(hass: HomeAssistant, card_assets, mav_source, monkeypatch) -> None:
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(_feed_zip())
    spawned: list[tuple] = []
    real_exec = asyncio.create_subprocess_exec

    async def spying_exec(*args, **kwargs):
        spawned.append(args)
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(mav.asyncio, "create_subprocess_exec", spying_exec)
    result = await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert result["trains"] == 2 and result["audit"]["trains_ok"] == 2
    assert (card_assets / "mav-index.json.gz").is_file()
    # The build ran in its own process, not in Home Assistant's executor.
    assert len(spawned) == 1 and spawned[0][0] == sys.executable
    assert Path(spawned[0][2]) == mav.BUILD_RUNNER


async def test_rebuild_service_reports_a_failed_build(hass: HomeAssistant, card_assets, mav_source) -> None:
    await _setup(hass)
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(b"PK not really a feed")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert err.value.translation_key == "mav_build_failed"
    assert "index build exited 1" in str(err.value)
    assert not (card_assets / "mav-index.json.gz").exists()


async def test_rebuild_service_is_admin_only(
    hass: HomeAssistant, hass_read_only_user, card_assets, mav_source
) -> None:
    await _setup(hass)
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(_feed_zip())
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    assert not (card_assets / "mav-index.json.gz").exists()


async def test_concurrent_uploads_use_their_own_temp_files(
    hass: HomeAssistant, hass_client, card_assets, mav_source
) -> None:
    await _setup(hass)
    client = await hass_client()
    first, second = await asyncio.gather(
        client.post(mav.UPLOAD_URL, data=_feed_zip()),
        client.post(mav.UPLOAD_URL, data=_feed_zip()),
    )
    assert first.status == 200 and second.status == 200
    assert not list(mav_source.parent.glob("*.upload"))


async def test_startup_rebuilds_a_missing_index(hass: HomeAssistant, card_assets, mav_source) -> None:
    """A HACS update replaces www/; the index comes back from the feed."""
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(_feed_zip())
    await _setup(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (card_assets / "mav-index.json.gz").is_file()
    assert hass.data[DOMAIN]["mav"]["audit"]["ok_ratio"] == 1.0
    # Built from this very feed: the next start leaves it alone.
    assert await mav.async_build(hass, card_assets) is None


def _write_feed(path: Path, version: str = "test-feed") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_feed_zip(version))


def test_staleness_follows_the_feed_not_the_clock(tmp_path: Path) -> None:
    """Which feed built the index is recorded by size and mtime, so an index
    finished after the next feed arrived (the startup build of the old feed
    held the lock while an upload replaced it) is still rebuilt."""
    src, out, state = tmp_path / "mav-gtfs.zip", tmp_path / "mav-index.json.gz", tmp_path / mav.STATE_NAME
    assert not mav._needs_build(src, out, state)
    _write_feed(src)
    assert mav._needs_build(src, out, state)

    out.write_bytes(b"index")
    mav._write_state(state, {"source": mav._fingerprint(src), "index": mav._fingerprint(out)})
    assert not mav._needs_build(src, out, state)

    # A new feed whose mtime is older than the index.
    _write_feed(src, "next-feed")
    before = out.stat().st_mtime_ns - 10**9
    os.utime(src, ns=(before, before))
    assert mav._needs_build(src, out, state)

    # A build that started and never finished is done again.
    mav._write_state(state, {"building": mav._fingerprint(src)})
    assert mav._needs_build(src, out, state)

    # An index this integration did not build (a release shipped it, the
    # update script restored it) is kept when it came from the same feed
    # version, whatever its mtime (HACS unpacks it after the feed arrived).
    state.unlink()
    mav_index.write({"v": mav_index.SCHEMA, "feed": "next-feed", "st": []}, out)
    os.utime(src, ns=(out.stat().st_mtime_ns + 10**9,) * 2)
    assert not mav._needs_build(src, out, state)
    mav_index.write({"v": mav_index.SCHEMA, "feed": "older-feed", "st": []}, out)
    os.utime(src, ns=(out.stat().st_mtime_ns - 10**9,) * 2)
    assert mav._needs_build(src, out, state)
    # The state's feed_version names this feed's version without opening the zip.
    mav._write_state(state, {"source": mav._fingerprint(src), "index": [0, 0], "feed_version": "older-feed"})
    assert not mav._needs_build(src, out, state)
    # Not an index at all, or one without a feed version: built again.
    out.write_bytes(b"index")
    assert mav._needs_build(src, out, state)


class _Upload(dict):
    """Just enough of an aiohttp request for MavGtfsUploadView.post."""

    def __init__(self, hass: HomeAssistant, body: bytes) -> None:
        super().__init__({KEY_HASS_USER: SimpleNamespace(is_admin=True)})
        self.app = {KEY_HASS: hass}

        async def chunks(_size):
            yield body

        self.content = SimpleNamespace(iter_chunked=chunks)


def _gate_builds(monkeypatch) -> tuple[asyncio.Event, asyncio.Event, list]:
    """Hold every build at its start until ``release`` is set."""
    started, release, calls = asyncio.Event(), asyncio.Event(), []
    real_build = mav._build

    async def gated_build(*args):
        calls.append(args)
        started.set()
        await release.wait()
        return await real_build(*args)

    monkeypatch.setattr(mav, "_build", gated_build)
    return started, release, calls


async def test_upload_build_outlives_a_client_that_gives_up(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch
) -> None:
    """curl --max-time or a proxy timeout cancels the request handler after
    the new feed replaced the old one; the build must still finish."""
    await _setup(hass)
    started, release, _calls = _gate_builds(monkeypatch)
    view = mav.MavGtfsUploadView(card_assets, lambda _hass: True)
    handler = asyncio.ensure_future(view.post(_Upload(hass, _feed_zip())))
    await started.wait()
    handler.cancel()
    with pytest.raises(asyncio.CancelledError):
        await handler

    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (card_assets / mav.INDEX_NAME).is_file()
    assert hass.data[DOMAIN]["mav"]["trains"] == 2
    assert not mav._needs_build(mav_source, card_assets / mav.INDEX_NAME, mav.state_path(hass))


async def test_waiting_builds_are_shared(hass: HomeAssistant, card_assets, mav_source, monkeypatch) -> None:
    """Requests that arrive while a build runs share one follow-up build,
    which reads the feed as it is when it starts."""
    await _setup(hass)
    _write_feed(mav_source)
    started, release, calls = _gate_builds(monkeypatch)
    first = asyncio.ensure_future(mav.async_build(hass, card_assets, force=True))
    await started.wait()
    second = asyncio.ensure_future(mav.async_build(hass, card_assets, force=True))
    third = asyncio.ensure_future(mav.async_build(hass, card_assets, force=True))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second, third)
    assert len(calls) == 2
    assert results[1] is results[2] and results[0]["trains"] == 2


SLOW_RUNNER = """import pathlib, sys, time
out = pathlib.Path(sys.argv[2])
(out.parent / (out.name + ".k1ll_me0")).write_bytes(b"half an index")
time.sleep(120)
"""


async def test_build_timeout_kills_the_process_and_its_temp_file(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch, tmp_path
) -> None:
    runner = tmp_path / "slow_runner.py"
    runner.write_text(SLOW_RUNNER, encoding="utf-8")
    monkeypatch.setattr(mav, "BUILD_RUNNER", runner)
    monkeypatch.setattr(mav, "BUILD_TIMEOUT", 1.5)
    procs = []
    real_exec = asyncio.create_subprocess_exec

    async def spying_exec(*args, **kwargs):
        procs.append(await real_exec(*args, **kwargs))
        return procs[-1]

    monkeypatch.setattr(mav.asyncio, "create_subprocess_exec", spying_exec)
    # The update script's own temp file is not ours to remove.
    (card_assets / "mav-index.json.gz.tmp").write_bytes(b"theirs")
    await _setup(hass)
    _write_feed(mav_source)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert err.value.translation_key == "mav_build_failed"
    assert "timed out after 1.5s" in str(err.value)
    assert procs[0].returncode == -signal.SIGKILL
    assert sorted(p.name for p in card_assets.glob("mav-index*")) == ["mav-index.json.gz.tmp"]
    # Thirty minutes of CPU at every start would not help: the next start
    # leaves this feed alone, and Repairs says so.
    assert not mav._needs_build(mav_source, card_assets / mav.INDEX_NAME, mav.state_path(hass))
    assert ir.async_get(hass).async_get_issue(DOMAIN, mav.ISSUE_BUILD_FAILED) is not None


async def test_kill_tolerates_a_process_that_already_exited(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch
) -> None:
    class ExitedProcess:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

        def kill(self):
            raise ProcessLookupError

        async def wait(self):
            return 0

    async def fake_exec(*args, **kwargs):
        return ExitedProcess()

    monkeypatch.setattr(mav.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(mav, "BUILD_TIMEOUT", 0.05)
    with pytest.raises(RuntimeError, match="timed out after 0.05s"):
        await mav._build(hass, mav_source, card_assets / mav.INDEX_NAME)


async def test_interpreter_that_does_not_start_is_not_a_missing_feed(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch, caplog
) -> None:
    _write_feed(mav_source)

    async def no_exec(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", sys.executable)

    monkeypatch.setattr(mav.asyncio, "create_subprocess_exec", no_exec)
    await _setup(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    # The startup build says so instead of returning quietly.
    assert "MÁV index build failed: could not start the index build" in caplog.text

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert err.value.translation_key == "mav_build_failed"
    assert "could not start the index build" in str(err.value)


def test_failed_audit_keeps_the_previous_index(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "build"
    work.mkdir()
    feed = work / "mav.zip"
    feed.write_bytes(_feed_zip())
    out = work / "mav-index.json.gz"
    out.write_bytes(b"previous index")

    def broken_audit(*_args, **_kwargs):
        raise RuntimeError("audit broke")

    monkeypatch.setattr(mav_index, "audit", broken_audit)
    with pytest.raises(RuntimeError, match="audit broke"):
        mav_index.build_and_audit(feed, out, min_trains=1)
    assert out.read_bytes() == b"previous index"
    assert sorted(p.name for p in work.iterdir()) == ["mav-index.json.gz", "mav.zip"]


def test_runner_prints_ascii_stats(tmp_path: Path, monkeypatch, capsys) -> None:
    """The pipe's encoding follows Home Assistant's locale; ASCII JSON reads
    back the same under any of them."""
    feed = tmp_path / "mav.zip"
    feed.write_bytes(_feed_zip("MÁV 2026 ősz"))
    monkeypatch.setattr(mav_index.os, "nice", lambda _inc: 0)
    assert mav_index._main(["mav_index.py", str(feed), str(tmp_path / "mav-index.json.gz")]) == 0
    printed = capsys.readouterr().out
    assert printed.isascii()
    assert json.loads(printed)["feed_version"] == "MÁV 2026 ősz"


def _index_bytes(card_assets: Path) -> bytes:
    return (card_assets / mav.INDEX_NAME).read_bytes()


async def test_bus_only_upload_keeps_the_good_feed_and_index(
    hass: HomeAssistant, hass_client, card_assets, mav_source
) -> None:
    """A wrong feed that is still GTFS (buses only) passes check_feed; its
    empty index must not replace the good one, nor the feed the good one."""
    await _setup(hass)
    client = await hass_client()
    assert (await client.post(mav.UPLOAD_URL, data=_feed_zip())).status == 200
    good_index, good_feed = _index_bytes(card_assets), mav_source.read_bytes()

    resp = await client.post(mav.UPLOAD_URL, data=_feed_zip("bus-feed", route_type="3"))
    assert resp.status == 422, await resp.text()
    body = await resp.json()
    assert body["written"] is False and "only 0 trains" in body["rejected"]
    assert _index_bytes(card_assets) == good_index
    assert mav_source.read_bytes() == good_feed
    assert not list(mav_source.parent.glob("*.upload"))
    notification = async_get_persistent_notifications(hass)[mav.NOTIFICATION_ID]
    assert "bus-feed was not written: only 0 trains" in notification["message"]
    # The feed on disk is still the one the index was built from.
    assert not mav._needs_build(mav_source, card_assets / mav.INDEX_NAME, mav.state_path(hass))
    # An upload that failed is the uploader's to fix: no Repairs issue.
    assert ir.async_get(hass).async_get_issue(DOMAIN, mav.ISSUE_BUILD_FAILED) is None


async def test_index_failing_its_audit_is_not_written(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch
) -> None:
    await _setup(hass)
    _write_feed(mav_source)
    await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    good_index = _index_bytes(card_assets)

    _write_feed(mav_source, "strict-feed")
    monkeypatch.setattr(mav, "MIN_OK", 1.01)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert err.value.translation_key == "mav_build_failed"
    assert "2 of 2 trains pass the audit" in str(err.value)
    assert _index_bytes(card_assets) == good_index
    message = async_get_persistent_notifications(hass)[mav.NOTIFICATION_ID]["message"]
    assert "strict-feed was not written" in message and "previous index stays" in message


async def test_failed_feed_is_not_built_again_at_every_start(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch
) -> None:
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(b"PK not really a feed")
    spawned: list[tuple] = []
    real_exec = asyncio.create_subprocess_exec

    async def spying_exec(*args, **kwargs):
        spawned.append(args)
        return await real_exec(*args, **kwargs)

    monkeypatch.setattr(mav.asyncio, "create_subprocess_exec", spying_exec)
    await _setup(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(spawned) == 1
    state = mav._read_state(mav.state_path(hass))
    assert state["failed"] == mav._fingerprint(mav_source) and "exited 1" in state["error"]
    issues = ir.async_get(hass)
    issue = issues.async_get_issue(DOMAIN, mav.ISSUE_BUILD_FAILED)
    assert issue is not None and issue.translation_placeholders["path"] == str(mav_source)

    # The next start: no build, and the Repairs issue (gone with the
    # restart) is back.
    ir.async_delete_issue(hass, DOMAIN, mav.ISSUE_BUILD_FAILED)
    await mav.async_build_if_needed(hass, card_assets)
    assert len(spawned) == 1
    assert issues.async_get_issue(DOMAIN, mav.ISSUE_BUILD_FAILED) is not None

    # The rebuild service still tries; a new feed builds and clears it all.
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert len(spawned) == 2
    _write_feed(mav_source)
    await mav.async_build_if_needed(hass, card_assets)
    assert len(spawned) == 3
    assert (card_assets / mav.INDEX_NAME).is_file()
    assert "failed" not in mav._read_state(mav.state_path(hass))
    assert issues.async_get_issue(DOMAIN, mav.ISSUE_BUILD_FAILED) is None


async def test_forced_request_upgrades_a_queued_build(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch
) -> None:
    """A rebuild asked for while a plain build waits makes that build a
    forced one, instead of queueing a second full build behind it."""
    await _setup(hass)
    _write_feed(mav_source)
    started, release, calls = _gate_builds(monkeypatch)
    first = asyncio.ensure_future(mav.async_build(hass, card_assets, force=True))
    await started.wait()
    plain = asyncio.ensure_future(mav.async_build(hass, card_assets))
    forced = asyncio.ensure_future(mav.async_build(hass, card_assets, force=True))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, plain, forced)
    # The first build made the index current: a plain build alone would
    # have returned None without building.
    assert len(calls) == 2
    assert results[1] is results[2] and results[1]["trains"] == 2


async def test_stopping_cancels_a_running_build(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch, tmp_path
) -> None:
    """Home Assistant cancels background tasks when it stops: the process
    dies with its temp file, and the next start builds again (a stop is
    not a failed feed)."""
    runner = tmp_path / "slow_runner.py"
    runner.write_text(SLOW_RUNNER, encoding="utf-8")
    monkeypatch.setattr(mav, "BUILD_RUNNER", runner)
    procs = []
    real_exec = asyncio.create_subprocess_exec

    async def spying_exec(*args, **kwargs):
        procs.append(await real_exec(*args, **kwargs))
        return procs[-1]

    monkeypatch.setattr(mav.asyncio, "create_subprocess_exec", spying_exec)
    await _setup(hass)
    _write_feed(mav_source)
    task = mav._build_task(hass, card_assets, True)
    temp = card_assets / (mav.INDEX_NAME + ".k1ll_me0")
    for _ in range(200):
        if temp.exists():
            break
        await asyncio.sleep(0.05)
    assert temp.exists()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert procs[0].returncode == -signal.SIGKILL
    assert not temp.exists()
    state = mav._read_state(mav.state_path(hass))
    assert state["building"] and "failed" not in state
    assert mav._needs_build(mav_source, card_assets / mav.INDEX_NAME, mav.state_path(hass))
    assert ir.async_get(hass).async_get_issue(DOMAIN, mav.ISSUE_BUILD_FAILED) is None


async def test_stopping_cancels_a_queued_build(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch
) -> None:
    """A queued build cancelled before it started is not handed to the next
    request, which gets a build of its own."""
    await _setup(hass)
    _write_feed(mav_source)
    started, release, calls = _gate_builds(monkeypatch)
    first = asyncio.ensure_future(mav.async_build(hass, card_assets, force=True))
    await started.wait()
    queued = mav._build_task(hass, card_assets, True)
    assert hass.data[DOMAIN]["mav_queued"].task is queued

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert "mav_queued" not in hass.data[DOMAIN]
    later = asyncio.ensure_future(mav.async_build(hass, card_assets, force=True))
    await asyncio.sleep(0)
    release.set()
    first_stats, later_stats = await asyncio.gather(first, later)
    assert len(calls) == 2 and later_stats["trains"] == 2


FAILING_RUNNER = """import pathlib, sys
out = pathlib.Path(sys.argv[2])
(out.parent / (out.name + ".f4iled00")).write_bytes(b"half an index")
sys.exit("MemoryError")
"""


async def test_temp_indexes_go_before_a_build_and_after_a_failed_one(
    hass: HomeAssistant, card_assets, mav_source, monkeypatch, tmp_path
) -> None:
    """www/ is served: a killed Home Assistant's temp index goes before the
    next build, and a build process that dies leaves none behind."""
    (card_assets / (mav.INDEX_NAME + ".0ld_k1ll")).write_bytes(b"from a killed build")
    await _setup(hass)
    _write_feed(mav_source)
    await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert sorted(p.name for p in card_assets.glob("mav-index*")) == [mav.INDEX_NAME]

    runner = tmp_path / "failing_runner.py"
    runner.write_text(FAILING_RUNNER, encoding="utf-8")
    monkeypatch.setattr(mav, "BUILD_RUNNER", runner)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert "exited 1: MemoryError" in str(err.value)
    assert sorted(p.name for p in card_assets.glob("mav-index*")) == [mav.INDEX_NAME]


async def test_setup_removes_uploads_left_behind(hass: HomeAssistant, mav_source) -> None:
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    stale = mav_source.with_name(f"{mav_source.name}.{'ab' * 16}.upload")
    stale.write_bytes(b"half an upload")
    other = mav_source.with_name("notes.upload")
    other.write_bytes(b"not ours")
    await _setup(hass)
    assert not stale.exists()
    assert other.exists()
