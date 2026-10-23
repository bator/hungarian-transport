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
import gtfs_compact as gc  # noqa: E402
import index_digest  # noqa: E402
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

    # A new zip next to the one in place (update_volan_index stages it): one
    # that does not load is rejected and the feed keeps the zip in place.
    shutil.copy(fixtures / "b.zip", base / "b.zip")
    bci.build_city_index(base, out)
    staged = base / "b.zip.tmp"
    staged.write_bytes(b"<html>gone</html>")
    err = raises(lambda: bci.build_city_index(base, out, zips={"b": staged}))
    info = bci.build_city_index(base, out, zips={"b": staged})
    check("a new zip that fails to load is rejected, the zip in place is used",
          err is None and info["rejected"] == ["b"] and "b" not in info["skipped"]
          and set(operator_trips(out)) == {"a", "b"}, repr((err, info)))
    # A zip that does not load may be a download gone wrong: not a content
    # rejection, so the updater keeps no fingerprint and fetches it again.
    check("a new zip that fails to load is not rejected for its content", info["refused"] == {}, repr(info))
    gtfs_zip(staged, city_feed("7", trips=0))
    info = bci.build_city_index(base, out, zips={"b": staged})
    check("a new zip without trips is rejected for its content, the zip in place is used",
          info["rejected"] == ["b"] and info["refused"] == {"b": {"why": "no trips", "until": ""}}
          and set(operator_trips(out)) == {"a", "b"}, repr(info))
    shutil.copy(fixtures / "b.zip", staged)
    info = bci.build_city_index(base, out, zips={"b": staged})
    check("a new zip that loads is used", info["rejected"] == [] and set(operator_trips(out)) == {"a", "b"}, repr(info))
    (base / "b.zip").unlink()
    staged.write_bytes(b"<html>gone</html>")
    err = raises(lambda: bci.build_city_index(base, out, zips={"b": staged}))
    check("a rejected new zip with none in place leaves the feed out (and the guard refuses)",
          isinstance(err, IndexGuardError) and "b missing" in str(err), repr(err))
    shutil.copy(fixtures / "b.zip", base / "b.zip")
    staged.unlink()

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

