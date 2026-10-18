#!/usr/bin/env python3
"""Build the compact Volán GTFS index used by Hungarian transport (4-digit coach routes)."""
from __future__ import annotations

import re
from pathlib import Path

from gtfs_compact import IndexGuardError, download_feed, load_parts, write_index

GTFS_URL = "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"
OUT = Path("volan-index.json.gz")
# The KTI feed has about 46,000 coach trips (September 2026). Far fewer means
# a broken or truncated feed, not a timetable change.
MIN_TRIPS = 10_000


def include_long(short: str, _agency: str, _aid: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", short))


def build_index(zpath: Path, out: Path, *, min_trips: int = MIN_TRIPS) -> dict:
    parts = load_parts(
        zpath,
        include_long,
        op_id="volan",
        op_name="Volán",
        prefix="",
    )
    trips = sum(len(p["t"]) for p in parts)
    if trips < min_trips:
        raise IndexGuardError(f"refusing to write {out.name}: {trips} trips, expected at least {min_trips}")
    return write_index(parts, out, with_operators=False)


def main() -> None:
    zpath = Path("volanbusz_gtfs.zip")
    if not zpath.exists():
        print("downloading", GTFS_URL)
        download_feed(GTFS_URL, zpath)
    info = build_index(zpath, OUT)
    print("wrote", OUT, info)


if __name__ == "__main__":
    main()
