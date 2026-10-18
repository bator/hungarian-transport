#!/usr/bin/env python3
"""Sanity checks for the compact GTFS builder. No live downloads."""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_city_index import CITY_FEEDS  # noqa: E402
from gtfs_compact import (  # noqa: E402
    SHAPE_SHARDS,
    assert_public_http_url,
    index_shapes,
    simplify_shape,
    catalog_url,
    decode_polyline,
    encode_polyline,
    hm,
    line_code,
    load_parts,
    open_gtfs,
    resolved_url,
    train_number,
    write_index,
)
from build_mav_index import _mav_module  # noqa: E402


def gtfs_zip(files: dict[str, str]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    path = Path(tmp.name)
    with zipfile.ZipFile(path, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return path


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    failed = False
    failed |= not check("hm empty", hm("") is None)
    failed |= not check("hm short", hm("8") is None)
    failed |= not check("hm garbage", hm("ab:cd") is None)
    failed |= not check("hm overnight", hm("25:10:00") == 1510)
    failed |= not check("train_number IC 856", train_number("IC 856 TÓPART") == "856")
    failed |= not check("train_number 9400", train_number("9400") == "9400")
    failed |= not check("train_number ignores G43", train_number("G43") == "")
    failed |= not check("line_code G43", line_code("G43", "") == "G43")
    failed |= not check("line_code S40 from long", line_code("", "S40") == "S40")
    rt = encode_polyline([(47.49889, 19.025), (47.18361, 18.42472), (46.44083, 16.98667)])
    dec = decode_polyline(rt)
    failed |= not check(
        "polyline roundtrip 3 pts",
        len(dec) == 3 and abs(dec[0][0] - 47.49889) < 1e-5 and abs(dec[-1][1] - 16.98667) < 1e-5,
        str(dec),
    )

    ids = [f["id"] for f in CITY_FEEDS]
    failed |= not check("city feeds unique ids", len(ids) == len(set(ids)), str(ids))
    for expected in (
        "miskolc",
        "pecs",
        "szeged",
        "szombathely",
        "veszprem",
        "paks",
        "dombovar",
        "kecskemet-kisvasut",
        "budaors",
        "bahart",
        "hodmezovasarhely",
        "kaposvar",
        "kiskunhalas",
        "szekesfehervar",
        "tatabanya",
        "varpalota",
    ):
        failed |= not check(f"city feed {expected}", expected in ids)
    failed |= not check("weekendbus stays out", "weekendbus" not in ids)
    failed |= not check("debrecen stays out", "debrecen" not in ids)

    os.environ["HUNGARIAN_TRANSPORT_SKIP_CATALOG"] = "1"
    bahart = next(f for f in CITY_FEEDS if f["id"] == "bahart")
    failed |= not check("bahart has public fallback", "menetbrand.com/download/bahart" in bahart["url"])
    failed |= not check("bahart url_env", bahart.get("url_env") == "BAHART_GTFS_URL")
    failed |= not check("bahart catalog_query", bahart.get("catalog_query") == "bahart")

    prev = os.environ.get("BAHART_GTFS_URL")
    os.environ["BAHART_GTFS_URL"] = "https://example.invalid/official.zip"
    try:
        failed |= not check(
            "bahart env override",
            resolved_url(bahart) == "https://example.invalid/official.zip",
        )
    finally:
        if prev is None:
            os.environ.pop("BAHART_GTFS_URL", None)
        else:
            os.environ["BAHART_GTFS_URL"] = prev
    failed |= not check("bahart falls back without env", resolved_url(bahart) == bahart["url"])

    files = {
        "agency.txt": "agency_id,agency_name\nA,Szeged - Local\n",
        "stops.txt": "stop_id,stop_name\n1,Alpha\n2,Beta\n",
        "routes.txt": "route_id,route_short_name,agency_id\nR1,12,A\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,S1,T1\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nS1,1,1,1,1,1,1,1,20200101,20991231\n",
        "calendar_dates.txt": "service_id,date,exception_type\nS1,20260101,1\nS1,20260102,2\nS1,20260103,99\n",
        # Interleaved trip ids and reversed stop_sequence — the parser must still
        # emit Alpha then Beta.
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,08:10:00,08:10:00,2,2\n"
            "T1,08:00:00,08:00:00,1,1\n"
        ),
    }
    zpath = gtfs_zip(files)
    try:
        parts = load_parts(zpath, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT")
        trip = parts[0]["t"][0]
        failed |= not check("stop_times sorted by sequence", trip[2] == [0, 1], str(trip))
        failed |= not check("minutes follow the sorted stops", trip[3] == [480, 490], str(trip[3]))
        failed |= not check("exception_type 1 is add", parts[0]["a"][0] == ["20260101"], str(parts[0]["a"]))
        failed |= not check("exception_type 2 is remove", parts[0]["r"][0] == ["20260102"], str(parts[0]["r"]))
        failed |= not check("unknown exception_type ignored", "20260103" not in parts[0]["a"][0] + parts[0]["r"][0])
        failed |= not check("route_type defaults to bus", trip[4] == 3, str(trip))

        tram_files = dict(files)
        tram_files["routes.txt"] = "route_id,route_short_name,agency_id,route_type\nR1,2,A,0\n"
        tz = gtfs_zip(tram_files)
        try:
            tparts = load_parts(tz, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT")
            failed |= not check("route_type 0 (tram) kept on the trip", tparts[0]["t"][0][4] == 0, str(tparts[0]["t"][0]))
            tout = Path(tempfile.mkdtemp()) / "tram.json.gz"
            write_index(tparts, tout, with_operators=True)
            tidx = json.loads(gzip.decompress(tout.read_bytes()))
            failed |= not check("write_index keeps route_type", tidx["t"][0][4] == 0 and tidx["v"] == 29, str(tidx["t"][0]))
        finally:
            tz.unlink()

        # Distinct stations named like jk_stop_8 must not collapse to one parent.
        rail_files = dict(files)
        rail_files["stops.txt"] = "stop_id,stop_name\njk_stop_8,Kiskunmajsa\njk_stop_9,Kecskemét KK\n"
        rail_files["stop_times.txt"] = (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,10:30:00,10:30:00,jk_stop_8,1\n"
            "T1,13:30:00,13:30:00,jk_stop_9,2\n"
        )
        rz = gtfs_zip(rail_files)
        try:
            rparts = load_parts(rz, lambda short, *_: bool(short), op_id="kisvasut", prefix="kisvasut:")
            failed |= not check("jk_stop_N stays two stations", len(rparts[0]["s"]) == 2, str(rparts[0]["s"]))
            failed |= not check("jk_stop_N keeps the trip", len(rparts[0]["t"]) == 1, str(rparts[0]["t"]))
        finally:
            rz.unlink()

        # Stop coordinates and shapes.txt end up in the index and its shapes folder.
        shape_files = dict(files)
        shape_files["stops.txt"] = "stop_id,stop_name,stop_lat,stop_lon\n1,Alpha,46.25000,20.14000\n2,Beta,46.26000,20.16000\n"
        shape_files["trips.txt"] = "route_id,service_id,trip_id,shape_id\nR1,S1,T1,SH1\n"
        # SH2 is split in two runs (ungrouped file); SH1 has a straight-line
        # midpoint that simplification must drop.
        shape_files["shapes.txt"] = (
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "SH1,46.25000,20.14000,1\nSH2,47.0,19.0,1\nSH1,46.25500,20.15000,2\n"
            "SH1,46.26000,20.16000,3\nSH2,47.1,19.1,2\n"
        )
        sz = gtfs_zip(shape_files)
        try:
            sparts = load_parts(sz, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT")
            sdir = Path(tempfile.mkdtemp())
            sout = sdir / "city-index.json.gz"
            sinfo = write_index(sparts, sout, with_operators=True)
            sidx = json.loads(gzip.decompress(sout.read_bytes()))
            folder = (sidx.get("sh") or {}).get("dir", "")
            failed |= not check("stop coordinates stored as 1e-5 degrees",
                                sidx["g"] == [4625000, 2014000, 4626000, 2016000], str(sidx["g"]))
            failed |= not check("trip points at shape 0", sidx["t"][0][5] == 0, str(sidx["t"][0]))
            failed |= not check("index head names its shapes folder",
                                index_shapes(sout) == folder and folder.startswith("city-shapes-"), folder)
            shards = sorted(p.name for p in (sdir / folder).iterdir())
            failed |= not check("shapes folder has every shard", len(shards) == SHAPE_SHARDS, str(len(shards)))
            shard0 = json.loads(gzip.decompress((sdir / folder / "00.json.gz").read_bytes()))
            pts = decode_polyline(shard0["p"][0])
            failed |= not check("straight midpoint simplified away", len(pts) == 2, str(pts))
            failed |= not check("only used shapes are written", sinfo["shapes"] == 1, str(sinfo))
            # A rebuild with other shapes swaps the folder and removes the old one.
            shape_files["shapes.txt"] = shape_files["shapes.txt"].replace("46.26000,20.16000,3", "46.27000,20.17000,3")
            sz2 = gtfs_zip(shape_files)
            try:
                write_index(load_parts(sz2, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT"),
                            sout, with_operators=True)
                folders = [p.name for p in sdir.iterdir() if p.is_dir()]
                failed |= not check("old shapes folder pruned after swap",
                                    len(folders) == 1 and folders[0] == index_shapes(sout) != folder, str(folders))
            finally:
                sz2.unlink()
        finally:
            sz.unlink()
        from gtfs_compact import load_shapes, open_gtfs_zip  # noqa: E402
        uz = gtfs_zip(shape_files)
        try:
            with open_gtfs_zip(uz) as z:
                ushapes = load_shapes(z)
            failed |= not check("ungrouped shape is read whole",
                                len(decode_polyline(ushapes.get("SH2", ""))) == 2, str(ushapes.get("SH2")))
        finally:
            uz.unlink()
        failed |= not check("simplify keeps a real corner",
                            len(simplify_shape([(47.0, 19.0), (47.001, 19.0), (47.001, 19.001)], 10)) == 3)

        volan_files = dict(files)
        volan_files["agency.txt"] = "agency_id,agency_name\nA,Szeged\n"
        volan_files["routes.txt"] = "route_id,route_short_name,agency_id\nR1,1,A\n"
        vz = gtfs_zip(volan_files)
        try:
            vparts = load_parts(vz, lambda short, *_: bool(short), split_agencies=True)
            failed |= not check(
                "volan local op_id is prefixed",
                vparts[0]["op_id"] == "volan-szeged",
                vparts[0]["op_id"],
            )
        finally:
            vz.unlink()

        out = Path(tempfile.mkdtemp()) / "idx.json.gz"
        info = write_index(parts, out, with_operators=True)
        failed |= not check("write_index emits trips", info["trips"] == 1)

        fat = {
            "v": 27,
            "s": [],
            "c": [],
            "a": [],
            "r": [],
            "t": [[0, "x", [0, 1], [1, 2]]] * 80,
        }
        fat_path = Path(tempfile.mkdtemp()) / "fat.json.gz"
        fat_path.write_bytes(gzip.compress(json.dumps(fat).encode("utf-8")))
        try:
            write_index(parts, fat_path, with_operators=True)
            failed |= not check("sanity threshold refuses a collapse", False, "wrote anyway")
        except RuntimeError as err:
            failed |= not check("sanity threshold refuses a collapse", "refusing" in str(err), str(err))
    finally:
        zpath.unlink()

    html = Path(tempfile.mkdtemp()) / "not.zip"
    html.write_text("<html>nope</html>")
    try:
        load_parts(html, lambda *_: True)
        failed |= not check("html rejected as zip", False)
    except Exception:
        failed |= not check("html rejected as zip", True)

    def url_ok(u: str) -> bool:
        try:
            assert_public_http_url(u)
            return True
        except ValueError:
            return False

    failed |= not check("https public url allowed", url_ok("https://gtfs.kti.hu/file.zip"))
    failed |= not check("file scheme blocked", not url_ok("file:///config/www/x.zip"))
    failed |= not check("loopback blocked", not url_ok("http://127.0.0.1/x.zip"))
    failed |= not check("rfc1918 blocked", not url_ok("http://192.168.1.1/x.zip"))
    failed |= not check("supervisor host blocked", not url_ok("http://supervisor/core"))
    import inspect
    cat_src = inspect.getsource(catalog_url)
    failed |= not check(
        "catalog prefers MDB HU filter over Transitland first hit",
        cat_src.find("_mdb_url") < cat_src.find("_transitland_url")
        and "_safe_http_url" in cat_src,
    )

    szf = (47.183611, 18.424722)
    mid = (47.3167, 18.7908)
    kel = (47.464444, 19.02)
    mav = _mav_module()
    chord = mav.RouteGrid([szf, kel])
    failed |= not check(
        "2-point chord misses an intermediate station",
        chord.gap(mid) > mav.STOP_TOLERANCE_M,
        str(round(chord.gap(mid))),
    )
    failed |= not check(
        "2-point chord passes its own end stations",
        chord.gap(szf) < 1 and chord.gap(kel) < 1,
    )

    stub_dir = Path(tempfile.mkdtemp()) / "official-chord"
    stub_dir.mkdir()
    (stub_dir / "agency.txt").write_text("agency_id,agency_name\nMAV,MÁV\n")
    (stub_dir / "routes.txt").write_text(
        "route_id,route_short_name,route_long_name,route_type\n"
        "R857,,IC 857 TÓPART,2\n"
    )
    (stub_dir / "trips.txt").write_text(
        "route_id,service_id,trip_id,shape_id\nR857,S1,T857,shp857\n"
    )
    (stub_dir / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n"
        f"838,Székesfehérvár,{szf[0]},{szf[1]}\n"
        f"900,Martonvásár,{mid[0]},{mid[1]}\n"
        f"3640,Budapest-Kelenföld,{kel[0]},{kel[1]}\n"
    )
    (stub_dir / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T857,07:00:00,07:00:00,838,1\n"
        "T857,07:25:00,07:25:00,900,2\n"
        "T857,07:50:00,07:50:00,3640,3\n"
    )
    (stub_dir / "shapes.txt").write_text(
        "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        f"shp857,{szf[0]},{szf[1]},0\n"
        f"shp857,{kel[0]},{kel[1]},1\n"
    )
    (stub_dir / "feed_info.txt").write_text("feed_publisher_name,feed_version\nMÁV,official-stub\n")
    stub_payload = mav.build(stub_dir)
    stub_var = (stub_payload["n"].get("857") or [[None, []]])[0]
    stub_geom = decode_polyline(stub_payload["p"][stub_var[0]]) if stub_var[0] is not None else []
    failed |= not check(
        "official 2-point chord replaced by stop sequence",
        stub_payload["_stats"]["stop_lines"] >= 1 and len(stub_geom) >= 3,
        f"stats={stub_payload['_stats']} npts={len(stub_geom)}",
    )

    rail_dir = Path(tempfile.mkdtemp()) / "pfaedle-out"
    rail_dir.mkdir()
    szf = (47.183611, 18.424722)
    kel = (47.464444, 19.02)
    npts = 41
    shape_rows = ["shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence"]
    for i in range(npts):
        t = i / (npts - 1)
        # A curving line (like the rails), so simplification keeps its bends.
        bend = 0.02 * math.sin(t * math.pi * 4)
        lat = szf[0] + (kel[0] - szf[0]) * t + bend
        lon = szf[1] + (kel[1] - szf[1]) * t
        shape_rows.append(f"shp857,{lat:.6f},{lon:.6f},{i}")
    (rail_dir / "agency.txt").write_text("agency_id,agency_name\nMAV,MÁV\n")
    (rail_dir / "routes.txt").write_text(
        "route_id,route_short_name,route_long_name,route_type\n"
        "R857,,IC 857 TÓPART,2\n"
    )
    (rail_dir / "trips.txt").write_text(
        "route_id,service_id,trip_id,shape_id\nR857,S1,T857,shp857\n"
    )
    (rail_dir / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n"
        f"838,Székesfehérvár,{szf[0]},{szf[1]}\n"
        f"3640,Budapest-Kelenföld,{kel[0]},{kel[1]}\n"
    )
    (rail_dir / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T857,07:00:00,07:00:00,838,1\n"
        "T857,07:50:00,07:50:00,3640,2\n"
    )
    (rail_dir / "shapes.txt").write_text("\n".join(shape_rows) + "\n")
    (rail_dir / "feed_info.txt").write_text("feed_publisher_name,feed_version\nMÁV,test-pfaedle\n")
    try:
        with open_gtfs(rail_dir) as src:
            failed |= not check("open_gtfs directory", "shapes.txt" in src.namelist())
        idx_out = Path(tempfile.mkdtemp()) / "mav-index.json.gz"
        payload = mav.build(rail_dir)
        info = mav.write(payload, idx_out)
        idx = json.loads(gzip.open(idx_out).read())
        var = (idx["n"].get("857") or [[None, []]])[0]
        ix = var[0]
        geom = decode_polyline(idx["p"][ix]) if ix is not None else []
        failed |= not check("pfaedle dir indexes 857", ix is not None, str(idx.get("n")))
        failed |= not check(
            "variant lists its own stations",
            [idx["st"][i][0] for i in var[1]] == ["Székesfehérvár", "Budapest-Kelenföld"],
            str(var[1]),
        )
        failed |= not check(
            "pfaedle dir keeps the bends of its shape",
            len(geom) >= 5,
            f"npts={len(geom)} info={info}",
        )
        failed |= not check(
            "pfaedle dir spans Szfvár–Kelenföld",
            geom and abs(geom[0][0] - szf[0]) < 1e-3 and abs(geom[-1][1] - kel[1]) < 1e-3,
            str(geom[:1] + geom[-1:]),
        )
    finally:
        pass

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
