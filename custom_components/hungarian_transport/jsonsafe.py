"""JSON from upstreams, refused before decoding when it nests too deep.

json.loads recurses in C for every array or object inside another, and on
Python 3.12 and 3.13 it only stops at a recursion limit counted in calls,
not in stack. Home Assistant's container builds Python with 1 MiB thread
stacks, so in its executor an answer nested some 9000 deep overflows the
thread's stack and the whole process dies (SIGSEGV) instead of raising
RecursionError. An answer of ELVIRA, FUTÁR or GitHub Pages can be anything,
so its nesting is counted first (check_depth): one pass over the text, a
_CHUNK at a time, that skips the strings (a bracket in a string nests
nothing).

check_depth raises RecursionError, the error json.loads raises where it
can, so a caller's handling of a too deep answer stays the one it has.
"""
from __future__ import annotations

from itertools import accumulate
import json
from typing import Any

# The deepest nesting decoded. The real answers nest about ten deep (an
# ELVIRA board, a mavplusz.hu answer; FUTÁR's and the daily index less);
# 512 decodes in a 1 MiB thread stack with room to spare, whatever
# object_pairs_hook calls back.
MAX_DEPTH = 512
# The text scanned per step, so a step's copies stay this small.
_CHUNK = 1 << 16
# Every byte but a quote and the brackets.
_NOT_STRUCTURE = bytes(b for b in range(256) if b not in b'"[]{}')
# A bracket's step in depth, by its byte.
_STEP = tuple(1 if b in b"[{" else -1 if b in b"]}" else 0 for b in range(256))


def _chunks(data: str | bytes | bytearray):
    """The text as UTF-8 bytes, _CHUNK at a time."""
    if isinstance(data, str):
        for pos in range(0, len(data), _CHUNK):
            yield data[pos : pos + _CHUNK].encode("utf-8", "surrogatepass")
        return
    encoding = json.detect_encoding(data)
    if encoding not in ("utf-8", "utf-8-sig"):
        # UTF-16 or 32 (json.loads reads them too): a byte of another
        # character can look like a bracket there. No upstream sends them.
        yield from _chunks(data.decode(encoding, "surrogatepass"))
        return
    view = memoryview(data)
    try:
        for pos in range(0, len(data), _CHUNK):
            yield bytes(view[pos : pos + _CHUNK])
    finally:
        view.release()


def check_depth(data: str | bytes | bytearray, max_depth: int = MAX_DEPTH) -> None:
    """RecursionError when the JSON text nests deeper than max_depth.

    In UTF-8 the quotes, backslashes and brackets are those bytes only,
    never part of another character. A backslash escapes the byte after it
    (an escaped backslash or quote is dropped before the quotes are
    counted): outside a string it is no JSON, and the decoder stops at it
    anyway. Text that is no JSON at all passes when its brackets do:
    json.loads then says what is wrong with it."""
    depth = 0
    in_string = False
    # The step before ended on a backslash escaping this step's first byte.
    escaped = False
    for chunk in _chunks(data):
        if escaped:
            chunk = chunk[1:]
        escaped = False
        if b"\\" in chunk:
            chunk = chunk.replace(b"\\\\", b"")
            if chunk.endswith(b"\\"):
                escaped = True
                chunk = chunk[:-1]
            chunk = chunk.replace(b'\\"', b"")
        # Two quotes in a row are a string without brackets (nearly every
        # string): gone, they leave every other quote's count as it was.
        parts = chunk.translate(None, _NOT_STRUCTURE).replace(b'""', b"").split(b'"')
        # Between the quotes alternately outside and inside a string.
        brackets = b"".join(parts[1 if in_string else 0 :: 2])
        in_string ^= len(parts) % 2 == 0
        opens = brackets.count(b"[") + brackets.count(b"{")
        if depth + opens > max_depth:
            # Perhaps too deep: how deep, bracket by bracket.
            levels = list(accumulate(map(_STEP.__getitem__, brackets), initial=depth))
            if -1 in levels:
                # Up to the first bracket closing nothing: the decoder stops there.
                levels = levels[: levels.index(-1)]
            if max(levels) > max_depth:
                raise RecursionError(f"JSON nested deeper than {max_depth} levels")
        depth += 2 * opens - len(brackets)
        if depth < 0:
            # A bracket closing nothing: the decoder stops there.
            return


def loads(data: str | bytes | bytearray, **kwargs: Any) -> Any:
    """json.loads, after check_depth."""
    check_depth(data)
    return json.loads(data, **kwargs)
