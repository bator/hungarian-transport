"""JSON nesting counted before it is decoded (jsonsafe.py), and the decoders
that run in Home Assistant's executor refusing a too deep answer with it."""

import asyncio
import gzip
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from custom_components.hungarian_transport import elvira, futar, jsonsafe, mav_daily, mavplusz

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent


def _depth(value) -> int:
    if isinstance(value, dict):
        return 1 + max(map(_depth, value.values()), default=0)
    if isinstance(value, list):
        return 1 + max(map(_depth, value), default=0)
    return 0


def _nested(depth: int) -> bytes:
    return b'{"a":' * (depth // 2) + b"[" * (depth - depth // 2) + b"1" + b"]" * (depth - depth // 2) + b"}" * (depth // 2)


@pytest.mark.parametrize("text", [b"[" * 513, b"[" * 100_000, _nested(513), _nested(9000)])
def test_json_nested_deeper_than_max_depth_is_a_recursion_error(text) -> None:
    for data in (text, bytearray(text), text.decode()):
        with pytest.raises(RecursionError, match="nested deeper than 512"):
            jsonsafe.check_depth(data)


def test_json_nested_max_depth_deep_passes_and_decodes() -> None:
    text = _nested(jsonsafe.MAX_DEPTH)
    jsonsafe.check_depth(text)
    assert _depth(jsonsafe.loads(text)) == jsonsafe.MAX_DEPTH


def test_brackets_in_strings_nest_nothing() -> None:
    """Escaped quotes and backslashes included: only a quote no backslash
    escapes ends a string."""
    for inner in ("[" * 2000, '\\"' + "{" * 2000, "\\\\" + "x" * 10, '\\\\\\"[' * 1000):
        text = json.dumps({"s": json.loads(f'"{inner}"'), "t": [1]})
        jsonsafe.check_depth(text, 2)
        jsonsafe.check_depth(text.encode(), 2)
        with pytest.raises(RecursionError):
            jsonsafe.check_depth(text, 1)


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64, 1 << 16])
def test_the_depth_counted_is_the_decoded_one_wherever_the_steps_split_the_text(monkeypatch, chunk) -> None:
    """Random JSON with strings full of brackets, quotes and backslashes,
    split into steps anywhere (an escape or a string across two of them)."""
    monkeypatch.setattr(jsonsafe, "_CHUNK", chunk)
    rnd = random.Random(chunk)
    alphabet = ["[", "]", "{", "}", '"', "\\", "é", "a", "a", "a"]

    def text() -> str:
        return "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 8)))

    def value(depth: int):
        roll = rnd.random()
        if depth <= 0 or roll < 0.3:
            return rnd.choice([1, None, True, text()])
        if roll < 0.65:
            return [value(depth - 1) for _ in range(rnd.randint(0, 3))]
        return {text(): value(depth - 1) for _ in range(rnd.randint(0, 3))}

    for _ in range(1500):
        doc = value(rnd.randint(0, 8))
        depth = _depth(doc)
        encoded = json.dumps(doc, ensure_ascii=rnd.random() < 0.5)
        for data in (encoded, encoded.encode()):
            jsonsafe.check_depth(data, depth)
            if depth:
                with pytest.raises(RecursionError):
                    jsonsafe.check_depth(data, depth - 1)


def test_text_that_is_no_json_is_left_to_the_decoder() -> None:
    """An unterminated string or a bracket closing nothing: the decoder
    stops there and says so (a ValueError), nested or not after it."""
    for text in ('["' + "[" * 2000, "]" + "[" * 2000, "[]]" + "[" * 2000):
        jsonsafe.check_depth(text)
        with pytest.raises(ValueError):
            jsonsafe.loads(text)


def test_utf16_json_is_counted_by_its_characters() -> None:
    """json.loads reads UTF-16 too; there a byte of another character can
    be a bracket's (U+5B5B is two '[' bytes)."""
    text = json.dumps({"k": "孛" * 1000}, ensure_ascii=False)
    assert jsonsafe.loads(text.encode("utf-16")) == {"k": "孛" * 1000}
    with pytest.raises(RecursionError):
        jsonsafe.check_depth(("[" * 600 + "]" * 600).encode("utf-16-le"))


def test_the_real_answers_nest_far_less_than_max_depth() -> None:
    """The fixtures are real answers (an ELVIRA board, mavplusz.hu); a
    Kelenföld day's board nests ten deep, as they do."""
    files = sorted(FIXTURES.glob("*/*.json"))
    assert files
    for path in files:
        raw = path.read_bytes()
        jsonsafe.check_depth(raw, 12)
        assert jsonsafe.loads(raw) == json.loads(raw)


def test_a_deep_answer_is_refused_before_it_is_decoded(monkeypatch) -> None:
    """By every decoder that runs in the executor, with the error each gives
    for nesting deeper than the decoder goes."""
    deep = b"[" * 9000 + b"]" * 9000

    def no_decoding(*_args, **_kwargs):
        raise AssertionError("decoded")

    monkeypatch.setattr(json, "loads", no_decoding)
    with pytest.raises(elvira.ElviraError, match="RecursionError"):
        elvira._parse_board(bytearray(deep))
    with pytest.raises(RecursionError):
        futar._decode(bytearray(deep))
    with pytest.raises(mav_daily.DailyIndexError, match="manifest is not JSON: JSON nested deeper"):
        mav_daily.check_manifest(deep)
    raw = gzip.compress(deep)
    man = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    with pytest.raises(mav_daily.DailyIndexError, match="index does not unpack: JSON nested deeper"):
        mav_daily.check_index(raw, man, 1)


async def test_a_deep_mavplusz_answer_is_a_value_error_without_decoding(aioclient_mock, monkeypatch) -> None:
    aioclient_mock.post(mavplusz.URL, content=b'{"errors":' + b"[" * 9000 + b"]" * 9000 + b"}")
    monkeypatch.setattr(json, "loads", lambda *_a, **_k: pytest.fail("decoded"))
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    with pytest.raises(ValueError, match="nested too deep"):
        await mavplusz.fetch(session, {})
    await session.close()


# Run in a process of its own: on the code before jsonsafe, the thread's
# stack overflowed and the process died (SIGSEGV), pytest with it.
IN_SMALL_STACK = """
import gzip, hashlib, sys, threading
sys.path.insert(0, sys.argv[1])
from custom_components.hungarian_transport import elvira, futar, mav_daily
deep = b"[" * 9000 + b"]" * 9000
raw = gzip.compress(deep)
man = {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
calls = {
    "elvira": lambda: elvira._parse_board(bytearray(deep)),
    "futar": lambda: futar._decode(bytearray(deep)),
    "manifest": lambda: mav_daily.check_manifest(deep),
    "index": lambda: mav_daily.check_index(raw, man, 1),
}
said = []
def run():
    for name, call in calls.items():
        try:
            call()
            said.append(name + " decoded")
        except Exception as err:
            said.append(name + " " + type(err).__name__)
# Home Assistant's container: Python built with THREAD_STACK_SIZE=0x100000.
threading.stack_size(1 << 20)
thread = threading.Thread(target=run)
thread.start()
thread.join()
print(",".join(said))
"""


def test_a_deep_answer_in_a_1_mib_thread_stack_is_an_error_not_a_dead_process() -> None:
    done = subprocess.run(
        [sys.executable, "-c", IN_SMALL_STACK, str(ROOT)], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, f"exit {done.returncode}: {done.stderr[-500:]}"
    assert done.stdout.strip() == "elvira ElviraError,futar RecursionError,manifest DailyIndexError,index DailyIndexError"
