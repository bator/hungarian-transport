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
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gtfs_compact  # noqa: E402
import mav_pages_site  # noqa: E402
import pages_timetables as pt  # noqa: E402
from gtfs_feed import encode_polyline  # noqa: E402

os.environ.pop("MAV_ALLOW_SHRINK", None)
NOW = datetime(2026, 11, 16, 10, 40, tzinfo=timezone.utc)
# The index schema passed as --schema (the stable release's, in the workflow).
SCHEMA = gtfs_compact.INDEX_SCHEMA
S = str(SCHEMA)
COMMIT = "c" * 40

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


def run(*args: str) -> tuple[int, str, str, dict]:
    out_file = Path(tempfile.mkstemp(prefix="gh-out-")[1])
    done = subprocess.run([sys.executable, str(HERE / "pages_timetables.py"), *args,
                           *(["--github-output", str(out_file)] if args[0] in ("due", "assemble") else [])],
                          capture_output=True, text=True, timeout=120)
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

    src = Settling(live)
    ns = argparse.Namespace(out=tmp / "site-settle", commit=COMMIT, mav_part=None, timetables_part=tmp / "p2",
                            max_bytes=mav_pages_site.MAX_SITE_BYTES, settle=0.0, github_output=None, schema=None)
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

    real = pt.urllib.request.urlopen
    pt.urllib.request.urlopen = fake_urlopen
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
        pt.urllib.request.urlopen = real
    try:
        pt.Source(url="http://bator.github.io/hungarian-transport")
        ok = False
    except ValueError:
        ok = True
    check("the live site is only an https URL", ok)

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("Pages timetables tests passed")
