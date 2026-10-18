#!/usr/bin/env python3
"""Build city-index.json.gz: municipal GTFS + Volán local (non-4-digit) routes."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from gtfs_compact import (
    PLATFORM_SPLIT_M,
    IndexGuardError,
    download_feed,
    feed_error,
    feed_urls,
    load_env_files,
    load_parts,
    operator_trips,
    stale_parts,
    write_index,
)

VOLAN_URL = "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"
OUT = Path("city-index.json.gz")
# A feed that the previous index had must not vanish or shrink below this
# share of its trips; the previous index stays in place instead.
MIN_TRIP_RATIO = 0.5
MIN_GUARDED_TRIPS = 50
# Comma-separated feed ids (or "all") that may be missing from a rebuild.
ALLOW_MISSING_ENV = "HUNGARIAN_TRANSPORT_ALLOW_MISSING"

# Debrecen DKV is listed as "Nem publikus" on menetbrand. Skip.
# Weekendbus (Csömör/Pécel) still hosts a zip, but the calendar ended 2025-12-31.
# Feeds that still need a form or login: MÁV, NAP, KEKO.
# Menetbrand /download/<slug> is the public zip when the publisher has no fixed
# filename (Kaposvár), a CAPTCHA (Kiskunhalas), or until a keyed URL is in .env.
# Bahart: official NAP URL via BAHART_GTFS_URL; Transitland / Mobility Database
# as catalog fallback; menetbrand if neither is set.
# "insecure": the publisher still serves plain http. HTTPS is tried first;
# only these feeds may fall back to http (download_feed warns when it does).
CITY_FEEDS = [
    {
        "id": "miskolc",
        "name": "Miskolc — MVK",
        "url": "http://gtfs.cdata.hu/mvkzrt.zip",
        "file": "miskolc.zip",
        "insecure": True,
    },
    {
        "id": "pecs",
        "name": "Pécs — Tüke Busz",
        "url": "http://mobilitas.biokom.hu/gtfs",
        "file": "pecs.zip",
        "insecure": True,
    },
    {
        "id": "szeged",
        "name": "Szeged — SZKT",
        "url": "http://szegedimenetrend.hu/google_transit.zip",
        "file": "szeged.zip",
        "insecure": True,
    },
    {
        "id": "szombathely",
        "name": "Szombathely — Blaguss",
        "url": "https://szombathely.utas.hu/api/static/v1/gtfs-google/gtfs-google.zip",
        "file": "szombathely.zip",
    },
    {
        "id": "veszprem",
        "name": "Veszprém — V-Busz",
        "url": "https://vbusz.hu/gtfs",
        "file": "veszprem.zip",
    },
    {
        "id": "paks",
        "name": "Paks — Paksbusz",
        "url": "https://utazastervezo.paksbusz.net/api/static/v1/gtfs-google/gtfs-google.zip",
        "file": "paks.zip",
    },
    {
        "id": "dombovar",
        "name": "Dombóvár — PTKE",
        "url": "https://dombovar.ptke.hu/gtfs",
        "file": "dombovar.zip",
    },
    {
        "id": "kecskemet-kisvasut",
        "name": "Kecskemét — Kisvasút",
        "url": "https://kecskemetikisvasut.hu/wp-content/uploads/jk-gtfs/feed.zip",
        "file": "kecskemet-kisvasut.zip",
    },
    {
        "id": "budaors",
        "name": "Budaörs — HOMM",
        "url": "https://armadina-transit.fra1.digitaloceanspaces.com/budaors.zip",
        "file": "budaors.zip",
    },
    {
        "id": "bahart",
        "name": "Balaton — Bahart",
        "url": "https://gtfs.menetbrand.com/download/bahart",
        "url_env": "BAHART_GTFS_URL",
        "catalog_query": "bahart",
        "file": "bahart.zip",
    },
    {
        "id": "hodmezovasarhely",
        "name": "Hódmezővásárhely — HVSZ",
        "url": "https://gtfs.menetbrand.com/download/hodmezovasarhely",
        "file": "hodmezovasarhely.zip",
    },
    {
        "id": "kaposvar",
        "name": "Kaposvár — KKZRT",
        "url": "https://gtfs.menetbrand.com/download/kaposvar",
        "file": "kaposvar.zip",
    },
    {
        "id": "kiskunhalas",
        "name": "Kiskunhalas — Halasbusz",
        "url": "https://gtfs.menetbrand.com/download/kiskunhalas",
        "file": "kiskunhalas.zip",
    },
    {
        "id": "szekesfehervar",
        "name": "Székesfehérvár — helyi",
        "url": "https://gtfs.menetbrand.com/download/szekesfehervar",
        "file": "szekesfehervar.zip",
    },
    {
        "id": "tatabanya",
        "name": "Tatabánya — T-Busz",
        "url": "https://gtfs.menetbrand.com/download/tatabanya",
        "file": "tatabanya.zip",
    },
    {
        "id": "varpalota",
        "name": "Várpalota — THURY-BUSZ",
        "url": "https://gtfs.menetbrand.com/download/varpalota",
        "file": "varpalota.zip",
    },
]


def include_all(short: str, _agency: str, _aid: str) -> bool:
    return bool(short)


def include_volan_local(short: str, agency: str, _aid: str) -> bool:
    if re.fullmatch(r"\d{4}", short):
        return False
    low = (agency or "").lower()
    if "helyközi" in low or "helykozi" in low:
        return False
    return True


def skip_helykozi(agency: str) -> bool:
    low = (agency or "").lower()
    return "helyközi" in low or "helykozi" in low


def _load_feed(base: Path, feed: dict) -> list[dict]:
    """Parts of one city feed. A missing zip is downloaded; when the zip from
    one URL (a keyed override, say) fails to download or parse, the next URL
    is tried, down to the public one."""
    zpath = base / feed["file"]

    def load() -> list[dict]:
        return load_parts(
            zpath,
            include_all,
            op_id=feed["id"],
            op_name=feed["name"],
            prefix=f"{feed['id']}:",
            split_platforms_m=PLATFORM_SPLIT_M,
        )

    if zpath.exists():
        return load()
    last: Exception | None = None
    for url in feed_urls(feed):
        try:
            print("downloading", feed["id"])
            download_feed(url, zpath, allow_insecure=bool(feed.get("insecure")))
            return load()
        except Exception as err:
            last = err
            zpath.unlink(missing_ok=True)
    raise last or FileNotFoundError(feed["file"])


def _fresh(got: list[dict], label: str, today: str | None) -> list[dict]:
    """Drop parts whose calendar already ended, loudly. The feed then counts
    as missing for guard_feeds, so a stale zip blocks the rebuild unless it
    is explicitly allowed to go."""
    ended = dict(stale_parts(got, today))
    if not ended:
        return got
    for op, end in ended.items():
        print(f"warn {label}: calendar of {op} ended {end[:4]}-{end[4:6]}-{end[6:]}; skipped", file=sys.stderr)
    return [p for p in got if p["op_id"] not in ended]


def collect_parts(
    base: Path, volan_zip: Path | None = None, *, today: str | None = None
) -> tuple[list[dict], list[str]]:
    """Operator parts with trips, and the feed ids that failed, had none or
    whose calendar already ended."""
    parts: list[dict] = []
    failed: list[str] = []
    for feed in CITY_FEEDS:
        try:
            got = [p for p in _load_feed(base, feed) if p.get("t")]
        except Exception as err:
            failed.append(feed["id"])
            print(f"warn {feed_error(feed['id'], err)}", file=sys.stderr)
            continue
        if not got:
            failed.append(feed["id"])
            print(f"warn {feed['id']}: no trips", file=sys.stderr)
        else:
            got = _fresh(got, feed["id"], today)
            if not got:
                failed.append(feed["id"])
        parts.extend(got)
    if volan_zip and volan_zip.exists():
        try:
            print("volan local", volan_zip)
            got = [
                p
                for p in load_parts(
                    volan_zip,
                    include_volan_local,
                    split_agencies=True,
                    skip_agency=skip_helykozi,
                    prefix="",
                    split_platforms_m=PLATFORM_SPLIT_M,
                )
                if p.get("t")
            ]
        except Exception as err:
            got = []
            print(f"warn volan-local: {err}", file=sys.stderr)
        got = _fresh(got, "volan-local", today)
        if not got:
            failed.append("volan-local")
        parts.extend(got)
    else:
        failed.append("volan-local")
    if not parts:
        raise RuntimeError("no city GTFS parts" + (f" ({', '.join(failed)})" if failed else ""))
    if failed:
        print("partial city index, skipped:", ", ".join(failed), file=sys.stderr)
    return parts, failed


def _feed_group(op_id: str) -> str | None:
    """The configured feed an operator comes from; Volán local agencies count
    as one. None for operators of feeds no longer configured."""
    if op_id in {f["id"] for f in CITY_FEEDS}:
        return op_id
    if op_id.startswith("volan-"):
        return "volan-local"
    return None


def _group_trips(op_trips: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for op_id, n in op_trips.items():
        group = _feed_group(op_id)
        if group:
            out[group] = out.get(group, 0) + n
    return out


def allowed_missing() -> set[str]:
    load_env_files()
    raw = os.environ.get(ALLOW_MISSING_ENV) or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


def guard_feeds(out: Path, parts: list[dict], allow: set[str] | None = None) -> None:
    """Refuse a rebuild that loses a feed the previous index had, or most of
    its trips. Without a previous index there is nothing to compare."""
    prev = operator_trips(out)
    if not prev:
        return
    allow = allowed_missing() if allow is None else allow
    now: dict[str, int] = {}
    for part in parts:
        now[part["op_id"]] = now.get(part["op_id"], 0) + len(part["t"])
    before, after = _group_trips(prev), _group_trips(now)
    problems = []
    for group, had in sorted(before.items()):
        if group in allow or "all" in allow:
            continue
        has = after.get(group, 0)
        if not has:
            problems.append(f"{group} missing (had {had} trips)")
        elif had >= MIN_GUARDED_TRIPS and has < had * MIN_TRIP_RATIO:
            problems.append(f"{group} {has} trips vs {had}")
    if problems:
        raise IndexGuardError(
            f"refusing to replace {out.name}: " + "; ".join(problems)
            + f" (set {ALLOW_MISSING_ENV} to accept)"
        )


def build_city_index(base: Path, out: Path, volan_zip: Path | None = None, *, today: str | None = None) -> dict:
    parts, failed = collect_parts(base, volan_zip, today=today)
    guard_feeds(out, parts)
    info = write_index(parts, out, with_operators=True, today=today)
    info["skipped"] = failed
    return info


def main() -> None:
    base = Path(".")
    volan = Path("volanbusz_gtfs.zip")
    if not volan.exists():
        print("downloading", VOLAN_URL)
        download_feed(VOLAN_URL, volan)
    info = build_city_index(base, OUT, volan)
    print("wrote", OUT, info)


if __name__ == "__main__":
    main()
