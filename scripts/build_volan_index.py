#!/usr/bin/env python3
"""Build the compact Volán GTFS index used by Hungarian transport (4-digit coach routes)."""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

GTFS_URL = "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"
OUT = Path("volan-index.json.gz")


def parent(sid: str) -> str:
    parts = sid.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return sid


def hm(t: str | None) -> int | None:
    if not t:
        return None
    a = t.split(":")
    return int(a[0]) * 60 + int(a[1])


def stop_num(pid: str) -> str:
    m = re.search(r"(\d+)$", pid)
    return m.group(1) if m else ""


def feed_version(z: zipfile.ZipFile) -> str:
    try:
        with z.open("feed_info.txt") as f:
            rows = list(csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")))
        if rows:
            return (rows[0].get("feed_version") or "").strip()
    except KeyError:
        pass
    return ""


def build_index(zpath: Path, out: Path) -> dict:
    z = zipfile.ZipFile(zpath)
    sid2p: dict[str, str] = {}
    sid2n: dict[str, str] = {}
    with z.open("stops.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            sid2p[row["stop_id"]] = parent(row["stop_id"])
            sid2n[row["stop_id"]] = row.get("stop_name") or ""

    pname: dict[str, str] = {}
    for sid, p in sid2p.items():
        pname.setdefault(p, sid2n[sid])

    routes: dict[str, str] = {}
    long_routes: set[str] = set()
    with z.open("routes.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            s = (row.get("route_short_name") or "").strip()
            routes[row["route_id"]] = s
            if re.fullmatch(r"\d{4}", s):
                long_routes.add(row["route_id"])

    trips_meta: dict[str, tuple[str, str]] = {}
    long_trips: set[str] = set()
    with z.open("trips.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            rid = row.get("route_id") or ""
            trips_meta[row["trip_id"]] = (rid, row.get("service_id") or "")
            if rid in long_routes:
                long_trips.add(row["trip_id"])

    cal: dict[str, list[str]] = {}
    with z.open("calendar.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            days = "".join(
                row[d]
                for d in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            )
            cal[row["service_id"]] = [days, row["start_date"], row["end_date"]]

    ex_add: dict[str, list[str]] = defaultdict(list)
    ex_rem: dict[str, list[str]] = defaultdict(list)
    with z.open("calendar_dates.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            sid = row["service_id"]
            (ex_add if row.get("exception_type") == "1" else ex_rem)[sid].append(row["date"])

    seqs: dict[str, list[tuple[str, int]]] = {}
    cur = None
    seq: list[tuple[str, int | None]] = []

    def flush(tid: str, items: list[tuple[str, int | None]]) -> None:
        if tid not in long_trips or not items:
            return
        pts: list[tuple[str, int]] = []
        for sid, m in items:
            p = sid2p.get(sid, sid)
            if m is None:
                continue
            if pts and pts[-1][0] == p:
                continue
            pts.append((p, m))
        if len(pts) >= 2:
            seqs[tid] = pts

    with z.open("stop_times.txt") as f:
        for row in csv.DictReader(io.TextIOWrapper(f, "utf-8-sig")):
            tid = row["trip_id"]
            if cur is None:
                cur = tid
            if tid != cur:
                flush(cur, seq)
                seq = []
                cur = tid
            if tid in long_trips:
                seq.append(
                    (
                        row["stop_id"],
                        hm(row.get("departure_time") or row.get("arrival_time")),
                    )
                )
        if cur:
            flush(cur, seq)

    used_p: set[str] = set()
    used_s: set[str] = set()
    for tid, pts in seqs.items():
        used_s.add(trips_meta[tid][1])
        for p, _m in pts:
            used_p.add(p)

    parents = sorted(used_p, key=lambda p: (pname.get(p, ""), p))
    p_ix = {p: i for i, p in enumerate(parents)}
    services = sorted(used_s)
    s_ix = {s: i for i, s in enumerate(services)}

    trip_rows = []
    for tid, pts in seqs.items():
        rid, sid = trips_meta[tid]
        trip_rows.append(
            [
                s_ix[sid],
                routes[rid],
                [p_ix[p] for p, _m in pts],
                [m for _p, m in pts],
            ]
        )

    cal_rows = []
    add_rows = []
    rem_rows = []
    for sid in services:
        days, start, end = cal.get(sid, ["0000000", "20200101", "20991231"])
        cal_rows.append([days, start, end])
        add_rows.append(ex_add.get(sid, []))
        rem_rows.append(ex_rem.get(sid, []))

    version = feed_version(z)
    idx = {
        "v": 26,
        "feed": version,
        "s": [[p, pname.get(p, p), stop_num(p)] for p in parents],
        "c": cal_rows,
        "a": add_rows,
        "r": rem_rows,
        "t": trip_rows,
    }
    raw = json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=9))
    tmp.replace(out)
    return {
        "feed_version": version,
        "trips": len(trip_rows),
        "stops": len(parents),
        "bytes": out.stat().st_size,
    }


def main() -> None:
    zpath = Path("volanbusz_gtfs.zip")
    if not zpath.exists():
        print("downloading", GTFS_URL)
        urllib.request.urlretrieve(GTFS_URL, zpath)
    info = build_index(zpath, OUT)
    print("wrote", OUT, info)


if __name__ == "__main__":
    main()
