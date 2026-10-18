"""MÁV route index: upload, build at startup, rebuild service, audit."""

import asyncio
import gzip
import io
import json
import math
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized
from homeassistant.setup import async_setup_component

from custom_components.hungarian_transport import mav, mav_index
from custom_components.hungarian_transport.const import DOMAIN, SERVICE_REBUILD_MAV

SZF = (47.18361, 18.42472)
MID = (47.31570, 18.78800)
KEL = (47.46432, 19.02063)
SZOMBATHELY = (47.23510, 16.63160)


def _rails(a, b, n=30):
    """A bending line between two stations, like a pfaedle shape."""
    out = []
    for i in range(n):
        t = i / (n - 1)
        bend = 0.01 * math.sin(t * math.pi)
        out.append((a[0] + (b[0] - a[0]) * t + bend, a[1] + (b[1] - a[1]) * t))
    return out


def _feed_zip() -> bytes:
    """Train 905 runs Szombathely - Kelenföld on one day and Székesfehérvár -
    Kelenföld on another (two stop patterns); a third trip has a shape that
    misses its middle station and must fall back to the station line."""
    long_line = _rails(SZOMBATHELY, SZF) + _rails(SZF, MID)[1:] + _rails(MID, KEL)[1:]
    short_line = _rails(SZF, MID) + _rails(MID, KEL)[1:]
    files = {
        "agency.txt": "agency_id,agency_name\nMAV,MÁV\n",
        "feed_info.txt": "feed_publisher_name,feed_version\nMÁV,test-feed\n",
        "routes.txt": "route_id,route_short_name,route_long_name,route_type\nR905,,IC 905 BAKONY,2\nR858,,IC 858,2\n",
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
    cmd = [sys.executable, "-I", str(mav.BUILD_RUNNER), str(feed), str(out)]
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    stats = json.loads(done.stdout)
    assert stats["trains"] == 2 and stats["audit"]["ok_ratio"] == 1.0
    assert "_loaded" not in json.loads(gzip.decompress(out.read_bytes()))

    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    done = subprocess.run(cmd[:-2] + [str(bad), str(out)], capture_output=True, text=True, timeout=60)
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
