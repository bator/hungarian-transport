#!/usr/bin/env python3
"""Restoring the durable indexes after a HACS update, and when the updater
records a feed as built. No network."""
from __future__ import annotations

import contextlib
import gzip
import http.server
import io
import importlib.util
import json
import os
import random
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import zipfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_city_index as bci  # noqa: E402
import gtfs_compact  # noqa: E402
import update_volan_index as uvi  # noqa: E402
from gtfs_compact import operator_trips  # noqa: E402
from update_volan_index import index_feed, index_schema, persist_index, restore_hacs_indexes  # noqa: E402

# The real network and builder, before Fake.install() replaces them.
REAL_DOWNLOAD_FEED = uvi.download_feed
REAL_BUILD_CITY_INDEX = uvi.build_city_index

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok' if ok else 'FAIL'}  {name}{'' if ok else ' ' + detail}")
    if not ok:
        failed += 1


def write_index(path: Path, schema: int, feed: str, pad: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Incompressible padding, so sizes differ after gzip as well.
    rnd = random.Random(pad)
    body = {"v": schema, "feed": feed, "pad": "".join(rnd.choice("0123456789abcdef") for _ in range(pad))}
    path.write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=0))


def feed_of(path: Path) -> str:
    return json.loads(gzip.decompress(path.read_bytes()))["feed"]


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "durable"
    www = Path(tmp) / "www"
    write_index(base / "volan-index.json.gz", 27, "local")
    check("schema read from the index head", index_schema(base / "volan-index.json.gz") == 27)

    restored = restore_hacs_indexes(base, www)
    check("missing index is restored", restored == ["volan-index.json.gz"]
          and feed_of(www / "volan-index.json.gz") == "local", repr(restored))

    check("identical index is left alone", restore_hacs_indexes(base, www) == [])

    write_index(www / "volan-index.json.gz", 27, "release", pad=50_000)
    restored = restore_hacs_indexes(base, www)
    check("larger shipped index with the same schema is replaced by the durable one",
          restored == ["volan-index.json.gz"] and feed_of(www / "volan-index.json.gz") == "local",
          repr(restored))

    write_index(www / "volan-index.json.gz", 28, "release-new-schema")
    restored = restore_hacs_indexes(base, www)
    check("shipped index with a newer schema is kept",
          restored == [] and feed_of(www / "volan-index.json.gz") == "release-new-schema",
          repr(restored))

    mav_base, mav_www = base / "mav-index.json.gz", www / "mav-index.json.gz"

    def age(path: Path, seconds: int) -> None:
        t = path.stat().st_mtime - seconds
        os.utime(path, (t, t))

    # Same feed: the pfaedle build (larger) beats the official zip's.
    write_index(mav_base, 2, "20261001", pad=50_000)
    write_index(mav_www, 2, "20261001")
    check("feed version read from the mav index head", index_feed(mav_www) == "20261001")
    restored = restore_hacs_indexes(base, www)
    check("larger local mav index of the same feed wins", "mav-index.json.gz" in restored
          and len(mav_www.read_bytes()) == len(mav_base.read_bytes()), repr(restored))

    write_index(mav_www, 2, "20261001", pad=200_000)
    restored = restore_hacs_indexes(base, www)
    check("larger shipped mav index of the same feed is kept", "mav-index.json.gz" not in restored
          and len(mav_www.read_bytes()) > 90_000, repr(restored))
    check("the kept mav index becomes the durable copy", mav_base.read_bytes() == mav_www.read_bytes())

    # The integration built a newer feed (uploaded zip, no pfaedle): smaller,
    # but later than the durable copy. It stays, and becomes the durable copy.
    write_index(mav_base, 2, "20261001", pad=50_000)
    age(mav_base, 3600)
    write_index(mav_www, 2, "20261015")
    restored = restore_hacs_indexes(base, www)
    check("mav index the integration built from a newer feed is kept",
          "mav-index.json.gz" not in restored and feed_of(mav_www) == "20261015", repr(restored))
    check("the durable mav copy follows the integration's build", feed_of(mav_base) == "20261015")

    # A durable copy built later than the served one, from another feed, wins.
    write_index(mav_base, 2, "20261020", pad=50_000)
    age(mav_www, 3600)
    restored = restore_hacs_indexes(base, www)
    check("later durable mav build of another feed is restored",
          "mav-index.json.gz" in restored and feed_of(mav_www) == "20261020", repr(restored))


def write_with_shapes(path: Path, schema: int, feed: str, folder: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"v": schema, "sh": {"dir": folder, "n": 64}, "feed": feed}
    path.write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=0))
    (path.parent / folder).mkdir(exist_ok=True)
    (path.parent / folder / "00.json.gz").write_bytes(gzip.compress(b'{"v":1,"p":[]}'))


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "durable"
    www = Path(tmp) / "www"
    base.mkdir()
    write_with_shapes(www / "volan-index.json.gz", 29, "built", "volan-shapes-new")
    (base / "volan-shapes-old").mkdir()
    persist_index(www / "volan-index.json.gz", base)
    check("persist copies the shapes folder and drops the old one",
          (base / "volan-shapes-new" / "00.json.gz").is_file() and not (base / "volan-shapes-old").exists())

    # HACS update: the served folder now holds the release index and shapes.
    for entry in list(www.iterdir()):
        if entry.is_dir():
            import shutil
            shutil.rmtree(entry)
        else:
            entry.unlink()
    write_with_shapes(www / "volan-index.json.gz", 29, "release", "volan-shapes-release")
    restored = restore_hacs_indexes(base, www)
    check("restore brings the durable index together with its shapes folder",
          restored == ["volan-index.json.gz"] and (www / "volan-shapes-new" / "00.json.gz").is_file(),
          repr(restored))
    check("the release shapes folder nobody points at is removed",
          not (www / "volan-shapes-release").exists(), repr(sorted(p.name for p in www.iterdir())))



VOLAN_FALLBACK = "https://kti-fallback.example/volan.zip"
# Fake.install replaces it; the tests of the real one keep it here.
REAL_REMOTE_META = uvi.remote_meta
# A developer's .env must not move the Volán URL under the tests.
os.environ["VOLAN_GTFS_URL"] = ""


class Fake:
    """Stands in for the network and the builders inside update_volan_index.main()."""

    def __init__(self, root: Path) -> None:
        self.base = root / "durable"
        self.www = root / "www"
        self.base.mkdir()
        self.www.mkdir()
        self.etag = {"https://kti.example/volan.zip": "volan-1", "https://public.example/a.zip": "a-2"}
        self.broken: set[str] = set()
        self.city_fails: BaseException | None = None
        self.city_info: dict = {"ops": 1, "trips": 5, "skipped": []}
        self.city_builds = 0
        self.downloads: list[str] = []
        self.heads: list[tuple[str, dict]] = []
        self.body: dict[str, bytes] = {}
        self.built: dict[str, bytes] = {}
        self.meta: dict[str, dict] = {}
        self.guard_missing = False
        self.until = ""

    def paths(self):
        return self.base, self.www / "volan-index.json.gz", self.www / "city-index.json.gz", self.www / "mav-index.json.gz"

    def remote_meta(self, url: str, **kw) -> dict:
        self.heads.append((url, kw))
        if url in self.broken:
            raise OSError("unreachable")
        if url in self.meta:
            return dict(self.meta[url])
        return {"etag": self.etag.get(url, "")}

    def download_feed(self, url: str, dest: Path, **_kw) -> dict:
        if url in self.broken:
            raise OSError("unreachable")
        self.downloads.append(url)
        # The bytes follow the fingerprint unless a test pins them.
        dest.write_bytes(self.body.get(url) or b"PK" + url.encode() + self.etag.get(url, "").encode())
        return {"size": dest.stat().st_size}

    def build_city_index(self, base: Path, out: Path, _volan: Path | None, *, zips=None) -> dict:
        """A zip (new or in place) starting with BROKEN fails to load, as
        build_city_index treats one: a new one is rejected for the zip in
        place, a zip in place is skipped. A new zip starting with SMALL
        loads but the guard refuses it: rejected for its content (refused,
        until self.until). self.built has the bytes used."""
        self.city_builds += 1
        if self.city_fails is not None:
            raise self.city_fails
        info = dict(self.city_info)
        info["rejected"], info["refused"] = [], {}
        skipped, self.built = list(info.get("skipped") or []), {}
        for feed in uvi.CITY_FEEDS:
            fid, old = feed["id"], base / feed["file"]
            new = (zips or {}).get(fid)
            if new is not None and not new.read_bytes().startswith((b"BROKEN", b"SMALL")):
                self.built[fid] = new.read_bytes()
                continue
            if new is not None:
                info["rejected"].append(fid)
                if new.read_bytes().startswith(b"SMALL"):
                    info["refused"][fid] = {"why": f"{fid} 10 trips vs 60", "until": self.until}
            if old.exists() and not old.read_bytes().startswith(b"BROKEN"):
                self.built[fid] = old.read_bytes()
            elif fid not in skipped:
                skipped.append(fid)
        info["skipped"] = skipped
        if self.guard_missing and skipped:
            raise gtfs_compact.IndexGuardError(f"refusing to replace {out.name}: {skipped[0]} missing")
        out.write_bytes(self.index_bytes(self.built))
        return info

    @staticmethod
    def index_bytes(built: dict[str, bytes]) -> bytes:
        """The city index a build from these zips writes: the same bytes for
        the same zips, as build_city_index does."""
        body = {"v": 29, "zips": {fid: data.decode("latin-1") for fid, data in sorted(built.items())}}
        return gzip.compress(json.dumps(body).encode(), mtime=0)

    def download_zip(self, url: str, dest: Path, **kw) -> dict:
        return self.download_feed(url, dest, **kw)

    def install(self) -> None:
        uvi.paths = self.paths
        uvi.remote_meta = self.remote_meta
        uvi.download_feed = self.download_feed
        uvi.download_zip = self.download_zip
        uvi.build_city_index = self.build_city_index
        uvi.notify = lambda *_a, **_k: None
        uvi.GTFS_URL = "https://kti.example/volan.zip"
        # The fallback answers only where a test says so.
        uvi.VOLAN_FALLBACK_URL = VOLAN_FALLBACK
        self.broken.add(VOLAN_FALLBACK)
        uvi.CITY_FEEDS = [{
            "id": "a",
            "file": "a.zip",
            "url": "https://public.example/a.zip",
            "url_env": "TEST_A_GTFS_URL",
        }]

        def no_mav(_base, **_kw):
            raise FileNotFoundError("no MÁV feed in this test")

        uvi.resolve_mav_feed = no_mav

    def state(self) -> dict:
        return json.loads((self.base / "state.json").read_text())

    def run(self) -> int:
        try:
            return uvi.main()
        except SystemExit as err:
            return int(err.code or 0)


