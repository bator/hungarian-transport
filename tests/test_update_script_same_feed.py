"""The local update script follows the same-feed rule
(scripts/update_volan_index.py, mav_source_newer)."""

import gzip
import importlib
import json
import os
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
