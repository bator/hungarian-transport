"""The local update script follows the same-feed rule
(scripts/update_volan_index.py, mav_source_newer and served_mav_wins)."""

import gzip
import importlib
import json
import os
import random
import sys
import time
import zipfile
from pathlib import Path

import pytest

from custom_components.hungarian_transport import mav_index

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FEED = "20261101.M2ChangeID"
STATIONS = [["A", 4718361, 1842472], ["B", 4746432, 1902063]]
LINE = [(47.18361, 18.42472), (47.46432, 19.02063)]
RAIL = [(47.18361, 18.42472), (47.3301, 18.7012), (47.46432, 19.02063)]


@pytest.fixture(scope="module")
def uvi():
    sys.path.insert(0, str(SCRIPTS))
    try:
        yield importlib.import_module("update_volan_index")
    finally:
        sys.path.remove(str(SCRIPTS))


def _index(path: Path, feed: str, fitted: bool, built: int) -> None:
    body = {"v": 2, "feed": feed, "st": STATIONS, "p": [mav_index.encode_polyline(RAIL if fitted else LINE)],
            "n": {"905": [[0, [0, 1]]]}, "l": {}}
    path.write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=built))


@pytest.fixture
def downloaded(tmp_path):
    """MÁV's zip of FEED, downloaded at 04:30, after the daily index's CI build."""
    src = tmp_path / "mav-gtfs.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("feed_info.txt", f"feed_publisher_name,feed_version\nMÁV,{FEED}\n")
    built = int(time.time()) - 3 * 3600
    os.utime(src, (built + 600,) * 2)
    return src, tmp_path / "mav-index.json.gz", built


@pytest.mark.parametrize("fitted", [True, False])
def test_a_zip_of_the_served_feed_is_not_built_over_it(uvi, downloaded, fitted) -> None:
    """The daily pfaedle index (or any index) of the zip's own feed stays:
    a stop-sequence build of it would replace the daily index for good."""
    src, out, built = downloaded
    _index(out, FEED, fitted, built)
    assert uvi.mav_source_newer(src, out) is False


def test_a_zip_of_another_feed_is_built(uvi, downloaded) -> None:
    src, out, built = downloaded
    _index(out, "20261031.M2ChangeID", True, built)
    assert uvi.mav_source_newer(src, out) is True


def test_a_pfaedle_directory_rebuilds_only_a_stop_sequence_index(uvi, downloaded, tmp_path) -> None:
    _src, out, built = downloaded
    gtfs_out = tmp_path / "gtfs-out"
    gtfs_out.mkdir()
    (gtfs_out / "feed_info.txt").write_text(f"feed_publisher_name,feed_version\nMÁV,{FEED}\n", encoding="utf-8")
    os.utime(gtfs_out / "feed_info.txt", (built + 600,) * 2)
    _index(out, FEED, False, built)
    assert uvi.mav_source_newer(gtfs_out, out) is True
    _index(out, FEED, True, built)
    assert uvi.mav_source_newer(gtfs_out, out) is False


def _padded(path: Path, feed: str, fitted: bool, pad: int, built: int = 0) -> int:
    """An index of feed with pad incompressible bytes; its gzipped size."""
    rnd = random.Random(pad)
    body = {"v": 2, "feed": feed, "st": STATIONS, "p": [mav_index.encode_polyline(RAIL if fitted else LINE)],
            "n": {"905": [[0, [0, 1]]]}, "l": {}, "pad": "".join(rnd.choice("0123456789abcdef") for _ in range(pad))}
    path.write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=built))
    return path.stat().st_size


@pytest.mark.parametrize(
    ("served_fitted", "durable_fitted", "served_pad", "durable_pad", "served_wins"),
    [
        # The fitted rank decides before the size: a fitted served index
        # stays against a much larger stop-sequence copy, and a fitted
        # durable copy replaces a much larger stop-sequence one.
        (True, False, 0, 200_000, True),
        (False, True, 200_000, 0, False),
        # Of one kind, only a copy clearly larger replaces the served one.
        (False, False, 0, 200_000, False),
        (False, False, 200_000, 0, True),
        (True, True, 0, 200_000, False),
        (True, True, 1000, 1000, True),
    ],
)
def test_served_mav_wins_by_the_same_feed_rule(
    uvi, tmp_path, served_fitted, durable_fitted, served_pad, durable_pad, served_wins
) -> None:
    served, durable = tmp_path / "served.json.gz", tmp_path / "durable.json.gz"
    _padded(served, FEED, served_fitted, served_pad)
    _padded(durable, FEED, durable_fitted, durable_pad)
    assert uvi.served_mav_wins(durable, served) is served_wins


def test_served_mav_wins_of_another_feed_by_build_time(uvi, tmp_path) -> None:
    served, durable = tmp_path / "served.json.gz", tmp_path / "durable.json.gz"
    _padded(served, "20261031.M2ChangeID", True, 0, built=1_700_000_000)
    _padded(durable, FEED, False, 0, built=1_700_003_600)
    assert uvi.served_mav_wins(durable, served) is False
    _padded(served, "20261031.M2ChangeID", False, 0, built=1_700_007_200)
    assert uvi.served_mav_wins(durable, served) is True
