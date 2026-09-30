#!/usr/bin/env python3
"""Build mav-index.json.gz: train number → full nationwide polyline from MÁV GTFS.

Official shapes.txt is often a 2-point stub, or a sparse chord that cuts
across the Székesfehérvár–Kelenföld fields (8 km jumps on line 30).
pfaedle output under /opt/pfaedle-trial/out matches those trips to OSM
rails (~50–200 m of the stations). Prefer that unzipped feed when present.

When the chosen shape still does not cover the stop bbox, the index uses
the stop coordinates in sequence so every rail trip draws end to end.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from gtfs_compact import (
    _open_csv,
    download_feed,
    encode_polyline,
    feed_version,
    line_code,
    load_env_files,
    open_gtfs,
    train_number,
)

OUT = Path("mav-index.json.gz")
MAV_INDEX_SCHEMA = 1
LOCAL_ZIPS = (
    Path("mav-gtfs.zip"),
    Path("/opt/pfaedle-trial/gtfs/mav-gtfs.zip"),
    Path("/config/hungarian-transport/mav-gtfs.zip"),
)
PFAEDLE_DIRS = (
    Path("/opt/pfaedle-trial/out"),
    Path("pfaedle-out"),
    Path("/config/hungarian-transport/pfaedle-out"),
)


def _feed_url() -> str:
    load_env_files()
    return (os.environ.get("MAV_GTFS_URL") or "").strip()


def _is_gtfs_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    names = {p.name.lower() for p in path.iterdir() if p.is_file()}
    return "routes.txt" in names and "trips.txt" in names and "shapes.txt" in names


def resolve_mav_feed(base: Path) -> Path:
    load_env_files()
    env_dir = (os.environ.get("MAV_PFAEDLE_DIR") or "").strip()
    if env_dir:
        p = Path(env_dir)
        if _is_gtfs_dir(p):
            return p
    env_path = (os.environ.get("MAV_GTFS_ZIP") or "").strip()
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    for cand in PFAEDLE_DIRS:
        p = cand if cand.is_absolute() else base / cand
        if _is_gtfs_dir(p):
            return p
    for cand in LOCAL_ZIPS:
        p = cand if cand.is_absolute() else base / cand
        if p.is_file():
            return p
    url = _feed_url()
    dest = base / "mav-gtfs.zip"
    if url:
        print("downloading MÁV GTFS")
        download_feed(url, dest)
        return dest
    raise FileNotFoundError(
        "MÁV GTFS not found. Set MAV_PFAEDLE_DIR / MAV_GTFS_ZIP / MAV_GTFS_URL "
        "or place mav-gtfs.zip here."
    )


def _bbox(pts: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if len(pts) < 2:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return (min(lats), max(lats), min(lons), max(lons))


def _seg_len(pts: list[tuple[float, float]]) -> float:
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        total += ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    return total


def _covers(shape: list[tuple[float, float]], stops: list[tuple[float, float]], ratio: float = 0.9) -> bool:
    """True when official geometry follows the stops, not just their bbox.

    A 2-point Székesfehérvár–Kelenföld chord spans the same bbox as the
    intermediate stations, so bbox-only comparison kept the 8 km stub.
    """
    sb = _bbox(stops)
    gb = _bbox(shape)
    if not sb or not gb:
        return False
    slat = sb[1] - sb[0]
    slon = sb[3] - sb[2]
    glat = gb[1] - gb[0]
    glon = gb[3] - gb[2]
    if slat < 0.01 and slon < 0.01:
        return len(shape) >= 2
    if not (glat >= slat * ratio and glon >= slon * ratio):
        return False
    if len(stops) > 2 and len(shape) <= 2:
        return False
    slen = _seg_len(stops)
    glen = _seg_len(shape)
    if slen > 0.01 and glen < slen * ratio:
        return False
    return True


def _score(stops: list[tuple[float, float]]) -> tuple[int, float]:
    box = _bbox(stops)
    if not box:
        return (len(stops), 0.0)
    span = (box[1] - box[0]) ** 2 + (box[3] - box[2]) ** 2
    return (len(stops), span)


def load_rail_shapes(zpath: Path) -> dict:
    with open_gtfs(zpath) as z:
        version = feed_version(z)
        # rid → (lookup_key, kind) where kind is "n" (train number) or "l" (line).
        routes: dict[str, tuple[str, str]] = {}
        for row in _open_csv(z, "routes.txt"):
            rid = row.get("route_id") or ""
            if not rid:
                continue
            if (row.get("route_type") or "").strip() != "2":
                continue
            short = (row.get("route_short_name") or "").strip()
            long_name = (row.get("route_long_name") or "").strip()
            num = train_number(long_name) or train_number(short)
            code = line_code(short, long_name)
            if num:
                routes[rid] = (num, "n")
            elif code:
                routes[rid] = (code, "l")
            else:
                continue

        trips: dict[str, tuple[str, str, str]] = {}
        for row in _open_csv(z, "trips.txt"):
            rid = row.get("route_id") or ""
            tid = row.get("trip_id") or ""
            if not tid or rid not in routes:
                continue
            key, kind = routes[rid]
            trips[tid] = (key, kind, (row.get("shape_id") or "").strip())

        stops: dict[str, tuple[float, float]] = {}
        for row in _open_csv(z, "stops.txt"):
            sid = row.get("stop_id") or ""
            if not sid:
                continue
            try:
                lat = float(row.get("stop_lat") or "")
                lon = float(row.get("stop_lon") or "")
            except ValueError:
                continue
            if abs(lat) < 1 and abs(lon) < 1:
                continue
            stops[sid] = (lat, lon)

        seqs: dict[str, list[tuple[float, float]]] = {}
        timed: list[tuple[str, int, str]] = []
        keep = set(trips)
        for row in _open_csv(z, "stop_times.txt"):
            tid = row.get("trip_id") or ""
            if tid not in keep:
                continue
            try:
                seqn = int(row["stop_sequence"]) if row.get("stop_sequence") not in (None, "") else 10**9
            except (TypeError, ValueError):
                seqn = 10**9
            timed.append((tid, seqn, row.get("stop_id") or ""))
        timed.sort(key=lambda x: (x[0], x[1]))
        cur: str | None = None
        pts: list[tuple[float, float]] = []
        for tid, _seqn, sid in timed:
            if cur is None:
                cur = tid
            if tid != cur:
                if len(pts) >= 2:
                    seqs[cur] = pts
                pts = []
                cur = tid
            coord = stops.get(sid)
            if coord and (not pts or pts[-1] != coord):
                pts.append(coord)
        if cur and len(pts) >= 2:
            seqs[cur] = pts

        best: dict[tuple[str, str], tuple[tuple[int, float], list[tuple[float, float]], str]] = {}
        used_shapes: set[str] = set()
        for tid, (key, kind, shape_id) in trips.items():
            stop_pts = seqs.get(tid)
            if not stop_pts:
                continue
            score = _score(stop_pts)
            slot = (kind, key)
            prev = best.get(slot)
            if prev is None or score > prev[0]:
                best[slot] = (score, stop_pts, shape_id)
                if shape_id:
                    used_shapes.add(shape_id)

        shape_pts: dict[str, list[tuple[float, float]]] = defaultdict(list)
        if used_shapes:
            for row in _open_csv(z, "shapes.txt"):
                sid = row.get("shape_id") or ""
                if sid not in used_shapes:
                    continue
                try:
                    lat = float(row.get("shape_pt_lat") or "")
                    lon = float(row.get("shape_pt_lon") or "")
                    seqn = int(row.get("shape_pt_sequence") or 0)
                except ValueError:
                    continue
                shape_pts[sid].append((seqn, lat, lon))

    official: dict[str, list[tuple[float, float]]] = {}
    for sid, rows in shape_pts.items():
        rows.sort(key=lambda x: x[0])
        pts = [(lat, lon) for _seq, lat, lon in rows]
        if len(pts) >= 2:
            official[sid] = pts

    numbers: dict[str, int] = {}
    lines: dict[str, int] = {}
    polylines: list[str] = []
    seen_poly: dict[str, int] = {}
    stub_replaced = 0

    def add_poly(geom: list[tuple[float, float]]) -> int | None:
        encoded = encode_polyline(geom)
        if not encoded:
            return None
        ix = seen_poly.get(encoded)
        if ix is None:
            ix = len(polylines)
            polylines.append(encoded)
            seen_poly[encoded] = ix
        return ix

    for kind, key in sorted(best, key=lambda k: (k[0], len(k[1]), k[1])):
        _score_v, stop_pts, shape_id = best[(kind, key)]
        geom = official.get(shape_id) or []
        if not _covers(geom, stop_pts):
            geom = stop_pts
            stub_replaced += 1
        ix = add_poly(geom)
        if ix is None:
            continue
        if kind == "l":
            lines[key] = ix
        else:
            numbers[key] = ix
    return {
        "v": MAV_INDEX_SCHEMA,
        "feed": version,
        "n": numbers,
        "l": lines,
        "p": polylines,
        "trains": len(numbers),
        "lines": len(lines),
        "shapes": len(polylines),
        "stub_replaced": stub_replaced,
    }


def write_mav_index(payload: dict, out: Path) -> dict:
    idx = {
        "v": payload["v"],
        "feed": payload.get("feed") or "",
        "n": payload["n"],
        "l": payload.get("l") or {},
        "p": payload["p"],
    }
    raw = json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=9))
    tmp.replace(out)
    return {
        "feed_version": idx["feed"],
        "trains": payload["trains"],
        "lines": payload.get("lines") or 0,
        "shapes": payload["shapes"],
        "stub_replaced": payload["stub_replaced"],
        "bytes": out.stat().st_size,
    }


def build_mav_index(zpath: Path, out: Path) -> dict:
    payload = load_rail_shapes(zpath)
    if payload["trains"] + payload.get("lines", 0) < 50:
        raise RuntimeError(
            f"MÁV index too small: {payload['trains']} trains, {payload.get('lines', 0)} lines"
        )
    return write_mav_index(payload, out)


def main() -> None:
    base = Path(".")
    zpath = resolve_mav_feed(base)
    print("using", zpath)
    info = build_mav_index(zpath, OUT)
    print("wrote", OUT, info)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as err:
        print(f"skip mav index: {err}", file=sys.stderr)
        sys.exit(0)
