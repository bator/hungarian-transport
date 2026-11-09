#!/usr/bin/env python3
"""The daily MÁV index on GitHub Pages: scripts/mav_pages_site.py (manifest,
deploy or skip by content digest and the published built_at's age,
refusals: a feed date that is no date, a site over the size limit; the
overlay's deflate level),
scripts/mav_daily_verdict.py (when a run that published nothing goes red)
and mav-index-daily.yml's steps as written there (run: blocks executed with
bash against stubs: the build's warning and failure kind when it fails, the
Pages URL, the published index's age check, the "Pages enabled" check
against a stub gh), plus what the workflow text must hold (triggers,
permissions per job, pinned actions, deploy only after a site was made). No
network."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKFLOW = ROOT / ".github" / "workflows" / "mav-index-daily.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
sys.path.insert(0, str(HERE))

import index_digest  # noqa: E402
import mav_daily_verdict as verdict_mod  # noqa: E402
import mav_pages_site as site  # noqa: E402

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        failed += 1


def step_script(name: str) -> str:
    """The run: block of the step with this name, dedented."""
    lines = TEXT.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == f"- name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for ln in lines[run + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        body.append(ln[indent:])
    return "\n".join(body) + "\n"


def job(name: str) -> str:
    """The block of job name under jobs:."""
    lines = TEXT.splitlines()
    start = lines.index(f"  {name}:")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^  \S", lines[i]) or re.match(r"^\S", lines[i])),
               len(lines))
    return "\n".join(lines[start:end])


def step(block: str, marker: str) -> str:
    lines = block.splitlines()
    at = next((i for i, ln in enumerate(lines) if marker in ln), None)
    if at is None:
        return ""
    start = next(i for i in range(at, -1, -1) if lines[i].lstrip().startswith("- "))
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= indent), len(lines))
    return "\n".join(lines[start:end])


def index_bytes(feed: str = "20261101.M2ChangeID", schema: int | None = None, trains: int = 60) -> bytes:
    data = {
        "v": site.mav_index.SCHEMA if schema is None else schema,
        "feed": feed,
        "st": [["A", 4700000, 1900000], ["B", 4710000, 1910000]],
        "p": ["_p~iF~ps|U_ulLnnqC"],
        "n": {str(100 + k): [[0, [0, 1]]] for k in range(trains)},
        "l": {},
    }
    return gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0)


def stats(feed: str = "20261101.M2ChangeID") -> dict:
    return {
        "feed_version": feed, "trains": 60, "lines": 0, "variants": 60, "fitted": 59, "fitted_share": 0.9833,
        "audit": {"trips": 900, "trips_bad": 2, "trains": 60, "trains_ok": 60, "ok_ratio": 1.0, "tolerance_m": 300.0},
    }


def built_ago(age: timedelta) -> str:
    """A manifest built_at age ago (the site's own built_at is now)."""
    when = datetime.now(timezone.utc).replace(microsecond=0) - age
    return when.isoformat().replace("+00:00", "Z")


def integration_timedelta(module: str, name: str) -> timedelta:
    """An integration module's `NAME = timedelta(hours=N)` (or days=N), read
    from the source (it imports Home Assistant); 0 when it is not there."""
    src = (ROOT / "custom_components" / "hungarian_transport" / module).read_text(encoding="utf-8")
    m = re.search(rf"^{name} = timedelta\((hours|days)=(\d+)\)$", src, re.M)
    return timedelta(**{m.group(1): int(m.group(2))}) if m else timedelta(0)


def gtfs_zip(path: Path, feed: str = "20261101.M2ChangeID") -> Path:
    """A MÁV-like zip (stored, as MÁV's own writer would not matter here)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("feed_info.txt", "\ufefffeed_publisher_name,feed_version,feed_start_date,feed_end_date\n"
                   f"MÁV-START,{feed},20261101,20261231\n")
        z.writestr("routes.txt", "route_id,route_short_name\nr1,S10\n")
        z.writestr("trips.txt", "route_id,service_id,trip_id\nr1,s1,t1\n")
    return path


def pfaedle_dir(path: Path, stamp: int = 0) -> Path:
    """pfaedle's output folder: its three tables plus MÁV's other ones."""
    path.mkdir(parents=True, exist_ok=True)
    tables = {
        "trips.txt": "route_id,service_id,trip_id,shape_id\nr1,s1,t1,sh1\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nsh1,47.5,19.0,1\nsh1,47.6,19.1,2\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,shape_dist_traveled\n"
                          "t1,08:00:00,08:00:00,a,1,0\nt1,08:30:00,08:30:00,b,2,13000.5\n" * 50,
        "routes.txt": "route_id,route_short_name\nr1,S10\n",
        "feed_info.txt": "feed_publisher_name,feed_version\nMÁV-START,20261101.M2ChangeID\n",
    }
    for name, text in tables.items():
        f = path / name
        f.write_text(text, encoding="utf-8")
        os.utime(f, (1_700_000_000 + stamp, 1_700_000_000 + stamp))
        f.chmod(0o600 if stamp else 0o644)
    return path


def run_site(ws: Path, *extra: str, index: bytes | None = None, st: dict | None = None,
             commit: str = "a" * 40) -> tuple[int, str, str, dict]:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "mav-index.json.gz").write_bytes(index if index is not None else index_bytes())
    (ws / "mav-build.json").write_text(json.dumps(st if st is not None else stats()))
    out_file = ws / "github_output"
    out_file.write_text("")
    done = subprocess.run(
        [sys.executable, str(HERE / "mav_pages_site.py"), "--index", str(ws / "mav-index.json.gz"),
         "--stats", str(ws / "mav-build.json"), "--commit", commit, "--out", str(ws / "site"),
         "--github-output", str(out_file), *extra],
        capture_output=True, text=True, timeout=60)
    outputs = dict(ln.split("=", 1) for ln in out_file.read_text().splitlines() if "=" in ln)
    return done.returncode, done.stdout, done.stderr, outputs


def bash(script: str, cwd: Path, path_prefix: Path | None = None, **env: str) -> tuple[subprocess.CompletedProcess, dict]:
    out = cwd / f"github_output_{len(list(cwd.glob('github_output_*')))}"
    out.write_text("")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("MAV_", "GITHUB_", "GH_"))}
    e.update({"GITHUB_OUTPUT": str(out), "RUNNER_TEMP": str(cwd / "runner-temp")})
    (cwd / "runner-temp").mkdir(exist_ok=True)
    if path_prefix is not None:
        e["PATH"] = f"{path_prefix}:{e.get('PATH', '')}"
    e.update(env)
    path = cwd / "step.sh"
    path.write_text(script)
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
                          cwd=cwd, env=e, capture_output=True, text=True, timeout=60)
    outputs = dict(ln.split("=", 1) for ln in out.read_text().splitlines() if "=" in ln)
    return done, outputs


with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)

    # ---- mav_pages_site.py ----------------------------------------------
    rc, out, err, outputs = run_site(tmp / "a")
    man_path = tmp / "a" / "site" / "mav-index.json"
    check("a good index makes a site and is deployed without a published manifest",
          rc == 0 and outputs.get("deploy") == "true" and man_path.is_file()
          and (tmp / "a" / "site" / "mav-index.json.gz").read_bytes() == index_bytes(), repr((rc, out, err)))
    man = json.loads(man_path.read_text()) if man_path.is_file() else {}
    raw = index_bytes()
    check("the manifest names format, schema, feed, size, sha256, digest, audit and commit",
          man.get("format") == site.MANIFEST_FORMAT and man.get("schema") == site.mav_index.SCHEMA
          and man.get("feed") == "20261101.M2ChangeID" and man.get("size") == len(raw)
          and man.get("sha256") == hashlib.sha256(raw).hexdigest()
          and man.get("digest") == index_digest.digest(tmp / "a" / "mav-index.json.gz")
          and man.get("audit") == {"trains": 60, "trains_ok": 60, "trips": 900, "trips_ok": 898, "ok_ratio": 1.0}
          and man.get("trains") == 60 and man.get("lines") == 0 and man.get("commit") == "a" * 40
          and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", man.get("built_at", "")) is not None
          and man.get("url") == "mav-index.json.gz", repr(man))
    check("no credentials or build paths in the manifest",
          not any(s in man_path.read_text() for s in ("netrc", "PASSWORD", "/tmp", "runner")) if man else False)

    live = tmp / "live.json"
    live.write_text(json.dumps({**man, "sha256": "f" * 64, "built_at": built_ago(timedelta(hours=2))}))
    rc, out, err, outputs = run_site(tmp / "b", "--live-file", str(live))
    check("the same content digest as a manifest published 2 h ago: no deploy, with a notice",
          rc == 0 and outputs.get("deploy") == "false" and "::notice title=Daily MÁV index unchanged::" in out,
          repr((rc, out, err, outputs)))
    # Unchanged content is deployed again once the published built_at is a
    # day's run old: the verdict and the integration read built_at as the
    # last build, so a feed MÁV leaves alone must not look like a stale index.
    for n, (label, when) in enumerate((("yesterday's run (26 h ago)", built_ago(timedelta(hours=26))),
                                       ("REFRESH_AFTER ago", built_ago(site.REFRESH_AFTER)),
                                       ("no valid time", "yesterday"))):
        live.write_text(json.dumps({**man, "sha256": "f" * 64, "built_at": when}))
        rc, out, err, outputs = run_site(tmp / f"b-old-{n}", "--live-file", str(live))
        rman = tmp / f"b-old-{n}" / "site" / "mav-index.json"
        check(f"the same content, published manifest built {label}: deployed again with a new built_at",
              rc == 0 and outputs.get("deploy") == "true" and "refresh" in out
              and rman.is_file() and json.loads(rman.read_text()).get("built_at") != when,
              repr((rc, out, err, outputs)))
    # Every reader of built_at: the verdict, the integration's manifest
    # check, and its "the daily workflow has stopped" notification.
    stale = (verdict_mod.STALE_AFTER, integration_timedelta("mav_daily.py", "STALE_MANIFEST"),
             integration_timedelta("timetable_notify.py", "STALE_PAGES"))
    check("an unchanged index is deployed again well before the verdict or the integration calls it stale",
          timedelta(hours=1) < site.REFRESH_AFTER < min(stale) < timedelta(days=1) + site.REFRESH_AFTER,
          repr((site.REFRESH_AFTER, stale)))

    for label, changed in (("digest", {"digest": "0" * 64}), ("format", {"format": 0}), ("schema", {"schema": 1})):
        live.write_text(json.dumps({**man, **changed}))
        rc, out, err, outputs = run_site(tmp / f"c-{label}", "--live-file", str(live))
        check(f"a published manifest with another {label}: deploy", rc == 0 and outputs.get("deploy") == "true",
              repr((rc, out, err)))

    live.write_text("<html>404</html>")
    rc, out, err, outputs = run_site(tmp / "d", "--live-file", str(live))
    check("an unreadable published manifest never stops a deploy",
          rc == 0 and outputs.get("deploy") == "true" and "deploying" in out, repr((rc, out, err)))
    check("an http:// live URL is not fetched", site.fetch_live("http://example.invalid/m.json")[0] is None)

    refusals = {
        "another schema": {"index": index_bytes(schema=site.mav_index.SCHEMA + 1)},
        "statistics of another feed": {"st": stats("20261031.M2ChangeID")},
        "no audit": {"st": {**stats(), "audit": None}},
        "too few trains": {"index": index_bytes(trains=3)},
        "undated feed": {"index": index_bytes(feed="latest"), "st": stats("latest")},
        # FEED_RE passes it; the integration's mav._gtfs_date does not, so
        # every client would reject the manifest that replaced a good one.
        "a feed date that is no date (month 13, day 99)": {"index": index_bytes(feed="20261399.M2ChangeID"),
                                                           "st": stats("20261399.M2ChangeID")},
        "a feed dated 30 February": {"index": index_bytes(feed="20260230.M2ChangeID"),
                                     "st": stats("20260230.M2ChangeID")},
        "a feed that could break a workflow output": {"index": index_bytes(feed="20261101\n::x"),
                                                      "st": stats("20261101\n::x")},
        "not a commit": {"commit": "main"},
        "not gzip": {"index": b"<html>"},
    }
    for label, kw in refusals.items():
        ws = tmp / f"refuse-{len(label)}-{label[:6].replace(' ', '_')}"
        rc, out, err, outputs = run_site(ws, **kw)
        check(f"refused, no site, no deploy: {label}",
              rc == 1 and "deploy" not in outputs and not (ws / "site").exists() and "no site:" in err,
              repr((rc, out, err, outputs)))

    # ---- the GTFS files: official zip, pfaedle overlay ---------------------
    pf_a = pfaedle_dir(tmp / "pf-a")
    pf_b = pfaedle_dir(tmp / "pf-b", stamp=86400)
    ov_a, ov_b = tmp / "ov-a.zip", tmp / "ov-b.zip"
    names = site.write_overlay(pf_a, ov_a)
    site.write_overlay(pf_b, ov_b)
    check("the overlay is deterministic: the same tables (other mtimes, modes, folder) give the same bytes",
          ov_a.read_bytes() == ov_b.read_bytes())
    site.write_overlay(pf_a, ov_b)
    check("the overlay is the same bytes when written again", ov_a.read_bytes() == ov_b.read_bytes())
    with zipfile.ZipFile(ov_a) as z:
        infos = z.infolist()
        check("the overlay holds exactly pfaedle's trips, shapes and stop_times, sorted, deflated, fixed time",
              names == ["shapes.txt", "stop_times.txt", "trips.txt"]
              and [i.filename for i in infos] == names
              and all(i.compress_type == zipfile.ZIP_DEFLATED and i.date_time == (1980, 1, 1, 0, 0, 0)
                      and (i.external_attr >> 16) == 0o100644 for i in infos)
              and z.read("stop_times.txt") == (pf_a / "stop_times.txt").read_bytes()
              and z.testzip() is None, repr([(i.filename, i.date_time, i.compress_type) for i in infos]))
    # The overlay deflates at ZIP_LEVEL (9), not zlib's default 6: z.open()
    # with a ZipInfo ignores the ZipFile's compresslevel. Tables where the
    # two levels differ, as MÁV's real ones do.
    pf_l = pfaedle_dir(tmp / "pf-level")
    rows = [f"t{k % 977},{k * 7919 % 100003},{'abcdefgh'[k % 8] * (1 + k * 31 % 9)},47.{k * 104729 % 99991}"
            for k in range(40000)]
    for name in site.OVERLAY_FILES:
        (pf_l / name).write_text("\n".join(rows) + "\n", encoding="utf-8")
    ov_l = tmp / "ov-level.zip"
    site.write_overlay(pf_l, ov_l)

    def deflated(raw: bytes, level: int) -> int:
        c = zlib.compressobj(level, zlib.DEFLATED, -15)
        return len(c.compress(raw) + c.flush())

    with zipfile.ZipFile(ov_l) as z:
        sizes = [(i.compress_size, deflated((pf_l / i.filename).read_bytes(), 9),
                  deflated((pf_l / i.filename).read_bytes(), 6)) for i in z.infolist()]
    check("the overlay is deflated at level 9 (the test tables differ between level 6 and 9)",
          bool(sizes) and all(got == l9 != l6 for got, l9, l6 in sizes), repr(sizes))
    ov_l2 = tmp / "ov-level-2.zip"
    site.write_overlay(pf_l, ov_l2)
    check("the level-9 overlay is deterministic", ov_l.read_bytes() == ov_l2.read_bytes())
    check("dated_feed takes a real date only, as the integration does",
          site.dated_feed("20261101.M2ChangeID") and site.dated_feed("20240229")
          and not any(site.dated_feed(v) for v in ("20261399.M2ChangeID", "20250229", "20261100", "latest", None, 2026)))

    (pf_b / "shapes.txt").write_text("shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nsh1,47.5,19.0,1\n")
    site.write_overlay(pf_b, ov_b)
    check("another shapes.txt gives other overlay bytes", ov_a.read_bytes() != ov_b.read_bytes())

    official = gtfs_zip(tmp / "dl" / "gtfs" / "mav-gtfs.zip")
    gtfs_args = ("--gtfs-zip", str(official), "--pfaedle-dir", str(pf_a))
    rc, out, err, outputs = run_site(tmp / "g", *gtfs_args)
    gsite = tmp / "g" / "site"
    gman = json.loads((gsite / "mav-index.json").read_text()) if (gsite / "mav-index.json").is_file() else {}
    g = gman.get("gtfs") or {}
    check("with the GTFS files: deployed, the site has every file",
          rc == 0 and outputs.get("deploy") == "true"
          and sorted(p.name for p in gsite.iterdir()) == sorted(
              ["ATTRIBUTION.txt", "index.html", "mav-gtfs.zip", "mav-index.json", "mav-index.json.gz",
               "mav-pfaedle-overlay.zip"]), repr((rc, out, err, sorted(p.name for p in gsite.iterdir()))))
    check("the official zip is published byte for byte", (gsite / "mav-gtfs.zip").read_bytes() == official.read_bytes())
    check("the published overlay is the deterministic one", (gsite / "mav-pfaedle-overlay.zip").read_bytes() == ov_a.read_bytes())
    check("the manifest's gtfs object: feed_version (= the index's feed), name, size, sha256 per file",
          g.get("feed_version") == gman.get("feed") == "20261101.M2ChangeID"
          and g.get("official") == {"name": "mav-gtfs.zip", "size": official.stat().st_size,
                                    "sha256": hashlib.sha256(official.read_bytes()).hexdigest()}
          and g.get("overlay") == {"name": "mav-pfaedle-overlay.zip", "size": ov_a.stat().st_size,
                                   "sha256": hashlib.sha256(ov_a.read_bytes()).hexdigest(),
                                   "files": ["shapes.txt", "stop_times.txt", "trips.txt"]}
          and g.get("attribution") == "ATTRIBUTION.txt", repr(g))
    check("the digests in the manifest match the published files",
          all(hashlib.sha256((gsite / g[k]["name"]).read_bytes()).hexdigest() == g[k]["sha256"]
              and (gsite / g[k]["name"]).stat().st_size == g[k]["size"] for k in ("official", "overlay")) if g else False)
    check("the manifest keeps format 1 and every field the integration reads",
          gman.get("format") == 1 == site.MANIFEST_FORMAT
          and all(gman.get(k) == man.get(k) for k in ("schema", "feed", "url", "size", "sha256", "digest")), repr(gman))
    attribution = (gsite / "ATTRIBUTION.txt").read_text(encoding="utf-8") if gsite.is_dir() else ""
    page = (gsite / "index.html").read_text(encoding="utf-8") if gsite.is_dir() else ""
    check("ATTRIBUTION.txt and index.html name MÁV CC0 and OpenStreetMap contributors (ODbL)",
          all(t in attribution for t in ("CC0", "© OpenStreetMap contributors", "ODbL", "mav-gtfs.zip",
                                         "mav-pfaedle-overlay.zip", "pfaedle"))
          and all(t in page for t in ("CC0", "OpenStreetMap contributors", "ODbL", 'href="mav-gtfs.zip"',
                                      'href="mav-pfaedle-overlay.zip"', 'href="ATTRIBUTION.txt"')), attribution)
    check("no build paths in the published text files",
          not any(str(tmp) in t for t in (attribution, page, json.dumps(gman))))

    live.write_text(json.dumps({**gman, "built_at": built_ago(timedelta(hours=3))}))
    rc, out, err, outputs = run_site(tmp / "g-same", *gtfs_args, "--live-file", str(live))
    check("the same index and GTFS files as published: no deploy",
          rc == 0 and outputs.get("deploy") == "false", repr((rc, out, err, outputs)))
    for label, changed in (("official zip", "official"), ("overlay", "overlay")):
        live.write_text(json.dumps({**gman, "gtfs": {**g, changed: {**g[changed], "sha256": "e" * 64}}}))
        rc, out, err, outputs = run_site(tmp / f"g-{changed}", *gtfs_args, "--live-file", str(live))
        check(f"only the {label} changed: deploy", rc == 0 and outputs.get("deploy") == "true"
              and "other GTFS files" in out, repr((rc, out, err, outputs)))
    live.write_text(json.dumps(man))
    rc, out, err, outputs = run_site(tmp / "g-first", *gtfs_args, "--live-file", str(live))
    check("a published manifest without GTFS files: deploy", rc == 0 and outputs.get("deploy") == "true",
          repr((rc, out, err, outputs)))

    gtfs_refusals = {
        "a MÁV zip of another feed": ("--gtfs-zip", str(gtfs_zip(tmp / "other.zip", "20261031.M2ChangeID")),
                                      "--pfaedle-dir", str(pf_a)),
        "pfaedle output without shapes.txt": ("--gtfs-zip", str(official), "--pfaedle-dir",
                                              str(pfaedle_dir(tmp / "pf-noshapes"))),
        "not a zip": ("--gtfs-zip", str(tmp / "live.json"), "--pfaedle-dir", str(pf_a)),
    }
    (tmp / "pf-noshapes" / "shapes.txt").unlink()
    for label, args_ in gtfs_refusals.items():
        ws = tmp / f"grefuse-{label[:8].replace(' ', '_')}"
        rc, out, err, outputs = run_site(ws, *args_)
        check(f"refused, no site, no deploy: {label}",
              rc == 1 and "deploy" not in outputs and not (ws / "site").exists() and "no site:" in err,
              repr((rc, out, err, outputs)))
    # The size guard: the sizes are printed either way; over the limit, no site.
    total = sum(p.stat().st_size for p in gsite.iterdir())
    rc, out, err, outputs = run_site(tmp / "g-default-limit", *gtfs_args)
    check("the site's size and every file's size are printed, with the 500 MB limit",
          rc == 0 and f"site: {total} bytes (limit {500 * 1024 * 1024})" in out
          and all(f"{n} " in out for n in ("mav-gtfs.zip", "mav-pfaedle-overlay.zip", "mav-index.json.gz")),
          repr((rc, out, err)))
    rc, out, err, outputs = run_site(tmp / "g-big", *gtfs_args, "--max-bytes", str(total - 1))
    check("a site over the size limit is refused: no site, no deploy, the sizes in the error",
          rc == 1 and "deploy" not in outputs and not (tmp / "g-big" / "site").exists()
          and "no site:" in err and f"would be {total} bytes, over the {total - 1} byte limit" in err
          and "mav-gtfs.zip " in err, repr((rc, out, err, outputs)))
    rc, out, err, outputs = run_site(tmp / "g-limit", *gtfs_args, "--max-bytes", str(total))
    check("a site of exactly the limit is published", rc == 0 and outputs.get("deploy") == "true",
          repr((rc, out, err)))
    check("ATTRIBUTION.txt cites MÁV-csoport as the source of the GTFS",
          "Source: MÁV-csoport" in attribution and "https://www.mavcsoport.hu/gtfs" in attribution, attribution)

    rc, out, err, outputs = run_site(tmp / "g-half", "--gtfs-zip", str(official))
    check("--gtfs-zip without --pfaedle-dir is a usage error", rc == 2 and not (tmp / "g-half" / "site").exists(),
          repr((rc, err)))

    # ---- the workflow's steps, as written --------------------------------
    build_step = step_script("MÁV GTFS, pfaedle, build and audit")
    ws = tmp / "wf-build"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "release_mav_index.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'if [ "${STUB_MAV:-}" = ok ]; then echo idx > mav-index.json.gz; echo "{}" > mav-build.json; exit 0; fi\n'
        'echo idx > mav-index.json.gz\n'
        'if [ "${STUB_MAV:-}" = auth ]; then echo "stub: HTTP 401" >&2; exit 77; fi\n'
        'echo "stub: pfaedle failed" >&2\nexit 1\n')
    done, outputs = bash(build_step, ws, STUB_MAV="fail")
    check("a failed build: warning, no index left, built not set, failure=build, the job goes on",
          done.returncode == 0 and "::warning title=Daily MÁV index not built::" in done.stdout
          and "built" not in outputs and outputs.get("failure") == "build" and not (ws / "mav-index.json.gz").exists(),
          repr((done.returncode, done.stdout, done.stderr, outputs)))
    done, outputs = bash(build_step, ws, STUB_MAV="auth")
    check("a refused MÁV login (exit 77): failure=auth, a warning naming the login, the job goes on",
          done.returncode == 0 and outputs.get("failure") == "auth" and "built" not in outputs
          and "MÁV refused the login" in done.stdout and not (ws / "mav-index.json.gz").exists(),
          repr((done.returncode, done.stdout, done.stderr, outputs)))
    check("the workflow's auth exit status is the verdict's AUTH_EXIT",
          f'"$rc" -eq {verdict_mod.AUTH_EXIT}' in build_step
          and f"exit {verdict_mod.AUTH_EXIT} ;;" in (HERE / "release_mav_index.sh").read_text(), build_step)
    done, outputs = bash(build_step, ws, STUB_MAV="ok")
    check("a good build sets built=true", done.returncode == 0 and outputs.get("built") == "true",
          repr((done.returncode, done.stdout, done.stderr)))

    site_step = step_script("Pages site")
    ws = tmp / "wf-site"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "mav_pages_site.py").write_text(
        "import os, sys\nopen('args.txt', 'w').write('\\n'.join(sys.argv[1:]))\n"
        "if os.environ.get('STUB_SITE') != 'ok':\n    sys.exit(1)\n"
        "os.makedirs(sys.argv[sys.argv.index('--out') + 1], exist_ok=True)\n"
        "out = sys.argv[sys.argv.index('--github-output') + 1]\nopen(out, 'a').write('deploy=true\\n')\n")
    done, outputs = bash(site_step, ws, STUB_SITE="ok", GITHUB_REPOSITORY="Bator/hungarian-transport",
                         GITHUB_REPOSITORY_OWNER="Bator", GITHUB_SHA="b" * 40)
    args = (ws / "args.txt").read_text().splitlines() if (ws / "args.txt").exists() else []
    check("the Pages site step asks the repository's own Pages URL (owner lowercased) and passes deploy on",
          done.returncode == 0 and outputs.get("deploy") == "true"
          and "https://bator.github.io/hungarian-transport/mav-index.json" in args and "b" * 40 in args,
          repr((done.returncode, done.stderr, args, outputs)))
    rt = str(ws / "runner-temp")
    check("the Pages site step passes the build's MÁV zip and pfaedle output from release_mav_index.sh's work dir",
          args[args.index("--gtfs-zip") + 1:args.index("--gtfs-zip") + 2] == [f"{rt}/mav/gtfs/mav-gtfs.zip"]
          and args[args.index("--pfaedle-dir") + 1:args.index("--pfaedle-dir") + 2] == [f"{rt}/mav/gtfs-out"]
          if "--gtfs-zip" in args and "--pfaedle-dir" in args else False, repr(args))
    done, outputs = bash(site_step, ws, STUB_SITE="fail", GITHUB_REPOSITORY="bator/hungarian-transport",
                         GITHUB_REPOSITORY_OWNER="bator", GITHUB_SHA="b" * 40)
    check("a refused site: warning, no deploy, the run stays green",
          done.returncode == 0 and "deploy" not in outputs
          and "::warning title=Daily MÁV index not published::" in done.stdout, repr((done.returncode, done.stdout)))

    # ---- mav_daily_verdict.py: a run that published nothing --------------
    now = datetime(2026, 11, 3, 3, 30, tzinfo=timezone.utc)

    def live_at(age: timedelta) -> dict:
        return {**man, "built_at": (now - age).isoformat().replace("+00:00", "Z")}

    v = verdict_mod.verdict
    for failure in ("build", "site", "secrets"):
        fail, line = v(failure, live_at(timedelta(hours=26)), "ok", now)
        check(f"{failure}: a published index 26 h old stays green, with a warning",
              not fail and line.startswith("::warning title=Daily MÁV index not published::") and "26 h ago" in line,
              line)
        fail, line = v(failure, live_at(timedelta(days=2)), "ok", now)
        check(f"{failure}: a published index exactly 2 days old fails the run",
              fail and line.startswith("::error title=Daily MÁV index is stale::") and "48 h ago" in line, line)
    fail, line = v("build", live_at(timedelta(days=9)), "ok", now)
    check("a published index 9 days old fails the run, naming its feed and build time",
          fail and "20261101.M2ChangeID" in line and "216 h ago" in line, line)
    for label, live_ in (("an unreadable", None), ("a readable", live_at(timedelta(days=9)))):
        fail, line = v("auth", live_, "HTTP 404", now)
        check(f"a refused login fails the run at once, with {label} published manifest",
              fail and line.startswith("::error title=Daily MÁV index: login refused::"), line)
    fail, line = v("build", None, "HTTP 503", now)
    check("an unreadable published manifest: age unknown, a warning, green",
          not fail and "HTTP 503" in line and line.startswith("::warning"), line)
    for bad in ({**man, "built_at": "yesterday"}, {k: x for k, x in man.items() if k != "built_at"},
                {**man, "built_at": "2026-11-01T03:20:00"}):
        fail, line = v("build", bad, "ok", now)
        check(f"a published manifest without a usable built_at fails the run: {bad.get('built_at')!r}",
              fail and "no valid built_at" in line, line)

    def run_verdict(*args_: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(HERE / "mav_daily_verdict.py"), *args_],
                              capture_output=True, text=True, timeout=60)

    live.write_text(json.dumps(live_at(timedelta(days=3))))
    done = run_verdict("--failure", "build", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI exits 1 on a stale published index",
          done.returncode == 1 and "::error title=Daily MÁV index is stale::" in done.stdout,
          repr((done.returncode, done.stdout, done.stderr)))
    live.write_text(json.dumps(live_at(timedelta(hours=5))))
    done = run_verdict("--failure", "site", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI exits 0 on a fresh published index", done.returncode == 0 and "5 h ago" in done.stdout,
          repr((done.returncode, done.stdout, done.stderr)))
    done = run_verdict("--failure", "auth", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI exits 1 on a refused login, a fresh index notwithstanding",
          done.returncode == 1 and "login refused" in done.stdout, repr((done.returncode, done.stdout)))
    done = run_verdict("--failure", "other", "--live-file", str(live))
    check("the verdict CLI refuses an unknown failure kind", done.returncode == 2, repr(done.returncode))

    age_step = step_script("Published index age")
    ws = tmp / "wf-age"
    (ws / "scripts").mkdir(parents=True)
    (ws / "scripts" / "mav_daily_verdict.py").write_text(
        "import os, sys\nopen('args.txt', 'w').write('\\n'.join(sys.argv[1:]))\n"
        "sys.exit(int(os.environ.get('STUB_RC', '0')))\n")
    age_cases = (
        ("no secrets", {"MAV_SECRETS": "false"}, "secrets"),
        ("a refused login", {"MAV_SECRETS": "true", "BUILD_FAILURE": "auth"}, "auth"),
        ("a failed build", {"MAV_SECRETS": "true", "BUILD_FAILURE": "build"}, "build"),
        ("a refused site", {"MAV_SECRETS": "true"}, "site"),
    )
    for label, env_, want in age_cases:
        for stub_rc in ("0", "1"):
            (ws / "args.txt").unlink(missing_ok=True)
            done, _ = bash(age_step, ws, STUB_RC=stub_rc, GITHUB_REPOSITORY="Bator/hungarian-transport",
                           GITHUB_REPOSITORY_OWNER="Bator", **env_)
            args = (ws / "args.txt").read_text().splitlines() if (ws / "args.txt").exists() else []
            check(f"the age step after {label}: --failure {want}, the Pages manifest, exit {stub_rc} passed on",
                  done.returncode == int(stub_rc) and args[:2] == ["--failure", want]
                  and "https://bator.github.io/hungarian-transport/mav-index.json" in args,
                  repr((done.returncode, done.stderr, args)))

    pages_step = step_script("GitHub Pages enabled")
    stub_bin = tmp / "bin"
    stub_bin.mkdir()
    (stub_bin / "gh").write_text(
        "#!/usr/bin/env bash\n"
        'case "${STUB_GH:-}" in\n'
        '  workflow) echo workflow ;;\n'
        '  legacy) echo legacy ;;\n'
        '  missing) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;\n'
        '  *) echo "gh: Server Error (HTTP 500)" >&2; exit 1 ;;\n'
        "esac\n")
    (stub_bin / "gh").chmod(0o755)
    ws = tmp / "wf-pages"
    ws.mkdir()
    cases = {
        "missing": (1, "::error title=GitHub Pages is not enabled::"),
        "legacy": (1, "::error title=GitHub Pages source is not GitHub Actions::"),
        "workflow": (0, "source GitHub Actions"),
        "error": (0, "::warning::Could not read the Pages settings"),
    }
    for gh_case, (want_rc, want_text) in cases.items():
        done, _ = bash(pages_step, ws, path_prefix=stub_bin, STUB_GH=gh_case, GH_TOKEN="x",
                       GITHUB_REPOSITORY="bator/hungarian-transport")
        check(f"Pages check, {gh_case}: exit {want_rc} with a clear message",
              done.returncode == want_rc and want_text in done.stdout, repr((done.returncode, done.stdout, done.stderr)))

# ---- the workflow text ------------------------------------------------------
on_block = TEXT.split("\non:\n", 1)[1].split("\n\n", 1)[0]
check("runs daily at 03:15 UTC and by hand, never for pull requests (secrets, deploy)",
      '- cron: "15 3 * * *"' in on_block and "workflow_dispatch:" in on_block
      and "pull_request" not in on_block and "push:" not in on_block, on_block)
top = TEXT.split("\njobs:\n", 1)[0]
check("the workflow token reads contents only",
      re.search(r"^permissions:\n  contents: read\n\n", top, re.M) is not None, top)
check("one run at a time, none cancelled half-way",
      re.search(r"^concurrency:\n  group: mav-index-daily\n  cancel-in-progress: false$", top, re.M) is not None)
build, deploy = job("build"), job("deploy")
check("both jobs have a time limit",
      re.search(r"^    timeout-minutes: \d+$", build, re.M) is not None
      and re.search(r"^    timeout-minutes: \d+$", deploy, re.M) is not None)
check("only the deploy job may write Pages and mint an OIDC token",
      "pages: write" not in build and "id-token" not in build
      and re.search(r"^    permissions:\n      pages: write\n      id-token: write\n", deploy, re.M) is not None, deploy)
check("the deploy job runs after a build that made a site, in the github-pages environment",
      "    needs: build" in deploy and "    if: needs.build.outputs.deploy == 'true'" in deploy
      and "      name: github-pages" in deploy
      and "deploy: ${{ steps.site.outputs.deploy }}" in build, deploy)
check("the deploy job checks out and runs nothing from the repository, and sees no MÁV secret",
      "actions/checkout" not in deploy and "secrets." not in deploy and "scripts/" not in deploy)
upload = step(build, "actions/upload-pages-artifact@")
check("only a site that differs is uploaded", "if: steps.site.outputs.deploy == 'true'" in upload, upload)
site_s = step(build, "- name: Pages site")
check("the site is made only from a build that went through",
      "if: steps.mav.outputs.built == 'true'" in site_s and "secrets." not in site_s, site_s)
secret_lines = [ln.strip() for ln in TEXT.splitlines() if "secrets." in ln]
check("the MÁV secrets reach only the build step's env (and the job's yes/no flag)",
      sorted(secret_lines) == sorted([
          "MAV_SECRETS: ${{ secrets.MAV_GTFS_USER != '' && secrets.MAV_GTFS_PASSWORD != '' }}",
          "MAV_GTFS_USER: ${{ secrets.MAV_GTFS_USER }}",
          "MAV_GTFS_PASSWORD: ${{ secrets.MAV_GTFS_PASSWORD }}",
      ]) and "secrets.MAV_GTFS" in step(build, "- name: MÁV GTFS, pfaedle, build and audit"), repr(secret_lines))
uses = re.findall(r"uses: (\S+)", TEXT)
check("every action is pinned to a commit sha",
      bool(uses) and all(re.fullmatch(r"[\w.-]+/[\w.-]+(/[\w.-]+)*@[0-9a-f]{40}", u) for u in uses), repr(uses))
check("the build reuses the release's MÁV pipeline and OSM cache",
      "bash scripts/release_mav_index.sh" in build and "key: osm-hungary-${{ steps.week.outputs.week }}" in build)
age_s = step(build, "- name: Published index age")
steps_order = [ln.strip() for ln in build.splitlines() if ln.strip().startswith("- ")]
check("the age check runs only when no site was made, as the build job's last step, without secrets",
      "if: steps.site.outputs.deploy == ''" in age_s and "secrets." not in age_s
      and "BUILD_FAILURE: ${{ steps.mav.outputs.failure }}" in age_s
      and steps_order[-1] == "- name: Published index age", repr((age_s, steps_order[-3:])))
checkout_s = step(build, "actions/checkout@")
check("the checkout runs without the secrets too (the age check needs the scripts)",
      bool(checkout_s) and "if:" not in checkout_s and "persist-credentials: false" in checkout_s, checkout_s)
for ref in ("actions/upload-pages-artifact@", "actions/deploy-pages@"):
    check(f"{ref.rstrip('@')} is used", ref in TEXT)

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("daily MÁV index tests passed")
