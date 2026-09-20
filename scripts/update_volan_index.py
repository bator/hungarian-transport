#!/usr/bin/env python3
"""Check KTI Volán GTFS once a day and rebuild the Lovelace index if it changed.

Intended to run on Home Assistant OS (python3 stdlib). Prints a one-line status
to stdout so a shell_command automation can notify on update or failure.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_volan_index import GTFS_URL, build_index  # noqa: E402

UA = "Hungarian-transport/1.0 (Home Assistant; +https://github.com/bator/hungarian-transport)"
HA_BASE = Path("/config/hungarian-transport")
HA_OUT = Path("/config/www/community/bkk-stop-card/volan-index.json.gz")
WEBHOOK_PATH = "/api/webhook/hungarian-transport-gtfs"


def notify(message: str, title: str = "Hungarian transport GTFS") -> None:
    """HA shell_command is killed after 60s; rebuilds notify via local webhook."""
    payload = json.dumps({"title": title, "message": message}).encode("utf-8")
    urls = [
        "http://127.0.0.1:8123" + WEBHOOK_PATH,
        "http://supervisor/core" + WEBHOOK_PATH,
    ]
    for url in urls:
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json", "User-Agent": UA},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return
        except Exception:
            continue


def paths() -> tuple[Path, Path]:
    if HA_BASE.is_dir() or Path("/config").is_dir():
        HA_BASE.mkdir(parents=True, exist_ok=True)
        HA_OUT.parent.mkdir(parents=True, exist_ok=True)
        return HA_BASE, HA_OUT
    here = Path(__file__).resolve().parent.parent
    return here, here / "volan-index.json.gz"


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


def headers_of(resp) -> dict:
    get = resp.headers.get
    last_modified = (get("Last-Modified") or "").strip()
    date = (get("Date") or "").strip()
    # KTI sets Last-Modified to the request time, so it is not a fingerprint.
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


def request(method: str, url: str, extra: dict | None = None):
    hdrs = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if extra:
        hdrs.update(extra)
    req = urllib.request.Request(url, method=method, headers=hdrs)
    return urllib.request.urlopen(req, timeout=60)


def remote_meta() -> tuple[dict, bytes | None]:
    """Return (headers, optional body). Body is set when HEAD is unavailable."""
    try:
        with request("HEAD", GTFS_URL) as resp:
            if 200 <= resp.status < 300:
                return headers_of(resp), None
    except urllib.error.HTTPError as err:
        if err.code not in (403, 405, 501):
            raise
    except OSError:
        pass

    extra = {}
    # Fall through to a GET; caller decides whether to keep the body.
    with request("GET", GTFS_URL, extra) as resp:
        meta = headers_of(resp)
        # Do not slurp 100 MB unless we already know we need it.
        return meta, None


def same_remote(state: dict, meta: dict) -> bool:
    if not state:
        return False
    if meta.get("etag") and state.get("etag"):
        return meta["etag"] == state["etag"]
    if meta.get("content_length") and state.get("content_length"):
        return meta["content_length"] == state["content_length"]
    if meta.get("last_modified") and state.get("last_modified"):
        return meta["last_modified"] == state["last_modified"]
    return False


def download_zip(dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="volanbusz_", suffix=".zip", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        req = urllib.request.Request(
            GTFS_URL,
            headers={"User-Agent": UA, "Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            meta = headers_of(resp)
            with tmp.open("wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
        tmp.replace(dest)
        return meta
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def main() -> int:
    base, out = paths()
    state_path = base / "state.json"
    zip_path = base / "volanbusz_gtfs.zip"
    state = load_state(state_path)

    try:
        meta, _body = remote_meta()
    except Exception as err:
        msg = f"error HEAD/GET {GTFS_URL}: {err}"
        print(msg, file=sys.stderr)
        notify(f"Volán GTFS frissítés sikertelen: {msg}")
        return 1

    if same_remote(state, meta) and out.exists():
        version = state.get("feed_version") or "?"
        print(f"unchanged {version}")
        return 0

    try:
        meta = download_zip(zip_path)
        info = build_index(zip_path, out)
    except Exception as err:
        msg = f"error rebuild: {err}"
        print(msg, file=sys.stderr)
        notify(f"Volán GTFS frissítés sikertelen: {msg}")
        return 1

    previous = state.get("feed_version") or ""
    try:
        meta_after, _ = remote_meta()
        if meta_after.get("last_modified") or meta_after.get("content_length"):
            meta = meta_after
    except Exception:
        pass
    new_state = {
        **meta,
        "feed_version": info["feed_version"],
        "trips": info["trips"],
        "stops": info["stops"],
        "index_bytes": info["bytes"],
        "index": str(out),
    }
    save_state(state_path, new_state)
    try:
        zip_path.unlink()
    except OSError:
        pass

    version = info["feed_version"] or "?"
    if previous and previous != version:
        line = f"updated {previous} -> {version}"
    elif previous == version:
        line = f"updated {version} (same feed_version, headers changed)"
    else:
        line = f"updated {version}"
    print(line)
    notify(f"Új Volán menetrend: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