# A new zip that loads but that the guard would refuse (its calendar already
# ended, most of its trips gone) is rejected for the zip in place, which
# passes; the other feeds' updates go through. HUNGARIAN_TRANSPORT_ALLOW_MISSING
# naming the feed lets it replace the zip in place: the reduction is meant.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    bci.CITY_FEEDS = [
        {"id": "a", "name": "A", "url": "https://a.example/a.zip", "file": "a.zip"},
        {"id": "b", "name": "B", "url": "https://b.example/b.zip", "file": "b.zip"},
    ]
    today = "20261022"
    gtfs_zip(base / "a.zip", city_feed(trips=60))
    gtfs_zip(base / "b.zip", city_feed("7", trips=60))
    out = base / "city-index.json.gz"
    bci.build_city_index(base, out, today=today)
    staged_a, staged_b = base / "a.zip.tmp", base / "b.zip.tmp"
    gtfs_zip(staged_b, city_feed("7", trips=70))
    for label, bad in (("whose calendar already ended", city_feed(trips=60, end="20260930")),
                       ("with most of its trips gone", city_feed(trips=10))):
        gtfs_zip(staged_a, bad)
        err, info = None, {}
        try:
            info = bci.build_city_index(base, out, today=today, zips={"a": staged_a, "b": staged_b})
        except Exception as e:
            err = e
        check(f"a new zip {label} is rejected, the zip in place is used and the other feed updates",
              err is None and info["rejected"] == ["a"] and info["skipped"] == ["volan-local"]
              and operator_trips(out) == {"a": 60, "b": 70}, repr((err, info)))
        # The reason goes with it, and the last day of the zip kept: once
        # that one ends, the updater offers the new zip to the guard again.
        check(f"a new zip {label} is rejected for its content, until the zip in place ends",
              set(info.get("refused") or {}) == {"a"} and info["refused"]["a"]["until"] == "20271231"
              and ("calendar ended" in info["refused"]["a"]["why"] or "10 trips vs 60" in info["refused"]["a"]["why"]),
              repr(info.get("refused")))
    first = index_digest.digest(out)
    first_bytes = out.read_bytes()
    bci.build_city_index(base, out, today=today, zips={"a": staged_a, "b": staged_b})
    check("the rebuild with a rejected zip is deterministic",
          index_digest.digest(out) == first and out.read_bytes() == first_bytes)

    # _previous() reads the whole previous index (about a second on the
    # release one): once per build, for the per-feed and the index guard.
    reads: list[str] = []
    real_previous = bci._previous

    def counted_previous(*args, **kwargs):
        reads.append("previous")
        return real_previous(*args, **kwargs)

    bci._previous = counted_previous
    try:
        bci.build_city_index(base, out, today=today, zips={"a": staged_a, "b": staged_b})
    finally:
        bci._previous = real_previous
    check("a build reads the previous index once", reads == ["previous"], repr(reads))

    os.environ[bci.ALLOW_MISSING_ENV] = "b"
    info = bci.build_city_index(base, out, today=today, zips={"a": staged_a, "b": staged_b})
    check("HUNGARIAN_TRANSPORT_ALLOW_MISSING naming another feed does not let the zip in",
          info["rejected"] == ["a"] and operator_trips(out) == {"a": 60, "b": 70}, repr(info))
    for allow in ("a", "all"):
        os.environ[bci.ALLOW_MISSING_ENV] = allow
        bci.build_city_index(base, out, today=today)
        err, info = None, {}
        try:
            info = bci.build_city_index(base, out, today=today, zips={"a": staged_a, "b": staged_b})
        except Exception as e:
            err = e
        check(f"HUNGARIAN_TRANSPORT_ALLOW_MISSING={allow} lets an intended reduction replace the zip in place",
              err is None and info["rejected"] == [] and info["refused"] == {}
              and operator_trips(out) == {"a": 10, "b": 70}, repr((err, info)))
        os.environ.pop(bci.ALLOW_MISSING_ENV, None)
        info = bci.build_city_index(base, out, today=today)
        check(f"(ALLOW_MISSING={allow}) the zip in place is back for the next check",
              operator_trips(out) == {"a": 60, "b": 60}, repr(operator_trips(out)))

    gtfs_zip(staged_a, city_feed(trips=58))
    info = bci.build_city_index(base, out, today=today, zips={"a": staged_a})
    check("a new zip the guard passes is used", info["rejected"] == [] and operator_trips(out)["a"] == 58, repr(info))

    # The zip in place fails the guard as well (it ended too): the newer zip
    # stays and the guard refuses, as before.
    gtfs_zip(base / "a.zip", city_feed(trips=60, end="20261001"))
    gtfs_zip(staged_a, city_feed(trips=10))
    err = raises(lambda: bci.build_city_index(base, out, today=today, zips={"a": staged_a}))
    check("when the zip in place fails too, the new zip is not rejected and the guard decides",
          isinstance(err, IndexGuardError) and "a 10 trips vs 58" in str(err), repr(err))
bci.CITY_FEEDS = real_feeds

