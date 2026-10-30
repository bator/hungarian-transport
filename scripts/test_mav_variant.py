#!/usr/bin/env python3
"""The MÁV audit picks a train's stop pattern as the card does: the shared
cases in testdata/mav_variant_cases.json (the card's pickMavVariant answers
the same ones) against mav_variant.pick_variant, and the scripts' audits
using it. No network."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mav_variant  # noqa: E402
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
check("the build script's MÁV module audits with the card's pick", mav.pick_variant is mav_variant.pick_variant)
check("the shared cases are there", len(CASES["cases"]) >= 8, str(len(CASES["cases"])))
for case in CASES["cases"]:
    index = {"st": CASES["stations"], "n": {"1": case["variants"]}, "l": {}}
    got = mav_variant.pick_variant(index, ("n", "1"), case["board"], case["alight"], case["headsign"])
    check(f"pick: {case['name']}", as_case(case["variants"], got) == case["expect"],
          repr((as_case(case["variants"], got), case["expect"])))

# The audit draws a trip between its own first and last station: the last
# is its headsign too, so a pattern towards another terminus is no route.
index = {"st": CASES["stations"], "n": {"8714": [[0, [1, 4]], [0, [3, 1, 4]]]}, "l": {}}
check("audit pick: the headsign defaults to the alighting station",
      mav_variant.pick_variant(index, ("n", "8714"), "Székesfehérvár", "Budapest-Kelenföld") == (None, -1, -1))
check("audit pick: an unknown train has no route",
      mav_variant.pick_variant(index, ("n", "9999"), "Székesfehérvár", "Siófok") == (None, -1, -1)
      and mav_variant.pick_variant(index, ("l", "S10"), "", "") == (None, -1, -1))

# audit_mav_index.py puts the same pick in place when it is imported.
sys.modules.pop("audit_mav_index", None)
mav.pick_variant = lambda *_a, **_k: (None, -1, -1)
importlib.import_module("audit_mav_index")
check("audit_mav_index.py audits with the card's pick", sys.modules["mav_index"].pick_variant is mav_variant.pick_variant)

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("MÁV variant tests passed")
