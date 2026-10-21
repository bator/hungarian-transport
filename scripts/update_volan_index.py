#!/usr/bin/env python3
"""Daily KTI Volán + municipal GTFS check for Hungarian transport on HA OS."""
from __future__ import annotations

import fcntl
import filecmp
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

WEBHOOK_PATH = "/api/webhook/hungarian-transport-gtfs"
# Same variable as run_update.sh: another webhook URL (tests, a non-default port).
WEBHOOK_ENV = "HUNGARIAN_TRANSPORT_WEBHOOK"
# Same string as gtfs_compact.UA, kept here so notify() works when the
# sibling modules fail to import (a half-updated install).
UA = "Hungarian-transport/1.0 (Home Assistant; +https://github.com/bator/hungarian-transport)"
# Exit status after a failure this script already sent to the webhook.
# run_update.sh reports every other non-zero status itself; 1 is what Python
# exits with on an uncaught exception or a syntax error, so it is not this.
REPORTED = 3
# A feed that fails to download (every URL it has) is reported through the
# webhook once it has failed FAIL_REPORT_DAYS days in a row, then every
# FAIL_REPEAT_DAYS while it keeps failing; until then update.log has it. A
# publisher down for a day is not news, but a moved URL would otherwise only
# show when the old zip's calendar ends and the feed leaves the index.
FAIL_REPORT_DAYS = 3
FAIL_REPEAT_DAYS = 7
# state.json "failing" key of the MÁV source (MAV_GTFS_URL).
MAV_KEY = "mav"


# A keyed feed override (BAHART_GTFS_URL=...?apikey=..., a MÁV link whose
# parameter names nobody knows) can end up in an exception text. What goes to
# the log and the webhook loses:
# - every URL query, and any "?name=..." query without a scheme (/f.zip?k=...);
# - the user:password part of a URL;
# - an Authorization scheme's credentials (Bearer ..., Basic ...);
# - the value of a name whose last _/- segment is a secret word (key, token,
#   secret, password ...: apikey, client_secret, refresh_token, x-api-key),
#   with = or :, spaces and quotes around it (apikey = x, "access_token": "x").
_URL_QUERY = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s?#'\"<>]*)\?[^\s#'\"<>]*")
_BARE_QUERY = re.compile(r"\?(?=[A-Za-z0-9_.%-]+=)[^\s#'\"<>]*")
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@'\"<>]+@")
_AUTH_SCHEME = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_SECRET_WORD = r"(?:api[_-]?key|apikey|key|token|secret|password|passwd|pwd|sig|signature|credentials?|authorization)"
_SECRET_PARAM = re.compile(
    r"(?i)(^|[^a-z0-9])((?:[a-z0-9]+[_-])*" + _SECRET_WORD + r")([\"']?\s*[:=]\s*[\"']?)"
    # an Authorization scheme word stays (its credentials went with _AUTH_SCHEME)
    r"(?!(?:Bearer|Basic)\s)[^\s&'\"<>,;]+"
)
# Redaction runs on this much of a text at most: the name pattern backtracks
# on long runs of separators ("a." * 8000 took 1.8 s), and a webhook or log
# line has no use for more.
REDACT_MAX = 4096


def redact(text: str) -> str:
    """text without URL queries, URL credentials, auth credentials and the values of
    secret-named parameters (see above), cut to REDACT_MAX characters."""
    if len(text) > REDACT_MAX:
        text = text[:REDACT_MAX] + " …"
    text = _URL_USERINFO.sub(r"\1…@", text)
    text = _URL_QUERY.sub(r"\1?…", text)
    text = _BARE_QUERY.sub("?…", text)
    text = _AUTH_SCHEME.sub(r"\1 …", text)
    return _SECRET_PARAM.sub(r"\1\2\3…", text)


def print_redacted_exc() -> None:
    """traceback.print_exc() through redact(), line by line (the last lines matter most)."""
    for line in traceback.format_exc().rstrip("\n").splitlines():
        print(redact(line), file=sys.stderr)


