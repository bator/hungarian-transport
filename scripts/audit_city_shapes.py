#!/usr/bin/env python3
"""Check every trip of a built GTFS index (city or Volán) the way the card
draws it: the trip's shapes.txt route from the sharded shapes folder must
reach each of its stops within TOLERANCE_M, in the trip's own order (walking
along the route, never back), so the line passes its stops as the bus does.

The card draws a city ride along its whole shape and puts its pins on the
trip's own stops, so a stop far from the shape is a pin beside the line, and
a missing shape falls back to network lookups or a straight line. The source
feeds decide both; this report shows, per operator, where they disagree.

    python3 scripts/audit_city_shapes.py city-index.json.gz [--json out.json]

Exit status is 0 unless --min-ok is given and an operator falls below it.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gtfs_compact import decode_polyline  # noqa: E402

TOLERANCE_M = 100.0
LONG_SEGMENT_M = 3000.0


def _gap(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.cos(math.radians(p[0]))
    ax, ay = (a[1] - p[1]) * k * 111320.0, (a[0] - p[0]) * 110540.0
    bx, by = (b[1] - p[1]) * k * 111320.0, (b[0] - p[0]) * 110540.0
    dx, dy = bx - ax, by - ay
    seg = dx * dx + dy * dy
    t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
    return math.hypot(ax + t * dx, ay + t * dy)


def distance_to_shape(p: tuple[float, float], shape: list[tuple[float, float]]) -> float:
    if len(shape) == 1:
        return _gap(p, shape[0], shape[0])
    return min(_gap(p, shape[i], shape[i + 1]) for i in range(len(shape) - 1))


def ordered_miss(points: list, shape: list[tuple[float, float]], tolerance_m: float):
    """Walk the shape forward: the first stop it does not reach within
    tolerance_m after the previous one, as (position, metres to the whole
    shape), or None when every stop is reached in order."""
    # A closed loop (Szeged 78 starts and ends past Mars tér, its first
    # stop) is walked round once more, so a trip may start anywhere on it.
    if len(shape) > 2 and _gap(shape[0], shape[-1], shape[-1]) <= tolerance_m:
        shape = shape + shape[1:]
    pos = 0
    last = len(shape) - 1
    for n, p in enumerate(points):
        if p is None:
            continue
        hit = next((k for k in range(pos, last) if _gap(p, shape[k], shape[k + 1]) <= tolerance_m), None)
        if hit is None:
            return n, distance_to_shape(p, shape)
        pos = hit
    return None


def longest_segment(shape: list[tuple[float, float]]) -> float:
    best = 0.0
    for a, b in zip(shape, shape[1:]):
        k = math.cos(math.radians(a[0]))
        best = max(best, math.hypot((b[1] - a[1]) * k * 111320.0, (b[0] - a[0]) * 110540.0))
    return best


class Shapes:
    """The sharded shapes folder an index names in its "sh" field."""

    def __init__(self, index_path: Path, sh: dict | None):
        self.dir = index_path.parent / sh["dir"] if sh and sh.get("dir") else None
        self.n = int(sh.get("n") or 0) if sh else 0
        self._shards: dict[int, list[str]] = {}

    def get(self, ref) -> list[tuple[float, float]]:
        if self.dir is None or self.n <= 0 or not isinstance(ref, int) or ref < 0:
            return []
        k = ref % self.n
        if k not in self._shards:
            path = self.dir / f"{k:02d}.json.gz"
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    self._shards[k] = json.load(fh).get("p") or []
            except (OSError, ValueError):
                self._shards[k] = []
        shard = self._shards[k]
        pos = ref // self.n
        return decode_polyline(shard[pos]) if pos < len(shard) and shard[pos] else []


def audit(index_path: Path, tolerance_m: float = TOLERANCE_M) -> dict:
    with gzip.open(index_path, "rt", encoding="utf-8") as fh:
        idx = json.load(fh)
    stops = idx.get("s") or []
    geo = idx.get("g") or []
    ops = [o[0] if isinstance(o, list) else str(o) for o in (idx.get("o") or [])]

    def coord(i: int):
        if 2 * i + 1 >= len(geo) or not (geo[2 * i] or geo[2 * i + 1]):
            return None
        return (geo[2 * i] / 1e5, geo[2 * i + 1] / 1e5)

    def op_of(i: int) -> str:
        row = stops[i] if 0 <= i < len(stops) else None
        n = int(row[3]) if row and len(row) > 3 else 0
        return ops[n] if 0 <= n < len(ops) else str(n)

    shapes = Shapes(index_path, idx.get("sh"))
    patterns: dict[tuple, dict] = {}
    for t in idx.get("t") or []:
        ps = t[2] if len(t) > 2 else []
        if not ps:
            continue
        key = (t[5] if len(t) > 5 else None, tuple(ps))
        rec = patterns.setdefault(key, {"route": t[1], "trips": 0, "ps": ps, "ref": key[0]})
        rec["trips"] += 1

    per_op: dict[str, dict] = defaultdict(lambda: {
        "patterns": 0, "trips": 0, "ok": 0, "no_shape": 0, "off_route": 0, "out_of_order": 0,
        "no_coords": 0, "long_segment": 0,
    })
    worst: list[dict] = []
    for (ref, _ps), rec in patterns.items():
        op = op_of(rec["ps"][0])
        r = per_op[op]
        r["patterns"] += 1
        r["trips"] += rec["trips"]
        pts = [coord(i) for i in rec["ps"]]
        if any(p is None for p in pts):
            r["no_coords"] += 1
        shape = shapes.get(ref)
        if len(shape) < 2:
            r["no_shape"] += 1
            worst.append({"op": op, "route": rec["route"], "trips": rec["trips"], "why": "no shape"})
            continue
        if longest_segment(shape) > LONG_SEGMENT_M:
            r["long_segment"] += 1
        far, far_stop = 0.0, ""
        for i, p in zip(rec["ps"], pts):
            if p is None:
                continue
            d = distance_to_shape(p, shape)
            if d > far:
                far, far_stop = d, stops[i][1] if i < len(stops) else ""
        if far > tolerance_m:
            r["off_route"] += 1
            r["trips_bad"] = r.get("trips_bad", 0) + rec["trips"]
            worst.append({"op": op, "route": rec["route"], "trips": rec["trips"], "why": "stop off route",
                          "stop": far_stop, "m": round(far)})
            continue
        miss = ordered_miss(pts, shape, tolerance_m)
        if miss is not None:
            r["out_of_order"] += 1
            r["trips_bad"] = r.get("trips_bad", 0) + rec["trips"]
            n = miss[0]
            worst.append({"op": op, "route": rec["route"], "trips": rec["trips"], "why": "stops out of order",
                          "stop": stops[rec["ps"][n]][1] if rec["ps"][n] < len(stops) else "", "m": round(miss[1])})
        else:
            r["ok"] += 1
    worst.sort(key=lambda w: (w["why"] != "no shape", w["why"] != "stop off route", -w.get("m", 0)))
    total = {k: sum(r.get(k, 0) for r in per_op.values()) for k in
             ("patterns", "trips", "trips_bad", "ok", "no_shape", "off_route", "out_of_order", "no_coords",
              "long_segment")}
    return {"tolerance_m": tolerance_m, "total": total, "operators": dict(per_op), "worst": worst[:40]}


def report_text(rep: dict) -> str:
    t = rep["total"]
    lines = [
        f"{t['ok']}/{t['patterns']} stop patterns ({t['trips'] - t['trips_bad']}/{t['trips']} trips) reach "
        f"every stop in order within {rep['tolerance_m']:.0f} m of their shape; {t['no_shape']} without a "
        f"shape, {t['off_route']} with a stop off it, {t['out_of_order']} out of order, "
        f"{t['no_coords']} with a stop lacking coordinates.",
    ]
    bad = sorted(((op, r) for op, r in rep["operators"].items() if r["ok"] < r["patterns"]),
                 key=lambda kv: kv[1]["ok"] / kv[1]["patterns"])
    for op, r in bad:
        lines.append(f"  {op:26} {r['ok']}/{r['patterns']} ok  no shape {r['no_shape']}  "
                     f"off route {r['off_route']}  out of order {r['out_of_order']}")
    for w in rep["worst"][:15]:
        extra = f" {w['stop']} {w['m']} m" if w["why"] != "no shape" else ""
        lines.append(f"  - {w['op']} {w['route']} ({w['trips']} trips): {w['why']}{extra}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("index", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--tolerance", type=float, default=TOLERANCE_M)
    ap.add_argument("--min-ok", type=float, default=None, help="fail when an operator's ok ratio is below this")
    args = ap.parse_args(argv)
    rep = audit(args.index, args.tolerance)
    print(report_text(rep))
    if args.json:
        args.json.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.min_ok is not None:
        low = [op for op, r in rep["operators"].items() if r["patterns"] and r["ok"] / r["patterns"] < args.min_ok]
        if low:
            print("below --min-ok: " + ", ".join(sorted(low)), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