os.environ["HUNGARIAN_TRANSPORT_SKIP_CATALOG"] = "1"
os.environ.pop("TEST_A_GTFS_URL", None)
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    # The Volán index (and its zip) is current; city feed "a" was built at
    # etag a-1 and has moved to a-2.
    (fake.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-1")
    (fake.www / "city-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.base / "a.zip").write_bytes(b"PK old")
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-1"},
        "cities": {"a": {"etag": "a-1"}},
    }))

    fake.city_fails = RuntimeError("bad feed")
    code = fake.run()
    check("failed city rebuild exits REPORTED", code == uvi.REPORTED, str(code))
    check("failed city rebuild keeps the old fingerprint",
          fake.state()["cities"]["a"]["etag"] == "a-1", repr(fake.state()["cities"]))
    check("failed city rebuild is marked for retry", fake.state().get("city_retry") is True)

    fake.city_fails = SystemExit(1)  # what on_signal raises on SIGTERM
    fake.run()
    check("killed city rebuild keeps the old fingerprint",
          fake.state()["cities"]["a"]["etag"] == "a-1", repr(fake.state()["cities"]))

    fake.city_fails = None
    builds = fake.city_builds
    code = fake.run()
    check("next run rebuilds and records the new fingerprint",
          code == 0 and fake.city_builds == builds + 1 and fake.state()["cities"]["a"]["etag"] == "a-2",
          repr(fake.state()))
    check("retry flag cleared after a good build", fake.state().get("city_retry") is False)

    builds, downloads = fake.city_builds, len(fake.downloads)
    fake.run()
    check("unchanged feed is neither downloaded nor rebuilt",
          fake.city_builds == builds and len(fake.downloads) == downloads)

    # A broken keyed override falls back to the public URL.
    os.environ["TEST_A_GTFS_URL"] = "https://keyed.example/a.zip"
    fake.broken.add("https://keyed.example/a.zip")
    fake.etag["https://public.example/a.zip"] = "a-3"
    fake.run()
    check("broken override falls back to the public URL",
          fake.downloads[-1] == "https://public.example/a.zip" and fake.state()["cities"]["a"]["etag"] == "a-3",
          repr(fake.downloads[-2:]))
    os.environ.pop("TEST_A_GTFS_URL", None)

    # Every way the run can die reaches the webhook, not only update.log.
    sent: list[str] = []
    uvi.notify = lambda message, *_a, **_k: sent.append(message)
    fake.city_fails = None

    def kill_self(_base, out, _volan, **_kw):
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("SIGTERM handler did not exit")

    uvi.build_city_index = kill_self
    fake.etag["https://public.example/a.zip"] = "a-4"
    code = fake.run()
    check("SIGTERM exits REPORTED and reports through the webhook",
          code == uvi.REPORTED and len(sent) == 1 and "SIGTERM" in sent[0] and "megszakítva" in sent[0],
          repr((code, sent)))
    uvi.build_city_index = fake.build_city_index

    sent.clear()
    real_paths = uvi.paths

    def broken_paths():
        raise PermissionError("/config is read-only")

    uvi.paths = broken_paths
    code = fake.run()
    check("a failure before the main loop exits REPORTED and reports through the webhook",
          code == uvi.REPORTED and len(sent) == 1 and "PermissionError" in sent[0], repr((code, sent)))
    uvi.paths = real_paths

    # O-7: the raw exception text reaches the webhook, so a keyed URL in it
    # must not: the query, URL credentials and secret-named params are cut.
    sent.clear()

    def keyed_failure(_base, out, _volan, **_kw):
        raise OSError("fetch https://feed.example/bahart.zip?apikey=SECRETKEY1&v=2"
                      " via http://u:SECRETPW@feed.example/ failed")

    uvi.build_city_index = keyed_failure
    fake.etag["https://public.example/a.zip"] = "a-5"
    code = fake.run()
    check("a keyed URL in an exception text is redacted before the webhook",
          code == uvi.REPORTED and len(sent) == 1 and "SECRETKEY1" not in sent[0] and "SECRETPW" not in sent[0]
          and "https://feed.example/bahart.zip?…" in sent[0] and "Helyi GTFS" in sent[0], repr((code, sent)))
    uvi.build_city_index = fake.build_city_index

    # The Volán HEAD failing does not stop the city feeds or the MÁV index.
    sent.clear()
    fake.broken.add("https://kti.example/volan.zip")
    fake.etag["https://public.example/a.zip"] = "a-5"
    builds = fake.city_builds
    code = fake.run()
    check("a Volán HEAD failure is reported and the city feeds still update",
          code == uvi.REPORTED and fake.city_builds == builds + 1 and fake.state()["cities"]["a"]["etag"] == "a-5"
          and len(sent) == 1 and "HEAD" in sent[0], repr((code, sent, fake.state()["cities"])))
    fake.broken.discard("https://kti.example/volan.zip")

    # The primary Volán URL down, the fallback up: no report, the fallback's
    # zip is downloaded, and its HEAD fingerprint is the one recorded.
    sent.clear()
    real_build_index = uvi.build_index

    def fake_build_index(zpath: Path, out: Path) -> dict:
        out.write_bytes(gzip.compress(b'{"v":29}'))
        return {"feed_version": zpath.read_bytes().decode("latin-1"), "trips": 46000, "stops": 9000}

    uvi.build_index = fake_build_index
    fake.broken.add("https://kti.example/volan.zip")
    fake.broken.discard(VOLAN_FALLBACK)
    fake.etag[VOLAN_FALLBACK] = "fallback-1"
    heads = len(fake.heads)
    code = fake.run()
    check("a Volán primary that is down falls back to the second URL",
          code == 0 and not any("sikertelen" in m for m in sent) and VOLAN_FALLBACK in fake.downloads[-2:]
          and [u for u, _kw in fake.heads[heads:]][:2] == ["https://kti.example/volan.zip", VOLAN_FALLBACK]
          and fake.state()["volan"].get("etag") == "fallback-1",
          repr((code, sent, fake.downloads[-2:], fake.state()["volan"])))
    # HEAD answers on the primary but its download fails: the fallback's
    # zip is used, and no fingerprint is kept for it, so the next run checks.
    fake.broken.discard("https://kti.example/volan.zip")
    fake.etag["https://kti.example/volan.zip"] = "volan-9"
    real_download = fake.download_feed
    failing_primary: list[str] = []

    def primary_get_fails(url: str, dest: Path, **kw) -> dict:
        if url == "https://kti.example/volan.zip":
            failing_primary.append(url)
            raise OSError("reset")
        return real_download(url, dest, **kw)

    fake.download_feed = primary_get_fails
    code = fake.run()
    check("a failed primary download is taken from the fallback",
          code == 0 and failing_primary and VOLAN_FALLBACK in fake.downloads[-2:]
          and fake.state()["volan"].get("etag") != "volan-9", repr((code, failing_primary, fake.state()["volan"])))
    fake.download_feed = real_download
    os.environ["VOLAN_GTFS_URL"] = "https://override.example/volan.zip"
    fake.etag["https://override.example/volan.zip"] = "override-1"
    heads = len(fake.heads)
    code = fake.run()
    check("VOLAN_GTFS_URL replaces the primary URL",
          code == 0 and fake.heads[heads][0] == "https://override.example/volan.zip"
          and "https://kti.example/volan.zip" not in [u for u, _kw in fake.heads[heads:]]
          and "https://override.example/volan.zip" in fake.downloads[-2:], repr((code, fake.heads[heads:])))
    check("the Volán URL list: override, then the fallback once",
          uvi.volan_urls(uvi.GTFS_URL, VOLAN_FALLBACK) == ["https://override.example/volan.zip", VOLAN_FALLBACK])
    os.environ["VOLAN_GTFS_URL"] = ""
    fake.broken.add(VOLAN_FALLBACK)
    fake.etag["https://kti.example/volan.zip"] = "volan-1"
    fake.run()

    # The real remote_meta: a primary that times out does not count as
    # answering (its {} never matches the state, and the fallback's zip
    # would be downloaded again every day it is down). The fallback's HEAD
    # decides, its fingerprint is recorded, the next run downloads nothing.
    class _Head:
        status = 200

        def __init__(self, headers: dict) -> None:
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    real_request = uvi.request
    asked: list[str] = []

    def primary_times_out(method: str, url: str, **_kw):
        asked.append(url)
        if url == "https://kti.example/volan.zip":
            raise urllib.error.URLError(TimeoutError("timed out"))
        if url == VOLAN_FALLBACK:
            return _Head({"ETag": '"fb-7"', "Content-Length": "40"})
        return _Head({"ETag": fake.etag.get(url, "")})

    uvi.request = primary_times_out
    uvi.remote_meta = REAL_REMOTE_META
    fake.broken.add("https://kti.example/volan.zip")
    fake.broken.discard(VOLAN_FALLBACK)
    sent.clear()
    before = list(fake.downloads)
    code = fake.run()
    first = fake.downloads[len(before):]
    check("a Volán primary that times out: the fallback's HEAD decides and its fingerprint is kept",
          code == 0 and first.count(VOLAN_FALLBACK) == 1 and "https://kti.example/volan.zip" not in first
          and fake.state()["volan"].get("etag") == '"fb-7"' and not any("sikertelen" in m for m in sent),
          repr((code, first, fake.state()["volan"], sent)))
    before = list(fake.downloads)
    asked.clear()
    code = fake.run()
    check("while the primary is down, the next run downloads nothing",
          code == 0 and VOLAN_FALLBACK not in fake.downloads[len(before):]
          and asked[:2] == ["https://kti.example/volan.zip", VOLAN_FALLBACK],
          repr((code, fake.downloads[len(before):], asked)))
    # Primary back: its own fingerprint differs, the zip is fetched there
    # once and found to be the same bytes.
    uvi.request = real_request
    uvi.remote_meta = fake.remote_meta
    fake.broken.discard("https://kti.example/volan.zip")
    fake.broken.add(VOLAN_FALLBACK)
    fake.run()
    uvi.build_index = real_build_index
    sent.clear()

    # A Volán rebuild that fails is reported; the city feeds go on, but the
    # new Volán zip does not trigger a city rebuild of its own.
    sent.clear()
    fake.etag["https://kti.example/volan.zip"] = "volan-2"
    fake.etag["https://public.example/a.zip"] = "a-6"
    real_build_index = uvi.build_index

    def broken_build(*_a, **_k):
        raise gtfs_compact.IndexGuardError("refusing to write volan-index.json.gz: 12 trips")

    uvi.build_index = broken_build
    builds = fake.city_builds
    code = fake.run()
    check("a failed Volán rebuild is reported and the city feeds still update",
          code == uvi.REPORTED and fake.city_builds == builds + 1 and fake.state()["cities"]["a"]["etag"] == "a-6"
          and fake.state()["volan"]["etag"] == "volan-1" and len(sent) == 1 and "volan rebuild" in sent[0],
          repr((code, sent)))
    builds = fake.city_builds
    fake.run()
    check("without a new Volán index the city is not rebuilt for it", fake.city_builds == builds)
    uvi.build_index = real_build_index
    fake.etag["https://kti.example/volan.zip"] = "volan-1"

    # A feed skipped for its ended calendar is recorded as built: the same
    # zip would be skipped again, so it is neither downloaded nor announced
    # every day. A feed that failed to load is fetched again.
    sent.clear()
    fake.etag["https://public.example/a.zip"] = "a-7"
    fake.city_info = {"ops": 0, "trips": 0, "skipped": ["a"], "expired": {"a": "20261101"}}
    code = fake.run()
    check("an ended feed's fingerprint is recorded",
          code == 0 and fake.state()["cities"]["a"]["etag"] == "a-7", repr(fake.state()["cities"]))
    check("the announcement names the ended calendar",
          len(sent) == 1 and "calendar ended: a 2026-11-01" in sent[0], repr(sent))
    sent.clear()
    builds, downloads = fake.city_builds, len(fake.downloads)
    fake.run()
    check("the ended feed is neither downloaded, rebuilt nor announced again",
          fake.city_builds == builds and len(fake.downloads) == downloads and sent == [],
          repr((fake.city_builds - builds, sent)))
    fake.etag["https://public.example/a.zip"] = "a-8"
    fake.city_info = {"ops": 0, "trips": 0, "skipped": ["a"], "expired": {}}
    fake.run()
    check("a feed skipped for failing to load loses its fingerprint",
          not {"etag", "sha256", "content_length", "fetched"} & set(fake.state()["cities"]["a"]),
          repr(fake.state()["cities"]))
    downloads = len(fake.downloads)
    fake.run()
    check("the same HEAD is downloaded again after a skip", len(fake.downloads) == downloads + 1, repr(fake.downloads))
    fake.city_info = {"ops": 1, "trips": 5, "skipped": []}

    # The success line names the feeds the index left out.
    sent.clear()
    fake.etag["https://public.example/a.zip"] = "a-9"
    fake.city_info = {"ops": 1, "trips": 5, "skipped": ["a", "volan-local"], "expired": {}}
    fake.run()
    check("the announcement lists the skipped feeds",
          len(sent) == 1 and "(skipped: a, volan-local)" in sent[0], repr(sent))
    fake.city_info = {"ops": 1, "trips": 5, "skipped": []}
    fake.run()