def notify(message: str, title: str = "Hungarian transport GTFS") -> None:
    payload = json.dumps({"title": title, "message": redact(message)}).encode("utf-8")
    webhook = (os.environ.get(WEBHOOK_ENV) or "").strip() or "http://127.0.0.1:8123" + WEBHOOK_PATH
    urls: list[tuple[str, dict]] = [
        (webhook, {"Content-Type": "application/json", "User-Agent": UA}),
    ]
    token = os.environ.get("SUPERVISOR_TOKEN")
    if token:
        urls.append(
            (
                "http://supervisor/core" + WEBHOOK_PATH,
                {
                    "Content-Type": "application/json",
                    "User-Agent": UA,
                    "Authorization": f"Bearer {token}",
                },
            )
        )
    for url, headers in urls:
        try:
            req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return
        except Exception:
            continue


sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from build_city_index import CITY_FEEDS, build_city_index, ymd  # noqa: E402
    from build_mav_index import build_mav_index, resolve_mav_feed  # noqa: E402
    from build_volan_index import GTFS_URL, build_index  # noqa: E402
    from gtfs_compact import (  # noqa: E402
        INDEX_SCHEMA,
        _urlopen,
        assert_public_http_url,
        download_feed,
        download_zip,
        feed_error,
        feed_urls,
        index_shapes,
        load_env_files,
        prune_shapes,
    )
except Exception as err:  # a sibling is missing, half-copied or broken: say so, do not die quietly
    try:
        # The webhook may be set in .env; gtfs_compact itself may be the
        # sibling that broke.
        from gtfs_compact import load_env_files  # noqa: E402

        load_env_files()
    except Exception:
        pass
    # A SyntaxError or NameError in a half-copied script is not an ImportError.
    notify(f"Volán GTFS frissítés el sem indult: {type(err).__name__}: {err}")
    if __name__ != "__main__":
        raise
    print_redacted_exc()
    sys.exit(REPORTED)

HA_BASE = Path("/config/hungarian-transport")
# The integration serves /hungarian-transport/ from its own www folder, and the
# card loads the indexes next to itself. The community folder is only used by
# the old HACS Dashboard install and the manual /local install.
INTEGRATION_WWW = Path("/config/custom_components/hungarian_transport/www")
LEGACY_WWW = Path("/config/www/community/hungarian-transport")


def default_www() -> Path:
    override = (os.environ.get("HUNGARIAN_TRANSPORT_WWW") or "").strip()
    if override:
        return Path(override)
    # Never create the integration folder: without a manifest HA would see a
    # broken custom component.
    if INTEGRATION_WWW.is_dir():
        return INTEGRATION_WWW
    return LEGACY_WWW


# HUNGARIAN_TRANSPORT_WWW and HUNGARIAN_TRANSPORT_WEBHOOK may come from
# /config/hungarian-transport/.env: a shell_command has no other place for them.
load_env_files()
HA_WWW = default_www()

INDEX_FILES = ("volan-index.json.gz", "city-index.json.gz", "mav-index.json.gz")
MAV_INDEX = "mav-index.json.gz"


def _atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(src.read_bytes())
    tmp.replace(dest)


def _copy_shapes(index: Path, src_dir: Path, dest_dir: Path) -> None:
    """Copy the shapes folder an index points at, if the target lacks it."""
    name = index_shapes(index)
    if not name or (dest_dir / name).is_dir() or not (src_dir / name).is_dir():
        return
    tmp = dest_dir / f"{name}.copy"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src_dir / name, tmp)
    os.replace(tmp, dest_dir / name)


