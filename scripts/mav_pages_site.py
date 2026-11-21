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
  ATTRIBUTION.txt, index.html  what the files are, their licences (the
                           daily workflow's site job writes the whole
                           site's over them: pages_timetables.py)

The GTFS files go into the manifest's "gtfs" object (name, size, sha256 per
file, and the official feed's feed_version, which must be the index's
feed). The manifest keeps format 1: the integration reads only the fields it
always did.

Whether to deploy: not when the published manifest names the same content
digest (index_digest.py), manifest format and schema, and the same "gtfs"
object apart from the overlay's bytes (same_content: MÁV's zip by size and
sha256, the overlay by name and tables), was built less than REFRESH_AFTER
ago, and its signature (mav-index.json.sig next to it, the URL with .sig;
with --live-file, the file next to it) verifies against the pinned public
key (sign_manifest.py): one without that is deployed again from this
build, signed, as the site job carries no unsigned MÁV manifest. pfaedle does not write the same tables twice for the
same feed and OSM extract, so the overlay's size and sha256 differ on every
run and would deploy each one; a new overlay ships with the next deploy
that a changed index, MÁV zip or refresh makes. Older, the same content is deployed again for its new built_at: the
daily verdict and the integration read built_at as the last build that went
through, and a feed MÁV leaves alone for days is not a stale index. One that
cannot be read (no site yet, Pages not enabled, a network error) never stops
a deploy: publishing the same content again is harmless.

A site larger than MAX_SITE_BYTES (GitHub Pages allows 1 GB) is refused:
the sizes of its files are printed either way.

Usage: mav_pages_site.py --index mav-index.json.gz --stats mav-build.json
         --commit SHA --out SITE_DIR [--gtfs-zip ZIP --pfaedle-dir DIR]
         [--live-url URL | --live-file PATH] [--github-output FILE]
         [--max-bytes N] [--allow-shrink]
         [--baseline-url URL | --baseline-file PATH] [--sig-settle SECONDS]
A published mav-index.json.sig that does not verify, or is not there (HTTP
404) next to a manifest that is, is read again with its manifest once,
--sig-settle seconds later (SIG_SETTLE_S: Pages' cache).
Prints deploy=true|false (and appends it to --github-output). Exit 1, with
nothing written, for an index or statistics that do not hold up, a feed
whose feed_version does not start with a real date (YYYYMMDD, as the
integration's served_index.feed_date reads it) or with one more than
MAX_FEED_AHEAD after the build, a site over the size limit,
or an index with fewer than half the trains and lines of the published one
(mav_shrink_guard.py; --allow-shrink or MAV_ALLOW_SHRINK=1 accepts it).
When the published manifest does not read, or its signature does not
verify (its counts are then its author's, not this workflow's), the
shrink guard compares with
the MÁV index of --baseline-url (the latest stable release's frontend zip,
fetched only then) or --baseline-file (such a zip, or a mav-index.json.gz);
without one either, it warns and compares with nothing.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import http.client
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent / "custom_components" / "hungarian_transport"))