# A server whose HEAD gives no usable fingerprint sends the same zip again:
# that is no new timetable, so nothing is rebuilt or announced.
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    sent = []
    uvi.notify = lambda message, *_a, **_k: sent.append(message)
    volan_builds: list[Path] = []

    def fake_build_index(zpath: Path, out: Path) -> dict:
        volan_builds.append(zpath)
        out.write_bytes(gzip.compress(b'{"v":29}'))
        return {"feed_version": "v1", "trips": 46000, "stops": 9000}

    uvi.build_index = fake_build_index
    fake.etag = {}
    fake.body = {"https://kti.example/volan.zip": b"PK volan same", "https://public.example/a.zip": b"PK a same"}
    code = fake.run()
    check("first run without fingerprints builds both and records their hashes",
          code == 0 and len(volan_builds) == 1 and fake.city_builds == 1 and len(sent) == 1
          and fake.state()["volan"].get("sha256") and fake.state()["cities"]["a"].get("sha256"),
          repr((code, sent, fake.state())))
    sent.clear()
    for _ in range(3):
        code = fake.run()
    check("the same bytes again are neither rebuilt nor announced",
          code == 0 and len(volan_builds) == 1 and fake.city_builds == 1 and sent == []
          and fake.downloads.count("https://kti.example/volan.zip") == 4, repr((code, sent, fake.downloads)))
    fake.body["https://public.example/a.zip"] = b"PK a new"
    fake.run()
    check("new bytes behind the same (missing) fingerprint are rebuilt",
          fake.city_builds == 2 and len(volan_builds) == 1 and len(sent) == 1, repr(sent))

    # A Volán zip that downloads but does not build leaves the zip the city
    # index takes its local lines from as it was.
    zip_path = fake.base / "volanbusz_gtfs.zip"
    good = zip_path.read_bytes()
    fake.body["https://kti.example/volan.zip"] = b"PK volan broken"
    seen_volan: list[bytes] = []

    def refusing_build(zpath: Path, _out: Path) -> dict:
        seen_volan.append(zpath.read_bytes())
        raise gtfs_compact.IndexGuardError("refusing to write volan-index.json.gz: 12 trips")

    def recording_city(_base: Path, out: Path, volan: Path | None, **_kw) -> dict:
        seen_volan.append(volan.read_bytes() if volan else b"")
        out.write_bytes(gzip.compress(b'{"v":29}'))
        return dict(fake.city_info)

    uvi.build_index = refusing_build
    uvi.build_city_index = recording_city
    sent.clear()
    fake.body["https://public.example/a.zip"] = b"PK a newer"
    code = fake.run()
    check("a Volán zip that fails its build does not replace the previous one",
          code == uvi.REPORTED and zip_path.read_bytes() == good and seen_volan == [b"PK volan broken", good]
          and not list(fake.base.glob("*.tmp")) and not list(fake.base.glob("gtfs_*.zip")),
          repr((code, seen_volan, sorted(p.name for p in fake.base.iterdir()))))
    uvi.build_index = fake_build_index
    uvi.build_city_index = fake.build_city_index

    # The fingerprint did not move, but the zip is gone: fetch it again.
    fake.body["https://kti.example/volan.zip"] = b"PK volan same"
    fake.etag["https://kti.example/volan.zip"] = "volan-7"
    fake.run()
    builds = len(volan_builds)
    zip_path.unlink()
    downloads = fake.downloads.count("https://kti.example/volan.zip")
    fake.run()
    check("a missing Volán zip is downloaded again despite the same fingerprint",
          zip_path.read_bytes() == b"PK volan same" and len(volan_builds) == builds
          and fake.downloads.count("https://kti.example/volan.zip") == downloads + 1,
          repr((fake.downloads, len(volan_builds) - builds)))

