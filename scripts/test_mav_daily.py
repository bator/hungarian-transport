#!/usr/bin/env python3
"""The daily MÁV index on GitHub Pages: scripts/mav_pages_site.py (manifest,
deploy or skip by content digest and the published built_at's age,
refusals: a feed date that is no date, a site over the size limit; the
overlay's deflate level),
scripts/mav_daily_verdict.py (when a run that published nothing goes red)
and mav-index-daily.yml's steps as written there (run: blocks executed with
bash against stubs: the build's warning and failure kind when it fails, the
Pages URL, the published index's age check, the Volán and city indexes'
due check, baseline and builds (one failing builder drops only its index),
the site's assembly from the parts this run made, the "Pages enabled" check
against a stub gh, the unpacking of the deployed site for its attestation),
plus what the workflow text must hold (triggers and the schedule's bounds,
the job graph, permissions per job, where the secrets go, pinned actions,
deploy only after a site was made, the attestation in a job of its own
after the deploy). No network; scripts/test_pages_timetables.py tests the
site script itself."""
from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import http.client
import io
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
import mav_shrink_guard  # noqa: E402
import sign_manifest  # noqa: E402

# The shrink guard's override, should the developer's shell have it.
os.environ.pop(mav_shrink_guard.ALLOW_ENV, None)
# The published mav-index.json counts as published only with a signature
# by the pinned key (mav_pages_site._finish): the tests sign theirs with a
# key made here, which every mav_pages_site.py run of the tests (run_site,
# through TRUST_WRAP) takes as the pinned one.
TEST_SEED = os.urandom(32)
TEST_PUB = sign_manifest.public_key(TEST_SEED)
# A .sig that does not verify is read again SIG_SETTLE_S (11 minutes) later
# in the workflow (Pages' cache); in the tests' runs at once.
TRUST_WRAP = ("import os, sys; sys.path.insert(0, sys.argv.pop(1)); import sign_manifest;"
              " sign_manifest.PUBLIC_KEY = bytes.fromhex(os.environ['TEST_PAGES_PUBLIC_KEY']);"
              " import mav_pages_site; mav_pages_site.SIG_SETTLE_S = 0.0;"
              " raise SystemExit(mav_pages_site.main(sys.argv[1:]))")


