#!/usr/bin/env python3
"""The GitHub Pages site of the daily MÁV index (mav-index-daily.yml).

Takes the index release_mav_index.sh built and its statistics, checks that
it is the schema this checkout's card reads, and writes the site folder:

  mav-index.json.gz        the index, byte for byte
  mav-index.json           the manifest the integration reads first
                           (mav_daily.py)
  mav-gtfs.zip             with --gtfs-zip: MÁV's GTFS exactly as downloaded
                           for this build (CC0)
  mav-pfaedle-overlay.zip  with --pfaedle-dir: pfaedle's trips.txt,
                           shapes.txt and stop_times.txt of the same build,
                           zipped deterministically (the same three files
                           give the same bytes)
  ATTRIBUTION.txt, index.html  what the files are, their licences

The GTFS files go into the manifest's "gtfs" object (name, size, sha256 per
file, and the official feed's feed_version, which must be the index's
feed). The manifest keeps format 1: the integration reads only the fields it
always did.

Whether to deploy: not when the published manifest names the same content
digest (index_digest.py), manifest format and schema, and the same "gtfs"
object (the zips' sizes and digests). One that cannot be read (no site yet,
Pages not enabled, a network error) never stops a deploy: publishing the
same content again is harmless.

Usage: mav_pages_site.py --index mav-index.json.gz --stats mav-build.json
         --commit SHA --out SITE_DIR [--gtfs-zip ZIP --pfaedle-dir DIR]
         [--live-url URL | --live-file PATH] [--github-output FILE]
Prints deploy=true|false (and appends it to --github-output). Exit 1, with
nothing written, for an index or statistics that do not hold up.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
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
GTFS_NAME = "mav-gtfs.zip"
OVERLAY_NAME = "mav-pfaedle-overlay.zip"
ATTRIBUTION_NAME = "ATTRIBUTION.txt"
# pfaedle's tables the overlay carries, in the order they are zipped.
OVERLAY_FILES = ("shapes.txt", "stop_times.txt", "trips.txt")
# A fixed timestamp and mode per member: the zip depends on the content only.
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
ZIP_LEVEL = 9
MAX_MANIFEST = 64 * 1024
LIVE_TIMEOUT = 20
# MÁV's feed_version: the feed's date, then an id ("20261030.M2ChangeID").
# Nothing else gets through: the value goes into a workflow output.
FEED_RE = re.compile(r"\d{8}(?!\d)[A-Za-z0-9._:+-]{0,64}")


class SiteError(Exception):
    """The index or its statistics do not make a site."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gtfs_feed_version(path: Path) -> str:
    """feed_info.txt's feed_version in the MÁV zip at path."""
    try:
        with zipfile.ZipFile(path) as z:
            info = next((i for i in z.infolist()
                         if not i.is_dir() and Path(i.filename).name.lower() == "feed_info.txt"), None)
            if info is None:
                raise SiteError(f"{path.name} has no feed_info.txt")
            text = z.read(info).decode("utf-8-sig")
    except (OSError, zipfile.BadZipFile) as err:
        raise SiteError(f"{path} is not a GTFS zip: {err}") from err
    row = next(csv.DictReader(io.StringIO(text)), None)
    version = ((row or {}).get("feed_version") or "").strip()
    if not FEED_RE.fullmatch(version):
        raise SiteError(f"{path.name} names no dated feed_version: {version!r}")
    return version


def write_overlay(pfaedle_dir: Path, out: Path) -> list[str]:
    """Zip pfaedle's OVERLAY_FILES from pfaedle_dir into out, deterministically:
    sorted names, fixed timestamps and modes, deflate at a fixed level."""
    present = {p.name.lower(): p for p in pfaedle_dir.iterdir() if p.is_file()} if pfaedle_dir.is_dir() else {}
    missing = [n for n in OVERLAY_FILES if n not in present]
    if missing:
        raise SiteError(f"pfaedle output {pfaedle_dir} lacks " + ", ".join(missing))
    names = sorted(OVERLAY_FILES)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_LEVEL) as z:
        for name in names:
            src = present[name]
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.file_size = src.stat().st_size
            with src.open("rb") as fh, z.open(info, "w") as dst:
                shutil.copyfileobj(fh, dst, 1 << 20)
    return names


def gtfs_manifest(gtfs_zip: Path, overlay: Path, files: list[str], feed: str) -> dict:
    """The manifest's "gtfs" object; the official feed must be the index's."""
    version = gtfs_feed_version(gtfs_zip)
    if version != feed:
        raise SiteError(f"the MÁV zip is of feed {version!r}, the index of {feed!r}")
    return {
        "feed_version": version,
        "official": {"name": GTFS_NAME, "size": gtfs_zip.stat().st_size, "sha256": _sha256(gtfs_zip)},
        "overlay": {"name": OVERLAY_NAME, "size": overlay.stat().st_size, "sha256": _sha256(overlay),
                    "files": files},
        "attribution": ATTRIBUTION_NAME,
    }


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
    return live is not None and all(live.get(k) == new[k] for k in ("format", "schema", "digest")) \
        and live.get("gtfs") == new.get("gtfs")


