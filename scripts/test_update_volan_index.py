#!/usr/bin/env python3
"""Restoring the durable indexes after a HACS update, and when the updater
records a feed as built. No network."""
from __future__ import annotations

import gzip
import http.server
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
import urllib.error
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gtfs_compact  # noqa: E402
import update_volan_index as uvi  # noqa: E402
from update_volan_index import index_feed, index_schema, persist_index, restore_hacs_indexes  # noqa: E402

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

    def paths(self):
        return self.base, self.www / "volan-index.json.gz", self.www / "city-index.json.gz", self.www / "mav-index.json.gz"

    def remote_meta(self, url: str, **kw) -> dict:
        self.heads.append((url, kw))
        if url in self.broken:
            raise OSError("unreachable")
        return {"etag": self.etag.get(url, "")}

    def download_feed(self, url: str, dest: Path, **_kw) -> dict:
        if url in self.broken:
            raise OSError("unreachable")
        self.downloads.append(url)
        # The bytes follow the fingerprint unless a test pins them.
        dest.write_bytes(self.body.get(url) or b"PK" + url.encode() + self.etag.get(url, "").encode())
        return {"size": dest.stat().st_size}

    def build_city_index(self, _base: Path, out: Path, _volan: Path | None) -> dict:
        self.city_builds += 1
        if self.city_fails is not None:
            raise self.city_fails
        out.write_bytes(gzip.compress(b'{"v":29}'))
        return dict(self.city_info)

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

    def kill_self(_base, out, _volan):
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

    def keyed_failure(_base, out, _volan):
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
    check("a feed skipped for failing to load is not recorded",
          fake.state()["cities"]["a"]["etag"] == "a-7", repr(fake.state()["cities"]))
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

    def recording_city(_base: Path, out: Path, volan: Path | None) -> dict:
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
