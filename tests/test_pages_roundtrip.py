"""The GitHub Pages site as the daily workflow makes it, read by the
integration: Volán and city indexes from scripts/gtfs_compact.py's own
writer, packed by scripts/pages_timetables.py part (with the stable
release's feeds and tag, release_feed and release_version), a MÁV part from
scripts/mav_pages_site.py, the site assembled by pages_timetables.py
assemble and signed by scripts/sign_manifest.py (openssl) with a key made
here; then weekly_timetables' and mav_daily's own checks (the signature by
the cryptography library, the manifests, every entry, every index and
shard) on the bytes as deployed. The build side and the install side check
the same entries with their own code: this holds them together (dated
feeds, operator lists, far-feed limits, the release tag's pattern).

The scripts run in a subprocess, as the workflow runs them: nothing of
theirs (sys.path, sys.modules) is left in the test process. No network."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from custom_components.hungarian_transport import mav_daily, mav_index, weekly_timetables

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
NOW = "2026-11-16T10:40:00+00:00"
TODAY = date(2026, 11, 16)
RELEASE = "v1.10.24"
RELEASE_FEEDS = {"volan": "volan:01716.20261020", "city": "szeged:abc;pecs:20261001"}

# Builds the site in work (argv[2]) with the scripts of argv[1], as the
# workflow's timetables, build, site and sign jobs do, and writes the test
# key's public half (base64) to work/pub.txt.
DRIVER = r'''
import base64, contextlib, gzip, io, json, os, sys, zipfile
from pathlib import Path

scripts, work = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(scripts))
import gtfs_compact
import mav_pages_site
import pages_timetables as pt
import sign_manifest as sm
from gtfs_feed import encode_polyline

NOW = sys.argv[3]


def part(op, feed, trips=6):
    shapes = [encode_polyline([(47.1, 19.1), (47.2, 19.2 + k / 10)]) for k in range(3)]
    return {"op_id": op, "op_name": op.title(), "feed": feed,
            "s": [[f"{op}:1", "A", "1"], [f"{op}:2", "B", "2"]], "g": [4710000, 1910000, 4720000, 1920000],
            "shapes": shapes, "c": [["1111111", "20260101", "20991231"]], "a": [[]], "r": [[]],
            "t": [[0, str(k), [0, 1], [480 + k, 490 + k], 3, k % 3] for k in range(trips)]}


build = work / "build"
build.mkdir()
gtfs_compact.write_index([part("volan", "01716.20261102")], build / "volan-index.json.gz", with_operators=False,
                         today="20261116")
gtfs_compact.write_index([part("szeged", "abc"), part("pecs", "20261110")], build / "city-index.json.gz",
                         with_operators=True, today="20261116")
(work / "release-feeds.json").write_text(json.dumps({"indexes": json.loads(sys.argv[4]), "release": sys.argv[5]}))
schema = str(gtfs_compact.INDEX_SCHEMA)
for name in ("volan", "city"):
    rc = pt.main(["part", "--schema", schema, "--index", str(build / f"{name}-index.json.gz"), "--out",
                  str(work / "timetables-part"), "--release-feeds", str(work / "release-feeds.json"), "--now", NOW])
    assert rc == 0, name

mav = work / "mav"
mav.mkdir()
feed = "20261116.M2ChangeID"
data = {"v": mav_pages_site.mav_index.SCHEMA, "feed": feed,
        "st": [["A", 4700000, 1900000], ["B", 4710000, 1910000]], "p": ["_p~iF~ps|U_ulLnnqC"],
        "n": {str(100 + k): [[0, [0, 1]]] for k in range(60)}, "l": {}}
(mav / "mav-index.json.gz").write_bytes(gzip.compress(json.dumps(data).encode(), mtime=0))
(mav / "mav-build.json").write_text(json.dumps({
    "feed_version": feed, "fitted_share": 1.0,
    "audit": {"trips": 900, "trips_bad": 0, "trains": 60, "trains_ok": 60, "ok_ratio": 1.0}}))
with zipfile.ZipFile(mav / "mav-gtfs.zip", "w") as z:
    z.writestr("feed_info.txt", f"feed_publisher_name,feed_version\nMÁV-START,{feed}\n")
(mav / "gtfs-out").mkdir()
for name in ("shapes.txt", "stop_times.txt", "trips.txt"):
    (mav / "gtfs-out" / name).write_text(f"{name}\n")
rc = mav_pages_site.main(["--index", str(mav / "mav-index.json.gz"), "--stats", str(mav / "mav-build.json"),
                          "--commit", "a" * 40, "--out", str(work / "mav-part"), "--gtfs-zip",
                          str(mav / "mav-gtfs.zip"), "--pfaedle-dir", str(mav / "gtfs-out")])
assert rc == 0

(work / "live").mkdir()
rc = pt.main(["assemble", "--out", str(work / "site"), "--commit", "c" * 40, "--live-dir", str(work / "live"),
              "--mav-part", str(work / "mav-part"), "--timetables-part", str(work / "timetables-part"),
              "--now", NOW, "--settle", "-1", "--sig-settle", "-1"])
assert rc == 0

seed = os.urandom(32)
pub = sm.public_key(seed)
sm.sign_site(work / "site", seed, pub)
(work / "pub.txt").write_text(base64.b64encode(pub).decode())
'''


@pytest.fixture(scope="module")
def site(tmp_path_factory) -> tuple[Path, str]:
    """The signed site and the base64 public key it is signed with."""
    work = tmp_path_factory.mktemp("pages-roundtrip")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("MAV_ALLOW_SHRINK", None)
    done = subprocess.run(
        [sys.executable, "-c", DRIVER, str(SCRIPTS), str(work), NOW, json.dumps(RELEASE_FEEDS), RELEASE],
        capture_output=True, text=True, timeout=300, env=env, cwd=work, check=False,
    )
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return work / "site", (work / "pub.txt").read_text()


def test_the_signatures_verify_with_the_integrations_own_ed25519(site) -> None:
    """sign_manifest.py signs with openssl; the integration verifies with
    the cryptography library, over the exact bytes deployed."""
    folder, pub = site
    for name in ("timetables.json", "mav-index.json"):
        raw, sig = (folder / name).read_bytes(), (folder / f"{name}.sig").read_bytes()
        mav_daily.verify_signature(raw, sig, name, pub)
        with pytest.raises(mav_daily.UnknownKey):
            mav_daily.verify_signature(raw + b" ", sig, name, pub)
    weekly_timetables.verify_manifest(
        (folder / "timetables.json").read_bytes(), (folder / "timetables.json.sig").read_bytes(), pub
    )


def test_every_index_entry_passes_the_integrations_checks(site) -> None:
    """check_manifest, check_entry and check_index (and every shard by its
    size and sha256) on what part and assemble wrote: both indexes,
    release_feed and release_version as the stable release named them."""
    folder, _pub = site
    man = weekly_timetables.check_manifest((folder / "timetables.json").read_bytes())
    assert set(man["indexes"]) == {"volan", "city"}
    for name, raw_entry in man["indexes"].items():
        entry = weekly_timetables.check_entry(name, raw_entry, TODAY)
        weekly_timetables.check_index((folder / weekly_timetables.INDEXES[name]).read_bytes(), entry)
        assert entry["release_feed"] == RELEASE_FEEDS[name]
        assert entry["release_version"] == RELEASE
        assert entry["built_at"] is not None
        shapes = entry["shapes"]
        assert shapes is not None and shapes["n"] >= 1
        assert weekly_timetables._verify_folder(folder / shapes["dir"], shapes["files"])
    # The install side's decision on it: built against this release, so a
    # v1.10.24 install takes it (release_newer is about an older release).
    assert not weekly_timetables.release_newer("1.10.24", RELEASE)
    assert weekly_timetables.release_newer("1.10.25", RELEASE)


def test_the_mav_files_pass_the_daily_check(site) -> None:
    """mav-index.json and its index as mav_daily installs them."""
    folder, _pub = site
    man = mav_daily.check_manifest((folder / "mav-index.json").read_bytes())
    count, _rank = mav_daily.check_index((folder / "mav-index.json.gz").read_bytes(), man, mav_index.MIN_TRAINS)
    assert count == 60
    assert mav_daily.built_at(man) is not None


def _pattern(path: Path, name: str) -> re.Pattern[str]:
    """The re.compile(...) pattern assigned to name in path, read from its
    source (the scripts are not imported here)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            call = node.value
            assert isinstance(call, ast.Call) and isinstance(call.args[0], ast.Constant)
            return re.compile(call.args[0].value)
    raise AssertionError(f"no {name} in {path}")


def test_a_release_tag_the_pages_side_writes_is_one_installs_read() -> None:
    """pages_timetables.RELEASE_TAG_RE (what part writes as release_version)
    is no wider than weekly_timetables.RELEASE_VERSION_RE (what installs
    take): an entry whose release_version installs refuse fails the whole
    index on every install."""
    pages = _pattern(SCRIPTS / "pages_timetables.py", "RELEASE_TAG_RE")
    installs = weekly_timetables.RELEASE_VERSION_RE
    for tag in ("v0.0.0", "v1.10.24", "v999999.999999.999999", "v1.2.3", "v10.0.100"):
        assert pages.fullmatch(tag) and installs.fullmatch(tag), tag
    # Wider than installs read: refused by the Pages side as well.
    for tag in ("v1234567.0.0", "v1.0.1234567", "v1.2", "1.2.3", "v1.2.3-rev.1", "v1.2.3\n"):
        assert not pages.fullmatch(tag), tag
    # Every part's width at its limit, and one past it.
    for width in range(1, 9):
        tag = "v" + ".".join(["9" * width] * 3)
        assert bool(pages.fullmatch(tag)) == (width <= 6), tag
        assert not pages.fullmatch(tag) or installs.fullmatch(tag), tag