def sign_file(path: Path, seed: bytes = TEST_SEED) -> Path:
    """path's .sig next to it, as the sign job writes it."""
    sig = path.with_name(path.name + ".sig")
    sig.write_text(base64.b64encode(sign_manifest.sign(path, seed)).decode("ascii") + "\n", encoding="ascii")
    return path


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
    """The block of job name under jobs:, empty without one."""
    lines = TEXT.splitlines()
    if f"  {name}:" not in lines:
        return ""
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
        [sys.executable, "-c", TRUST_WRAP, str(HERE), "--index", str(ws / "mav-index.json.gz"),
         "--stats", str(ws / "mav-build.json"), "--commit", commit, "--out", str(ws / "site"),
         "--github-output", str(out_file), *extra],
        capture_output=True, text=True, timeout=60, env={**os.environ, "TEST_PAGES_PUBLIC_KEY": TEST_PUB.hex()})
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
    sign_file(live)
    rc, out, err, outputs = run_site(tmp / "b", "--live-file", str(live))
    check("the same content digest as a manifest published 2 h ago: no deploy, with a notice",
          rc == 0 and outputs.get("deploy") == "false" and "::notice title=Daily MÁV index unchanged::" in out,
          repr((rc, out, err, outputs)))
    published_at = site.built_time(json.loads(live.read_text()))
    check("live_built names the published manifest's built_at (its signature verified), for the site job's"
          " --mav-built-at", outputs.get("live_built") == published_at.astimezone(timezone.utc).replace(
              microsecond=0).isoformat().replace("+00:00", "Z"), repr(outputs))
    rc, out, err, outputs = run_site(tmp / "b-nolive")
    check("live_built is empty without a published manifest", rc == 0 and outputs.get("live_built") == "",
          repr(outputs))
    # The same content counts as published only with a signature that
    # verifies: the site job carries no unsigned MÁV manifest, so this
    # build's index goes up in its place, signed.
    signed_live = live.read_bytes()
    for n, (label, how) in enumerate((
        ("no mav-index.json.sig (the first run after signing came in)",
         lambda: live.with_name(live.name + ".sig").unlink()),
        ("a mav-index.json.sig by another key", lambda: sign_file(live, os.urandom(32))),
        ("a mav-index.json changed after signing",
         lambda: live.write_bytes(signed_live.replace(b'"commit"', b'"commit" ', 1))),
        ("a mav-index.json.sig that is not base64", lambda: live.with_name(live.name + ".sig").write_text("!!\n")),
    )):
        live.write_bytes(signed_live)
        sign_file(live)
        how()
        rc, out, err, outputs = run_site(tmp / f"b-unsigned-{n}", "--live-file", str(live))
        check(f"the same content as a manifest published 2 h ago, but {label}: deployed again, signed",
              rc == 0 and outputs.get("deploy") == "true" and "not trusted" in out
              and "deploying it again, signed" in out
              # Every one of them read again once (a 404 too: Pages' cache
              # may keep the one from before the first signed deploy).
              and "reading it and its signature again" in out,
              repr((rc, out, err, outputs)))
    live.write_bytes(signed_live)
    sign_file(live)
    # Pages' CDN serving the previous deploy's .sig next to this manifest
    # (each path cached on its own, up to PAGES_MAX_AGE): read again
    # SIG_SETTLE_S later, then the same content as published, no deploy.
    good_live_sig = live.with_name(live.name + ".sig").read_bytes()
    site_waits: list[float] = []

    def site_settled(seconds: float) -> None:
        site_waits.append(seconds)
        live.with_name(live.name + ".sig").write_bytes(good_live_sig)

    for settle_args, want_deploy, want_waits in (((), "false", [site.SIG_SETTLE_S]),
                                                 (("--sig-settle", "-1"), "true", [])):
        sign_file(live, os.urandom(32))
        site_waits.clear()
        gh = tmp / "gh-cdn-site.txt"
        gh.write_text("")
        real_sleep, real_key = site.time.sleep, sign_manifest.PUBLIC_KEY
        site.time.sleep, sign_manifest.PUBLIC_KEY = site_settled, TEST_PUB
        out_buf = io.StringIO()
        (tmp / "b-cdn" / "site").mkdir(parents=True, exist_ok=True)
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(out_buf):
                rc = site.main(["--index", str(tmp / "b" / "mav-index.json.gz"), "--stats",
                                str(tmp / "b" / "mav-build.json"), "--commit", "a" * 40, "--out",
                                str(tmp / "b-cdn" / "site"), "--live-file", str(live), "--github-output", str(gh),
                                *settle_args])
        finally:
            site.time.sleep, sign_manifest.PUBLIC_KEY = real_sleep, real_key
        got_deploy = dict(ln.split("=", 1) for ln in gh.read_text().splitlines() if "=" in ln).get("deploy")
        check("the published manifest with the previous deploy's .sig at first"
              + (": read again SIG_SETTLE_S later, the same content, no deploy" if want_waits else
                 ", --sig-settle -1: not read again, deployed again"),
              rc == 0 and got_deploy == want_deploy and site_waits == want_waits,
              repr((rc, got_deploy, site_waits, out_buf.getvalue()[-500:])))
    live.write_bytes(signed_live)
    sign_file(live)
    # --live-url: the signature is the URL with .sig.
    served = {"https://pages.example/r/mav-index.json": signed_live,
              "https://pages.example/r/mav-index.json.sig": live.with_name(live.name + ".sig").read_bytes()}
    asked_urls: list[str] = []

    class Served(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def serve(req, timeout=None):
        asked_urls.append(req.full_url)
        if req.full_url not in served:
            raise site.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return Served(served[req.full_url])

    ws_url = tmp / "b-url"
    ws_url.mkdir()
    (ws_url / "mav-index.json.gz").write_bytes(index_bytes())
    (ws_url / "mav-build.json").write_text(json.dumps(stats()))
    sig_url = "https://pages.example/r/mav-index.json.sig"
    good_url_sig = served[sig_url]
    url_waits: list[float] = []
    # Pages' CDN may still answer the 404 it cached for the .sig before the
    # first signed deploy (cached404): the second read, SIG_SETTLE_S later,
    # finds the real one.
    late_sig = {"on": False}

    def url_settled(seconds: float) -> None:
        url_waits.append(seconds)
        if late_sig["on"]:
            served[sig_url] = good_url_sig

    real_urlopen, real_pub, real_sleep = site.urllib.request.urlopen, sign_manifest.PUBLIC_KEY, site.time.sleep
    site.urllib.request.urlopen, sign_manifest.PUBLIC_KEY, site.time.sleep = serve, TEST_PUB, url_settled
    try:
        results = []
        for case in ("signed", "no sig", "cached 404"):
            served.pop(sig_url, None)
            if case == "signed":
                served[sig_url] = good_url_sig
            late_sig["on"] = case == "cached 404"
            url_waits.clear()
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                rc = site.main(["--index", str(ws_url / "mav-index.json.gz"), "--stats", str(ws_url / "mav-build.json"),
                                "--commit", "a" * 40, "--out", str(ws_url / f"site-{case}"),
                                "--live-url", "https://pages.example/r/mav-index.json"])
            results.append((rc, buf.getvalue(), list(url_waits)))
    finally:
        site.urllib.request.urlopen, sign_manifest.PUBLIC_KEY, site.time.sleep = real_urlopen, real_pub, real_sleep
    check("--live-url: the published manifest's signature is read from the URL with .sig; with it, the same"
          " content is not deployed, without it (HTTP 404, also read again SIG_SETTLE_S later) it is, signed",
          results[0][0] == 0 and "deploy=false" in results[0][1] and results[0][2] == [] and results[1][0] == 0
          and "deploy=true" in results[1][1] and "no mav-index.json.sig (HTTP 404)" in results[1][1]
          and results[1][2] == [site.SIG_SETTLE_S]
          and "https://pages.example/r/mav-index.json.sig" in asked_urls, repr((results, asked_urls)))
    check("--live-url: a .sig that Pages' cache still answers 404 for (before the first signed deploy) is read"
          " again once, SIG_SETTLE_S later: the real one then, the same content, no deploy",
          results[2][0] == 0 and "deploy=false" in results[2][1] and results[2][2] == [site.SIG_SETTLE_S],
          repr(results[2]))
    # Unchanged content is deployed again once the published built_at is a
    # day's run old: the verdict and the integration read built_at as the
    # last build, so a feed MÁV leaves alone must not look like a stale index.
    for n, (label, when) in enumerate((("yesterday's run (26 h ago)", built_ago(timedelta(hours=26))),
                                       ("REFRESH_AFTER ago", built_ago(site.REFRESH_AFTER)),
                                       ("no valid time", "yesterday"))):
        live.write_text(json.dumps({**man, "sha256": "f" * 64, "built_at": when}))
        sign_file(live)
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

    # A truncated feed, consistent in itself and newer, must not replace a
    # published index with more than twice its trains and lines (60 here).
    for n, (label, prev, ok) in enumerate((
        ("121 trains and lines", {"trains": 100, "lines": 21}, False),
        ("120 trains and lines (exactly half)", {"trains": 100, "lines": 20}, True),
        ("49 trains and lines (under the floor)", {"trains": 49, "lines": 0}, True),
        ("no integer counts", {"trains": "many", "lines": None}, True),
        ("no counts", {"trains": None, "lines": None}, True),
    )):
        live.write_text(json.dumps({**man, "feed": "20261031.M2ChangeID", "digest": "0" * 64, **prev}))
        sign_file(live)
        rc, out, err, outputs = run_site(tmp / f"shrink-{n}", "--live-file", str(live))
        if ok:
            check(f"a published index of {label}: deployed", rc == 0 and outputs.get("deploy") == "true",
                  repr((rc, out, err)))
        else:
            check(f"a published index of {label}: refused, nothing written",
                  rc == 1 and "60 trains and 0 lines vs 100 and 21" in err and "MAV_ALLOW_SHRINK" in err
                  and not (tmp / f"shrink-{n}" / "site").exists() and "deploy" not in outputs,
                  repr((rc, out, err, outputs)))
    live.write_text(json.dumps({**man, "feed": "20261031.M2ChangeID", "digest": "0" * 64, "trains": 121}))
    sign_file(live)
    rc, out, err, outputs = run_site(tmp / "shrink-flag", "--live-file", str(live), "--allow-shrink")
    check("--allow-shrink publishes it, with a warning",
          rc == 0 and outputs.get("deploy") == "true" and "::warning title=MÁV index shrank::" in out,
          repr((rc, out, err)))
    os.environ[mav_shrink_guard.ALLOW_ENV] = "1"
    try:
        rc, out, err, outputs = run_site(tmp / "shrink-env", "--live-file", str(live))
    finally:
        os.environ.pop(mav_shrink_guard.ALLOW_ENV, None)
    check("MAV_ALLOW_SHRINK=1 publishes it", rc == 0 and outputs.get("deploy") == "true", repr((rc, out, err)))

    # A published manifest that does not read (Pages down, a network error)
    # must not let a truncated feed through: the latest stable release's
    # MÁV index (its frontend zip, or the index itself) stands in for it.
    live.write_text("<html>503</html>")
    release_zip = tmp / "release" / "hungarian-transport-frontend.zip"
    release_zip.parent.mkdir()
    with zipfile.ZipFile(release_zip, "w") as z:
        z.writestr("volan-index.json.gz", b"not this one")
        z.writestr("mav-index.json.gz", index_bytes(trains=121))
    (tmp / "release" / "mav-index.json.gz").write_bytes(index_bytes(trains=121))
    for n, (label, baseline) in enumerate((("frontend zip", release_zip),
                                           ("MÁV index", tmp / "release" / "mav-index.json.gz"))):
        rc, out, err, outputs = run_site(tmp / f"shrink-base-{n}", "--live-file", str(live),
                                         "--baseline-file", str(baseline))
        check(f"an unreadable published manifest: a release {label} of 121 trains refuses 60",
              rc == 1 and "the latest release's index" in err and "60 trains and 0 lines vs 121 and 0" in err
              and not (tmp / f"shrink-base-{n}" / "site").exists() and "deploy" not in outputs,
              repr((rc, out, err, outputs)))
    with zipfile.ZipFile(tmp / "release" / "small.zip", "w") as z:
        z.writestr("mav-index.json.gz", index_bytes(trains=60))
    rc, out, err, outputs = run_site(tmp / "shrink-base-ok", "--live-file", str(live),
                                     "--baseline-file", str(tmp / "release" / "small.zip"))
    check("an unreadable published manifest: a release index of as many trains lets it deploy",
          rc == 0 and outputs.get("deploy") == "true" and "60 trains and 0 lines" in out, repr((rc, out, err)))
    with zipfile.ZipFile(tmp / "release" / "no-mav.zip", "w") as z:
        z.writestr("volan-index.json.gz", b"x")
    for n, extra in enumerate((("--baseline-file", str(tmp / "release" / "no-mav.zip")), ())):
        rc, out, err, outputs = run_site(tmp / f"shrink-base-none-{n}", "--live-file", str(live), *extra)
        check("an unreadable published manifest and no release MÁV index"
              f"{' in the zip' if extra else ' asked'}: deployed, with a warning that nothing was compared",
              rc == 0 and outputs.get("deploy") == "true" and "::warning title=MÁV shrink guard skipped::" in out,
              repr((rc, out, err)))
    check("the release index is only an https URL", site.fetch_baseline("http://example.invalid/z.zip")[0] is None)

    # A connection cut mid-body (IncompleteRead) or a garbled status line
    # (BadStatusLine) is an http.client.HTTPException, not an OSError: it
    # must read as "could not fetch", never stop the deploy or the verdict.
    class CutShort:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://example.invalid/z.zip"

        def read(self, *size):
            raise http.client.IncompleteRead(b'{"feed":', 64)

    def garbled(*_a, **_k):
        raise http.client.BadStatusLine("HTTP/1.1 2OO OK")

    real_urlopen = site.urllib.request.urlopen
    try:
        for label, opener in (("a body cut short", lambda *_a, **_k: CutShort()), ("a garbled status line", garbled)):
            site.urllib.request.urlopen = opener
            try:
                got = (site.fetch_live("https://example.invalid/mav-index.json"),
                       site.fetch_baseline("https://example.invalid/z.zip"))
            except Exception as err:  # noqa: BLE001 - the failure this checks for
                got = err
            check(f"{label} from the published manifest or the release zip is a reason, not an exception",
                  isinstance(got, tuple) and all(r[0] is None and r[1] for r in got), repr(got))
            out_buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(out_buf):
                    rc = verdict_mod.main(["--failure", "build", "--live-url", "https://example.invalid/m.json"])
            except Exception as err:  # noqa: BLE001 - the failure this checks for
                rc = err
            check(f"{label} from the published manifest: the verdict warns and stays green",
                  rc == 0 and out_buf.getvalue().startswith("::warning title=Daily MÁV index not published::"),
                  repr((rc, out_buf.getvalue())))
    finally:
        site.urllib.request.urlopen = real_urlopen
    live.write_text(json.dumps({**man, "feed": "20261031.M2ChangeID", "digest": "0" * 64,
                                "trains": 60, "lines": 0}))
    sign_file(live)
    rc, out, err, outputs = run_site(tmp / "shrink-base-live", "--live-file", str(live),
                                     "--baseline-file", str(release_zip))
    check("a readable published manifest whose signature verifies is compared, not the release index",
          rc == 0 and outputs.get("deploy") == "true" and "release index" not in out, repr((rc, out, err)))

    # The counts of a published manifest whose signature does not verify are
    # its author's: one who can put files on Pages must not freeze the site
    # by naming a million trains and lines. The guard compares with the
    # release's index instead, and with nothing (a warning) without one.
    with zipfile.ZipFile(tmp / "release" / "inflated-ok.zip", "w") as z:
        z.writestr("mav-index.json.gz", index_bytes(trains=60))
    for label, how in (("no mav-index.json.sig", lambda: live.with_name(live.name + ".sig").unlink()),
                       ("a mav-index.json.sig by another key", lambda: sign_file(live, os.urandom(32)))):
        live.write_text(json.dumps({**man, "feed": "20261031.M2ChangeID", "digest": "0" * 64,
                                    "trains": 10**6, "lines": 10**6}))
        sign_file(live)
        how()
        n = len(label)
        rc, out, err, outputs = run_site(tmp / f"shrink-untrusted-{n}", "--live-file", str(live))
        check(f"a published manifest with {label} naming a million trains: not compared (a warning), deployed",
              rc == 0 and outputs.get("deploy") == "true" and "::warning title=MÁV shrink guard skipped::" in out
              and "refusing" not in err, repr((rc, out, err)))
        rc, out, err, outputs = run_site(tmp / f"shrink-untrusted-rel-{n}", "--live-file", str(live),
                                         "--baseline-file", str(tmp / "release" / "inflated-ok.zip"))
        check(f"a published manifest with {label} naming a million trains: the release index is compared,"
              " deployed", rc == 0 and outputs.get("deploy") == "true" and "published manifest: not trusted" in out
              and "release index for the shrink guard: 60 trains" in out, repr((rc, out, err)))
        rc, out, err, outputs = run_site(tmp / f"shrink-untrusted-big-{n}", "--live-file", str(live),
                                         "--baseline-file", str(release_zip))
        check(f"a published manifest with {label}: a release index of 121 trains still refuses 60",
              rc == 1 and "the latest release's index" in err and "60 trains and 0 lines vs 121 and 0" in err,
              repr((rc, out, err)))

    # The script release.yml runs on the previous release's index.
    def mav_gz(path: Path, trains: int, lines: int = 0) -> Path:
        path.write_bytes(gzip.compress(json.dumps(
            {"v": 1, "n": {str(i): [] for i in range(trains)}, "l": {str(i): [] for i in range(lines)}}).encode()))
        return path

    guard = HERE / "mav_shrink_guard.py"
    small, big = mav_gz(tmp / "g-small.gz", 40, 10), mav_gz(tmp / "g-big.gz", 90, 11)
    mid = mav_gz(tmp / "g-mid.gz", 90, 10)
    junk = tmp / "g-junk.gz"
    junk.write_bytes(b"not gzip")
    no_allow = {k: v for k, v in os.environ.items() if k != mav_shrink_guard.ALLOW_ENV}
    for label, args, want in (
        ("50 vs 101: refused", (small, big), 1),
        ("50 vs 100: taken", (small, mid), 0),
        ("101 vs 50: taken", (big, small), 0),
        ("an unreadable previous index: nothing compared", (small, junk), 0),
        ("an unreadable new index: nothing compared", (junk, big), 0),
    ):
        done = subprocess.run([sys.executable, str(guard), *map(str, args)], capture_output=True, text=True,
                              timeout=30, env=no_allow)
        check(f"mav_shrink_guard.py {label}", done.returncode == want,
              repr((done.returncode, done.stdout, done.stderr)))
    done = subprocess.run([sys.executable, str(guard), str(small), str(big)], capture_output=True, text=True,
                          timeout=30, env={**no_allow, mav_shrink_guard.ALLOW_ENV: "true"})
    check("mav_shrink_guard.py with MAV_ALLOW_SHRINK=true takes the smaller index",
          done.returncode == 0 and "accepted" in done.stdout, repr((done.returncode, done.stdout, done.stderr)))

    _far = (datetime.now(timezone.utc) + site.MAX_FEED_AHEAD + timedelta(days=2)).strftime("%Y%m%d")
    _near = (datetime.now(timezone.utc) + site.MAX_FEED_AHEAD - timedelta(days=2)).strftime("%Y%m%d")
    rc, out, err, outputs = run_site(tmp / "near-ahead", index=index_bytes(feed=_near + ".M2ChangeID"),
                                     st=stats(_near + ".M2ChangeID"))
    check("a feed dated a little under MAX_FEED_AHEAD ahead makes a site", rc == 0 and outputs.get("deploy") == "true",
          repr((rc, out, err)))
    refusals = {
        "another schema": {"index": index_bytes(schema=site.mav_index.SCHEMA + 1)},
        "statistics of another feed": {"st": stats("20261031.M2ChangeID")},
        "no audit": {"st": {**stats(), "audit": None}},
        "too few trains": {"index": index_bytes(trains=3)},
        "undated feed": {"index": index_bytes(feed="latest"), "st": stats("latest")},
        # FEED_RE passes it; the integration's served_index.feed_date does not, so
        # every client would reject the manifest that replaced a good one.
        "a feed date that is no date (month 13, day 99)": {"index": index_bytes(feed="20261399.M2ChangeID"),
                                                           "st": stats("20261399.M2ChangeID")},
        "a feed dated 30 February": {"index": index_bytes(feed="20260230.M2ChangeID"),
                                     "st": stats("20260230.M2ChangeID")},
        "a feed that could break a workflow output": {"index": index_bytes(feed="20261101\n::x"),
                                                      "st": stats("20261101\n::x")},
        "not a commit": {"commit": "main"},
        "not gzip": {"index": b"<html>"},
        # A feed_end_date as the version would outrank every correct feed
        # after it in installs.
        "a feed dated more than MAX_FEED_AHEAD ahead": {"index": index_bytes(feed=_far + ".M2ChangeID"),
                                                        "st": stats(_far + ".M2ChangeID")},
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

    # feed_info.txt as the index builder reads it (gtfs_feed): a publisher
    # name in cp1250, a space after the header's commas, a folder in the zip.
    odd = tmp / "odd-feed-info.zip"
    with zipfile.ZipFile(odd, "w") as z:
        z.writestr("mav/feed_info.txt", "feed_publisher_name, feed_version, feed_start_date\n".encode()
                   + "MÁV-START Zrt.,".encode("cp1250") + b" 20261101.M2ChangeID , 20261101\n")
    try:
        odd_version = site.gtfs_feed_version(odd)
    except site.SiteError as err:
        odd_version = repr(err)
    check("gtfs_feed_version reads a cp1250 feed_info.txt with spaces after the commas",
          odd_version == "20261101.M2ChangeID", odd_version)
    for name, body, why in [
        ("no-feed-info.zip", {"routes.txt": "route_id\nr1\n"}, "has no feed_info.txt"),
        ("undated.zip", {"feed_info.txt": "feed_publisher_name,feed_version\nMÁV-START,latest\n"},
         "names no dated feed_version"),
    ]:
        with zipfile.ZipFile(tmp / name, "w") as z:
            for member, text in body.items():
                z.writestr(member, text)
        try:
            site.gtfs_feed_version(tmp / name)
            refused = ""
        except site.SiteError as err:
            refused = str(err)
        check(f"gtfs_feed_version refuses a zip that {why}", why in refused, refused)

    official = gtfs_zip(tmp / "dl" / "gtfs" / "mav-gtfs.zip")
    gtfs_args =("--gtfs-zip", str(official), "--pfaedle-dir", str(pf_a))
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
    sign_file(live)
    rc, out, err, outputs = run_site(tmp / "g-same", *gtfs_args, "--live-file", str(live))
    check("the same index and GTFS files as published: no deploy",
          rc == 0 and outputs.get("deploy") == "false", repr((rc, out, err, outputs)))
    for n, (label, changed, value) in enumerate((("official zip's sha256", "official", {"sha256": "e" * 64}),
                                                 ("official zip's size", "official", {"size": 1}),
                                                 ("overlay's tables", "overlay", {"files": ["shapes.txt"]}))):
        live.write_text(json.dumps({**gman, "built_at": built_ago(timedelta(hours=3)),
                                    "gtfs": {**g, changed: {**g[changed], **value}}}))
        rc, out, err, outputs = run_site(tmp / f"g-changed-{n}", *gtfs_args, "--live-file", str(live))
        check(f"only the {label} changed: deploy", rc == 0 and outputs.get("deploy") == "true"
              and "other GTFS files" in out, repr((rc, out, err, outputs)))
    # pfaedle writes other bytes for the same feed and OSM extract on every
    # run (every daily run of November 2026 did): an overlay that differs
    # only in its size and sha256 is no new content, or every run deploys.
    live.write_text(json.dumps({**gman, "built_at": built_ago(timedelta(hours=3)),
                                "gtfs": {**g, "overlay": {**g["overlay"], "size": 1, "sha256": "e" * 64}}}))
    sign_file(live)
    rc, out, err, outputs = run_site(tmp / "g-overlay-bytes", *gtfs_args, "--live-file", str(live))
    check("only the overlay's bytes changed, published 3 h ago: no deploy",
          rc == 0 and outputs.get("deploy") == "false" and "::notice title=Daily MÁV index unchanged::" in out,
          repr((rc, out, err, outputs)))
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
    check("the Pages site step names the latest stable release's frontend zip as the shrink guard's fallback",
          args[args.index("--baseline-url") + 1:args.index("--baseline-url") + 2]
          == ["https://github.com/Bator/hungarian-transport/releases/latest/download/hungarian-transport-frontend.zip"]
          if "--baseline-url" in args else False, repr(args))
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

    # A built_at counts only when the manifest's signature verifies.
    for label, trust_ in (("no mav-index.json.sig", f"{verdict_mod.UNTRUSTED}no mav-index.json.sig (HTTP 404)"),
                          ("a .sig that does not verify",
                           f"{verdict_mod.UNTRUSTED}mav-index.json.sig does not verify against the pinned public key")):
        fail, line = v("build", live_at(timedelta(hours=1)), "ok", now, trust_)
        check(f"a fresh published manifest with {label}: not trusted, the run fails, nothing of it named",
              fail and line.startswith("::error title=Daily MÁV index: published manifest not trusted::")
              and "20261101.M2ChangeID" not in line and "1 h ago" not in line, line)
    fail, line = v("build", live_at(timedelta(days=9)), "ok", now, f"{verdict_mod.UNKNOWN}mav-index.json.sig: HTTP 503")
    check("a published manifest whose .sig could not be read: age unknown, a warning, green",
          not fail and line.startswith("::warning") and "HTTP 503" in line and "216 h" not in line, line)

    # The CLI pins the tests' key (as run_site does), and its own wait for a
    # .sig that does not verify is 0 unless a test passes --sig-settle.
    verdict_wrap = ("import os, sys; sys.path.insert(0, sys.argv.pop(1)); import sign_manifest;"
                    " sign_manifest.PUBLIC_KEY = bytes.fromhex(os.environ['TEST_PAGES_PUBLIC_KEY']);"
                    " import mav_daily_verdict; raise SystemExit(mav_daily_verdict.main(sys.argv[1:]))")

    def run_verdict(*args_: str) -> subprocess.CompletedProcess:
        if "--sig-settle" not in args_ and "--failure" in args_:
            args_ = (*args_, "--sig-settle", "0")
        return subprocess.run([sys.executable, "-c", verdict_wrap, str(HERE), *args_],
                              capture_output=True, text=True, timeout=60,
                              env={**os.environ, "TEST_PAGES_PUBLIC_KEY": TEST_PUB.hex()})

    live.write_text(json.dumps(live_at(timedelta(days=3))))
    sign_file(live)
    done = run_verdict("--failure", "build", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI exits 1 on a stale published index",
          done.returncode == 1 and "::error title=Daily MÁV index is stale::" in done.stdout,
          repr((done.returncode, done.stdout, done.stderr)))
    live.write_text(json.dumps(live_at(timedelta(hours=5))))
    sign_file(live)
    done = run_verdict("--failure", "site", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI exits 0 on a fresh published index", done.returncode == 0 and "5 h ago" in done.stdout,
          repr((done.returncode, done.stdout, done.stderr)))
    good_sig = live.with_name(live.name + ".sig").read_bytes()
    sign_file(live, os.urandom(32))
    done = run_verdict("--failure", "site", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI exits 1 on a fresh published index whose .sig does not verify, also read again",
          done.returncode == 1 and "published manifest not trusted" in done.stdout
          and "reading both again in 0 s" in done.stdout and "5 h ago" not in done.stdout,
          repr((done.returncode, done.stdout, done.stderr)))
    live.with_name(live.name + ".sig").unlink()
    done = run_verdict("--failure", "site", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI exits 1 on a fresh published index without a .sig (HTTP 404)",
          done.returncode == 1 and "no mav-index.json.sig (HTTP 404)" in done.stdout,
          repr((done.returncode, done.stdout, done.stderr)))

    # Pages' CDN serving the previous deploy's .sig next to the new manifest
    # (each path cached on its own, up to PAGES_MAX_AGE): the verdict reads
    # both again SIG_SETTLE_S later, by default, and then trusts them.
    live.with_name(live.name + ".sig").write_text(
        base64.b64encode(sign_manifest.sign(live, os.urandom(32))).decode() + "\n")
    waits: list[float] = []

    def settled(seconds: float) -> None:
        waits.append(seconds)
        live.with_name(live.name + ".sig").write_bytes(good_sig)

    real_sleep, real_key = verdict_mod.time.sleep, sign_manifest.PUBLIC_KEY
    verdict_mod.time.sleep, sign_manifest.PUBLIC_KEY = settled, TEST_PUB
    out_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf):
            rc = verdict_mod.main(["--failure", "site", "--live-file", str(live), "--now", now.isoformat()])
    finally:
        verdict_mod.time.sleep, sign_manifest.PUBLIC_KEY = real_sleep, real_key
    check("a .sig of the previous deploy at first: read again once, SIG_SETTLE_S (11 minutes, past Pages' cache)"
          " later, then trusted: green", rc == 0 and waits == [site.SIG_SETTLE_S] and site.SIG_SETTLE_S >= 660
          and "5 h ago" in out_buf.getvalue(), repr((rc, waits, out_buf.getvalue())))
    # --live-url: what the site answers for the .sig decides. One who can
    # put files on Pages can make it oversized or refused (HTTP 4xx): that
    # is no signature, the run red whatever built_at says; only a network
    # error, a timeout or a server error (HTTP 5xx) leaves the age unknown
    # (green, a warning). A 404 is read again once, SIG_SETTLE_S later
    # (Pages' cache may keep the 404 from before the first signed deploy).
    fresh_body = live.read_bytes()
    fresh_sig = live.with_name(live.name + ".sig").read_bytes()
    man_url, sig_url = "https://pages.example/v/mav-index.json", "https://pages.example/v/mav-index.json.sig"

    class Body(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def http_error(code: int):
        def raise_(url):
            raise site.urllib.error.HTTPError(url, code, "x", {}, None)
        return raise_

    def raise_timeout(url):
        raise TimeoutError("timed out")

    for label, sig_answer, later, want_rc, want_text, want_waits in (
        ("an oversized .sig", b"A" * (site.MAX_SIG + 1), None, 1, "is no signature", []),
        ("a .sig the site refuses (HTTP 403)", http_error(403), None, 1, "HTTP 403", []),
        ("a .sig gone for good (HTTP 410)", http_error(410), None, 1, "HTTP 410", []),
        ("a .sig the server fails on (HTTP 503)", http_error(503), None, 0, "HTTP 503", []),
        ("a .sig that times out", raise_timeout, None, 0, "TimeoutError", []),
        ("a .sig not there (HTTP 404), also read again", http_error(404), None, 1, "(HTTP 404)",
         [site.SIG_SETTLE_S]),
        ("a .sig Pages' cache answers 404 for at first", http_error(404), fresh_sig, 0, "5 h ago",
         [site.SIG_SETTLE_S]),
    ):
        answers = {man_url: fresh_body, sig_url: sig_answer}
        url_waits: list[float] = []

        def url_open(req, timeout=None, _answers=answers):
            got = _answers.get(req.full_url)
            if got is None:
                raise site.urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
            if callable(got):
                got(req.full_url)
            return Body(got)

        def url_sleep(seconds: float, _answers=answers, _later=later, _waits=url_waits) -> None:
            _waits.append(seconds)
            if _later is not None:
                _answers[sig_url] = _later

        real = (site.urllib.request.urlopen, verdict_mod.time.sleep, sign_manifest.PUBLIC_KEY)
        site.urllib.request.urlopen, verdict_mod.time.sleep, sign_manifest.PUBLIC_KEY = url_open, url_sleep, TEST_PUB
        out_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf):
                rc = verdict_mod.main(["--failure", "build", "--live-url", man_url, "--now", now.isoformat()])
        finally:
            site.urllib.request.urlopen, verdict_mod.time.sleep, sign_manifest.PUBLIC_KEY = real
        text = out_buf.getvalue()
        check(f"the verdict, --live-url, a fresh published index with {label}: "
              + ("red, not trusted" if want_rc else "green"),
              rc == want_rc and want_text in text and url_waits == want_waits
              and (("published manifest not trusted" in text and "5 h ago" not in text) if want_rc
                   else "::warning title=Daily MÁV index not published::" in text),
              repr((rc, url_waits, text)))
    check("the verdict's .sig classes: HTTP 5xx and network errors unknown, any other status and an oversized"
          " .sig untrusted",
          verdict_mod.sig_unreadable("HTTP 500").startswith(verdict_mod.UNKNOWN)
          and verdict_mod.sig_unreadable("URLError: <urlopen error x>").startswith(verdict_mod.UNKNOWN)
          and verdict_mod.sig_unreadable("HTTP 451").startswith(verdict_mod.UNTRUSTED)
          and verdict_mod.sig_unreadable("HTTP 301").startswith(verdict_mod.UNTRUSTED)
          and verdict_mod.sig_unreadable(f"larger than {site.MAX_SIG} bytes").startswith(verdict_mod.UNTRUSTED))
    big = live.with_name(live.name + ".sig")
    big.write_bytes(b"A" * (site.MAX_SIG + 1))
    done = run_verdict("--failure", "site", "--live-file", str(live), "--now", now.isoformat())
    check("the verdict CLI, --live-file: an oversized .sig is no signature, the run red",
          done.returncode == 1 and "is no signature" in done.stdout, repr((done.returncode, done.stdout)))
    sign_file(live)
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

    # ---- the timetables job's steps ----------------------------------------
    stub_pt = (
        "import os, sys\nfrom pathlib import Path\n"
        "open('pt-args.txt', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "cmd = sys.argv[1]\n"
        "def arg(n):\n    return sys.argv[sys.argv.index(n) + 1]\n"
        "if cmd == 'due':\n    open(arg('--github-output'), 'a').write('due=true\\nindexes=volan,city\\n')\n"
        "if cmd == 'baseline':\n"
        "    out = Path(arg('--out')); out.mkdir(parents=True, exist_ok=True)\n"
        "    for n in os.environ.get('STUB_BASELINE', '').split():\n"
        "        (out / f'{n}-index.json.gz').write_text(n)\n"
        "if cmd == 'part':\n"
        "    if os.environ.get('STUB_PART') == 'fail':\n        sys.exit(1)\n"
        "    out = Path(arg('--out')); out.mkdir(parents=True, exist_ok=True)\n"
        "    (out / Path(arg('--index')).name).write_text('packed')\n"
        "if cmd == 'assemble':\n"
        "    open(arg('--github-output'), 'a').write('deploy=true\\n')\n"
        "    sys.exit(int(os.environ.get('STUB_RC', '0')))\n")
    gh_env = {"GITHUB_REPOSITORY": "Bator/hungarian-transport", "GITHUB_REPOSITORY_OWNER": "Bator",
              "GITHUB_SHA": "b" * 40}

    def tools_ws(label: str, schema: str | None = "28") -> Path:
        """A workspace as the timetables job has it: the stable release at
        the root (its gtfs_compact.py naming its schema), the workflow
        commit's scripts in .pages-tools."""
        ws_ = tmp / label
        (ws_ / ".pages-tools" / "scripts").mkdir(parents=True)
        (ws_ / ".pages-tools" / "scripts" / "pages_timetables.py").write_text(stub_pt)
        (ws_ / "scripts").mkdir()
        (ws_ / "scripts" / "gtfs_compact.py").write_text(
            "UA = 'x'\n" + (f"INDEX_SCHEMA = {schema}\n" if schema is not None else ""))
        return ws_

    ws = tools_ws("wf-due")
    due_step = step_script("Timetables due")
    for force in ("true", "false"):
        (ws / "pt-args.txt").unlink(missing_ok=True)
        done, outputs = bash(due_step, ws, FORCE=force, TAG="v1.10.21", **gh_env)
        args = (ws / "pt-args.txt").read_text().split() if (ws / "pt-args.txt").exists() else []

        def after(flag: str, _args: list[str] = args) -> list[str]:
            return _args[_args.index(flag) + 1:_args.index(flag) + 2] if flag in _args else []

        check(f"the due step runs the workflow commit's script with the stable release's schema, asks the"
              f" repository's own Pages site, --force {force}, compares with the stable release's zip, writes"
              " its feeds for the parts, and passes due, indexes and schema on",
              done.returncode == 0 and outputs.get("due") == "true" and outputs.get("indexes") == "volan,city"
              and outputs.get("schema") == "28" and args[:1] == ["due"] and after("--schema") == ["28"]
              and after("--live-url") == ["https://bator.github.io/hungarian-transport"]
              and after("--force") == [force]
              and after("--release-url") == ["https://github.com/Bator/hungarian-transport/releases/download/v1.10.21/"
                                             "hungarian-transport-frontend.zip"]
              and after("--release-out") == [f"{ws / 'runner-temp'}/release-feeds.json"]
              and after("--release-tag") == ["v1.10.21"],
              repr((done.returncode, done.stderr, args, outputs)))
    check("the due step takes the stable release's tag from the stable step",
          "TAG: ${{ steps.stable.outputs.tag }}" in step(job("timetables"), "- name: Timetables due"))
    ws = tools_ws("wf-due-noschema", schema=None)
    done, outputs = bash(due_step, ws, FORCE="false", TAG="v1.10.21", **gh_env)
    check("a stable release whose gtfs_compact.py names no schema: an error, nothing due",
          done.returncode == 1 and "names no INDEX_SCHEMA" in done.stdout and "due" not in outputs,
          repr((done.returncode, done.stdout, outputs)))

    ws = tools_ws("wf-baseline")
    base_step = step_script("Baseline indexes")
    done, _ = bash(base_step, ws, STUB_BASELINE="volan city", TAG="v1.10.21", **gh_env)
    args = (ws / "pt-args.txt").read_text().split() if (ws / "pt-args.txt").exists() else []
    check("the baseline step names the Pages site and the stable release's zip, and puts the indexes"
          " in place for the builders' guards (a copy stays in .baseline)",
          done.returncode == 0 and (ws / "volan-index.json.gz").read_text() == "volan"
          and (ws / "city-index.json.gz").read_text() == "city" and (ws / ".baseline" / "city-index.json.gz").is_file()
          and "https://bator.github.io/hungarian-transport" in args and ".baseline" in args
          and "https://github.com/Bator/hungarian-transport/releases/download/v1.10.21/hungarian-transport-frontend.zip"
          in args, repr((done.returncode, done.stderr, args)))
    ws = tools_ws("wf-baseline-none")
    done, _ = bash(base_step, ws, STUB_BASELINE="", TAG="v1.10.21", **gh_env)
    check("no baseline: the builders find no index in place, and the step goes on",
          done.returncode == 0 and not (ws / "volan-index.json.gz").exists(), repr((done.returncode, done.stderr)))

    build_tt = step_script("Build the Volán and city indexes")
    stub_builder = ("import os, sys\nname = sys.argv[0].split('build_')[1].split('_index')[0]\n"
                    "open(f'{name}-env.txt', 'w').write(os.environ.get('HUNGARIAN_TRANSPORT_CARRY_INDEX', '-') + ' '"
                    " + os.environ.get('HUNGARIAN_TRANSPORT_ALLOW_MISSING', '-'))\n"
                    "if name in os.environ.get('STUB_FAIL', '').split():\n    sys.exit(3)\n"
                    "open(f'{name}-index.json.gz', 'w').write('built')\n")

    def tt_ws(label: str, baseline: bool = True) -> Path:
        ws_ = tools_ws(f"wf-tt-{label}")
        for n in ("volan", "city"):
            (ws_ / "scripts" / f"build_{n}_index.py").write_text(stub_builder)
        if baseline:
            (ws_ / ".baseline").mkdir()
            (ws_ / ".baseline" / "city-index.json.gz").write_text("old")
        return ws_

    ws = tt_ws("ok")
    done, outputs = bash(build_tt, ws, DUE_INDEXES="volan,city", SCHEMA="28", ALLOW_MISSING_VAR="", **gh_env)
    part_dir = ws / "runner-temp" / "timetables-part"
    check("both builders through: both indexes packed, built=volan,city, the city builder carries from .baseline",
          done.returncode == 0 and outputs.get("built") == "volan,city"
          and sorted(p_.name for p_ in part_dir.iterdir()) == ["city-index.json.gz", "volan-index.json.gz"]
          and (ws / "city-env.txt").read_text() == ".baseline/city-index.json.gz ",
          repr((done.returncode, done.stdout, done.stderr, outputs)))
    ws = tt_ws("volan-fails")
    done, outputs = bash(build_tt, ws, DUE_INDEXES="volan,city", SCHEMA="28", ALLOW_MISSING_VAR="", STUB_FAIL="volan", **gh_env)
    check("a failing Volán builder: a warning, only the city index packed, the step goes on",
          done.returncode == 0 and outputs.get("built") == "city"
          and "::warning title=volan index not built::build_volan_index.py failed (exit 3" in done.stdout
          and sorted(p_.name for p_ in (ws / "runner-temp" / "timetables-part").iterdir()) == ["city-index.json.gz"],
          repr((done.returncode, done.stdout, done.stderr, outputs)))
    ws = tt_ws("both-fail", baseline=False)
    done, outputs = bash(build_tt, ws, DUE_INDEXES="volan,city", SCHEMA="28", ALLOW_MISSING_VAR="", STUB_FAIL="volan city", **gh_env)
    check("both builders failing: two warnings, built empty (nothing uploaded), green; no carry without a baseline",
          done.returncode == 0 and outputs.get("built") == "" and done.stdout.count("::warning title=") == 2
          and (ws / "city-env.txt").read_text() == "- ", repr((done.returncode, done.stdout, outputs)))
    ok_args = tmp / "wf-tt-ok" / "pt-args.txt"
    args = ok_args.read_text().split() if ok_args.exists() else []
    check("the part is checked against the stable release's schema, by the workflow commit's script, and"
          " records the release's feeds the due step wrote",
          args[:1] == ["part"] and "--schema" in args and args[args.index("--schema") + 1] == "28"
          and '.pages-tools/scripts/pages_timetables.py part --schema "$SCHEMA"' in build_tt
          and args[args.index("--release-feeds") + 1:args.index("--release-feeds") + 2]
          == [f"{tmp / 'wf-tt-ok' / 'runner-temp'}/release-feeds.json"] if "--release-feeds" in args else False,
          repr(args))
    check("the Pages build takes an insecure feed (Miskolc) over http as a release does: no HUNGARIAN_TRANSPORT_HTTPS_ONLY set",
          "HUNGARIAN_TRANSPORT_HTTPS_ONLY: " not in TEXT)
    ws = tt_ws("city-due")
    done, outputs = bash(build_tt, ws, DUE_INDEXES="city", SCHEMA="28", ALLOW_MISSING_VAR="", **gh_env)
    check("only the due index is built: city alone, the Volán builder never runs",
          done.returncode == 0 and outputs.get("built") == "city" and not (ws / "volan-env.txt").exists()
          and sorted(p_.name for p_ in (ws / "runner-temp" / "timetables-part").iterdir()) == ["city-index.json.gz"],
          repr((done.returncode, done.stdout, done.stderr, outputs)))
    ws = tt_ws("bad-name")
    done, outputs = bash(build_tt, ws, DUE_INDEXES="volan,../x", SCHEMA="28", ALLOW_MISSING_VAR="", **gh_env)
    check("an index name that is neither volan nor city stops the step", done.returncode == 1
          and "unexpected index name" in done.stdout, repr((done.returncode, done.stdout)))
    step_limits = [int(m) for m in re.findall(r"^        timeout-minutes: (\d+)$", job("timetables"), re.M)]
    job_limit = int(re.search(r"^    timeout-minutes: (\d+)$", job("timetables"), re.M).group(1))
    tt_step_count = sum(1 for ln in job("timetables").splitlines() if re.match(r"^      - ", ln))
    check("every timetables step has its own time limit, the job's comfortably above their sum, and each builder"
          " one that leaves its step's for the other one",
          len(step_limits) == tt_step_count and job_limit >= sum(step_limits) + 10
          and 'timeout --kill-after=1m 20m python3 "scripts/build_${name}_index.py"' in build_tt
          and int(re.search(r"^        timeout-minutes: (\d+)$", step(job("timetables"), "- name: Build the Volán"),
                            re.M).group(1)) >= 2 * (20 + 1) + 2,
          repr((step_limits, tt_step_count, job_limit)))
    ws = tt_ws("part-fails")
    done, outputs = bash(build_tt, ws, DUE_INDEXES="volan,city", SCHEMA="28", ALLOW_MISSING_VAR="", STUB_PART="fail", **gh_env)
    check("an index the part refuses is not counted as built", done.returncode == 0 and outputs.get("built") == "",
          repr((done.returncode, done.stdout, outputs)))
    ws = tt_ws("allow")
    done, outputs = bash(build_tt, ws, DUE_INDEXES="volan,city", SCHEMA="28", ALLOW_MISSING_VAR="szeged, pecs", **gh_env)
    check("HUNGARIAN_TRANSPORT_ALLOW_MISSING reaches the builders, with a warning on every run",
          done.returncode == 0 and (ws / "city-env.txt").read_text().endswith(" szeged, pecs")
          and "::warning title=City index guard relaxed::" in done.stdout, repr((done.returncode, done.stdout)))
    ws = tt_ws("bad-allow")
    done, outputs = bash(build_tt, ws, DUE_INDEXES="volan,city", SCHEMA="28", ALLOW_MISSING_VAR="x\n::error::y", **gh_env)
    check("an allow_missing value with a newline stops the step without echoing it, nothing built",
          done.returncode == 1 and "::error title=Bad allow_missing::" in done.stdout and "::error::y" not in done.stdout
          and not (ws / "volan-env.txt").exists(), repr((done.returncode, done.stdout)))

    stable_step = step_script("Latest stable release")
    gh_bin = tmp / "gh-releases"
    gh_bin.mkdir()
    (gh_bin / "gh").write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" > "$PWD/gh-args.txt"\n'
        'case "${STUB_GH:-}" in\n'
        "  fail) echo 'gh: HTTP 502' >&2; exit 1 ;;\n"
        "  none) printf 'v1.11.0-rev.3\\n' ;;\n"
        "  *) printf 'v1.9.0\\nv1.10.21\\nv1.10.3\\nv1.10.22x\\n' ;;\n"
        "esac\n")
    (gh_bin / "gh").chmod(0o755)
    ws = tmp / "wf-stable"
    ws.mkdir()
    for case, want_rc, want_tag, text in (("ok", 0, "v1.10.21", ""), ("none", 0, "", "::warning title=No Volán or city"),
                                           ("fail", 1, None, "::error::Could not list the releases")):
        done, outputs = bash(stable_step, ws, path_prefix=gh_bin, STUB_GH=case, GH_TOKEN="x", **gh_env)
        gh_args = (ws / "gh-args.txt").read_text() if (ws / "gh-args.txt").exists() else ""
        check(f"the latest stable release, {case}: "
              + (f"tag {want_tag or 'empty'}" if want_tag is not None else "an error, the job fails"),
              done.returncode == want_rc and outputs.get("tag") == want_tag and text in done.stdout
              and "repos/Bator/hungarian-transport/releases" in gh_args and "--paginate" in gh_args
              and "select((.draft or .prerelease) | not)" in stable_step,
              repr((done.returncode, done.stdout, done.stderr, outputs, gh_args)))
    tt_steps_all = [ln.strip() for ln in job("timetables").splitlines() if re.match(r"^      - ", ln)]
    root_co = step(job("timetables"), "ref: ${{ steps.stable.outputs.tag }}")
    tools_co = step(job("timetables"), "path: .pages-tools")
    check("the stable release is checked out at the root first, then the workflow commit's scripts in"
          " .pages-tools (the root checkout would clean them away), only with a stable tag",
          tt_steps_all[:3] == ["- name: Latest stable release", "- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
                               "- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1"]
          and "path:" not in root_co and "ref: ${{ github.sha }}" in tools_co and "scripts\n" in tools_co + "\n"
          and "custom_components/hungarian_transport" in tools_co
          and all("if: steps.stable.outputs.tag != ''" in b for b in (root_co, tools_co))
          and "if: steps.stable.outputs.tag != ''" in step(job("timetables"), "- name: Timetables due"),
          repr((tt_steps_all[:3], root_co, tools_co)))
    check("the timetables job runs no script of the stable release but its builders",
          all("python3 scripts/" not in step_script(n) for n in ("Timetables due", "Baseline indexes"))
          and re.findall(r"python3 (\S+)", build_tt) == ['"scripts/build_${name}_index.py"',
                                                         ".pages-tools/scripts/pages_timetables.py"],
          repr(re.findall(r"python3 (\S+)", build_tt)))

    assemble_step = step_script("Assemble the site")
    for mav_new, tt_new, want in (("true", "true", ["--mav-part", "--timetables-part"]),
                                  ("true", "false", ["--mav-part"]), ("false", "true", ["--timetables-part"]),
                                  ("false", "false", [])):
        ws = tmp / f"wf-assemble-{mav_new}-{tt_new}"
        (ws / "scripts").mkdir(parents=True)
        (ws / "scripts" / "pages_timetables.py").write_text(stub_pt)
        done, outputs = bash(assemble_step, ws, MAV_NEW=mav_new, TIMETABLES_NEW=tt_new, SCHEMA="", **gh_env)
        args = (ws / "pt-args.txt").read_text().split() if (ws / "pt-args.txt").exists() else []
        rt = str(ws / "runner-temp")
        parts = [a_ for a_ in args if a_ in ("--mav-part", "--timetables-part")]
        check(f"the assemble step, MÁV part new: {mav_new}, timetables new: {tt_new}: passes {want or 'no part'},"
              " the Pages site, the commit, and deploy on",
              done.returncode == 0 and outputs.get("deploy") == "true" and parts == want
              and all(args[args.index(f_) + 1] == f"{rt}/{f_[2:]}" for f_ in want)
              and "https://bator.github.io/hungarian-transport" in args and "b" * 40 in args
              and f"{rt}/site" in args, repr((done.returncode, done.stderr, args)))
    (ws / "pt-args.txt").unlink(missing_ok=True)
    done, _ = bash(assemble_step, ws, MAV_NEW="true", TIMETABLES_NEW="false", SCHEMA="28", **gh_env)
    args = (ws / "pt-args.txt").read_text().split() if (ws / "pt-args.txt").exists() else []
    check("the assemble step passes the stable release's schema on when the timetables job named one",
          done.returncode == 0 and args[args.index("--schema") + 1:args.index("--schema") + 2] == ["28"]
          if "--schema" in args else False, repr(args))
    done, _ = bash(assemble_step, ws, MAV_NEW="true", TIMETABLES_NEW="false", SCHEMA="", STUB_RC="1", **gh_env)
    check("a refused site fails the assemble step", done.returncode == 1, repr(done.returncode))

    # The attest step's input: the artifact upload-pages-artifact made (a
    # tar of the site's top level, as its action.yml writes it).
    unpack_step = step_script("Unpack the deployed site")
    mav_names = ("mav-index.json.gz", "mav-index.json", "mav-gtfs.zip", "mav-pfaedle-overlay.zip")
    tt_names = ("timetables.json", "volan-index.json.gz", "city-index.json.gz", "volan-shapes-0123456789/00.json.gz",
                "volan-shapes-0123456789/01.json.gz", "city-shapes-abcdef0123/00.json.gz")
    man = {"format": 1, "indexes": {"volan": {"shapes": {"dir": "volan-shapes-0123456789"}},
                                    "city": {"shapes": {"dir": "city-shapes-abcdef0123"}}}}
    site_all = (*mav_names, *tt_names, "ATTRIBUTION.txt", "index.html")
    volan_only = ("timetables.json", "volan-index.json.gz", "volan-shapes-0123456789/00.json.gz",
                  "volan-shapes-0123456789/01.json.gz")
    # (label, files in the site, MAV_NEW, BUILT, attested, error). Only what
    # this run built is attested: a file carried from the live site keeps the
    # attestation of the run that built it.
    unpack_cases = (
        ("everything built this run", site_all, "true", "volan,city", (*mav_names, *tt_names), ""),
        ("nothing new but timetables.json (no part built)", site_all, "false", "", ("timetables.json",), ""),
        ("only the MÁV part new: the carried indexes are not attested", site_all, "true", "",
         (*mav_names, "timetables.json"), ""),
        ("only the Volán index new: MÁV and city carried, not attested", site_all, "false", "volan", volan_only, ""),
        ("a fork without the MÁV secrets", (*tt_names, "index.html"), "false", "volan,city", tt_names, ""),
        ("a new MÁV part without the overlay", (*mav_names[:3], *tt_names), "true", "", (),
         "mav-pfaedle-overlay.zip is not in the deployed site"),
        ("a built index missing from the site", (*mav_names, "timetables.json"), "false", "city", (),
         "city-index.json.gz is not in the deployed site"),
        ("a site without timetables.json", mav_names, "true", "", (), "timetables.json is not in the deployed site"),
        ("an unexpected index name", site_all, "false", "volan,x", (), "unexpected index name"),
    )
    for k, (label, names, mav_new, built, want, error) in enumerate(unpack_cases):
        ws = tmp / f"wf-unpack-{k}"
        (ws / "built").mkdir(parents=True)
        (ws / "runner" / "pages").mkdir(parents=True)
        for name in names:
            (ws / "built" / name).parent.mkdir(parents=True, exist_ok=True)
            body = json.dumps(man).encode() if name == "timetables.json" else name.encode()
            (ws / "built" / name).write_bytes(body)
        subprocess.run(["tar", "--directory", str(ws / "built"), "-cf", str(ws / "runner" / "pages" / "artifact.tar"),
                        "."], check=True)
        done, outputs = bash(unpack_step, ws, RUNNER_TEMP=str(ws / "runner"), MAV_NEW=mav_new, BUILT=built)
        out_text = (ws / f"github_output_{0}").read_text() if (ws / "github_output_0").exists() else ""
        subjects = out_text.split("subjects<<SUBJECTS_EOF\n", 1)[1].split("SUBJECTS_EOF", 1)[0].splitlines() \
            if "subjects<<SUBJECTS_EOF" in out_text else []
        unpacked = ws / "runner" / "site"
        if error:
            ok = done.returncode == 1 and error in done.stdout
        else:
            ok = (done.returncode == 0 and sorted(subjects) == sorted(f"{unpacked}/{n}" for n in want)
                  and all(Path(x).is_file() for x in subjects))
        check(f"the deployed site unpacked for its attestation, {label}: "
              + (f"an error naming {error.split()[0]}" if error else "this run's files, one per line, nothing else"),
              ok, repr((done.returncode, done.stdout, done.stderr, subjects)))

