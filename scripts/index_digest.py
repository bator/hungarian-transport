#!/usr/bin/env python3
"""Content digest of a compact GTFS index, independent of row order.

The weekly release compares the rebuilt indexes with the latest release's.
Two builds of the same feed may list stops, trips, operators and shapes in
another order (builders before the sorted trip order did, depending on
PYTHONHASHSEED), and the shapes folder is named after that order. The digest
resolves every reference (a trip's stops, service and shape, a stop's
operator) and sorts what is left, so it changes only with the timetable.

Usage: index_digest.py INDEX.json.gz...  prints "<digest>  <path>" per index.
The shapes folder an index names ("sh") is read from next to the index; an
index whose shapes cannot be read fails (exit 1), so the caller treats it as
changed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path


def _shapes(index: Path, sh: dict | None) -> dict[int, str]:
    if not sh:
        return {}
    folder = index.parent / str(sh["dir"])
    n = int(sh["n"])
    out: dict[int, str] = {}
    for k in range(n):
        with gzip.open(folder / f"{k:02d}.json.gz", "rt", encoding="utf-8") as fh:
            for pos, enc in enumerate(json.load(fh)["p"]):
                out[pos * n + k] = enc
    return out


def canonical(index: Path) -> dict:
    with gzip.open(index, "rt", encoding="utf-8") as fh:
        idx = json.load(fh)
    shapes = _shapes(index, idx.get("sh"))
    ops = [list(o) for o in idx.get("o") or []]
    coords = idx.get("g") or []
    stops = []
    for i, row in enumerate(idx.get("s") or []):
        stop = list(row[:3])
        if len(row) > 3:
            stop.append(ops[row[3]][0])
        stop.extend(coords[2 * i: 2 * i + 2])
        stops.append(stop)
    cal, added, removed = idx.get("c") or [], idx.get("a") or [], idx.get("r") or []
    trips = []
    for t in idx.get("t") or []:
        sv = t[0]
        trip = [
            cal[sv],
            sorted(added[sv]) if sv < len(added) else [],
            sorted(removed[sv]) if sv < len(removed) else [],
            t[1],
            [stops[p][0] for p in t[2]],
            t[3],
            *t[4:5],
        ]
        if len(t) > 5:
            trip.append(shapes[t[5]] if t[5] is not None and t[5] >= 0 else None)
        trips.append(json.dumps(trip, ensure_ascii=False, separators=(",", ":")))
    return {
        "v": idx.get("v"),
        "feed": sorted(filter(None, str(idx.get("feed") or "").split(";"))),
        "o": sorted(ops),
        "s": sorted(json.dumps(s, ensure_ascii=False, separators=(",", ":")) for s in stops),
        "t": sorted(trips),
    }


def digest(index: Path) -> str:
    body = json.dumps(canonical(index), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: index_digest.py INDEX.json.gz...", file=sys.stderr)
        return 2
    status = 0
    for arg in argv:
        try:
            print(f"{digest(Path(arg))}  {arg}")
        except (OSError, ValueError, KeyError, IndexError, TypeError, EOFError) as err:
            print(f"error {arg}: {type(err).__name__}: {err}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
