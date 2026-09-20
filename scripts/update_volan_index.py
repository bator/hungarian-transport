#!/usr/bin/env python3
"""Daily KTI Volán + municipal GTFS check for Hungarian transport on HA OS."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import sys
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_city_index import CITY_FEEDS, build_city_index  # noqa: E402
from build_volan_index import GTFS_URL, build_index  # noqa: E402
from gtfs_compact import UA, download_feed, download_zip  # noqa: E402

WEBHOOK_PATH = "/api/webhook/hungarian-transport-gtfs"
HA_BASE = Path("/config/hungarian-transport")
HA_WWW = Path(
    os.environ.get(
        "HUNGARIAN_TRANSPORT_WWW",
        "/config/www/community/hungarian-transport",
    )
)


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


def paths() -> tuple[Path, Path, Path]:
    if HA_BASE.is_dir() or Path("/config").is_dir():
        HA_BASE.mkdir(parents=True, exist_ok=True)
        HA_WWW.mkdir(parents=True, exist_ok=True)
        return HA_BASE, HA_WWW / "volan-index.json.gz", HA_WWW / "city-index.json.gz"
    here = Path(__file__).resolve().parent.parent
    return here, here / "volan-index.json.gz", here / "city-index.json.gz"


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
    base, volan_out, city_out = paths()
    lock = acquire_lock(base / "update.lock")
    if lock is None:
        print("another update is running")
        return 0
    sweep_temps(base, volan_out.parent)

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
            changed.append(f"volan {info['feed_version'] or '?'}")
        else:
            print(f"unchanged volan {volan_state.get('feed_version') or '?'}")

        city_need = volan_changed or not city_out.exists()
        for feed in CITY_FEEDS:
            try:
                meta = remote_meta(feed["url"])
            except Exception as err:
                print(f"warn HEAD {feed['id']}: {err}", file=sys.stderr)
                continue
            prev = city_state.get(feed["id"]) or {}
            zpath = base / feed["file"]
            if same_remote(prev, meta) and zpath.exists():
                city_state[feed["id"]] = merge_meta(meta, prev, zpath.stat().st_size)
                continue
            try:
                downloaded = download_feed(feed["url"], zpath)
            except Exception as err:
                print(f"warn download {feed['id']}: {err}", file=sys.stderr)
                continue
            city_state[feed["id"]] = merge_meta(
                meta, downloaded, zpath.stat().st_size if zpath.exists() else None
            )
            city_need = True

        if city_need:
            try:
                info = build_city_index(base, city_out, zip_path if zip_path.exists() else None)
                changed.append(f"city ops={info['ops']} trips={info['trips']}")
            except Exception as err:
                msg = f"error city rebuild: {err}"
                print(msg, file=sys.stderr)
                notify(f"Helyi GTFS frissítés sikertelen: {msg}")
                exit_code = 1
    finally:
        save_state(
            state_path,
            {
                "volan": volan_state,
                "cities": city_state,
                "index": str(volan_out),
                "city_index": str(city_out),
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
