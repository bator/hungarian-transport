#!/usr/bin/env python3
"""Steps of release.yml's build job, run as written there (the run: blocks,
extracted and executed with bash) in a scratch workspace: "MÁV index
(pfaedle)" with a stub release_mav_index.sh, "Did a timetable change" with a
stub index_digest.py, and "Stamp release and pack assets". No network."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        failed += 1


def step_script(name: str) -> str:
    """The run: block of the step with this name, dedented."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == f"- name: {name}")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for ln in lines[run + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        body.append(ln[indent:])
    return "\n".join(body) + "\n"


def build_job_env() -> dict[str, str]:
    """The build job's env: entries that are plain strings (no expressions)."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln == "  build:")
    env_at = next(i for i in range(start, len(lines)) if lines[i] == "    env:")
    env = {}
    for ln in lines[env_at + 1:]:
        if ln.strip() and not ln.startswith("      "):
            break
        key, _, value = ln.strip().partition(": ")
        if value.startswith('"') and value.endswith('"'):
            env[key] = value[1:-1]
    return env


JOB_ENV = build_job_env()
MAV_STEP = step_script("MÁV index (pfaedle)")
CHANGED_STEP = step_script("Did a timetable change")
PACK_STEP = step_script("Stamp release and pack assets")

# The release's own script, stubbed: writes mav-index.json.gz as a
# successful build does, or fails.
MAV_SCRIPT_STUB = """#!/usr/bin/env bash
set -euo pipefail
if [ "${STUB_MAV:-}" = ok ]; then
  python3 -c 'import gzip, json; open("mav-index.json.gz", "wb").write(gzip.compress(json.dumps({"v": 7, "feed": "new"}).encode(), mtime=0))'
  exit 0
