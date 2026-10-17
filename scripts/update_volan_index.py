#!/usr/bin/env python3
"""Daily KTI Volán + municipal GTFS check for Hungarian transport on HA OS."""
from __future__ import annotations

import fcntl
import filecmp
import gzip
import json
import os
import re
import shutil
import signal
import sys
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_city_index import CITY_FEEDS, build_city_index  # noqa: E402
from build_mav_index import build_mav_index, resolve_mav_feed  # noqa: E402
from build_volan_index import GTFS_URL, build_index  # noqa: E402
from gtfs_compact import (  # noqa: E402
    UA,
    download_feed,
    download_zip,
    feed_error,
    index_shapes,
    prune_shapes,
    resolved_url,
)

WEBHOOK_PATH = "/api/webhook/hungarian-transport-gtfs"
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


HA_WWW = default_www()

INDEX_FILES = ("volan-index.json.gz", "city-index.json.gz", "mav-index.json.gz")


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


def restore_hacs_indexes(base: Path, www: Path) -> list[str]:
    """Put the durable indexes back after HACS replaced the served folder.

    Each index brings the shapes folder it points at.
    The durable copy is the last index built here, and state.json describes
    exactly that feed; if the feed has moved since, this same run rebuilds.
    So the durable copy wins over whatever a release shipped, unless it was
    built with an older index schema than the shipped one (stale scripts in
    /config/hungarian-transport). For mav-index the larger, pfaedle-based
    index wins, since releases built without pfaedle ship a stop-sequence one.
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
                if name == "mav-index.json.gz" and dest.stat().st_size + 4096 >= src.stat().st_size:
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


def notify(message: str, title: str = "Hungarian transport GTFS") -> None:
    payload = json.dumps({"title": title, "message": message}).encode("utf-8")
    urls: list[tuple[str, dict]] = [
        ("http://127.0.0.1:8123" + WEBHOOK_PATH, {"Content-Type": "application/json", "User-Agent": UA}),
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


def request(method: str, url: str):
    hdrs = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    req = urllib.request.Request(url, method=method, headers=hdrs)
    return urllib.request.urlopen(req, timeout=60)


def remote_meta(url: str) -> dict:
    try:
        with request("HEAD", url) as resp:
            if 200 <= resp.status < 300:
                return headers_of(resp)
    except urllib.error.HTTPError as err:
        if err.code not in (403, 405, 501):
            raise
    except OSError:
        pass
    # Do not GET a 100 MB body just to read headers. Treat as unknown.
    return {}


def same_remote(state: dict, meta: dict) -> bool:
    if not state or not meta:
        return False
    if meta.get("etag") and state.get("etag"):
        return meta["etag"] == state["etag"]
    if meta.get("content_length") and state.get("content_length"):
        return str(meta["content_length"]) == str(state["content_length"])
    if meta.get("last_modified") and state.get("last_modified"):
        return meta["last_modified"] == state["last_modified"]
    return False


def acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def main() -> int:
    base, volan_out, city_out, mav_out = paths()
    lock = acquire_lock(base / "update.lock")
    if lock is None:
        print("another update is running")
        return 0
    sweep_temps(base, volan_out.parent)
    restored = restore_hacs_indexes(base, volan_out.parent)

    def on_signal(_signum, _frame):
        sweep_temps(base, volan_out.parent)
        sys.exit(1)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    state_path = base / "state.json"
    zip_path = base / "volanbusz_gtfs.zip"
    state = load_state(state_path)
    volan_state = state.get("volan") or {
        k: state.get(k)
        for k in ("etag", "last_modified", "content_length", "feed_version")
        if state.get(k)
    }
    city_state = state.get("cities") or {}
    changed: list[str] = []
    exit_code = 0
    if restored:
        changed.append("restored " + ", ".join(restored))

    try:
        try:
            volan_meta = remote_meta(GTFS_URL)
        except Exception as err:
            msg = f"error HEAD {GTFS_URL}: {err}"
            print(msg, file=sys.stderr)
            notify(f"Volán GTFS frissítés sikertelen: {msg}")
            return 1

        volan_changed = not (same_remote(volan_state, volan_meta) and volan_out.exists())
        if volan_changed:
            try:
                downloaded = download_zip(GTFS_URL, zip_path)
                info = build_index(zip_path, volan_out)
            except Exception as err:
                msg = f"error volan rebuild: {err}"
                print(msg, file=sys.stderr)
                notify(f"Volán GTFS frissítés sikertelen: {msg}")
                return 1
            volan_state = merge_meta(
                volan_meta,
                downloaded,
                zip_path.stat().st_size if zip_path.exists() else None,
            )
            volan_state["feed_version"] = info["feed_version"]
            volan_state["trips"] = info["trips"]
            volan_state["stops"] = info["stops"]
            persist_index(volan_out, base)
            changed.append(f"volan {info['feed_version'] or '?'}")
        else:
            print(f"unchanged volan {volan_state.get('feed_version') or '?'}")

        city_need = volan_changed or not city_out.exists()
        for feed in CITY_FEEDS:
            url = resolved_url(feed)
            try:
                meta = remote_meta(url)
            except Exception as err:
                print(f"warn HEAD {feed_error(feed['id'], err)}", file=sys.stderr)
                continue
            prev = city_state.get(feed["id"]) or {}
            zpath = base / feed["file"]
            if same_remote(prev, meta) and zpath.exists():
                city_state[feed["id"]] = merge_meta(meta, prev, zpath.stat().st_size)
                continue
            try:
                downloaded = download_feed(url, zpath)
            except Exception as err:
                print(f"warn download {feed_error(feed['id'], err)}", file=sys.stderr)
                continue
            city_state[feed["id"]] = merge_meta(
                meta, downloaded, zpath.stat().st_size if zpath.exists() else None
            )
            city_need = True

        if city_need:
            try:
                info = build_city_index(base, city_out, zip_path if zip_path.exists() else None)
                persist_index(city_out, base)
                changed.append(f"city ops={info['ops']} trips={info['trips']}")
            except Exception as err:
                msg = f"error city rebuild: {err}"
                print(msg, file=sys.stderr)
                notify(f"Helyi GTFS frissítés sikertelen: {msg}")
                exit_code = 1

        mav_src = None
        try:
            mav_src = resolve_mav_feed(base)
        except FileNotFoundError:
            mav_src = None
        except Exception as err:
            print(f"warn mav source: {type(err).__name__}", file=sys.stderr)
            mav_src = None
        if mav_src is not None and mav_source_newer(mav_src, mav_out):
            try:
                info = build_mav_index(mav_src, mav_out)
                persist_index(mav_out, base)
                changed.append(
                    f"mav trains={info['trains']} lines={info.get('lines') or 0}"
                )
            except Exception as err:
                msg = f"error mav rebuild: {err}"
                print(msg, file=sys.stderr)
                notify(f"MÁV GTFS frissítés sikertelen: {msg}")
                exit_code = 1
    finally:
        save_state(
            state_path,
            {
                "volan": volan_state,
                "cities": city_state,
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
