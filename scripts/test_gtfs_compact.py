#!/usr/bin/env python3
"""Sanity checks for the compact GTFS builder. No live downloads."""
from __future__ import annotations

import gzip
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gtfs_compact import hm, load_parts, write_index  # noqa: E402


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

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
