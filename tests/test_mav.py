"""MÁV route index: upload, build at startup, rebuild service, audit."""

import gzip
import io
import json
import math
import zipfile
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
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
    assert resp.status == 401
    admin = await hass_client()
    resp = await admin.post(mav.UPLOAD_URL, data=b"not a zip")
    assert resp.status == 400
    assert not mav_source.exists()


async def test_rebuild_service(hass: HomeAssistant, card_assets, mav_source) -> None:
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(_feed_zip())
    result = await hass.services.async_call(DOMAIN, SERVICE_REBUILD_MAV, {}, blocking=True, return_response=True)
    assert result["trains"] == 2 and result["audit"]["trains_ok"] == 2
    assert (card_assets / "mav-index.json.gz").is_file()


async def test_startup_rebuilds_a_missing_index(hass: HomeAssistant, card_assets, mav_source) -> None:
    """A HACS update replaces www/; the index comes back from the feed."""
    mav_source.parent.mkdir(parents=True, exist_ok=True)
    mav_source.write_bytes(_feed_zip())
    await _setup(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (card_assets / "mav-index.json.gz").is_file()
    assert hass.data[DOMAIN]["mav"]["audit"]["ok_ratio"] == 1.0
