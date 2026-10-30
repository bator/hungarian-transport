"""The stop pattern of a MÁV train the card draws, in Python: the twin of
pickMavVariant (src/lib/gtfs.js, #77) for the scripts' MÁV audit.

scripts/testdata/mav_variant_cases.json holds the cases both must answer
alike (scripts/test_mav_variant.py checks this side). The integration's
mav_index.pick_variant still falls back to a pattern serving the boarding
station only, or to the first pattern, where the card draws nothing from
the index; use_card_picker() puts this one in its place for the audits the
scripts run (build_mav_index.py, audit_mav_index.py). The audit's verdict
is the same either way (a fallback pattern never serves both stations, so
the trip fails either way); what differs is whether the report says "no
route" or "stations not in any variant", and that the two picks can no
longer drift apart unnoticed.
"""
from __future__ import annotations

from types import ModuleType
from typing import Any


def pick_variant(
    index: dict[str, Any], key: tuple[str, str], board: str, alight: str, headsign: str | None = None
):
    """The variant the card draws for train key between board and alight:
    the first (longest) pattern serving board and then alight; else the
    first serving board, before its end, whose last station is headsign;
    else none: (None, -1, -1). Without board, the first pattern. headsign
    defaults to alight, as for a trip drawn to its own last station (the
    audit). Returns (variant, board_pos, alight_pos), like
    mav_index.pick_variant. Names compare by mav_index.fold."""
    from mav_index import fold  # noqa: PLC0415  (the integration's module, on sys.path)

    kind, value = key
    variants = (index.get("n" if kind == "n" else "l") or {}).get(value) or []
    if not board:
        return (variants[0], -1, -1) if variants else (None, -1, -1)
    stations = index.get("st") or []
    fb, fa = fold(board), fold(alight) if alight else ""
    head = alight if headsign is None else headsign
    fh = fold(head) if head else ""

    def name(i: int) -> str:
        return fold(stations[i][0]) if 0 <= i < len(stations) else ""

    to_head = None
    for var in variants:
        names = [name(i) for i in var[1]]
        if fb not in names:
            continue
        bpos = names.index(fb)
        apos = names.index(fa, bpos + 1) if fa and fa in names[bpos + 1:] else -1
        if apos > bpos:
            return var, bpos, apos
        if to_head is None and fh and bpos < len(names) - 1 and names[-1] == fh:
            to_head = (var, bpos, -1)
    return to_head or (None, -1, -1)


def use_card_picker(mav: ModuleType) -> ModuleType:
    """mav (the integration's mav_index module) with this pick_variant in
    place of its own, for mav.audit; mav itself, for chaining."""
    mav.pick_variant = pick_variant
    return mav