def persist_index(www_path: Path, base: Path) -> None:
    """Keep a durable copy (index and its shapes folder) outside the folder
    HACS overwrites on update."""
    if not www_path.is_file() or not base.is_dir():
        return
    dest = base / www_path.name
    try:
        if dest.resolve() == www_path.resolve():
            return
        _copy_shapes(www_path, www_path.parent, base)
        _atomic_copy(www_path, dest)
        prune_shapes(dest, index_shapes(dest))
    except OSError as err:
        print(f"warn persist {www_path.name}: {type(err).__name__}", file=sys.stderr)


def index_schema(path: Path) -> int | None:
    """Schema version from the head of a gzipped index (`{"v":N,...}`)."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(64)
    except (OSError, EOFError):
        return None
    m = re.match(rb'\{"v":(\d+)', head)
    return int(m.group(1)) if m else None


def schema_conflict(out: Path) -> str:
    """Why these scripts must not write out, or "": the index in place has a
    newer schema than they build (a release shipped it, and the copies in
    /config/hungarian-transport are older). Rebuilding it would hand the card
    an index format it no longer reads; restore_hacs_indexes keeps the
    shipped one for the same reason."""
    shipped = index_schema(out) if out.is_file() else None
    if shipped is None or shipped <= INDEX_SCHEMA:
        return ""
    return (
        f"{out.name} has index schema {shipped}, these scripts build {INDEX_SCHEMA}:"
        " not rebuilt, copy the new scripts to /config/hungarian-transport"
    )


def index_feed(path: Path) -> str:
    """Feed version from the head of a gzipped MÁV index (`{"v":N,"feed":"..."`)."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(512)
    except (OSError, EOFError):
        return ""
    m = re.match(rb'\{"v":\d+,"feed":"((?:[^"\\]|\\.)*)"', head)
    return m.group(1).decode("utf-8", errors="replace") if m else ""


def served_mav_wins(durable: Path, served: Path) -> bool:
    """Whether the served MÁV index beats the durable copy.

    The integration also builds the served one, from an uploaded or copied
    feed, without touching the durable copy. Of two builds of the same feed
    the larger, pfaedle-based one wins (an official zip gives a stop-sequence
    index). Of two different feeds, or when a feed version is unknown, the
    later build wins.
    """
    feed = index_feed(durable)
    if feed and feed == index_feed(served):
        return served.stat().st_size + 4096 >= durable.stat().st_size
    return served.stat().st_mtime > durable.stat().st_mtime


def restore_hacs_indexes(base: Path, www: Path) -> list[str]:
    """Put the durable indexes back after HACS replaced the served folder.

    Each index brings the shapes folder it points at.
    The durable copy is the last index built here, and state.json describes
    exactly that feed; if the feed has moved since, this same run rebuilds.
    So the durable copy wins over whatever a release shipped, unless it was
    built with an older index schema than the shipped one (stale scripts in
    /config/hungarian-transport). For mav-index see served_mav_wins(); a
    served index that wins becomes the durable copy, so the next HACS update
    restores it and not an older feed.
    """
    restored: list[str] = []
    if not base.is_dir():
        return restored
    www.mkdir(parents=True, exist_ok=True)
    for name in INDEX_FILES:
        src = base / name
        dest = www / name
        if not src.is_file():
            continue
        if dest.is_file():
            try:
                if filecmp.cmp(src, dest, shallow=False):
                    continue
                if name == MAV_INDEX and served_mav_wins(src, dest):
                    persist_index(dest, base)
                    continue
            except OSError:
                pass
            local_v, shipped_v = index_schema(src), index_schema(dest)
            if local_v is not None and shipped_v is not None and local_v < shipped_v:
                print(
                    f"warn keep shipped {name}: local schema {local_v} < {shipped_v};"
                    " copy the new scripts to /config/hungarian-transport",
                    file=sys.stderr,
                )
                continue
        try:
            _copy_shapes(src, base, www)
            _atomic_copy(src, dest)
            restored.append(name)
        except OSError as err:
            print(f"warn restore {name}: {type(err).__name__}", file=sys.stderr)
    for name in INDEX_FILES:
        if (www / name).is_file():
            prune_shapes(www / name, index_shapes(www / name))
    return restored


