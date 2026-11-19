#!/usr/bin/env python3
"""The Volán and city indexes on GitHub Pages: scripts/pages_timetables.py.
When the weekly indexes are due (the published manifest's age, schema and
contents, the dispatch input), the builders' baseline (the published
indexes checked against their manifest, else the release's zip), a part
of what a run built, and the assembled site: timetables.json against its
files (sizes, sha256, the head's feed and shapes), carried files byte for
byte (the MÁV files, an index not built in this run), refusals when a
listed file cannot be carried, the size limit, one more fetch for a file
Pages serves a moment late. Fixture indexes come from gtfs_compact's own
writer; the live site is a local folder or a stubbed urlopen. No network."""
from __future__ import annotations

import argparse
import base64
import contextlib
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gtfs_compact  # noqa: E402
import mav_pages_site  # noqa: E402
import pages_timetables as pt  # noqa: E402
import sign_manifest as sm  # noqa: E402
from gtfs_feed import encode_polyline  # noqa: E402

os.environ.pop("MAV_ALLOW_SHRINK", None)
NOW = datetime(2026, 11, 16, 10, 40, tzinfo=timezone.utc)
# The index schema passed as --schema (the stable release's, in the workflow).
SCHEMA = gtfs_compact.INDEX_SCHEMA
S = str(SCHEMA)
COMMIT = "c" * 40
# The published manifest is trusted only with a signature by the pinned key
# (pages_timetables Source.timetables). The tests' live sites are signed
# with a key made here, which this process and every pages_timetables.py
# run of the tests (run(), through TRUST_WRAP) take as the pinned one; the
# script itself never takes another key than its constant.
REAL_PUBLIC_KEY = sm.PUBLIC_KEY
TEST_SEED = os.urandom(32)
TEST_PUB = sm.public_key(TEST_SEED)
sm.PUBLIC_KEY = TEST_PUB
# A signature that does not verify is read again SIG_SETTLE_S (11 minutes)
# after the first read in the workflow (Pages' cache); in the tests' runs at
# once (TEST_SIG_SETTLE, 0), unless a test asks otherwise.
TRUST_WRAP = ("import os, sys; sys.path.insert(0, sys.argv.pop(1)); import sign_manifest;"
              " sign_manifest.PUBLIC_KEY = bytes.fromhex(os.environ['TEST_PAGES_PUBLIC_KEY']);"
              " import pages_timetables; pages_timetables.SIG_SETTLE_S = float(os.environ['TEST_SIG_SETTLE']);"
              " raise SystemExit(pages_timetables.main(sys.argv[1:]))")
REAL_SIG_SETTLE = pt.SIG_SETTLE_S
pt.SIG_SETTLE_S = 0.0

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        failed += 1


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def part(op: str, feed: str, trips: int = 6, bend: float = 0.0) -> dict:
    """An operator part with shapes, as gtfs_compact.load_parts returns one."""
    shapes = [encode_polyline([(47.1, 19.1), (47.2, 19.2 + k / 10 + bend)]) for k in range(3)]
    return {"op_id": op, "op_name": op.title(), "feed": feed,
            "s": [[f"{op}:1", "A", "1"], [f"{op}:2", "B", "2"]], "g": [4710000, 1910000, 4720000, 1920000],
            "shapes": shapes, "c": [["1111111", "20260101", "20991231"]], "a": [[]], "r": [[]],
            "t": [[0, str(k), [0, 1], [480 + k, 490 + k], 3, k % 3] for k in range(trips)]}


def build(folder: Path, *, volan_feed: str = "01716.20261102", city_bend: float = 0.0,
          schema: int = gtfs_compact.INDEX_SCHEMA) -> tuple[Path, Path]:
    """volan-index.json.gz and city-index.json.gz with their shapes in folder."""
    folder.mkdir(parents=True, exist_ok=True)
    volan = folder / "volan-index.json.gz"
    city = folder / "city-index.json.gz"
    gtfs_compact.write_index([part("volan", volan_feed)], volan, with_operators=False, schema=schema,
                             today="20261116")
    gtfs_compact.write_index([part("szeged", "abc", bend=city_bend), part("pecs", "20261110")], city,
                             with_operators=True, schema=schema, today="20261116")
    return volan, city


def head(path: Path) -> dict:
    return json.loads(gzip.decompress(path.read_bytes()))


def sign_live(folder: Path) -> None:
    """Sign a live site's timetables.json with the tests' key, as the
    workflow's sign job signs the one it deploys."""
    if (folder / "timetables.json").is_file():
        sm.sign_site(folder, TEST_SEED, TEST_PUB)


def run(*args: str, signed: bool = True) -> tuple[int, str, str, dict]:
    """pages_timetables.py with args, the tests' key pinned; a --live-dir
    site is signed first (signed=False: as it is)."""
    if signed and "--live-dir" in args:
        sign_live(Path(args[args.index("--live-dir") + 1]))
    out_file = Path(tempfile.mkstemp(prefix="gh-out-")[1])
    done = subprocess.run([sys.executable, "-c", TRUST_WRAP, str(HERE), *args,
                           *(["--github-output", str(out_file)] if args[0] in ("due", "assemble") else [])],
                          capture_output=True, text=True, timeout=120,
                          env={**os.environ, "TEST_PAGES_PUBLIC_KEY": TEST_PUB.hex(), "TEST_SIG_SETTLE": "0"})
    outputs = dict(ln.split("=", 1) for ln in out_file.read_text().splitlines() if "=" in ln)
    out_file.unlink()
    return done.returncode, done.stdout, done.stderr, outputs


def make_part(build_dir: Path, part_dir: Path, names: tuple[str, ...] = ("volan", "city"),
              now: datetime = NOW) -> None:
    for name in names:
        rc, out, err, _ = run("part", "--schema", S, "--index", str(build_dir / f"{name}-index.json.gz"), "--out", str(part_dir),
                              "--now", now.isoformat())
        assert rc == 0, (out, err)


def manifest_problems(site: Path) -> list[str]:
    """What in site's timetables.json does not match its files, as the
    integration would check them."""
    man = json.loads((site / "timetables.json").read_text())
    bad = []
    if man.get("format") != 1 or man.get("schema") != gtfs_compact.INDEX_SCHEMA:
        bad.append(f"format/schema {man.get('format')}/{man.get('schema')}")
    for name, entry in man["indexes"].items():
        index = site / entry["url"]
        if not index.is_file():
            bad.append(f"{name}: no {entry['url']}")
            continue
        if entry["size"] != index.stat().st_size or entry["sha256"] != sha(index):
            bad.append(f"{name}: size/sha256")
        idx = head(index)
        if idx.get("v") != man["schema"] or idx.get("feed") != entry["feed"]:
            bad.append(f"{name}: schema/feed")
        if entry["trips"] != len(idx["t"]) or entry["stops"] != len(idx["s"]):
            bad.append(f"{name}: trips/stops")
        if ("o" in idx) != ("operators" in entry) or entry.get("operators", len(idx.get("o", []))) != len(idx.get("o", [])):
            bad.append(f"{name}: operators")
        sh, shapes = idx.get("sh"), entry["shapes"]
        if (sh is None) != (shapes is None):
            bad.append(f"{name}: shapes null")
            continue
        if sh is None:
            continue
        if {"dir": shapes["dir"], "n": shapes["n"]} != sh:
            bad.append(f"{name}: shapes dir/n")
        if sorted(shapes["files"]) != [f"{k:02d}.json.gz" for k in range(sh["n"])]:
            bad.append(f"{name}: shard names")
        on_disk = sorted(p.name for p in (site / sh["dir"]).iterdir())
        if on_disk != sorted(shapes["files"]):
            bad.append(f"{name}: shards on disk {len(on_disk)}")
        for shard, meta in shapes["files"].items():
            f = site / sh["dir"] / shard
            if not f.is_file() or meta != {"size": f.stat().st_size, "sha256": sha(f)}:
                bad.append(f"{name}: shard {shard}")
    return bad


def mav_part(ws: Path, feed: str = "20261101.M2ChangeID") -> Path:
    """mav_pages_site.py's site folder of a small MÁV index, with its GTFS."""
    ws.mkdir(parents=True, exist_ok=True)
    data = {"v": mav_pages_site.mav_index.SCHEMA, "feed": feed,
            "st": [["A", 4700000, 1900000], ["B", 4710000, 1910000]], "p": ["_p~iF~ps|U_ulLnnqC"],
            "n": {str(100 + k): [[0, [0, 1]]] for k in range(60)}, "l": {}}
    (ws / "mav-index.json.gz").write_bytes(gzip.compress(json.dumps(data).encode(), mtime=0))
    (ws / "mav-build.json").write_text(json.dumps({
        "feed_version": feed, "fitted_share": 1.0,
        "audit": {"trips": 900, "trips_bad": 0, "trains": 60, "trains_ok": 60, "ok_ratio": 1.0}}))
    with zipfile.ZipFile(ws / "mav-gtfs.zip", "w") as z:
        z.writestr("feed_info.txt", f"feed_publisher_name,feed_version\nMÁV-START,{feed}\n")
    (ws / "gtfs-out").mkdir(exist_ok=True)
    for name in ("shapes.txt", "stop_times.txt", "trips.txt"):
        (ws / "gtfs-out" / name).write_text(f"{name}\n")
    with contextlib.redirect_stdout(io.StringIO()):
        rc = mav_pages_site.main(["--index", str(ws / "mav-index.json.gz"), "--stats", str(ws / "mav-build.json"),
                                  "--commit", "a" * 40, "--out", str(ws / "site"), "--gtfs-zip",
                                  str(ws / "mav-gtfs.zip"), "--pfaedle-dir", str(ws / "gtfs-out")])
    assert rc == 0
    return ws / "site"


MAV_FILES = ("mav-index.json.gz", "mav-index.json", "mav-gtfs.zip", "mav-pfaedle-overlay.zip")


