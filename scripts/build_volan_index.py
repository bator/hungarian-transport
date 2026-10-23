#!/usr/bin/env python3
"""Build the compact Volán GTFS index used by Hungarian transport (4-digit coach routes)."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from gtfs_compact import IndexGuardError, download_feed, load_env_files, load_parts, write_index

# MÁV's announced home of the Volán feed; the KTI address served the same
# zip before it and is the fallback. VOLAN_GTFS_URL (environment, .env or
# the repository variable in CI) replaces the first; the fallback stays.
GTFS_URL = "https://opendata.utas.hu/public-gtfs/volanbusz_gtfs.zip"
FALLBACK_URL = "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"
URL_ENV = "VOLAN_GTFS_URL"
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


def volan_urls(primary: str = GTFS_URL, fallback: str = FALLBACK_URL) -> list[str]:
    """The Volán feed URLs to try in order: VOLAN_GTFS_URL or primary, then
    fallback (once)."""
    load_env_files()
    override = (os.environ.get(URL_ENV) or "").strip()
    return list(dict.fromkeys(u for u in (override or primary, fallback) if u))


def download_volan(dest: Path, urls: list[str] | None = None) -> str:
    """Download the Volán zip from the first URL that works; that URL."""
    last: Exception | None = None
    for url in urls or volan_urls():
        if last is not None:
            print(f"warn Volán GTFS: {type(last).__name__}; trying {url}", file=sys.stderr)
        try:
            print("downloading", url)
            download_feed(url, dest)
            return url
        except Exception as err:
            last = err
    raise last or RuntimeError("no Volán GTFS URL")


def main() -> None:
    zpath = Path("volanbusz_gtfs.zip")
    if not zpath.exists():
        download_volan(zpath)
    info = build_index(zpath, OUT)
    print("wrote", OUT, info)


if __name__ == "__main__":
    main()