# Older scripts in /config/hungarian-transport do not overwrite an index a
# release shipped with a newer schema (restore_hacs_indexes kept it): the
# run says so and leaves both indexes, and the pending city zips, as they are.
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    sent = []
    uvi.notify = lambda message, *_a, **_k: sent.append(message)
    volan_builds = []
    uvi.build_index = lambda z, out: (volan_builds.append(z), out.write_bytes(gzip.compress(b'{"v":29}')),
                                      {"feed_version": "v2", "trips": 1, "stops": 1})[2]
    newer = gtfs_compact.INDEX_SCHEMA + 1
    shipped = gzip.compress(json.dumps({"v": newer, "feed": "release"}, separators=(",", ":")).encode(), mtime=0)
    for name in ("volan-index.json.gz", "city-index.json.gz"):
        (fake.www / name).write_bytes(shipped)
        write_index(fake.base / name, gtfs_compact.INDEX_SCHEMA, "local")
    (fake.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-0")
    (fake.base / "a.zip").write_bytes(b"PK old")
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-0"},
        "cities": {"a": {"etag": "a-1"}},
    }))
    code = fake.run()
    check("a newer-schema shipped index is not rebuilt by older scripts",
          code == uvi.REPORTED and volan_builds == [] and fake.city_builds == 0
          and fake.downloads == []
          and all((fake.www / n).read_bytes() == shipped for n in ("volan-index.json.gz", "city-index.json.gz")),
          repr((code, volan_builds, fake.city_builds, fake.downloads)))
    check("the schema conflict is reported through the webhook, once per index",
          len(sent) == 2 and all(f"schema {newer}" in m and "copy the new scripts" in m for m in sent), repr(sent))
    check("the city rebuild stays pending for the new scripts",
          fake.state().get("city_retry") is True and fake.state()["cities"]["a"]["etag"] == "a-1"
          and fake.state()["volan"].get("etag") == "volan-0", repr(fake.state()))
    sent.clear()
    code = fake.run()
    check("the same schema conflict is not reported again the next day",
          code == 0 and sent == [] and volan_builds == [] and fake.city_builds == 0
          and fake.downloads == [], repr((code, sent, fake.downloads)))
    check("a moved city feed is not downloaded while the index cannot be written",
          fake.downloads == [] and fake.state()["cities"]["a"] == {"etag": "a-1"}
          and fake.state().get("city_retry") is True, repr((fake.downloads, fake.state())))
    # An unchanged feed would not be rebuilt anyway: no report at all, even
    # for a conflict that was never reported.
    st = fake.state()
    st.update(schema_reported={}, city_retry=False, volan={"etag": "volan-1"}, cities={"a": {"etag": "a-2"}})
    (fake.base / "state.json").write_text(json.dumps(st))
    code = fake.run()
    check("an unchanged feed with a schema conflict sends no report",
          code == 0 and sent == [] and volan_builds == [] and fake.city_builds == 0, repr((code, sent)))
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-0"},
        "cities": {"a": {"etag": "a-1"}},
    }))
    # New scripts (same schema as the shipped indexes): both are rebuilt.
    for name in ("volan-index.json.gz", "city-index.json.gz"):
        write_index(fake.www / name, gtfs_compact.INDEX_SCHEMA, "release")
    sent.clear()
    code = fake.run()
    check("with scripts of the shipped schema both indexes are rebuilt",
          code == 0 and len(volan_builds) == 1 and fake.city_builds == 1
          and fake.state()["cities"]["a"]["etag"] == "a-2", repr((code, sent, fake.state())))

# A city feed (or MAV_GTFS_URL) that keeps failing to download is reported
# on the third day, then weekly; one bad day only goes to update.log.
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    sent = []
    uvi.notify = lambda message, *_a, **_k: sent.append(message)
    uvi.build_index = lambda _z, out: (out.write_bytes(gzip.compress(b'{"v":29}')),
                                       {"feed_version": "v1", "trips": 1, "stops": 1})[1]
    fake.run()
    fake.broken.add("https://public.example/a.zip")
    mav_fails = {"on": True}

    def failing_mav(_base, *, url_errors=None):
        if mav_fails["on"] and url_errors is not None:
            url_errors.append(urllib.error.HTTPError("https://mav.example/x?key=s3cret", 404, "Not Found", {}, None))
        return Path(tmp) / "no-such-mav.zip"

    uvi.resolve_mav_feed = failing_mav
    uvi.mav_source_newer = lambda *_a: False
    start = date(2026, 12, 30)
    got: list[tuple[int, list[str]]] = []
    for day in range(12):
        uvi._today = lambda d=day: start + timedelta(days=d)
        sent.clear()
        code = fake.run()
        got.append((code, list(sent)))
    reported_days = [d for d, (_c, msgs) in enumerate(got) if msgs]
    check("a failing feed is reported on its third day and weekly after that",
          reported_days == [2, 9] and all(c == 0 for c, _m in got), repr(got))
    city_msg = [m for m in got[2][1] if "Helyi" in m]
    mav_msg = [m for m in got[2][1] if "MÁV" in m]
    check("the report names the feed, since when and the error, not the URL",
          len(city_msg) == 1 and "a since 2026-12-30 (3 days, 3 runs, last OSError)" in city_msg[0]
          and len(mav_msg) == 1 and "mav since 2026-12-30" in mav_msg[0] and "HTTP 404" in mav_msg[0]
          and "s3cret" not in mav_msg[0], repr(got[2][1]))
    check("the failure count is kept in state.json", fake.state()["failing"]["a"]["runs"] == 12,
          repr(fake.state().get("failing")))
    fake.broken.discard("https://public.example/a.zip")
    mav_fails["on"] = False
    fake.run()
    check("a feed that downloads again leaves the failing list", fake.state()["failing"] == {},
          repr(fake.state().get("failing")))
    uvi._today = date.today

# The daily HEAD of a keyed http:// override goes over https, like its
# download; only a feed marked insecure is asked over plain http.
opened: list[tuple[str, bool]] = []
# remote_meta: a server without HEAD (403/405/501) is unknown ({}); one that
# does not answer is unknown only where the caller has no other URL.
real_request = uvi.request
for raised, strict_raises, label in (
        (urllib.error.URLError(TimeoutError("timed out")), True, "a timeout"),
        (ConnectionRefusedError("refused"), True, "a refused connection"),
        (urllib.error.HTTPError("https://x.example/a.zip", 405, "no HEAD", {}, None), False, "HTTP 405"),
        (urllib.error.HTTPError("https://x.example/a.zip", 501, "no HEAD", {}, None), False, "HTTP 501")):
    def head_fails(_method, _url, _err=raised, **_kw):
        raise _err
    uvi.request = head_fails
    lenient = REAL_REMOTE_META("https://x.example/a.zip")
    try:
        strict = REAL_REMOTE_META("https://x.example/a.zip", unreachable_ok=False)
    except OSError:
        strict = "raised"
    check(f"remote_meta on {label}: {{}} by default, {'raises' if strict_raises else '{}'} with unreachable_ok=False",
          lenient == {} and strict == ("raised" if strict_raises else {}), repr((lenient, strict)))


def head_500(_method, _url, **_kw):
    raise urllib.error.HTTPError("https://x.example/a.zip", 500, "boom", {}, None)


uvi.request = head_500
try:
    REAL_REMOTE_META("https://x.example/a.zip")
    raised_500 = False
except urllib.error.HTTPError:
    raised_500 = True
check("remote_meta on HTTP 500 raises", raised_500)
uvi.request = real_request

real_urlopen, real_assert = uvi._urlopen, uvi.assert_public_http_url
uvi._urlopen = lambda req, timeout=60, *, allow_insecure=False: opened.append((req.full_url, allow_insecure))
uvi.assert_public_http_url = lambda _url: None
try:
    uvi.request("HEAD", "http://nap.example/bahart.zip?apikey=k")
    uvi.request("HEAD", "http://gtfs.cdata.hu/mvkzrt.zip", allow_insecure=True)
finally:
    uvi._urlopen, uvi.assert_public_http_url = real_urlopen, real_assert
