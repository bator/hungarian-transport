#!/usr/bin/env python3
"""Compact a GTFS zip into the JSON the Lovelace card decompresses."""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

RoutePred = Callable[[str, str, str], bool]


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


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()
    folded = re.sub(r"^.*\s+-\s+", "", folded)
    folded = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return folded or "local"


def _open_csv(z: zipfile.ZipFile, name: str):
    names = {n.lower(): n for n in z.namelist()}
    real = names.get(name.lower())
    if not real:
        raise FileNotFoundError(name)
    return csv.DictReader(io.TextIOWrapper(z.open(real), "utf-8-sig"))


def load_parts(
    zpath: Path,
    include_route: RoutePred,
    *,
    prefix: str = "",
    split_agencies: bool = False,
    op_id: str = "",
    op_name: str = "",
    skip_agency: Callable[[str], bool] | None = None,
) -> list[dict]:
    """Parse one GTFS zip into one or more operator parts."""
    z = zipfile.ZipFile(zpath)
    version = feed_version(z)

    agencies: dict[str, str] = {}
    try:
        for row in _open_csv(z, "agency.txt"):
            aid = row.get("agency_id") or ""
            agencies[aid] = (row.get("agency_name") or aid or op_name).strip()
    except FileNotFoundError:
        pass

    sid2p: dict[str, str] = {}
    sid2n: dict[str, str] = {}
    for row in _open_csv(z, "stops.txt"):
        raw = row["stop_id"]
        sid2p[raw] = parent(raw)
        sid2n[raw] = row.get("stop_name") or ""

    pname: dict[str, str] = {}
    for sid, p in sid2p.items():
        pname.setdefault(p, sid2n[sid])

    routes: dict[str, str] = {}
    route_agency: dict[str, str] = {}
    keep_routes: set[str] = set()
    for row in _open_csv(z, "routes.txt"):
        rid = row["route_id"]
        short = (row.get("route_short_name") or "").strip() or (row.get("route_long_name") or "").strip()
        aid = row.get("agency_id") or ""
        aname = agencies.get(aid, aid or op_name)
        if skip_agency and skip_agency(aname):
            continue
        if not include_route(short, aname, aid):
            continue
        routes[rid] = short
        route_agency[rid] = aid
        keep_routes.add(rid)

    trips_meta: dict[str, tuple[str, str]] = {}
    keep_trips: set[str] = set()
    for row in _open_csv(z, "trips.txt"):
        rid = row.get("route_id") or ""
        tid = row["trip_id"]
        trips_meta[tid] = (rid, row.get("service_id") or "")
        if rid in keep_routes:
            keep_trips.add(tid)

    cal: dict[str, list[str]] = {}
    try:
        for row in _open_csv(z, "calendar.txt"):
            days = "".join(
                row.get(d, "0")
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
            cal[row["service_id"]] = [days, row.get("start_date") or "20200101", row.get("end_date") or "20991231"]
    except FileNotFoundError:
        pass

    ex_add: dict[str, list[str]] = defaultdict(list)
    ex_rem: dict[str, list[str]] = defaultdict(list)
    try:
        for row in _open_csv(z, "calendar_dates.txt"):
            sid = row["service_id"]
            (ex_add if row.get("exception_type") == "1" else ex_rem)[sid].append(row["date"])
    except FileNotFoundError:
        pass

    seqs: dict[str, list[tuple[str, int]]] = {}
    cur = None
    seq: list[tuple[str, int | None]] = []

    def flush(tid: str, items: list[tuple[str, int | None]]) -> None:
        if tid not in keep_trips or not items:
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

    for row in _open_csv(z, "stop_times.txt"):
        tid = row["trip_id"]
        if cur is None:
            cur = tid
        if tid != cur:
            flush(cur, seq)
            seq = []
            cur = tid
        if tid in keep_trips:
            seq.append((row["stop_id"], hm(row.get("departure_time") or row.get("arrival_time"))))
    if cur:
        flush(cur, seq)

    def pack(op: str, name: str, trip_ids: Iterable[str]) -> dict:
        used_p: set[str] = set()
        used_s: set[str] = set()
        chosen = []
        for tid in trip_ids:
            pts = seqs.get(tid)
            if not pts:
                continue
            chosen.append(tid)
            used_s.add(trips_meta[tid][1])
            for p, _m in pts:
                used_p.add(p)
        pre = prefix
        parents = sorted(used_p, key=lambda p: (pname.get(p, ""), p))
        p_ix = {p: i for i, p in enumerate(parents)}
        services = sorted(used_s)
        s_ix = {s: i for i, s in enumerate(services)}
        trip_rows = []
        for tid in chosen:
            rid, sid = trips_meta[tid]
            pts = seqs[tid]
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
        return {
            "op_id": op,
            "op_name": name,
            "feed": version,
            "s": [[pre + p, pname.get(p, p), stop_num(p)] for p in parents],
            "c": cal_rows,
            "a": add_rows,
            "r": rem_rows,
            "t": trip_rows,
        }

    if not split_agencies:
        oid = op_id or slug(op_name or Path(zpath).stem)
        oname = op_name or agencies.get(next(iter(agencies), ""), oid)
        return [pack(oid, oname, keep_trips)]

    by_agency: dict[str, list[str]] = defaultdict(list)
    for tid in keep_trips:
        rid = trips_meta[tid][0]
        by_agency[route_agency.get(rid, "")].append(tid)
    parts = []
    for aid, tids in by_agency.items():
        aname = agencies.get(aid, aid or op_name or "Helyi")
        oid = slug(aname)
        if oid in {"helykozi-busz", "mav-szemelyszallitasi-zrt"}:
            continue
        display = aname
        if " - " in display:
            display = display.split(" - ", 1)[1]
        if oid == "volanbusz":
            oid = "volan-helyi"
            display = "Volán helyi"
        packed = pack(oid, display, tids)
        packed["s"] = [[f"{oid}:{row[0]}", row[1], row[2]] for row in packed["s"]]
        if packed["t"]:
            parts.append(packed)
    return parts


def write_index(parts: list[dict], out: Path, *, with_operators: bool, schema: int = 27) -> dict:
    """Merge operator parts into one gzipped compact index."""
    ops: list[tuple[str, str]] = []
    seen_op: dict[str, int] = {}
    all_s: list[list] = []
    all_c: list = []
    all_a: list = []
    all_r: list = []
    all_t: list = []
    feeds: list[str] = []
    for part in parts:
        oid = part["op_id"]
        if oid not in seen_op:
            seen_op[oid] = len(ops)
            ops.append((oid, part["op_name"]))
        op_ix = seen_op[oid]
        s_off = len(all_s)
        c_off = len(all_c)
        for row in part["s"]:
            if with_operators:
                all_s.append([row[0], row[1], row[2], op_ix])
            else:
                all_s.append([row[0], row[1], row[2]])
        all_c.extend(part["c"])
        all_a.extend(part["a"])
        all_r.extend(part["r"])
        for t in part["t"]:
            all_t.append([t[0] + c_off, t[1], [p + s_off for p in t[2]], t[3]])
        if part.get("feed"):
            feeds.append(f"{oid}:{part['feed']}")
    idx = {
        "v": schema,
        "feed": ";".join(feeds),
        "s": all_s,
        "c": all_c,
        "a": all_a,
        "r": all_r,
        "t": all_t,
    }
    if with_operators:
        idx["o"] = [[oid, name] for oid, name in ops]
    raw = json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=9))
    tmp.replace(out)
    return {
        "feed_version": idx["feed"],
        "trips": len(all_t),
        "stops": len(all_s),
        "ops": len(ops),
        "bytes": out.stat().st_size,
    }