fi
echo "stub: MÁV download failed" >&2
exit 1
"""
# Content digest stand-in: the decompressed JSON, keys sorted.
DIGEST_STUB = """import gzip, hashlib, json, sys
data = json.loads(gzip.decompress(open(sys.argv[1], "rb").read()))
print(hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(), sys.argv[1])
"""


def write_gz(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(obj).encode(), mtime=0))


def workspace(root: Path, *, baseline_mav: dict | None, schema: int = 7) -> Path:
    ws = root
    (ws / "dist").mkdir(parents=True)
    (ws / ".baseline").mkdir()
    tools = ws / ".release-tools" / "scripts"
    tools.mkdir(parents=True)
    (tools / "release_mav_index.sh").write_text(MAV_SCRIPT_STUB)
    (tools / "index_digest.py").write_text(DIGEST_STUB)
    pkg = ws / "custom_components" / "hungarian_transport"
    pkg.mkdir(parents=True)
    (pkg / "mav_index.py").write_text(f"SCHEMA = {schema}\n")
    for name in ("volan-index.json.gz", "city-index.json.gz"):
        write_gz(ws / name, {"v": 1, "name": name})
        write_gz(ws / ".baseline" / name, {"v": 1, "name": name})
    if baseline_mav is not None:
        write_gz(ws / ".baseline" / "mav-index.json.gz", baseline_mav)
    return ws


def bash(script: str, cwd: Path, **env: str) -> tuple[subprocess.CompletedProcess, dict]:
    out = cwd / f"github_output_{len(list(cwd.glob('github_output_*')))}"
    out.write_text("")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("MAV_", "GITHUB_"))}
    e.pop("PYTHONDONTWRITEBYTECODE", None)
    e.update(JOB_ENV)
    e.update({"GITHUB_OUTPUT": str(out), "RUNNER_TEMP": str(cwd / "runner-temp")})
    e.update(env)
    path = cwd / "step.sh"
    path.write_text(script)
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
                          cwd=cwd, env=e, capture_output=True, text=True, timeout=60)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return done, outputs


def dist_mav(ws: Path) -> dict | None:
    p = ws / "dist" / "mav-index.json.gz"
    return json.loads(gzip.decompress(p.read_bytes())) if p.exists() else None


OLD = {"v": 7, "feed": "old"}

with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)

    # Without the secrets: the previous release's index ships again (the
    # weekly run used to drop it, and count that as a timetable change).
    ws = workspace(tmp / "a", baseline_mav=OLD)
    write_gz(ws / "dist" / "mav-index.json.gz", {"v": 7, "feed": "stale"})
    done, out = bash(MAV_STEP, ws, MAV_SECRETS="false")
    check("no secrets: the previous release's MÁV index ships again",
          done.returncode == 0 and dist_mav(ws) == OLD and "built" not in out,
          repr((done.returncode, dist_mav(ws), done.stdout, done.stderr)))
    done, out = bash(CHANGED_STEP, ws, REFRESH="true")
    check("no secrets: the weekly refresh sees no timetable change",
          done.returncode == 0 and out.get("changed") == "false", repr((out, done.stdout, done.stderr)))

    ws = workspace(tmp / "b", baseline_mav={"v": 6, "feed": "old"})
    write_gz(ws / "dist" / "mav-index.json.gz", {"v": 7, "feed": "stale"})
    done, _ = bash(MAV_STEP, ws, MAV_SECRETS="false")
    check("no secrets, previous index in a schema this code cannot read: none ships",
          done.returncode == 0 and dist_mav(ws) is None, repr((dist_mav(ws), done.stdout, done.stderr)))
    done, out = bash(CHANGED_STEP, ws, REFRESH="true")
    check("an index that went away is no timetable change by itself",
          out.get("changed") == "false" and "not counted as a change" in done.stdout, repr((out, done.stdout)))
    check("the build job writes no bytecode: the schema check leaves no __pycache__ in the package",
          not (ws / "custom_components" / "hungarian_transport" / "__pycache__").exists())

    ws = workspace(tmp / "c", baseline_mav=None)
    done, _ = bash(MAV_STEP, ws, MAV_SECRETS="false")
    check("no secrets, no previous index: none ships", done.returncode == 0 and dist_mav(ws) is None,
          repr((done.stdout, done.stderr)))
    done, out = bash(CHANGED_STEP, ws, REFRESH="true")
    check("no index in either release: no change", out.get("changed") == "false" and "neither" in done.stdout,
          repr((out, done.stdout)))

    # With the secrets: a failed build keeps the previous index (as before);
    # a successful one ships and lets the OSM cache be saved.
    ws = workspace(tmp / "d", baseline_mav=OLD)
    done, out = bash(MAV_STEP, ws, MAV_SECRETS="true", STUB_MAV="fail")
    check("secrets, failed build: the previous index ships, with a warning, no cache save",
          done.returncode == 0 and dist_mav(ws) == OLD and "MÁV index not rebuilt" in done.stdout
          and "built" not in out, repr((done.returncode, out, done.stdout, done.stderr)))

    ws = workspace(tmp / "e", baseline_mav=OLD)
    done, out = bash(MAV_STEP, ws, MAV_SECRETS="true", STUB_MAV="ok")
    check("secrets, successful build: the new index ships and built=true",
          done.returncode == 0 and dist_mav(ws) == {"v": 7, "feed": "new"} and out.get("built") == "true",
          repr((done.returncode, out, done.stdout, done.stderr)))
    done, out = bash(CHANGED_STEP, ws, REFRESH="true")
    check("a new MÁV feed is a timetable change", out.get("changed") == "true"
          and "mav-index.json.gz changed" in done.stdout, repr((out, done.stdout)))

    ws = workspace(tmp / "f", baseline_mav=None)
    bash(MAV_STEP, ws, MAV_SECRETS="true", STUB_MAV="ok")
    done, out = bash(CHANGED_STEP, ws, REFRESH="true")
    check("a MÁV index that appeared is a change", out.get("changed") == "true", repr((out, done.stdout)))

    ws = workspace(tmp / "g", baseline_mav=OLD)
    bash(MAV_STEP, ws, MAV_SECRETS="false")
    write_gz(ws / "volan-index.json.gz", {"v": 1, "name": "volan", "new": True})
    done, out = bash(CHANGED_STEP, ws, REFRESH="true")
    check("a Volán change still publishes", out.get("changed") == "true"
          and "volan-index.json.gz changed" in done.stdout, repr((out, done.stdout)))
    done, out = bash(CHANGED_STEP, ws, REFRESH="false")
    check("not a refresh: always changed", out.get("changed") == "true", repr(out))

    # The pack step: no bytecode in the integration zip, every frontend file
    # (shapes folders, Leaflet, the MÁV index) in the frontend zip.
    ws = tmp / "pack"
    pkg = ws / "custom_components" / "hungarian_transport"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "__pycache__" / "mav_index.cpython-312.pyc").write_bytes(b"\0")
    (pkg / "stray.pyc").write_bytes(b"\0")
    (pkg / "mav_index.py").write_text("SCHEMA = 7\n")
    (pkg / "manifest.json").write_text(json.dumps({"domain": "hungarian_transport", "version": "0.0.0"}))
    dist = ws / "dist"
    (dist / "leaflet").mkdir(parents=True)
    (dist / "leaflet" / "leaflet.js").write_text("// leaflet")
    (dist / "volan-shapes-abc").mkdir()
    (dist / "volan-shapes-abc" / "00.json.gz").write_bytes(b"x")
    (dist / "hungarian-transport.js").write_text('var CARD_VERSION = "0.0.0";\n')
    (dist / "hungarian-transport-loader.js").write_text("// loader\n")
    for name in ("volan-index.json.gz", "city-index.json.gz", "mav-index.json.gz"):
        write_gz(dist / name, {"v": 1})
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "commit", "-q", "--allow-empty", "-m", "t"], check=True)
    done, _ = bash(PACK_STEP, ws, TAG="v1.2.3", GITHUB_SHA="0" * 40, GITHUB_EVENT_NAME="schedule",
                   GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="1")
    check("the pack step runs", done.returncode == 0, repr((done.stdout[-500:], done.stderr[-800:])))
    if done.returncode == 0:
        with zipfile.ZipFile(ws / "hungarian-transport.zip") as z:
            names = z.namelist()
        check("the integration zip has no __pycache__ or .pyc",
              not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
              and "mav_index.py" in names and "www/mav-index.json.gz" in names, repr(names))
        with zipfile.ZipFile(ws / "hungarian-transport-frontend.zip") as z:
            front = set(z.namelist())
        want = {"hungarian-transport.js", "volan-index.json.gz", "city-index.json.gz", "mav-index.json.gz",
                "leaflet/leaflet.js", "volan-shapes-abc/00.json.gz"}
        check("the frontend zip has the bundle, the indexes, Leaflet and the shapes", want <= front, repr(front))
        sums = (ws / "SHA256SUMS").read_text()
        check("SHA256SUMS covers both zips",
              hashlib.sha256((ws / "hungarian-transport.zip").read_bytes()).hexdigest() in sums
              and "hungarian-transport-frontend.zip" in sums, sums)
        check("without docs/releases/<version>.md the packed release notes file is empty",
              (ws / "release-notes.md").exists() and (ws / "release-notes.md").stat().st_size == 0)
        # The notes written for the version ride along to the publish job; a
        # -rev prerelease never takes them.
        (ws / "docs" / "releases").mkdir(parents=True)
        (ws / "docs" / "releases" / "1.2.3.md").write_text("Hungarian transport 1.2.3.\n\n## Fixes\n\n- one\n")
        done, _ = bash(PACK_STEP, ws, TAG="v1.2.3", GITHUB_SHA="0" * 40, GITHUB_EVENT_NAME="workflow_dispatch",
                       GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="1")
        check("docs/releases/<version>.md is packed as the release notes",
              done.returncode == 0 and (ws / "release-notes.md").read_text() == (ws / "docs" / "releases" / "1.2.3.md").read_text(),
              repr(done.stderr[-400:]))
        done, _ = bash(PACK_STEP, ws, TAG="v1.2.3-rev.1", GITHUB_SHA="0" * 40, GITHUB_EVENT_NAME="workflow_dispatch",
                       GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="1")
        check("a -rev prerelease takes no notes file",
              done.returncode == 0 and (ws / "release-notes.md").stat().st_size == 0, repr(done.stderr[-400:]))

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("release workflow tests passed")