# A smaller new timetable rejected while the one in place still runs is
# taken once that one ended: its rejection names that day.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    bci.CITY_FEEDS = [
        {"id": "a", "name": "A", "url": "https://a.example/a.zip", "file": "a.zip"},
        {"id": "b", "name": "B", "url": "https://b.example/b.zip", "file": "b.zip"},
    ]
    gtfs_zip(base / "a.zip", city_feed(trips=60, end="20261031"))
    gtfs_zip(base / "b.zip", city_feed("7", trips=60))
    out = base / "city-index.json.gz"
    bci.build_city_index(base, out, today="20261022")
    staged_a = gtfs_zip(base / "a.zip.tmp", city_feed(trips=20))
    info = bci.build_city_index(base, out, today="20261022", zips={"a": staged_a})
    check("a smaller timetable is rejected while the one in place runs, until its last day",
          info["refused"].get("a", {}).get("until") == "20261031" and operator_trips(out) == {"a": 60, "b": 60},
          repr(info))
    info = bci.build_city_index(base, out, today="20261101", zips={"a": staged_a})
    check("the day after, the same zip is taken", info["rejected"] == [] and operator_trips(out) == {"a": 20, "b": 60},
          repr(info))
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
    # The previous index's calendar has ended (nothing runs): a truncated
    # feed must not pass because there is nothing running to compare with.
    write_index([seasonal("volan", 300, 0)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 40)], vout, with_operators=False, today="20261021"))
    check("an ended previous Volán index is compared on all its trips",
          isinstance(err, IndexGuardError) and "40 trips vs previous 300" in str(err), repr(err))
    err = raises(lambda: write_index([seasonal("volan", 0, 200)], vout, with_operators=False, today="20261021"))
    check("a full new Volán feed after an ended one is written", err is None, repr(err))
    # Only a few of the previous trips still run: a feed that really shrank
    # is refused. At least GUARD_MIN_TRIPS running: the rebuild has to keep
    # nearly all of them; fewer: all trips are the yardstick.
    write_index([seasonal("volan", 100000, 60)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 30)], vout, with_operators=False, today="20261021"))
    check("Volán: 60 of 100060 running, new 30 is refused",
          isinstance(err, IndexGuardError) and "30 running trips vs previous 60" in str(err), repr(err))
    write_index([seasonal("volan", 100000, 49)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 30)], vout, with_operators=False, today="20261021"))
    check("Volán: 49 of 100049 running, new 30 is refused on all trips",
          isinstance(err, IndexGuardError) and "30 trips vs previous 100049" in str(err), repr(err))
    vout.unlink()
    write_index([seasonal("volan", 1000, 400)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 400)], vout, with_operators=False, today="20261021"))
    check("Volán: running trips a fair share (400 of 1400) are the yardstick", err is None, repr(err))
    out = Path(tmp) / "city-index.json.gz"
    write_index([seasonal("szeged", 1960, 40)], out, with_operators=True, today="20260820", guard_total=False)
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 0, 5)], set(), today="20261021"))
    check("city: 40 of 2000 running, new 5 is refused on all trips",
          isinstance(err, IndexGuardError) and "szeged 5 trips vs 2000" in str(err), repr(err))
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 1960, 40)], set(), today="20261021"))
    check("city: the same nearly-ended feed again is accepted", err is None, repr(err))
    # The summer service was most of the feed (900 ended, 100 running): a
    # rebuild with the 100 autumn trips is the same feed, not a lost one.
    write_index([seasonal("szeged", 900, 100)], out, with_operators=True, today="20260820", guard_total=False)
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 0, 100)], set(), today="20261021"))
    check("city: 100 of 1000 running, new 100 is accepted", err is None, repr(err))
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 0, 2000)], set(), today="20261021"))
    check("city: 100 of 1000 running, a new larger period is accepted", err is None, repr(err))
    err = raises(lambda: bci.guard_feeds(out, [seasonal("szeged", 0, 60)], set(), today="20261021"))
    check("city: 100 of 1000 running, new 60 is refused",
          isinstance(err, IndexGuardError) and "szeged 60 trips vs 100" in str(err), repr(err))
    vout.unlink()
    write_index([seasonal("volan", 900, 100)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 100)], vout, with_operators=False, today="20261021"))
    check("Volán: 100 of 1000 running, new 100 is accepted", err is None, repr(err))
    write_index([seasonal("volan", 900, 100)], vout, with_operators=False, today="20260820")
    err = raises(lambda: write_index([seasonal("volan", 0, 60)], vout, with_operators=False, today="20261021"))
    check("Volán: 100 of 1000 running, new 60 is refused",
          isinstance(err, IndexGuardError) and "60 running trips vs previous 100" in str(err), repr(err))
    check("guard_basis: rule table",
          [gc.guard_basis(r, t, 0.5) for r, t in ((49, 100049), (60, 100060), (100, 1000), (400, 1400), (200, 500))]
          == [(False, 0.5), (True, 0.9), (True, 0.9), (True, 0.5), (True, 0.5)])
    vout.write_bytes(gzip.compress(b"[1, 2"))
    check("a truncated previous index is no reference", gc.existing_trips(vout) is None)
    vout.write_bytes(gzip.compress(b"\xff\xfe not utf-8"))
    check("a non-UTF-8 previous index is no reference", gc.existing_trips(vout) is None)

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

    # The weekly release compares MÁV indexes by content (index_digest).
    first = index_digest.digest(out)
    time.sleep(1.1)  # gzip stamps the build time into the file
    again = Path(tmp) / "again.json.gz"
    bmi.build_mav_index(feed, again)
    check("a MÁV rebuild of the same feed has the same digest",
          index_digest.digest(again) == first, f"{index_digest.digest(again)} {first}")
    idx = json.loads(gzip.decompress(out.read_bytes()))
    # The same content with the station and polyline tables in another order.
    order = list(range(len(idx["st"])))[::-1]
    pos = {old: new for new, old in enumerate(order)}
    shuffled = dict(idx, st=[idx["st"][i] for i in order],
                    n={k: [[v[0], [pos[i] for i in v[1]]] for v in vs] for k, vs in idx["n"].items()})
    (Path(tmp) / "shuffled.json.gz").write_bytes(gzip.compress(json.dumps(shuffled).encode(), mtime=0))
    check("the MÁV digest ignores table order", index_digest.digest(Path(tmp) / "shuffled.json.gz") == first)
    moved = dict(idx, st=[[n, la + 1, lo] for n, la, lo in idx["st"]])
    (Path(tmp) / "moved.json.gz").write_bytes(gzip.compress(json.dumps(moved).encode(), mtime=0))
    check("a moved MÁV station changes the digest", index_digest.digest(Path(tmp) / "moved.json.gz") != first)
    swapped = dict(idx, n={k: vs[::-1] for k, vs in idx["n"].items()})
    swapped["n"]["800"] = [idx["n"]["800"][0], idx["n"]["801"][0]]
    (Path(tmp) / "swapped.json.gz").write_bytes(gzip.compress(json.dumps(swapped).encode(), mtime=0))
    check("a changed MÁV variant list changes the digest", index_digest.digest(Path(tmp) / "swapped.json.gz") != first)