check("an http:// HEAD is upgraded to https unless the feed is insecure",
      opened == [("https://nap.example/bahart.zip?apikey=k", False), ("http://gtfs.cdata.hu/mvkzrt.zip", True)],
      repr(opened))
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    uvi.CITY_FEEDS = [{"id": "m", "file": "m.zip", "url": "http://m.example/m.zip", "insecure": True},
                      {"id": "k", "file": "k.zip", "url": "https://k.example/k.zip"}]
    fake.run()
    check("the city loop tells remote_meta which feeds may use http",
          [(u, kw.get("allow_insecure")) for u, kw in fake.heads[1:]]
          == [("http://m.example/m.zip", True), ("https://k.example/k.zip", False)], repr(fake.heads))

    # A kill during the HACS restore (before the main loop) is reported too.
    sent = []
    uvi.notify = lambda message, *_a, **_k: sent.append(message)
    stale: list[int] = []
    signal.signal(signal.SIGTERM, lambda *_a: stale.append(1))
    real_restore = uvi.restore_hacs_indexes

    def kill_in_restore(*_a):
        os.kill(os.getpid(), signal.SIGTERM)
        raise AssertionError("SIGTERM handler did not exit")

    uvi.restore_hacs_indexes = kill_in_restore
    try:
        code = fake.run()
    finally:
        uvi.restore_hacs_indexes = real_restore
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    check("the SIGTERM handler is in place before the HACS restore",
          code == uvi.REPORTED and not stale and len(sent) == 1 and "megszakítva" in sent[0], repr((code, sent, stale)))

check("notify's user agent is the downloader's", uvi.UA == gtfs_compact.UA)

# HUNGARIAN_TRANSPORT_WWW and HUNGARIAN_TRANSPORT_WEBHOOK from .env: the
# served folder is chosen at import, so .env must be read before that.
# ---- size-only HEAD, a zip that fails to load, a broken new zip -------------

day0 = date(2026, 11, 2)
uvi._today = lambda: day0
check("size-only match holds within WEAK_MATCH_DAYS of the last download",
      uvi.same_remote({"content_length": "10", "fetched": "2026-10-27"}, {"content_length": "10"}))
check("size-only match expires after WEAK_MATCH_DAYS",
      not uvi.same_remote({"content_length": "10", "fetched": "2026-10-26"}, {"content_length": "10"}))
check("size-only match without a download date is no match",
      not uvi.same_remote({"content_length": "10"}, {"content_length": "10"}))
check("size and Last-Modified both equal is a match, however old",
      uvi.same_remote({"content_length": "10", "last_modified": "x", "fetched": "2025-01-01"},
                      {"content_length": "10", "last_modified": "x"}))
check("a new Last-Modified at the same size is no match",
      not uvi.same_remote({"content_length": "10", "last_modified": "x", "fetched": "2026-11-02"},
                          {"content_length": "10", "last_modified": "y"}))
check("the ETag decides when both have one",
      uvi.same_remote({"etag": "e", "content_length": "9"}, {"etag": "e", "content_length": "10"}))

with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    built_volan: list[bytes] = []

    def size_build_index(zpath: Path, out: Path) -> dict:
        built_volan.append(zpath.read_bytes())
        out.write_bytes(gzip.compress(b'{"v":29}'))
        return {"feed_version": "v1", "trips": 46000, "stops": 9000}

    real_build_index = uvi.build_index
    uvi.build_index = size_build_index
    kti, pub = "https://kti.example/volan.zip", "https://public.example/a.zip"
    # KTI: no ETag, a Last-Modified that is just the response time (dropped):
    # only the size is left. The next timetable has the same size.
    fake.meta = {kti: {"content_length": "13"}, pub: {"content_length": "9"}}
    fake.body = {kti: b"PK volan v1  ", pub: b"PK a v1  "}
    day = 0
    uvi._today = lambda: day0 + timedelta(days=day)
    code = fake.run()
    check("size-only: the first run downloads and builds", code == 0 and built_volan == [b"PK volan v1  "]
          and fake.built.get("a") == b"PK a v1  " and fake.state()["volan"].get("fetched") == "2026-11-02",
          repr((code, built_volan, fake.state())))
    fake.body = {kti: b"PK volan v2  ", pub: b"PK a v2  "}
    downloads = len(fake.downloads)
    day = 3
    fake.run()
    check("size-only: within the week the same size is not downloaded", len(fake.downloads) == downloads)
    day = 7
    code = fake.run()
    check("size-only: after a week the same-size change is downloaded and built",
          code == 0 and built_volan[-1] == b"PK volan v2  " and fake.built.get("a") == b"PK a v2  "
          and (fake.base / "a.zip").read_bytes() == b"PK a v2  "
          and fake.state()["cities"]["a"].get("fetched") == "2026-11-09", repr((code, built_volan, fake.state())))
    builds, vbuilds, downloads = fake.city_builds, len(built_volan), len(fake.downloads)
    day = 14
    fake.run()
    check("size-only: the same bytes a week later are downloaded but not rebuilt",
          len(fake.downloads) == downloads + 2 and fake.city_builds == builds and len(built_volan) == vbuilds
          and fake.state()["cities"]["a"].get("fetched") == "2026-11-16" and not list(fake.base.glob("*.tmp")),
          repr((fake.downloads[downloads:], fake.state())))
    uvi.build_index = real_build_index
    uvi._today = date.today

with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    pub = "https://public.example/a.zip"
    # a.zip on disk fails to load, but its ETag is the one in state.json.
    fake.etag[pub] = "a-5"
    (fake.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-1")
    (fake.base / "a.zip").write_bytes(b"BROKEN a-5 on disk")
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-1"},
        "cities": {"a": {"etag": "a-5", "sha256": "x"}},
    }))
    fake.body[pub] = b"PK a-5 good"
    fake.run()
    check("a zip that fails to load drops the feed's whole fingerprint",
          fake.downloads == [] and "etag" not in fake.state()["cities"]["a"], repr(fake.state()["cities"]))
    fake.run()
    check("the next run downloads it again and builds from it",
          fake.downloads == [pub] and fake.built.get("a") == b"PK a-5 good"
          and fake.state()["cities"]["a"].get("etag") == "a-5", repr((fake.downloads, fake.state()["cities"])))

with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    fake.guard_missing = True  # a feed missing from the rebuild is refused, as guard_feeds does
    pub = "https://public.example/a.zip"
    (fake.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-1")
    (fake.base / "a.zip").write_bytes(b"PK a good")
    (fake.www / "city-index.json.gz").write_bytes(fake.index_bytes({"a": b"PK a good"}))
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-1"},
        "cities": {"a": {"etag": "a-1", "sha256": "good"}},
    }))
    fake.etag[pub] = "a-2"
    fake.body[pub] = b"SMALL a-2"
    sent: list[str] = []
    uvi.notify = lambda message, *_a, **_k: sent.append(message)
    code = fake.run()
    check("a new city zip the guard refuses does not replace the working one",
          code == 0 and (fake.base / "a.zip").read_bytes() == b"PK a good" and fake.built.get("a") == b"PK a good"
          and not list(fake.base.glob("*.tmp")), repr((code, sent, sorted(p.name for p in fake.base.iterdir()))))
    check("a rejected zip is not announced as a new timetable, nor recorded as the zip in use",
          sent == [] and fake.state()["cities"]["a"].get("etag") == "a-1"
          and fake.state()["cities"]["a"].get("sha256") == "good"
          and fake.state()["cities"]["a"].get("rejected", {}).get("etag") == "a-2"
          and fake.state()["cities"]["a"]["rejected"].get("why") == "a 10 trips vs 60"
          and fake.state()["cities"]["a"]["rejected"].get("checked") == date.today().isoformat(),
          repr((sent, fake.state()["cities"])))
    check("a rejected zip counts as a failing feed",
          fake.state()["failing"].get("a", {}).get("runs") == 1, repr(fake.state().get("failing")))
    # The publisher keeps serving it: not downloaded, rebuilt or announced
    # again, and reported as failing from the third day (FAIL_REPORT_DAYS).
    builds = fake.city_builds
    for day in (1, 2):
        uvi._today = lambda day=day: date.today() + timedelta(days=day)
        fake.run()
    check("the same rejected zip is neither downloaded nor rebuilt again",
          fake.downloads == [pub] and fake.city_builds == builds, repr((fake.downloads, fake.city_builds - builds)))
    check("a feed whose new zip keeps being rejected is reported on the third day",
          len(sent) == 1 and "Helyi GTFS frissítés sikertelen" in sent[0] and "a since" in sent[0]
          and "new zip rejected" in sent[0] and fake.state()["failing"]["a"]["runs"] == 3, repr((sent, fake.state())))
    # A HEAD without a fingerprint: the bytes are fetched, and their sha256
    # matches the rejected zip's, so no rebuild.
    fake.meta[pub] = {}
    sent.clear()
    uvi._today = lambda: date.today() + timedelta(days=3)
    code = fake.run()
    check("the same rejected bytes behind a HEAD without fingerprint are not rebuilt",
          code == 0 and fake.downloads == [pub, pub] and fake.city_builds == builds and sent == []
          and not list(fake.base.glob("*.tmp")), repr((code, fake.downloads, sent)))
    del fake.meta[pub]
    # REJECT_RECHECK_DAYS after the build that rejected it, the zip is built
    # again: the guard may judge it otherwise by now. Still refused, the
    # failing streak goes on and the check date moves.
    uvi._today = lambda: date.today() + timedelta(days=uvi.REJECT_RECHECK_DAYS)
    code = fake.run()
    check("a rejected zip is downloaded and built again after REJECT_RECHECK_DAYS",
          code == 0 and fake.downloads == [pub, pub, pub] and fake.city_builds == builds + 1 and sent == []
          and fake.state()["cities"]["a"]["rejected"].get("checked")
          == (date.today() + timedelta(days=uvi.REJECT_RECHECK_DAYS)).isoformat()
          and fake.state()["failing"]["a"]["runs"] == 5 and (fake.base / "a.zip").read_bytes() == b"PK a good",
          repr((code, fake.downloads, fake.city_builds - builds, sent, fake.state())))
    uvi._today = date.today
    fake.body[pub] = b"PK a fixed"
    fake.etag[pub] = "a-3"
    code = fake.run()
    check("a fixed zip replaces the old one after its build",
          code == 0 and (fake.base / "a.zip").read_bytes() == b"PK a fixed" and fake.built.get("a") == b"PK a fixed"
          and fake.state()["cities"]["a"].get("etag") == "a-3", repr((code, fake.state()["cities"])))
    check("a taken zip clears the rejected fingerprint and the failure count",
          "rejected" not in fake.state()["cities"]["a"] and "a" not in fake.state()["failing"], repr(fake.state()))
    fake.etag[pub] = "a-4"
    fake.body[pub] = b"PK a newest"
    fake.city_fails = RuntimeError("bad build")
    code = fake.run()
    check("a failed city build keeps the zip in place and leaves no new one behind",
          code == uvi.REPORTED and (fake.base / "a.zip").read_bytes() == b"PK a fixed"
          and not list(fake.base.glob("*.tmp")), repr((code, sorted(p.name for p in fake.base.iterdir()))))

