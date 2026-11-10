"""The served MÁV index (served_index.py): what an index file is, and the
decision whether another replaces it. Files on tmp_path, no Home Assistant."""

import gzip
import json
import os
import random
import zipfile
from datetime import date
from pathlib import Path

import pytest

from custom_components.hungarian_transport import gtfs_feed, mav_index, served_index

FEED = "20261101.M2ChangeID"
STATIONS = [["Székesfehérvár", 4718361, 1842472], ["Budapest-Kelenföld", 4746432, 1902063]]
# The line through the two stations (a stop-sequence variant), and a route
# that follows the rails between them (a pfaedle-fitted one).
LINE = [(47.18361, 18.42472), (47.46432, 19.02063)]
RAIL = [(47.18361, 18.42472), (47.3301, 18.7012), (47.46432, 19.02063)]
SLACK = mav_index.SAME_FEED_SLACK


def _index(path: Path, feed: str | None = FEED, *, fitted: bool = False, pad: int = 0, built: int = 0) -> int:
    """An index as mav_index.write lays it out ("feed" right after "v"); its
    gzipped size. pad adds incompressible bytes, so sizes differ after gzip."""
    rnd = random.Random(pad)
    body = {
        "v": mav_index.SCHEMA,
        "feed": feed,
        "st": STATIONS,
        "p": [gtfs_feed.encode_polyline(RAIL if fitted else LINE)],
        "n": {"905": [[0, [0, 1]]]},
        "l": {},
        "pad": "".join(rnd.choice("0123456789abcdef") for _ in range(pad)),
    }
    raw = gzip.compress(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(), mtime=built)
    path.write_bytes(raw)
    return len(raw)


# ---- what an index file is ----------------------------------------------------


def test_read_feed(tmp_path: Path) -> None:
    path = tmp_path / "mav-index.json.gz"
    _index(path)
    assert served_index.read_feed(path) == FEED
    _index(path, None)
    assert served_index.read_feed(path) is None
    _index(path, 'quoted "x"')
    assert served_index.read_feed(path) == 'quoted "x"'
    path.write_bytes(b"not gzip")
    assert served_index.read_feed(path) is None
    path.write_bytes(gzip.compress(b'{"v":2,"st":[]}'))
    assert served_index.read_feed(path) is None
    assert served_index.read_feed(tmp_path / "missing.json.gz") is None
    assert served_index.read_feed(tmp_path) is None


def test_read_feed_reads_a_long_head(tmp_path: Path) -> None:
    """The city index joins every operator's feed into its "feed"."""
    long_city = ";".join(f"feed{k}:{k:05d}.20261101" for k in range(400))
    assert len(long_city) > 4096
    path = tmp_path / "city-index.json.gz"
    path.write_bytes(gzip.compress(json.dumps({"v": 29, "feed": long_city}, separators=(",", ":")).encode()))
    assert served_index.read_feed(path) == long_city


def test_head_feed_reads_a_partial_head() -> None:
    assert served_index.head_feed(b'{"v":2,"feed":"20261101.M2ChangeID","st":[["A"') == FEED
    assert served_index.head_feed(b'{"v":2,"feed":"20261101.M2Cha') is None
    assert served_index.head_feed(b'{"v":2,"feed":null,') is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20261022.M2ChangeID", date(2026, 10, 22)),
        ("volan:01716.20261102", date(2026, 11, 2)),
        ("20261231", date(2026, 12, 31)),
        ("M2ChangeID20261022", date(2026, 10, 22)),
        # No real date: undated, and a later run does not stand in for it.
        ("20261399.M2ChangeID", None),
        ("20260230.M2ChangeID", None),
        ("20261399.20261101", None),
        # Not eight digits on their own.
        ("volan:01716", None),
        ("202610221.x", None),
        ("local-feed", None),
        ("", None),
        (None, None),
    ],
)
def test_feed_date(value, expected) -> None:
    assert served_index.feed_date(value) == expected


def test_built_at(tmp_path: Path) -> None:
    path = tmp_path / "mav-index.json.gz"
    _index(path, built=1_700_000_000)
    assert served_index.built_at(path) == 1_700_000_000
    # No build time in the header (the Volán and city indexes), or no gzip:
    # the file's own mtime.
    for raw in (gzip.compress(b"{}", mtime=0), b"plain"):
        path.write_bytes(raw)
        os.utime(path, (1_600_000_000, 1_600_000_000))
        assert served_index.built_at(path) == 1_600_000_000
    with pytest.raises(OSError):
        served_index.built_at(tmp_path / "missing.json.gz")


def test_read_rank(tmp_path: Path) -> None:
    path = tmp_path / "mav-index.json.gz"
    size = _index(path, fitted=True)
    assert served_index.read_rank(path) == mav_index.IndexRank(True, size)
    size = _index(path)
    assert served_index.read_rank(path) == mav_index.IndexRank(False, size)
    path.write_bytes(gzip.compress(b"[]"))
    assert served_index.read_rank(path) is None