# ---- the workflow text ------------------------------------------------------
on_block = TEXT.split("\non:\n", 1)[1].split("\n\n", 1)[0]
check("runs daily and by hand, never for pull requests (secrets, deploy)",
      "schedule:" in on_block and "workflow_dispatch:" in on_block
      and "pull_request" not in on_block and "push:" not in on_block, on_block)
check("the dispatch has one input, a boolean asking for the Volán and city indexes now",
      re.search(r"^  workflow_dispatch:\n    inputs:\n      timetables:\n        description: .+\n"
                r"        type: boolean\n        default: false$", on_block, re.M) is not None
      and on_block.count("type:") == 1, on_block)
check("named for what it publishes now", TEXT.startswith("name: Daily timetables (GitHub Pages)\n"))
# The schedule against what November 2026 showed: MÁV's feed of the day out
# between 03:17 and 10:03 UTC, on 3 November still missing at 13:54; GitHub
# starting the 03:15 cron 6 h 48 min to 8 h 13 min late; the owner's dispatch
# at 11:30 Budapest time (09:30-10:30 UTC). The concurrency group keeps one
# running and one pending run, and a third arrival cancels the pending one.
crons = [(int(m.group(2)) * 60 + int(m.group(1)))
         for m in re.finditer(r'- cron: "(\d+) (\d+) \* \* \*"', on_block)]
