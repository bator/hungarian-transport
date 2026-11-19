#!/usr/bin/env python3
"""Steps of release.yml's build job, run as written there (the run: blocks,
extracted and executed with bash) in a scratch workspace: "MÁV index
(pfaedle)" with a stub release_mav_index.sh, and "Stamp release and pack
assets". No network."""
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
WORKFLOW = HERE.parent / ".github" / "workflows" / "release.yml"

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
PACK_STEP = step_script("Stamp release and pack assets")

# The release's own script, stubbed: writes mav-index.json.gz as a
# successful build does, or fails.
MAV_SCRIPT_STUB = """#!/usr/bin/env bash
set -euo pipefail
if [ "${STUB_MAV:-}" = ok ]; then
  python3 -c 'import gzip, json, os
body = {"v": 7, "feed": "new"}
n = int(os.environ.get("STUB_MAV_TRAINS") or 0)
if n:
    body["n"] = {str(i): [] for i in range(n)}
open("mav-index.json.gz", "wb").write(gzip.compress(json.dumps(body).encode(), mtime=0))'
  exit 0
fi
echo "stub: MÁV download failed" >&2
exit 1
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
    # The real guard: standard library only, as in .release-tools.
    (tools / "mav_shrink_guard.py").write_text((HERE / "mav_shrink_guard.py").read_text(encoding="utf-8"))
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

    # Without the secrets: the previous release's index ships again.
    ws = workspace(tmp / "a", baseline_mav=OLD)
    write_gz(ws / "dist" / "mav-index.json.gz", {"v": 7, "feed": "stale"})
    done, out = bash(MAV_STEP, ws, MAV_SECRETS="false")
    check("no secrets: the previous release's MÁV index ships again",
          done.returncode == 0 and dist_mav(ws) == OLD and "built" not in out,
          repr((done.returncode, dist_mav(ws), done.stdout, done.stderr)))

    ws = workspace(tmp / "b", baseline_mav={"v": 6, "feed": "old"})
    write_gz(ws / "dist" / "mav-index.json.gz", {"v": 7, "feed": "stale"})
    done, _ = bash(MAV_STEP, ws, MAV_SECRETS="false")
    check("no secrets, previous index in a schema this code cannot read: none ships",
          done.returncode == 0 and dist_mav(ws) is None, repr((dist_mav(ws), done.stdout, done.stderr)))
    check("the build job writes no bytecode: the schema check leaves no __pycache__ in the package",
          not (ws / "custom_components" / "hungarian_transport" / "__pycache__").exists())

    ws = workspace(tmp / "c", baseline_mav=None)
    done, _ = bash(MAV_STEP, ws, MAV_SECRETS="false")
    check("no secrets, no previous index: none ships", done.returncode == 0 and dist_mav(ws) is None,
          repr((done.stdout, done.stderr)))

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

    # A build with under half the previous release's trains and lines (a
    # truncated feed that passed the build's checks) does not ship: the
    # previous index does, as on a failed build. MAV_ALLOW_SHRINK takes it.
    trains = {str(i): [] for i in range(100)}
    big = {"v": 7, "feed": "old", "n": trains}
    for label, n, env, want_new in (
        ("49 of 100 trains: the previous index ships, with a warning", 49, {}, False),
        ("50 of 100 trains: the new index ships", 50, {}, True),
        ("49 of 100 trains with MAV_ALLOW_SHRINK=1: the new index ships", 49, {"MAV_ALLOW_SHRINK": "1"}, True),
    ):
        ws = workspace(tmp / f"shrink-{n}-{len(env)}", baseline_mav=big)
        done, out = bash(MAV_STEP, ws, MAV_SECRETS="true", STUB_MAV="ok", STUB_MAV_TRAINS=str(n), **env)
        got = dist_mav(ws) or {}
        if want_new:
            ok = got.get("feed") == "new" and len(got.get("n") or {}) == n and out.get("built") == "true"
        else:
            ok = (got == big and "built" not in out and "under half the trains" in done.stdout
                  and not (ws / "mav-index.json.gz").exists())
        check(f"secrets, {label}", done.returncode == 0 and ok,
              repr((done.returncode, out, done.stdout, done.stderr)))
    ws = workspace(tmp / "shrink-old-schema", baseline_mav={"v": 6, "feed": "old", "n": trains})
    done, out = bash(MAV_STEP, ws, MAV_SECRETS="true", STUB_MAV="ok", STUB_MAV_TRAINS="10")
    check("secrets, a smaller build than a previous index this code cannot read: the new index ships",
          done.returncode == 0 and (dist_mav(ws) or {}).get("feed") == "new" and out.get("built") == "true",
          repr((done.returncode, out, done.stdout, done.stderr)))

    ws = workspace(tmp / "f", baseline_mav=None)
    done, out = bash(MAV_STEP, ws, MAV_SECRETS="true", STUB_MAV="ok")
    check("secrets, no previous index: the new index ships",
          done.returncode == 0 and (dist_mav(ws) or {}).get("feed") == "new" and out.get("built") == "true",
          repr((done.returncode, out, done.stdout, done.stderr)))

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
    done, _ = bash(PACK_STEP, ws, TAG="v1.2.3", GITHUB_SHA="0" * 40, GITHUB_EVENT_NAME="workflow_dispatch",
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
        # A tag push (a stable cut by pushing its tag) takes its notes too.
        done, _ = bash(PACK_STEP, ws, TAG="v1.2.3", GITHUB_SHA="0" * 40, GITHUB_EVENT_NAME="push",
                       GITHUB_SERVER_URL="https://github.com", GITHUB_REPOSITORY="o/r", GITHUB_RUN_ID="1")
        check("a pushed stable tag packs its release notes",
              done.returncode == 0 and (ws / "release-notes.md").read_text() == (ws / "docs" / "releases" / "1.2.3.md").read_text(),
              repr(done.stderr[-400:]))

# The documented check pins the release workflow and the ref the run started
# from: without --signer-workflow any workflow of the repository passes (the
# daily Pages one signs with the same repository), and the refs are the ones
# the workflow runs from: main for the manual runs, which make the releases
# (a manual run from another branch is refused), the tag on a push. The example uses main: a refs/tags/ one passed no release there is
# (v1.9.3 has no attestation; v1.9.10 to v1.10.20 started on main).
_wf_text = WORKFLOW.read_text(encoding="utf-8")
for _where, _text in {"release.yml": _wf_text,
                      "docs/timetables.md": (HERE.parent / "docs" / "timetables.md").read_text(encoding="utf-8")}.items():
    _cmds = [c.replace("#", "") for c in re.findall(r"gh attestation verify hungarian-transport\.zip(?:[^\n]*\\\n)*[^\n]*", _text)]
    check(f"{_where}: the documented release check pins the release workflow and a source ref",
          bool(_cmds) and all(re.search(r"--signer-workflow \S+/\.github/workflows/release\.yml(\s|$)", c)
                              and re.search(r"--source-ref refs/(heads/main|tags/\S+)(\s|$)", c) for c in _cmds),
          repr(_cmds))
    check(f"{_where}: the documented release check uses refs/heads/main, where the releases start",
          bool(_cmds) and all(re.search(r"--source-ref refs/heads/main(\s|$)", c) for c in _cmds), repr(_cmds))
    _at = _text.find("gh attestation verify hungarian-transport.zip")
    check(f"{_where}: next to the check, refs/tags/<tag> is named for a release cut by pushing its tag",
          "refs/tags/<tag>" in _text[_at:_at + 900], _text[_at:_at + 900])
# A code release always publishes: no step decides that the indexes did not
# change (that was the weekly timetable release's), and nothing in the
# workflow runs on a schedule or carries a city feed over.
_publish = _wf_text[_wf_text.index("\n  publish:\n"):]
_publish_if = re.search(r"^    if: (.*)$", _publish, re.M)
check("every resolved release is published: the publish job waits on no change check",
      _publish_if is not None and _publish_if.group(1) == "needs.resolve.outputs.create == 'true'"
      and "Did a timetable change" not in _wf_text and "outputs.changed" not in _wf_text,
      repr(_publish_if.group(0) if _publish_if else None))
check("release.yml has no schedule trigger and no timetable refresh path",
      "schedule:" not in _wf_text and "refresh" not in _wf_text.replace("REFRESH_TOKEN", "")
      and "HUNGARIAN_TRANSPORT_CARRY_INDEX" not in _wf_text and "\n  beta:\n" not in _wf_text)
# A stable release starts the daily Pages workflow, which rebuilds the Volán
# and city indexes with the new release's builders and against its indexes
# (timetables.json's release_feed and release_version): only after the
# release is published, not for a -rev prerelease, only for the highest
# stable release (not a backport tag of an older line), from a job that may
# start workflows and list releases and nothing else, and a failure to list
# the releases or to start it is a warning (the cron catches up).
_pages = _wf_text[_wf_text.index("\n  pages:\n"):] if "\n  pages:\n" in _wf_text else ""
check("after a stable release the Pages timetables are rebuilt at once: mav-index-daily.yml on main with"
      " timetables=true, by a job of its own that may only start workflows and read the releases",
      "    needs: [resolve, publish]" in _pages
      and "    if: needs.resolve.outputs.create == 'true' && !contains(needs.resolve.outputs.tag, '-rev.')" in _pages
      and re.search(r"^    permissions:\n      actions: write\n      contents: read\n    steps:", _pages, re.M) is not None
      and "gh workflow run mav-index-daily.yml --ref main -f timetables=true" in _pages
      and "::warning title=Pages timetables not started::" in _pages
      and "actions/checkout" not in _pages and "secrets." not in _pages
      and re.findall(r"^  ([\w-]+):$", _wf_text.split("\njobs:\n", 1)[1], re.M)[-1] == "pages",
      _pages[:800])
with tempfile.TemporaryDirectory() as _tmp:
    _ws = Path(_tmp)
    (_ws / "bin").mkdir()
    # gh api: the stable tags (STUB_TAGS, as the --jq filter leaves them),
    # or an API failure; gh workflow run: its arguments written down.
    (_ws / "bin" / "gh").write_text(
        '#!/usr/bin/env bash\n'
        'if [ "$1" = api ]; then\n'
        '  [ "${STUB_API:-}" != fail ] || { echo "HTTP 502" >&2; exit 1; }\n'
        '  printf "%s\\n" $STUB_TAGS; exit 0\n'
        'fi\n'
        'echo "$*" > "$PWD/gh-args.txt"\n'
        '[ "${STUB_GH:-}" != fail ] || { echo "HTTP 403" >&2; exit 1; }\n')
    (_ws / "bin" / "gh").chmod(0o755)
    try:
        _start = step_script("Start mav-index-daily.yml")
    except StopIteration:
        _start = "exit 1\n"
    _run_args = ["workflow", "run", "mav-index-daily.yml", "--ref", "main", "-f", "timetables=true"]
    for _case, _tag, _tags, _env, _want, _started in (
        ("the highest stable", "v1.2.3", "v1.1.9 v1.2.3 v1.2.2", {},
         "mav-index-daily.yml started on main (timetables=true) for v1.2.3", True),
        ("the highest by version, not by text (v1.10.0 above v1.9.5)", "v1.10.0", "v1.9.5 v1.10.0", {},
         "started on main", True),
        ("a backport tag below the highest stable", "v1.1.10", "v1.1.10 v1.2.3 v1.1.9", {},
         "v1.1.10 is not the highest stable release (v1.2.3)", False),
        ("the highest stable, gh workflow run fails", "v1.2.3", "v1.2.3", {"STUB_GH": "fail"},
         "::warning title=Pages timetables not started::Could not start", True),
        ("the releases cannot be listed", "v1.2.3", "v1.2.3", {"STUB_API": "fail"},
         "::warning title=Pages timetables not started::Could not list the releases", False),
    ):
        (_ws / "gh-args.txt").unlink(missing_ok=True)
        _done, _ = bash(_start, _ws, PATH=f"{_ws / 'bin'}:{os.environ['PATH']}", TAG=_tag, STUB_TAGS=_tags,
                        GH_TOKEN="x", GH_REPO="o/r", **_env)
        _ran = (_ws / "gh-args.txt").read_text().split() if (_ws / "gh-args.txt").exists() else None
        check(f"the Pages start step, {_case}: exit 0, " + ("started" if _started else "not started"),
              _done.returncode == 0 and _want in _done.stdout and _ran == (_run_args if _started else None),
              repr((_done.returncode, _done.stdout, _done.stderr, _ran)))
check("the release runs from a v* tag push or from main",
      re.search(r"^  push:\n    tags:\n      - \"v\*\"$", _wf_text, re.M) is not None
      and '[ "${GITHUB_EVENT_NAME}" = "workflow_dispatch" ] && [ "${GITHUB_REF}" != "refs/heads/main" ]' in _wf_text)

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("release workflow tests passed")
