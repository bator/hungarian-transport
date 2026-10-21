#!/usr/bin/env python3
"""Index builders refuse a broken rebuild, MÁV feed refresh and audit, and
streamed GTFS reading. No network."""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_city_index as bci  # noqa: E402
import build_mav_index as bmi  # noqa: E402
from build_volan_index import build_index  # noqa: E402
from gtfs_compact import (  # noqa: E402
    IndexGuardError,
    _open_csv,
    calendar_end,
    operator_stats,
    operator_trips,
    stale_parts,
    write_index,
)

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        failed += 1


def raises(fn, exc: type[BaseException] = Exception) -> BaseException | None:
    try:
        fn()
    except exc as err:
        return err
    return None


def gtfs_zip(path: Path, files: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return path


def city_feed(route: str = "12", trips: int = 1, end: str = "20271231") -> dict[str, str]:
    stop_times = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    trip_rows = ["route_id,service_id,trip_id"]
    for i in range(trips):
        trip_rows.append(f"R1,S1,T{i}")
        stop_times.append(f"T{i},08:{i % 60:02d}:00,08:{i % 60:02d}:00,1,1")
        stop_times.append(f"T{i},09:{i % 60:02d}:00,09:{i % 60:02d}:00,2,2")
    return {
        "agency.txt": "agency_id,agency_name\nA,Local\n",
        "stops.txt": "stop_id,stop_name\n1,Alpha\n2,Beta\n",
        "routes.txt": f"route_id,route_short_name,agency_id\nR1,{route},A\n",
        "trips.txt": "\n".join(trip_rows) + "\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        f"S1,1,1,1,1,1,1,1,20260101,{end}\n",
        "stop_times.txt": "\n".join(stop_times) + "\n",
    }


def part(op_id: str, trips: int, end: str = "20271231") -> dict:
    return {
        "op_id": op_id,
        "op_name": op_id,
        "feed": "",
        "s": [[f"{op_id}:1", "Alpha", "1"], [f"{op_id}:2", "Beta", "2"]],
        "g": [],
        "c": [["1111111", "20260101", end]],
        "a": [[]],
        "r": [[]],
        "t": [[0, "1", [0, 1], [480, 490], 3, -1]] * trips,
    }


# ---- 1. city index: a feed the previous index had must not vanish ----------

os.environ["HUNGARIAN_TRANSPORT_SKIP_CATALOG"] = "1"
os.environ.pop(bci.ALLOW_MISSING_ENV, None)
real_feeds = bci.CITY_FEEDS
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "city-index.json.gz"
    write_index([part("miskolc", 100), part("pecs", 80), part("volan-szeged", 60), part("weekendbus", 9)],
                out, with_operators=True)
    check("trips per operator read back", operator_trips(out) == {
        "miskolc": 100, "pecs": 80, "volan-szeged": 60, "weekendbus": 9}, repr(operator_trips(out)))
    good = [part("miskolc", 100), part("pecs", 80), part("volan-szeged", 50)]
    check("rebuild with every feed passes", raises(lambda: bci.guard_feeds(out, good, set())) is None)
    err = raises(lambda: bci.guard_feeds(out, [part("miskolc", 100), part("volan-szeged", 60)], set()))
    check("missing feed is refused", isinstance(err, IndexGuardError) and "pecs missing" in str(err), repr(err))
    err = raises(lambda: bci.guard_feeds(out, [part("miskolc", 40), part("pecs", 80), part("volan-eger", 60)], set()))
    check("feed that lost most of its trips is refused",
          isinstance(err, IndexGuardError) and "miskolc 40 trips vs 100" in str(err), repr(err))
    check("Volán local agencies count as one feed",
          raises(lambda: bci.guard_feeds(out, [part("miskolc", 100), part("pecs", 80), part("volan-eger", 60)],
                                         set())) is None)
    err = raises(lambda: bci.guard_feeds(out, [part("miskolc", 100), part("pecs", 80)], set()))
    check("missing Volán local routes are refused", isinstance(err, IndexGuardError) and "volan-local" in str(err))
    check("allowed feed may be missing",
          raises(lambda: bci.guard_feeds(out, [part("miskolc", 100), part("volan-szeged", 60)], {"pecs"})) is None)
    check("no previous index, nothing to compare",
          raises(lambda: bci.guard_feeds(Path(tmp) / "none.json.gz", [part("miskolc", 1)], set())) is None)

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    fixtures = base / "fixtures"
    fixtures.mkdir()
    gtfs_zip(fixtures / "a.zip", city_feed())
    gtfs_zip(fixtures / "b.zip", city_feed("7"))
    bci.CITY_FEEDS = [
        {"id": "a", "name": "A", "url": "https://a.example/a.zip", "file": "a.zip"},
        {"id": "b", "name": "B", "url": "https://public.example/b.zip", "url_env": "TEST_B_GTFS_URL", "file": "b.zip"},
    ]
    downloads: list[str] = []
    bad_urls: dict[str, bytes | None] = {}

    def fake_download(url: str, dest: Path, **_kw) -> dict:
        downloads.append(url)
        if url in bad_urls:
            body = bad_urls[url]
            if body is None:
                raise OSError("401")
            dest.write_bytes(body)
        else:
            shutil.copy(fixtures / Path(url).name, dest)
        return {"size": dest.stat().st_size}

    bci.download_feed = fake_download
    os.environ["TEST_B_GTFS_URL"] = "https://keyed.example/b.zip"
    bad_urls["https://keyed.example/b.zip"] = None
    out = base / "city-index.json.gz"
    info = bci.build_city_index(base, out)
    check("broken keyed override falls back to the public URL",
          downloads[-2:] == ["https://keyed.example/b.zip", "https://public.example/b.zip"]
          and set(operator_trips(out)) == {"a", "b"}, repr(downloads))

    (base / "b.zip").unlink()
    bad_urls["https://keyed.example/b.zip"] = b"PK not really a zip"
    bci.build_city_index(base, out)
    check("override zip that does not parse falls back to the public URL",
          downloads[-1] == "https://public.example/b.zip" and set(operator_trips(out)) == {"a", "b"},
          repr(downloads[-2:]))
    os.environ.pop("TEST_B_GTFS_URL", None)

    before = out.read_bytes()
    (base / "b.zip").write_bytes(b"<html>gone</html>")
    err = raises(lambda: bci.build_city_index(base, out))
    check("city rebuild without a feed the index had is refused",
          isinstance(err, IndexGuardError) and "b missing" in str(err), repr(err))
    check("refused city rebuild leaves the previous index", out.read_bytes() == before)

    os.environ[bci.ALLOW_MISSING_ENV] = "b"
    info = bci.build_city_index(base, out)
    check("allowed missing feed builds and is reported as skipped",
          "b" in info["skipped"] and set(operator_trips(out)) == {"a"}, repr(info))
    os.environ.pop(bci.ALLOW_MISSING_ENV, None)

    # The http:// publishers are the only ones allowed to fall back to http.
    seen_kw: list[dict] = []

    def recording_download(url: str, dest: Path, **kw) -> dict:
        seen_kw.append(kw)
        shutil.copy(fixtures / "a.zip", dest)
        return {"size": dest.stat().st_size}

    bci.download_feed = recording_download
    bci._load_feed(base, {"id": "c", "name": "C", "url": "http://c.example/c.zip", "file": "c.zip", "insecure": True})
    bci._load_feed(base, {"id": "d", "name": "D", "url": "https://d.example/d.zip", "file": "d.zip"})
    check("insecure feed entry reaches download_feed",
          [kw.get("allow_insecure") for kw in seen_kw] == [True, False], repr(seen_kw))
bci.CITY_FEEDS = real_feeds

# ---- 1b. a feed whose calendar already ended is not an index ----------------

check("calendar_end takes the latest end_date", calendar_end(part("x", 1)) == "20271231")
late = part("x", 1)
late["c"] = [["0000000", "20200101", "20991231"], ["1111100", "20260101", "20260630"]]
late["a"] = [[], ["20260705", "20260820"]]
check("calendar_end ignores the all-zero default row and counts added dates", calendar_end(late) == "20260820")
none = part("x", 1)
none["c"] = [["0000000", "20200101", "20991231"]]
check("a feed without a calendar has no end", calendar_end(none) == "")
check("stale_parts names the ended operators",
      stale_parts([part("fresh", 1), late], today="20260901") == [("x", "20260820")])
check("stale_parts on the last service day is still fresh", stale_parts([late], today="20260820") == [])

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "city-index.json.gz"
    err = raises(lambda: write_index([part("miskolc", 100), late], out, with_operators=True, today="20270101"))
    check("write_index refuses a part whose calendar ended",
          isinstance(err, IndexGuardError) and "calendar ended for x (2026-08-20)" in str(err) and not out.exists(),
          repr(err))
    write_index([part("miskolc", 100), none], out, with_operators=True, today="20270101")
    check("write_index accepts a part without a calendar", out.exists())

    stale_zip = gtfs_zip(Path(tmp) / "volan.zip", city_feed("1001", trips=3, end="20250131"))
    vout = Path(tmp) / "volan-index.json.gz"
    err = raises(lambda: build_index(stale_zip, vout, min_trips=3))
    check("Volán index from an ended calendar is refused",
          isinstance(err, IndexGuardError) and "calendar ended" in str(err) and not vout.exists(), repr(err))

    base = Path(tmp) / "city"
    fixtures = base / "fixtures"
    fixtures.mkdir(parents=True)
    gtfs_zip(fixtures / "a.zip", city_feed())
    gtfs_zip(fixtures / "b.zip", city_feed("7", end="20250131"))
    bci.CITY_FEEDS = [
        {"id": "a", "name": "A", "url": "https://a.example/a.zip", "file": "a.zip"},
        {"id": "b", "name": "B", "url": "https://b.example/b.zip", "file": "b.zip"},
    ]

    def copy_download(url: str, dest: Path, **_kw) -> dict:
        shutil.copy(fixtures / Path(url).name, dest)
        return {"size": dest.stat().st_size}

    bci.download_feed = copy_download
    cout = base / "city-index.json.gz"
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        info = bci.build_city_index(base, cout, today="20260301")
    check("city feed with an ended calendar is skipped with a warning",
          "b" in info["skipped"] and set(operator_trips(cout)) == {"a"} and "calendar of b ended 2025-01-31" in stderr.getvalue(),
          f"{info} {stderr.getvalue()!r}")
    write_index([part("a", 5), part("b", 5)], cout, with_operators=True, today="20260301")
    before = cout.read_bytes()
    err = raises(lambda: bci.build_city_index(base, cout, today="20260301"))
    check("an ended feed the previous index still runs is refused, and the message says why",
          isinstance(err, IndexGuardError) and "b calendar ended 2025-01-31" in str(err)
          and "runs until 2027-12-31" in str(err) and cout.read_bytes() == before, repr(err))
    os.environ[bci.ALLOW_MISSING_ENV] = "b"
    info = bci.build_city_index(base, cout, today="20260301")
    check("allowed, the ended feed is dropped and the rest is written",
          "b" in info["skipped"] and set(operator_trips(cout)) == {"a"}, repr(info))
    os.environ.pop(bci.ALLOW_MISSING_ENV, None)

    # Bahart after its last sailing: the zip and the previous index both
    # ended. Nothing that still runs is lost, so the other feeds go ahead.
    write_index([part("a", 5), part("b", 5, end="20250131")], cout, with_operators=True, today="20250101")
    check("operator_stats reads the last service day per operator",
          operator_stats(cout) == {"a": (5, "20271231"), "b": (5, "20250131")}, repr(operator_stats(cout)))
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        info = bci.build_city_index(base, cout, today="20260301")
    check("a feed whose calendar ended in the previous index too leaves without blocking the rebuild",
          set(operator_trips(cout)) == {"a"} and info["expired"] == {"b": "20250131"} and "b" in info["skipped"]
          and "calendar ended 2025-01-31 in the previous index" in stderr.getvalue(),
          f"{info} {stderr.getvalue()!r}")
    check("the next rebuild without the ended feed passes", raises(
        lambda: bci.build_city_index(base, cout, today="20260301")) is None)

    # The same zip downloaded anew (a failed download, not an ended calendar)
    # is not an expired feed.
    write_index([part("a", 5), part("b", 5, end="20250131")], cout, with_operators=True, today="20250101")
    (base / "b.zip").write_bytes(b"<html>gone</html>")
    with contextlib.redirect_stderr(io.StringIO()):
        info = bci.build_city_index(base, cout, today="20260301")
    check("a broken feed whose previous calendar ended is skipped, not expired",
          info["expired"] == {} and "b" in info["skipped"], repr(info))
bci.CITY_FEEDS = real_feeds

# Volán local agencies whose calendar ended in the previous index do not
# count towards the share of trips the rebuild must keep.
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "city-index.json.gz"
    write_index([part("volan-szeged", 60), part("volan-siofok", 200, end="20260930")], out, with_operators=True,
                today="20260901")
    check("ended Volán local agencies are not lost trips",
          raises(lambda: bci.guard_feeds(out, [part("volan-szeged", 60)], set(), today="20261019")) is None)
    err = raises(lambda: bci.guard_feeds(out, [part("volan-szeged", 60)], set(), today="20260915"))
    check("running Volán local agencies still are",
          isinstance(err, IndexGuardError) and "volan-local 60 trips vs 260" in str(err), repr(err))

# Year end: several big feeds' calendars end on 31 December together. The
# feeds still running are far fewer than the previous index's trips, but
# guard_feeds compares feed by feed, so write_index must not refuse on the
# total (it counted the ended trips and ignored ALLOW_MISSING).
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    fixtures = base / "fixtures"
    fixtures.mkdir()
    gtfs_zip(fixtures / "miskolc.zip", city_feed(trips=49))
    gtfs_zip(fixtures / "szeged.zip", city_feed("7", trips=3, end="20261231"))
    gtfs_zip(fixtures / "pecs.zip", city_feed("9", trips=3, end="20261231"))
    bci.CITY_FEEDS = [
        {"id": fid, "name": fid, "url": f"https://{fid}.example/{fid}.zip", "file": f"{fid}.zip"}
        for fid in ("miskolc", "szeged", "pecs")
    ]

    def year_end_download(url: str, dest: Path, **_kw) -> dict:
        shutil.copy(fixtures / Path(url).name, dest)
        return {"size": dest.stat().st_size}

    bci.download_feed = year_end_download
    cout = base / "city-index.json.gz"
    write_index([part("miskolc", 49), part("szeged", 100, end="20261231"), part("pecs", 107, end="20261231")],
                cout, with_operators=True, today="20261201")
    with contextlib.redirect_stderr(io.StringIO()):
        err = raises(lambda: bci.build_city_index(base, cout, today="20270102"))
    check("feeds whose calendars ended together at year end leave without freezing the city index",
          err is None and operator_trips(cout) == {"miskolc": 49}, repr(err))

    # A running feed lost on purpose (ALLOW_MISSING=all) is not refused on
    # the total either.
    write_index([part("miskolc", 49), part("szeged", 200)], cout, with_operators=True, today="20261201")
    (base / "szeged.zip").write_bytes(b"<html>moved</html>")
    os.environ[bci.ALLOW_MISSING_ENV] = "all"
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            err = raises(lambda: bci.build_city_index(base, cout, today="20261202"))
    finally:
        os.environ.pop(bci.ALLOW_MISSING_ENV, None)
    check("ALLOW_MISSING=all accepts a city index with far fewer trips in all",
          err is None and set(operator_trips(cout) or {}) == {"miskolc", "pecs"}, repr(err))
    write_index([part("miskolc", 49), part("szeged", 200)], cout, with_operators=True, today="20261201")
    with contextlib.redirect_stderr(io.StringIO()):
        err = raises(lambda: bci.build_city_index(base, cout, today="20261202"))
    check("without it the lost running feed is still refused, by name",
          isinstance(err, IndexGuardError) and "szeged missing (had 200 trips)" in str(err), repr(err))
bci.CITY_FEEDS = real_feeds

# The Volán index keeps its overall guard: one feed, so the total is the feed.
with tempfile.TemporaryDirectory() as tmp:
    vout = Path(tmp) / "volan-index.json.gz"
    write_index([part("volan", 100)], vout, with_operators=False)
    err = raises(lambda: write_index([part("volan", 40)], vout, with_operators=False))
    check("an index losing most of its trips is still refused by default",
          isinstance(err, IndexGuardError) and "40 running trips vs previous 100" in str(err), repr(err))


def seasonal(op_id: str, summer: int, autumn: int) -> dict:
    """A feed with a summer service (ended 2026-08-31) and an autumn one."""
    p = part(op_id, 0)
    p["c"] = [["1111111", "20260615", "20260831"], ["1111111", "20260901", "20261212"]]
    p["a"], p["r"] = [[], []], [[], []]
    p["t"] = [[0, "1", [0, 1], [480, 490], 3, -1]] * summer + [[1, "1", [0, 1], [480, 490], 3, -1]] * autumn
    if not summer:
        p["c"], p["a"], p["r"] = p["c"][1:], [[]], [[]]
        p["t"] = [[0, "1", [0, 1], [480, 490], 3, -1]] * autumn
    return p


# A feed whose summer service ended: its summer trips are not lost by a
# rebuild that has every trip still running, per feed (city) and in total
# (Volán); losing most of the running ones is still refused.
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / "city-index.json.gz"
    write_index([seasonal("szeged", 300, 200)], out, with_operators=True, today="20260820", guard_total=False)
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 0, 200)], set(), today="20261021"))
    check("city guard counts only trips whose own service still runs", err is None, repr(err))
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 0, 90)], set(), today="20261021"))
    check("losing most of the running trips is still refused",
          isinstance(err, IndexGuardError) and "szeged 90 trips vs 200" in str(err), repr(err))
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 300, 90)], set(), today="20261021"))
    check("ended trips in the new feed do not make up for lost running ones",
          isinstance(err, IndexGuardError) and "szeged 90 trips vs 200" in str(err), repr(err))
    vout = Path(tmp) / "volan-index.json.gz"
    write_index([seasonal("volan", 300, 200)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 200)], vout, with_operators=False, today="20261021"))
    check("Volán total counts only trips whose own service still runs", err is None, repr(err))
    write_index([seasonal("volan", 300, 200)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 90)], vout, with_operators=False, today="20261021"))
    check("Volán total still refuses losing most of the running trips",
          isinstance(err, IndexGuardError) and "90 running trips vs previous 200" in str(err), repr(err))

