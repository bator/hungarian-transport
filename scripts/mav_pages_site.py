#!/usr/bin/env python3
"""The GitHub Pages site of the daily MÁV index (mav-index-daily.yml).

Takes the index release_mav_index.sh built and its statistics, checks that
it is the schema this checkout's card reads, and writes the site folder:

  mav-index.json.gz  the index, byte for byte
  mav-index.json     the manifest the integration reads first (mav_daily.py)

Whether to deploy: not when the published manifest names the same content
digest (index_digest.py), manifest format and schema. One that cannot be
read (no site yet, Pages not enabled, a network error) never stops a
deploy: publishing the same content again is harmless.

Usage: mav_pages_site.py --index mav-index.json.gz --stats mav-build.json
         --commit SHA --out SITE_DIR [--live-url URL | --live-file PATH]
         [--github-output FILE]
Prints deploy=true|false (and appends it to --github-output). Exit 1, with
nothing written, for an index or statistics that do not hold up.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent / "custom_components" / "hungarian_transport"))

import mav_index  # noqa: E402
from index_digest import digest  # noqa: E402

# Bump when the manifest changes in a way an older integration must not read.
MANIFEST_FORMAT = 1
INDEX_NAME = "mav-index.json.gz"
MANIFEST_NAME = "mav-index.json"
MAX_MANIFEST = 64 * 1024
LIVE_TIMEOUT = 20
# MÁV's feed_version: the feed's date, then an id ("20261030.M2ChangeID").
# Nothing else gets through: the value goes into a workflow output.
FEED_RE = re.compile(r"\d{8}(?!\d)[A-Za-z0-9._:+-]{0,64}")


class SiteError(Exception):
    """The index or its statistics do not make a site."""


def manifest(index: Path, stats: dict, commit: str, built_at: datetime | None = None) -> dict:
    raw = index.read_bytes()
    try:
        data = json.loads(gzip.decompress(raw))
    except (OSError, EOFError, ValueError) as err:
        raise SiteError(f"{index} is not a gzip JSON index: {err}") from err
    if not isinstance(data, dict):
        raise SiteError(f"{index} is not an index")
    if data.get("v") != mav_index.SCHEMA:
        raise SiteError(f"{index} has schema {data.get('v')!r}, this checkout reads {mav_index.SCHEMA}")
    feed = data.get("feed")
    if not isinstance(feed, str) or not FEED_RE.fullmatch(feed):
        raise SiteError(f"{index} names no dated feed: {feed!r}")
    if stats.get("feed_version") != feed:
        raise SiteError(f"statistics are of feed {stats.get('feed_version')!r}, the index of {feed!r}")
    audit = stats.get("audit")
    if not isinstance(audit, dict) or not all(
        isinstance(audit.get(k), int) for k in ("trains", "trains_ok", "trips", "trips_bad")
    ):
        raise SiteError("statistics carry no audit")
    trains, lines = len(data.get("n") or {}), len(data.get("l") or {})
    if trains + lines < mav_index.MIN_TRAINS:
        raise SiteError(f"only {trains} trains and {lines} lines")
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        raise SiteError(f"not a commit sha: {commit!r}")
    when = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    return {
        "format": MANIFEST_FORMAT,
        "schema": mav_index.SCHEMA,
        "feed": feed,
        "built_at": when.isoformat().replace("+00:00", "Z"),
        "url": INDEX_NAME,
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "digest": digest(index),
        "trains": trains,
        "lines": lines,
        "audit": {
            "trains": audit["trains"],
            "trains_ok": audit["trains_ok"],
            "trips": audit["trips"],
            "trips_ok": audit["trips"] - audit["trips_bad"],
            "ok_ratio": audit.get("ok_ratio"),
        },
        "fitted_share": stats.get("fitted_share"),
        "commit": commit,
    }


def fetch_live(url: str) -> tuple[dict | None, str]:
    """The published manifest, or None and why not."""
    if not url.startswith("https://"):
        return None, f"not an https URL: {url}"
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as res:  # noqa: S310 - https only
            body = res.read(MAX_MANIFEST + 1)
    except urllib.error.HTTPError as err:
        return None, f"HTTP {err.code}"
    except (urllib.error.URLError, OSError) as err:
        return None, f"{type(err).__name__}: {err}"
    if len(body) > MAX_MANIFEST:
        return None, "larger than a manifest"
    return parse_live(body)


def parse_live(body: bytes) -> tuple[dict | None, str]:
    try:
        live = json.loads(body)
    except ValueError as err:
        return None, f"not JSON: {err}"
    if not isinstance(live, dict):
        return None, "not a JSON object"
    return live, "ok"


def same_content(new: dict, live: dict | None) -> bool:
    """The published site serves this content already. Not the sha256: the
    gzip header carries the build time, so every build's bytes differ."""
    return live is not None and all(live.get(k) == new[k] for k in ("format", "schema", "digest"))


def write_site(index: Path, man: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(index, out / INDEX_NAME)
    (out / MANIFEST_NAME).write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--stats", type=Path, required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out", type=Path, required=True)
    live_src = ap.add_mutually_exclusive_group()
    live_src.add_argument("--live-url")
    live_src.add_argument("--live-file", type=Path)
    ap.add_argument("--github-output", type=Path)
    args = ap.parse_args(argv)
    try:
        stats = json.loads(args.stats.read_text(encoding="utf-8"))
        if not isinstance(stats, dict):
            raise SiteError(f"{args.stats} is not a statistics object")
        man = manifest(args.index, stats, args.commit)
    except (OSError, ValueError, SiteError) as err:
        print(f"no site: {err}", file=sys.stderr)
        return 1
    if args.live_file is not None:
        live, why = (parse_live(args.live_file.read_bytes()) if args.live_file.is_file() else (None, "no file"))
    elif args.live_url:
        live, why = fetch_live(args.live_url)
    else:
        live, why = None, "not asked"
    deploy = not same_content(man, live)
    write_site(args.index, man, args.out)
    audit = man["audit"]
    print(f"feed {man['feed']}, {man['size']} bytes, sha256 {man['sha256'][:12]}, digest {man['digest'][:12]}, "
          f"audit {audit['trains_ok']}/{audit['trains']} trains, {audit['trips_ok']}/{audit['trips']} trips")
    if live is None:
        print(f"published manifest: none ({why}); deploying")
    elif deploy:
        print(f"published manifest: feed {live.get('feed')!r}, digest {str(live.get('digest'))[:12]}; deploying")
    else:
        print(f"::notice title=Daily MÁV index unchanged::Feed {man['feed']} has the content GitHub Pages "
              "already serves; nothing is deployed.")
    print(f"deploy={'true' if deploy else 'false'}")
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"deploy={'true' if deploy else 'false'}\nfeed={man['feed']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
