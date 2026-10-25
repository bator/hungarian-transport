"""MÁV rail route index for the card (mav-index.json.gz, schema 2).

The card draws a train by its number (or a suburban line code such as S30).
One number can run different routes on different days (936 sometimes
continues to Szombathely), so the index keeps every stop pattern of a number
as its own variant, with its route and its stations. The card picks the
variant that serves both of its stations in order, and puts the pins on that
variant's own station coordinates.

Routes come from the feed's shapes.txt. MÁV's official shapes are coarse
chords; a pfaedle-matched feed follows the OSM rails. A variant whose shape
misses one of its own stations by more than STOP_TOLERANCE_M uses the station
sequence instead, so a train never draws a route that skips its stops.

``audit`` checks every trip of the feed against the index the way the card
uses it. Standard library only: it runs inside Home Assistant, and as its own
process (``python mav_index.py FEED OUT``) so a minute of CPU on stop_times
does not hold Home Assistant's GIL right after startup.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SCHEMA = 2
SIMPLIFY_M = 10.0
# A station's coordinate sits beside its tracks; big stations spread their
# platforms 100-200 m around the station point.
STOP_TOLERANCE_M = 300.0
RAIL_ROUTE_TYPES = {"2"} | {str(t) for t in range(100, 118)}
LINE_CODE_RE = re.compile(r"^[GSZHM]\d{2}$", re.I)
REQUIRED_FILES = ("routes.txt", "trips.txt", "stops.txt", "stop_times.txt")
# build_and_audit writes no index with fewer trains and lines than this (a
# bus-only GTFS passes check_feed), or with fewer than MIN_OK of the trains
# passing the audit: the same guard as scripts/build_mav_index.py.
MIN_TRAINS = 50
MIN_OK = 0.99


def train_number(name: str) -> str:
    """'IC 856 TÓPART' -> '856'. A suburban code like 'G43' has none."""
    s = (name or "").strip()
    if not s or LINE_CODE_RE.fullmatch(s):
        return ""
    m = re.search(r"(?<!\d)(\d{3,5})(?!\d)", s)
    if not m:
        return ""
    return m.group(1).lstrip("0") or m.group(1)


def line_code(short: str, long_name: str = "") -> str:
    """Suburban product code (G43, S30), not a train number."""
    for raw in (short, long_name):
        s = (raw or "").strip().upper()
        if LINE_CODE_RE.fullmatch(s):
            return s
    return ""


def fold(name: str) -> str:
    """Station name key, matching the card: case, accents, dashes, ' M'."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[–—-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+m$", "", s)
    for suffix in (" vasutallomas", " palyaudvar", " pu.", " pu", " va.", " va"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    # "Budapest-Kelenföld" is "Kelenföld vasútállomás" to BKK.
    if s.startswith("budapest ") and len(s) > len("budapest "):
        s = s[len("budapest "):]
    return s


# ---- GTFS access ---------------------------------------------------------


class Feed:
    """A GTFS feed in a zip or an unzipped directory (pfaedle's gtfs-out)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._zip = zipfile.ZipFile(self.path) if self.path.is_file() else None

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self) -> "Feed":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def names(self) -> set[str]:
        if self._zip is not None:
            return {Path(n).name.lower() for n in self._zip.namelist()}
        return {p.name.lower() for p in self.path.iterdir() if p.is_file()}

    def _member(self, name: str) -> str | None:
        if self._zip is None:
            p = self.path / name
            return str(p) if p.is_file() else None
        for n in self._zip.namelist():
            if Path(n).name.lower() == name:
                return n
        return None

    def rows(self, name: str) -> Iterator[dict[str, str]]:
        member = self._member(name)
        if member is None:
            return iter(())
        if self._zip is None:
            fh = open(member, encoding="utf-8-sig", newline="")
        else:
            fh = io.TextIOWrapper(self._zip.open(member), encoding="utf-8-sig", newline="")
        return _rows(fh)


def _rows(fh) -> Iterator[dict[str, str]]:
    with fh:
        yield from csv.DictReader(fh)


def check_feed(path: Path) -> None:
    """Raise ValueError unless path is a readable GTFS feed."""
    try:
        with Feed(path) as feed:
            missing = [n for n in REQUIRED_FILES if n not in feed.names()]
    except (zipfile.BadZipFile, OSError) as err:
        raise ValueError(f"not a GTFS zip: {err}") from err
    if missing:
        raise ValueError("GTFS feed lacks " + ", ".join(missing))


def feed_version(feed: Feed) -> str:
    for row in feed.rows("feed_info.txt"):
        return (row.get("feed_version") or "").strip()
    return ""


# ---- geometry -------------------------------------------------------------


def _encode_signed(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    out = []
    while value >= 0x20:
        out.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    out.append(chr(value + 63))
    return "".join(out)


def encode_polyline(pts: list[tuple[float, float]]) -> str:
    """Google encoded polyline, precision 5 (the card's decodePolyline)."""
    plat = plng = 0
    out = []
    for lat, lng in pts:
        ilat, ilng = int(round(lat * 1e5)), int(round(lng * 1e5))
        out.append(_encode_signed(ilat - plat))
        out.append(_encode_signed(ilng - plng))
        plat, plng = ilat, ilng
    return "".join(out)


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    index = lat = lng = 0
    while index < len(encoded):
        for coord in (0, 1):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if coord == 0:
                lat += delta
            else:
                lng += delta
        pts.append((lat / 1e5, lng / 1e5))
    return pts


def simplify(pts: list[tuple[float, float]], tol_m: float = SIMPLIFY_M) -> list[tuple[float, float]]:
    """Douglas-Peucker on a local equirectangular projection (metres)."""
    if len(pts) < 3:
        return list(pts)
    kx = 111320.0 * math.cos(math.radians(pts[0][0]))
    xy = [(lon * kx, lat * 110540.0) for lat, lon in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    tol2 = tol_m * tol_m
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        dx, dy = xy[b][0] - ax, xy[b][1] - ay
        seg2 = dx * dx + dy * dy
        worst, wi = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i][0] - ax, xy[i][1] - ay
            if seg2:
                t = max(0.0, min(1.0, (px * dx + py * dy) / seg2))
                px -= t * dx
                py -= t * dy
            d2 = px * px + py * py
            if d2 > worst:
                worst, wi = d2, i
        if worst > tol2:
            keep[wi] = True
            stack.append((a, wi))
            stack.append((wi, b))
    return [p for p, k in zip(pts, keep) if k]


def _seg_gap(p, a, b, k: float) -> float:
    ax, ay = (a[1] - p[1]) * k * 111320.0, (a[0] - p[0]) * 110540.0
    bx, by = (b[1] - p[1]) * k * 111320.0, (b[0] - p[0]) * 110540.0
    dx, dy = bx - ax, by - ay
    seg = dx * dx + dy * dy
    t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
    return math.hypot(ax + t * dx, ay + t * dy)


class RouteGrid:
    """A polyline's segments bucketed on a ~2 km grid, so the distance from a
    station only looks at the few segments around it."""

    CELL = 0.02

    def __init__(self, pts: list[tuple[float, float]]):
        self.pts = pts
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        c = self._cell
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            for cx in range(c(min(a[0], b[0])), c(max(a[0], b[0])) + 1):
                for cy in range(c(min(a[1], b[1])), c(max(a[1], b[1])) + 1):
                    self.grid[(cx, cy)].append(i)

    def _cell(self, v: float) -> int:
        return math.floor(v / self.CELL)

    def gap(self, p: tuple[float, float]) -> float:
        k = math.cos(math.radians(p[0]))
        if len(self.pts) == 1:
            q = self.pts[0]
            return math.hypot((q[1] - p[1]) * k * 111320.0, (q[0] - p[0]) * 110540.0)
        cx, cy = self._cell(p[0]), self._cell(p[1])
        best = math.inf
        seen: set[int] = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.grid.get((cx + dx, cy + dy), ()):
                    if i not in seen:
                        seen.add(i)
                        best = min(best, _seg_gap(p, self.pts[i], self.pts[i + 1], k))
        if best == math.inf and self.pts:
            best = min(math.hypot((q[1] - p[1]) * k * 111320.0, (q[0] - p[0]) * 110540.0) for q in self.pts)
        return best


# ---- build ----------------------------------------------------------------


def _load(feed: Feed):
    routes: dict[str, tuple[str, str]] = {}
    for row in feed.rows("routes.txt"):
        if (row.get("route_type") or "").strip() not in RAIL_ROUTE_TYPES:
            continue
        short = (row.get("route_short_name") or "").strip()
        long_name = (row.get("route_long_name") or "").strip()
        num = train_number(long_name) or train_number(short)
        code = line_code(short, long_name)
        if num:
            routes[row.get("route_id") or ""] = ("n", num)
        elif code:
            routes[row.get("route_id") or ""] = ("l", code)
    trips: dict[str, tuple[tuple[str, str], str]] = {}
    for row in feed.rows("trips.txt"):
        key = routes.get(row.get("route_id") or "")
        if key and row.get("trip_id"):
            trips[row["trip_id"]] = (key, (row.get("shape_id") or "").strip())
    stops: dict[str, tuple[str, float, float]] = {}
    for row in feed.rows("stops.txt"):
        try:
            lat, lon = float(row.get("stop_lat") or ""), float(row.get("stop_lon") or "")
        except ValueError:
            continue
        if abs(lat) < 1 and abs(lon) < 1:
            continue
        stops[row.get("stop_id") or ""] = ((row.get("stop_name") or "").strip(), lat, lon)
    seqs: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in feed.rows("stop_times.txt"):
        tid = row.get("trip_id") or ""
        if tid in trips:
            try:
                n = int(float(row.get("stop_sequence") or ""))
            except ValueError:
                # No usable sequence: file order, never the stop id's alphabet.
                n = seqs[tid][-1][0] + 1 if seqs[tid] else 0
            seqs[tid].append((n, row.get("stop_id") or ""))
    trip_stops: dict[str, tuple[str, ...]] = {}
    for tid, rows in seqs.items():
        ids: list[str] = []
        for _n, sid in sorted(rows):
            if sid in stops and (not ids or ids[-1] != sid):
                ids.append(sid)
        if len(ids) >= 2:
            trip_stops[tid] = tuple(ids)
    return trips, stops, trip_stops


def _shapes(feed: Feed, wanted: set[str]) -> dict[str, list[tuple[float, float]]]:
    raw: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    if not wanted:
        return {}
    for row in feed.rows("shapes.txt"):
        sid = row.get("shape_id") or ""
        if sid not in wanted:
            continue
        try:
            raw[sid].append((int(float(row.get("shape_pt_sequence") or 0)), float(row["shape_pt_lat"]), float(row["shape_pt_lon"])))
        except (KeyError, ValueError):
            continue
    return {sid: [(lat, lon) for _n, lat, lon in sorted(rows)] for sid, rows in raw.items()}


def build(path: Path) -> dict[str, Any]:
    """Index dict (schema 2) plus build statistics under "_stats".

    "_loaded" carries the parsed feed (trips, stops, trip_stops) for ``audit``,
    so the audit right after a build does not read stop_times.txt again.
    ``write`` drops every "_" key.
    """
    with Feed(path) as feed:
        version = feed_version(feed)
        trips, stops, trip_stops = _load(feed)
        # key -> stop pattern -> shape ids seen with it
        patterns: dict[tuple[str, str], dict[tuple[str, ...], list[str]]] = defaultdict(lambda: defaultdict(list))
        for tid, (key, sid) in trips.items():
            ids = trip_stops.get(tid)
            if ids:
                patterns[key][ids].append(sid)
        wanted = {s for pats in patterns.values() for sids in pats.values() for s in sids if s}
        shapes = _shapes(feed, wanted)
    stop_ix: dict[str, int] = {}
    stop_table: list[list[Any]] = []
    polylines: list[str] = []
    poly_ix: dict[str, int] = {}
    numbers: dict[str, list[list[Any]]] = {}
    lines: dict[str, list[list[Any]]] = {}
    fitted = stop_lines = 0

    def sidx(sid: str) -> int:
        if sid not in stop_ix:
            name, lat, lon = stops[sid]
            stop_ix[sid] = len(stop_table)
            stop_table.append([name, round(lat * 1e5), round(lon * 1e5)])
        return stop_ix[sid]

    def pix(pts: list[tuple[float, float]]) -> int:
        enc = encode_polyline(pts)
        if enc not in poly_ix:
            poly_ix[enc] = len(polylines)
            polylines.append(enc)
        return poly_ix[enc]

    for (kind, key) in sorted(patterns):
        variants = []
        # Longest patterns first: the card takes the first variant that
        # serves both of its stations.
        for ids, sids in sorted(patterns[(kind, key)].items(), key=lambda kv: (-len(kv[0]), kv[0])):
            pts = [(stops[s][1], stops[s][2]) for s in ids]
            geom: list[tuple[float, float]] | None = None
            for shape_id in dict.fromkeys(s for s in sids if s):
                cand = shapes.get(shape_id) or []
                if len(cand) >= 2:
                    grid = RouteGrid(cand)
                    if all(grid.gap(p) <= STOP_TOLERANCE_M for p in pts):
                        geom = simplify(cand)
                        break
            if geom is None:
                geom = pts
                stop_lines += 1
            else:
                fitted += 1
            variants.append([pix(geom), [sidx(s) for s in ids]])
        (numbers if kind == "n" else lines)[key] = variants
    return {
        "v": SCHEMA,
        "feed": version,
        "st": stop_table,
        "p": polylines,
        "n": numbers,
        "l": lines,
        "_loaded": (trips, stops, trip_stops),
        "_stats": {
            "feed_version": version,
            "trains": len(numbers),
            "lines": len(lines),
            "variants": fitted + stop_lines,
            "fitted": fitted,
            "stop_lines": stop_lines,
            "shapes": len(polylines),
        },
    }


def write(index: dict[str, Any], out: Path) -> int:
    """Atomically write index (without build stats) as gzip JSON; bytes."""
    data = {k: v for k, v in index.items() if not k.startswith("_")}
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=out.name + ".", dir=str(out.parent))
    try:
        # mkstemp makes the file 0600; the index is served, and travels into
        # the release zip, so it gets the mode a plain file would have.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(gzip.compress(raw, compresslevel=9))
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out.stat().st_size


def read(path: Path) -> dict[str, Any]:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


# ---- the card's choice, and the audit ---------------------------------------


def pick_variant(index: dict[str, Any], key: tuple[str, str], board: str, alight: str):
    """The variant the card draws for a train between two stations: the
    first (longest) one that serves board before alight, else one serving
    board, else the first. Returns (variant, board_pos, alight_pos)."""
    kind, value = key
    variants = (index.get("n" if kind == "n" else "l") or {}).get(value) or []
    if not variants:
        return None, -1, -1
    stations = index.get("st") or []
    fb, fa = fold(board), fold(alight)
    first_board = None
    for var in variants:
        names = [fold(stations[i][0]) for i in var[1]]
        bpos = names.index(fb) if fb in names else -1
        if bpos >= 0:
            apos = names.index(fa, bpos + 1) if fa in names[bpos + 1:] else -1
            if apos > bpos:
                return var, bpos, apos
            if first_board is None:
                first_board = (var, bpos, -1)
    return first_board or (variants[0], -1, -1)


def audit(path: Path, index: dict[str, Any], loaded: tuple | None = None) -> dict[str, Any]:
    """Every trip, as the card would draw it between its first and last
    station: is every one of its stations on the drawn route?

    ``loaded`` is the (trips, stops, trip_stops) triple of ``_load``; an index
    fresh from ``build`` carries it as "_loaded". The feed is only parsed
    again for an index read back from disk."""
    if loaded is None:
        loaded = index.get("_loaded")
    if loaded is None:
        with Feed(path) as feed:
            loaded = _load(feed)
    trips, stops, trip_stops = loaded
    polys: dict[int, RouteGrid] = {}
    gaps: dict[tuple[int, str], float] = {}
    keys: dict[tuple[str, str], dict[str, Any]] = {}
    checked = bad_trips = 0
    for tid, (key, _sid) in trips.items():
        ids = trip_stops.get(tid)
        if not ids:
            continue
        checked += 1
        rec = keys.setdefault(key, {"trips": 0, "bad": 0, "worst": None})
        rec["trips"] += 1
        var, bpos, apos = pick_variant(index, key, stops[ids[0]][0], stops[ids[-1]][0])
        problem = None
        if var is None:
            problem = {"trip": tid, "why": "no route"}
        elif bpos < 0 or apos < 0:
            problem = {"trip": tid, "why": "stations not in any variant"}
        else:
            ix = var[0]
            if ix not in polys:
                polys[ix] = RouteGrid(decode_polyline(index["p"][ix]))
            worst_m, worst_stop = 0.0, ""
            for sid in ids:
                ck = (ix, sid)
                if ck not in gaps:
                    gaps[ck] = polys[ix].gap((stops[sid][1], stops[sid][2]))
                if gaps[ck] > worst_m:
                    worst_m, worst_stop = gaps[ck], stops[sid][0]
            if worst_m > STOP_TOLERANCE_M:
                problem = {"trip": tid, "why": "station off route", "stop": worst_stop, "m": round(worst_m)}
        if problem:
            bad_trips += 1
            rec["bad"] += 1
            rec["worst"] = rec["worst"] or problem
    trains = len(keys)
    ok = sum(1 for r in keys.values() if not r["bad"])
    worst = [
        {"train": ("" if k[0] == "n" else "line ") + k[1], "bad_trips": r["bad"], "trips": r["trips"], **r["worst"]}
        for k, r in sorted(keys.items(), key=lambda kv: -kv[1]["bad"]) if r["bad"]
    ]
    return {
        "trips": checked,
        "trips_bad": bad_trips,
        "trains": trains,
        "trains_ok": ok,
        "ok_ratio": round(ok / trains, 4) if trains else 0.0,
        "tolerance_m": STOP_TOLERANCE_M,
        "worst": worst[:30],
    }


# ---- build + audit as one job (Home Assistant runs it in a subprocess) -------


def build_and_audit(
    src: Path, out: Path, *, min_trains: int = MIN_TRAINS, min_ok: float = MIN_OK
) -> dict[str, Any]:
    """Check, build and audit, then write; the stats dict Home Assistant keeps.

    The audit runs on the index in memory and the write comes last, so a
    build that fails on the way leaves the previous index alone. So does an
    index with fewer than min_trains trains and lines, or with fewer than
    min_ok of its trains passing the audit: stats then say why under
    "rejected", and "written" is False."""
    check_feed(src)
    index = build(src)
    stats = dict(index["_stats"])
    stats["written"] = False
    count = stats["trains"] + stats["lines"]
    if count < min_trains:
        stats["rejected"] = (
            f"only {stats['trains']} trains and {stats['lines']} lines (at least {min_trains} expected)"
        )
        return stats
    report = audit(src, index)
    stats["audit"] = {k: v for k, v in report.items() if k != "worst"}
    stats["audit_worst"] = report["worst"][:5]
    if report["ok_ratio"] < min_ok:
        stats["rejected"] = (
            f"{report['trains_ok']} of {report['trains']} trains pass the audit "
            f"({report['ok_ratio']:.1%}, at least {min_ok:.0%} expected)"
        )
        return stats
    stats["bytes"] = write(index, out)
    stats["written"] = True
    return stats


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="mav_index.py",
        description=(
            "Build, audit and (when it passes) write the MÁV route index. Stats JSON on "
            "stdout; \"rejected\" in it says why nothing was written."
        ),
    )
    parser.add_argument("feed", help="MÁV GTFS zip or directory")
    parser.add_argument("out", help="index to write (mav-index.json.gz)")
    parser.add_argument("--min-trains", type=int, default=MIN_TRAINS)
    parser.add_argument("--min-ok", type=float, default=MIN_OK)
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as err:
        return 2 if err.code else 0
    try:
        # Below Home Assistant's own priority: the build is not urgent.
        os.nice(10)
    except (OSError, AttributeError):
        pass
    try:
        stats = build_and_audit(
            Path(args.feed), Path(args.out), min_trains=args.min_trains, min_ok=args.min_ok
        )
    except Exception as err:  # noqa: BLE001 - the parent reads stderr
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        return 1
    # ASCII only: the pipe's encoding follows the locale Home Assistant runs in.
    json.dump(stats, sys.stdout, ensure_ascii=True)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
