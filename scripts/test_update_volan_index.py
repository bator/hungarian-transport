#!/usr/bin/env python3
"""Restoring the durable indexes after a HACS update, and when the updater
records a feed as built. No network."""
from __future__ import annotations

import gzip
import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        self.city_builds = 0
        self.downloads: list[str] = []

    def paths(self):
        return self.base, self.www / "volan-index.json.gz", self.www / "city-index.json.gz", self.www / "mav-index.json.gz"

    def remote_meta(self, url: str) -> dict:
        if url in self.broken:
            raise OSError("unreachable")
        return {"etag": self.etag.get(url, "")}

    def download_feed(self, url: str, dest: Path) -> dict:
        if url in self.broken:
            raise OSError("unreachable")
        self.downloads.append(url)
        dest.write_bytes(b"PK" + url.encode())
        return {"size": dest.stat().st_size}

    def build_city_index(self, _base: Path, out: Path, _volan: Path | None) -> dict:
        self.city_builds += 1
        if self.city_fails is not None:
            raise self.city_fails
        out.write_bytes(gzip.compress(b'{"v":29}'))
        return {"ops": 1, "trips": 5, "skipped": []}

    def install(self) -> None:
        uvi.paths = self.paths
        uvi.remote_meta = self.remote_meta
        uvi.download_feed = self.download_feed
        uvi.build_city_index = self.build_city_index
        uvi.notify = lambda *_a, **_k: None
        uvi.GTFS_URL = "https://kti.example/volan.zip"
        uvi.CITY_FEEDS = [{
            "id": "a",
            "file": "a.zip",
            "url": "https://public.example/a.zip",
            "url_env": "TEST_A_GTFS_URL",
        }]

        def no_mav(_base):
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
    # The Volán index is current; city feed "a" was built at etag a-1 and has moved to a-2.
    (fake.www / "volan-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.www / "city-index.json.gz").write_bytes(gzip.compress(b'{"v":29}'))
    (fake.base / "a.zip").write_bytes(b"PK old")
    (fake.base / "state.json").write_text(json.dumps({
        "volan": {"etag": "volan-1"},
        "cities": {"a": {"etag": "a-1"}},
    }))

    fake.city_fails = RuntimeError("bad feed")
    code = fake.run()
    check("failed city rebuild exits 1", code == 1, str(code))
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

if failed:
    sys.exit(1)
print("update_volan_index tests passed")
