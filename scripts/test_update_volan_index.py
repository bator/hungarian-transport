#!/usr/bin/env python3
"""Restoring the durable indexes after a HACS update, and when the updater
records a feed as built. No network."""
from __future__ import annotations

import gzip
import http.server
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

    def paths(self):
        return self.base, self.www / "volan-index.json.gz", self.www / "city-index.json.gz", self.www / "mav-index.json.gz"

    def remote_meta(self, url: str) -> dict:
        if url in self.broken:
            raise OSError("unreachable")
        return {"etag": self.etag.get(url, "")}

    def download_feed(self, url: str, dest: Path, **_kw) -> dict:
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
        return dict(self.city_info)

    def download_zip(self, url: str, dest: Path) -> dict:
        return self.download_feed(url, dest)

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

check("notify's user agent is the downloader's", uvi.UA == gtfs_compact.UA)

err = None
try:
    uvi.request("HEAD", "http://127.1/volan.zip")
except ValueError as e:
    err = e
check("HEAD requests go through the public-host guard", err is not None, repr(err))

# ---- run_update.sh: the shell wrapper reports the exits the script cannot ---

script = Path(__file__).resolve().parent / "run_update.sh"
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
