#!/usr/bin/env python3
"""The release workflow's "Resolve tag" step, run as written in release.yml
against a stub gh (releases from a fixture, filtered by the step's own --jq
through jq) and a stub git. No network."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"
COMMIT = "5c5843a0000000000000000000000000000000aa"

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        failed += 1


def resolve_script() -> str:
    """The run: block of the step named "Resolve tag", dedented."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "- name: Resolve tag" and "id: ver" in lines[i + 1])
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip()) + 2
    body = []
    for ln in lines[run + 1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) < indent:
            break
        body.append(ln[indent:])
    return "\n".join(body) + "\n"


GH_STUB = """#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
if os.environ.get("STUB_FAIL") == "1":
    print("HTTP 502: Bad Gateway", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["release", "list"]:
    jq = args[args.index("--jq") + 1]
    fields = args[args.index("--json") + 1].split(",")
    rows = [{k: r[k] for k in fields} for r in json.loads(os.environ["STUB_RELEASES"])]
    sys.exit(subprocess.run(["jq", "-r", jq], input=json.dumps(rows), text=True).returncode)
if args[:1] == ["api"] and "/git/ref/tags/" in args[1]:
    tag = args[1].rsplit("/", 1)[1]
    if tag in os.environ.get("STUB_BARE_TAGS", "").split():
        print("refs/tags/" + tag)
        sys.exit(0)
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    sys.exit(1)
print("unexpected gh " + " ".join(args), file=sys.stderr)
sys.exit(2)
"""

GIT_STUB = f"""#!/bin/sh
if [ "$1" = rev-parse ]; then echo {COMMIT}; exit 0; fi
echo "unexpected git $*" >&2
exit 2
"""


def rel(tag: str, created: str, published: str | None = None) -> dict:
    return {
        "tagName": tag,
        "isPrerelease": "-rev." in tag,
        "createdAt": created,
        "publishedAt": published or created,
    }


def run(releases: list[dict], event: str = "schedule", bump: str = "", ref_type: str = "branch",
        ref_name: str = "main", fail: bool = False, bare: str = "") -> tuple[int, dict, str]:
    """Run the step; releases are in gh's own order (createdAt, newest first)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        bindir = tmpdir / "bin"
        bindir.mkdir()
        for name, body in (("gh", GH_STUB), ("git", GIT_STUB)):
            p = bindir / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        script = tmpdir / "resolve.sh"
        script.write_text(resolve_script())
        out = tmpdir / "output"
        out.touch()
        env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(out),
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF_TYPE": ref_type,
            "GITHUB_REF_NAME": ref_name,
            "GITHUB_SHA": "f" * 40,
            "GITHUB_REPOSITORY": "bator/hungarian-transport",
            "BUMP": bump,
            "STUB_RELEASES": json.dumps(releases),
            "STUB_FAIL": "1" if fail else "0",
            "STUB_BARE_TAGS": bare,
        }
        proc = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60)
        outputs = dict(ln.split("=", 1) for ln in out.read_text().splitlines() if "=" in ln)
        return proc.returncode, outputs, proc.stdout + proc.stderr


if not shutil.which("jq"):
    print("FAIL jq is required to run the release resolve tests")
    sys.exit(1)

# v1.9.15 is a weekly timetable release tagged on v1.9.14's commit: same
# createdAt, and gh may list the older one first on the tie.
T = "2026-10-21T07:33:17Z"
tie = [rel("v1.9.14", T, "2026-10-21T07:40:10Z"), rel("v1.9.15", T, "2026-10-26T02:41:00Z"),
       rel("v1.9.13", "2026-10-20T05:39:11Z")]
code, o, log = run(tie)
check("weekly: tie on createdAt picks the higher stable", code == 0 and o.get("tag") == "v1.9.16"
      and o.get("ref") == "refs/tags/v1.9.15" and o.get("baseline") == "v1.9.15"
      and o.get("target") == COMMIT and o.get("refresh") == "true" and o.get("beta") == "false", f"{code} {o} {log}")
code, o, log = run(tie, event="workflow_dispatch", bump="rev")
check("rev dispatch: tie on createdAt numbers from the higher stable",
      code == 0 and o.get("tag") == "v1.9.17-rev.1" and o.get("baseline") == "v1.9.15", f"{code} {o} {log}")
code, o, log = run(tie, event="push", ref_type="tag", ref_name="v1.9.16")
check("tag push: baseline is the last published release", code == 0 and o.get("tag") == "v1.9.16"
      and o.get("baseline") == "v1.9.15", f"{code} {o} {log}")

# An older version tagged (and published) after the newest one.
late = [rel("v1.8.9", "2026-10-21T09:00:00Z"), rel("v1.9.14", T), rel("v1.9.13", "2026-10-20T05:39:11Z")]
code, o, log = run(late)
check("weekly: an older tag created later does not win", code == 0 and o.get("tag") == "v1.9.15"
      and o.get("ref") == "refs/tags/v1.9.14", f"{code} {o} {log}")
code, o, log = run(late, event="workflow_dispatch", bump="patch")
check("patch dispatch: an older tag created later does not win", code == 0 and o.get("tag") == "v1.9.15", f"{code} {o} {log}")

# A -rev series open two patches above the stable; 1.10.0 vs 1.9.x sorts by version.
revs = [rel("v1.9.16-rev.3", "2026-10-22T10:00:00Z"), rel("v1.9.16-rev.2", "2026-10-22T09:00:00Z"),
        rel("v1.9.14", T), rel("v1.9.9", "2026-10-19T07:05:11Z")]
code, o, log = run(revs)
check("weekly: next patch below the open -rev series, beta run follows", code == 0 and o.get("tag") == "v1.9.15"
      and o.get("ref") == "refs/tags/v1.9.14" and o.get("beta") == "true", f"{code} {o} {log}")
code, o, log = run(revs, event="workflow_dispatch", bump="rev")
check("rev dispatch continues the open series", code == 0 and o.get("tag") == "v1.9.16-rev.4"
      and o.get("baseline") == "v1.9.16-rev.3", f"{code} {o} {log}")
code, o, log = run(revs, event="workflow_dispatch", bump="patch")
check("patch dispatch takes the open series' number", code == 0 and o.get("tag") == "v1.9.16", f"{code} {o} {log}")
minor = [rel("v1.9.20", "2026-10-22T10:00:00Z"), rel("v1.10.0", T)]
code, o, log = run(minor)
check("1.10.0 ranks above 1.9.20", code == 0 and o.get("tag") == "v1.10.1", f"{code} {o} {log}")

# Fail closed.
code, o, log = run(tie, fail=True)
check("GitHub API failure stops the run", code != 0 and "tag" not in o and "Could not list" in log, f"{code} {o} {log}")
code, o, log = run(tie, bare="v1.9.16")
check("a bare tag on the next version stops the weekly run", code != 0 and "tag" not in o
      and "v1.9.16 already exists" in log, f"{code} {o} {log}")
code, o, log = run([rel("v1.9.16-rev.1", T)])
check("no stable release: nothing to refresh", code == 0 and o.get("tag") == "" and o.get("create") == "false",
      f"{code} {o} {log}")

if failed:
    sys.exit(1)
print("release resolve tests passed")