def tree(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)

    # ---- part ---------------------------------------------------------------
    volan, city = build(tmp / "b1")
    rc, out, err, _ = run("part", "--schema", S, "--index", str(volan), "--out", str(tmp / "p1"), "--now", NOW.isoformat())
    rc2, out2, err2, _ = run("part", "--schema", S, "--index", str(city), "--out", str(tmp / "p1"), "--now", NOW.isoformat())
    part_json = json.loads((tmp / "p1" / "part.json").read_text()) if (tmp / "p1" / "part.json").is_file() else {}
    entries = part_json.get("indexes", {})
    check("part packs both indexes, each with its shapes folder, and records them in part.json",
          rc == 0 and rc2 == 0 and sorted(entries) == ["city", "volan"]
          and (tmp / "p1" / "volan-index.json.gz").read_bytes() == volan.read_bytes()
          and (tmp / "p1" / "city-index.json.gz").read_bytes() == city.read_bytes()
          and all((tmp / "p1" / head(p)["sh"]["dir"]).is_dir() for p in (volan, city)),
          repr((rc, out, err, rc2, out2, err2)))
    ve, ce = entries.get("volan", {}), entries.get("city", {})
    check("a part entry: url, size, sha256, the head's feed, built_at now, trips, stops",
          ve.get("url") == "volan-index.json.gz" and ve.get("size") == volan.stat().st_size
          and ve.get("sha256") == sha(volan) and ve.get("feed") == head(volan)["feed"] == "volan:01716.20261102"
          and ve.get("built_at") == "2026-11-16T10:40:00Z" and ve.get("trips") == 6 and ve.get("stops") == 2
          and "operators" not in ve, repr(ve))
    check("the city entry counts its operators and names every operator's feed",
          ce.get("operators") == 2 and ce.get("feed") == "szeged:abc;pecs:20261110" and ce.get("trips") == 12,
          repr(ce))
    sh = head(city)["sh"]
    check("the shapes entry: the head's dir and n, and every shard's size and sha256",
          ce.get("shapes", {}).get("dir") == sh["dir"] and ce["shapes"]["n"] == sh["n"] == gtfs_compact.SHAPE_SHARDS
          and ce["shapes"]["files"] == {
              f"{k:02d}.json.gz": {"size": (city.parent / sh["dir"] / f"{k:02d}.json.gz").stat().st_size,
                                   "sha256": sha(city.parent / sh["dir"] / f"{k:02d}.json.gz")}
              for k in range(sh["n"])}, repr(ce.get("shapes"))[:300])

    old_volan, _ = build(tmp / "b-old", schema=gtfs_compact.INDEX_SCHEMA - 1)
    rc, out, err, _ = run("part", "--schema", S, "--index", str(old_volan), "--out", str(tmp / "p-old"))
    check("part refuses an index of another schema: exit 1, a warning, nothing packed",
          rc == 1 and "::warning title=Index not published::" in out and "schema" in out
          and not (tmp / "p-old").exists(), repr((rc, out, err)))
    _, city_gap = build(tmp / "b-gap")
    (city_gap.parent / head(city_gap)["sh"]["dir"] / "07.json.gz").unlink()
    rc, out, err, _ = run("part", "--schema", S, "--index", str(city_gap), "--out", str(tmp / "p-gap"))
    check("part refuses an index whose shapes folder lacks a shard",
          rc == 1 and "07.json.gz is missing" in out and not (tmp / "p-gap").exists(), repr((rc, out)))
    two = tmp / "b-two" / "volan-index.json.gz"
    two.parent.mkdir()
    two.write_bytes(volan.read_bytes() + gzip.compress(b"", mtime=0))
    rc, out, err, _ = run("part", "--schema", S, "--index", str(two), "--out", str(tmp / "p-two"))
    check("part refuses an index of two gzip members (the integration reads one)",
          rc == 1 and "single gzip member" in out, repr((rc, out)))
    shutil.copytree(tmp / "b1", tmp / "b-same")
    rc, out, err, _ = run("part", "--schema", S, "--index", str(tmp / "b-same" / "city-index.json.gz"), "--out", str(tmp / "p-same"),
                          "--baseline", str(city))
    check("part says when the rebuilt index has the baseline's content, and packs it all the same",
          rc == 0 and "::notice title=city index unchanged::" in out
          and (tmp / "p-same" / "city-index.json.gz").is_file(), repr((rc, out, err)))
    _, city_new = build(tmp / "b-new", city_bend=0.5)
    rc, out, err, _ = run("part", "--schema", S, "--index", str(city_new), "--out", str(tmp / "p-new"), "--baseline", str(city))
    check("no notice for an index whose content changed", rc == 0 and "unchanged" not in out, out)

    # ---- assemble: a first site, nothing published yet ----------------------
    empty = tmp / "live-empty"
    empty.mkdir()
    site1 = tmp / "site1"
    rc, out, err, outputs = run("assemble", "--out", str(site1), "--commit", COMMIT, "--live-dir", str(empty),
                                "--timetables-part", str(tmp / "p1"), "--now", NOW.isoformat())
    man1 = json.loads((site1 / "timetables.json").read_text()) if (site1 / "timetables.json").is_file() else {}
    check("both indexes of the part make a site without a published one: deploy",
          rc == 0 and outputs.get("deploy") == "true" and sorted(man1.get("indexes", {})) == ["city", "volan"],
          repr((rc, out, err, outputs)))
    check("timetables.json matches its files: sizes, sha256, feed == the head's, shapes dir, n and every shard",
          bool(man1) and manifest_problems(site1) == [], repr(manifest_problems(site1) if man1 else None))
    check("timetables.json: format 1, this checkout's schema, the site's built_at and commit, the part's entries",
          man1.get("format") == 1 and man1.get("schema") == gtfs_compact.INDEX_SCHEMA == 29
          and man1.get("built_at") == "2026-11-16T10:40:00Z" and man1.get("commit") == COMMIT
          and man1.get("indexes", {}).get("volan") == ve and man1.get("indexes", {}).get("city") == ce,
          repr(man1)[:300])
    check("the indexes and shards are the builders' bytes",
          (site1 / "city-index.json.gz").read_bytes() == city.read_bytes()
          and all((site1 / sh["dir"] / n).read_bytes() == (city.parent / sh["dir"] / n).read_bytes()
                  for n in ce["shapes"]["files"]))
    check("timetables.json stays under 64 KiB", (site1 / "timetables.json").stat().st_size <= pt.MAX_MANIFEST)
    check("no MÁV files in a site without a MÁV part or published MÁV index",
          not any((site1 / n).exists() for n in MAV_FILES), repr(sorted(tree(site1))[:8]))
    attribution = (site1 / "ATTRIBUTION.txt").read_text() if (site1 / "ATTRIBUTION.txt").is_file() else ""
    page = (site1 / "index.html").read_text() if (site1 / "index.html").is_file() else ""
    check("ATTRIBUTION.txt names the Volán source, every city feed, menetbrand and the CC BY 4.0 feeds",
          "opendata.utas.hu/public-gtfs/volanbusz_gtfs.zip" in attribution and "Szeged — SZKT" in attribution
          and "gtfs.menetbrand.com" in attribution and "CC BY 4.0" in attribution and "gy-mate" in attribution
          and "BAHART_GTFS_URL" in attribution and "MÁV" not in attribution.split("== Weekly", 1)[0][60:],
          attribution[:400])
    check("index.html links timetables.json and both indexes",
          all(f'href="{n}"' in page for n in ("timetables.json", "volan-index.json.gz", "city-index.json.gz",
                                               "ATTRIBUTION.txt")) and page.startswith("<!doctype html>"), page[:300])

    # ---- assemble: carry from a live site ----------------------------------
    # The live site: the first site plus the MÁV files of a daily build.
    mav1 = mav_part(tmp / "mav1")
    live = tmp / "live"
    shutil.copytree(site1, live)
    for name in MAV_FILES:
        shutil.copyfile(mav1 / name, live / name)
    _, city2 = build(tmp / "b2", city_bend=0.25)
    make_part(tmp / "b2", tmp / "p2", ("city",), NOW + timedelta(days=7))
    site2 = tmp / "site2"
    later = NOW + timedelta(days=7)
    rc, out, err, outputs = run("assemble", "--out", str(site2), "--commit", COMMIT, "--live-dir", str(live),
                                "--timetables-part", str(tmp / "p2"), "--now", later.isoformat())
    man2 = json.loads((site2 / "timetables.json").read_text()) if (site2 / "timetables.json").is_file() else {}
    check("a new city index alone: deploy, the Volán index carried from the live site",
          rc == 0 and outputs.get("deploy") == "true" and "volan: carried" in out and "city: new" in out
          and "MÁV: carried" in out, repr((rc, out, err)))
    check("the carried Volán entry is the published one as it was (its own built_at), the city one new",
          man2.get("indexes", {}).get("volan") == man1["indexes"]["volan"]
          and man2["indexes"]["city"]["sha256"] == sha(city2)
          and man2["indexes"]["city"]["built_at"] == "2026-11-23T10:40:00Z"
          and man2.get("built_at") == "2026-11-23T10:40:00Z", repr(man2)[:300])
    check("the carried Volán index and its shapes are the live files byte for byte",
          {k: v for k, v in tree(site2).items() if k.startswith("volan-")}
          == {k: v for k, v in tree(live).items() if k.startswith("volan-")})
    check("the MÁV files and the MÁV manifest are carried verbatim",
          all((site2 / n).read_bytes() == (live / n).read_bytes() for n in MAV_FILES))
    check("the old city shapes folder is not carried, the new one is complete",
          not (site2 / ce["shapes"]["dir"]).exists() and manifest_problems(site2) == [],
          repr(manifest_problems(site2)))
    attribution = (site2 / "ATTRIBUTION.txt").read_text()
    check("the combined ATTRIBUTION.txt has the MÁV section (CC0, OpenStreetMap) and the Volán and city one",
          "== Daily MÁV index ==" in attribution and "CC0 1.0" in attribution
          and "OpenStreetMap contributors" in attribution and "== Weekly Volán and city indexes ==" in attribution
          and "Feed: 20261101.M2ChangeID" in attribution, attribution[:600])
    page = (site2 / "index.html").read_text()
    check("the combined index.html lists the MÁV files and the indexes",
          all(f'href="{n}"' in page for n in (*MAV_FILES, "timetables.json", "volan-index.json.gz")), page[:400])

    # A new MÁV part, the indexes carried.
    mav2 = mav_part(tmp / "mav2", feed="20261102.M2ChangeID")
    site3 = tmp / "site3"
    rc, out, err, outputs = run("assemble", "--out", str(site3), "--commit", COMMIT, "--live-dir", str(live),
                                "--mav-part", str(mav2), "--now", later.isoformat())
    man3 = json.loads((site3 / "timetables.json").read_text()) if (site3 / "timetables.json").is_file() else {}
    check("a new MÁV part alone: deploy, its files, both indexes carried with their entries as published",
          rc == 0 and outputs.get("deploy") == "true"
          and all((site3 / n).read_bytes() == (mav2 / n).read_bytes() for n in MAV_FILES)
          and man3.get("indexes") == man1["indexes"] and manifest_problems(site3) == [],
          repr((rc, out, err)))

    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-none"), "--commit", COMMIT,
                                "--live-dir", str(live), "--now", later.isoformat())
    check("nothing new: no deploy, nothing written, nothing fetched",
          rc == 0 and outputs.get("deploy") == "false" and not (tmp / "site-none").exists(), repr((rc, out)))
    (tmp / "p-empty").mkdir()
    (tmp / "p-empty" / "part.json").write_text('{"indexes": {}}')
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-none"), "--commit", COMMIT,
                                "--live-dir", str(live), "--timetables-part", str(tmp / "p-empty"))
    check("a part without an index is nothing new", rc == 0 and outputs.get("deploy") == "false", repr((rc, out)))

    # ---- refusals: a listed file that cannot be carried --------------------
    def broken_live(label: str, change) -> Path:
        folder = tmp / f"live-{label}"
        shutil.copytree(live, folder)
        change(folder)
        return folder

    # A published Volán index that cannot be carried is left out, not the
    # whole site.
    drops = {
        "a missing volan shard": lambda f: (f / ve["shapes"]["dir"] / "03.json.gz").unlink(),
        "another volan index": lambda f: (f / "volan-index.json.gz").write_bytes(b"other"),
        "a volan entry without its shapes files": lambda f: (f / "timetables.json").write_text(
            (f / "timetables.json").read_text().replace('"files"', '"nofiles"', 1)),
    }
    for label, change in drops.items():
        folder = broken_live(label.replace(" ", "-"), change)
        out_dir = tmp / f"site-{label.replace(' ', '-')}"
        rc, out, err, outputs = run("assemble", "--out", str(out_dir), "--commit", COMMIT, "--live-dir", str(folder),
                                    "--timetables-part", str(tmp / "p2"), "--settle", "0")
        man = json.loads((out_dir / "timetables.json").read_text()) if rc == 0 else {}
        check(f"{label} on the live site: the Volán index left out with a warning, the new city index and the"
              " MÁV files deployed",
              rc == 0 and outputs.get("deploy") == "true" and sorted(man.get("indexes", {})) == ["city"]
              and "::warning title=volan index dropped::" in out
              and not (out_dir / "volan-index.json.gz").exists() and not (out_dir / ve["shapes"]["dir"]).exists()
              and all((out_dir / n).is_file() for n in MAV_FILES) and manifest_problems(out_dir) == [],
              repr((rc, out[-400:], err[-300:])))

    cases = {
        "a missing MÁV overlay": lambda f: (f / "mav-pfaedle-overlay.zip").unlink(),
        "another MÁV zip": lambda f: (f / "mav-gtfs.zip").write_bytes(b"PK other"),
        "an unreadable timetables.json": lambda f: (f / "timetables.json").write_text("{not json"),
        "an unreadable mav-index.json": lambda f: (f / "mav-index.json").write_text("[]"),
    }
    for label, change in cases.items():
        folder = broken_live(label.replace(" ", "-"), change)
        out_dir = tmp / f"site-{label.replace(' ', '-')}"
        rc, out, err, outputs = run("assemble", "--out", str(out_dir), "--commit", COMMIT, "--live-dir", str(folder),
                                    "--timetables-part", str(tmp / "p2"), "--settle", "0")
        check(f"{label} on the live site: exit 1, an error, no deploy, no site left",
              rc == 1 and outputs.get("deploy") == "false" and "::error title=GitHub Pages site not assembled::" in out
              and not out_dir.exists(), repr((rc, out[-400:], err[-300:])))

    folder = broken_live("no-manifests", lambda f: [(f / n).unlink() for n in ("timetables.json", "mav-index.json")])
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-404"), "--commit", COMMIT,
                                "--live-dir", str(folder), "--timetables-part", str(tmp / "p2"))
    man = json.loads((tmp / "site-404" / "timetables.json").read_text()) if rc == 0 else {}
    check("no published manifests (404): nothing to carry, the new city index alone is deployed",
          rc == 0 and outputs.get("deploy") == "true" and sorted(man.get("indexes", {})) == ["city"]
          and not (tmp / "site-404" / "mav-index.json").exists(), repr((rc, out, err)))

    def old_schema(f: Path) -> None:
        m = json.loads((f / "timetables.json").read_text())
        m["schema"] = gtfs_compact.INDEX_SCHEMA - 1
        (f / "timetables.json").write_text(json.dumps(m))

    folder = broken_live("old-schema", old_schema)
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-old"), "--commit", COMMIT,
                                "--live-dir", str(folder), "--timetables-part", str(tmp / "p2"))
    man = json.loads((tmp / "site-old" / "timetables.json").read_text()) if rc == 0 else {}
    check("a published index of another schema is left out with a warning, not carried",
          rc == 0 and outputs.get("deploy") == "true" and sorted(man.get("indexes", {})) == ["city"]
          and "::warning title=volan index dropped::" in out, repr((rc, out, err)))

    # Indexes of another schema than the published ones (a new stable
    # release changed the format): the new ones make the site's schema.
    _, city_28 = build(tmp / "b-28", schema=SCHEMA - 1)
    rc, out, err, _ = run("part", "--schema", str(SCHEMA - 1), "--index", str(city_28), "--out", str(tmp / "p-28"))
    rc2, out2, err2, outputs = run("assemble", "--out", str(tmp / "site-28"), "--commit", COMMIT,
                                   "--live-dir", str(live), "--timetables-part", str(tmp / "p-28"))
    man = json.loads((tmp / "site-28" / "timetables.json").read_text()) if rc2 == 0 else {}
    check("a part of another schema than the published one: the site takes the part's, the published Volán index"
          " of the other one is left out with a warning",
          rc == 0 and rc2 == 0 and man.get("schema") == SCHEMA - 1 and sorted(man.get("indexes", {})) == ["city"]
          and "::warning title=volan index dropped::" in out2, repr((rc, out, rc2, out2[-400:])))
    rc, out, err, _ = run("part", "--schema", S, "--index", str(volan), "--out", str(tmp / "p-28"))
    check("part refuses to add an index of one schema to a part of another",
          rc == 1 and "the part holds indexes of schema" in out, repr((rc, out)))
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-mav-only"), "--commit", COMMIT, "--live-dir",
                                str(empty), "--mav-part", str(mav2), "--schema", "31")
    man = json.loads((tmp / "site-mav-only" / "timetables.json").read_text()) if rc == 0 else {}
    check("a site without any index names --schema", rc == 0 and man.get("schema") == 31 and man.get("indexes") == {},
          repr((rc, out)))
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-mav-carry"), "--commit", COMMIT, "--live-dir",
                                str(live), "--mav-part", str(mav2), "--schema", "31")
    man = json.loads((tmp / "site-mav-carry" / "timetables.json").read_text()) if rc == 0 else {}
    check("no index built: the carried indexes keep the published schema, whatever --schema says",
          rc == 0 and man.get("schema") == SCHEMA and sorted(man.get("indexes", {})) == ["city", "volan"],
          repr((rc, out)))

    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-big"), "--commit", COMMIT,
                                "--live-dir", str(live), "--timetables-part", str(tmp / "p2"), "--max-bytes", "1000")
    check("a site over the size limit is refused, naming the limit",
          rc == 1 and outputs.get("deploy") == "false" and "1000 byte limit" in out
          and not (tmp / "site-big").exists(), repr((rc, out[-300:])))
    bad_part = tmp / "p-bad"
    shutil.copytree(tmp / "p2", bad_part)
    (bad_part / "city-index.json.gz").write_bytes(b"x")
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-bad"), "--commit", COMMIT,
                                "--live-dir", str(live), "--timetables-part", str(bad_part))
    check("a part whose index is not the one part.json names is refused",
          rc == 1 and outputs.get("deploy") == "false" and "not the file part.json names" in out, repr((rc, out)))
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-c"), "--commit", "nope",
                                "--live-dir", str(live), "--timetables-part", str(tmp / "p2"))
    check("assemble refuses a commit that is no sha", rc == 2 and not (tmp / "site-c").exists(), repr((rc, err)))

    # Pages serving a deploy's files a moment apart: the first fetch of a
    # file is the previous one's, the second the manifest's.
    class Settling(pt.Source):
        def __init__(self, folder: Path) -> None:
            super().__init__(folder=folder)
            self.calls: dict[str, int] = {}

        def fetch(self, name: str, dest: Path, cap: int):
            self.calls[name] = self.calls.get(name, 0) + 1
            got = super().fetch(name, dest, cap)
            if name == "volan-index.json.gz" and self.calls[name] == 1:
                dest.write_bytes(b"stale")
                return pt.file_digest(dest)
            return got

    sign_live(live)
    src = Settling(live)
    ns = argparse.Namespace(out=tmp / "site-settle", commit=COMMIT, mav_part=None, timetables_part=tmp / "p2",
                            max_bytes=mav_pages_site.MAX_SITE_BYTES, settle=0.0, sig_settle=0.0, github_output=None,
                            schema=None)
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rc = pt.cmd_assemble(ns, src, later)
    check("a carried file that does not match at first is fetched again once, and then carried",
          rc == 0 and src.calls.get("volan-index.json.gz") == 2 and manifest_problems(tmp / "site-settle") == []
          and "fetching the volan index again" in buf.getvalue(), repr((rc, src.calls.get("volan-index.json.gz"), buf.getvalue()[-300:])))

    class Broken(pt.Source):
        def fetch(self, name: str, dest: Path, cap: int):
            got = super().fetch(name, dest, cap)
            if name.startswith("volan-shapes-"):
                dest.write_bytes(b"stale")
                return pt.file_digest(dest)
            return got

    ns.out = tmp / "site-broken"
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rc = pt.cmd_assemble(ns, Broken(folder=live), later)
    man = json.loads((tmp / "site-broken" / "timetables.json").read_text()) if rc == 0 else {}
    check("a Volán shard that still does not match when fetched again: the index left out, no partial files",
          rc == 0 and sorted(man.get("indexes", {})) == ["city"] and "::warning title=volan index dropped::" in buf.getvalue()
          and not any(n.startswith("volan-") for n in tree(tmp / "site-broken")), buf.getvalue()[-400:])

    # ---- due ----------------------------------------------------------------
    fresh = json.loads((site1 / "timetables.json").read_text())

    def aged(volan_age: timedelta, city_age: timedelta) -> dict:
        m = json.loads(json.dumps(fresh))
        m["indexes"]["volan"]["built_at"] = pt.utc_text(NOW - volan_age)
        m["indexes"]["city"]["built_at"] = pt.utc_text(NOW - city_age)
        return m

    day = timedelta(days=1)
    due_cases = [
        ("no published manifest", None, False, ["volan", "city"]),
        ("both built a day ago", aged(day, day), False, []),
        ("both a day ago, the dispatch input set", aged(day, day), True, ["volan", "city"]),
        ("the city index 6 days 12 hours old (the Volán one stays)", aged(day, timedelta(days=6, hours=12)), False,
         ["city"]),
        ("both 7 days old", aged(timedelta(days=7), timedelta(days=7)), False, ["volan", "city"]),
        ("the volan index a minute short of it", aged(timedelta(days=6, hours=12) - timedelta(minutes=1), day), False, []),
        ("no city index", {**aged(day, day), "indexes": {"volan": aged(day, day)["indexes"]["volan"]}}, False, ["city"]),
        ("another schema than --schema", {**aged(day, day), "schema": SCHEMA - 1}, False, ["volan", "city"]),
        ("another format", {**aged(day, day), "format": 2}, False, ["volan", "city"]),
        ("a city built_at without an offset", {**aged(day, day), "indexes": {
            **aged(day, day)["indexes"], "city": {**fresh["indexes"]["city"], "built_at": "2026-11-15T10:00:00"}}},
         False, ["city"]),
    ]
    for label, man_, force, want in due_cases:
        got, why = pt.due(man_, "HTTP 404", force, NOW, SCHEMA)
        check(f"due, {label}: {want or 'none'}", got == want and bool(why), why)
    got, why = pt.due(aged(day, day), "", False, NOW, SCHEMA + 1)
    check("due compares with the --schema given, not the checkout's", got == ["volan", "city"], why)

    due_live = tmp / "due-live"
    due_live.mkdir()
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat())
    check("the due CLI: no timetables.json, both due", rc == 0 and outputs.get("due") == "true"
          and outputs.get("indexes") == "volan,city", repr((rc, out, err)))
    (due_live / "timetables.json").write_text(json.dumps(aged(day, day)))
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat(), "--force", "false")
    check("the due CLI: fresh indexes are not due", rc == 0 and outputs.get("due") == "false"
          and outputs.get("indexes") == "", repr((rc, out, err)))
    (due_live / "timetables.json").write_text(json.dumps(aged(day, timedelta(days=8))))
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat())
    check("the due CLI: an old city index alone is due alone", rc == 0 and outputs.get("due") == "true"
          and outputs.get("indexes") == "city", repr((rc, out, err)))
    (due_live / "timetables.json").write_text(json.dumps(aged(day, day)))
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat(), "--force", "true")
    check("the due CLI: the dispatch input makes them due", rc == 0 and outputs.get("due") == "true", repr((rc, out)))
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat(), "--force", "")
    check("the due CLI: an empty input (a scheduled run) is not forcing", outputs.get("due") == "false", out)
    (due_live / "timetables.json").write_text("garbage")
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat())
    check("the due CLI: an unreadable timetables.json is due", rc == 0 and outputs.get("due") == "true", out)
    rc, out, err, outputs = run("due", "--schema", S, "--live-url", "http://example.invalid", "--now", NOW.isoformat())
    check("the due CLI takes only an https live URL", rc == 2, repr((rc, err)))

    # ---- baseline -----------------------------------------------------------
    rc, out, err, _ = run("baseline", "--live-dir", str(live), "--out", str(tmp / "base1"))
    check("the baseline is the published indexes with their shapes, checked against timetables.json",
          rc == 0 and tree(tmp / "base1") == {k: v for k, v in tree(live).items()
                                              if k.startswith(("volan-", "city-"))}, repr((rc, out, err)))
    release_zip = tmp / "frontend.zip"
    _, rel_city = build(tmp / "b-rel", city_bend=0.75)
    with zipfile.ZipFile(release_zip, "w") as z:
        z.writestr("hungarian-transport.js", "//")
        z.write(rel_city, "city-index.json.gz")
        for f in sorted((rel_city.parent / head(rel_city)["sh"]["dir"]).iterdir()):
            z.write(f, f"{head(rel_city)['sh']['dir']}/{f.name}")
    folder = broken_live("base-bad-city", lambda f: (f / "city-index.json.gz").write_bytes(b"x"))
    rc, out, err, _ = run("baseline", "--live-dir", str(folder), "--release-file", str(release_zip),
                          "--out", str(tmp / "base2"))
    got = tree(tmp / "base2")
    check("a published index that does not verify comes from the release zip, its shapes too",
          rc == 0 and got.get("city-index.json.gz") == rel_city.read_bytes()
          and got.get("volan-index.json.gz") == (live / "volan-index.json.gz").read_bytes()
          and sum(k.startswith(head(rel_city)["sh"]["dir"] + "/") for k in got) == gtfs_compact.SHAPE_SHARDS
          and not any(k.startswith(ce["shapes"]["dir"]) for k in got)
          and "baseline city: the latest release's index" in out, repr((rc, out, err)))
    rc, out, err, _ = run("baseline", "--live-dir", str(empty), "--out", str(tmp / "base3"))
    check("no baseline anywhere: a warning per index, exit 0",
          rc == 0 and "::warning title=No volan baseline::" in out and "::warning title=No city baseline::" in out
          and tree(tmp / "base3") == {}, repr((rc, out)))

    # ---- the https source ---------------------------------------------------
    class Res(io.BytesIO):
        def __init__(self, body: bytes, url: str) -> None:
            super().__init__(body)
            self._url = url

        def geturl(self) -> str:
            return self._url

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen: list[tuple[str, dict]] = []

    def fake_urlopen(req, timeout=None):
        seen.append((req.full_url, dict(req.header_items())))
        if req.full_url.endswith("/missing.json"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        if req.full_url.endswith("/down.json"):
            raise urllib.error.HTTPError(req.full_url, 503, "Unavailable", {}, None)
        if req.full_url.endswith("/big.bin"):
            return Res(b"x" * 100, req.full_url)
        return Res(b'{"format": 1}', req.full_url)

    real = pt._urlopen
    pt._urlopen = fake_urlopen
    try:
        https = pt.Source(url="https://bator.github.io/hungarian-transport")
        man_, _body, state = https.manifest("timetables.json")
        check("the https source asks with Cache-Control: no-cache and reads a manifest",
              man_ == {"format": 1} and state == "ok" and seen[-1][0] == "https://bator.github.io/hungarian-transport/timetables.json"
              and seen[-1][1].get("Cache-control") == "no-cache", repr(seen[-1]))
        man_, _body, state = https.manifest("missing.json")
        check("a 404 is a missing manifest, not an error", man_ is None and state == "missing")
        try:
            https.manifest("down.json")
            raised = ""
        except pt.FetchError as err_:
            raised = str(err_)
        check("any other HTTP error is a FetchError naming the status", raised == "down.json: HTTP 503", raised)
        try:
            https.fetch("big.bin", tmp / "big.bin", 99)
            raised = ""
        except pt.FetchError as err_:
            raised = str(err_)
        check("a file over its cap is refused", "larger than 99 bytes" in raised, raised)
    finally:
        pt._urlopen = real
    try:
        pt.Source(url="http://bator.github.io/hungarian-transport")
        ok = False
    except ValueError:
        ok = True
    check("the live site is only an https URL", ok)



# ---- the release's index feeds, release_feed, far-future feeds -------------
class Res(io.BytesIO):
    """An answer as urlopen gives one: a body, its URL, a status, headers."""

    def __init__(self, body: bytes, url: str, status: int = 200, headers: dict | None = None) -> None:
        super().__init__(body)
        self._url, self.status, self.headers = url, status, headers or {}

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ranged(blob: bytes, *, ranges: bool = True, redirect: str | None = None, log: list | None = None,
           cap: int | None = None):
    """A stub _urlopen serving blob at any https URL, with Range when ranges
    (206 and Content-Range), the whole file otherwise; redirect: the URL
    the first request ends on (as GitHub's release download does); cap: at
    most this many bytes in answer to a range (a shorter range, named in
    the Content-Range, as a storage that caps its answers sends)."""
    def opener(req, timeout=None):
        url = req.full_url
        rng = req.get_header("Range")
        if log is not None:
            log.append((url, rng))
        final = redirect or url
        if rng and ranges:
            a, b = (int(x) for x in rng.split("=", 1)[1].split("-"))
            b = min(b, len(blob) - 1, a + cap - 1 if cap else b)
            return Res(blob[a:b + 1], final, 206, {"Content-Range": f"bytes {a}-{b}/{len(blob)}"})
        return Res(blob, final)
    return opener


def release_zip(path: Path, volan: Path | None, city: Path | None, filler: int = 0) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("hungarian-transport.js", "//")
        if filler:
            # Incompressible: a large zip, of which only a little is read.
            z.writestr("mav-index.json.gz", os.urandom(filler), compress_type=zipfile.ZIP_STORED)
        for index in (volan, city):
            if index is not None:
                z.write(index, index.name)
                for f in sorted((index.parent / head(index)["sh"]["dir"]).iterdir()):
                    z.write(f, f"{head(index)['sh']['dir']}/{f.name}")
    return path


with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)
    volan, city = build(tmp / "rel", volan_feed="01716.20261109")
    rel = release_zip(tmp / "frontend.zip", volan, city, filler=3 * 1024 * 1024)
    want = {"volan": head(volan)["feed"], "city": head(city)["feed"]}

    check("release_feeds of a local zip: each index's feed from its head",
          pt.release_feeds(path=rel) == want, repr(pt.release_feeds(path=rel)))
    log: list = []
    real = pt._urlopen
    try:
        pt._urlopen = ranged(rel.read_bytes(), log=log,
                             redirect="https://release-assets.example/signed?sig=1")
        got = pt.release_feeds("https://github.com/o/r/releases/download/v1.2.3/hungarian-transport-frontend.zip")
        fetched = sum(int(r.split("=")[1].split("-")[1]) - int(r.split("=")[1].split("-")[0]) + 1
                      for _u, r in log)
        check("release_feeds over https reads the zip's directory and the heads by Range requests, a small"
              " part of the zip, the redirect's target asked directly after the first answer",
              got == want and all(r for _u, r in log) and len(log) <= 12
              and fetched < rel.stat().st_size // 4 and log[0][0].startswith("https://github.com/")
              and all(u == "https://release-assets.example/signed?sig=1" for u, _r in log[1:]),
              repr((got, len(log), fetched, rel.stat().st_size, log[:3])))
        log.clear()
        pt._urlopen = ranged(rel.read_bytes(), ranges=False, log=log)
        got = pt.release_feeds("https://example.invalid/frontend.zip")
        check("a server that answers a Range request with the whole file: the whole zip is downloaded instead",
              got == want and log[-1][1] is None, repr((got, log)))
        pt._urlopen = ranged(rel.read_bytes()[:-30])
        try:
            pt.release_feeds("https://example.invalid/frontend.zip")
            raised = ""
        except pt.SiteError as err_:
            raised = str(err_)
        check("a cut zip raises SiteError", raised.startswith("release zip:"), raised)

        def gone(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

        pt._urlopen = gone
        try:
            pt.release_feeds("https://example.invalid/frontend.zip")
            raised = ""
        except pt.SiteError as err_:
            raised = str(err_)
        check("a release zip that is not there raises SiteError naming the status", "HTTP 404" in raised, raised)
    finally:
        pt._urlopen = real
    try:
        pt.release_feeds("http://example.invalid/frontend.zip")
        raised = ""
    except pt.SiteError as err_:
        raised = str(err_)
    check("release_feeds takes only an https URL", "not an https URL" in raised, raised)
    only_city = release_zip(tmp / "only-city.zip", None, city)
    check("a release without a Volán index names only the city one",
          pt.release_feeds(path=only_city) == {"city": want["city"]})

    # A server may answer a range with a shorter one (its Content-Range says
    # so): that is a short read, never a ValueError out of readinto.
    # The indexes first and 3 MB after them: their heads are read from far
    # before the end of the zip, so a read asks for a whole RANGE_CHUNK.
    front = tmp / "front.zip"
    shutil.copyfile(release_zip(tmp / "front-indexes.zip", volan, city), front)
    with zipfile.ZipFile(front, "a") as z:
        z.writestr("zz-filler.bin", os.urandom(3 * 1024 * 1024), compress_type=zipfile.ZIP_STORED)
    real = pt._urlopen
    try:
        log.clear()
        pt._urlopen = ranged(front.read_bytes(), cap=64 * 1024, log=log)
        try:
            got = pt.release_feeds("https://example.invalid/frontend.zip")
        except Exception as err_:  # noqa: BLE001 - the failure this checks for
            got = err_
        check("a range answered with a shorter one (64 KiB at most): the rest is asked for, the feeds read",
              got == want and len(log) <= pt.MAX_RANGE_REQUESTS, repr((got, len(log))))
        pt._urlopen = ranged(rel.read_bytes(), cap=64 * 1024)
        raw = pt._RangeFile("https://example.invalid/frontend.zip")
        buf = bytearray(200 * 1024)
        n = raw.readinto(buf)
        check("_RangeFile.readinto returns the bytes of a short answer, and moves on by them",
              n == 64 * 1024 and bytes(buf[:n]) == rel.read_bytes()[:n] and raw.tell() == n, repr((n, raw.tell())))
    finally:
        pt._urlopen = real

    # Whatever reading the release zip raises (zipfile and zlib have their
    # own exceptions), the due step warns and goes by age only.
    def method_99(src: Path, dest: Path) -> Path:
        """src's zip with every member's compression method 99 (none
        zipfile knows): NotImplementedError when a member is opened."""
        with zipfile.ZipFile(src) as z_in, zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as z_out:
            for info in z_in.infolist():
                z_out.writestr(info.filename, z_in.read(info), compress_type=zipfile.ZIP_STORED)
        blob = bytearray(dest.read_bytes())
        for sig, off in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
            k = blob.find(sig)
            while k >= 0:
                blob[k + off:k + off + 2] = (99).to_bytes(2, "little")
                k = blob.find(sig, k + 4)
        dest.write_bytes(bytes(blob))
        return dest

    def broken_deflate(dest: Path) -> Path:
        """A deflated zip whose volan-index.json.gz member's deflate data is
        garbage: zlib.error while it is read."""
        release_zip(dest, volan, None)
        blob = bytearray(dest.read_bytes())
        with zipfile.ZipFile(dest) as z:
            info = z.getinfo("volan-index.json.gz")
        start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        blob[start:start + 16] = b"\xff" * 16
        dest.write_bytes(bytes(blob))
        return dest

    due_live = tmp / "due-exc-live"
    due_live.mkdir()
    entries = {n: {"url": pt.INDEXES[n], "feed": want[n], "built_at": pt.utc_text(NOW - timedelta(days=1)),
                   "size": 1, "sha256": "0" * 64, "shapes": None, "release_feed": "older"} for n in pt.INDEXES}
    (due_live / "timetables.json").write_text(json.dumps({"format": 1, "schema": SCHEMA, "indexes": entries}))
    for label, zpath in (("NotImplementedError (a compression method zipfile lacks)",
                          method_99(rel, tmp / "method99.zip")),
                         ("zlib.error (a broken deflate stream)", broken_deflate(tmp / "deflate.zip"))):
        rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat(),
                                    "--release-file", str(zpath), "--release-out", str(tmp / "rf-exc.json"))
        check(f"due with a release zip that raises {label}: a warning, due by age only, the step does not fail",
              rc == 0 and outputs.get("due") == "false" and "::warning title=Release index not read::" in out
              and "Traceback" not in err and json.loads((tmp / "rf-exc.json").read_text()) == {"indexes": {}},
              repr((rc, out[-600:], err[-600:], outputs)))
    real_rf = pt.release_feeds
    try:
        for exc in (RuntimeError("encrypted\n::error::x"), ValueError("bad"), OSError("disk"),
                    zlib.error("Error -3"), NotImplementedError("method")):
            def raising(*_a, _exc=exc, **_k):
                raise _exc

            pt.release_feeds = raising
            buf = io.StringIO()
            gh_out = tmp / "gh-exc.txt"
            gh_out.write_text("")
            try:
                with contextlib.redirect_stdout(buf):
                    rc = pt.main(["due", "--schema", S, "--live-dir", str(due_live), "--now", NOW.isoformat(),
                                  "--release-url", "https://example.invalid/z.zip", "--github-output", str(gh_out)])
            except Exception as err_:  # noqa: BLE001 - the failure this checks for
                rc = err_
            text = buf.getvalue()
            warn = [ln for ln in text.splitlines() if ln.startswith("::warning title=Release index not read::")]
            check(f"due when reading the release raises {type(exc).__name__}: one warning line (no workflow"
                  " command out of the text), due by age only",
                  rc == 0 and len(warn) == 1 and type(exc).__name__ in warn[0] and "\n::error::x" not in text
                  and "due=false" in gh_out.read_text(), repr((rc, text[-500:])))
    finally:
        pt.release_feeds = real_rf

    # due against the release
    def man_with(volan_rf, city_rf, age: timedelta = timedelta(days=1), version: str | None = None) -> dict:
        entries = {}
        for name, rf in (("volan", volan_rf), ("city", city_rf)):
            entry = {"url": pt.INDEXES[name], "feed": want[name], "built_at": pt.utc_text(NOW - age),
                     "size": 1, "sha256": "0" * 64, "shapes": None}
            if rf is not None:
                entry["release_feed"] = rf
                if version is not None:
                    entry["release_version"] = version
            entries[name] = entry
        return {"format": 1, "schema": SCHEMA, "indexes": entries}

    for label, man_, release, names in (
        ("both built against the release's indexes", man_with(want["volan"], want["city"]), want, []),
        ("the city one against an older release's", man_with(want["volan"], "szeged:old"), want, ["city"]),
        ("no release_feed (built before the field)", man_with(None, None), want, ["volan", "city"]),
        ("the release could not be read: by age only", man_with(None, None), None, []),
        ("a release without a city index: the city one has nothing to compare",
         man_with(want["volan"], None), {"volan": want["volan"]}, []),
        ("both old and of another release: listed once each", man_with("x", "y", timedelta(days=7)), want,
         ["volan", "city"]),
    ):
        got, why = pt.due(man_, "", False, NOW, SCHEMA, release)
        check(f"due against the release, {label}: {names or 'none'}", got == names and bool(why), why)

    # The integration goes by release_version (weekly_timetables
    # release_newer): a new release whose indexes have the feeds of the one
    # before still makes both due, so that the site names it.
    for label, man_, release, tag, names in (
        ("the release's feeds and its tag", man_with(want["volan"], want["city"], version="v1.10.24"), want,
         "v1.10.24", []),
        ("the release's feeds, an older release's tag", man_with(want["volan"], want["city"], version="v1.10.23"),
         want, "v1.10.24", ["volan", "city"]),
        ("the release's feeds, no release_version", man_with(want["volan"], want["city"]), want, "v1.10.24",
         ["volan", "city"]),
        ("an older tag, but the release could not be read", man_with(want["volan"], want["city"], version="v1.10.23"),
         None, "v1.10.24", []),
        ("an older tag, no --release-tag", man_with(want["volan"], want["city"], version="v1.10.23"), want, None, []),
        ("an older tag, a release without a city index",
         man_with(want["volan"], None, version="v1.10.23"), {"volan": want["volan"]}, "v1.10.24", ["volan"]),
    ):
        got, why = pt.due(man_, "", False, NOW, SCHEMA, release, tag)
        check(f"due against the release's tag, {label}: {names or 'none'}",
              got == names and bool(why) and ("release_version, not v1.10.24" in why) == bool(names), why)

    live = tmp / "due-live"
    live.mkdir()
    (live / "timetables.json").write_text(json.dumps(man_with(want["volan"], "szeged:old")))
    feeds_out = tmp / "release-feeds.json"
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(live), "--now", NOW.isoformat(),
                                "--release-file", str(rel), "--release-out", str(feeds_out))
    check("the due CLI with --release-file: the index built against another release is due, the release's"
          " feeds are written for part",
          rc == 0 and outputs.get("due") == "true" and outputs.get("indexes") == "city"
          and json.loads(feeds_out.read_text()) == {"indexes": want}, repr((rc, out, err, outputs)))
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(live), "--now", NOW.isoformat(),
                                "--release-file", str(rel), "--release-out", str(feeds_out),
                                "--release-tag", "v1.10.24")
    check("the due CLI with --release-tag: the release's tag is written for part next to its feeds",
          rc == 0 and json.loads(feeds_out.read_text()) == {"indexes": want, "release": "v1.10.24"},
          repr((rc, out, err, outputs)))
    for version, want_due in (("v1.10.23", "volan,city"), ("v1.10.24", "")):
        tag_live = tmp / f"due-tag-live-{version}"
        tag_live.mkdir()
        (tag_live / "timetables.json").write_text(json.dumps(man_with(want["volan"], want["city"], version=version)))
        rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(tag_live), "--now", NOW.isoformat(),
                                    "--release-file", str(rel), "--release-tag", "v1.10.24")
        check(f"the due CLI, entries of the release's feeds and release_version {version}, --release-tag v1.10.24:"
              f" {want_due or 'nothing'} due", rc == 0 and outputs.get("indexes") == want_due
              and outputs.get("due") == ("true" if want_due else "false"), repr((rc, out, err, outputs)))
    for bad_tag in ("v1.10.24-rev1", "1.10.24", "v1.10.24\n::error::x"):
        rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(live), "--now", NOW.isoformat(),
                                    "--release-file", str(rel), "--release-tag", bad_tag)
        check(f"the due CLI refuses --release-tag {bad_tag!r}: only a stable tag",
              rc == 2 and "vX.Y.Z" in err, repr((rc, err)))
    (tmp / "junk.zip").write_bytes(b"not a zip")
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(live), "--now", NOW.isoformat(),
                                "--release-file", str(tmp / "junk.zip"), "--release-out", str(feeds_out),
                                "--release-tag", "v1.10.24")
    check("a release zip that does not read: a warning, due by age only, no feeds for part",
          rc == 0 and outputs.get("due") == "false" and "::warning title=Release index not read::" in out
          and json.loads(feeds_out.read_text()) == {"indexes": {}}, repr((rc, out, err, outputs)))

    # part records release_feed and release_version, assemble publishes them
    feeds_out.write_text(json.dumps({"indexes": want, "release": "v1.10.24"}))
    for name, index in (("volan", volan), ("city", city)):
        rc, out, err, _ = run("part", "--schema", S, "--index", str(index), "--out", str(tmp / "p-rf"),
                              "--now", NOW.isoformat(), "--release-feeds", str(feeds_out))
        check(f"part of the {name} index with --release-feeds", rc == 0, repr((rc, out, err)))
    entries = json.loads((tmp / "p-rf" / "part.json").read_text())["indexes"]
    check("part records each index's release_feed: the release's feed of that index",
          entries["volan"].get("release_feed") == want["volan"] and entries["city"].get("release_feed") == want["city"],
          repr({k: v.get("release_feed") for k, v in entries.items()}))
    check("part records each index's release_version: the stable release's tag whose index release_feed is",
          entries["volan"].get("release_version") == "v1.10.24" and entries["city"].get("release_version") == "v1.10.24",
          repr({k: v.get("release_version") for k, v in entries.items()}))
    feeds_out.write_text(json.dumps({"indexes": {"city": want["city"]}, "release": "v1.10.24"}))
    rc, out, err, _ = run("part", "--schema", S, "--index", str(volan), "--out", str(tmp / "p-rf2"),
                          "--now", NOW.isoformat(), "--release-feeds", str(feeds_out))
    rc2, out2, err2, _ = run("part", "--schema", S, "--index", str(city), "--out", str(tmp / "p-rf2"),
                             "--now", NOW.isoformat(), "--release-feeds", str(tmp / "nowhere.json"))
    entries2 = json.loads((tmp / "p-rf2" / "part.json").read_text())["indexes"]
    check("no release_feed (nor release_version) when the release has no index of that name, or there is no"
          " feeds file",
          rc == 0 and rc2 == 0 and "release_feed" not in entries2["volan"] and "release_feed" not in entries2["city"]
          and "release_version" not in entries2["volan"] and "release_version" not in entries2["city"],
          repr(entries2))
    for label, tag in (("no release tag", None), ("a tag that is no stable one", "v1.10.24-rev2")):
        feeds_out.write_text(json.dumps({"indexes": want, **({"release": tag} if tag else {})}))
        out_dir = tmp / f"p-rv-{len(label)}"
        rc, out, err, _ = run("part", "--schema", S, "--index", str(volan), "--out", str(out_dir),
                              "--now", NOW.isoformat(), "--release-feeds", str(feeds_out))
        entry_rv = json.loads((out_dir / "part.json").read_text())["indexes"]["volan"] if rc == 0 else {}
        check(f"part with {label} in the feeds file: release_feed, no release_version",
              entry_rv.get("release_feed") == want["volan"] and "release_version" not in entry_rv, repr(entry_rv))
    empty = tmp / "empty"
    empty.mkdir()
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-rf"), "--commit", COMMIT, "--live-dir",
                                str(empty), "--timetables-part", str(tmp / "p-rf"), "--now", NOW.isoformat())
    man_rf = json.loads((tmp / "site-rf" / "timetables.json").read_text())
    check("timetables.json publishes each entry's release_feed and release_version",
          rc == 0 and man_rf["indexes"]["volan"].get("release_feed") == want["volan"]
          and man_rf["indexes"]["city"].get("release_feed") == want["city"]
          and all(man_rf["indexes"][n].get("release_version") == "v1.10.24" for n in ("volan", "city"))
          and manifest_problems(tmp / "site-rf") == [], repr((rc, out, err)))
    print("an entry as published:", json.dumps({k: v for k, v in man_rf["indexes"]["volan"].items()
                                                 if k != "shapes"}))
    # Carried as published: a MÁV-only run keeps it.
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "site-rf2"), "--commit", COMMIT, "--live-dir",
                                str(tmp / "site-rf"), "--mav-part", str(mav_part(tmp / "mav-rf")),
                                "--now", (NOW + timedelta(hours=1)).isoformat())
    man_rf2 = json.loads((tmp / "site-rf2" / "timetables.json").read_text())
    check("a carried index keeps its release_feed and release_version",
          rc == 0 and man_rf2["indexes"] == man_rf["indexes"]
          and man_rf2["indexes"]["city"].get("release_version") == "v1.10.24", repr((rc, out, err)))

    # ---- a published manifest without a signature that verifies ----------
    # Every deploy signs its manifest anew: one whose signature does not
    # verify (none yet, another key, entries changed after signing) is no
    # manifest at all, so nothing of it is signed again.
    trusted = tmp / "site-rf"
    sign_live(trusted)
    variants = {}
    for label, how in (("no timetables.json.sig (the first run after signing came in)",
                        lambda f: (f / "timetables.json.sig").unlink()),
                       ("a signature by another key", lambda f: sm.sign_site(f, (k := os.urandom(32)), sm.public_key(k))),
                       ("an entry changed after signing", lambda f: (f / "timetables.json").write_text(
                           (f / "timetables.json").read_text().replace('"trips": 6', '"trips": 7', 1))),
                       ("a signature that is not base64", lambda f: (f / "timetables.json.sig").write_text("!!\n"))):
        folder = tmp / f"untrusted-{len(variants)}"
        shutil.copytree(trusted, folder)
        how(folder)
        variants[label] = folder
    for label, folder in variants.items():
        # No .sig at all is the first run after signing came in (a warning);
        # one that is there but does not verify turns the run red (below).
        bad = not label.startswith("no ")
        word = "bad signature" if bad else "unsigned"
        rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(folder), "--now", NOW.isoformat(),
                                    signed=False)
        check(f"due, the published manifest with {label}: untrusted, both due",
              rc == 0 and outputs.get("indexes") == "volan,city" and word in out, repr((rc, out, err)))
        rc, out, err, _ = run("baseline", "--live-dir", str(folder), "--release-file", str(rel),
                              "--out", str(folder.parent / (folder.name + "-base")), signed=False)
        got = tree(folder.parent / (folder.name + "-base"))
        check(f"baseline, the published manifest with {label}: the release's indexes, none of the site's",
              rc == 0 and got.get("volan-index.json.gz") == volan.read_bytes()
              and got.get("city-index.json.gz") == city.read_bytes() and word in out, repr((rc, out)))
        rc, out, err, outputs = run("assemble", "--out", str(folder.parent / (folder.name + "-site")), "--commit",
                                    COMMIT, "--live-dir", str(folder), "--mav-part", str(tmp / "mav-rf" / "site"),
                                    "--settle", "0", "--now", NOW.isoformat(), signed=False)
        man_u = json.loads((folder.parent / (folder.name + "-site") / "timetables.json").read_text()) if rc == 0 else {}
        check(f"assemble, the published manifest with {label}: nothing carried from it, "
              + ("an error, named in untrusted (the run goes red after the deploy)" if bad else "a warning")
              + ", the MÁV part deployed", rc == 0 and outputs.get("deploy") == "true" and man_u.get("indexes") == {}
              and f"::{'error' if bad else 'warning'} title=Published timetables.json not trusted::" in out
              and outputs.get("untrusted") == ("timetables.json" if bad else "")
              and not any(n.startswith(("volan-", "city-")) for n in tree(folder.parent / (folder.name + "-site"))),
              repr((rc, out[-600:], err, outputs)))
        if bad:
            # Both indexes new: nothing to carry, the manifest's signature is
            # checked all the same.
            both = tmp / f"both-{folder.name}"
            make_part(tmp / "rel", both)
            rc, out, err, outputs = run("assemble", "--out", str(folder.parent / (folder.name + "-site2")),
                                        "--commit", COMMIT, "--live-dir", str(folder), "--timetables-part",
                                        str(both), "--settle", "0", "--now", NOW.isoformat(), signed=False)
            check(f"assemble with both indexes new, the published manifest with {label}: deployed, untrusted"
                  " names it all the same, the error says the deploy replaces it",
                  rc == 0 and outputs.get("deploy") == "true"
                  and outputs.get("untrusted") == "timetables.json"
                  and "and the deploy replaces it with a signed one." in out, repr((rc, out[-400:], outputs)))
            rc, out, err, outputs = run("assemble", "--out", str(folder.parent / (folder.name + "-site3")),
                                        "--commit", COMMIT, "--live-dir", str(folder), "--settle", "0",
                                        "--now", NOW.isoformat(), signed=False)
            check(f"assemble with nothing new, the published manifest with {label}: nothing deployed, untrusted"
                  " names it, the error says nothing replaces it in this run", rc == 0 and outputs.get("deploy") == "false"
                  and outputs.get("untrusted") == "timetables.json" and "this run deploys nothing" in out
                  and "the deploy replaces it" not in out, repr((rc, out[-400:], outputs)))
    rc, out, err, outputs = run("due", "--schema", S, "--live-dir", str(trusted), "--now", NOW.isoformat())
    check("due, the same manifest with a good signature: trusted, nothing due", rc == 0 and outputs.get("due") == "false",
          repr((rc, out)))

    class LateSig(pt.Source):
        """Pages serving the previous deploy's .sig for a moment."""

        def __init__(self, folder: Path) -> None:
            super().__init__(folder=folder)
            self.sigs = 0

        def fetch(self, name: str, dest: Path, cap: int):
            got = super().fetch(name, dest, cap)
            if name == "timetables.json.sig":
                self.sigs += 1
                if self.sigs == 1:
                    dest.write_bytes((variants["a signature by another key"] / name).read_bytes())
                    return pt.file_digest(dest)
            return got

    late = LateSig(trusted)
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        live_man, state = late.timetables(settle=0)
    check("a signature that does not verify at first is read again once (settle), then trusted",
          state == "ok" and live_man is not None and late.sigs == 2 and "reading both again" in buf.getvalue(),
          repr((state, late.sigs)))
    late = LateSig(trusted)
    live_man, state = late.timetables()
    check("without settle it is not read again: untrusted", live_man is None and pt.bad_signature(state)
          and late.sigs == 1, repr((state, late.sigs)))

    # ---- mav-index.json: signed the same way, carried only when it verifies
    # site-rf2: the MÁV files and timetables.json, as a deploy leaves them.
    mav_live = tmp / "site-rf2"
    sign_live(mav_live)
    check("the tests' signing (the sign job's) signs mav-index.json too when the site has it",
          sm.verify(mav_live / "mav-index.json", sm.read_sig(mav_live / "mav-index.json.sig"), TEST_PUB),
          repr(sorted(tree(mav_live))))
    rc, out, err, outputs = run("assemble", "--out", str(tmp / "mav-carry"), "--commit", COMMIT, "--live-dir",
                                str(mav_live), "--timetables-part", str(tmp / "p-rf"), "--settle", "0",
                                "--now", (NOW + timedelta(hours=2)).isoformat())
    check("a published mav-index.json whose .sig verifies: its files carried",
          rc == 0 and outputs.get("deploy") == "true" and outputs.get("untrusted") == ""
          and all(tree(tmp / "mav-carry").get(n) == (mav_live / n).read_bytes() for n in MAV_FILES),
          repr((rc, out[-400:], err)))
    mav_variants = {}
    for label, how in (("no mav-index.json.sig (the first run after signing came in)",
                        lambda f: (f / "mav-index.json.sig").unlink()),
                       ("a mav-index.json.sig by another key",
                        lambda f: (f / "mav-index.json.sig").write_text(base64.b64encode(
                            sm.sign(f / "mav-index.json", os.urandom(32))).decode() + "\n")),
                       ("a mav-index.json changed after signing", lambda f: (f / "mav-index.json").write_text(
                           (f / "mav-index.json").read_text().replace('"trains": 60', '"trains": 61', 1)))):
        folder = tmp / f"mav-untrusted-{len(mav_variants)}"
        shutil.copytree(mav_live, folder)
        how(folder)
        mav_variants[label] = folder
    for label, folder in mav_variants.items():
        bad = not label.startswith("no ")
        site_dir = folder.parent / (folder.name + "-site")
        rc, out, err, outputs = run("assemble", "--out", str(site_dir), "--commit", COMMIT, "--live-dir",
                                    str(folder), "--timetables-part", str(tmp / "p-rf"), "--settle", "0",
                                    "--now", (NOW + timedelta(hours=2)).isoformat(), signed=False)
        if bad:
            check(f"{label}, no MÁV part in this run: refused (an error, exit 1), nothing written or deployed,"
                  " untrusted names it", rc == 1 and outputs.get("deploy") == "false" and not site_dir.exists()
                  and "::error title=GitHub Pages site not assembled::" in out and "not trusted" in out
                  and outputs.get("untrusted") == "mav-index.json", repr((rc, out[-600:], err, outputs)))
        else:
            check(f"{label}, no MÁV part in this run: nothing carried, nothing deployed, a warning (exit 0)",
                  rc == 0 and outputs.get("deploy") == "false" and not site_dir.exists()
                  and "::warning title=GitHub Pages site not assembled::" in out and "unsigned" in out
                  and outputs.get("untrusted") == "", repr((rc, out[-600:], err, outputs)))
        site_dir = folder.parent / (folder.name + "-part-site")
        rc, out, err, outputs = run("assemble", "--out", str(site_dir), "--commit", COMMIT, "--live-dir",
                                    str(folder), "--mav-part", str(mav_part(tmp / f"mav-new-{folder.name}",
                                                                            feed="20261102.M2ChangeID")),
                                    "--settle", "0", "--now", (NOW + timedelta(hours=2)).isoformat(), signed=False)
        new_man = json.loads((site_dir / "mav-index.json").read_text()) if rc == 0 else {}
        check(f"{label}, this run's MÁV part: published in its place"
              + (", untrusted names the published one (the run goes red after the deploy)" if bad else ""),
              rc == 0 and outputs.get("deploy") == "true" and new_man.get("feed") == "20261102.M2ChangeID"
              and outputs.get("untrusted") == ("mav-index.json" if bad else ""), repr((rc, out[-600:], outputs)))

    class LateMavSig(pt.Source):
        """Pages serving the previous deploy's mav-index.json.sig for a
        moment."""

        def __init__(self, folder: Path) -> None:
            super().__init__(folder=folder)
            self.sigs = 0

        def fetch(self, name: str, dest: Path, cap: int):
            got = super().fetch(name, dest, cap)
            if name == "mav-index.json.sig":
                self.sigs += 1
                if self.sigs == 1:
                    dest.write_bytes((mav_variants["a mav-index.json.sig by another key"] / name).read_bytes())
                    return pt.file_digest(dest)
            return got

    late_mav = LateMavSig(mav_live)
    with contextlib.redirect_stdout(io.StringIO()):
        (tmp / "late-mav").mkdir()
        got_man = pt.mav_from_live(late_mav, tmp / "late-mav", settle=0)
    check("a mav-index.json.sig that does not verify at first is read again once (settle), then carried",
          got_man is not None and late_mav.sigs == 2 and late_mav.states.get("mav-index.json") == "ok"
          and (tmp / "late-mav" / "mav-index.json").read_bytes() == (mav_live / "mav-index.json").read_bytes(),
          repr((late_mav.sigs, late_mav.states)))

    # ---- Pages' CDN: one deploy's manifest next to the other's .sig ----
    # Fastly caches each path on its own for up to PAGES_MAX_AGE (600 s),
    # keyed on the path alone: after a deploy, a cache that took the .sig
    # files before it serves them for up to ten minutes next to the new
    # manifests. gen1: the site before the deploy (mav_live); gen2: the
    # deploy (a new MÁV index, the indexes carried), signed.
    gen2 = tmp / "cdn-gen2"
    rc, out, err, _ = run("assemble", "--out", str(gen2), "--commit", COMMIT, "--live-dir", str(mav_live),
                          "--mav-part", str(mav_part(tmp / "mav-gen2", feed="20261102.M2ChangeID")),
                          "--settle", "0", "--now", (NOW + timedelta(hours=3)).isoformat())
    sign_live(gen2)
    check("the CDN fixture: two signed generations whose manifests and signatures all differ",
          rc == 0 and all((gen2 / n).read_bytes() != (mav_live / n).read_bytes()
                          for n in ("mav-index.json", "mav-index.json.sig", "timetables.json", "timetables.json.sig")),
          repr((rc, out[-400:], err)))

    class Clock:
        """time.monotonic and time.sleep of pages_timetables, a sleep moving
        the clock on at once."""

        def __init__(self) -> None:
            self.now = 1000.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    class Cdn(pt.Source):
        """gen2 as Pages serves it right after its deploy: the paths in
        stale come from gen1 (cached before the deploy) until the clock
        reaches expires; every other path is gen2's. Each read takes a
        second."""

        def __init__(self, clock: Clock, stale: set[str], expires: float) -> None:
            super().__init__(folder=gen2)
            self.clock, self.stale, self.expires = clock, stale, expires

        def fetch(self, name: str, dest: Path, cap: int):
            got = super().fetch(name, dest, cap)
            if name in self.stale and self.clock.now < self.expires:
                if (mav_live / name).read_bytes() != (gen2 / name).read_bytes():
                    shutil.copyfile(mav_live / name, dest)
                else:
                    # The same file in both deploys: another deploy's copy
                    # stands in (a cache of one before them).
                    dest.write_bytes(b"older " + (gen2 / name).read_bytes())
                got = pt.file_digest(dest)
            self.clock.now += 1
            return got

    def cdn_main(argv: list[str], stale: set[str], sig_settle: float | None = None) -> tuple[int, str, dict, Clock]:
        """pages_timetables.main against Cdn: the first read at 1000 s, the
        stale copies expiring at 1595 s (cached 5 s before the deploy, which
        was over before the first read)."""
        clock = Clock()
        cdn = Cdn(clock, stale, clock.now + pt.PAGES_MAX_AGE - 5)
        gh_out = tmp / "gh-cdn.txt"
        gh_out.write_text("")
        real = (pt.time.monotonic, pt.time.sleep, pt._source, pt.SIG_SETTLE_S)
        pt.time.monotonic, pt.time.sleep, pt._source, pt.SIG_SETTLE_S = clock.monotonic, clock.sleep, \
            (lambda _args: cdn), REAL_SIG_SETTLE
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc_ = pt.main([*argv, "--github-output", str(gh_out),
                               *([] if sig_settle is None else ["--sig-settle", str(sig_settle)])])
        finally:
            pt.time.monotonic, pt.time.sleep, pt._source, pt.SIG_SETTLE_S = real
        outputs_ = dict(ln.split("=", 1) for ln in gh_out.read_text().splitlines() if "=" in ln)
        return rc_, buf.getvalue(), outputs_, clock

    sigs = {"mav-index.json.sig", "timetables.json.sig"}
    check("SIG_SETTLE_S is Pages' cache age and a minute", REAL_SIG_SETTLE == pt.PAGES_MAX_AGE + 60 == 660,
          repr(REAL_SIG_SETTLE))
    cdn_site = tmp / "cdn-site"
    asm = ["assemble", "--out", str(cdn_site), "--commit", COMMIT, "--live-dir", str(gen2), "--timetables-part",
           str(tmp / "p-rf"), "--settle", "0", "--now", (NOW + timedelta(hours=4)).isoformat()]
    for stale, what in ((sigs, "the previous deploy's .sig files next to the new manifests"),
                        ({"mav-index.json", "timetables.json"}, "the previous deploy's manifests next to the new .sig"
                                                                 " files")):
        rc, out, outputs, clock = cdn_main(asm, stale)
        check(f"assemble right after a deploy, Pages serving {what}: read again once, SIG_SETTLE_S after the first"
              " read (past the cache), then trusted: the new MÁV files carried, deployed, nothing untrusted",
              rc == 0 and outputs.get("deploy") == "true" and outputs.get("untrusted") == ""
              and len(clock.sleeps) == 1 and abs(sum(clock.sleeps) + 3 - REAL_SIG_SETTLE) <= 3
              and (cdn_site / "mav-index.json").read_bytes() == (gen2 / "mav-index.json").read_bytes(),
              repr((rc, outputs, clock.sleeps, out[-600:])))
    rc, out, outputs, clock = cdn_main(asm, sigs, sig_settle=30)
    check("the same with the half minute signed() waited before: still the previous .sig, refused (exit 1),"
          " untrusted names mav-index.json", rc == 1 and outputs.get("untrusted") == "mav-index.json"
          and len(clock.sleeps) == 1 and 25 <= clock.sleeps[0] <= 30, repr((rc, outputs, clock.sleeps)))
    rc, out, outputs, clock = cdn_main(["assemble", "--out", str(tmp / "cdn-none"), "--commit", COMMIT,
                                        "--live-dir", str(gen2), "--settle", "0"], sigs)
    check("assemble with nothing new right after a deploy: one wait covers both manifests (the second one, read"
          " after it, verifies at once), nothing untrusted",
          rc == 0 and outputs.get("deploy") == "false" and outputs.get("untrusted") == ""
          and len(clock.sleeps) == 1 and out.count("reading both again") == 1, repr((rc, outputs, clock.sleeps)))
    # A manifest whose .sig does not verify after the one wait (a stale copy
    # put in by hand here: a cache could not have one by then) is read
    # again without waiting again.
    clock = Clock()
    cdn = Cdn(clock, sigs, clock.now + pt.PAGES_MAX_AGE - 5)
    real = (pt.time.monotonic, pt.time.sleep)
    pt.time.monotonic, pt.time.sleep = clock.monotonic, clock.sleep
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            first = cdn.signed("mav-index.json", REAL_SIG_SETTLE)[2]
            cdn.stale.add("timetables.json.sig")
            cdn.expires = clock.now + 30
            second = cdn.signed("timetables.json", REAL_SIG_SETTLE)[2]
    finally:
        pt.time.monotonic, pt.time.sleep = real
    check("a second manifest that does not verify after the wait is read again at once, no second wait (no"
          " cached copy of before the deploy is left by then: its .sig is bad)",
          first == "ok" and pt.bad_signature(second) and len(clock.sleeps) == 2 and clock.sleeps[1] == 0.0,
          repr((first, second, clock.sleeps)))
    # A MÁV or Volán file of the previous deploy next to the new manifest is
    # the same cache effect: fetched again past the cache (the same one wait
    # as the signatures), not half a minute later.
    for stale, what in (({"mav-index.json.gz"}, "the previous deploy's mav-index.json.gz"),
                        ({"mav-index.json.gz"} | sigs, "the previous deploy's mav-index.json.gz and .sig files"),
                        ({"volan-index.json.gz"}, "an older volan-index.json.gz")):
        site_dir = tmp / f"cdn-file-{len(what)}"
        argv = ["assemble", "--out", str(site_dir), "--commit", COMMIT, "--live-dir", str(gen2), "--now",
                (NOW + timedelta(hours=4)).isoformat(),
                *(["--timetables-part", str(tmp / "p-rf")] if "mav" in what else
                  ["--mav-part", str(mav_part(tmp / "mav-gen3", feed="20261103.M2ChangeID"))])]
        rc, out, outputs, clock = cdn_main(argv, stale)
        carried = "mav-index.json.gz" if "mav" in what else "volan-index.json.gz"
        check(f"assemble right after a deploy, Pages serving {what}: fetched again once, SIG_SETTLE_S after the"
              " first read, then carried and deployed",
              rc == 0 and outputs.get("deploy") == "true" and outputs.get("untrusted") == ""
              and len(clock.sleeps) == 1 and abs(sum(clock.sleeps) + 3 - REAL_SIG_SETTLE) <= 6
              and (site_dir / carried).read_bytes() == (gen2 / carried).read_bytes(),
              repr((rc, outputs, clock.sleeps, out[-600:])))
    rc, out, outputs, clock = cdn_main(["assemble", "--out", str(tmp / "cdn-file-old"), "--commit", COMMIT,
                                        "--live-dir", str(gen2), "--timetables-part", str(tmp / "p-rf"),
                                        "--settle", "30", "--now", (NOW + timedelta(hours=4)).isoformat()],
                                       {"mav-index.json.gz"}, sig_settle=-1)
    check("the same with the half minute assemble waited before (--sig-settle -1, --settle 30): still the"
          " previous file, refused (exit 1), nothing deployed",
          rc == 1 and outputs.get("deploy") == "false" and clock.sleeps == [30.0], repr((rc, outputs, clock.sleeps)))
    due_args = ["due", "--schema", S, "--live-dir", str(gen2), "--now", (NOW + timedelta(hours=4)).isoformat()]
    rc, out, outputs, clock = cdn_main(due_args, sigs)
    rc_ok, out_ok, outputs_ok, _ = cdn_main(due_args, set())
    check("due right after a deploy, Pages serving the previous timetables.json.sig: read again past the cache,"
          " due as with both of one deploy (not both for an untrusted manifest)",
          rc == 0 and outputs == outputs_ok and "bad signature" not in out.splitlines()[-3]
          and len(clock.sleeps) == 1 and outputs.get("indexes") != "volan,city",
          repr((rc, outputs, outputs_ok, clock.sleeps, out[-400:])))

    attribution = (tmp / "site-rf" / "ATTRIBUTION.txt").read_text()
    check("ATTRIBUTION.txt names timetables.json.sig", "timetables.json.sig" in attribution, attribution[-400:])

    # far-future feeds
    limit = (NOW + pt.MAX_FEED_AHEAD).strftime("%Y%m%d")
    beyond = (NOW + pt.MAX_FEED_AHEAD + timedelta(days=1)).strftime("%Y%m%d")
    far_volan, _ = build(tmp / "far-v", volan_feed=f"01716.{beyond}")
    edge_volan, _ = build(tmp / "edge-v", volan_feed=f"01716.{limit}")
    far_city = tmp / "far-c" / "city-index.json.gz"
    far_city.parent.mkdir()
    gtfs_compact.write_index([part("szeged", "abc"), part("pecs", beyond)], far_city, with_operators=True,
                             schema=SCHEMA, today="20261116")
    for label, index, want_rc in ((f"a Volán feed dated {beyond}", far_volan, 1),
                                  (f"a city operator's feed dated {beyond}", far_city, 1),
                                  (f"a Volán feed dated {limit}, MAX_FEED_AHEAD ahead", edge_volan, 0)):
        out_dir = tmp / f"p-far-{len(label)}"
        rc, out, err, _ = run("part", "--schema", S, "--index", str(index), "--out", str(out_dir), "--now", NOW.isoformat())
        check(f"part, {label}: " + ("refused, nothing packed, the published one stays" if want_rc else "packed"),
              rc == want_rc and (("::warning title=Index not published::" in out and "more than 400 days ahead" in out
                                  and not out_dir.exists()) if want_rc else (out_dir / index.name).is_file()),
              repr((rc, out, err)))
    check("far_feed reads the date as the integration does (the first 8-digit run of each operator's feed)",
          pt.far_feed("city", f"a:x;b:1.{beyond}.2", NOW) and not pt.far_feed("city", f"a:{limit}", NOW)
          and not pt.far_feed("volan", "volan:123456789", NOW) and pt.far_feed("volan", f"volan:01716.{beyond}", NOW))

    # ---- fetch_release ------------------------------------------------------
    real, real_max = pt._urlopen, pt.MAX_RELEASE_ZIP
    dest = tmp / "dl" / "frontend.zip"
    dest.parent.mkdir()
    cases = (
        ("an http URL is not asked", "http://example.invalid/z.zip", None, "not an https URL: http://example.invalid/z.zip"),
        ("a download", "https://example.invalid/z.zip", lambda req, timeout=None: Res(b"PK" * 10, req.full_url), "ok"),
        ("an answer that ended on http", "https://example.invalid/z.zip",
         lambda req, timeout=None: Res(b"PK", "http://example.invalid/z.zip"), "redirected away from https"),
        ("a redirect refused on the way", "https://example.invalid/z.zip",
         lambda req, timeout=None: (_ for _ in ()).throw(pt._NotHttps("redirected away from https")),
         "redirected away from https"),
        ("larger than MAX_RELEASE_ZIP", "https://example.invalid/z.zip",
         lambda req, timeout=None: Res(b"x" * 2048, req.full_url), "larger than a release zip"),
        ("an HTTP error", "https://example.invalid/z.zip",
         lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.HTTPError(req.full_url, 502, "Bad", {}, None)),
         "HTTP 502"),
        ("no network", "https://example.invalid/z.zip",
         lambda req, timeout=None: (_ for _ in ()).throw(urllib.error.URLError("name resolution")),
         "URLError: <urlopen error name resolution>"),
    )
    try:
        for label, url, opener, want_why in cases:
            asked: list = []

            def stub(req, timeout=None, _o=opener, _asked=asked):
                _asked.append(req.full_url)
                return _o(req, timeout)

            pt._urlopen = stub
            pt.MAX_RELEASE_ZIP = 1024
            dest.unlink(missing_ok=True)
            why = pt.fetch_release(url, dest)
            check(f"fetch_release, {label}: {want_why}",
                  why == want_why and (dest.read_bytes() == b"PK" * 10 if want_why == "ok" else not dest.exists())
                  and (asked == [] if url.startswith("http:") else asked == [url]), repr((why, asked)))
    finally:
        pt._urlopen, pt.MAX_RELEASE_ZIP = real, real_max

    # The redirect handling itself: urllib's machinery with stand-ins for
    # the network (an https and an http handler that answer from a table).
    import email.message
    import urllib.response

    hits: list[str] = []

    def answer(req, routes):
        hits.append(req.full_url)
        code, location, body = routes.get(req.full_url, (404, None, b""))
        msg = email.message.Message()
        if location:
            msg["Location"] = location
        res = urllib.response.addinfourl(io.BytesIO(body), msg, req.full_url, code)
        res.msg = "answer"
        return res

    def stand_ins(routes):
        class Https(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return answer(req, routes)

        class Http(urllib.request.HTTPHandler):
            def http_open(self, req):
                return answer(req, routes)

        return Https(), Http()

    real_build = pt.urllib.request.build_opener
    for label, routes, want_why, want_hits in (
        ("a redirect to another https host is followed",
         {"https://a.example/z.zip": (302, "https://b.example/z.zip", b""), "https://b.example/z.zip": (200, None, b"PK")},
         "ok", ["https://a.example/z.zip", "https://b.example/z.zip"]),
        ("a redirect to http is refused before http is asked, even one that would come back to https",
         {"https://a.example/z.zip": (302, "http://b.example/z.zip", b""),
          "http://b.example/z.zip": (302, "https://evil.example/z.zip", b""),
          "https://evil.example/z.zip": (200, None, b"PK")},
         "redirected away from https", ["https://a.example/z.zip"]),
    ):
        hits.clear()
        pt.urllib.request.build_opener = lambda *h, _r=routes: real_build(*h, *stand_ins(_r))
        try:
            dest.unlink(missing_ok=True)
            why = pt.fetch_release("https://a.example/z.zip", dest)
        finally:
            pt.urllib.request.build_opener = real_build
        check(f"fetch_release, {label}", why == want_why and hits == want_hits, repr((why, hits)))
    hits.clear()
    pt.urllib.request.build_opener = lambda *h: real_build(*h, *stand_ins(
        {"https://site.example/timetables.json": (301, "http://site.example/timetables.json", b"")}))
    try:
        pt.Source(url="https://site.example").fetch("timetables.json", tmp / "m.json", 1024)
        raised = ""
    except pt.FetchError as err_:
        raised = str(err_)
    finally:
        pt.urllib.request.build_opener = real_build
    check("the live site's files are not followed to http either",
          raised == "timetables.json: redirected away from https" and hits == ["https://site.example/timetables.json"],
          repr((raised, hits)))


# ---- timetables.json.sig ---------------------------------------------------

with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)
    # RFC 8032, 7.1, TEST 2: the key, its public key and its signature over
    # the one byte 0x72.
    rfc_seed = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
    rfc_pub = bytes.fromhex("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c")
    rfc_sig = bytes.fromhex("92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
                            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00")
    msg = tmp / "msg"
    msg.write_bytes(b"\x72")
    check("ed25519 by openssl: RFC 8032's test 2 key, public key and signature",
          sm.public_key(rfc_seed) == rfc_pub and sm.sign(msg, rfc_seed) == rfc_sig and sm.verify(msg, rfc_sig, rfc_pub)
          and not sm.verify(msg, rfc_sig[:-1] + b"\x01", rfc_pub))
    check("the pinned public key is the repository's (32 bytes)",
          base64.b64encode(REAL_PUBLIC_KEY).decode() == sm.PAGES_SIGNING_PUBLIC_KEY
          == "iUfHMDrTTf+3dV7JUu8ktyihkUfpkeTQRQxym955kq8=" and len(REAL_PUBLIC_KEY) == 32)

    site = tmp / "site"
    site.mkdir()
    (site / "timetables.json").write_bytes(b'{"format": 1, "indexes": {}}\n')
    seed = os.urandom(32)
    pub = sm.public_key(seed)
    out = sm.sign_site(site, seed, pub)
    text = out.read_text(encoding="ascii")
    sig = base64.b64decode(text.strip(), validate=True)
    check("sign_site writes timetables.json.sig: one line, base64 of 64 bytes, a signature over the file's bytes",
          out == site / "timetables.json.sig" and text.endswith("\n") and text.count("\n") == 1 and len(sig) == 64
          and sm.verify(site / "timetables.json", sig, pub) and not sm.verify(site / "timetables.json", sig, REAL_PUBLIC_KEY))
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        print("skip the cross-check with cryptography (not installed)")
    else:
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, (site / "timetables.json").read_bytes())
            crossed = True
        except Exception:  # noqa: BLE001
            crossed = False
        check("the signature verifies with the cryptography package too", crossed)
    (site / "timetables.json").write_bytes(b'{"format": 1, "indexes": {"x": 1}}\n')
    check("a changed manifest does not verify", not sm.verify(site / "timetables.json", sig, pub))

    def sign_cli(env_key: str | None, folder: Path = site) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if k != sm.KEY_ENV}
        if env_key is not None:
            env[sm.KEY_ENV] = env_key
        done = subprocess.run([sys.executable, str(HERE / "sign_manifest.py"), "sign", "--site", str(folder)],
                              capture_output=True, text=True, timeout=60, env=env)
        return done.returncode, done.stdout + done.stderr

    stale = site / "timetables.json.sig"
    for label, key, want_text in (
        ("no PAGES_SIGNING_KEY", None, "PAGES_SIGNING_KEY is not set"),
        ("an empty one", "", "PAGES_SIGNING_KEY is not set"),
        ("one that is not base64", "not*base64!", "is not base64"),
        ("one of 31 bytes", base64.b64encode(os.urandom(31)).decode(), "decodes to 31 bytes"),
        ("a key that is not the pinned public key's", base64.b64encode(seed).decode(),
         "is not the key of the pinned public key"),
    ):
        stale.write_text("stale\n")
        rc, text = sign_cli(key)
        check(f"sign refuses {label}: exit 1, an error, no .sig left (a stale one removed), the key not echoed",
              rc == 1 and "::error title=timetables.json not signed::" in text and want_text in text
              and not stale.exists() and (not key or key not in text), repr((rc, text)))
    rc, text = sign_cli(base64.b64encode(seed).decode(), tmp / "no-site")
    check("sign refuses a folder without timetables.json", rc == 1 and "is not there" in text, text)
    # The CLI with the pinned key cannot run here (only the secret has it);
    # sign_site with its public key is the same path, and verify reads it.
    sm.sign_site(site, seed, pub)
    done = subprocess.run([sys.executable, str(HERE / "sign_manifest.py"), "verify", "--site", str(site)],
                          capture_output=True, text=True, timeout=60)
    check("verify refuses a signature by another key than the pinned one", done.returncode == 1, done.stdout)
    check("the .sig as written reads back and verifies with the test key's public key",
          sm.verify(site / "timetables.json", sm.read_sig(stale), pub))

    # mav-index.json is signed the same way when the site has it.
    (site / "mav-index.json").write_bytes(b'{"format": 1, "feed": "20261101"}\n')
    sm.sign_site(site, seed, pub)
    mav_sig = sm.read_sig(site / "mav-index.json.sig")
    check("sign_site writes mav-index.json.sig too: one line, base64 of 64 bytes, a signature over its bytes",
          (site / "mav-index.json.sig").read_text().count("\n") == 1 and len(mav_sig) == 64
          and sm.verify(site / "mav-index.json", mav_sig, pub)
          and not sm.verify(site / "timetables.json", mav_sig, pub)
          and sm.verify(site / "timetables.json", sm.read_sig(stale), pub))
    real_sign = sm.sign

    def fail_mav(data: Path, key: bytes) -> bytes:
        if data.name == "mav-index.json":
            raise sm.SignError("openssl did not sign: test")
        return real_sign(data, key)

    sm.sign = fail_mav
    try:
        sm.sign_site(site, seed, pub)
        raised = ""
    except sm.SignError as err_:
        raised = str(err_)
    finally:
        sm.sign = real_sign
    check("a mav-index.json that cannot be signed: SignError, and no .sig of either manifest left",
          "test" in raised and not (site / "timetables.json.sig").exists()
          and not (site / "mav-index.json.sig").exists(), raised)

    # The CLI as the sign job runs it, the tests' key pinned, through an
    # openssl that writes down its environment: the key reaches it on stdin
    # only, never in its environment.
    bin_dir = tmp / "openssl-bin"
    bin_dir.mkdir()
    env_log = tmp / "openssl-env.log"
    fake = bin_dir / "openssl"
    fake.write_text(f"#!/bin/sh\nenv >> \"$OPENSSL_ENV_LOG\"\necho --- >> \"$OPENSSL_ENV_LOG\"\n"
                    f"exec {shutil.which('openssl')} \"$@\"\n")
    fake.chmod(0o755)
    sign_wrap = ("import os, sys; sys.path.insert(0, sys.argv.pop(1)); import sign_manifest;"
                 " sign_manifest.PUBLIC_KEY = bytes.fromhex(os.environ['TEST_PAGES_PUBLIC_KEY']);"
                 " raise SystemExit(sign_manifest.main(sys.argv[1:]))")
    key_text = base64.b64encode(TEST_SEED).decode()

    def pinned_cli(*args: str, key: str | None = key_text) -> tuple[int, str]:
        env = {k: v for k, v in os.environ.items() if k != sm.KEY_ENV}
        env.update({"PATH": f"{bin_dir}:{env.get('PATH', '')}", "OPENSSL_ENV_LOG": str(env_log),
                    "TEST_PAGES_PUBLIC_KEY": TEST_PUB.hex()})
        if key is not None:
            env[sm.KEY_ENV] = key
        done = subprocess.run([sys.executable, "-c", sign_wrap, str(HERE), *args], capture_output=True, text=True,
                              timeout=60, env=env)
        return done.returncode, done.stdout + done.stderr

    rc, text = pinned_cli("sign", "--site", str(site))
    logged = env_log.read_text() if env_log.is_file() else ""
    check("sign with the pinned key: both manifests signed, each .sig verifying; openssl ran without"
          " PAGES_SIGNING_KEY in its environment, and the key is not printed",
          rc == 0 and "timetables.json.sig: signed" in text and "mav-index.json.sig: signed" in text
          and sm.verify(site / "timetables.json", sm.read_sig(site / "timetables.json.sig"), TEST_PUB)
          and sm.verify(site / "mav-index.json", sm.read_sig(site / "mav-index.json.sig"), TEST_PUB)
          and logged.count("---") >= 5 and "OPENSSL_ENV_LOG=" in logged and sm.KEY_ENV not in logged
          and key_text not in logged and key_text not in text, repr((rc, text, logged.count("---"))))
    rc, text = pinned_cli("verify", "--site", str(site), key=None)
    check("verify with the pinned key: both signatures good", rc == 0 and text.count("good signature") == 2, text)
    (site / "mav-index.json").write_bytes(b'{"format": 1, "feed": "20261102"}\n')
    rc, text = pinned_cli("verify", "--site", str(site), key=None)
    check("verify fails when mav-index.json's signature does not verify", rc == 1
          and "mav-index.json.sig: no good signature" in text, text)
    (site / "mav-index.json").unlink()
    (site / "mav-index.json.sig").unlink()
    rc, text = pinned_cli("verify", "--site", str(site), key=None)
    check("verify of a site without mav-index.json checks timetables.json only", rc == 0
          and text.count("good signature") == 1, text)

    # _openssl itself: KEY_ENV is in this process's environment, not in the
    # child's.
    env_log.unlink()
    saved = {k: os.environ.get(k) for k in (sm.KEY_ENV, "PATH", "OPENSSL_ENV_LOG")}
    os.environ.update({sm.KEY_ENV: "c2VjcmV0", "PATH": f"{bin_dir}:{saved['PATH']}", "OPENSSL_ENV_LOG": str(env_log)})
    try:
        done = sm._openssl(["version"])
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    logged = env_log.read_text() if env_log.is_file() else ""
    check("_openssl passes its environment without PAGES_SIGNING_KEY",
          done.returncode == 0 and "OPENSSL_ENV_LOG=" in logged and sm.KEY_ENV not in logged
          and "c2VjcmV0" not in logged, logged[:300])

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("Pages timetables tests passed")