check("two daily crons, nothing else on a schedule",
      len(crons) == 2 and on_block.count("- cron:") == 2, repr(crons))
dispatch_end, published_by, missing_at, budapest_midnight = 11 * 60 + 30, 10 * 60 + 3, 13 * 60 + 54, 23 * 60
late_most = 8 * 60 + 13
check("at most one cron can meet the dispatch, however late GitHub starts it (it never starts one early):"
      " with the dispatch, two runs at most, none cancelled",
      sum(c < dispatch_end for c in crons) <= 1, repr(crons))
check("a cron after the feed seen missing at 13:54 UTC, starting the same Budapest day (winter) even 8 h late",
      any(missing_at < c and c + late_most < budapest_midnight for c in crons), repr(crons))
check("the morning cron, started as late as the 03:15 one was, lands after the dispatch window and after"
      " the latest morning publication: installations without the dispatch get the day's feed by midday",
      any(c < dispatch_end and c + 6 * 60 + 48 >= max(dispatch_end, published_by) for c in crons), repr(crons))
top = TEXT.split("\njobs:\n", 1)[0]
check("the workflow token reads contents only",
      re.search(r"^permissions:\n  contents: read\n\n", top, re.M) is not None, top)
check("one run at a time, none cancelled half-way",
      re.search(r"^concurrency:\n  group: mav-index-daily\n  cancel-in-progress: false$", top, re.M) is not None)