# ---- 1. Volán index: too few trips is a broken feed -----------------------

with tempfile.TemporaryDirectory() as tmp:
    zpath = gtfs_zip(Path(tmp) / "volan.zip", city_feed("1001", trips=3))
    out = Path(tmp) / "volan-index.json.gz"
    err = raises(lambda: build_index(zpath, out))
    check("Volán index far below the expected trips is refused",
          isinstance(err, IndexGuardError) and not out.exists(), repr(err))
    empty = gtfs_zip(Path(tmp) / "empty.zip", city_feed("12"))
    err = raises(lambda: build_index(empty, out, min_trips=1))
    check("Volán index with 0 trips is refused", isinstance(err, IndexGuardError), repr(err))
    info = build_index(zpath, out, min_trips=3)
    check("Volán index at the floor is written", info["trips"] == 3 and out.exists(), repr(info))

# ---- 5. MÁV: MAV_GTFS_URL refresh, audit before write ----------------------

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    dest = base / "mav-gtfs.zip"
    served: dict[str, object] = {"body": b"PK feed-1", "calls": 0}

    def fake_mav_download(url: str, path: Path, **_kw) -> dict:
        served["calls"] = int(served["calls"]) + 1
        if served["body"] is None:
            raise OSError("down")
        path.write_bytes(served["body"])  # type: ignore[arg-type]
        return {"size": path.stat().st_size}

    bmi.download_feed = fake_mav_download
    bmi.PFAEDLE_DIRS = ()
    for key in ("MAV_PFAEDLE_DIR", "MAV_GTFS_ZIP"):
        os.environ.pop(key, None)
    os.environ["MAV_GTFS_URL"] = "https://mav.example/gtfs.zip"

    def make_old(path: Path) -> None:
        t = time.time() - bmi.URL_MAX_AGE_S - 60
        os.utime(path, (t, t))

    got = bmi.resolve_mav_feed(base)
    check("MAV_GTFS_URL is downloaded when no zip is there", got == dest and dest.read_bytes() == b"PK feed-1")
    bmi.resolve_mav_feed(base)
    check("a fresh download is not fetched again", served["calls"] == 1, str(served["calls"]))

    make_old(dest)
    served["body"] = b"PK feed-2"
    bmi.resolve_mav_feed(base)
    check("an existing mav-gtfs.zip does not shadow MAV_GTFS_URL",
          dest.read_bytes() == b"PK feed-2" and served["calls"] == 2, str(served["calls"]))

    make_old(dest)
    mtime = dest.stat().st_mtime
    bmi.resolve_mav_feed(base)
    check("an unchanged feed keeps its file, so the index is not rebuilt",
          dest.stat().st_mtime == mtime and served["calls"] == 3)

    served["body"] = None
    url_errors: list[BaseException] = []
    bmi.resolve_mav_feed(base, url_errors=url_errors)
    check("a failed download keeps the previous zip", dest.read_bytes() == b"PK feed-2")
    check("and says so to the caller, who counts the failing days",
          len(url_errors) == 1 and isinstance(url_errors[0], OSError), repr(url_errors))
    url_errors.clear()
    bmi.resolve_mav_feed(base, url_errors=url_errors)
    check("the kept zip keeps its old mtime, so the next run tries again",
          served["calls"] == 5 and len(url_errors) == 1, f"{served['calls']} {url_errors!r}")
    check("no temp zip is left behind", sorted(p.name for p in base.iterdir()) == ["mav-gtfs.zip"],
          repr(sorted(p.name for p in base.iterdir())))
    dest.unlink()
    check("a failed first download raises",
          raises(lambda: bmi.resolve_mav_feed(base), OSError) is not None)
    os.environ.pop("MAV_GTFS_URL", None)

