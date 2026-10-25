#!/usr/bin/env python3
"""release_mav_index.sh against a local https server: the real curl, the
script's own netrc and retry handling, its cleanup and its pfaedle time limit.

curl reaches the server through a .curlrc (CURL_HOME) that points
www.mavcsoport.hu and download.geofabrik.de at 127.0.0.1 and trusts a
throwaway certificate; the script itself runs unchanged. docker is a stub.
Needs bash, curl, openssl and timeout (coreutils); skips without them.
"""

from __future__ import annotations

import base64
import http.server
import io
import os
import random
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "release_mav_index.sh"
MAV_PATH = "/gtfs/gtfsMavMenetrend.zip"
OSM_PATH = "/europe/hungary-latest.osm.pbf"

failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        failed.append(name)


for tool in ("bash", "curl", "openssl", "timeout"):
    if shutil.which(tool) is None:
        print(f"skip release_mav_index tests: no {tool}")
        sys.exit(0)


def gtfs_zip() -> bytes:
    """Over 1 MB (the script's floor), with the members it checks for."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name in ("routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "feed_info.txt"):
            z.writestr(name, "id\n1\n")
        z.writestr("padding.txt", random.Random(7).randbytes(1_200_000))
    return buf.getvalue()


class Handler(http.server.BaseHTTPRequestHandler):
    # path -> (status, body); every request is logged with its Basic auth.
    routes: dict[str, tuple[int, bytes]] = {}
    seen: list[tuple[str, str | None]] = []

    def do_GET(self):  # noqa: N802
        auth = self.headers.get("Authorization")
        decoded = base64.b64decode(auth.split()[1]).decode("utf-8") if auth else None
        Handler.seen.append((self.path, decoded))
        status, body = Handler.routes.get(self.path, (404, b""))
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="gtfs"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def requests_to(path: str) -> list[str | None]:
    return [auth for p, auth in Handler.seen if p == path]


with tempfile.TemporaryDirectory() as tmp_name:
    tmp = Path(tmp_name)
    cert, key = tmp / "cert.pem", tmp / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-keyout", str(key), "-out", str(cert), "-subj", "/CN=www.mavcsoport.hu",
         "-addext", "subjectAltName=DNS:www.mavcsoport.hu,DNS:download.geofabrik.de"],
        check=True, capture_output=True,
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    curl_home = tmp / "curlhome"
    curl_home.mkdir()
    (curl_home / ".curlrc").write_text(
        f'cacert = "{cert}"\n'
        f"connect-to = www.mavcsoport.hu:443:127.0.0.1:{port}\n"
        f"connect-to = download.geofabrik.de:443:127.0.0.1:{port}\n"
    )

    # A checkout as far as the script looks before it downloads anything.
    checkout = tmp / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "custom_components" / "hungarian_transport").mkdir(parents=True)
    (checkout / "scripts" / "build_mav_index.py").write_text("")
    (checkout / "custom_components" / "hungarian_transport" / "mav_index.py").write_text("")

    # docker: records its arguments; "run" fails, or hangs with DOCKER_HANG.
    stub = tmp / "stub"
    stub.mkdir()
    docker_log = tmp / "docker.log"
    (stub / "docker").write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{docker_log}"\n'
        'if [ "$1" = run ]; then\n'
        '  if [ -n "${DOCKER_HANG:-}" ]; then exec sleep 60; fi\n'
        "  exit 3\n"
        "fi\n"
        "exit 0\n"
    )
    (stub / "docker").chmod(0o755)
    # curl for runs that must not download anything.
    curl_log = tmp / "curl.log"
    no_curl = tmp / "nocurl"
    no_curl.mkdir()
    (no_curl / "curl").write_text(f'#!/usr/bin/env bash\necho "$*" >> "{curl_log}"\nexit 7\n')
    (no_curl / "curl").chmod(0o755)

    def run(work: str, *, password: str | None = "pw", user: str = "test-user", path_extra: list[Path] = (),
            **env: str) -> subprocess.CompletedProcess:
        e = {k: v for k, v in os.environ.items()
             if k not in ("MAV_GTFS_USER", "MAV_GTFS_PASSWORD", "MAV_GTFS_URL", "MAV_GTFS_ZIP", "OSM_PBF",
                          "GITHUB_STEP_SUMMARY", "PFAEDLE_TIMEOUT", "MAV_INDEX_OUT")
             # a proxy in the developer's shell would take curl past connect-to
             and not k.lower().endswith("_proxy")}
        e["PATH"] = os.pathsep.join([str(p) for p in path_extra] + [str(stub), os.environ["PATH"]])
        e["CURL_HOME"] = str(curl_home)
        e["DOWNLOAD_RETRY_DELAY"] = "1"
        e["MAV_INDEX_OUT"] = str(tmp / "out-index.json.gz")
        if password is not None:
            e["MAV_GTFS_USER"], e["MAV_GTFS_PASSWORD"] = user, password
        e.update(env)
        return subprocess.run(["bash", str(SCRIPT), work], cwd=checkout, env=e,
                              capture_output=True, text=True, timeout=120)

    # 1. A refused login is asked once, never retried (each retry is another
    # login); 403 and other 4xx alike. 5xx is retried, 6 attempts in all.
    for status, want in ((401, 1), (403, 1), (404, 1), (503, 6)):
        Handler.seen.clear()
        Handler.routes = {MAV_PATH: (status, b"")}
        done = run(str(tmp / f"w{status}"), DOWNLOAD_RETRY_DELAY="0" if status != 503 else "1")
        n = len(requests_to(MAV_PATH))
        check(f"HTTP {status}: {want} request(s), then the script fails",
              done.returncode != 0 and n == want, repr((done.returncode, n, done.stderr[-400:])))
        check(f"HTTP {status}: the netrc is removed", not (tmp / f"w{status}" / "netrc").exists())

    # 2. The netrc round trip: what curl sends is the password as set, for
    # the characters a netrc treats specially (a leading # is a comment).
    for user, password in (("test-user", "#fakepw"), ("#user", "pw #2"), ("test-user", "with space"),
                           ("test-user", 'q"uote'), ("test-user", "back\\slash\\"), ("u", "plain"),
                           ("test-user", '"#\\ x')):
        Handler.seen.clear()
        Handler.routes = {MAV_PATH: (401, b"")}
        done = run(str(tmp / "wnetrc"), user=user, password=password)
        got = requests_to(MAV_PATH)
        check(f"netrc round trip {user!r}:{password!r}", got == [f"{user}:{password}"], repr(got))
        check(f"netrc round trip {password!r}: the password is in no output",
              password not in done.stdout + done.stderr or password == "plain")

    Handler.seen.clear()
    done = run(str(tmp / "wctl"), password="pw\x01x")
    check("a control character in the password is refused before any request",
          done.returncode != 0 and not Handler.seen and "control character" in done.stderr,
          repr((done.returncode, Handler.seen, done.stderr[-300:])))

    # 3. An OSM download that breaks off leaves nothing behind for
    # actions/cache: the .part and .md5 go with the failed run.
    Handler.seen.clear()
    Handler.routes = {MAV_PATH: (200, gtfs_zip()), OSM_PATH: (200, b"pbf bytes")}  # .md5 is a 404
    osm = tmp / "osmcache" / "hungary-latest.osm.pbf"
    done = run(str(tmp / "wosm"), OSM_PBF=str(osm))
    check("a failed OSM download fails the script", done.returncode != 0 and "MÁV GTFS ok" in done.stdout,
          repr((done.returncode, done.stdout[-300:], done.stderr[-300:])))
    check("a failed OSM download leaves no .part or .md5",
          not osm.exists() and not Path(str(osm) + ".part").exists() and not Path(str(osm) + ".md5").exists(),
          repr(sorted(p.name for p in osm.parent.iterdir())))
    check("the GTFS download sent the login once", requests_to(MAV_PATH) == ["test-user:pw"], repr(Handler.seen))

    # 4. MAV_GTFS_ZIP: a zip downloaded before is used as is, without
    # credentials and without curl. 5. A relative WORK_DIR without OSM_PBF
    # gives docker an absolute source.
    first = tmp / "first.zip"
    first.write_bytes(gtfs_zip())
    rel = checkout / "rel" / "work"
    rel.mkdir(parents=True)
    (rel / "hungary-latest.osm.pbf").write_bytes(b"cached extract")
    docker_log.unlink(missing_ok=True)
    curl_log.unlink(missing_ok=True)
    done = run("rel/work", password=None, path_extra=[no_curl], MAV_GTFS_ZIP=str(first))
    runs = [line for line in docker_log.read_text().splitlines() if line.startswith("run ")] \
        if docker_log.exists() else []
    check("MAV_GTFS_ZIP: no download, no credentials needed, pfaedle reached",
          not curl_log.exists() and len(runs) == 1 and "MÁV GTFS ok" in done.stdout
          and (rel / "gtfs" / "mav-gtfs.zip").read_bytes() == first.read_bytes(),
          repr((done.returncode, curl_log.exists(), runs, done.stderr[-300:])))
    check("a relative WORK_DIR: docker -v gets an absolute extract path",
          bool(runs) and f"-v {rel.resolve()}/hungary-latest.osm.pbf:/osm/hungary-latest.osm.pbf:ro" in runs[0],
          repr(runs))
    check("a failed pfaedle run fails the script and removes its container",
          done.returncode != 0 and "pfaedle failed" in done.stderr
          and any(line.startswith("rm -f mav-pfaedle-") for line in docker_log.read_text().splitlines()),
          repr((done.returncode, done.stderr[-300:])))

    done = run(str(rel), password=None, MAV_GTFS_ZIP=str(rel / "gtfs" / "mav-gtfs.zip"))
    check("MAV_GTFS_ZIP inside the work dir it clears is refused",
          done.returncode == 2 and "work dir" in done.stderr, repr((done.returncode, done.stderr)))
    done = run(str(tmp / "wnone"), password=None)
    check("without credentials and without MAV_GTFS_ZIP the script refuses",
          done.returncode == 2 and "required" in done.stderr, repr((done.returncode, done.stderr)))

    # 6. A pfaedle run that hangs is stopped at PFAEDLE_TIMEOUT.
    docker_log.unlink(missing_ok=True)
    done = run("rel/work", password=None, MAV_GTFS_ZIP=str(first), PFAEDLE_TIMEOUT="2s", DOCKER_HANG="1")
    log = docker_log.read_text().splitlines() if docker_log.exists() else []
    check("a pfaedle run past PFAEDLE_TIMEOUT fails the script",
          done.returncode != 0 and "did not finish within 2s" in done.stderr, repr((done.returncode, done.stderr)))
    check("its container is removed", any(line.startswith("rm -f mav-pfaedle-") for line in log), repr(log))

    server.shutdown()

if failed:
    print(f"{len(failed)} failed")
    sys.exit(1)
print("release_mav_index tests passed")