def attribution(man: dict) -> str:
    gtfs = man.get("gtfs")
    lines = [
        "Hungarian transport: daily MÁV timetable files",
        "https://github.com/bator/hungarian-transport",
        "",
        f"Feed: {man['feed']}, built {man['built_at']} from commit {man['commit']}.",
        "",
    ]
    if gtfs:
        lines += [
            f"{GTFS_NAME}",
            "  MÁV's official GTFS feed, byte for byte as downloaded for this build",
            "  (MÁV-START / MÁV Csoport, https://www.mavcsoport.hu/gtfs). Licence: CC0 1.0.",
            "",
            f"{OVERLAY_NAME}",
            "  " + ", ".join(gtfs["overlay"]["files"]) + " of the same feed as written by pfaedle",
            "  (https://github.com/ad-freiburg/pfaedle), which matched every train to the",
            "  railway lines of OpenStreetMap. Use them in place of the official feed's",
            "  tables of the same names. The shapes are derived from OpenStreetMap data:",
            "  © OpenStreetMap contributors, Open Database License (ODbL) 1.0,",
            "  https://www.openstreetmap.org/copyright",
            "",
        ]
    lines += [
        f"{INDEX_NAME}, {MANIFEST_NAME}",
        "  The integration's train route index and its manifest. The routes are",
        "  derived from OpenStreetMap data: © OpenStreetMap contributors (ODbL 1.0).",
        "",
    ]
    return "\n".join(lines)


def index_html(man: dict) -> str:
    def row(name: str, what: str) -> str:
        return f'<li><a href="{name}">{name}</a> — {html.escape(what)}</li>'

    gtfs = man.get("gtfs")
    items = [row(MANIFEST_NAME, "manifest: feed, sizes, sha256 digests"),
             row(INDEX_NAME, f"the integration's train route index ({man['size']} bytes)")]
    if gtfs:
        items += [row(GTFS_NAME, f"MÁV's official GTFS as downloaded, CC0 ({gtfs['official']['size']} bytes)"),
                  row(OVERLAY_NAME, "pfaedle's " + ", ".join(gtfs["overlay"]["files"])
                      + f" of the same feed ({gtfs['overlay']['size']} bytes)")]
    items.append(row(ATTRIBUTION_NAME, "sources and licences"))
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Daily MÁV timetable</title>\n</head>\n<body>\n"
        "<h1>Daily MÁV timetable</h1>\n"
        f"<p>Feed <code>{html.escape(man['feed'])}</code>, built {html.escape(man['built_at'])} by "
        "<a href=\"https://github.com/bator/hungarian-transport\">hungarian-transport</a>.</p>\n"
        "<ul>\n" + "\n".join(items) + "\n</ul>\n"
        "<p>MÁV GTFS: CC0 1.0. Shapes and routes: © <a href=\"https://www.openstreetmap.org/copyright\">"
        "OpenStreetMap contributors</a> (ODbL 1.0).</p>\n</body>\n</html>\n"
    )


def write_site(index: Path, man: dict, out: Path, gtfs_zip: Path | None = None,
               overlay: Path | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(index, out / INDEX_NAME)
    if gtfs_zip is not None and overlay is not None:
        shutil.copyfile(gtfs_zip, out / GTFS_NAME)
        shutil.copyfile(overlay, out / OVERLAY_NAME)
    (out / ATTRIBUTION_NAME).write_text(attribution(man), encoding="utf-8")
    (out / "index.html").write_text(index_html(man), encoding="utf-8")
    (out / MANIFEST_NAME).write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--stats", type=Path, required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--gtfs-zip", type=Path, help="the MÁV zip the index was built from")
    ap.add_argument("--pfaedle-dir", type=Path, help="pfaedle's output folder of the same build")
    live_src = ap.add_mutually_exclusive_group()
    live_src.add_argument("--live-url")
    live_src.add_argument("--live-file", type=Path)
    ap.add_argument("--github-output", type=Path)
    args = ap.parse_args(argv)
    if (args.gtfs_zip is None) != (args.pfaedle_dir is None):
        ap.error("--gtfs-zip and --pfaedle-dir go together")
    with tempfile.TemporaryDirectory() as tmp:
        overlay = Path(tmp) / OVERLAY_NAME if args.gtfs_zip is not None else None
        try:
            stats = json.loads(args.stats.read_text(encoding="utf-8"))
            if not isinstance(stats, dict):
                raise SiteError(f"{args.stats} is not a statistics object")
            man = manifest(args.index, stats, args.commit)
            if overlay is not None:
                files = write_overlay(args.pfaedle_dir, overlay)
                man["gtfs"] = gtfs_manifest(args.gtfs_zip, overlay, files, man["feed"])
        except (OSError, ValueError, SiteError) as err:
            print(f"no site: {err}", file=sys.stderr)
            return 1
        return _finish(args, man, overlay)


def _finish(args: argparse.Namespace, man: dict, overlay: Path | None) -> int:
    if args.live_file is not None:
        live, why = (parse_live(args.live_file.read_bytes()) if args.live_file.is_file() else (None, "no file"))
    elif args.live_url:
        live, why = fetch_live(args.live_url)
    else:
        live, why = None, "not asked"
    deploy = not same_content(man, live)
    write_site(args.index, man, args.out, args.gtfs_zip, overlay)
    audit = man["audit"]
    print(f"feed {man['feed']}, {man['size']} bytes, sha256 {man['sha256'][:12]}, digest {man['digest'][:12]}, "
          f"audit {audit['trains_ok']}/{audit['trains']} trains, {audit['trips_ok']}/{audit['trips']} trips")
    gtfs = man.get("gtfs")
    if gtfs:
        print(f"GTFS {gtfs['official']['size']} bytes, sha256 {gtfs['official']['sha256'][:12]}; overlay "
              f"{gtfs['overlay']['size']} bytes, sha256 {gtfs['overlay']['sha256'][:12]}")
    if live is None:
        print(f"published manifest: none ({why}); deploying")
    elif deploy:
        print(f"published manifest: feed {live.get('feed')!r}, digest {str(live.get('digest'))[:12]}"
              f"{'' if live.get('digest') != man['digest'] else ', other GTFS files'}; deploying")
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
