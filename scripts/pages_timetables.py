#!/usr/bin/env python3
"""The Volán and city indexes on GitHub Pages (mav-index-daily.yml).

The daily Pages workflow builds the MÁV index every day and the Volán and
city indexes once a week, and every Pages deploy replaces the whole site.
This script decides when the weekly indexes are due, fetches the published
ones as the builders' baseline, packs what a run built, and assembles the
site from this run's new parts and the files it carries over from the live
site. The integration reads timetables.json
(custom_components/hungarian_transport/weekly_timetables.py):

  timetables.json             format, schema, built_at, commit, and per
                              index (volan, city) its url, size, sha256,
                              feed, built_at, trips, stops (city: operators),
                              release_feed (the feed of the latest stable
                              release's index of that name when it was
                              built, when known) and shapes (dir, n, files:
                              size and sha256 of every shard), or null
                              without shapes
  timetables.json.sig         ed25519 signature of timetables.json
                              (scripts/sign_manifest.py, the site job)
  volan-index.json.gz, city-index.json.gz
                              the indexes byte for byte as
                              build_volan_index.py / build_city_index.py
                              wrote them
  volan-shapes-<hash>/NN.json.gz, city-shapes-<hash>/NN.json.gz
                              the shapes shards each index names in its head

The published timetables.json is used (its entries carried, its indexes
taken as a baseline or as built) only when its timetables.json.sig verifies
over its exact bytes against the pinned public key (Source.timetables,
sign_manifest.py); one that does not is taken as no manifest at all, so a
site job never signs again entries this workflow did not sign.

Subcommands (every --live-url is the site's https base URL; --live-dir a
local folder in its place, for tests and the PR check). --schema is the
index schema of the code that builds the indexes (the workflow builds them
with the latest stable release's code, and passes that release's
gtfs_compact.INDEX_SCHEMA), not this checkout's:

  due      (--live-url URL | --live-dir DIR) --schema N [--force true|false]
           [--release-url URL | --release-file ZIP] [--release-out FILE]
           [--now ISO] [--github-output FILE]
           Prints due=true|false and indexes=NAME,... (the due ones). Both
           are due when the published timetables.json is missing or does
           not read, names another format or a schema other than N, and
           with --force true; else each index on its own, when it is not
           listed, was built DUE_AFTER ago or longer (or names no build
           time), or names another release_feed than the feed of the
           release's index of its name (the latest stable release's
           frontend zip, of which only the zip's directory and the heads
           of its indexes are read; a release that cannot be read leaves
           this rule out with a warning). --release-out: those feeds as
           JSON, {"indexes": {name: feed}}, for part.
  baseline (--live-url URL | --live-dir DIR) [--release-url URL |
           --release-file ZIP] --out DIR
           Puts each published index and its shapes, verified against the
           published manifest, into DIR; an index that is not published or
           does not verify comes from the release's frontend zip instead
           (the latest stable release), when it has one. Never fails for a
           missing baseline: the builders then compare with nothing.
  part     --index INDEX.json.gz --out DIR --schema N [--baseline OLD.json.gz]
           [--release-feeds FILE] [--now ISO]
           Checks an index this run built (schema N, a feed, no feed dated
           more than MAX_FEED_AHEAD after now, every shapes shard), copies
           it and its shapes folder into DIR and records its manifest entry
           and N in DIR/part.json (built_at now; release_feed the feed
           --release-feeds, due's --release-out, names for it). Exit 1 for
           an index that does not hold up. With --baseline, a notice says
           when the content (index_digest.py) is the baseline's.
  assemble --out SITE --commit SHA (--live-url URL | --live-dir DIR)
           [--mav-part DIR] [--timetables-part DIR] [--schema N]
           [--max-bytes N] [--now ISO] [--settle SECONDS] [--github-output FILE]
           Writes the whole site: the MÁV files from --mav-part (this run's
           mav_pages_site.py output), else every file the published
           mav-index.json names, carried; each index of --timetables-part,
           else the published one, carried with its published manifest entry
           as it is; timetables.json (of the part's schema, else the
           published one's, else --schema), and ATTRIBUTION.txt and
           index.html for the whole site. Prints deploy=true|false: true
           when the MÁV part or at least one index is new.
           A published Volán or city index that cannot be carried (a file
           missing, or not its size and sha256 also when fetched again
           --settle seconds later), or is of another schema than the new
           one, is left out with a warning. Exit 1, with nothing written,
           when a MÁV file the published mav-index.json lists cannot be
           carried (every install's daily MÁV check reads it), a published
           manifest is there but does not read, or the site is over the
           size limit. A published manifest that is not there (HTTP 404)
           has nothing to carry. A MÁV file that does not match (Pages may
           serve a deploy's files a moment apart) is fetched again once,
           --settle seconds later.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mav_pages_site as mav_site  # noqa: E402
import sign_manifest  # noqa: E402
# INDEX_SCHEMA: only assemble's last resort for a site without any index
# (the schema of the code that builds the indexes is passed as --schema).
from gtfs_compact import INDEX_SCHEMA, UA, shapes_stem  # noqa: E402
from index_digest import digest  # noqa: E402
# The integration's own reading of an index head and a feed's date (on
# sys.path since gtfs_compact put the package there).
from served_index import HEAD_BYTES, feed_date, head_feed  # noqa: E402

MANIFEST_FORMAT = 1
MANIFEST_NAME = "timetables.json"
SIG_NAME = sign_manifest.SIG_NAME
MAX_SIG = 1024
PART_NAME = "part.json"
# Index name -> its file, in the manifest's order.
INDEXES = {"volan": "volan-index.json.gz", "city": "city-index.json.gz"}
# A week less the spread of GitHub's late cron starts: an index built on one
# day's run is due on the same weekday a week later, whichever of that
# day's runs (04:45, 14:20 UTC, a dispatch) comes first, even when it
# started hours earlier than the one that built it.
DUE_AFTER = timedelta(days=6, hours=12)
MAX_MANIFEST = 64 * 1024
# The integration's caps (weekly_timetables.py): an index, a shard, and an
# index with all of its shards.
MAX_INDEX = 48 * 1024 * 1024
MAX_SHARD = 8 * 1024 * 1024
MAX_INDEX_TOTAL = 160 * 1024 * 1024
MAX_SHARDS = 256
# The MÁV files a carry fetches, each at most this big (the site is about
# 35 MB, mav_pages_site.MAX_SITE_BYTES the whole of it).
MAX_MAV_FILE = 300 * 1024 * 1024
MAX_RELEASE_ZIP = 200 * 1024 * 1024
# The release zip's directory and its indexes' heads are read by Range
# requests of this size (release_feeds), at most this many of them: a few
# requests of a few hundred kilobytes, not the whole zip, twice a day.
RANGE_CHUNK = 256 * 1024
MAX_RANGE_REQUESTS = 64
# An index whose feed (a city index: any operator's) names a date further
# ahead is not published (part); mav_pages_site.py holds the MÁV index to
# the same limit.
MAX_FEED_AHEAD = mav_site.MAX_FEED_AHEAD
FETCH_TIMEOUT = 120
SETTLE_S = 30.0
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
SHARD_RE = re.compile(r"\d{2,3}\.json\.gz")


class SiteError(Exception):
    """The site cannot be made, or an index does not hold up."""


class FetchError(SiteError):
    """A live file could not be read (network, HTTP error, too large)."""


class MismatchError(SiteError):
    """A fetched file is not the size or sha256 its manifest names: maybe
    a deploy that Pages serves only partly yet, so worth one more try."""


class NoRanges(FetchError):
    """The server answered a Range request with something else than the
    range: the whole file, say."""


class _NotHttps(urllib.error.URLError):
    """A redirect to another scheme than https."""


class _HttpsRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to another https URL. urllib's own handler
    follows one to plain http as well, and the URL of the last answer
    (geturl) does not tell whether a hop on the way was http: an
    unauthenticated answer there could send the request on to any https
    host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme != "https":
            fp.close()
            raise _NotHttps("redirected away from https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(req: urllib.request.Request, timeout: float = FETCH_TIMEOUT):
    """urlopen for the live site and the release: https only, every redirect
    included."""
    return urllib.request.build_opener(_HttpsRedirect()).open(req, timeout=timeout)


def utc_text(when: datetime) -> str:
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> datetime | None:
    """An ISO time with an offset (a manifest's built_at), else None."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def file_digest(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            size += len(chunk)
            h.update(chunk)
    return size, h.hexdigest()


# ---- the live site ---------------------------------------------------------

class Source:
    """The published site: its https base URL, or a local folder standing in
    for it (tests, the PR check). Names are site paths without a leading
    slash ("city-shapes-0123456789/00.json.gz")."""

    def __init__(self, url: str | None = None, folder: Path | None = None) -> None:
        if (url is None) == (folder is None):
            raise ValueError("a live URL or a live folder")
        if url is not None and not url.startswith("https://"):
            raise ValueError(f"not an https URL: {url}")
        self.url = url.rstrip("/") + "/" if url is not None else None
        self.folder = folder

    def describe(self, name: str) -> str:
        return f"{self.url}{name}" if self.url is not None else str(self.folder / name)

    def fetch(self, name: str, dest: Path, cap: int) -> tuple[int, str] | None:
        """Copy the site's file name to dest, at most cap bytes; its (size,
        sha256), or None when the site has no such file (HTTP 404). Raises
        FetchError for anything else."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.folder is not None:
            src = self.folder / name
            if not src.is_file():
                return None
            if src.stat().st_size > cap:
                raise FetchError(f"{name} is larger than {cap} bytes")
            shutil.copyfile(src, dest)
            return file_digest(dest)
        url = self.url + urllib.parse.quote(name)
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "User-Agent": UA})
        try:
            with _urlopen(req) as res:
                if not res.geturl().startswith("https://"):
                    raise FetchError(f"{name}: redirected away from https")
                h = hashlib.sha256()
                size = 0
                with dest.open("wb") as fh:
                    for chunk in iter(lambda: res.read(1 << 20), b""):
                        size += len(chunk)
                        if size > cap:
                            raise FetchError(f"{name} is larger than {cap} bytes")
                        h.update(chunk)
                        fh.write(chunk)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise FetchError(f"{name}: HTTP {err.code}") from None
        except _NotHttps:
            raise FetchError(f"{name}: redirected away from https") from None
        # http.client.HTTPException: a body cut short or a garbled answer.
        except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
            raise FetchError(f"{name}: {type(err).__name__}: {err}") from None
        return size, h.hexdigest()

    def manifest(self, name: str) -> tuple[dict | None, bytes | None, str]:
        """The site's JSON manifest name: (it, its bytes, "ok"); (None, None,
        "missing") for a 404; FetchError when it is there but does not read."""
        with tempfile.TemporaryDirectory() as tmp:
            got = self.fetch(name, Path(tmp) / "m.json", MAX_MANIFEST)
            if got is None:
                return None, None, "missing"
            body = (Path(tmp) / "m.json").read_bytes()
        try:
            data = json.loads(body)
        except ValueError as err:
            raise FetchError(f"{name} is not JSON: {err}") from None
        if not isinstance(data, dict):
            raise FetchError(f"{name} is not a JSON object")
        return data, body, "ok"

    def timetables(self, settle: float = -1) -> tuple[dict | None, str]:
        """The published timetables.json, trusted only when its
        timetables.json.sig verifies over its exact bytes against the
        pinned public key (sign_manifest.PUBLIC_KEY): (it, "ok"); (None,
        "missing") for a 404; (None, "unsigned: ...") for one without a
        signature that verifies (the first run after signing came in has
        none yet). Every run signs the manifest it deploys anew, with its
        entries as they are: an unsigned one, whoever put it there, is so
        treated as no manifest at all, and nothing of it is carried, used
        as a baseline or taken as built. A signature that is there but
        does not verify is read again once, with the manifest, settle
        seconds later (Pages may serve a deploy's files a moment apart);
        settle < 0: never. FetchError for a manifest or signature that is
        there but does not read."""
        why = ""
        for attempt in (1, 2):
            data, body, state = self.manifest(MANIFEST_NAME)
            if data is None:
                return None, state
            with tempfile.TemporaryDirectory() as tmp:
                got = self.fetch(SIG_NAME, Path(tmp) / "sig", MAX_SIG)
                raw = (Path(tmp) / "sig").read_bytes() if got is not None else None
            if raw is None:
                return None, f"unsigned: no {SIG_NAME}"
            if sign_manifest.verify_bytes(body, sign_manifest.decode_sig(raw)):
                return data, "ok"
            why = f"unsigned: {SIG_NAME} does not verify against the pinned public key"
            if attempt == 2 or settle < 0:
                break
            print(f"{why}; reading both again in {settle:g} s")
            time.sleep(settle)
        return None, why


def fetch_verified(source: Source, name: str, dest: Path, size: object, sha256: object, cap: int) -> None:
    """Fetch name to dest and check it is size bytes with that sha256."""
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= cap \
            or not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
        raise SiteError(f"the published manifest names no usable size and sha256 for {name}")
    got = source.fetch(name, dest, cap)
    if got is None:
        raise MismatchError(f"{name} is listed in the published manifest but not on the site (HTTP 404)")
    if got != (size, sha256):
        raise MismatchError(f"{name} is {got[0]} bytes, sha256 {got[1][:12]}; the published manifest names"
                            f" {size} bytes, sha256 {sha256[:12]}")


# ---- an index and its manifest entry ----------------------------------------

def read_index(path: Path) -> dict:
    """The index at path: one gzip member (as the integration requires)
    holding a JSON object."""
    raw = path.read_bytes()
    if len(raw) > MAX_INDEX:
        raise SiteError(f"{path.name} is {len(raw)} bytes, over {MAX_INDEX}")
    try:
        d = zlib.decompressobj(wbits=31)
        body = d.decompress(raw) + d.flush()
        if d.unused_data or not d.eof:
            raise SiteError(f"{path.name} is not a single gzip member")
        data = json.loads(body)
    except (zlib.error, ValueError) as err:
        raise SiteError(f"{path.name} is not a gzip JSON index: {err}") from err
    if not isinstance(data, dict):
        raise SiteError(f"{path.name} is not an index")
    return data


def _shapes_dir_ok(dir_name: object, url: str) -> bool:
    return isinstance(dir_name, str) and re.fullmatch(
        re.escape(shapes_stem(Path(url))) + r"-shapes-[A-Za-z0-9_-]{1,64}", dir_name) is not None


def shard_names(n: int) -> list[str]:
    return [f"{k:02d}.json.gz" for k in range(n)]


def far_feed(name: str, feed: str, now: datetime) -> str:
    """Why an index's feed names a date more than MAX_FEED_AHEAD after now
    (a city index: any operator's feed, "op:version;op:version"), as the
    integration reads the date (served_index.feed_date); "" when none does."""
    versions = [v for _op, sep, v in (p.partition(":") for p in feed.split(";")) if sep] if name == "city" \
        else [feed]
    limit = now.astimezone(timezone.utc).date() + MAX_FEED_AHEAD
    for version in versions:
        day = feed_date(version)
        if day is not None and day > limit:
            return (f"feed {version[:40]!r} is dated {day.isoformat()}, more than {MAX_FEED_AHEAD.days} days"
                    " ahead")
    return ""


def build_entry(index: Path, built_at: str, schema: int, now: datetime | None = None) -> dict:
    """The manifest entry of an index this run built (its shapes folder next
    to it), refusing one not of schema (the index format of the code that
    built it, which the release of that code reads) and one whose feed is
    dated more than MAX_FEED_AHEAD after now (far_feed; default built_at)."""
    name = next((k for k, v in INDEXES.items() if v == index.name), None)
    if name is None:
        raise SiteError(f"{index.name} is none of " + ", ".join(INDEXES.values()))
    data = read_index(index)
    if data.get("v") != schema:
        raise SiteError(f"{index.name} has schema {data.get('v')!r}, not {schema}")
    feed = data.get("feed")
    # The feed goes into the manifest and the log, never a workflow command.
    if not isinstance(feed, str) or not feed or not feed.isprintable():
        raise SiteError(f"{index.name} names no feed")
    far = far_feed(name, feed, now or parse_time(built_at) or datetime.now(timezone.utc))
    if far:
        raise SiteError(f"{index.name}: {far}; the published one stays")
    trips, stops = data.get("t"), data.get("s")
    if not isinstance(trips, list) or not trips or not isinstance(stops, list):
        raise SiteError(f"{index.name} has no trips")
    size, sha = file_digest(index)
    entry: dict = {"url": index.name, "size": size, "sha256": sha, "feed": feed, "built_at": built_at,
                   "trips": len(trips), "stops": len(stops)}
    if isinstance(data.get("o"), list):
        entry["operators"] = len(data["o"])
    sh = data.get("sh")
    if sh is None:
        entry["shapes"] = None
        return entry
    if not isinstance(sh, dict) or not _shapes_dir_ok(sh.get("dir"), index.name) \
            or not isinstance(sh.get("n"), int) or not 1 <= sh["n"] <= MAX_SHARDS:
        raise SiteError(f"{index.name} names no usable shapes folder: {sh!r}")
    folder = index.parent / sh["dir"]
    files = {}
    total = size
    for shard in shard_names(sh["n"]):
        path = folder / shard
        if not path.is_file():
            raise SiteError(f"{index.name}: shapes shard {sh['dir']}/{shard} is missing")
        s_size, s_sha = file_digest(path)
        if s_size > MAX_SHARD:
            raise SiteError(f"{sh['dir']}/{shard} is {s_size} bytes, over {MAX_SHARD}")
        total += s_size
        files[shard] = {"size": s_size, "sha256": s_sha}
    if total > MAX_INDEX_TOTAL:
        raise SiteError(f"{index.name} and its shapes are {total} bytes, over {MAX_INDEX_TOTAL}")
    entry["shapes"] = {"dir": sh["dir"], "n": sh["n"], "files": files}
    return entry


def check_entry(name: str, entry: object) -> dict:
    """A manifest entry (published or from a part) whose files can be
    fetched and checked: the expected url, a size and sha256 for the index
    and every shard."""
    if not isinstance(entry, dict) or entry.get("url") != INDEXES[name]:
        raise SiteError(f"the {name} entry names no {INDEXES[name]}")
    shapes = entry.get("shapes")
    if shapes is None:
        return entry
    if not isinstance(shapes, dict) or not _shapes_dir_ok(shapes.get("dir"), INDEXES[name]) \
            or not isinstance(shapes.get("n"), int) or not 1 <= shapes["n"] <= MAX_SHARDS \
            or not isinstance(shapes.get("files"), dict) \
            or sorted(shapes["files"]) != shard_names(shapes["n"]) \
            or not all(isinstance(v, dict) for v in shapes["files"].values()):
        raise SiteError(f"the {name} entry names no usable shapes: {shapes!r}"[:300])
    return entry


def entry_files(entry: dict) -> list[tuple[str, object, object, int]]:
    """(site path, size, sha256, cap) of an entry's index and shards."""
    out = [(entry["url"], entry.get("size"), entry.get("sha256"), MAX_INDEX)]
    shapes = entry.get("shapes")
    if shapes:
        for shard in shard_names(shapes["n"]):
            meta = shapes["files"][shard]
            out.append((f"{shapes['dir']}/{shard}", meta.get("size"), meta.get("sha256"), MAX_SHARD))
    return out


def check_head(path: Path, entry: dict, schema: int) -> None:
    """The index at path is the one entry describes: its schema, feed and
    shapes folder."""
    data = read_index(path)
    shapes = entry.get("shapes")
    sh = {"dir": shapes["dir"], "n": shapes["n"]} if shapes else None
    if data.get("v") != schema or data.get("feed") != entry.get("feed") or data.get("sh") != sh:
        raise SiteError(f"{path.name} is not the index its manifest entry describes"
                        f" (schema {data.get('v')!r}, shapes {data.get('sh')!r})")


def copy_entry(source: Source, entry: dict, out: Path, schema: int) -> None:
    """Fetch an entry's index and shards from source into out, each checked
    against the entry, then the index's head."""
    for name, size, sha, cap in entry_files(entry):
        fetch_verified(source, name, out / name, size, sha, cap)
    check_head(out / entry["url"], entry, schema)


# ---- due --------------------------------------------------------------------

def due(live: dict | None, why: str, force: bool, now: datetime, schema: int,
        release: dict[str, str] | None = None) -> tuple[list[str], str]:
    """The indexes to build in this run (in INDEXES order), and why. One
    index that is due does not make the other one due: a city index whose
    guard refuses it for days must not rebuild and redeploy the Volán index
    on every run. What concerns the whole manifest (none, another format or
    schema, the dispatch input) makes both due.

    release: name -> the feed of the latest stable release's index of that
    name (release_feeds), None when it could not be read. An index is due
    when its entry's release_feed is not that feed (or it has none): it was
    built against another release's index, and installs of that release
    refuse it (weekly_timetables.py). Its builders and guards are the new
    release's too. An index the release lacks has nothing to compare."""
    if force:
        return list(INDEXES), "asked for by the workflow_dispatch input timetables"
    if live is None:
        return list(INDEXES), f"no published {MANIFEST_NAME} ({why})"
    if live.get("format") != MANIFEST_FORMAT:
        return list(INDEXES), f"the published {MANIFEST_NAME} has another format"
    if live.get("schema") != schema:
        return list(INDEXES), f"the published indexes are not of schema {schema}"
    indexes = live.get("indexes") if isinstance(live.get("indexes"), dict) else {}
    names, reasons = [], []
    for name in INDEXES:
        entry = indexes.get(name)
        if not isinstance(entry, dict):
            names.append(name)
            reasons.append(f"no published {name} index")
            continue
        built = parse_time(entry.get("built_at"))
        if built is None:
            names.append(name)
            reasons.append(f"the published {name} index names no build time")
            continue
        age = now - built
        if age >= DUE_AFTER:
            names.append(name)
            reasons.append(f"the published {name} index was built {int(age.total_seconds() // 3600)} h ago")
            continue
        if release is not None and name in release and entry.get("release_feed") != release[name]:
            names.append(name)
            reasons.append(f"the published {name} index was built against "
                           + ("another release's" if entry.get("release_feed") else "no release's")
                           + f" {name} index, not the latest stable release's")
    if not names:
        return [], (f"both published indexes were built less than {DUE_AFTER} ago"
                    + ("" if release is None else ", against the latest stable release's"))
    return names, "; ".join(reasons)


# ---- baseline ---------------------------------------------------------------

def baseline_from_live(source: Source, out: Path) -> set[str]:
    """The published indexes that verify, copied with their shapes into
    out; their names."""
    try:
        live, state = source.timetables()
    except FetchError as err:
        print(f"published {MANIFEST_NAME}: {err}")
        return set()
    if live is None:
        print(f"published {MANIFEST_NAME}: none ({state})")
        return set()
    schema = live.get("schema")
    indexes = live.get("indexes") if isinstance(live.get("indexes"), dict) else {}
    got = set()
    for name in INDEXES:
        if name not in indexes:
            continue
        with tempfile.TemporaryDirectory(dir=out) as tmp:
            try:
                entry = check_entry(name, indexes[name])
                copy_entry(source, entry, Path(tmp), schema)
            except SiteError as err:
                print(f"published {name} index: {err}; not a baseline")
                continue
            for path_name, *_ in entry_files(entry):
                dest = out / path_name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(Path(tmp) / path_name), dest)
        got.add(name)
        print(f"baseline {name}: the published index, feed {entry.get('feed')}, built {entry.get('built_at')}")
    return got


def fetch_release(url: str, dest: Path) -> str:
    """Download the release's frontend zip at url (https, redirects to https
    only) to dest, at most MAX_RELEASE_ZIP bytes; "ok", or why not (dest
    then removed)."""
    if not url.startswith("https://"):
        return f"not an https URL: {url}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    why = "ok"
    try:
        with _urlopen(req) as res:
            if not res.geturl().startswith("https://"):
                why = "redirected away from https"
            else:
                got = 0
                with dest.open("wb") as fh:
                    for chunk in iter(lambda: res.read(1 << 20), b""):
                        got += len(chunk)
                        if got > MAX_RELEASE_ZIP:
                            why = "larger than a release zip"
                            break
                        fh.write(chunk)
    except urllib.error.HTTPError as err:
        why = f"HTTP {err.code}"
    except _NotHttps:
        why = "redirected away from https"
    except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
        why = f"{type(err).__name__}: {err}"
    if why != "ok":
        dest.unlink(missing_ok=True)
    return why


# ---- the latest stable release's indexes ------------------------------------

class _RangeFile(io.RawIOBase):
    """The file at an https URL, read by Range requests (at most
    MAX_RANGE_REQUESTS), for zipfile to read the release zip's directory
    and the heads of its indexes without the rest. GitHub sends a release
    download on to a signed URL of its storage: after the first answer,
    that URL is asked directly. NoRanges when the server does not answer
    with the range asked for."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.pos = 0
        self.requests = 0
        self.size = 1
        _, self.size = self._get(0, 0)
        if self.size > MAX_RELEASE_ZIP:
            raise FetchError(f"the release zip is {self.size} bytes, larger than a release zip")

    def _get(self, start: int, end: int) -> tuple[bytes, int]:
        """Bytes start..end (inclusive) and the file's size."""
        self.requests += 1
        if self.requests > MAX_RANGE_REQUESTS:
            raise FetchError(f"more than {MAX_RANGE_REQUESTS} range requests")
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": UA,
                                                        "Accept-Encoding": "identity"})
        try:
            with _urlopen(req) as res:
                if not res.geturl().startswith("https://"):
                    raise FetchError("redirected away from https")
                if res.status != 206:
                    raise NoRanges(f"HTTP {res.status} to a Range request")
                got = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", res.headers.get("Content-Range") or "")
                if got is None or int(got.group(1)) != start or not start <= int(got.group(2)) <= end:
                    raise NoRanges(f"Content-Range {str(res.headers.get('Content-Range'))[:60]!r} for bytes"
                                   f" {start}-{end}")
                want = int(got.group(2)) - start + 1
                body = res.read(want + 1)
                self.url = res.geturl()
        except urllib.error.HTTPError as err:
            raise FetchError(f"HTTP {err.code}") from None
        except _NotHttps:
            raise FetchError("redirected away from https") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
            raise FetchError(f"{type(err).__name__}: {err}") from None
        if len(body) != want:
            raise FetchError(f"{len(body)} bytes in answer to a range of {want}")
        return body, int(got.group(3))

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.pos, io.SEEK_END: self.size}[whence]
        if base + offset < 0:
            raise OSError("seek before the start")
        self.pos = base + offset
        return self.pos

    def readinto(self, buf) -> int:
        n = min(len(buf), self.size - self.pos)
        if n <= 0:
            return 0
        body, _size = self._get(self.pos, self.pos + n - 1)
        buf[:n] = body
        self.pos += n
        return n