# A new zip that fails to load (a download cut short gets here when no
# Content-Length gave it away) keeps no fingerprint: it is downloaded and
# built again every run, and counts as failing. An entry the scripts before
# it left (rejected without a reason) no longer stops a download either.
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    pub = "https://public.example/a.zip"
    (fake.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-1")
    (fake.base / "a.zip").write_bytes(b"PK a good")
    (fake.www / "city-index.json.gz").write_bytes(fake.index_bytes({"a": b"PK a good"}))
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-1"},
        "cities": {"a": {"etag": "a-1", "sha256": "good", "rejected": {"etag": "a-2"}}},
    }))
    fake.etag[pub] = "a-2"
    fake.body[pub] = b"BROKEN a-2"
    sent = []
    uvi.notify = lambda message, *_a, **_k: sent.append(message)
    code = fake.run()
    check("a rejected fingerprint without a reason does not stop the download",
          code == 0 and fake.downloads == [pub] and fake.city_builds == 1, repr((code, fake.downloads)))
    check("a new zip that does not load keeps no rejected fingerprint",
          "rejected" not in fake.state()["cities"]["a"] and fake.state()["cities"]["a"].get("etag") == "a-1"
          and (fake.base / "a.zip").read_bytes() == b"PK a good" and sent == [],
          repr((sent, fake.state()["cities"])))
    for day in (1, 2):
        uvi._today = lambda day=day: date.today() + timedelta(days=day)
        fake.run()
    check("a new zip that does not load is downloaded and built again each run",
          fake.downloads == [pub] * 3 and fake.city_builds == 3, repr((fake.downloads, fake.city_builds)))
    check("and is reported on the third day as a zip that does not load",
          len(sent) == 1 and "a since" in sent[0] and "does not load" in sent[0]
          and fake.state()["failing"]["a"]["runs"] == 3, repr((sent, fake.state().get("failing"))))
    fake.body[pub] = b"PK a-2 whole"
    uvi._today = lambda: date.today() + timedelta(days=3)
    code = fake.run()
    check("once it loads it is taken and the failure count is cleared",
          code == 0 and (fake.base / "a.zip").read_bytes() == b"PK a-2 whole"
          and fake.state()["cities"]["a"].get("etag") == "a-2" and "a" not in fake.state()["failing"],
          repr((code, fake.state())))
    uvi._today = date.today

# A zip rejected while the one in place still runs is not kept out past
# that one's last service day ("until"): then it is downloaded and built
# again, and the guard decides anew.
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    pub = "https://public.example/a.zip"
    (fake.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-1")
    (fake.base / "a.zip").write_bytes(b"PK a good")
    (fake.www / "city-index.json.gz").write_bytes(fake.index_bytes({"a": b"PK a good"}))
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-1"},
        "cities": {"a": {"etag": "a-1", "sha256": "good"}},
    }))
    fake.etag[pub] = "a-2"
    fake.body[pub] = b"SMALL a-2"
    fake.until = (date.today() + timedelta(days=1)).strftime("%Y%m%d")
    fake.run()
    check("the rejection keeps the last day of the zip in place",
          fake.state()["cities"]["a"]["rejected"].get("until") == (date.today() + timedelta(days=1)).isoformat(),
          repr(fake.state()["cities"]))
    uvi._today = lambda: date.today() + timedelta(days=1)
    fake.run()
    check("up to that day the rejected zip is not downloaded again", fake.downloads == [pub], repr(fake.downloads))
    fake.body[pub] = b"PK a-2 (the same timetable, now acceptable)"
    uvi._today = lambda: date.today() + timedelta(days=2)
    code = fake.run()
    check("the day after, it is downloaded, built and taken",
          code == 0 and fake.downloads == [pub, pub] and fake.state()["cities"]["a"].get("etag") == "a-2"
          and "rejected" not in fake.state()["cities"]["a"], repr((code, fake.downloads, fake.state()["cities"])))
    uvi._today = date.today

# A schema conflict (no download) leaves a feed's failing streak alone: it
# says nothing about whether the feed downloads.
with tempfile.TemporaryDirectory() as tmp:
    fake = Fake(Path(tmp))
    fake.install()
    (fake.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":%d}' % gtfs_compact.INDEX_SCHEMA))
    (fake.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-1")
    (fake.www / "city-index.json.gz").write_bytes(gzip.compress(b'{"v":%d}' % (gtfs_compact.INDEX_SCHEMA + 1)))
    (fake.base / "a.zip").write_bytes(b"PK old")
    streak = {"since": (date.today() - timedelta(days=1)).isoformat(), "runs": 2, "error": "HTTP 404"}
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-1"}, "cities": {"a": {"etag": "a-1"}}, "failing": {"a": dict(streak)},
    }))
    fake.run()
    check("a moved feed not downloaded for a schema conflict keeps its failing streak",
          fake.downloads == [] and fake.state()["failing"].get("a") == streak, repr((fake.downloads, fake.state())))


