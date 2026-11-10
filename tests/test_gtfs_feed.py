"""The one GTFS reading of every builder (gtfs_feed): Home Assistant's
local MÁV build, the realtime static train numbers and the release and
update scripts. Home Assistant free."""

import io
import subprocess
import sys
import tracemalloc
import zipfile
from pathlib import Path

import pytest

from custom_components.hungarian_transport import gtfs_feed, mav_index

PKG = Path(gtfs_feed.__file__).resolve().parent


def _zip(path: Path, files: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return path


class OpenOnly:
    """A feed without read(): the rows must be streamed."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def namelist(self) -> list[str]:
        return list(self.files)

    def open(self, name: str):
        return io.BytesIO(self.files[name])

    def read(self, name: str) -> bytes:
        raise AssertionError(f"{name} read whole")


# ---- MÁV route names ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "number"),
    [("IC 856 TÓPART", "856"), ("9400", "9400"), ("G43", ""), ("S 0712", "712"), ("", ""), ("12", "")],
)
def test_train_number(name: str, number: str) -> None:
    assert gtfs_feed.train_number(name) == number


def test_line_code() -> None:
    assert gtfs_feed.line_code("G43") == "G43"
    assert gtfs_feed.line_code("", "s40") == "S40"
    assert gtfs_feed.line_code("IC 856", "Budapest - Szeged") == ""


# ---- encoded polylines ---------------------------------------------------------


def test_polyline_is_googles_encoding() -> None:
    pts = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert gtfs_feed.encode_polyline(pts) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert gtfs_feed.decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == pts
    assert gtfs_feed.decode_polyline("") == []


def test_polyline_roundtrip() -> None:
    pts = [(47.49889, 19.025), (47.18361, 18.42472), (46.44083, 16.98667)]
    dec = gtfs_feed.decode_polyline(gtfs_feed.encode_polyline(pts))
    assert len(dec) == 3 and all(abs(a - b) < 1e-5 for p, q in zip(pts, dec) for a, b in zip(p, q))


# ---- members and rows ------------------------------------------------------------


def test_member_in_any_case_and_in_a_subfolder() -> None:
    z = OpenOnly({"feed/Stops.TXT": b"", "Trips.txt": b"", "x/trips.txt": b""})
    assert gtfs_feed.member(z, "stops.txt") == "feed/Stops.TXT"
    # The top-level one wins over one in a subfolder.
    assert gtfs_feed.member(z, "trips.txt") == "Trips.txt"
    with pytest.raises(FileNotFoundError):
        gtfs_feed.member(z, "routes.txt")


def test_rows_strip_headers_and_values() -> None:
    """An export with a space after each comma (" T1") gives the ids the
    other files use, or trips lose their service and stops."""
    src = OpenOnly({
        "trips.txt": "﻿ route_id , trip_id ,service_id\nR1, T1 , S1\n\nR2,T2\nR3,T3,S3,extra\n".encode(),
    })
    rows = list(gtfs_feed.open_csv(src, "trips.txt"))
    assert rows == [
        {"route_id": "R1", "trip_id": "T1", "service_id": "S1"},
        {"route_id": "R2", "trip_id": "T2", "service_id": ""},
        {"route_id": "R3", "trip_id": "T3", "service_id": "S3"},
    ]


def test_rows_read_cp1250() -> None:
    src = OpenOnly({
        "legacy.txt": "stop_id,stop_name\n1,".encode() + "Hősök tere".encode("cp1250") + b"\n",
        "mixed.txt": "stop_id,stop_name\n1,Szőny\n2,".encode() + "Érd".encode("cp1250") + b"\n",
    })
    assert next(gtfs_feed.open_csv(src, "legacy.txt"))["stop_name"] == "Hősök tere"
    assert [r["stop_name"] for r in gtfs_feed.open_csv(src, "mixed.txt")] == ["Szőny", "Érd"]


def test_missing_member_raises_before_iteration() -> None:
    with pytest.raises(FileNotFoundError):
        gtfs_feed.open_csv(OpenOnly({}), "stops.txt")


def test_rows_are_streamed(tmp_path: Path) -> None:
    big = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n" + "".join(
        f"T{i // 20},08:00:00,08:00:00,{i % 997},{i % 20}\n" for i in range(300_000)
    )
    with zipfile.ZipFile(_zip(tmp_path / "big.zip", {"stop_times.txt": big})) as z:
        tracemalloc.start()
        count = sum(1 for _ in gtfs_feed.open_csv(z, "stop_times.txt"))
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    assert count == 300_000 and peak < len(big) / 4


def test_feed_version() -> None:
    src = OpenOnly({"feed_info.txt": b"feed_publisher_name, feed_version\nMAV, 20261101.M2ChangeID \n"})
    assert gtfs_feed.feed_version(src) == "20261101.M2ChangeID"
    assert gtfs_feed.feed_version(OpenOnly({})) == ""


def test_feed_reads_a_zip_and_a_directory_alike(tmp_path: Path) -> None:
    files = {"Feed_Info.txt": "feed_version\nv1\n", "stops.txt": "stop_id,stop_name\n1,A\n"}
    folder = tmp_path / "gtfs-out"
    folder.mkdir()
    for name, body in files.items():
        (folder / name).write_text(body, encoding="utf-8")
    for path in (_zip(tmp_path / "f.zip", files), folder):
        with gtfs_feed.Feed(path) as feed:
            assert feed.names() == {"feed_info.txt", "stops.txt"}
            assert gtfs_feed.feed_version(feed) == "v1"
            assert list(feed.rows("stops.txt")) == [{"stop_id": "1", "stop_name": "A"}]
            assert list(feed.rows("shapes.txt")) == []


def test_reader_imports_without_home_assistant() -> None:
    """The scripts import it by path, as mav_index does in its own process."""
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import gtfs_feed, mav_index;"
        " assert mav_index.gtfs_feed is gtfs_feed; assert 'homeassistant' not in sys.modules"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code, str(PKG)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


# ---- Home Assistant's MÁV build reads as the release does ---------------------


STATIONS = [("1", "Székesfehérvár", 47.18361, 18.42472), ("2", "Martonvásár", 47.3157, 18.788),
            ("3", "Budapest-Kelenföld", 47.46432, 19.02063)]


def _mav_files(sep: str = ",", encoding: str = "utf-8") -> dict[str, bytes]:
    def table(header: str, rows: list[tuple]) -> bytes:
        lines = [header.replace(",", sep)] + [sep.join(str(v) for v in row) for row in rows]
        return ("\n".join(lines) + "\n").encode(encoding)

    trips = [("R905", "S1", "T1", ""), ("R858", "S1", "T2", "")]
    times = [(t, f"0{h}:00:00", f"0{h}:00:00", s, n) for t in ("T1", "T2") for n, (s, h) in
             enumerate(zip(("1", "2", "3"), (6, 7, 8)), 1)]
    return {
        "feed_info.txt": table("feed_publisher_name,feed_version", [("MÁV", "test")]),
        "routes.txt": table("route_id,route_short_name,route_long_name,route_type",
                            [("R905", "", "IC 905 BAKONY", 2), ("R858", "", "IC 858", 2)]),
        "trips.txt": table("route_id,service_id,trip_id,shape_id", trips),
        "stops.txt": table("stop_id,stop_name,stop_lat,stop_lon", STATIONS),
        "stop_times.txt": table("trip_id,arrival_time,departure_time,stop_id,stop_sequence", times),
    }


def _index(path: Path) -> dict:
    return {k: v for k, v in mav_index.build(path).items() if not k.startswith("_")}


def test_mav_build_reads_a_padded_cp1250_feed_in_a_subfolder(tmp_path: Path) -> None:
    """The feed quirks the release build always read: a space after every
    comma, cp1250 station names, members in a folder of the zip."""
    plain = _index(_zip(tmp_path / "plain.zip", _mav_files()))
    assert sorted(plain["n"]) == ["858", "905"]
    quirky = _mav_files(sep=", ", encoding="cp1250")
    path = _zip(tmp_path / "quirky.zip", {f"gtfs/{name}": body for name, body in quirky.items()})
    mav_index.check_feed(path)
    assert _index(path) == plain