# ---- does a candidate of the same feed replace it -----------------------------


@pytest.mark.parametrize(
    ("served_fitted", "new_fitted", "larger", "expected"),
    [
        # A fitted served index: only a fitted one clearly larger replaces it.
        (True, True, SLACK + 1, True),
        (True, True, SLACK, False),
        (True, False, 100_000, False),
        (True, None, 100_000, False),
        # A stop-sequence one: any fitted one, or one clearly larger.
        (False, True, -1000, True),
        (False, False, SLACK + 1, True),
        (False, False, SLACK, False),
        (False, None, 100_000, False),
    ],
)
def test_replaces_a_served_index_of_its_feed(tmp_path: Path, served_fitted, new_fitted, larger, expected) -> None:
    served = tmp_path / "mav-index.json.gz"
    size = _index(served, fitted=served_fitted, pad=150_000)
    new = mav_index.IndexRank(new_fitted, None if new_fitted is None else size + larger)
    assert served_index.replaces(new, served) is expected


def test_a_build_not_made_yet_replaces_only_what_does_not_read(tmp_path: Path) -> None:
    served = tmp_path / "mav-index.json.gz"
    _index(served)
    assert not served_index.replaces(served_index.LOCAL_BUILD, served)
    _index(served, fitted=True)
    assert not served_index.replaces(served_index.LOCAL_BUILD, served)
    # Missing or corrupt: anything replaces it.
    served.write_bytes(b"\x1f\x8bnot really")
    assert served_index.replaces(served_index.LOCAL_BUILD, served)
    served.unlink()
    assert served_index.replaces(served_index.LOCAL_BUILD, served)


# ---- of another feed ------------------------------------------------------------


@pytest.mark.parametrize(
    ("feed", "than", "expected"),
    [
        ("20261102.M2ChangeID", "20261101.M2ChangeID", True),
        ("20261101.other", "20261101.M2ChangeID", False),
        ("20261031.M2ChangeID", "20261101.M2ChangeID", False),
        ("20261101.M2ChangeID", None, True),
        ("20261101.M2ChangeID", "release", True),
        (None, "20261101.M2ChangeID", False),
        ("release", None, False),
    ],
)
def test_newer_feed(feed, than, expected) -> None:
    assert served_index.newer_feed(feed, than) is expected


TODAY = date(2026, 10, 23)


@pytest.mark.parametrize(
    ("local", "index_feed", "expected"),
    [
        # An index of a newer feed beats the local one; an older, the same
        # date or the same feed_version does not.
        (("20260601.M2ChangeID", "20260601", "20261231"), "20261023.M2ChangeID", True),
        (("20261030.M2ChangeID", "20261030", "20261231"), "20261023.M2ChangeID", False),
        (("20261023.other", "20261023", "20261231"), "20261023.M2ChangeID", False),
        (("20261023.M2ChangeID", "20260101", "20261001"), "20261023.M2ChangeID", False),
        # An expired local feed loses to any dated or undated index that names
        # its feed; one that ends today still runs.
        (("20261030.M2ChangeID", "20261001", "20261022"), "20261023.M2ChangeID", True),
        (("20261030.M2ChangeID", "20261001", "20261023"), "20261023.M2ChangeID", False),
        (("20260601.M2ChangeID", "20260101", "20260601"), "release", True),
        # A local feed without a date in its feed_version is dated by its
        # start, then by its end; an undated one keeps its place.
        (("local-feed", "20260601", "20261231"), "20261023.M2ChangeID", True),
        (("local-feed", "20261101", "20261231"), "20261023.M2ChangeID", False),
        (("local-feed", "", "20261030"), "20261023.M2ChangeID", False),
        (("local-feed", "", ""), "20261023.M2ChangeID", False),
        (("20261399.M2ChangeID", "", ""), "20261023.M2ChangeID", False),
        # An index that cannot be dated never beats a running dated feed, and
        # one that names no feed never wins, not even over an expired one.
        (("20260601.M2ChangeID", "20260601", "20261231"), "release", False),
        (("20260601.M2ChangeID", "20260101", "20260601"), None, False),
    ],
)
def test_superseded(local, index_feed, expected) -> None:
    assert served_index.superseded(served_index.FeedInfo(*local), index_feed, TODAY) is expected


def test_source_info(tmp_path: Path) -> None:
    src = tmp_path / "mav-gtfs.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr(
            "feed_info.txt",
            "feed_publisher_name,feed_version,feed_start_date,feed_end_date\nMÁV, 20261101.M2ChangeID ,20261101,20261231\n",
        )
    assert served_index.source_info(src) == served_index.FeedInfo("20261101.M2ChangeID", "20261101", "20261231")
    assert served_index.source_info(src).day == date(2026, 11, 1)
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("routes.txt", "route_id\n")
    assert served_index.source_info(src) == served_index.FeedInfo()
    src.write_bytes(b"not a zip")
    assert served_index.source_info(src) == served_index.FeedInfo()
    assert served_index.source_info(tmp_path / "missing.zip") == served_index.FeedInfo()