import gtfs_feed  # noqa: E402
import mav_index  # noqa: E402
import mav_shrink_guard  # noqa: E402
import sign_manifest  # noqa: E402
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
# Half of GitHub Pages' 1 GB site limit; the site is about 35 MB.
MAX_SITE_BYTES = 500 * 1024 * 1024
MAX_MANIFEST = 64 * 1024
MAX_SIG = 1024
# The same content is deployed again once the published manifest is this old,
# so its built_at says the daily run went through: mav_daily_verdict.py fails
# a run that published nothing on a built_at STALE_AFTER (2 days) old, and
# the integration looks again later on one older than mav_daily.STALE_MANIFEST
# (20 h), as on one of an earlier day's feed.
# Under a day, so one of a day's runs (the 04:45 and 14:20 UTC crons, a
# dispatch) refreshes it every day even when MÁV published nothing new; a
# run a few hours after another with the same content (a rerun, the
# afternoon catch-up, a dispatch after the morning build) deploys nothing.
REFRESH_AFTER = timedelta(hours=12)
# A feed date further ahead than this is a publisher's mistake, not a
# timetable (a feed_end_date written as the version, a typo in the year):
# installs order feeds by that date, so the index would beat every correct
# one after it until the date is reached. The weekly Volán and city indexes
# are held to it too (pages_timetables.py part, build_city_index.py).
MAX_FEED_AHEAD = timedelta(days=400)
LIVE_TIMEOUT = 20
# GitHub Pages serves every file through its CDN (Fastly, Cache-Control:
# max-age=600) and caches each path on its own, keyed on the path alone:
# neither a query string nor a request's no-cache reaches past it (checked
# 2026-11-19: a never-seen ?query answered X-Cache: HIT with the cached
# ETag; the integration found the same, mav_daily.PAGES_MAX_AGE). For up to
# PAGES_MAX_AGE after a deploy a manifest and its .sig can so be of two
# deploys, either way round, and the signature does not verify; after the
# first deploy with a .sig, a cache may still answer the 404 it took for
# the .sig before. A reader that finds one so (a .sig that does not verify,
# or a 404 for it next to a manifest that is there) reads both again once,
# SIG_SETTLE_S after its first read of the site, when every copy cached
# before the deploy has expired
# (the minute on top: a cache that took its copy a moment late, the
# clocks): pages_timetables.py (Source.signed, and a file that does not
# match its manifest), mav_daily_verdict.py and this script (_finish).
PAGES_MAX_AGE = 600
SIG_SETTLE_S = PAGES_MAX_AGE + 60
# The shrink guard's fallback: a release's frontend zip (about 15 MB) and
# the MÁV index in it (about 0.25 MB).
BASELINE_TIMEOUT = 120
MAX_BASELINE_ZIP = 200 * 1024 * 1024
MAX_BASELINE_INDEX = 32 * 1024 * 1024
# MÁV's feed_version: the feed's date, then an id ("20261030.M2ChangeID").
# Nothing else gets through: the value goes into a workflow output.
FEED_RE = re.compile(r"\d{8}(?!\d)[A-Za-z0-9._:+-]{0,64}")


def dated_feed(value: object) -> bool:
    """value is a feed_version the integration accepts (mav_daily.check_manifest):
    FEED_RE, and its first eight digits a real date (not 20261399)."""
    if not isinstance(value, str) or not FEED_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value[:8], "%Y%m%d")
    except ValueError:
        return False
    return True


