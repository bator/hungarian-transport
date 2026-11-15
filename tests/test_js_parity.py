"""Values the Python side and the cards' JavaScript each keep a copy of.

The cards are built from src/ and cannot import the integration's
constants, nor it theirs, so the copies are compared here: a change on one
side alone fails CI instead of the card's call being refused at runtime or
an apiKey going unreported."""

from pathlib import Path
import re

import pytest
import voluptuous as vol

import custom_components.hungarian_transport as integration
from custom_components.hungarian_transport import lovelace_resource, realtime

SRC = Path(__file__).resolve().parents[1] / "src"

# `const NAME = value;`, exported or not; the value is what is left up to
# the semicolon (a number, a quoted string, a name or a [list]).
_CONST = re.compile(r"^\s*(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*=\s*([^;]+);", re.M)


def _js_consts(*files: str) -> dict[str, str]:
    """The top-level constants of these src/ files, by name, as source text."""
    out: dict[str, str] = {}
    for name in files:
        text = (SRC / name).read_text(encoding="utf-8")
        out.update({m.group(1): m.group(2).strip() for m in _CONST.finditer(text)})
    return out


def _js_value(consts: dict[str, str], source: str):
    """A constant's value: a number, a string, or a list of those, with the
    names in it looked up among `consts`."""
    source = source.strip()
    if source.startswith("[") and source.endswith("]"):
        items = [item for item in source[1:-1].split(",") if item.strip()]
        return [_js_value(consts, item) for item in items]
    if m := re.fullmatch(r"""(['"])(.*)\1""", source):
        return m.group(2)
    if re.fullmatch(r"\d+", source):
        return int(source)
    if source in consts:
        return _js_value(consts, consts[source])
    raise AssertionError(f"cannot read the JavaScript value {source!r}; is the constant still a literal?")


def _js_const(consts: dict[str, str], name: str, where: str):
    assert name in consts, f"{name} is no longer a top-level constant of {where}: update this test"
    return _js_value(consts, consts[name])


def test_the_own_card_types_match_the_cards_tags() -> None:
    """The apiKey report covers exactly the cards whose apiKey the cards
    read (src/tags.js OWN_CARD_TYPES, partly named in src/config.js)."""
    consts = _js_consts("config.js", "tags.js")
    js = _js_const(consts, "OWN_CARD_TYPES", "src/tags.js")
    assert len(js) == len(set(js)), f"src/tags.js OWN_CARD_TYPES lists a tag twice: {js}"
    assert set(js) == lovelace_resource.OWN_CARD_TYPES, (
        "lovelace_resource.OWN_CARD_TYPES differs from src/tags.js OWN_CARD_TYPES: "
        f"only in Python {sorted(lovelace_resource.OWN_CARD_TYPES - set(js))}, "
        f"only in JavaScript {sorted(set(js) - lovelace_resource.OWN_CARD_TYPES)}"
    )


def _trip(**extra) -> dict:
    return {"key": "k", **extra}


def test_the_realtime_limits_match_what_the_card_sends() -> None:
    """The card sends at most RT_MAX_TRIPS rows of RT_MAX_STOPS stops
    (src/lib/realtime.js); the service takes exactly that much, no more."""
    consts = _js_consts("lib/realtime.js")
    max_trips = _js_const(consts, "RT_MAX_TRIPS", "src/lib/realtime.js")
    max_stops = _js_const(consts, "RT_MAX_STOPS", "src/lib/realtime.js")
    assert (max_trips, max_stops) == (realtime.MAX_TRIPS, realtime.MAX_STOPS), (
        f"src/lib/realtime.js RT_MAX_TRIPS/RT_MAX_STOPS = {max_trips}/{max_stops}, "
        f"realtime.MAX_TRIPS/MAX_STOPS = {realtime.MAX_TRIPS}/{realtime.MAX_STOPS}"
    )

    schema = integration.REALTIME_SERVICE_SCHEMA
    schema({"feed": "volan", "trips": [_trip()] * max_trips})
    with pytest.raises(vol.Invalid):
        schema({"feed": "volan", "trips": [_trip()] * (max_trips + 1)})
    last = max_stops - 1
    stops = [f"s{i}" for i in range(max_stops)]
    schema({"feed": "volan", "trips": [_trip(stops=stops, repeat=[last], **{"from": 0, "to": last})]})
    for bad in (
        {"stops": [*stops, "one-more"]},
        {"repeat": [max_stops]},
        {"from": max_stops},
        {"to": max_stops},
    ):
        with pytest.raises(vol.Invalid):
            schema({"feed": "volan", "trips": [_trip(**bad)]})


def test_the_elvira_limits_match_what_the_card_sends() -> None:
    """The card cuts dest_name to ELVIRA_DEST_NAME_MAX (src/lib/board.js)
    and clamps the horizon to MIN/MAX_MINUTES_AFTER (src/config.js); the
    service takes exactly those."""
    board = _js_consts("lib/board.js")
    config = _js_consts("config.js")
    dest_max = _js_const(board, "ELVIRA_DEST_NAME_MAX", "src/lib/board.js")
    low = _js_const(config, "MIN_MINUTES_AFTER", "src/config.js")
    high = _js_const(config, "MAX_MINUTES_AFTER", "src/config.js")
    assert dest_max == integration.ELVIRA_DEST_NAME_MAX, (
        f"src/lib/board.js ELVIRA_DEST_NAME_MAX = {dest_max}, "
        f"__init__.ELVIRA_DEST_NAME_MAX = {integration.ELVIRA_DEST_NAME_MAX}"
    )

    schema = integration.ELVIRA_SERVICE_SCHEMA
    call = {"origin_code": "005501016", "dest_code": "005510009"}
    schema({**call, "dest_name": "x" * dest_max, "minutes_after": low})
    schema({**call, "minutes_after": high})
    for bad in ({"dest_name": "x" * (dest_max + 1)}, {"minutes_after": low - 1}, {"minutes_after": high + 1}):
        with pytest.raises(vol.Invalid):
            schema({**call, **bad})