with tempfile.TemporaryDirectory() as tmp:
    feed = Path(tmp) / "mav"
    feed.mkdir()
    routes = ["route_id,route_short_name,route_long_name,route_type"]
    trips = ["route_id,service_id,trip_id"]
    stop_times = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    stops = ["stop_id,stop_name,stop_lat,stop_lon"]
    for i in range(61):
        stops.append(f"{i},Station {i},{47 + i * 0.01:.4f},{19 + i * 0.01:.4f}")
    for n in range(60):
        routes.append(f"R{n},,IC {800 + n} TEST,2")
        trips.append(f"R{n},S1,T{n}")
        stop_times.append(f"T{n},07:00:00,07:00:00,{n},1")
        stop_times.append(f"T{n},07:30:00,07:30:00,{n + 1},2")
    (feed / "agency.txt").write_text("agency_id,agency_name\nMAV,MÁV\n")
    (feed / "routes.txt").write_text("\n".join(routes) + "\n")
    (feed / "trips.txt").write_text("\n".join(trips) + "\n")
    (feed / "stops.txt").write_text("\n".join(stops) + "\n")
    (feed / "stop_times.txt").write_text("\n".join(stop_times) + "\n")
    (feed / "feed_info.txt").write_text("feed_publisher_name,feed_version\nMÁV,test\n")
    out = Path(tmp) / "mav-index.json.gz"
    out.write_bytes(b"previous")

    mav = bmi._mav_module()
    real_audit = mav.audit
    mav.audit = lambda _path, _index: {
        "trips": 60, "trips_bad": 30, "trains": 60, "trains_ok": 30, "ok_ratio": 0.5,
        "tolerance_m": 300, "worst": [{"train": "800"}],
    }
    try:
        err = raises(lambda: bmi.build_mav_index(feed, out))
    finally:
        mav.audit = real_audit
    check("MÁV index failing its audit is refused", isinstance(err, RuntimeError) and "audit" in str(err), repr(err))
    check("refused MÁV index leaves the previous one", out.read_bytes() == b"previous")
    stats = bmi.build_mav_index(feed, out)
    check("MÁV index passing its audit is written",
          stats["audit"]["ok_ratio"] >= bmi.MIN_OK and json.loads(gzip.decompress(out.read_bytes()))["feed"] == "test",
          repr(stats.get("audit")))