def _member_feed(z: zipfile.ZipFile, info: zipfile.ZipInfo) -> str | None:
    """The feed in the head of the index info names in z: its gzip unpacked
    only up to the integration's HEAD_BYTES (served_index.read_feed)."""
    unzip = zlib.decompressobj(wbits=31)
    head = b""
    pending = b""
    with z.open(info) as fh:
        while len(head) < HEAD_BYTES and not unzip.eof and head_feed(head) is None:
            if not pending:
                pending = fh.read(1 << 15)
                if not pending:
                    break
            try:
                head += unzip.decompress(pending, HEAD_BYTES - len(head))
            except zlib.error:
                return None
            pending = unzip.unconsumed_tail
    feed = head_feed(head)
    return feed if isinstance(feed, str) and feed and feed.isprintable() else None


def zip_feeds(z: zipfile.ZipFile) -> dict[str, str]:
    """Name -> the feed of each index the release zip z has."""
    members = {i.filename: i for i in z.infolist()}
    feeds = {}
    for name, file in INDEXES.items():
        info = members.get(file)
        if info is None or info.file_size > MAX_INDEX:
            continue
        feed = _member_feed(z, info)
        if feed is not None:
            feeds[name] = feed
    return feeds


def release_feeds(url: str | None = None, path: Path | None = None) -> dict[str, str]:
    """Name -> feed of each index in the latest stable release's frontend
    zip: the local zip at path, else the one at url, read by Range requests
    (only its directory and the indexes' heads), the whole zip only when
    the server does not answer ranges. Raises SiteError when the zip cannot
    be read."""
    if path is not None:
        try:
            with zipfile.ZipFile(path) as z:
                return zip_feeds(z)
        except (OSError, EOFError, zipfile.BadZipFile) as err:
            raise SiteError(f"release zip: {err}") from None
    if not url or not url.startswith("https://"):
        raise SiteError(f"release zip: not an https URL: {url}")
    try:
        raw = _RangeFile(url)
        with zipfile.ZipFile(io.BufferedReader(raw, RANGE_CHUNK)) as z:
            feeds = zip_feeds(z)
        print(f"release zip: its directory and index heads read in {raw.requests} range requests")
        return feeds
    except NoRanges as err:
        print(f"release zip: {err}; downloading the whole zip")
    except (FetchError, OSError, EOFError, zipfile.BadZipFile) as err:
        raise SiteError(f"release zip: {err}") from None
    with tempfile.TemporaryDirectory() as tmp:
        zpath = Path(tmp) / "frontend.zip"
        why = fetch_release(url, zpath)
        if why != "ok":
            raise SiteError(f"release zip: {why}")
        return release_feeds(path=zpath)