build, deploy, attest_job = job("build"), job("deploy"), job("attest")
tt_job, site_job, sign_job = job("timetables"), job("site"), job("sign")
jobs_block = TEXT.split("\njobs:\n", 1)[1]
check("seven jobs: build (MÁV), timetables, site, sign, deploy, signatures, attest",
      re.findall(r"^  ([\w-]+):$", jobs_block, re.M)
      == ["build", "timetables", "site", "sign", "deploy", "signatures", "attest"],
      repr(re.findall(r"^  ([\w-]+):$", jobs_block, re.M)))
sig_job = job("signatures")
check("the signatures job runs after the deploy (whatever it did) when the site job names an untrusted"
      " published manifest, with no permissions, no checkout and no secret",
      "    needs: [site, deploy]" in sig_job
      and "    if: ${{ !cancelled() && needs.site.outputs.untrusted != '' }}" in sig_job
      and re.search(r"^    permissions: \{\}$", sig_job, re.M) is not None and "actions/checkout" not in sig_job
      and "secrets." not in sig_job and "untrusted: ${{ steps.assemble.outputs.untrusted }}" in job("site"),
      sig_job)
with tempfile.TemporaryDirectory() as _sig_tmp:
    for value, shown in (("timetables.json", "timetables.json"), ("timetables.json,mav-index.json",
                                                                  "timetables.json,mav-index.json"),
                         ("x\n::warning::y", "(unexpected names)")):
        done, _ = bash(step_script("A published signature does not verify"), Path(_sig_tmp), UNTRUSTED=value)
        check(f"the signatures step with {value!r}: exit 1, one error annotation naming {shown}",
              done.returncode == 1 and done.stdout.count("::error title=Published manifest signature does not"
                                                         " verify::") == 1
              and f"verify::{shown}:" in done.stdout and "::warning::" not in done.stdout, repr(done.stdout))