# ---- 5b. MÁV credentials: HTTP Basic to the feed's host only --------------

import base64  # noqa: E402
import http.client  # noqa: E402
import socket  # noqa: E402
import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402
import urllib.response  # noqa: E402

import build_volan_index as bvi  # noqa: E402
import update_volan_index as uvi  # noqa: E402

# Not a developer's .env: the tests set what they use.
gc._ENV_LOADED = True
for key in ("MAV_GTFS_USER", "MAV_GTFS_PASSWORD", "MAV_GTFS_URL", "VOLAN_GTFS_URL"):
    os.environ.pop(key, None)
USER, PASSWORD = "rail-user", "pa ss:wörd\"1"
EXPECTED_AUTH = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()

tiny = io.BytesIO()
with zipfile.ZipFile(tiny, "w") as z:
    z.writestr("agency.txt", "agency_id\n")
TINY_ZIP = tiny.getvalue()
PUBLIC_IPS = {"mav.example": "93.184.216.34", "cdn.example": "93.184.216.35"}
real_gai = gc.socket.getaddrinfo


def fake_gai(host, port, *args, **kwargs):
    if host in PUBLIC_IPS:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IPS[host], port))]
    raise socket.gaierror(-2, "Name or service not known")


def response(url: str, code: int, headers: str, body: bytes = b""):
    msg = http.client.parse_headers(io.BytesIO(headers.encode("latin-1") + b"\r\n"))
    resp = urllib.response.addinfourl(io.BytesIO(body), msg, url, code)
    resp.msg = {200: "OK", 302: "Found", 401: "Unauthorized"}[code]
    return resp


class FakeHTTPS(urllib.request.HTTPSHandler):
    """The server side, in place of a socket: mav.example answers ROUTES,
    every request (with the Authorization it carried) goes to SEEN."""

    SEEN: list[tuple[str, str | None]] = []
    ROUTES: dict[str, tuple[int, str]] = {}

    def https_open(self, req):
        FakeHTTPS.SEEN.append((req.full_url, req.get_header("Authorization")))
        code, extra = FakeHTTPS.ROUTES.get(req.full_url, (200, ""))
        if code == 200:
            return response(req.full_url, 200, f"Content-Length: {len(TINY_ZIP)}\r\n", TINY_ZIP)
        if code == 302:
            return response(req.full_url, 302, f"Location: {extra}\r\nContent-Length: 0\r\n")
        return response(req.full_url, code, "Content-Length: 0\r\nWWW-Authenticate: Basic realm=\"GTFS\"\r\n")