def baseline_from_release(zpath: Path, names: list[str], out: Path) -> set[str]:
    """Each index of names in the release zip at zpath, with the shapes
    folder its head names when the zip has it, into out; their names."""
    got = set()
    try:
        z = zipfile.ZipFile(zpath)
    except (OSError, zipfile.BadZipFile) as err:
        print(f"release zip: {err}")
        return got
    with z:
        members = {i.filename: i for i in z.infolist()}
        for name in names:
            info = members.get(INDEXES[name])
            if info is None or info.file_size > MAX_INDEX:
                print(f"baseline {name}: not in the release zip")
                continue
            dest = out / INDEXES[name]
            with z.open(info) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            try:
                sh = read_index(dest).get("sh")
            except SiteError as err:
                print(f"baseline {name}: the release's index does not read ({err})")
                dest.unlink()
                continue
            if isinstance(sh, dict) and _shapes_dir_ok(sh.get("dir"), INDEXES[name]) \
                    and isinstance(sh.get("n"), int) and 1 <= sh["n"] <= MAX_SHARDS:
                for shard in shard_names(sh["n"]):
                    member = members.get(f"{sh['dir']}/{shard}")
                    if member is None or member.file_size > MAX_SHARD:
                        continue
                    target = out / sh["dir"] / shard
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
            got.add(name)
            print(f"baseline {name}: the latest release's index")
    return got