check("the build and timetables jobs run side by side, the site job after both, whatever they did (failed,"
      " timed out), unless the run is cancelled",
      "needs:" not in build and "needs:" not in tt_job and "    needs: [build, timetables]" in site_job
      and "    if: ${{ !cancelled() }}" in site_job and ".result" not in site_job, site_job[:600])


def minutes(block: str) -> int | None:
    m = re.search(r"^    timeout-minutes: (\d+)$", block, re.M)
    return int(m.group(1)) if m else None


# deploy-pages waits for the deployment up to its own timeout input, 600000
# ms by default: a job limit of 10 minutes cut it short before it gave up.
check("every job has a time limit, the deploy job's above deploy-pages' own 10 minutes",
      all(minutes(b) for b in (build, tt_job, site_job, sign_job, deploy, attest_job)) and minutes(deploy) >= 15
      and "timeout:" not in step(deploy, "actions/deploy-pages@"),
      repr([minutes(b) for b in (build, tt_job, site_job, sign_job, deploy, attest_job)]))
check("only the deploy job may write Pages, only it and the attest job mint an OIDC token",
      all("pages: write" not in b and "id-token" not in b and "permissions:" not in b
          for b in (build, tt_job, site_job)) and "pages: write" not in attest_job
      and re.search(r"^    permissions:\n      contents: read\n    \S", sign_job, re.M) is not None
      and re.search(r"^    permissions:\n      pages: write\n      id-token: write\n    \S", deploy, re.M) is not None,
      deploy)