def gtfs_zip(path: Path, trips: int, end: str = "20271231", route: str = "12") -> Path:
    """A one-route city feed with trips trips, running until end."""
    stop_times = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    trip_rows = ["route_id,service_id,trip_id"]
    for i in range(trips):
        trip_rows.append(f"R1,S1,T{i}")
        stop_times += [f"T{i},08:{i % 60:02d}:00,08:{i % 60:02d}:00,1,1", f"T{i},09:{i % 60:02d}:00,09:{i % 60:02d}:00,2,2"]
    files = {
        "agency.txt": "agency_id,agency_name\nA,Local\n",
        "stops.txt": "stop_id,stop_name\n1,Alpha\n2,Beta\n",
        "routes.txt": f"route_id,route_short_name,agency_id\nR1,{route},A\n",
        "trips.txt": "\n".join(trip_rows) + "\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        f"S1,1,1,1,1,1,1,1,20260101,{end}\n",
        "stop_times.txt": "\n".join(stop_times) + "\n",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return path


class RealCity(Fake):
    """Fake network, but the real build_city_index on real zips, on day self.day."""

    FEEDS = [{"id": "a", "name": "A", "url": "https://a.example/a.zip", "file": "a.zip"},
             {"id": "b", "name": "B", "url": "https://b.example/b.zip", "file": "b.zip"}]

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.day = date(2026, 10, 22)
        self.etag.update({f["url"]: f"{f['id']}-1" for f in self.FEEDS})
        self.sent: list[str] = []

    def install(self) -> None:
        super().install()
        uvi.CITY_FEEDS = bci.CITY_FEEDS = self.FEEDS
        uvi._today = lambda: self.day
        uvi.notify = lambda message, *_a, **_k: self.sent.append(message)
        today = lambda: self.day.strftime("%Y%m%d")  # noqa: E731
        uvi.build_city_index = lambda b, o, _v, *, zips=None: REAL_BUILD_CITY_INDEX(b, o, None, today=today(), zips=zips)

    def start(self, a: Path, b: Path) -> None:
        """The index in place, built from a and b on self.day."""
        shutil.copy(a, self.base / "a.zip")
        shutil.copy(b, self.base / "b.zip")
        REAL_BUILD_CITY_INDEX(self.base, self.www / "city-index.json.gz", None, today=self.day.strftime("%Y%m%d"))
        (self.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
        (self.base / "volanbusz_gtfs.zip").write_bytes(b"PK volan-1")
        (self.base / "state.json").write_text(json.dumps({
            "volan": {"etag": "volan-1"},
            "cities": {f["id"]: {"etag": f"{f['id']}-1", "sha256": uvi.file_sha256(self.base / f["file"])}
                       for f in self.FEEDS},
        }))

    def trips(self) -> dict[str, int] | None:
        return operator_trips(self.www / "city-index.json.gz")


real_city_feeds = bci.CITY_FEEDS
# The reviewers' case: a smaller winter timetable is refused while the
# summer one runs; the summer one ends, and the feed must come back with
# the winter zip the publisher still serves, not drop out for good.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    city = RealCity(root)
    city.install()
    city.start(gtfs_zip(root / "a-summer.zip", 60, end="20261031"), gtfs_zip(root / "b.zip", 60, route="7"))
    pub = "https://a.example/a.zip"
    city.etag[pub] = "a-2"
    city.body[pub] = gtfs_zip(root / "a-winter.zip", 20).read_bytes()
    city.run()
    rejected = city.state()["cities"]["a"].get("rejected", {})
    check("real build: the smaller timetable is refused while the summer one runs, until its last day",
          city.trips() == {"a": 60, "b": 60} and rejected.get("until") == "2026-10-31" and rejected.get("why")
          and city.sent == [], repr((city.trips(), city.state()["cities"]["a"], city.sent)))
    city.day = date(2026, 10, 25)
    city.run()
    check("real build: not downloaded again while the summer timetable runs", city.downloads == [pub],
          repr(city.downloads))
    city.day = date(2026, 11, 1)
    city.run()
    check("real build: the day the summer timetable ended, the winter zip is downloaded again and taken",
          city.downloads == [pub, pub] and city.trips() == {"a": 20, "b": 60}
          and "rejected" not in city.state()["cities"]["a"] and "a" not in city.state()["failing"],
          repr((city.downloads, city.trips(), city.state())))

# A rebuild for a new zip that is rejected is quiet only when the index
# stayed as it was: another feed whose calendar ended in it leaves the
# index, and that is news.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    city = RealCity(root)
    city.install()
    city.start(gtfs_zip(root / "a.zip", 60), gtfs_zip(root / "b-summer.zip", 60, end="20261031", route="7"))
    pub = "https://a.example/a.zip"
    city.etag[pub] = "a-2"
    city.body[pub] = gtfs_zip(root / "a-small.zip", 20).read_bytes()
    code = city.run()
    check("real build: a rejected zip that changes nothing sends nothing",
          code == 0 and city.sent == [] and city.trips() == {"a": 60, "b": 60}, repr((code, city.sent)))
    city.day = date(2026, 11, 2)
    city.etag[pub] = "a-3"
    city.body[pub] = gtfs_zip(root / "a-smaller.zip", 19).read_bytes()
    code = city.run()
    check("real build: a rejected zip whose rebuild lost an ended feed is announced",
          code == 0 and city.trips() == {"a": 60}
          and [m for m in city.sent if m.startswith("Új menetrend") and "calendar ended: b 2026-10-31" in m],
          repr((code, city.trips(), city.sent)))

# A connection cut mid-download (Content-Length says more) is a download
# that failed: no fingerprint is kept, the next run downloads the zip again
# and takes it.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    city = RealCity(root)
    city.install()
    city.start(gtfs_zip(root / "a.zip", 60), gtfs_zip(root / "b.zip", 60, route="7"))
    new_body = gtfs_zip(root / "a-new.zip", 62).read_bytes()
    gets: list[int] = []

    class Cut(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            gets.append(1)
            self.send_response(200)
            self.send_header("Content-Length", str(len(new_body)))
            self.end_headers()
            self.wfile.write(new_body if len(gets) > 1 else new_body[: len(new_body) // 2])
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *_a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Cut)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/a.zip"
    city.FEEDS = [{**city.FEEDS[0], "url": url, "insecure": True}, city.FEEDS[1]]
    uvi.CITY_FEEDS = bci.CITY_FEEDS = city.FEEDS
    city.etag[url] = "a-2"
    uvi.download_feed = REAL_DOWNLOAD_FEED
    real_blocked, real_assert = gtfs_compact._blocked_ip, gtfs_compact.assert_public_http_url
    # Loopback stands in for the publisher (https is tried first and fails).
    gtfs_compact._blocked_ip = lambda ip: False
    gtfs_compact.assert_public_http_url = lambda _u: None
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            code = city.run()
        check("real download cut short: a failed download, no rejected fingerprint, the zip in place stays",
              code == 0 and len(gets) == 1 and "rejected" not in city.state()["cities"]["a"]
              and city.state()["failing"]["a"]["error"] == "IncompleteDownload"
              and city.trips() == {"a": 60, "b": 60} and not list(city.base.glob("*.tmp")),
              repr((code, len(gets), city.state(), city.trips())))
        city.day = date(2026, 10, 23)
        with contextlib.redirect_stderr(io.StringIO()):
            code = city.run()
        check("real download cut short: the next run downloads the zip again and takes it",
              code == 0 and len(gets) == 2 and (city.base / "a.zip").read_bytes() == new_body
              and city.trips() == {"a": 62, "b": 60} and "a" not in city.state()["failing"],
              repr((code, len(gets), city.trips(), city.state())))
    finally:
        gtfs_compact._blocked_ip, gtfs_compact.assert_public_http_url = real_blocked, real_assert
        srv.shutdown()
        srv.server_close()
bci.CITY_FEEDS = real_city_feeds
uvi._today = date.today

with tempfile.TemporaryDirectory() as tmp:
    env_file = Path(tmp) / ".env"
    env_file.write_text(f"export HUNGARIAN_TRANSPORT_WWW={tmp}/www  # manual /local install\n"
                        f"{uvi.WEBHOOK_ENV}=http://127.0.0.1:9/api/webhook/x\n")
    clean = {k: v for k, v in os.environ.items() if k not in ("HUNGARIAN_TRANSPORT_WWW", uvi.WEBHOOK_ENV)}
    done = subprocess.run(
        [sys.executable, "-I", "-c",
         "import os, sys; sys.path.insert(0, sys.argv[1]); import update_volan_index as u;"
         " print(u.HA_WWW); print(os.environ.get(u.WEBHOOK_ENV))", str(Path(__file__).resolve().parent)],
        env={**clean, "HUNGARIAN_TRANSPORT_ENV": str(env_file)}, capture_output=True, text=True, timeout=60,
    )
    check("HUNGARIAN_TRANSPORT_WWW and the webhook are read from .env",
          done.stdout.split() == [f"{tmp}/www", "http://127.0.0.1:9/api/webhook/x"], repr((done.stdout, done.stderr)))

err = None
try:
    uvi.request("HEAD", "http://127.1/volan.zip")
except ValueError as e:
    err = e
check("HEAD requests go through the public-host guard", err is not None, repr(err))

# ---- redact(): what the log and the webhook may say ---

for raw, want in [
    ("error HEAD https://h.example/f.zip?apikey=K1&x=1 timed out", "error HEAD https://h.example/f.zip?… timed out"),
    ("not public: http://user:pw@10.0.0.1/feed", "not public: http://…@10.0.0.1/feed"),
    ("HTTP Error 403 for apikey=K1 and token=T1", "HTTP Error 403 for apikey=… and token=…"),
    ("error city rebuild: HTTP Error 404: Not Found", "error city rebuild: HTTP Error 404: Not Found"),
    ("https://h.example/download/bahart/ ok, keyboard=fine", "https://h.example/download/bahart/ ok, keyboard=fine"),
    # J-3: what the name list let through
    ("fetch /f.zip?k=S1 failed", "fetch /f.zip?… failed"),
    ("client_secret=S2", "client_secret=…"),
    ("refresh_token=S3&x=1", "refresh_token=…&x=1"),
    ("Authorization: Bearer S4", "Authorization: Bearer …"),
    ("Authorization: S4b", "Authorization: …"),
    ("apikey: S5", "apikey: …"),
    ("apikey = S6", "apikey = …"),
    ("x-api-key: S7", "x-api-key: …"),
    ('{"access_token": "S8"}', '{"access_token": "…"}'),
    ("monkey=fine, what? ok", "monkey=fine, what? ok"),
]:
    check(f"redact({raw!r})", uvi.redact(raw) == want, repr(uvi.redact(raw)))
_t0 = time.monotonic()
_long = uvi.redact("a." * 8000 + " apikey=S9")
check("redact() of a long text is quick and capped at REDACT_MAX",
      time.monotonic() - _t0 < 0.5 and len(_long) <= uvi.REDACT_MAX + 2, repr((time.monotonic() - _t0, len(_long))))
check("a secret at the start of a long text is still redacted", "S10" not in uvi.redact("apikey=S10 " + "x" * 9000))

# O-3: the traceback of a broken sibling goes through the same redaction.
_err = io.StringIO()
try:
    raise OSError("fetch https://h.example/f.zip?apikey=S11 via /g.zip?k=S12 failed")
except OSError:
    with contextlib.redirect_stderr(_err):
        uvi.print_redacted_exc()
check("print_redacted_exc() keeps the traceback and drops the secrets",
      "Traceback" in _err.getvalue() and "OSError" in _err.getvalue() and "S11" not in _err.getvalue()
      and "S12" not in _err.getvalue(), repr(_err.getvalue()[-200:]))

# The earlier blocks replaced uvi.notify; a fresh copy of the module has the real one.
_spec = importlib.util.spec_from_file_location("uvi_fresh", Path(uvi.__file__))
uvi_fresh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uvi_fresh)
posted: list[bytes] = []


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        return b""


def _fake_urlopen(req, timeout=None):
    posted.append(req.data)
    return _Resp()


_real_urlopen = uvi_fresh.urllib.request.urlopen
uvi_fresh.urllib.request.urlopen = _fake_urlopen
try:
    uvi_fresh.notify("direct: https://h.example/f.zip?apikey=K2")
finally:
    uvi_fresh.urllib.request.urlopen = _real_urlopen
check("notify() itself redacts what it posts",
      len(posted) == 1 and b"K2" not in posted[0] and b"f.zip?" in posted[0], repr(posted))

# ---- run_update.sh: the shell wrapper reports the exits the script cannot ---

script = Path(__file__).resolve().parent / "run_update.sh"
set_lines = [line for line in script.read_text().splitlines() if line.startswith("set -")]
check("run_update.sh runs with set -euo pipefail", set_lines == ["set -euo pipefail"], repr(set_lines))
probe = subprocess.run(["bash", "-c", "; ".join(set_lines + ["false | true"])], capture_output=True, timeout=30)
check("under its options a failing pipeline stage is not hidden by the last one", probe.returncode != 0,
      repr(probe.returncode))
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    base = root / "base"
    bin_dir = root / "bin"
    base.mkdir()
    bin_dir.mkdir()
    curl_log = root / "curl.log"
    stdin_log = root / "curl.stdin"
    fake_curl = bin_dir / "curl"
    # Logs its arguments one per line (the JSON body is one of them) and,
    # for `-H @-`, what it read from stdin. FAKE_CURL_FAIL fails the webhook.
    fake_curl.write_text(
        "#!/bin/sh\n"
        f"for a in \"$@\"; do printf '%s\\n' \"$a\" >> {curl_log}; done\n"
        f"for a in \"$@\"; do if [ \"$a\" = @- ]; then cat >> {stdin_log}; fi; done\n"
        "for a in \"$@\"; do case \"$a\" in *127.0.0.1*) [ -n \"$FAKE_CURL_FAIL\" ] && exit 22;; esac; done\n"
        "exit 0\n"
    )
    fake_curl.chmod(fake_curl.stat().st_mode | stat.S_IXUSR)
    worker = base / "update_volan_index.py"
    worker.write_text(
        "import os, signal, sys\n"
        "mode = os.environ.get('FAKE_MODE', '0')\n"
        "print('fake run', mode)\n"
        "if mode == 'kill':\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
        "sys.exit(int(mode))\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "HUNGARIAN_TRANSPORT_BASE": str(base),
        "HUNGARIAN_TRANSPORT_WEBHOOK": "http://127.0.0.1:1/api/webhook/test",
    }

    def run_worker(mode: str, extra: dict | None = None) -> str:
        curl_log.unlink(missing_ok=True)
        stdin_log.unlink(missing_ok=True)
        done = subprocess.run(["bash", str(script), "--worker"], env={**env, "FAKE_MODE": mode, **(extra or {})},
                              capture_output=True, text=True, timeout=60)
        check(f"worker exits 0 after a run with mode {mode}", done.returncode == 0, done.stderr)
        return curl_log.read_text() if curl_log.exists() else ""

    def body_of(posted: str) -> dict:
        args = posted.splitlines()
        return json.loads(args[args.index("--data") + 1])

    check("a clean run sends nothing from the wrapper", run_worker("0") == "")
    check(f"exit {uvi.REPORTED} (the script reported itself) sends nothing from the wrapper",
          run_worker(str(uvi.REPORTED)) == "")
    posted = run_worker("1")
    check("exit 1 (a syntax error or an uncaught exception) is reported by the wrapper",
          posted and "exit 1" in body_of(posted)["message"] and "összeomlott" in body_of(posted)["message"],
          repr(posted))
    posted = run_worker("kill")
    check("a killed script is reported by the wrapper",
          "exit 137" in posted and "http://127.0.0.1:1/api/webhook/test" in posted, repr(posted))
    posted = run_worker("2")
    check("a script that crashed before reporting is reported by the wrapper",
          "exit 2" in posted and "el sem indult" in posted, repr(posted))
    check("the log holds every run", (base / "update.log").read_text().count("fake run") == 5)

    # The webhook may come from .env, as for the script itself.
    (base / ".env").write_text("export HUNGARIAN_TRANSPORT_WEBHOOK=http://127.0.0.1:2/api/webhook/from-env  # test\n")
    shutil.copy(Path(__file__).resolve().parent / "gtfs_compact.py", base / "gtfs_compact.py")
    no_hook = {k: v for k, v in env.items() if k != "HUNGARIAN_TRANSPORT_WEBHOOK"}
    curl_log.unlink(missing_ok=True)
    done = subprocess.run(["bash", str(script), "--worker"],
                          env={**no_hook, "FAKE_MODE": "2", "HUNGARIAN_TRANSPORT_ENV": str(base / ".env")},
                          capture_output=True, text=True, timeout=60)
    posted = curl_log.read_text() if curl_log.exists() else ""
    check("the wrapper takes HUNGARIAN_TRANSPORT_WEBHOOK from .env",
          done.returncode == 0 and "http://127.0.0.1:2/api/webhook/from-env" in posted, repr((posted, done.stderr)))
    (base / "gtfs_compact.py").unlink()
    (base / ".env").unlink()
    curl_log.unlink(missing_ok=True)
    subprocess.run(["bash", str(script), "--worker"], env={**no_hook, "FAKE_MODE": "2"},
                   capture_output=True, text=True, timeout=60)
    posted = curl_log.read_text() if curl_log.exists() else ""
    check("without one the wrapper uses the default webhook",
          "http://127.0.0.1:8123/api/webhook/hungarian-transport-gtfs" in posted, repr(posted))

    token = "tok-" + "s3cr3t"
    posted = run_worker("2", {"FAKE_CURL_FAIL": "1", "SUPERVISOR_TOKEN": token})
    check("the supervisor fallback carries the token, but not on curl's command line",
          "http://supervisor/core/api/webhook/hungarian-transport-gtfs" in posted and token not in posted
          and stdin_log.exists() and f"Authorization: Bearer {token}" in stdin_log.read_text(), repr(posted))

    # The message (with the log path in it) is escaped as JSON.
    odd = root / 'ba"se\\x'
    odd.mkdir()
    shutil.copy(worker, odd / "update_volan_index.py")
    posted = run_worker("2", {"HUNGARIAN_TRANSPORT_BASE": str(odd)})
    try:
        message = body_of(posted)["message"]
    except (ValueError, KeyError) as err:
        message = repr(err)
    check("the wrapper's message is valid JSON with quotes and backslashes in it",
          f"{odd}/update.log" in message, repr(posted))
    nopy = root / "nopy"
    nopy.mkdir()
    for tool in ("bash", "tr", "sed", "cat", "mkdir", "timeout", "wc", "mv", "printf"):
        found = shutil.which(tool)
        if found:
            (nopy / tool).symlink_to(found)
    (nopy / "curl").symlink_to(fake_curl)
    posted = run_worker("2", {"HUNGARIAN_TRANSPORT_BASE": str(odd), "PATH": str(nopy)})
    try:
        message = body_of(posted)["message"]
    except (ValueError, KeyError) as err:
        message = repr(err)
    check("without python3 the message is still valid JSON", "el sem indult" in message and str(odd) in message,
          repr(posted))

    done = subprocess.run(["bash", str(script)], env={**env, "FAKE_MODE": "0"}, capture_output=True, text=True, timeout=60)
    check("the detached launcher exits 0 at once", done.returncode == 0 and done.stdout.startswith("started "), repr(done))

# ---- a half-copied sibling script is reported, and exits REPORTED -----------


class Hook(http.server.BaseHTTPRequestHandler):
    posts: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Hook.posts.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_a):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Hook)
threading.Thread(target=server.serve_forever, daemon=True).start()
here = Path(__file__).resolve().parent
try:
    for broken_source, label in (("def build_city_index(:\n", "SyntaxError"), ("CITY_FEEDS = [\n  {\n", "SyntaxError"),
                                 ("raise NameError('half copied')\n", "NameError")):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("update_volan_index.py", "gtfs_compact.py", "build_mav_index.py", "build_volan_index.py"):
                shutil.copy(here / name, Path(tmp) / name)
            (Path(tmp) / "build_city_index.py").write_text(broken_source)
            Hook.posts.clear()
            done = subprocess.run(
                [sys.executable, "-I", str(Path(tmp) / "update_volan_index.py")],
                env={**os.environ, uvi.WEBHOOK_ENV: f"http://127.0.0.1:{server.server_port}/api/webhook/test"},
                capture_output=True, text=True, timeout=60,
            )
            check(f"a sibling with a {label} ({broken_source.splitlines()[0]}) is reported and exits REPORTED",
                  done.returncode == uvi.REPORTED and len(Hook.posts) == 1
                  and label in Hook.posts[0]["message"] and "el sem indult" in Hook.posts[0]["message"],
                  repr((done.returncode, Hook.posts, done.stderr[-300:])))
finally:
    server.shutdown()

if failed:
    sys.exit(1)
print("update_volan_index tests passed")