real_https_handler = gc._PublicHTTPSHandler
gc.socket.getaddrinfo = fake_gai
gc._PublicHTTPSHandler = FakeHTTPS
try:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "mav.zip"
        FakeHTTPS.SEEN.clear()
        FakeHTTPS.ROUTES = {}
        gc.download_zip("https://mav.example/gtfs.zip", dest, auth=(USER, PASSWORD))
        check("credentials go to the feed's host as HTTP Basic",
              FakeHTTPS.SEEN == [("https://mav.example/gtfs.zip", EXPECTED_AUTH)] and dest.read_bytes() == TINY_ZIP,
              repr(FakeHTTPS.SEEN))

        FakeHTTPS.SEEN.clear()
        FakeHTTPS.ROUTES = {"https://mav.example/gtfs.zip": (302, "https://cdn.example/gtfs.zip")}
        dest.unlink()
        gc.download_zip("https://mav.example/gtfs.zip", dest, auth=(USER, PASSWORD))
        check("the Authorization header is not sent after a cross-host redirect",
              [u for u, _a in FakeHTTPS.SEEN] == ["https://mav.example/gtfs.zip", "https://cdn.example/gtfs.zip"]
              and FakeHTTPS.SEEN[0][1] == EXPECTED_AUTH and FakeHTTPS.SEEN[1][1] is None and dest.is_file(),
              repr(FakeHTTPS.SEEN))

        FakeHTTPS.SEEN.clear()
        FakeHTTPS.ROUTES = {"https://mav.example/gtfs.zip": (401, "")}
        err = raises(lambda: gc.download_zip("https://mav.example/gtfs.zip", Path(tmp) / "x.zip", auth=(USER, "wrong")))
        text = f"{err} {err!r} " + uvi.error_name(err)
        check("a refused login raises HTTP 401 without the credentials",
              isinstance(err, urllib.error.HTTPError) and err.code == 401 and USER not in text
              and "wrong" not in text and "Basic" not in text and not (Path(tmp) / "x.zip").exists(), text)

        FakeHTTPS.SEEN.clear()
        err = raises(lambda: gc.download_zip("http://mav.example/gtfs.zip", Path(tmp) / "x.zip",
                                             allow_insecure=True, auth=(USER, PASSWORD)), ValueError)
        check("credentials never go over plain http, even for an insecure feed",
              err is not None and not FakeHTTPS.SEEN and PASSWORD not in str(err), repr(err))
        FakeHTTPS.ROUTES = {}
        gc.download_feed("http://mav.example/gtfs.zip", dest, allow_insecure=True, auth=(USER, PASSWORD))
        check("an http:// feed URL with credentials is asked over https only",
              [u for u, _a in FakeHTTPS.SEEN] == ["https://mav.example/gtfs.zip"], repr(FakeHTTPS.SEEN))
        for bad in [("a:b", "pw-x1"), ("", "pw-x2"), ("uname-x3", ""), ("uname-x4", "line\nbreak")]:
            err = raises(lambda bad=bad: gc.basic_auth(bad), ValueError)
            check(f"unusable credentials refused without naming them ({bad[0]!r})",
                  err is not None and (not bad[1] or bad[1] not in str(err)) and (not bad[0] or bad[0] not in str(err)),
                  repr(err))
        check("userinfo in a feed URL is still refused",
              raises(lambda: gc.assert_public_http_url(f"https://{USER}:pw@mav.example/gtfs.zip"), ValueError) is not None)
        quoted = urllib.parse.quote(PASSWORD, safe="")
        line = f"urlopen failed: Authorization: {EXPECTED_AUTH} for https://{USER}:{quoted}@mav.example/x"
        check("redaction covers an Authorization header and URL credentials",
              EXPECTED_AUTH.split()[1] not in uvi.redact(line) and quoted not in uvi.redact(line)
              and USER not in uvi.redact(line), uvi.redact(line))
finally:
    gc.socket.getaddrinfo = real_gai
    gc._PublicHTTPSHandler = real_https_handler

