#!/usr/bin/env python3
"""Whether a daily MÁV run that published nothing goes red (mav-index-daily.yml).

A run that deploys nothing keeps the previous site on GitHub Pages, and the
integration keeps its index (mav_daily.py says UP_TO_DATE). That is fine for
a day; it must not go on unnoticed. The run fails (exit 1, ::error::):

- at once when MÁV refused the login (HTTP 401/403; release_mav_index.sh
  exits AUTH_EXIT and never retries it, as every retry is another login):
  only new credentials help;
- when nothing was built or published (FAILURES) and the published
  manifest's built_at is STALE_AFTER or older, or names no build time.

Otherwise it stays green with a ::warning:: naming the published feed's age,
also when the published manifest cannot be read (its age is unknown; a
network error must not turn the run red). Nothing here deploys or removes
anything: the workflow deploys only a site that was made.

Usage: mav_daily_verdict.py --failure auth|build|site|secrets
         (--live-url URL | --live-file PATH) [--now ISO8601]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mav_pages_site import fetch_live, parse_live  # noqa: E402

# release_mav_index.sh's exit status for a refused login (401/403).
AUTH_EXIT = 77
STALE_AFTER = timedelta(days=2)
FAILURES = {
    "auth": "MÁV refused the login (HTTP 401/403); check MAV_GTFS_USER / MAV_GTFS_PASSWORD",
    "build": "the MÁV download, pfaedle or the audit failed",
    "site": "the built index did not make a Pages site",
    "secrets": "there are no MAV_GTFS_USER / MAV_GTFS_PASSWORD secrets",
}


def _built_at(live: dict) -> datetime | None:
    value = live.get("built_at")
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def verdict(failure: str, live: dict | None, why: str, now: datetime) -> tuple[bool, str]:
    """(fail the run, the annotation to print) for a run that published nothing
    because of failure (a FAILURES key), with the published manifest live (None
    when it could not be read, why says why) at now."""
    if failure not in FAILURES:
        raise ValueError(f"unknown failure {failure!r}")
    cause = FAILURES[failure]
    if failure == "auth":
        return True, (f"::error title=Daily MÁV index: login refused::{cause[0].upper()}{cause[1:]}. "
                      "Not retried (an account lockout); GitHub Pages keeps the previous index.")
    if live is None:
        return False, (f"::warning title=Daily MÁV index not published::Nothing published: {cause}. The published "
                       f"manifest could not be read ({why}), so its age is unknown; GitHub Pages keeps what it has.")
    built = _built_at(live)
    feed = live.get("feed")
    if built is None:
        return True, (f"::error title=Daily MÁV index: published manifest has no build time::Nothing published: "
                      f"{cause}, and the published manifest (feed {feed!r}) names no valid built_at.")
    age = now - built
    hours = int(age.total_seconds() // 3600)
    if age >= STALE_AFTER:
        return True, (f"::error title=Daily MÁV index is stale::Nothing published: {cause}. GitHub Pages still "
                      f"serves feed {feed!r}, built {live.get('built_at')} ({hours} h ago, the limit is "
                      f"{int(STALE_AFTER.total_seconds() // 3600)} h).")
    return False, (f"::warning title=Daily MÁV index not published::Nothing published: {cause}. GitHub Pages keeps "
                   f"feed {feed!r}, built {live.get('built_at')} ({hours} h ago).")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--failure", required=True, choices=sorted(FAILURES))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--live-url")
    src.add_argument("--live-file", type=Path)
    ap.add_argument("--now", help="the time to measure the age at (ISO 8601 with offset); default now")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            ap.error("--now needs a UTC offset")
    if args.failure == "auth":
        live, why = None, "not read"
    elif args.live_file is not None:
        live, why = parse_live(args.live_file.read_bytes()) if args.live_file.is_file() else (None, "no file")
    else:
        live, why = fetch_live(args.live_url)
    fail, line = verdict(args.failure, live, why, now)
    print(line)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