def paths() -> tuple[Path, Path, Path, Path]:
    if HA_BASE.is_dir() or Path("/config").is_dir():
        HA_BASE.mkdir(parents=True, exist_ok=True)
        HA_WWW.mkdir(parents=True, exist_ok=True)
        return (
            HA_BASE,
            HA_WWW / "volan-index.json.gz",
            HA_WWW / "city-index.json.gz",
            HA_WWW / "mav-index.json.gz",
        )
    here = Path(__file__).resolve().parent.parent
    return (
        here,
        here / "volan-index.json.gz",
        here / "city-index.json.gz",
        here / "mav-index.json.gz",
    )


def mav_source_newer(src: Path, out: Path) -> bool:
    if not out.exists():
        return True
    try:
        if src.is_dir():
            times = [p.stat().st_mtime for p in src.iterdir() if p.is_file()]
            newest = max(times) if times else 0
        else:
            newest = src.stat().st_mtime
        return newest > out.stat().st_mtime
    except OSError:
        return True


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def sweep_temps(*dirs: Path) -> None:
    for folder in dirs:
        if not folder.is_dir():
            continue
        for p in list(folder.glob("gtfs_*.zip")) + list(folder.glob("*.tmp")):
            try:
                p.unlink()
            except OSError:
                pass


def headers_of(resp) -> dict:
    get = resp.headers.get
    last_modified = (get("Last-Modified") or "").strip()
    date = (get("Date") or "").strip()
    if last_modified and date:
        try:
            lm = parsedate_to_datetime(last_modified)
            dt = parsedate_to_datetime(date)
            if abs((lm - dt).total_seconds()) < 5:
                last_modified = ""
        except (TypeError, ValueError):
            pass
    return {
        "etag": (get("ETag") or "").strip(),
        "last_modified": last_modified,
        "content_length": (get("Content-Length") or "").strip(),
    }


def merge_meta(head: dict, downloaded: dict | None, size: int | None) -> dict:
    """Keep the HEAD fingerprint; fill gaps from the GET/file, never blank them out."""
    out = dict(head or {})
    for k, v in (downloaded or {}).items():
        if v and not out.get(k):
            out[k] = v
    if size:
        out["size"] = size
        if not out.get("content_length"):
            out["content_length"] = str(size)
    return out


def request(method: str, url: str, *, allow_insecure: bool = False):
    """HEAD/GET with the same public-host and redirect guards as a download:
    a feed URL (a keyed override from .env included) must not reach the
    supervisor or the LAN.

    An http:// URL is asked over https, as download_feed fetches it: a keyed
    override (BAHART_GTFS_URL with its apikey) never goes out in the clear.
    Only a feed entry marked "insecure" is asked over plain http, and only
    such a feed may be redirected to http."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme == "http" and not allow_insecure:
        url = parsed._replace(scheme="https").geturl()
    assert_public_http_url(url)
    hdrs = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    req = urllib.request.Request(url, method=method, headers=hdrs)
    return _urlopen(req, timeout=60, allow_insecure=allow_insecure)


def remote_meta(url: str, *, allow_insecure: bool = False) -> dict:
    try:
        with request("HEAD", url, allow_insecure=allow_insecure) as resp:
            if 200 <= resp.status < 300:
                return headers_of(resp)
    except urllib.error.HTTPError as err:
        if err.code not in (403, 405, 501):
            raise
    except OSError:
        pass
    # Do not GET a 100 MB body just to read headers. Treat as unknown.
    return {}


# A HEAD whose only fingerprint is the size (no ETag; a Last-Modified that is
# just the response time is dropped, see headers_of) misses a same-size
# timetable change. Such a match holds this many days after the zip was last
# downloaded ("fetched" in state.json); then it is downloaded again and its
# sha256 decides whether it is new.
WEAK_MATCH_DAYS = 7


def same_remote(state: dict, meta: dict) -> bool:
    if not state or not meta:
        return False
    if meta.get("etag") and state.get("etag"):
        return meta["etag"] == state["etag"]
    strong = False
    if meta.get("last_modified") and state.get("last_modified"):
        if meta["last_modified"] != state["last_modified"]:
            return False
        strong = True
    if meta.get("content_length") and state.get("content_length"):
        if str(meta["content_length"]) != str(state["content_length"]):
            return False
        if not strong:
            fetched = _day(state.get("fetched"))
            return fetched is not None and 0 <= (_today() - fetched).days < WEAK_MATCH_DAYS
    return strong


def file_sha256(path: Path) -> str:
    """Content hash of a downloaded zip: a server whose HEAD gives no usable
    fingerprint (403/405, a timeout, no ETag, length or Last-Modified) sends
    the same bytes again, and that is no new timetable."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _today() -> date:
    return date.today()