# MAV_GTFS_USER / MAV_GTFS_PASSWORD reach the download of MAV_GTFS_URL.
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    calls: list[dict] = []

    def capturing_download(url: str, path: Path, **kw) -> dict:
        calls.append({"url": url, **kw})
        if kw.get("auth") and kw["auth"][1] != PASSWORD:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", http.client.HTTPMessage(), None)
        path.write_bytes(b"PK feed")
        return {"size": 7}

    bmi.download_feed = capturing_download
    os.environ["MAV_GTFS_URL"] = "https://mav.example/gtfs.zip"
    bmi.resolve_mav_feed(base)
    check("without credentials MAV_GTFS_URL is fetched anonymously", calls and "auth" not in calls[-1], repr(calls))
    (base / "mav-gtfs.zip").unlink()
    os.environ["MAV_GTFS_USER"], os.environ["MAV_GTFS_PASSWORD"] = USER, PASSWORD
    bmi.resolve_mav_feed(base)
    check("MAV_GTFS_USER / MAV_GTFS_PASSWORD are passed as auth",
          calls[-1].get("auth") == (USER, PASSWORD) and calls[-1]["url"] == "https://mav.example/gtfs.zip", repr(calls[-1]))

    # A refused login keeps the previous zip and says so without the secret.
    t = time.time() - bmi.URL_MAX_AGE_S - 60
    os.utime(base / "mav-gtfs.zip", (t, t))
    os.environ["MAV_GTFS_PASSWORD"] = "wrong-secret"
    out, err_out, url_errors = io.StringIO(), io.StringIO(), []
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err_out):
        bmi.resolve_mav_feed(base, url_errors=url_errors)
    logged = out.getvalue() + err_out.getvalue() + " ".join(f"{e} {e!r}" for e in url_errors)
    check("a refused login is reported as HTTP 401, the zip kept, no credential logged",
          len(url_errors) == 1 and uvi.error_name(url_errors[0]) == "HTTP 401"
          and (base / "mav-gtfs.zip").read_bytes() == b"PK feed"
          and "wrong-secret" not in logged and USER not in logged, logged)

    os.environ.pop("MAV_GTFS_PASSWORD")
    url_errors = []
    bmi.resolve_mav_feed(base, url_errors=url_errors)
    check("only one of the two set is an error (the old zip stays), not an anonymous download",
          len(url_errors) == 1 and isinstance(url_errors[0], ValueError) and USER not in str(url_errors[0])
          and "auth" in calls[-1], repr((url_errors, calls[-1:])))
    (base / "mav-gtfs.zip").unlink()
    check("only one of the two set without an old zip raises",
          raises(lambda: bmi.resolve_mav_feed(base), ValueError) is not None)
    for key in ("MAV_GTFS_USER", "MAV_GTFS_PASSWORD", "MAV_GTFS_URL"):
        os.environ.pop(key, None)

# ---- 5c. Volán feed URLs: opendata.utas.hu, then KTI ------------------------

check("the Volán feed comes from opendata.utas.hu first, KTI second",
      bvi.volan_urls() == ["https://opendata.utas.hu/public-gtfs/volanbusz_gtfs.zip",
                           "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"], repr(bvi.volan_urls()))
os.environ["VOLAN_GTFS_URL"] = "https://mirror.example/volan.zip"
check("VOLAN_GTFS_URL replaces the first, the KTI fallback stays",
      bvi.volan_urls() == ["https://mirror.example/volan.zip", "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"])
os.environ["VOLAN_GTFS_URL"] = bvi.FALLBACK_URL
check("an override equal to the fallback is tried once", bvi.volan_urls() == [bvi.FALLBACK_URL])
os.environ.pop("VOLAN_GTFS_URL")
with tempfile.TemporaryDirectory() as tmp:
    tried: list[str] = []

    def flaky_download(url: str, path: Path, **_kw) -> dict:
        tried.append(url)
        if "opendata" in url:
            raise OSError("down")
        path.write_bytes(b"PK volan")
        return {"size": 8}

    bvi.download_feed = flaky_download
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        used = bvi.download_volan(Path(tmp) / "volanbusz_gtfs.zip")
    check("a failing primary falls back to KTI", used == bvi.FALLBACK_URL and tried == bvi.volan_urls(), repr(tried))
    bvi.download_feed = lambda *_a, **_k: (_ for _ in ()).throw(OSError("all down"))
    check("both failing raises the last error",
          raises(lambda: bvi.download_volan(Path(tmp) / "v.zip"), OSError) is not None)
    check("the city builder downloads through the same list", bci.download_volan is bvi.download_volan)

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