# success() looks at every job before this one, and the build job fails on
# a stale MÁV index while the site may still carry a new Volán or city index.
check("the deploy job runs after a sign job that signed a site, in the github-pages environment, whatever"
      " the build job did; the sign job after a site job that made one, on main only",
      "    needs: sign" in deploy and "    if: ${{ !cancelled() && needs.sign.result == 'success' }}" in deploy
      and "      name: github-pages" in deploy and "    needs: site" in sign_job
      and "    if: ${{ !cancelled() && needs.site.result == 'success' && needs.site.outputs.deploy == 'true'"
      " && github.ref == 'refs/heads/main' }}" in sign_job
      and "deploy: ${{ steps.assemble.outputs.deploy }}" in site_job, deploy)
check("the deploy job checks out and runs nothing from the repository, and sees no MÁV secret",
      "actions/checkout" not in deploy and "secrets." not in deploy and "scripts/" not in deploy)
upload = step(sign_job, "actions/upload-pages-artifact@")
site_up = step(site_job, "name: pages-site")
site_dl = step(sign_job, "name: pages-site")
check("only the sign job makes the Pages artifact, from the site the site job assembled (pages-site, only a"
      " site to deploy, kept 3 days), after signing it",
      "if: steps.assemble.outputs.deploy == 'true'" in site_up and "path: ${{ runner.temp }}/site" in site_up
      and "if-no-files-found: error" in site_up and re.search(r"^ +retention-days: 3$", site_up, re.M) is not None
      and "path: ${{ runner.temp }}/site" in site_dl and "path: ${{ runner.temp }}/site" in upload
      and TEXT.count("actions/upload-pages-artifact@") == 1 and "upload-pages-artifact" not in site_job, upload)
mav_up = step(build, "name: mav-part")
tt_up = step(tt_job, "name: timetables-part")
check("the build job hands its site to the site job as the mav-part artifact, only when it is to be deployed",
      "if: steps.site.outputs.deploy == 'true'" in mav_up and "path: ${{ runner.temp }}/site" in mav_up
      and "deploy: ${{ steps.site.outputs.deploy }}" in build, mav_up)
check("the timetables job uploads what it built as timetables-part, only when it built an index",
      "if: steps.indexes.outputs.built != ''" in tt_up and "path: ${{ runner.temp }}/timetables-part" in tt_up
      and "built: ${{ steps.indexes.outputs.built }}" in tt_job, tt_up)
for label, up in (("mav-part", mav_up), ("timetables-part", tt_up)):
    check(f"the {label} artifact is kept 3 days and an upload with no files fails",
          re.search(r"^ +retention-days: 3$", up, re.M) is not None and "if-no-files-found: error" in up, up)
mav_dl, tt_dl = step(site_job, "name: mav-part"), step(site_job, "name: timetables-part")
check("the site job downloads a part only when its job made one",
      "if: needs.build.outputs.deploy == 'true'" in mav_dl and "if: needs.timetables.outputs.built != ''" in tt_dl
      and "MAV_NEW: ${{ needs.build.outputs.deploy == 'true' }}" in site_job
      and "TIMETABLES_NEW: ${{ needs.timetables.outputs.built != '' }}" in site_job, repr((mav_dl, tt_dl)))
tt_steps = [ln.strip() for ln in tt_job.splitlines() if ln.strip().startswith("- ")]
check("the timetables job builds only when due, and the dispatch input forces it",
      all("if: steps.due.outputs.due == 'true'" in step(tt_job, f"- name: {n}")
          for n in ("Baseline indexes", "Build the Volán and city indexes"))
      and "FORCE: ${{ inputs.timetables == true }}" in step(tt_job, "- name: Timetables due"), repr(tt_steps))
