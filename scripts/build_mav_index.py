#!/usr/bin/env python3
"""Build mav-index.json.gz from MÁV GTFS, audit it, and write it if it passes.

The builder is the integration's own (custom_components/hungarian_transport/
mav_index.py): every stop pattern of every train with its route and its
stations. Official shapes.txt is a coarse chord (8 km jumps on line 30); a
pfaedle-matched feed follows the OSM rails, so prefer that when present.
A pattern whose shape misses one of its stations falls back to the station
sequence. The audit then checks every trip of the feed station by station;
below MIN_OK of the trains passing, the previous index is kept.
"""
from __future__ import annotations

import filecmp
import os
import sys
import tempfile
import time
from pathlib import Path

from gtfs_compact import download_feed, load_env_files

OUT = Path("mav-index.json.gz")
# Share of trains that must pass the audit before the index is written; the
# integration warns below the same ratio.
MIN_OK = 0.99
# A zip downloaded from MAV_GTFS_URL is fetched again once it is this old.
URL_MAX_AGE_S = 20 * 3600
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


def _feed_auth() -> tuple[str, str] | None:
    """MAV_GTFS_USER / MAV_GTFS_PASSWORD (environment or .env): HTTP Basic
    credentials for MAV_GTFS_URL, MÁV's own registered-user download. Sent
    over https only, to the URL's host only (never on a redirect); never in
    a URL, a log line, an error or state.json. Only one of the two set is
    an error, not an anonymous download."""
    load_env_files()
    user = (os.environ.get("MAV_GTFS_USER") or "").strip()
    password = os.environ.get("MAV_GTFS_PASSWORD") or ""
    if not user and not password:
        return None
    if not user or not password:
        raise ValueError("set both MAV_GTFS_USER and MAV_GTFS_PASSWORD, or neither")
    return user, password


def _is_gtfs_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    names = {p.name.lower() for p in path.iterdir() if p.is_file()}
    return "routes.txt" in names and "trips.txt" in names and "shapes.txt" in names


def refresh_from_url(
    url: str, dest: Path, *, max_age_s: float = URL_MAX_AGE_S, auth: tuple[str, str] | None = None
) -> BaseException | None:
    """Fetch MAV_GTFS_URL into dest unless dest is recent. An unchanged zip
    keeps its file (and mtime), so the index is not rebuilt for nothing; a
    failed download keeps the old zip and returns its error (the old mtime
    stays, so the next run tries again). Without an old zip it raises.
    auth: HTTP Basic credentials (see _feed_auth)."""
    try:
        if time.time() - dest.stat().st_mtime < max_age_s:
            return None
    except OSError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="gtfs_", suffix=".zip", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        print("downloading MÁV GTFS" + (" (HTTP Basic auth)" if auth else ""))
        if auth is not None:
            download_feed(url, tmp, auth=auth)
        else:
            download_feed(url, tmp)
        if dest.is_file() and filecmp.cmp(tmp, dest, shallow=False):
            print("MÁV GTFS unchanged")
            return None
        tmp.replace(dest)
        return None
    except Exception as err:
        if not dest.is_file():
            raise
        print(f"warn MÁV GTFS download: {type(err).__name__}; keeping {dest.name}", file=sys.stderr)
        return err
    finally:
        tmp.unlink(missing_ok=True)


def resolve_mav_feed(base: Path, *, url_errors: list[BaseException] | None = None) -> Path:
    """The MÁV feed to build from. A MAV_GTFS_URL download that failed while
    an older copy was kept is appended to url_errors (update_volan_index
    reports it once it keeps failing)."""
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
    # With MAV_GTFS_URL set, base/mav-gtfs.zip is that URL's copy: refresh it
    # instead of letting the first download shadow the URL for good.
    url = _feed_url()
    dest = base / "mav-gtfs.zip"
    if url:
        try:
            auth = _feed_auth()
        except ValueError as err:
            # Like a failed download: an older copy stays in use.
            if not dest.is_file():
                raise
            if url_errors is not None:
                url_errors.append(err)
            return dest
        err = refresh_from_url(url, dest, auth=auth)
        if err is not None and url_errors is not None:
            url_errors.append(err)
        return dest
    for cand in LOCAL_ZIPS:
        p = cand if cand.is_absolute() else base / cand
        if p.is_file():
            return p
    raise FileNotFoundError(
        "MÁV GTFS not found. Set MAV_PFAEDLE_DIR / MAV_GTFS_ZIP / MAV_GTFS_URL "
        "(with MAV_GTFS_USER / MAV_GTFS_PASSWORD for MÁV's own download) or place mav-gtfs.zip here."
    )


def _mav_module():
    """The integration's builder, so Home Assistant and this script make the
    same index (schema 2: every stop pattern of a train with its stations)."""
    pkg = Path(__file__).resolve().parent.parent / "custom_components" / "hungarian_transport"
    if str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    import mav_index  # noqa: PLC0415

    return mav_index


def build_mav_index(zpath: Path, out: Path, *, audit: bool = True, min_ok: float = MIN_OK) -> dict:
    """Build, audit, then write. An index that fails the audit is not
    written, so the previous one stays in place."""
    mav = _mav_module()
    index = mav.build(zpath)
    stats = dict(index["_stats"])
    if stats["trains"] + stats["lines"] < 50:
        raise RuntimeError(f"MÁV index too small: {stats['trains']} trains, {stats['lines']} lines")
    if audit:
        report = mav.audit(zpath, index)
        stats["audit"] = {k: v for k, v in report.items() if k != "worst"}
        stats["audit_worst"] = report["worst"][:5]
        if report["ok_ratio"] < min_ok:
            worst = ", ".join(w["train"] for w in stats["audit_worst"])
            raise RuntimeError(
                f"MÁV index failed its audit: {report['trains_ok']}/{report['trains']} trains pass "
                f"({report['ok_ratio']:.1%} < {min_ok:.0%}); first failing: {worst}"
            )
    stats["bytes"] = mav.write(index, out)
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
