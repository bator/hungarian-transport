#!/usr/bin/env python3
"""Audit mav-index.json.gz against the MÁV GTFS it was built from.

Every trip is drawn the way the card draws it (the variant serving its first
and last station) and every one of its stations must lie on that route.

Usage: audit_mav_index.py GTFS(.zip|dir) mav-index.json.gz [--json report.json]
Exit status 1 when fewer than --min-ok (default 0.99) of the trains pass.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "hungarian_transport"))

import mav_index  # noqa: E402
from mav_variant import use_card_picker  # noqa: E402

# The trains are drawn as the card draws them (#77), see mav_variant.py.
use_card_picker(mav_index)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("gtfs", type=Path)
    ap.add_argument("index", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--min-ok", type=float, default=0.99)
    args = ap.parse_args()
    report = mav_index.audit(args.gtfs, mav_index.read(args.index))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"mav audit: {report['trains_ok']}/{report['trains']} trains pass ({report['ok_ratio']:.1%}), "
        f"{report['trips_bad']}/{report['trips']} trips off route"
    )
    for w in report["worst"][:15]:
        print("  ", json.dumps(w, ensure_ascii=False))
    if report["ok_ratio"] < args.min_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