def cmd_baseline(args: argparse.Namespace, source: Source) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    got = baseline_from_live(source, args.out)
    rest = [n for n in INDEXES if n not in got]
    if rest and (args.release_url or args.release_file is not None):
        with tempfile.TemporaryDirectory() as tmp:
            zpath = args.release_file
            if zpath is None:
                zpath = Path(tmp) / "frontend.zip"
                why = fetch_release(args.release_url, zpath)
                if why != "ok":
                    print(f"release zip: {why}")
                    zpath = None
            if zpath is not None:
                got |= baseline_from_release(zpath, rest, args.out)
    for name in INDEXES:
        if name not in got:
            print(f"::warning title=No {name} baseline::Neither GitHub Pages nor the latest release has a {name}"
                  " index that reads; the builder compares the new one with nothing.")
    return 0


# ---- part -------------------------------------------------------------------

def read_release_feeds(path: Path | None) -> dict[str, str]:
    """due's --release-out file: name -> feed; {} without one that reads."""
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.is_file() else {}
    except (OSError, ValueError):
        return {}
    indexes = data.get("indexes") if isinstance(data, dict) else None
    if not isinstance(indexes, dict):
        return {}
    return {n: f for n, f in indexes.items()
            if n in INDEXES and isinstance(f, str) and f and f.isprintable() and len(f) <= 4096}


