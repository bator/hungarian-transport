#!/usr/bin/env python3
"""Build mav-index.json.gz from MÁV GTFS, then audit it.

The builder is the integration's own (custom_components/hungarian_transport/
mav_index.py): every stop pattern of every train with its route and its
stations. Official shapes.txt is a coarse chord (8 km jumps on line 30); a
pfaedle-matched feed follows the OSM rails, so prefer that when present.
A pattern whose shape misses one of its stations falls back to the station
sequence. The audit then checks every trip of the feed station by station.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from gtfs_compact import download_feed, load_env_files

OUT = Path("mav-index.json.gz")
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


def _mav_module():
    """The integration's builder, so Home Assistant and this script make the
    same index (schema 2: every stop pattern of a train with its stations)."""
    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "hungarian_transport"
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    import mav_index  # noqa: PLC0415

    return mav_index


def build_mav_index(zpath: Path, out: Path, *, audit: bool = True) -> dict:
    mav = _mav_module()
    index = mav.build(zpath)
    stats = dict(index["_stats"])
    if stats["trains"] + stats["lines"] < 50:
        raise RuntimeError(f"MÁV index too small: {stats['trains']} trains, {stats['lines']} lines")
    stats["bytes"] = mav.write(index, out)
    if audit:
        report = mav.audit(zpath, index)
        stats["audit"] = {k: v for k, v in report.items() if k != "worst"}
        stats["audit_worst"] = report["worst"][:5]
    return stats


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
