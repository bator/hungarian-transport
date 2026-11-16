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
                              feed, built_at, trips, stops (city: operators)
                              and shapes (dir, n, files: size and sha256 of
                              every shard), or null without shapes
  volan-index.json.gz, city-index.json.gz
                              the indexes byte for byte as
                              build_volan_index.py / build_city_index.py
                              wrote them
  volan-shapes-<hash>/NN.json.gz, city-shapes-<hash>/NN.json.gz
                              the shapes shards each index names in its head

Subcommands (every --live-url is the site's https base URL; --live-dir a
local folder in its place, for tests and the PR check). --schema is the
index schema of the code that builds the indexes (the workflow builds them
with the latest stable release's code, and passes that release's
gtfs_compact.INDEX_SCHEMA), not this checkout's:

  due      (--live-url URL | --live-dir DIR) --schema N [--force true|false]
           [--now ISO] [--github-output FILE]
           Prints due=true|false and indexes=NAME,... (the due ones). Both
           are due when the published timetables.json is missing or does
           not read, names another format or a schema other than N, and
           with --force true; else each index on its own, when it is not
           listed or was built DUE_AFTER ago or longer (or names no build
           time).
  baseline (--live-url URL | --live-dir DIR) [--release-url URL |
           --release-file ZIP] --out DIR
           Puts each published index and its shapes, verified against the
           published manifest, into DIR; an index that is not published or
           does not verify comes from the release's frontend zip instead
           (the latest stable release), when it has one. Never fails for a
           missing baseline: the builders then compare with nothing.
  part     --index INDEX.json.gz --out DIR --schema N [--baseline OLD.json.gz]
           [--now ISO]
           Checks an index this run built (schema N, a feed, every shapes
           shard), copies it and its shapes folder into DIR and records its
           manifest entry and N in DIR/part.json (built_at now). Exit 1 for
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
# INDEX_SCHEMA: only assemble's last resort for a site without any index
# (the schema of the code that builds the indexes is passed as --schema).
from gtfs_compact import INDEX_SCHEMA, UA, shapes_stem  # noqa: E402
from index_digest import digest  # noqa: E402

MANIFEST_FORMAT = 1
MANIFEST_NAME = "timetables.json"
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
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as res:  # noqa: S310 - https only
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


def build_entry(index: Path, built_at: str, schema: int) -> dict:
    """The manifest entry of an index this run built (its shapes folder next
    to it), refusing one not of schema (the index format of the code that
    built it, which the release of that code reads)."""
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

def due(live: dict | None, why: str, force: bool, now: datetime, schema: int) -> tuple[list[str], str]:
    """The indexes to build in this run (in INDEXES order), and why. One
    index that is due does not make the other one due: a city index whose
    guard refuses it for days must not rebuild and redeploy the Volán index
    on every run. What concerns the whole manifest (none, another format or
    schema, the dispatch input) makes both due."""
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
    if not names:
        return [], f"both published indexes were built less than {DUE_AFTER} ago"
    return names, "; ".join(reasons)


# ---- baseline ---------------------------------------------------------------

def baseline_from_live(source: Source, out: Path) -> set[str]:
    """The published indexes that verify, copied with their shapes into
    out; their names."""
    try:
        live, _body, _state = source.manifest(MANIFEST_NAME)
    except FetchError as err:
        print(f"published {MANIFEST_NAME}: {err}")
        return set()
    if live is None:
        print(f"published {MANIFEST_NAME}: none")
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
    """Download the release's frontend zip at url (https, redirects
    allowed) to dest; "ok", or why not."""
    if not url.startswith("https://"):
        return f"not an https URL: {url}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as res:  # noqa: S310 - https only
            if not res.geturl().startswith("https://"):
                return "redirected away from https"
            got = 0
            with dest.open("wb") as fh:
                for chunk in iter(lambda: res.read(1 << 20), b""):
                    got += len(chunk)
                    if got > MAX_RELEASE_ZIP:
                        return "larger than a release zip"
                    fh.write(chunk)
    except urllib.error.HTTPError as err:
        return f"HTTP {err.code}"
    except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
        return f"{type(err).__name__}: {err}"
    return "ok"


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

def cmd_part(args: argparse.Namespace, now: datetime) -> int:
    try:
        entry = build_entry(args.index, utc_text(now), args.schema)
    except (OSError, SiteError) as err:
        print(f"::warning title=Index not published::{args.index.name}: {err}")
        return 1
    name = next(k for k, v in INDEXES.items() if v == args.index.name)
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
          f" {entry['stops']} stops, shapes {entry['shapes']['dir'] if entry['shapes'] else 'none'}")
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


def live_timetables(source: Source) -> dict | None:
    """The published timetables.json; None for a 404. Raises FetchError for
    one that is there but does not read: assemble then refuses the site.
    Which indexes such a manifest lists cannot be told, and the cause is
    most likely the network: dropping both indexes on that would leave
    every install without a published index (they keep their own, but each
    next run rebuilds both), while a refusal costs only this run's deploy."""
    live, _body, _state = source.manifest(MANIFEST_NAME)
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
    live = live_timetables(source)
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
                    reread = live_timetables(source) or {"indexes": {}}
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
            f"{MANIFEST_NAME}",
            "  The manifest of these indexes: per index its feed, size, sha256 and shapes shards.",
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
            live_man, _body, state = source.manifest(MANIFEST_NAME)
            why = "HTTP 404" if state == "missing" else "ok"
        except FetchError as err:
            live_man, why = None, str(err)
        names, reason = due(live_man, why, args.force == "true", now, args.schema)
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