# The attest job reads this artifact; a re-run of it after a Sigstore outage
# failed with "artifact not found" once the action's default of one day was
# over. A weekend's worth now, and the docs say how long.
_keep = re.search(r"^ +retention-days: (\d+)$", upload, re.M)
check("the Pages artifact is kept 3 days or more, for a re-run of the attestation",
      _keep is not None and int(_keep.group(1)) >= 3, upload)
_docs = (ROOT / "docs" / "timetables.md").read_text(encoding="utf-8")
check("docs/timetables.md says how long the artifact is kept for a re-run",
      _keep is not None and "Pages artifact is kept (three days)" in _docs and int(_keep.group(1)) == 3,
      repr(_keep and _keep.group(1)))
# A re-run waits in the concurrency group too, and may cancel a waiting run
# (the dispatch): the workflow and the docs say when to re-run.
check("the workflow and the docs say a re-run joins the concurrency group and when to make one",
      "Re-run when no run of this workflow is queued." in TEXT and "joins the concurrency group" in TEXT
      and "re-run when no run of the workflow is queued" in _docs)
site_s = step(build, "- name: Pages site")
check("the site is made only from a build that went through",
      "if: steps.mav.outputs.built == 'true'" in site_s and "secrets." not in site_s, site_s)
secret_lines = [ln.strip() for ln in TEXT.splitlines() if "secrets." in ln]
city_secrets = [
    "BAHART_GTFS_URL: ${{ secrets.BAHART_GTFS_URL }}",
    "MOBILITY_DATABASE_REFRESH_TOKEN: ${{ secrets.MOBILITY_DATABASE_REFRESH_TOKEN }}",
    "TRANSITLAND_API_KEY: ${{ secrets.TRANSITLAND_API_KEY }}",
]
sign_step = step(sign_job, "- name: Sign the manifest")
check("the MÁV secrets reach only the MÁV build step's env (and the job's yes/no flag), the city feeds'"
      " only the timetables build step's, the signing key only the sign job's signing step",
      sorted(secret_lines) == sorted([
          "MAV_SECRETS: ${{ secrets.MAV_GTFS_USER != '' && secrets.MAV_GTFS_PASSWORD != '' }}",
          "MAV_GTFS_USER: ${{ secrets.MAV_GTFS_USER }}",
          "MAV_GTFS_PASSWORD: ${{ secrets.MAV_GTFS_PASSWORD }}",
          *city_secrets,
          "PAGES_SIGNING_KEY: ${{ secrets.PAGES_SIGNING_KEY }}",
      ]) and "secrets.MAV_GTFS" in step(build, "- name: MÁV GTFS, pfaedle, build and audit")
      and all(ln in step(tt_job, "- name: Build the Volán and city indexes") for ln in city_secrets)
      and "secrets." not in site_job and "secrets." not in sign_job.replace(sign_step, "")
      and "secrets.MAV" not in tt_job
      and re.search(r"^        env:\n          PAGES_SIGNING_KEY: \$\{\{ secrets\.PAGES_SIGNING_KEY \}\}\n        run:",
                    sign_step, re.M) is not None, repr(secret_lines))
sign_steps = [ln.strip() for ln in sign_job.splitlines() if re.match(r"^      - ", ln)]
sign_co = step(sign_job, "actions/checkout@")
check("every deploy signs timetables.json: the sign job, bound to the github-pages environment (whose secret"
      " the key is, main only), checks out only sign_manifest.py without credentials, signs the downloaded site"
      " and only then makes the Pages artifact; a failure stops the deploy",
      re.search(r"^    environment:\n      name: github-pages\n", sign_job, re.M) is not None
      and sign_steps == ["- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
                         "- uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2",
                         "- name: No newer site on GitHub Pages",
                         "- name: Sign the manifest",
                         "- uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5.0.0"]
      and "persist-credentials: false" in sign_co and "scripts/sign_manifest.py" in sign_co
      and "sparse-checkout-cone-mode: false" in sign_co
      and 'run: python3 scripts/sign_manifest.py sign --site "$RUNNER_TEMP/site"' in sign_step
      and "if:" not in sign_step and "if:" not in upload
      and "continue-on-error" not in sign_job, repr(sign_steps))
# "Re-run failed jobs" after a failed sign job signs this run's artifact
# (kept 3 days) and deploys it: never over a newer run's site.
live_check = step(sign_job, "- name: No newer site on GitHub Pages")
check("the sign job refuses a site older than the one GitHub Pages serves (sign_manifest.py live-check), before"
      " the key is read, and the workflow no longer invites a re-run of a failed sign job without saying so",
      'python3 scripts/sign_manifest.py live-check --site "$RUNNER_TEMP/site" --live-url "$pages_url"' in live_check
      and 'pages_url="https://${owner}.github.io/${GITHUB_REPOSITORY#*/}"' in live_check
      and "secrets." not in live_check and "if:" not in live_check and "continue-on-error" not in live_check
      and sign_job.index("- name: No newer site on GitHub Pages") < sign_job.index("- name: Sign the manifest")
      and "\"Re-run failed jobs\" after a failed sign job\n      # finds it." not in site_job
      and "sign job refuses it once GitHub Pages serves a newer site" in site_job, live_check)
asm_step = step(site_job, "- name: Assemble the site")
check("the site job never carries a published manifest older than the build and timetables jobs read:"
      " their live_built outputs reach assemble as --mav-built-at and --timetables-built-at, only when set",
      "live_built: ${{ steps.site.outputs.live_built }}" in build
      and "live_built: ${{ steps.due.outputs.live_built }}" in tt_job
      and "MAV_BUILT: ${{ needs.build.outputs.live_built }}" in asm_step
      and "TIMETABLES_BUILT: ${{ needs.timetables.outputs.live_built }}" in asm_step
      and 'if [ -n "${MAV_BUILT:-}" ]; then parts+=(--mav-built-at "$MAV_BUILT"); fi' in asm_step
      and 'if [ -n "${TIMETABLES_BUILT:-}" ]; then parts+=(--timetables-built-at "$TIMETABLES_BUILT"); fi'
      in asm_step, asm_step)
release_text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
check("the timetables build reads the release's variables for the Volán URL and the city guard",
      all(v in step(tt_job, "- name: Build the Volán and city indexes") for v in (
          "VOLAN_GTFS_URL: ${{ vars.VOLAN_GTFS_URL || '' }}",
          "ALLOW_MISSING_VAR: ${{ vars.HUNGARIAN_TRANSPORT_ALLOW_MISSING || '' }}"))
      and "VOLAN_GTFS_URL: ${{ vars.VOLAN_GTFS_URL || '' }}" in release_text
      and "vars.HUNGARIAN_TRANSPORT_ALLOW_MISSING" in release_text)
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
deploy_steps = [ln.strip() for ln in deploy.splitlines() if ln.strip().startswith("- ")]
attest_steps = [ln.strip() for ln in attest_job.splitlines() if ln.strip().startswith("- ")]
attest = step(attest_job, "actions/attest@")
fetched = step(attest_job, "actions/download-artifact@")
check("only the attest job may write attestations, with an OIDC token and nothing else",
      "attestations: write" not in build and "attestations: write" not in deploy
      and re.search(r"^    permissions:\n      id-token: write\n      attestations: write\n    \S",
                    attest_job, re.M) is not None, attest_job)
# "Re-run failed jobs" after a failed attestation reruns the failed job and
# the ones that need it: were the attestation in the deploy job, the re-run
# would deploy this run's artifact again, maybe over a newer run's index.
check("the deploy job ends with the deploy: no attestation step a re-run would deploy again with",
      deploy_steps[-1].startswith("- uses: actions/deploy-pages@") and "actions/attest@" not in deploy
      and "download-artifact" not in deploy, repr(deploy_steps))
check("the attest job runs after the deploy, never deploys, and runs nothing from the repository",
      "    needs: [build, timetables, deploy]" in attest_job
      and "    if: ${{ !cancelled() && needs.deploy.result == 'success' }}" in attest_job
      and "environment:" not in attest_job
      and "deploy-pages" not in attest_job and "actions/checkout" not in attest_job
      and "secrets." not in attest_job and "scripts/" not in attest_job, attest_job)
check("the deployed files are attested from the artifact Pages deployed",
      [s_.split("@")[0] for s_ in attest_steps] == [
          "- uses: actions/download-artifact", "- name: Unpack the deployed site", "- uses: actions/attest"]
      and "name: github-pages" in fetched and "path: ${{ runner.temp }}/pages" in fetched
      and "subject-path: ${{ steps.files.outputs.subjects }}" in attest
      and "id: files" in step(attest_job, "- name: Unpack the deployed site"),
      repr((attest_steps, attest)))
# The documented check pins the branch: a dispatch from another branch signs
# with a workflow file of the same path.
verify_docs = {"the workflow": TEXT, "docs/timetables.md": (ROOT / "docs" / "timetables.md").read_text(encoding="utf-8")}
for where, text in verify_docs.items():
    cmds = re.findall(r"gh attestation verify mav-(?:[^\n]*\\\n)*[^\n]*", text)
    check(f"{where}: the documented attestation check pins the workflow and the main branch",
          bool(cmds) and all("--signer-workflow bator/hungarian-transport/.github/workflows/mav-index-daily.yml" in c
                             and "--source-ref refs/heads/main" in c for c in cmds), repr(cmds))
check("the attestation is the one the release uses (same action, same pin)",
      re.search(r"uses: (actions/attest@[0-9a-f]{40})", attest) is not None
      and re.search(r"uses: (actions/attest@[0-9a-f]{40})", attest).group(1)
      in (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"), attest)

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("daily MÁV index tests passed")