class SiteError(Exception):
    """The index or its statistics do not make a site."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gtfs_feed_version(path: Path) -> str:
    """feed_info.txt's feed_version in the MÁV zip at path, read as the
    index builder reads it (gtfs_feed: a cp1250 publisher name, spaces
    around the header's names)."""
    try:
        with zipfile.ZipFile(path) as z:
            try:
                gtfs_feed.member(z, "feed_info.txt")
            except FileNotFoundError:
                raise SiteError(f"{path.name} has no feed_info.txt") from None
            version = gtfs_feed.feed_version(z).strip()
    except (OSError, zipfile.BadZipFile, csv.Error) as err:
        raise SiteError(f"{path} is not a GTFS zip: {err}") from err
    if not dated_feed(version):
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
            # z.open(ZipInfo) deflates at the ZipInfo's own level, not the
            # ZipFile's compresslevel (None: zlib's default, 6). Python 3.13
            # names it compress_level; 3.12 and older _compresslevel.
            if hasattr(info, "compress_level"):
                info.compress_level = ZIP_LEVEL
            else:
                info._compresslevel = ZIP_LEVEL
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
    if not dated_feed(feed):
        raise SiteError(f"{index} names no dated feed: {feed!r}")
    when = (built_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    day = datetime.strptime(feed[:8], "%Y%m%d").date()
    if day > when.date() + MAX_FEED_AHEAD:
        raise SiteError(f"{index} is of feed {feed!r}, dated more than {MAX_FEED_AHEAD.days} days after"
                        f" {when.date().isoformat()}")
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


def fetch_bytes(url: str, cap: int = MAX_MANIFEST) -> tuple[bytes | None, str]:
    """The file at url, at most cap bytes, or None and why not."""
    if not url.startswith("https://"):
        return None, f"not an https URL: {url}"
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as res:  # noqa: S310 - https only
            body = res.read(cap + 1)
    except urllib.error.HTTPError as err:
        return None, f"HTTP {err.code}"
    # http.client.HTTPException: a body cut short (IncompleteRead) or a
    # garbled answer (BadStatusLine) is a network error like any other.
    except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
        return None, f"{type(err).__name__}: {err}"
    if len(body) > cap:
        return None, f"larger than {cap} bytes"
    return body, "ok"


def fetch_live(url: str) -> tuple[dict | None, str]:
    """The published manifest, or None and why not."""
    body, why = fetch_bytes(url)
    return parse_live(body) if body is not None else (None, why)


def signature_state(body: bytes | None, sig: bytes | None, sig_why: str = "") -> str:
    """"ok" when sig (the published mav-index.json.sig's bytes) is the
    pinned key's signature over body (mav-index.json's exact bytes), else
    why not."""
    if body is None:
        return "no manifest"
    if sig is None:
        return f"no {MANIFEST_NAME}.sig ({sig_why or 'none'})"
    if not sign_manifest.verify_bytes(body, sign_manifest.decode_sig(sig)):
        return f"{MANIFEST_NAME}.sig does not verify against the pinned public key"
    return "ok"


def parse_live(body: bytes) -> tuple[dict | None, str]:
    try:
        live = json.loads(body)
    except ValueError as err:
        return None, f"not JSON: {err}"
    if not isinstance(live, dict):
        return None, "not a JSON object"
    return live, "ok"


def built_time(man: dict) -> datetime | None:
    """A manifest's built_at, or None when it names no time with an offset."""
    value = man.get("built_at")
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def refresh_due(new: dict, live: dict) -> bool:
    """The published manifest was built REFRESH_AFTER or longer before new, or
    names no build time: deploy the same content again for new's built_at."""
    built, now = built_time(live), built_time(new)
    return built is None or now is None or now - built >= REFRESH_AFTER


def baseline_counts(path: Path) -> tuple[int, int] | None:
    """(trains, lines) of the MÁV index at path, or of the mav-index.json.gz
    in the zip at path (a release's frontend zip); None when there is none
    or it does not read."""
    try:
        if not zipfile.is_zipfile(path):
            return mav_shrink_guard.counts(path)
        with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory() as tmp:
            info = z.getinfo(INDEX_NAME)
            if info.file_size > MAX_BASELINE_INDEX:
                return None
            index = Path(tmp) / INDEX_NAME
            with z.open(info) as src, index.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            return mav_shrink_guard.counts(index)
    except (OSError, KeyError, zipfile.BadZipFile, ValueError):
        return None


def fetch_baseline(url: str) -> tuple[tuple[int, int] | None, str]:
    """baseline_counts of the zip at url (https only, redirects included),
    or None and why not."""
    if not url.startswith("https://"):
        return None, f"not an https URL: {url}"
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "baseline.zip"
        try:
            with urllib.request.urlopen(url, timeout=BASELINE_TIMEOUT) as res:  # noqa: S310 - https only
                if not res.geturl().startswith("https://"):
                    return None, "redirected away from https"
                with dest.open("wb") as fh:
                    got = 0
                    for chunk in iter(lambda: res.read(1 << 20), b""):
                        got += len(chunk)
                        if got > MAX_BASELINE_ZIP:
                            return None, "larger than a release zip"
                        fh.write(chunk)
        except urllib.error.HTTPError as err:
            return None, f"HTTP {err.code}"
        except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
            return None, f"{type(err).__name__}: {err}"
        found = baseline_counts(dest)
        return found, "ok" if found is not None else f"no readable {INDEX_NAME} in it"


def check_shrink(man: dict, live: dict | None, allow: bool = False,
                 baseline: tuple[int, int] | None = None) -> None:
    """SiteError when the index has fewer than half the trains and lines of
    the published one (mav_shrink_guard): a truncated feed that is
    consistent in itself and names a newer feed would replace it. allow
    (--allow-shrink, MAV_ALLOW_SHRINK) accepts an intended reduction.

    live is the published manifest only when its signature verifies (the
    caller passes None for one that does not): its trains and lines are
    whatever its author wrote, and one who can put files on Pages could
    otherwise name a million of each and so refuse every build, freezing
    the site on what they put there. Without a published manifest that
    reads and verifies (Pages down, a network error, no site yet, no or a
    bad signature), the index is compared with baseline, (trains, lines)
    of the latest stable release's MÁV index: an outage must not let a
    truncated feed through. Without that either, a warning says that
    nothing was compared, and the index is published: refusing it instead
    would let anyone who can break the published signature (or an outage
    of both the site and the release download) stop every daily build,
    while the guard is there for a truncated feed of MÁV's, which the next
    run with a reference still catches."""
    if live is not None:
        prev, what = (live.get("trains"), live.get("lines")), "the published index"
    elif baseline is not None:
        prev, what = baseline, "the latest release's index"
    else:
        print("::warning title=MÁV shrink guard skipped::Neither a published manifest whose signature verifies"
              " nor a release's MÁV index could be read; the index was not compared with a previous one.")
        return
    why = mav_shrink_guard.shrink_problem(man["trains"], man["lines"], *prev)
    if not why:
        return
    if allow:
        print(f"::warning title=MÁV index shrank::{why} ({what}); published, the shrink guard is relaxed.")
        return
    raise SiteError(f"refusing to replace {what}: {why}; --allow-shrink or"
                    f" {mav_shrink_guard.ALLOW_ENV}=1 accepts it")


def _gtfs_identity(gtfs: object) -> object:
    """What of a manifest's "gtfs" object same_content compares: all of it
    but the overlay's size and sha256, which change with every pfaedle run
    (its tables are not reproducible), while the official zip's identity and
    the overlay's name and tables say what the site serves."""
    if not isinstance(gtfs, dict):
        return gtfs
    out = dict(gtfs)
    overlay = out.get("overlay")
    if isinstance(overlay, dict):
        out["overlay"] = {k: v for k, v in overlay.items() if k not in ("size", "sha256")}
    return out


def same_content(new: dict, live: dict | None) -> bool:
    """The published site serves this content already. Not the sha256: the
    gzip header carries the build time, so every build's bytes differ. Nor
    the overlay's bytes (_gtfs_identity)."""
    return live is not None and all(live.get(k) == new[k] for k in ("format", "schema", "digest")) \
        and _gtfs_identity(live.get("gtfs")) == _gtfs_identity(new.get("gtfs"))


ATTRIBUTION_TITLE = "Hungarian transport: daily MÁV timetable files"
REPO_URL = "https://github.com/bator/hungarian-transport"


def attribution_lines(man: dict) -> list[str]:
    """ATTRIBUTION.txt's MÁV part, below its title: the files of manifest
    man and their sources and licences. pages_timetables.py puts it into
    the site's combined ATTRIBUTION.txt as it is."""
    gtfs = man.get("gtfs")
    lines = [
        f"Feed: {man.get('feed')}, built {man.get('built_at')} from commit {man.get('commit')}.",
        "",
    ]
    if gtfs:
        lines += [
            f"{GTFS_NAME}",
            "  MÁV's official GTFS feed, byte for byte as downloaded for this build.",
            "  Source: MÁV-csoport (published by MÁV-START), https://www.mavcsoport.hu/gtfs",
            "  Licence: CC0 1.0, as MÁV-csoport states for its GTFS feed.",
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
    return lines


def attribution(man: dict) -> str:
    return "\n".join([ATTRIBUTION_TITLE, REPO_URL, "", *attribution_lines(man)])


def html_row(name: str, what: str) -> str:
    """A list item of index.html: the file's link and what it is."""
    return f'<li><a href="{name}">{name}</a> — {html.escape(what)}</li>'


def html_page(title: str, body: str) -> str:
    """index.html around body (already escaped HTML)."""
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{html.escape(title)}</title>\n</head>\n<body>\n{body}</body>\n</html>\n"
    )


def html_items(man: dict) -> list[str]:
    """index.html's list items for the MÁV files of manifest man."""
    gtfs = man.get("gtfs")
    items = [html_row(MANIFEST_NAME, "manifest: feed, sizes, sha256 digests"),
             html_row(INDEX_NAME, f"the integration's train route index ({man.get('size')} bytes)")]
    if gtfs:
        items += [html_row(GTFS_NAME, f"MÁV's official GTFS as downloaded, CC0 ({gtfs['official']['size']} bytes)"),
                  html_row(OVERLAY_NAME, "pfaedle's " + ", ".join(gtfs["overlay"]["files"])
                           + f" of the same feed ({gtfs['overlay']['size']} bytes)")]
    return items


def html_intro(man: dict) -> str:
    """index.html's line on the MÁV feed of manifest man."""
    return (f"<p>Feed <code>{html.escape(str(man.get('feed')))}</code>, built {html.escape(str(man.get('built_at')))} by "
            f"<a href=\"{REPO_URL}\">hungarian-transport</a>.</p>\n")


HTML_LICENCES = ("<p>MÁV GTFS: CC0 1.0. Shapes and routes: © <a href=\"https://www.openstreetmap.org/copyright\">"
                 "OpenStreetMap contributors</a> (ODbL 1.0).</p>\n")


def index_html(man: dict) -> str:
    items = [*html_items(man), html_row(ATTRIBUTION_NAME, "sources and licences")]
    return html_page("Daily MÁV timetable", "<h1>Daily MÁV timetable</h1>\n" + html_intro(man)
                     + "<ul>\n" + "\n".join(items) + "\n</ul>\n" + HTML_LICENCES)


def site_files(index: Path, man: dict, gtfs_zip: Path | None = None,
               overlay: Path | None = None) -> dict[str, Path | bytes]:
    """The site's files by name: a file to copy, or the bytes to write."""
    files: dict[str, Path | bytes] = {INDEX_NAME: index}
    if gtfs_zip is not None and overlay is not None:
        files[GTFS_NAME] = gtfs_zip
        files[OVERLAY_NAME] = overlay
    files[ATTRIBUTION_NAME] = attribution(man).encode("utf-8")
    files["index.html"] = index_html(man).encode("utf-8")
    files[MANIFEST_NAME] = (json.dumps(man, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    return files


def site_sizes(files: dict[str, Path | bytes]) -> dict[str, int]:
    return {name: (src.stat().st_size if isinstance(src, Path) else len(src)) for name, src in files.items()}


def check_size(sizes: dict[str, int], limit: int) -> None:
    """SiteError, naming every file's size, for a site over limit bytes."""
    total = sum(sizes.values())
    if total > limit:
        raise SiteError(f"the site would be {total} bytes, over the {limit} byte limit ("
                        + ", ".join(f"{n} {b}" for n, b in sizes.items()) + ")")


def write_site(index: Path, man: dict, out: Path, gtfs_zip: Path | None = None,
               overlay: Path | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, src in site_files(index, man, gtfs_zip, overlay).items():
        if isinstance(src, Path):
            shutil.copyfile(src, out / name)
        else:
            (out / name).write_bytes(src)


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
    ap.add_argument("--max-bytes", type=int, default=MAX_SITE_BYTES,
                    help=f"refuse a larger site (default {MAX_SITE_BYTES})")
    base_src = ap.add_mutually_exclusive_group()
    base_src.add_argument("--baseline-url", help="a release's frontend zip: the shrink guard's comparison"
                          " when the published manifest does not read")
    base_src.add_argument("--baseline-file", type=Path, help="as --baseline-url, a local zip or mav-index.json.gz")
    ap.add_argument("--allow-shrink", action="store_true", default=mav_shrink_guard.allowed(),
                    help="publish an index with under half the trains and lines of the published one"
                         f" (default: {mav_shrink_guard.ALLOW_ENV} set)")
    ap.add_argument("--sig-settle", type=float, default=SIG_SETTLE_S,
                    help="seconds before a published manifest whose signature does not verify, or is not there,"
                         f" is read again with it (default {SIG_SETTLE_S:g}: Pages' cache age and a minute; negative: never)")
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
            sizes = site_sizes(site_files(args.index, man, args.gtfs_zip, overlay))
            print(f"site: {sum(sizes.values())} bytes (limit {args.max_bytes}): "
                  + ", ".join(f"{n} {b}" for n, b in sizes.items()))
            check_size(sizes, args.max_bytes)
        except (OSError, ValueError, SiteError) as err:
            print(f"no site: {err}", file=sys.stderr)
            return 1
        return _finish(args, man, overlay)


def _read_live(args: argparse.Namespace) -> tuple[bytes | None, bytes | None, str, str]:
    """The published manifest's bytes and its .sig's (--live-file: the file
    next to it), with why each is not there."""
    body, sig, why, sig_why = None, None, "not asked", ""
    if args.live_file is not None:
        body, why = (args.live_file.read_bytes(), "ok") if args.live_file.is_file() else (None, "no file")
        sig_file = args.live_file.with_name(args.live_file.name + ".sig")
        # A file that is not there stands for the site's 404.
        sig, sig_why = (sig_file.read_bytes(), "ok") if sig_file.is_file() else (None, "HTTP 404")
    elif args.live_url:
        body, why = fetch_bytes(args.live_url)
        if body is not None:
            sig, sig_why = fetch_bytes(args.live_url + ".sig", MAX_SIG)
    return body, sig, why, sig_why


def _finish(args: argparse.Namespace, man: dict, overlay: Path | None) -> int:
    # The published manifest and its signature (mav-index.json.sig next to
    # it, sign_manifest.py): the same content counts as already published
    # only when that signature verifies. One that does not (none yet: the
    # first run after signing came in; another key; an edit) is deployed
    # again from this build, signed, since the site job never carries an
    # unsigned MÁV manifest (pages_timetables.py mav_from_live). The shrink
    # guard compares with it only when its signature verifies (its counts
    # are its author's), else with the latest stable release's MÁV index.
    # A .sig that is there but does not verify, or that is not there (HTTP
    # 404) next to a manifest that is, is read again with its manifest once,
    # --sig-settle (SIG_SETTLE_S) later: right after a deploy Pages' CDN may
    # pair one deploy's manifest with the other's .sig, or still answer the
    # 404 it cached for the .sig before the first signed deploy, and
    # deciding on that would deploy the same content again for nothing.
    for attempt in (1, 2):
        body, sig, why, sig_why = _read_live(args)
        live, why = parse_live(body) if body is not None else (None, why)
        trust = signature_state(body, sig, sig_why) if live is not None else "no manifest"
        if live is None or (sig is None and sig_why != "HTTP 404") or trust == "ok" or attempt == 2 \
                or args.sig_settle < 0:
            break
        print(f"published manifest: {trust}; reading it and its signature again in {args.sig_settle:g} s"
              f" (GitHub Pages caches each file for up to {PAGES_MAX_AGE} s)")
        time.sleep(args.sig_settle)
    trusted = live if trust == "ok" else None
    baseline = None
    if trusted is None and (args.baseline_url or args.baseline_file is not None):
        if args.baseline_file is not None:
            baseline = baseline_counts(args.baseline_file)
            got = "ok" if baseline is not None else f"no readable {INDEX_NAME}"
        else:
            baseline, got = fetch_baseline(args.baseline_url)
        print(("published manifest: none" if live is None else "published manifest: not trusted")
              + f" ({why if live is None else trust}); release index for the shrink guard: "
              + (f"{baseline[0]} trains and {baseline[1]} lines" if baseline is not None else f"none ({got})"))
    try:
        check_shrink(man, trusted, args.allow_shrink, baseline)
    except SiteError as err:
        print(f"no site: {err}", file=sys.stderr)
        return 1
    same = same_content(man, live) and trust == "ok"
    refresh = same and refresh_due(man, live)
    deploy = not same or refresh
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
    elif trust != "ok" and same_content(man, live):
        print(f"published manifest: the same content, but not trusted ({trust}); deploying it again, signed")
    elif not same:
        print(f"published manifest: feed {live.get('feed')!r}, digest {str(live.get('digest'))[:12]}"
              f"{'' if live.get('digest') != man['digest'] else ', other GTFS files'}; deploying")
    elif refresh:
        # The age, not the published value: it goes into an annotation.
        built = built_time(live)
        age = "no valid build time" if built is None else \
            f"built {int((built_time(man) - built).total_seconds() // 3600)} h ago"
        print(f"::notice title=Daily MÁV index unchanged::Feed {man['feed']} has the content GitHub Pages "
              f"already serves ({age}); deploying it again to refresh built_at.")
    else:
        print(f"::notice title=Daily MÁV index unchanged::Feed {man['feed']} has the content GitHub Pages "
              "already serves; nothing is deployed.")
    print(f"deploy={'true' if deploy else 'false'}")
    # live_built: the published manifest's built_at, its signature verified,
    # for the site job's assemble (pages_timetables.py --mav-built-at): it
    # never carries an older mav-index.json over the one read here.
    built = built_time(live) if trusted is not None else None
    live_built = built.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") \
        if built is not None else ""
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"deploy={'true' if deploy else 'false'}\nfeed={man['feed']}\nlive_built={live_built}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