# ---- 6. GTFS members are streamed, not read whole --------------------------


class OpenOnly:
    """A GTFS source without read(): _open_csv must stream."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def namelist(self) -> list[str]:
        return list(self.files)

    def open(self, name: str):
        return io.BytesIO(self.files[name])

    def read(self, name: str) -> bytes:
        raise AssertionError(f"{name} read whole")


src = OpenOnly({
    "stops.txt": "﻿ stop_id , stop_name ,stop_lat\n1,Győr,47.6\n2,Pécs\n".encode("utf-8"),
    "legacy.txt": "stop_id,stop_name\n1,".encode() + "Hősök tere".encode("cp1250") + b"\n",
    "mixed.txt": "stop_id,stop_name\n1,Szőny\n2,".encode() + "Érd".encode("cp1250") + b"\n",
})
rows = list(_open_csv(src, "stops.txt"))
check("BOM and header whitespace stripped", rows and set(rows[0]) == {"stop_id", "stop_name", "stop_lat"}, repr(rows))
check("short row gets empty strings", rows[1].get("stop_lat") == "" and rows[1]["stop_name"] == "Pécs", repr(rows))
check("cp1250 member decoded", next(_open_csv(src, "legacy.txt"))["stop_name"] == "Hősök tere")
check("stray cp1250 bytes in a UTF-8 member decoded",
      [r["stop_name"] for r in _open_csv(src, "mixed.txt")] == ["Szőny", "Érd"])
check("missing member raises before iteration", raises(lambda: _open_csv(src, "nope.txt"), FileNotFoundError) is not None)

with tempfile.TemporaryDirectory() as tmp:
    big = "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n" + "".join(
        f"T{i // 20},08:00:00,08:00:00,{i % 997},{i % 20}\n" for i in range(300_000)
    )
    zpath = gtfs_zip(Path(tmp) / "big.zip", {"stop_times.txt": big})
    with zipfile.ZipFile(zpath) as z:
        tracemalloc.start()
        count = sum(1 for _ in _open_csv(z, "stop_times.txt"))
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    check("stop_times streamed in bounded memory",
          count == 300_000 and peak < len(big) / 4, f"rows={count} peak={peak} member={len(big)}")

if failed:
    sys.exit(1)
print("index guard tests passed")