def cmd_part(args: argparse.Namespace, now: datetime) -> int:
    try:
        entry = build_entry(args.index, utc_text(now), args.schema, now)
    except (OSError, SiteError) as err:
        print(f"::warning title=Index not published::{args.index.name}: {err}")
        return 1
    name = next(k for k, v in INDEXES.items() if v == args.index.name)
    release = read_release_feeds(args.release_feeds).get(name)
    if release is not None:
        entry["release_feed"] = release
    args.out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.index, args.out / entry["url"])
    if entry["shapes"]:
        folder = args.out / entry["shapes"]["dir"]
        folder.mkdir(exist_ok=True)
        for shard in shard_names(entry["shapes"]["n"]):
            shutil.copyfile(args.index.parent / entry["shapes"]["dir"] / shard, folder / shard)
    part_file = args.out / PART_NAME
    part = json.loads(part_file.read_text(encoding="utf-8")) if part_file.is_file() else {}
    if part.get("schema", args.schema) != args.schema:
        print(f"::warning title=Index not published::{args.index.name}: the part holds indexes of schema"
              f" {part.get('schema')!r}, not {args.schema}")
        return 1
    part["schema"] = args.schema
    part.setdefault("indexes", {})[name] = entry
    part_file.write_text(json.dumps(part, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{name}: {entry['size']} bytes, sha256 {entry['sha256'][:12]}, {entry['trips']} trips,"
          f" {entry['stops']} stops, shapes {entry['shapes']['dir'] if entry['shapes'] else 'none'},"
          f" release_feed {'unknown' if release is None else release[:80]}")
    if args.baseline is not None and args.baseline.is_file():
        try:
            same = digest(args.index) == digest(args.baseline)
        except (OSError, ValueError, KeyError, TypeError) as err:
            print(f"{name}: no comparison with the baseline ({type(err).__name__})")
        else:
            if same:
                print(f"::notice title={name} index unchanged::The rebuilt {name} index has the content of the"
                      " published one; it is published again so that its built_at says it is current.")
    return 0


# ---- assemble ---------------------------------------------------------------

def mav_files(man: dict) -> list[tuple[str, object, object]]:
    """(name, size, sha256) of every file a MÁV manifest names, the index's
    name and the GTFS files' names the ones mav_pages_site.py writes."""
    if man.get("url") != mav_site.INDEX_NAME:
        raise SiteError(f"{mav_site.MANIFEST_NAME} names no {mav_site.INDEX_NAME}")
    out = [(mav_site.INDEX_NAME, man.get("size"), man.get("sha256"))]
    gtfs = man.get("gtfs")
    if gtfs is not None:
        if not isinstance(gtfs, dict):
            raise SiteError(f"{mav_site.MANIFEST_NAME} has an unreadable gtfs object")
        for key, want in (("official", mav_site.GTFS_NAME), ("overlay", mav_site.OVERLAY_NAME)):
            meta = gtfs.get(key)
            if not isinstance(meta, dict) or meta.get("name") != want:
                raise SiteError(f"{mav_site.MANIFEST_NAME} names no {want}")
            out.append((want, meta.get("size"), meta.get("sha256")))
    return out


def mav_from_part(part: Path, out: Path) -> dict:
    """This run's MÁV files from mav_pages_site.py's site folder, checked
    against its manifest; the manifest."""
    body = (part / mav_site.MANIFEST_NAME).read_bytes()
    man = json.loads(body)
    if not isinstance(man, dict):
        raise SiteError(f"the MÁV part's {mav_site.MANIFEST_NAME} is not a manifest")
    for name, size, sha in mav_files(man):
        src = part / name
        if not src.is_file() or file_digest(src) != (size, sha):
            raise SiteError(f"the MÁV part's {name} is not the file its manifest names")
        shutil.copyfile(src, out / name)
    (out / mav_site.MANIFEST_NAME).write_bytes(body)
    return man


def mav_from_live(source: Source, out: Path) -> dict | None:
    """Every MÁV file the published mav-index.json names, checked against it,
    and the manifest itself as published; None when there is none.

    A file that cannot be carried raises (assemble refuses the site), unlike
    a Volán or city index: a site without the MÁV files breaks every
    install's daily MÁV check (mav_daily.py), and the MÁV build itself is
    tried again on the next run (twice a day), so a refused deploy costs
    less than one without them."""
    man, body, _state = source.manifest(mav_site.MANIFEST_NAME)
    if man is None:
        return None
    for name, size, sha in mav_files(man):
        fetch_verified(source, name, out / name, size, sha, MAX_MAV_FILE)
    (out / mav_site.MANIFEST_NAME).write_bytes(body)
    return man


def part_names(part: Path | None) -> list[str]:
    """The indexes a timetables part holds (by its part.json), [] without
    one."""
    try:
        data = json.loads((part / PART_NAME).read_text(encoding="utf-8")) if part is not None else None
    except (OSError, ValueError):
        return []
    indexes = data.get("indexes") if isinstance(data, dict) else None
    return [n for n in INDEXES if isinstance(indexes, dict) and n in indexes]


def part_schema(part: Path | None) -> int | None:
    """The schema a timetables part's indexes are of (part.json), None
    without a part that names one."""
    try:
        data = json.loads((part / PART_NAME).read_text(encoding="utf-8")) if part is not None else None
    except (OSError, ValueError):
        return None
    schema = data.get("schema") if isinstance(data, dict) else None
    return schema if isinstance(schema, int) and not isinstance(schema, bool) else None


def timetables_from_part(part: Path, out: Path, schema: int) -> dict[str, dict]:
    """This run's indexes from the timetables part, each checked against
    the entry the part recorded for it and its head against schema; name ->
    entry."""
    part_file = part / PART_NAME
    if not part_file.is_file():
        return {}
    data = json.loads(part_file.read_text(encoding="utf-8"))
    indexes = data.get("indexes") if isinstance(data, dict) else None
    if not isinstance(indexes, dict):
        raise SiteError(f"the timetables part's {PART_NAME} lists no indexes")
    new = {}
    for name in INDEXES:
        if name not in indexes:
            continue
        entry = check_entry(name, indexes[name])
        for path_name, size, sha, _cap in entry_files(entry):
            src = part / path_name
            if not src.is_file() or file_digest(src) != (size, sha):
                raise SiteError(f"the timetables part's {path_name} is not the file {PART_NAME} names")
            (out / path_name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out / path_name)
        check_head(out / entry["url"], entry, schema)
        new[name] = entry
    return new


def live_timetables(source: Source, settle: float = -1) -> dict | None:
    """The published timetables.json; None for a 404, and for one whose
    signature does not verify (Source.timetables, settle), with a warning:
    nothing is carried from it, so this site's manifest, signed again,
    never vouches for entries this workflow did not sign. Raises FetchError
    for one that is there but does not read: assemble then refuses the
    site. Which indexes such a manifest lists cannot be told, and the cause
    is most likely the network: dropping both indexes on that would leave
    every install without a published index (they keep their own, but each
    next run rebuilds both), while a refusal costs only this run's deploy."""
    live, state = source.timetables(settle)
    if state.startswith("unsigned"):
        print(f"::warning title=Published timetables.json not trusted::{state}; no Volán or city index is carried"
              " over from it (the indexes built in this run are published, the next run rebuilds the others).")
    if live is not None and not isinstance(live.get("indexes"), dict):
        raise SiteError(f"the published {MANIFEST_NAME} lists no indexes")
    return live


def _remove_entry_files(out: Path, entry: object) -> None:
    """What copy_entry may have left of a carry that failed."""
    if not isinstance(entry, dict):
        return
    if entry.get("url") in INDEXES.values():
        (out / entry["url"]).unlink(missing_ok=True)
    shapes = entry.get("shapes")
    if isinstance(shapes, dict) and _shapes_dir_ok(shapes.get("dir"), str(entry.get("url"))):
        shutil.rmtree(out / shapes["dir"], ignore_errors=True)


def timetables_from_live(source: Source, out: Path, skip: set[str], schema: int | None,
                         settle: float = SETTLE_S) -> tuple[dict[str, dict], int | None]:
    """The published indexes not in skip, checked against the published
    timetables.json, with their entries as published; (name -> entry, the
    schema they are of). schema None: whatever the published manifest names
    (no index was built in this run), else an index of another schema is
    left out with a warning.

    A published index that cannot be carried (a file missing, or not the
    size and sha256 the manifest names, also when fetched again settle
    seconds later with the manifest read again) is left out with a warning,
    not the whole site: the integration then finds it not published and
    keeps its own, and the next run's due check finds it missing and builds
    it again. The MÁV files are never left out that way (mav_from_live)."""
    if skip >= set(INDEXES):
        return {}, schema
    live = live_timetables(source, settle)
    if live is None:
        return {}, schema
    if schema is None:
        published = live.get("schema")
        schema = published if isinstance(published, int) and not isinstance(published, bool) else None
    carried = {}
    reread = None
    for name in INDEXES:
        if name in skip or name not in live["indexes"]:
            continue
        if schema is None or live.get("schema") != schema:
            print(f"::warning title={name} index dropped::The published {name} index is of schema"
                  f" {live.get('schema')!r}, this site's of {schema!r}, and none was built in this run;"
                  " the site goes without it until the next build.")
            continue
        entry = live["indexes"][name]
        problem = None
        for attempt in (1, 2):
            try:
                entry = check_entry(name, entry)
                copy_entry(source, entry, out, schema)
                problem = None
                break
            except SiteError as err:
                problem = err
                _remove_entry_files(out, entry)
                if attempt == 2 or not isinstance(err, (MismatchError, FetchError)) or settle < 0:
                    break
                print(f"{err}; fetching the {name} index again in {settle:g} s")
                time.sleep(settle)
                if reread is None:
                    reread = live_timetables(source, settle) or {"indexes": {}}
                entry = reread["indexes"].get(name)
                if reread.get("schema") != schema or entry is None:
                    problem = SiteError(f"the published {name} index changed while it was fetched")
                    break
        if problem is not None:
            print(f"::warning title={name} index dropped::The published {name} index could not be carried over"
                  f" ({str(problem)[:300]}); the site goes without it until the next build.")
            continue
        carried[name] = entry
    return carried, schema


def city_sources() -> list[str]:
    """ATTRIBUTION.txt's lines on the city feeds this checkout builds from."""
    import build_city_index  # noqa: PLC0415 - only for the attribution

    lines = []
    for feed in build_city_index.CITY_FEEDS:
        host = urllib.parse.urlparse(feed["url"]).hostname or ""
        via = f"{host}, or the address in the {feed['url_env']} secret" if feed.get("url_env") else host
        lines.append(f"  - {feed['name']} ({feed['id']}): {via}")
    return lines


def timetables_attribution(indexes: dict[str, dict]) -> list[str]:
    lines = []
    volan, city = indexes.get("volan"), indexes.get("city")
    if volan:
        lines += [
            "volan-index.json.gz" + (f", {volan['shapes']['dir']}/" if volan.get("shapes") else ""),
            "  The integration's Volán coach timetable: every intercity route of the Volánbusz",
            f"  GTFS feed {volan.get('feed')}, built {volan.get('built_at')}.",
            "  Source: Volánbusz's GTFS as MÁV publishes it,",
            "  https://opendata.utas.hu/public-gtfs/volanbusz_gtfs.zip (or the same zip at",
            "  https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip).",
            "",
        ]
    if city:
        lines += [
            "city-index.json.gz" + (f", {city['shapes']['dir']}/" if city.get("shapes") else ""),
            f"  The integration's city and local timetables, built {city.get('built_at')}, from the",
            "  publishers' GTFS feeds:",
            *city_sources(),
            "  - Volánbusz's local lines: the Volán feed above.",
            "  Where a publisher has no public zip address, the feed comes from the menetbrand",
            "  proxy (https://gtfs.menetbrand.com/letoltes/). The feeds on gy-mate.hu are",
            "  Máté Gyöngyösi's (https://github.com/gy-mate/gtfs-feeds), licensed CC BY 4.0",
            "  (https://creativecommons.org/licenses/by/4.0/): credit them when you publish",
            "  anything built on them. The other feeds are the operators' own, under their terms.",
            "",
        ]
    if indexes:
        lines += [
            f"{MANIFEST_NAME}, {MANIFEST_NAME}.sig",
            "  The manifest of these indexes: per index its feed, size, sha256 and shapes shards;",
            "  and its ed25519 signature (base64), which the integration checks before it reads it.",
            "",
        ]
    return lines


def site_attribution(mav: dict | None, indexes: dict[str, dict]) -> str:
    lines = ["Hungarian transport: timetable files on GitHub Pages", mav_site.REPO_URL, ""]
    if mav is not None:
        lines += ["== Daily MÁV index ==", "", *mav_site.attribution_lines(mav)]
    if indexes:
        lines += ["== Weekly Volán and city indexes ==", "", *timetables_attribution(indexes)]
    return "\n".join(lines)


def site_html(mav: dict | None, indexes: dict[str, dict]) -> str:
    body = "<h1>Hungarian transport timetables</h1>\n"
    if mav is not None:
        body += ("<h2>Daily MÁV timetable</h2>\n" + mav_site.html_intro(mav)
                 + "<ul>\n" + "\n".join(mav_site.html_items(mav)) + "\n</ul>\n" + mav_site.HTML_LICENCES)
    if indexes:
        items = [mav_site.html_row(MANIFEST_NAME, "manifest: feeds, sizes, sha256 digests, shapes shards")]
        for name, label in (("volan", "Volán coach timetable"), ("city", "city and local timetables")):
            entry = indexes.get(name)
            if entry:
                items.append(mav_site.html_row(entry["url"], f"the integration's {label}, feed {entry.get('feed')},"
                                               f" built {entry.get('built_at')} ({entry.get('size')} bytes)"))
        body += ("<h2>Weekly Volán and city timetables</h2>\n<ul>\n" + "\n".join(items) + "\n</ul>\n"
                 "<p>Máté Gyöngyösi's feeds (gy-mate.hu): CC BY 4.0. The others: their operators' terms.</p>\n")
    body += (f"<p><a href=\"{mav_site.ATTRIBUTION_NAME}\">{mav_site.ATTRIBUTION_NAME}</a>: the sources and"
             " licences of every file.</p>\n")
    return mav_site.html_page("Hungarian transport timetables", body)


def site_size(out: Path) -> tuple[int, dict[str, int]]:
    sizes = {}
    for path in sorted(out.rglob("*")):
        if path.is_file():
            sizes[path.relative_to(out).as_posix()] = path.stat().st_size
    return sum(sizes.values()), sizes


def assemble(args: argparse.Namespace, source: Source, now: datetime) -> tuple[dict | None, dict, dict]:
    """Write the site into args.out; (MÁV manifest, new indexes, carried
    indexes)."""
    out = args.out
    if args.mav_part is not None:
        mav = mav_from_part(args.mav_part, out)
    else:
        mav = mav_from_live(source, out)
    # The schema of the indexes this run built (the stable release's
    # builders), else the published one's; a site without any index names
    # --schema, else this checkout's.
    schema = None
    if part_names(args.timetables_part):
        schema = part_schema(args.timetables_part)
        if schema is None:
            raise SiteError(f"the timetables part's {PART_NAME} names no schema")
    new = timetables_from_part(args.timetables_part, out, schema) if schema is not None else {}
    carried, schema = timetables_from_live(source, out, set(new), schema, args.settle)
    if schema is None:
        schema = args.schema if args.schema is not None else INDEX_SCHEMA
    indexes = {name: (new.get(name) or carried[name]) for name in INDEXES if name in new or name in carried}
    manifest = {"format": MANIFEST_FORMAT, "schema": schema, "built_at": utc_text(now),
                "commit": args.commit, "indexes": indexes}
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if (out / MANIFEST_NAME).stat().st_size > MAX_MANIFEST:
        raise SiteError(f"{MANIFEST_NAME} is over {MAX_MANIFEST} bytes")
    (out / mav_site.ATTRIBUTION_NAME).write_text(site_attribution(mav, indexes), encoding="utf-8")
    (out / "index.html").write_text(site_html(mav, indexes), encoding="utf-8")
    total, sizes = site_size(out)
    print(f"site: {total} bytes (limit {args.max_bytes}) in {len(sizes)} files: "
          + ", ".join(f"{n} {b}" for n, b in sizes.items() if "/" not in n))
    if total > args.max_bytes:
        raise SiteError(f"the site would be {total} bytes, over the {args.max_bytes} byte limit")
    return mav, new, carried


def cmd_assemble(args: argparse.Namespace, source: Source, now: datetime) -> int:
    if not COMMIT_RE.fullmatch(args.commit or ""):
        print(f"no site: not a commit sha: {args.commit!r}", file=sys.stderr)
        return 2
    if args.mav_part is None and not part_names(args.timetables_part):
        print("::notice title=Nothing to publish::Neither a new MÁV index nor a new Volán or city index in this"
              " run; GitHub Pages keeps the site it has.")
        _output(args, False)
        return 0
    for attempt in (1, 2):
        if args.out.exists():
            shutil.rmtree(args.out)
        args.out.mkdir(parents=True)
        try:
            mav, new, carried = assemble(args, source, now)
            break
        except MismatchError as err:
            if attempt == 2 or args.settle < 0:
                return _refuse(args, err)
            print(f"{err}; fetching again in {args.settle:g} s")
            time.sleep(args.settle)
        # KeyError, TypeError: a published manifest without a field the
        # combined ATTRIBUTION.txt or index.html names.
        except (OSError, ValueError, KeyError, TypeError, SiteError) as err:
            return _refuse(args, err)
    mav_how = "none" if mav is None else ("new" if args.mav_part is not None else "carried")
    print(f"MÁV: {mav_how}" + (f", feed {mav.get('feed')}" if mav is not None else ""))
    for name in INDEXES:
        entry = new.get(name) or carried.get(name)
        how = "new" if name in new else "carried" if name in carried else "none"
        print(f"{name}: {how}" + (f", feed {entry.get('feed')}, built {entry.get('built_at')}" if entry else ""))
    if not new.keys() | carried.keys():
        print("::warning title=No Volán or city index on GitHub Pages::Neither index was built in this run, and"
              " GitHub Pages has none to carry over.")
    _output(args, True)
    return 0


def _refuse(args: argparse.Namespace, err: Exception) -> int:
    shutil.rmtree(args.out, ignore_errors=True)
    # The reason is the script's own text, without the published values.
    print(f"::error title=GitHub Pages site not assembled::{str(err)[:300]}; nothing is deployed, the site keeps"
          " its files.")
    _output(args, False)
    return 1


def _output(args: argparse.Namespace, deploy: bool) -> None:
    print(f"deploy={'true' if deploy else 'false'}")
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"deploy={'true' if deploy else 'false'}\n")