def _day(text: object) -> date | None:
    try:
        return date.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def error_name(err: BaseException | None) -> str:
    """What failed, without the URL (a keyed override carries its key)."""
    if err is None:
        return "no URL"
    if isinstance(err, urllib.error.HTTPError):
        return f"HTTP {err.code}"
    return type(err).__name__


def note_failure(failing: dict, key: str, error: str, today: date) -> dict | None:
    """Count one more failed run of a feed in failing (state.json); the
    entry when a report is due (see FAIL_REPORT_DAYS), else None."""
    entry = failing.get(key)
    if not isinstance(entry, dict) or _day(entry.get("since")) is None:
        entry = failing[key] = {"since": today.isoformat(), "runs": 0}
    try:
        runs = int(entry.get("runs") or 0)
    except (TypeError, ValueError):
        runs = 0
    entry["runs"] = runs + 1
    entry["error"] = error
    since = _day(entry["since"]) or today
    reported = _day(entry.get("reported"))
    if (today - since).days + 1 < FAIL_REPORT_DAYS:
        return None
    if reported is not None and (today - reported).days < FAIL_REPEAT_DAYS:
        return None
    entry["reported"] = today.isoformat()
    return entry


def failure_report(due: dict[str, dict], today: date) -> str:
    parts = []
    for key, entry in sorted(due.items()):
        since = _day(entry.get("since")) or today
        parts.append(
            f"{key} since {since.isoformat()} ({(today - since).days + 1} days,"
            f" {entry.get('runs')} runs, last {entry.get('error')})"
        )
    return "download failing: " + "; ".join(parts)


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def report(title: str, msg: str) -> None:
    """A failure, to update.log and to the webhook. The raw exception text in
    msg may hold a keyed URL: both get it redacted."""
    msg = redact(msg)
    print(msg, file=sys.stderr)
    notify(f"{title}: {msg}")


def main() -> int:
    """Run the update; any failure reaches the webhook, not only the log.

    run_update.sh detaches this script, so a traceback in update.log is all a
    silent failure would leave. Exit REPORTED (3) means "reported through
    the webhook"; the shell wrapper reports every other failure (killed, not
    started, a crash this function did not catch)."""
    try:
        return _main()
    except SystemExit:
        raise
    except Exception as err:
        report("Volán GTFS frissítés sikertelen", f"error {type(err).__name__}: {err}")
        return REPORTED


