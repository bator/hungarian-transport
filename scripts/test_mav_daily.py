#!/usr/bin/env python3
"""The daily MÁV index on GitHub Pages: scripts/mav_pages_site.py (manifest,
deploy or skip by content digest, refusals) and mav-index-daily.yml's steps
as written there (run: blocks executed with bash against stubs: the build's
warning when it fails, the Pages URL, the "Pages enabled" check against a
stub gh), plus what the workflow text must hold (triggers, permissions per
job, pinned actions, deploy only after a site was made). No network."""
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORKFLOW = ROOT / ".github" / "workflows" / "mav-index-daily.yml"
TEXT = WORKFLOW.read_text(encoding="utf-8")
sys.path.insert(0, str(HERE))

import index_digest  # noqa: E402
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
    live.write_text(json.dumps({**man, "sha256": "f" * 64, "built_at": "2026-10-31T04:40:00Z"}))
    rc, out, err, outputs = run_site(tmp / "b", "--live-file", str(live))
    check("the same content digest as the published manifest: no deploy, with a notice",
          rc == 0 and outputs.get("deploy") == "false" and "::notice title=Daily MÁV index unchanged::" in out,
          repr((rc, out, err, outputs)))

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

    live.write_text(json.dumps({**gman, "built_at": "2026-10-31T03:20:00Z"}))
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
        'echo idx > mav-index.json.gz\necho "stub: pfaedle failed" >&2\nexit 1\n')
    done, outputs = bash(build_step, ws, STUB_MAV="fail")
    check("a failed build: warning, no index left, built not set, the job goes on",
          done.returncode == 0 and "::warning title=Daily MÁV index not built::" in done.stdout
          and "built" not in outputs and not (ws / "mav-index.json.gz").exists(),
          repr((done.returncode, done.stdout, done.stderr, outputs)))
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
for ref in ("actions/upload-pages-artifact@", "actions/deploy-pages@"):
    check(f"{ref.rstrip('@')} is used", ref in TEXT)

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("daily MÁV index tests passed")
