#!/usr/bin/env python3
"""Compact a GTFS zip into the JSON the Lovelace card decompresses."""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import re
import tempfile
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

RoutePred = Callable[[str, str, str], bool]

UA = "Hungarian-transport/1.0 (Home Assistant; +https://github.com/bator/hungarian-transport)"
MAX_MEMBER = 500 * 1024 * 1024
INDEX_SCHEMA = 27


def parent(sid: str) -> str:
    parts = sid.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return sid


def hm(t: str | None) -> int | None:
    if not t:
        return None
    a = t.split(":")
    if len(a) < 2:
        return None
    try:
        return int(a[0]) * 60 + int(a[1])
    except ValueError:
        return None


def stop_num(pid: str) -> str:
    m = re.search(r"(\d+)$", pid)
    return m.group(1) if m else ""


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()
    folded = re.sub(r"^.*\s+-\s+", "", folded)
    folded = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return folded or "local"


def open_gtfs_zip(zpath: Path) -> zipfile.ZipFile:
    head = zpath.read_bytes()[:4]
    if head[:2] != b"PK":
        raise zipfile.BadZipFile(f"{zpath} is not a zip file")
    z = zipfile.ZipFile(zpath)
    try:
        for info in z.infolist():
            if info.file_size > MAX_MEMBER:
                raise zipfile.BadZipFile(
                    f"{info.filename} uncompressed size {info.file_size} exceeds limit"
                )
    except Exception:
        z.close()
        raise
    return z


def feed_version(z: zipfile.ZipFile) -> str:
    try:
        rows = list(_open_csv(z, "feed_info.txt"))
        if rows:
            return (rows[0].get("feed_version") or "").strip()
    except FileNotFoundError:
        pass
    return ""


def _open_csv(z: zipfile.ZipFile, name: str):
    names = {n.lower(): n for n in z.namelist()}
    real = names.get(name.lower())
    if not real:
        raise FileNotFoundError(name)
    raw = z.read(real)
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1250", "iso-8859-2", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    return csv.DictReader(io.StringIO(text))


def download_zip(url: str, dest: Path) -> dict:
    """Write url to dest via a sibling tempfile so a kill cannot leave a truncated zip."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="gtfs_", suffix=".zip", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        if tmp.stat().st_size < 4 or tmp.read_bytes()[:2] != b"PK":
            raise zipfile.BadZipFile(f"{url} did not return a zip")
        tmp.replace(dest)
        return {"size": dest.stat().st_size, "content_length": str(dest.stat().st_size)}
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def download_feed(url: str, dest: Path) -> dict:
    """Prefer HTTPS when the published URL is still http://."""
    candidates = [url]
    if url.startswith("http://"):
        candidates.insert(0, "https://" + url[len("http://") :])
    last: Exception | None = None
    for u in candidates:
        try:
            return download_zip(u, dest)
        except Exception as err:
            last = err
    raise last or RuntimeError(f"download failed: {url}")


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
    with open_gtfs_zip(zpath) as z:
        return _load_parts(
            z,
            include_route,
            prefix=prefix,
            split_agencies=split_agencies,
            op_id=op_id,
            op_name=op_name,
            skip_agency=skip_agency,
            zpath=zpath,
        )


def _load_parts(
    z: zipfile.ZipFile,
    include_route: RoutePred,
    *,
    prefix: str,
    split_agencies: bool,
    op_id: str,
    op_name: str,
    skip_agency: Callable[[str], bool] | None,
    zpath: Path,
) -> list[dict]:
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
        raw = row.get("stop_id") or ""
        if not raw:
            continue
        sid2p[raw] = parent(raw)
        sid2n[raw] = row.get("stop_name") or ""

    pname: dict[str, str] = {}
    for sid, p in sid2p.items():
        pname.setdefault(p, sid2n[sid])

    routes: dict[str, str] = {}
    route_agency: dict[str, str] = {}
    keep_routes: set[str] = set()
    for row in _open_csv(z, "routes.txt"):
        rid = row.get("route_id") or ""
        if not rid:
            continue
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
        tid = row.get("trip_id") or ""
        if not tid:
            continue
        trips_meta[tid] = (rid, row.get("service_id") or "")
        if rid in keep_routes:
            keep_trips.add(tid)

    cal: dict[str, list[str]] = {}
    try:
        for row in _open_csv(z, "calendar.txt"):
            sid = row.get("service_id") or ""
            if not sid:
                continue
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
            cal[sid] = [days, row.get("start_date") or "20200101", row.get("end_date") or "20991231"]
    except FileNotFoundError:
        pass

    ex_add: dict[str, list[str]] = defaultdict(list)
    ex_rem: dict[str, list[str]] = defaultdict(list)
    try:
        for row in _open_csv(z, "calendar_dates.txt"):
            sid = row.get("service_id") or ""
            date = row.get("date") or ""
            if not sid or not date:
                continue
            if row.get("exception_type") == "1":
                ex_add[sid].append(date)
            elif row.get("exception_type") == "2":
                ex_rem[sid].append(date)
    except FileNotFoundError:
        pass

    seqs: dict[str, list[tuple[str, int]]] = {}

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

    timed: list[tuple[str, int, str, int | None]] = []
    for row in _open_csv(z, "stop_times.txt"):
        tid = row.get("trip_id") or ""
        if tid not in keep_trips:
            continue
        try:
            seqn = int(row["stop_sequence"]) if row.get("stop_sequence") not in (None, "") else 10**9
        except (TypeError, ValueError):
            seqn = 10**9
        timed.append(
            (
                tid,
                seqn,
                row.get("stop_id") or "",
                hm(row.get("departure_time") or row.get("arrival_time")),
            )
        )
    timed.sort(key=lambda x: (x[0], x[1]))
    cur: str | None = None
    seq: list[tuple[str, int | None]] = []
    for tid, _seqn, sid, m in timed:
        if cur is None:
            cur = tid
        if tid != cur:
            flush(cur, seq)
            seq = []
            cur = tid
        seq.append((sid, m))
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
        elif not oid.startswith("volan-"):
            # Keep municipal ids (szeged, pecs, …) unique when Volán local
            # agencies reuse the city name.
            oid = "volan-" + oid
        packed = pack(oid, display, tids)
        packed["s"] = [[f"{oid}:{row[0]}", row[1], row[2]] for row in packed["s"]]
        if packed["t"]:
            parts.append(packed)
    return parts


def existing_trips(out: Path) -> int | None:
    if not out.exists():
        return None
    try:
        with gzip.open(out, "rt", encoding="utf-8") as fh:
            idx = json.load(fh)
        return len(idx.get("t") or [])
    except (OSError, json.JSONDecodeError, gzip.BadGzipFile):
        return None


def write_index(parts: list[dict], out: Path, *, with_operators: bool, schema: int = INDEX_SCHEMA) -> dict:
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
    prev = existing_trips(out)
    if prev is not None and prev >= 50 and len(all_t) < prev * 0.5:
        raise RuntimeError(
            f"refusing to replace {out.name}: {len(all_t)} trips vs previous {prev}"
        )
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