def _main() -> int:
    # The folders whose temp files this run owns, swept on SIGTERM. Filled
    # once the lock is held: before that, they belong to the run holding it.
    owned: list[Path] = []

    def on_signal(signum, _frame):
        # SIGTERM from `timeout`, a Home Assistant restart or shell_command's
        # process-group kill: the run did not finish, say so.
        sweep_temps(*owned)
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        notify(f"Volán GTFS frissítés megszakítva ({name}): az indexek nem frissültek, lásd update.log")
        sys.exit(REPORTED)

    # First thing: a kill during the lock, the sweep or the HACS restore is
    # reported as well.
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    base, volan_out, city_out, mav_out = paths()
    lock = acquire_lock(base / "update.lock")
    if lock is None:
        print("another update is running")
        return 0
    owned.extend((base, volan_out.parent))
    sweep_temps(*owned)
    restored = restore_hacs_indexes(base, volan_out.parent)

    state_path = base / "state.json"
    zip_path = base / "volanbusz_gtfs.zip"
    # A new Volán zip waits here until its index built (sweep_temps takes it
    # if the run dies): the city index takes its Volán local lines from
    # zip_path, so a zip that fails build_index must not replace it.
    new_zip = base / (zip_path.name + ".tmp")
    state = load_state(state_path)
    volan_state = state.get("volan") or {
        k: state.get(k)
        for k in ("etag", "last_modified", "content_length", "feed_version")
        if state.get(k)
    }
    city_state = state.get("cities") or {}
    # Fingerprints of city zips downloaded this run. They go into state.json
    # only once a city index was built from them; otherwise (failed build,
    # SIGTERM) the next run downloads and rebuilds again.
    city_pending: dict[str, dict] = {}
    # Their zips, next to the ones in use (feed id -> path), until then.
    city_staged: dict[str, Path] = {}
    city_retry = bool(state.get("city_retry"))
    # Feeds failing to download, run after run: {id: {since, runs, error, reported}}.
    feed_ids = {feed["id"] for feed in CITY_FEEDS}
    failing = {
        k: v
        for k, v in (state.get("failing") or {}).items()
        if (k in feed_ids or k == MAV_KEY) and isinstance(v, dict)
    }
    # Schema conflicts (see schema_conflict) already sent to the webhook:
    # index name -> message. Reported once, not every day; one that cleared
    # is forgotten, so it is reported again should it come back.
    schema_reported = {
        k: v
        for k, v in (state.get("schema_reported") or {}).items()
        if isinstance(v, str) and k in (volan_out.name, city_out.name)
    }
    for out in (volan_out, city_out):
        if out.name in schema_reported and not schema_conflict(out):
            schema_reported.pop(out.name)

    def skip_rebuild(title: str, out: Path, msg: str) -> int:
        if schema_reported.get(out.name) == msg:
            print(f"warn {msg} (reported before)", file=sys.stderr)
            return 0
        schema_reported[out.name] = msg
        report(title, msg)
        return REPORTED

    today = _today()
    changed: list[str] = []
    exit_code = 0
    if restored:
        changed.append("restored " + ", ".join(restored))

    try:
        # A Volán failure is reported and the run goes on: the city feeds and
        # the local MÁV index do not depend on it.
        volan_built = False
        try:
            volan_meta: dict | None = remote_meta(GTFS_URL)
        except Exception as err:
            volan_meta = None
            report("Volán GTFS frissítés sikertelen", f"error HEAD {GTFS_URL}: {err}")
            exit_code = REPORTED

        if volan_meta is not None and not (
            same_remote(volan_state, volan_meta) and volan_out.exists() and zip_path.exists()
        ) and (volan_conflict := schema_conflict(volan_out)):
            # A rebuild would follow, but these scripts must not write this
            # index. Checked before the download: the same 100 MB every day
            # for an index that cannot be written helps nobody.
            exit_code = skip_rebuild("Volán GTFS frissítés kihagyva", volan_out, volan_conflict) or exit_code
        elif volan_meta is not None and not (
            same_remote(volan_state, volan_meta) and volan_out.exists() and zip_path.exists()
        ):
            try:
                downloaded = download_zip(GTFS_URL, new_zip)
                digest = file_sha256(new_zip)
                info = None
                if digest != volan_state.get("sha256") or not volan_out.exists():
                    info = build_index(new_zip, volan_out)
                new_zip.replace(zip_path)
            except Exception as err:
                new_zip.unlink(missing_ok=True)
                report("Volán GTFS frissítés sikertelen", f"error volan rebuild: {err}")
                exit_code = REPORTED
            else:
                size = zip_path.stat().st_size
                if info is None:
                    # The zip the index was built from, byte for byte.
                    volan_state = merge_meta(volan_meta, volan_state, size)
                    volan_state["fetched"] = today.isoformat()
                    print(f"unchanged volan {volan_state.get('feed_version') or '?'} (same zip)")
                else:
                    volan_state = merge_meta(volan_meta, downloaded, size)
                    volan_state["fetched"] = today.isoformat()
                    volan_state["sha256"] = digest
                    volan_state["feed_version"] = info["feed_version"]
                    volan_state["trips"] = info["trips"]
                    volan_state["stops"] = info["stops"]
                    persist_index(volan_out, base)
                    changed.append(f"volan {info['feed_version'] or '?'}")
                    volan_built = True
        elif volan_meta is not None:
            print(f"unchanged volan {volan_state.get('feed_version') or '?'}")

        # A city rebuild that failed (or was killed) last time is retried even
        # when no feed moved since; the Volán local routes come from zip_path.
        city_retry = city_retry or volan_built or not city_out.exists()
        due: dict[str, dict] = {}
        for feed in CITY_FEEDS:
            fid = feed["id"]
            prev = city_state.get(fid) or {}
            zpath = base / feed["file"]
            insecure = bool(feed.get("insecure"))
            fetched = False
            last_err: BaseException | None = None
            # A keyed override (BAHART_GTFS_URL) that fails falls back to the
            # catalog and then the public URL.
            for url in feed_urls(feed):
                try:
                    meta = remote_meta(url, allow_insecure=insecure)
                except Exception as err:
                    last_err = err
                    print(f"warn HEAD {feed_error(fid, err)}", file=sys.stderr)
                    continue
                if same_remote(prev, meta) and zpath.exists():
                    city_state[fid] = merge_meta(meta, prev, zpath.stat().st_size)
                    fetched = True
                    break
                # The zip in place stays until a city index was built from
                # the new one (build_city_index rejects one that fails to
                # load and keeps using the old).
                staged = zpath.with_name(zpath.name + ".tmp")
                try:
                    downloaded = download_feed(url, staged, allow_insecure=insecure)
                except Exception as err:
                    staged.unlink(missing_ok=True)
                    last_err = err
                    print(f"warn download {feed_error(fid, err)}", file=sys.stderr)
                    continue
                fetched = True
                size = staged.stat().st_size if staged.exists() else None
                digest = file_sha256(staged) if staged.exists() else ""
                if digest and digest == prev.get("sha256") and zpath.exists():
                    # The zip the index was built from, byte for byte.
                    staged.unlink()
                    city_state[fid] = merge_meta(meta, prev, size)
                    city_state[fid]["fetched"] = today.isoformat()
                    print(f"unchanged {fid} (same zip)")
                    break
                city_pending[fid] = merge_meta(meta, downloaded, size)
                city_pending[fid]["fetched"] = today.isoformat()
                if digest:
                    city_pending[fid]["sha256"] = digest
                if staged.exists():
                    city_staged[fid] = staged
                city_retry = True
                break
            if fetched:
                failing.pop(fid, None)
            else:
                entry = note_failure(failing, fid, error_name(last_err), today)
                if entry is not None:
                    due[fid] = entry
        if due:
            # Not a failed run: the index keeps the last zip of each, and the
            # rest of the run goes on.
            report(
                "Helyi GTFS frissítés sikertelen",
                failure_report(due, today) + "; the index keeps the last zip until its calendar ends,"
                " check the feed URL",
            )

        city_conflict = schema_conflict(city_out) if city_retry else ""
        if city_conflict:
            # city_retry stays set and the new zips stay pending: the scripts
            # that can write this index rebuild it on their first run.
            exit_code = skip_rebuild("Helyi GTFS frissítés kihagyva", city_out, city_conflict) or exit_code
        elif city_retry:
            try:
                info = build_city_index(
                    base, city_out, zip_path if zip_path.exists() else None, zips=dict(city_staged)
                )
                # The new zips the index was built from replace the old ones;
                # a rejected one goes, and its feed is downloaded again next
                # run (state.json keeps the old zip's fingerprint).
                rejected = set(info.get("rejected") or [])
                for fid, staged in city_staged.items():
                    if fid in rejected:
                        staged.unlink(missing_ok=True)
                    else:
                        staged.replace(staged.with_name(staged.name.removesuffix(".tmp")))
                city_staged.clear()
                persist_index(city_out, base)
                expired = info.get("expired") or {}
                # A feed the index skipped because of its content (an ended
                # calendar) is recorded: the same zip would be skipped
                # again. One that failed to load is downloaded and rebuilt
                # again next time; its fingerprint is dropped, so the same
                # HEAD or even the same bytes count as new then.
                skipped = [fid for fid in info.get("skipped") or [] if fid not in expired]
                for fid, meta in city_pending.items():
                    if fid not in skipped and fid not in rejected:
                        city_state[fid] = meta
                for fid in skipped:
                    if isinstance(city_state.get(fid), dict):
                        for key in ("etag", "last_modified", "content_length", "size", "sha256", "fetched"):
                            city_state[fid].pop(key, None)
                city_retry = False
                line = f"city ops={info['ops']} trips={info['trips']}"
                if expired:
                    line += " (calendar ended: " + ", ".join(
                        f"{fid} {ymd(end)}" for fid, end in sorted(expired.items())
                    ) + ")"
                if skipped:
                    line += " (skipped: " + ", ".join(skipped) + ")"
                if rejected:
                    line += " (new zip rejected, previous kept: " + ", ".join(sorted(rejected)) + ")"
                changed.append(line)
            except Exception as err:
                report("Helyi GTFS frissítés sikertelen", f"error city rebuild: {err}")
                exit_code = REPORTED

        mav_src = None
        mav_errors: list[BaseException] = []
        try:
            mav_src = resolve_mav_feed(base, url_errors=mav_errors)
        except FileNotFoundError:
            mav_src = None
        except Exception as err:
            print(f"warn mav source: {type(err).__name__}", file=sys.stderr)
            mav_errors.append(err)
            mav_src = None
        if mav_errors:
            entry = note_failure(failing, MAV_KEY, error_name(mav_errors[-1]), today)
            if entry is not None:
                report(
                    "MÁV GTFS frissítés sikertelen",
                    failure_report({MAV_KEY: entry}, today) + "; the MÁV index stays as it was, check MAV_GTFS_URL",
                )
        else:
            failing.pop(MAV_KEY, None)
        if mav_src is not None and mav_source_newer(mav_src, mav_out):
            try:
                info = build_mav_index(mav_src, mav_out)
                persist_index(mav_out, base)
                changed.append(
                    f"mav trains={info['trains']} lines={info.get('lines') or 0}"
                )
            except Exception as err:
                report("MÁV GTFS frissítés sikertelen", f"error mav rebuild: {err}")
                exit_code = REPORTED
    finally:
        # New city zips no index was built from (failed build, schema
        # conflict, SIGTERM): the zips in place stay.
        for staged in city_staged.values():
            staged.unlink(missing_ok=True)
        save_state(
            state_path,
            {
                "volan": volan_state,
                "cities": city_state,
                "city_retry": city_retry,
                "failing": failing,
                "schema_reported": schema_reported,
                "index": str(volan_out),
                "city_index": str(city_out),
                "mav_index": str(mav_out),
            },
        )
        lock.close()

    if exit_code:
        return exit_code
    if not changed:
        print("unchanged")
        return 0
    line = "updated " + "; ".join(changed)
    print(line)
    notify(f"Új menetrend: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
