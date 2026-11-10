#!/usr/bin/env python3
"""The MÁV audit picks a train's stop pattern as the card does: the shared
cases in testdata/mav_variant_cases.json (the card's pickMavVariant answers
the same ones) against mav_index.pick_variant, the one pick Home
Assistant's audit and the scripts' audits use. No network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_mav_index import _mav_module  # noqa: E402

CASES = json.loads((HERE / "testdata" / "mav_variant_cases.json").read_text(encoding="utf-8"))

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        failed += 1


def as_case(variants: list, got: tuple) -> dict | None:
    """(variant, board, alight) as the vector writes it: the variant's
    position in variants, or None for no route."""
    var, b, a = got
    if var is None:
        return None
    return {"variant": next(i for i, v in enumerate(variants) if v is var), "board": b, "alight": a}


mav = _mav_module()
check("the shared cases are there", len(CASES["cases"]) >= 8, str(len(CASES["cases"])))
for case in CASES["cases"]:
    index = {"st": CASES["stations"], "n": {"1": case["variants"]}, "l": {}}
    got = mav.pick_variant(index, ("n", "1"), case["board"], case["alight"], case["headsign"])
    check(f"pick: {case['name']}", as_case(case["variants"], got) == case["expect"],
          repr((as_case(case["variants"], got), case["expect"])))

# The audit draws a trip between its own first and last station: the last
# is its headsign too, so a pattern towards another terminus is no route.
index = {"st": CASES["stations"], "n": {"8714": [[0, [1, 4]], [0, [3, 1, 4]]]}, "l": {}}
check("audit pick: the headsign defaults to the alighting station",
      mav.pick_variant(index, ("n", "8714"), "Székesfehérvár", "Budapest-Kelenföld") == (None, -1, -1))
check("audit pick: an unknown train has no route",
      mav.pick_variant(index, ("n", "9999"), "Székesfehérvár", "Siófok") == (None, -1, -1)
      and mav.pick_variant(index, ("l", "S10"), "", "") == (None, -1, -1))

# A trip on a pattern the audit does not find is reported as no route.
feed_index = {"st": CASES["stations"], "p": [""], "n": {"8714": [[0, [1, 4]]]}, "l": {}}
stops = {"a": ("Székesfehérvár", 47.18, 18.42), "b": ("Budapest-Kelenföld", 47.46, 19.02)}
report = mav.audit(Path("unused"), feed_index, ({"T": (("n", "8714"), "")}, stops, {"T": ("a", "b")}))
check("audit: a trip no pattern serves in order is no route",
      report["trips_bad"] == 1 and report["worst"][0]["why"] == "no route", repr(report))

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("MÁV variant tests passed")