# ---- command line -----------------------------------------------------------

def _source(args: argparse.Namespace) -> Source:
    return Source(url=args.live_url) if args.live_url else Source(folder=args.live_dir)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def live(p: argparse.ArgumentParser) -> None:
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--live-url", help="the Pages site's https base URL")
        g.add_argument("--live-dir", type=Path, help="a local folder in place of the site")

    def when(p: argparse.ArgumentParser) -> None:
        p.add_argument("--now", help="the time to take as now (ISO, with an offset); tests")

    p = sub.add_parser("due", help="whether the Volán and city indexes are due")
    live(p)
    when(p)
    p.add_argument("--schema", type=int, required=True,
                   help="the index schema of the code that builds them (its gtfs_compact.INDEX_SCHEMA)")
    p.add_argument("--force", default="false", choices=("true", "false", ""))
    rel = p.add_mutually_exclusive_group()
    rel.add_argument("--release-url", help="the latest stable release's frontend zip (https)")
    rel.add_argument("--release-file", type=Path, help="as --release-url, a local zip")
    p.add_argument("--release-out", type=Path, help="write the release's index feeds here (JSON), for part")
    p.add_argument("--github-output", type=Path)

    p = sub.add_parser("baseline", help="the published (or released) indexes for the builders' guards")
    live(p)
    rel = p.add_mutually_exclusive_group()
    rel.add_argument("--release-url")
    rel.add_argument("--release-file", type=Path)
    p.add_argument("--out", type=Path, required=True)

    p = sub.add_parser("part", help="pack an index this run built")
    when(p)
    p.add_argument("--index", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--baseline", type=Path)
    p.add_argument("--schema", type=int, required=True, help="the index schema of the code that built it")
    p.add_argument("--release-feeds", type=Path, help="due's --release-out: the release_feed to record")

    p = sub.add_parser("assemble", help="the whole Pages site")
    live(p)
    when(p)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--mav-part", type=Path)
    p.add_argument("--timetables-part", type=Path)
    p.add_argument("--max-bytes", type=int, default=mav_site.MAX_SITE_BYTES)
    p.add_argument("--schema", type=int, help="timetables.json's schema when it lists no index (default:"
                   " this checkout's)")
    p.add_argument("--settle", type=float, default=SETTLE_S,
                   help="seconds before fetching a mismatched file again (negative: never)")
    p.add_argument("--github-output", type=Path)

    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if getattr(args, "now", None):
        now = parse_time(args.now)
        if now is None:
            ap.error("--now needs an ISO time with an offset")
    try:
        source = _source(args) if hasattr(args, "live_url") else None
    except ValueError as err:
        ap.error(str(err))
    if args.cmd == "due":
        try:
            live_man, state = source.timetables()
            why = "HTTP 404" if state == "missing" else state
        except FetchError as err:
            live_man, why = None, str(err)
        release = None
        if args.release_url or args.release_file is not None:
            try:
                release = release_feeds(args.release_url, args.release_file)
            except SiteError as err:
                print(f"::warning title=Release index not read::{str(err)[:300]}; the indexes are due by"
                      " their age only in this run.")
            else:
                for name in INDEXES:
                    print(f"latest stable release, {name} index: "
                          + (f"feed {release[name][:120]}" if name in release else "none"))
        if args.release_out is not None:
            args.release_out.write_text(json.dumps({"indexes": release or {}}, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
        names, reason = due(live_man, why, args.force == "true", now, args.schema, release)
        print(f"{'due: ' + ', '.join(names) if names else 'not due'}: {reason}")
        lines = f"due={'true' if names else 'false'}\nindexes={','.join(names)}\n"
        print(lines, end="")
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as fh:
                fh.write(lines)
        return 0
    if args.cmd == "baseline":
        return cmd_baseline(args, source)
    if args.cmd == "part":
        return cmd_part(args, now)
    return cmd_assemble(args, source, now)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
