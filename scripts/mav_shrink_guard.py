#!/usr/bin/env python3
"""Refuse a MÁV index that lost most of the trains and lines of the one out.

A truncated MÁV feed can still be self-consistent: it passes the build's
absolute floor (mav_index.MIN_TRAINS) and its audit, and it names a newer
feed, so without this it would replace the index users have. Compared on
trains + lines ("n" and "l" of mav-index.json.gz, "trains" and "lines" of
the Pages manifest): fewer than MIN_SHARE of the previous ones is refused,
when the previous one had at least MIN_GUARDED. MAV_ALLOW_SHRINK=1 (or
true, yes) accepts an intended reduction.

Used by mav_pages_site.py (against the published manifest) and by
release.yml (against the previous release's index), standard library only:
the release runs it from the workflow's own commit, without the
integration's package.

Usage: mav_shrink_guard.py NEW.json.gz PREVIOUS.json.gz
Exit status 1 when NEW is refused; 0 otherwise, also when either index does
not read (nothing to compare: the build's own checks decide).
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import zlib
from pathlib import Path

MIN_SHARE = 0.5
# As mav_index.MIN_TRAINS: below it there is no index worth guarding.
MIN_GUARDED = 50
ALLOW_ENV = "MAV_ALLOW_SHRINK"


def allowed() -> bool:
    """Whether MAV_ALLOW_SHRINK accepts a reduction."""
    return (os.environ.get(ALLOW_ENV) or "").strip().lower() in ("1", "true", "yes")


def shrink_problem(trains: int, lines: int, prev_trains: object, prev_lines: object) -> str:
    """Why trains + lines may not replace prev_trains + prev_lines, or "".
    Previous counts that are not integers (an older manifest, a value
    gone missing) or total under MIN_GUARDED compare with nothing."""
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (prev_trains, prev_lines)):
        return ""
    had, has = prev_trains + prev_lines, trains + lines
    if had < MIN_GUARDED or has >= MIN_SHARE * had:
        return ""
    return (f"{trains} trains and {lines} lines vs {prev_trains} and {prev_lines} before"
            f" (under {MIN_SHARE:.0%})")


def counts(path: Path) -> tuple[int, int] | None:
    """(trains, lines) of a MÁV index file; None when it does not read."""
    try:
        data = json.loads(gzip.decompress(Path(path).read_bytes()))
    except (OSError, EOFError, ValueError, zlib.error):
        return None
    if not isinstance(data, dict):
        return None
    trains, lines = data.get("n") or {}, data.get("l") or {}
    if not isinstance(trains, dict) or not isinstance(lines, dict):
        return None
    return len(trains), len(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: mav_shrink_guard.py NEW.json.gz PREVIOUS.json.gz", file=sys.stderr)
        return 2
    new, old = counts(Path(argv[0])), counts(Path(argv[1]))
    if new is None or old is None:
        print(f"mav shrink guard: {argv[0] if new is None else argv[1]} does not read; nothing compared")
        return 0
    why = shrink_problem(*new, *old)
    if not why:
        print(f"mav shrink guard: {new[0]} trains and {new[1]} lines vs {old[0]} and {old[1]} before")
        return 0
    if allowed():
        print(f"mav shrink guard: {why}; accepted, {ALLOW_ENV} is set")
        return 0
    print(f"mav shrink guard: refusing {argv[0]}: {why}; set {ALLOW_ENV}=1 to accept", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
