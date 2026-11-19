#!/usr/bin/env python3
"""What the workflows must cover, read from their text: every scripts test
runs in CI and in the release, the MÁV index check runs for a change to any
file its build imports, assembles the daily Pages site offline and keeps its
artifacts when it fails, and the shell
and workflow linters run pinned and checksummed. No network."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" — {detail}"))
    if not ok:
        failed += 1


def step(text: str, marker: str) -> str:
    """The step (from its "- " line to the next one at that indent) holding
    marker; "" when no line does."""
    lines = text.splitlines()
    at = next((i for i, ln in enumerate(lines) if marker in ln), None)
    if at is None:
        return ""
    start = next(i for i in range(at, -1, -1) if lines[i].lstrip().startswith("- "))
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) <= indent), len(lines))
    return "\n".join(lines[start:end])


def local_imports(path: Path, seen: set[Path]) -> set[Path]:
    """The scripts/*.py modules path imports, and theirs."""
    if path in seen:
        return seen
    seen.add(path)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
            [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
        for name in names:
            dep = path.parent / f"{name.split('.')[0]}.py"
            if dep.is_file():
                local_imports(dep, seen)
    return seen


validate = (WF / "validate.yml").read_text(encoding="utf-8")
release = (WF / "release.yml").read_text(encoding="utf-8")
mav = (WF / "mav-index.yml").read_text(encoding="utf-8")

tests = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "scripts").glob("test_*.py"))
missing = [t for t in tests if f"python3 {t}" not in validate]
check("validate.yml runs every scripts/test_*.py", not missing, repr(missing))
loop = step(release, "- name: GTFS script and helper tests")
missing = [t for t in tests if t not in loop]
check("release.yml's GTFS test step runs every scripts/test_*.py", not missing, repr(missing))

sparse = step(release, "path: .release-tools")
used = set(re.findall(r"\.release-tools/(scripts/[\w.-]+)", release))
checked_out = set(re.findall(r"^\s+(scripts/[\w.-]+)$", sparse, re.M))
check("release.yml checks out every .release-tools script it runs",
      bool(used) and used <= checked_out, repr(sorted(used - checked_out)))

paths_block = mav.split("paths:", 1)[1].split("\n\n", 1)[0] if "paths:" in mav else ""
listed = set(re.findall(r"^\s+- (\S+)$", paths_block, re.M))
needed = {p.relative_to(ROOT).as_posix() for p in local_imports(ROOT / "scripts" / "build_mav_index.py", set())}
needed |= {"scripts/release_mav_index.sh", "custom_components/hungarian_transport/mav_index.py",
           "custom_components/hungarian_transport/gtfs_feed.py", ".github/workflows/mav-index.yml"}
check("mav-index.yml runs for a change to every file the MÁV build imports",
      needed <= listed, repr(sorted(needed - listed)))

pages_step = step(mav, "- name: Pages site (not published)")
check("mav-index.yml assembles the daily workflow's whole site offline (no live site, nothing published)",
      "scripts/pages_timetables.py" in listed and "python3 scripts/pages_timetables.py assemble" in pages_step
      and "--live-dir" in pages_step and "--live-url" not in pages_step and '--mav-part "$RUNNER_TEMP/mav-part"' in pages_step,
      pages_step)

upload = step(mav, "actions/upload-artifact@")
check("the MÁV index artifact is kept when a step failed, the second build included",
      "!cancelled()" in upload and "mav-index.again.json.gz" in upload and "mav-index.json.gz" in upload,
      upload)

build_step = step(release, "- name: Build GTFS indexes")
baseline_step = step(release, "- name: Previous release indexes")
check("a code release builds every city feed afresh against the previous release's indexes (no carried part)",
      "HUNGARIAN_TRANSPORT_CARRY_INDEX" not in release
      and "python3 scripts/build_city_index.py" in build_step
      and "unzip -o -j baseline/hungarian-transport-frontend.zip volan-index.json.gz city-index.json.gz -d ."
      in baseline_step, build_step)

integration = validate.split("\n  integration:\n", 1)[1].split("\n  integration-oldest:\n", 1)[0] \
    if "\n  integration:\n" in validate else ""
shuffled = step(integration, "- name: pytest in a random order")
check("the integration tests run a second time in a shuffled order (pytest-randomly pinned, installed only"
      " for that step, after the ordered run; the seed in the log), on the current Home Assistant only",
      re.search(r"pip install pytest-randomly==\d+\.\d+\.\d+$", shuffled, re.M) is not None
      and "--randomly-seed=\"$SEED\"" in shuffled and "-p randomly" in shuffled and "echo \"pytest-randomly seed:" in shuffled
      and "SEED: ${{ github.run_id }}" in shuffled
      and integration.index("- name: pytest\n") < integration.index("- name: pytest in a random order")
      and "randomly" not in (ROOT / "requirements_test.txt").read_text(encoding="utf-8")
      and "randomly" not in validate.split("\n  integration-oldest:\n", 1)[1], shuffled)

lint = step(validate, "- name: Install ShellCheck")
pins = re.findall(r"(SHELLCHECK|ACTIONLINT)_SHA256: ([0-9a-f]{64})$", lint, re.M)
urls = re.findall(r"(SHELLCHECK|ACTIONLINT)_URL: (https://github\.com/\S+/releases/download/v[\d.]+/\S+)$", lint, re.M)
check("ShellCheck and actionlint come from pinned release URLs with recorded sha256 sums",
      sorted(k for k, _ in pins) == ["ACTIONLINT", "SHELLCHECK"] and sorted(k for k, _ in urls) == ["ACTIONLINT", "SHELLCHECK"]
      and lint.count("sha256sum --check --strict") == 2, lint)
check("validate.yml runs ShellCheck on the shell scripts and actionlint on the workflows",
      "xargs -0 --no-run-if-empty shellcheck" in step(validate, "- name: ShellCheck")
      and re.search(r"^\s+actionlint$", step(validate, "- name: actionlint"), re.M) is not None)

if failed:
    print(f"{failed} failed")
    sys.exit(1)
print("workflow tests passed")
