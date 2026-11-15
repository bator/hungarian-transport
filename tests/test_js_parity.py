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

# `const NAME = value;` at the start of a line, exported or not; the value is
# what is left up to the semicolon (a number, a quoted string, a name or a
# [list]). Only a top-level one: an indented const of the same name inside a
# function would otherwise stand in for it.
_CONST = re.compile(r"^(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*=\s*([^;]+);", re.M)
# Comments, left out before a constant's use is looked for: block comments,
# and line comments also at the end of a line of code. Quoted strings and
# template literals are kept whole, so the // of a URL in one is no comment.
_CODE_TOKEN = re.compile(
    r"""(?P<str>'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`)"""
    r"|(?P<comment>/\*.*?\*/|//[^\n]*)",
    re.S,
)


def _code_of(text: str) -> str:
    """The text without its comments."""
    return _CODE_TOKEN.sub(lambda m: m.group("str") or "", text)


def _consts_of(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in _CONST.finditer(text)}


def _js_consts(*files: str) -> dict[str, str]:
    """The top-level constants of these src/ files, by name, as source text."""
    out: dict[str, str] = {}
    for name in files:
        out.update(_consts_of((SRC / name).read_text(encoding="utf-8")))
    return out


def _js_uses(name: str, *patterns: str) -> None:
    """Each pattern (a regular expression) is in the code of src/<name>,
    comments left out: a mirrored constant that is still defined but no
    longer what the card's code limits with compares equal and proves
    nothing."""
    code = _code_of((SRC / name).read_text(encoding="utf-8"))
    for pattern in patterns:
        assert re.search(pattern, code), f"src/{name} no longer has {pattern!r}: update this test"


def test_a_use_left_in_a_comment_is_no_use() -> None:
    """A use that only a comment still shows, at the end of a line or in a
    block, does not count; a // inside a string is no comment."""
    text = (
        "const a = x; // b.slice(0, LIMIT)\n"
        "/* c.slice(0, LIMIT) */\n"
        "const url = 'https://example.org/x'; const d = e.slice(0, OTHER);\n"
        "const t = `a // b`; f.slice(0, THIRD);\n"
    )
    code = _code_of(text)
    assert "LIMIT" not in code
    assert "'https://example.org/x'" in code and "e.slice(0, OTHER)" in code
    assert "`a // b`" in code and "f.slice(0, THIRD)" in code


def test_only_top_level_constants_are_read() -> None:
    """A function's own const of the same name is not the top-level one."""
    text = "const LIMIT = 1;\nexport const NAMES = ['a',\n  'b'];\nfunction f() {\n  const LIMIT = 2;\n}\n"
    assert _consts_of(text) == {"LIMIT": "1", "NAMES": "['a',\n  'b']"}


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
    # The card types whose apiKey the dashboard key scan collects.
    _js_uses("lib/dashboard-key.js", r"OWN_CARD_TYPES\.indexOf\(\s*type\s*\)")


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
    # A feed's trips are cut at RT_MAX_TRIPS (attachRealtime), a row's stops
    # at RT_MAX_STOPS (rtRequest).
    _js_uses(
        "lib/realtime.js",
        r"\.trips\.length\s*>=\s*RT_MAX_TRIPS\b",
        r"\.slice\(\s*0\s*,\s*RT_MAX_STOPS\s*\)",
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
    _js_uses("lib/board.js", r"\.slice\(\s*0\s*,\s*ELVIRA_DEST_NAME_MAX\s*\)")
    # clampMinutesAfter
    _js_uses("config.js", r"Math\.min\(\s*MAX_MINUTES_AFTER\s*,\s*Math\.max\(\s*MIN_MINUTES_AFTER\s*,")
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


# The per-row limits of rtRequest (src/lib/realtime.js) and the schema's
# names for them (__init__.py), with the code that limits with each.
_RT_TRIP_FIELDS = (
    ("RT_KEY_MAX", "REALTIME_KEY_MAX", r"\.slice\(\s*0\s*,\s*RT_KEY_MAX\s*\)"),
    ("RT_TRIP_ID_MAX", "REALTIME_TRIP_ID_MAX", r"\.slice\(\s*0\s*,\s*RT_TRIP_ID_MAX\s*\)"),
    ("RT_ROUTE_MAX", "REALTIME_ROUTE_MAX", r"\.slice\(\s*0\s*,\s*RT_ROUTE_MAX\s*\)"),
    ("RT_START_MAX", "REALTIME_START_MAX", r"<=\s*RT_START_MAX\b"),
    ("RT_STOP_ID_MAX", "REALTIME_STOP_ID_MAX", r"\.length\s*<=\s*RT_STOP_ID_MAX\b"),
)


def test_the_realtime_row_fields_match_what_the_card_sends() -> None:
    """rtRequest cuts a row's key, trip_id and route and drops a start or
    stop id past its limit (src/lib/realtime.js); the service takes exactly
    those, no more. They used to be literals on both sides, compared by
    nothing."""
    consts = _js_consts("lib/realtime.js")
    js = {name: _js_const(consts, name, "src/lib/realtime.js") for name, _py, _use in _RT_TRIP_FIELDS}
    py = {name: getattr(integration, py_name) for name, py_name, _use in _RT_TRIP_FIELDS}
    assert js == py, f"src/lib/realtime.js {js} differs from __init__.py {py}"
    _js_uses("lib/realtime.js", *(use for _name, _py, use in _RT_TRIP_FIELDS))

    schema = integration.REALTIME_TRIP_SCHEMA
    longest = {
        "key": "k" * js["RT_KEY_MAX"],
        "trip_id": "t" * js["RT_TRIP_ID_MAX"],
        "route": "r" * js["RT_ROUTE_MAX"],
        "start": js["RT_START_MAX"],
        "stops": ["s" * js["RT_STOP_ID_MAX"]],
    }
    schema(longest)
    for bad in (
        {"key": "k" * (js["RT_KEY_MAX"] + 1)},
        {"trip_id": "t" * (js["RT_TRIP_ID_MAX"] + 1)},
        {"route": "r" * (js["RT_ROUTE_MAX"] + 1)},
        {"start": js["RT_START_MAX"] + 1},
        {"stops": ["s" * (js["RT_STOP_ID_MAX"] + 1)]},
    ):
        with pytest.raises(vol.Invalid):
            schema({**longest, **bad})
