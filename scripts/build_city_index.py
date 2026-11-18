#!/usr/bin/env python3
"""Build city-index.json.gz: municipal GTFS + Volán local (non-4-digit) routes."""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import NamedTuple

from build_volan_index import download_volan, is_helykozi
from gtfs_compact import (
    PLATFORM_SPLIT_M,
    IndexGuardError,
    _meters,
    calendar_end,
    download_feed,
    feed_error,
    feed_urls,
    guard_basis,
    index_parts,
    load_env_files,
    load_parts,
    operator_running_trips_of,
    operator_stats_of,
    read_index,
    running_trips,
    stale_parts,
    write_index,
)

OUT = Path("city-index.json.gz")
# A feed that the previous index had must not vanish or shrink below this
# share of its trips; the previous index stays in place instead.
MIN_TRIP_RATIO = 0.5
MIN_GUARDED_TRIPS = 50
# Comma-separated feed ids (or "all") that may be missing from a rebuild.
ALLOW_MISSING_ENV = "HUNGARIAN_TRANSPORT_ALLOW_MISSING"
# The weekly refresh (release.yml) downloads every city feed afresh, and
# one publisher's outage would leave its feed out, so the guard refused the
# whole release. With this naming the previous release's city index (its
# shapes folder next to it), a feed that fails to download or load keeps
# that index's part instead, with a ::warning::. A feed the previous index
# lacks has nothing to keep and is left out as before.
CARRY_ENV = "HUNGARIAN_TRANSPORT_CARRY_INDEX"
# More feeds failing at once is the network or the runner, not a publisher:
# none is carried, and the guard decides as without CARRY_ENV.
MAX_CARRIED = 3

# Weekendbus (Csömör/Pécel) still hosts a zip, but its calendar ended
# 2025-12-31. GYSEV trains are in MÁV's feed (the MÁV index); the GYSEV zip
# adds only Austrian services and replacement buses. Neither is wired.
# Feeds that still need a form or login: MÁV, NAP. KeKo's own zip
# (timetable.keko.hu) answers 401; menetbrand serves it.
# Menetbrand /download/<slug> is the public zip when the publisher has no fixed
# filename (Kaposvár), a CAPTCHA (Kiskunhalas), a login (Kecskemét KeKo, DKV),
# no host any more (dombovar.ptke.hu stopped resolving in November 2026), or
# until a keyed URL is in .env.
# gy-mate.hu: Máté Gyöngyösi's hand-made feeds (CC BY 4.0) of the Budavári
# Sikló, the Zugliget Libegő, two Danube ferries and the Üröm minibus; the
# first three run from frequencies.txt (load_parts expands it).
# Bahart: official NAP URL via BAHART_GTFS_URL; Transitland / Mobility Database
# as catalog fallback; menetbrand if neither is set.
# "insecure": the publisher still serves plain http. HTTPS is tried first;
# only these feeds may fall back to http (download_feed warns when it does).
# "mirrors": copies of the same zip, tried in order after "url" fails.
# "agencies": the agency ids or names a feed is read for; the rest of the
# zip is left out. A route without agency_id in a zip of one agency is that
# agency's (load_parts).
# "names": stop names as the feed writes them -> as the index shows them.
# "replaces": the zip this feed's source replaced on purpose, as the guard
# recognises it in the index in place: no feed_version ("unversioned", it
# had no feed_info.txt) and trips of the route types the new source leaves
# out ("dropped"). Against that index the guard compares the rebuild with
# the old feed's other trips only (_group_problem), and a part carried over
# from it (CARRY_ENV) leaves the dropped trips out. Once the index in place
# holds the new feed, the guard compares as for any feed: the rule follows
# the indexes, not the calendar, so it holds whenever the new code first
# builds (the weekly Pages build and the release run the latest stable
# release's builders).
# Szeged: SZKT's own zip (developer page szkt.hu/fejlesztoknek-gtfs-adatbazis,
# NAP profile 11; menetbrand mirrors it byte for byte). It also carries MÁV's
# Szeged local buses (agency hkir_V-33367), which the Volán feed already
# gives as volan-szeged, so only the SZKT trams and trolleybuses are read.
# The tram-train to Hódmezővásárhely is in MÁV's own GTFS (route "1").
# szegedimenetrend.hu, the address used until November 2026, still serves
# the DAKK-era zip of December 2025 (Volánbusz, SZKT and MÁV-START at once),
# whose calendar_dates stop in January 2026: no fallback to it. That zip had
# no feed_info, and its buses (route_type 3) and tram-train (109) are what
# the new one leaves out.
# SZKT cuts its stop names to 25 characters, abbreviated ("Belv. tem.
# II.") or cut off mid-bracket ("Londoni krt. (Bakay N. u."). The full
# names are the ones the zip it replaced gave the same stops (within 40 m),
# and the Volán feed's for the stops it shares; where neither spells a word
# out, only "u." is written as "utca". A name SZKT changes no longer
# matches and is shown as SZKT writes it.
SZEGED_STOP_NAMES = {
    "Anna - kút": "Anna-kút",
    "Aradi v. tere": "Aradi vértanúk tere",
    "Attila u. (Mars tér)": "Attila utca (Mars tér)",
    "Bakay N. utca": "Bakay Nándor utca",
    "Belv. tem. II.": "Belvárosi temető II. kapu",
    "Belvárosi tem.": "Belvárosi temető",
    "Bercsényi u.": "Bercsényi utca",
    "Berlini krt.": "Berlini körút",
    "Brüsszeli krt.": "Brüsszeli körút",
    "Budapesti krt.": "Budapesti körút",
    "Bécsi krt.": "Bécsi körút",
    "Damjanich u.": "Damjanich utca",
    "Deák F. Gimn.": "Deák Ferenc Gimnázium",
    "Fecske u.": "Fecske utca",
    "Gál u.": "Gál utca",
    "Glattfelder t.": "Glattfelder Gyula tér",
    "Kecskeméti u.": "Kecskeméti utca",
    "Károlyi u.": "Károlyi utca",
    "Londoni krt.": "Londoni körút",
    "Londoni krt. (Bakay N. u.": "Londoni körút (Bakay Nándor utca)",
    "Makkosházi krt.": "Makkosházi körút",
    "Mars tér (Attila u.)": "Mars tér (Attila utca)",
    "Mars tér (Aut. áll.)": "Mars tér (autóbusz-állomás)",
    "Mikszáth K. u.": "Mikszáth K. utca",
    "Rókusi vízt.": "Rókusi víztorony",
    "Római krt.": "Római körút",
    "Sándor u.": "Sándor utca",
    "Szeged vá.": "Szeged vasútállomás",
    "Szivárvány kit": "Szivárvány kitérő",
    "Szt György tér": "Szent György tér",
    "Szt István tér": "Szent István tér",
    "Tabán u.(Felső Tisza-part": "Tabán utca (Felső Tisza-part)",
    "Veresács u.": "Veres ács utca",
    "Vitéz u.": "Vitéz utca",
    "Vásárhelyi P. út": "Vásárhelyi Pál út",
}

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
        "url": "https://mobilitas.biokom.hu/gtfs/",
        "file": "pecs.zip",
    },
    {
        "id": "szeged",
        "name": "Szeged — SZKT",
        "url": "https://gtfs.szkt.hu/zipstore/szkt-szeged-hu.zip",
        "mirrors": ["https://gtfs.menetbrand.com/download/szeged/"],
        "file": "szeged.zip",
        "agencies": ("SZKT", "Szegedi Közlekedési Kft."),
        "names": SZEGED_STOP_NAMES,
        "replaces": {"unversioned": True, "dropped": (3, 109)},
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
        "url": "https://gtfs.menetbrand.com/download/dombovar/",
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
        "url": "https://gtfs.menetbrand.com/download/bahart/",
        "url_env": "BAHART_GTFS_URL",
        "catalog_query": "bahart",
        "file": "bahart.zip",
    },
    {
        "id": "hodmezovasarhely",
        "name": "Hódmezővásárhely — HVSZ",
        "url": "https://gtfs.menetbrand.com/download/hodmezovasarhely/",
        "file": "hodmezovasarhely.zip",
    },
    {
        "id": "kaposvar",
        "name": "Kaposvár — KKZRT",
        "url": "https://gtfs.menetbrand.com/download/kaposvar/",
        "file": "kaposvar.zip",
    },
    {
        "id": "kiskunhalas",
        "name": "Kiskunhalas — Halasbusz",
        "url": "https://gtfs.menetbrand.com/download/kiskunhalas/",
        "file": "kiskunhalas.zip",
    },
    {
        "id": "szekesfehervar",
        "name": "Székesfehérvár — helyi",
        "url": "https://gtfs.menetbrand.com/download/szekesfehervar/",
        "file": "szekesfehervar.zip",
    },
    {
        "id": "tatabanya",
        "name": "Tatabánya — T-Busz",
        "url": "https://gtfs.menetbrand.com/download/tatabanya/",
        "file": "tatabanya.zip",
    },
    {
        "id": "varpalota",
        "name": "Várpalota — THURY-BUSZ",
        "url": "https://gtfs.menetbrand.com/download/varpalota/",
        "file": "varpalota.zip",
    },
    {
        "id": "debrecen",
        "name": "Debrecen — DKV",
        "url": "https://gtfs.menetbrand.com/download/debrecen/",
        "file": "debrecen.zip",
    },
    {
        "id": "kecskemet",
        "name": "Kecskemét — KeKo",
        "url": "https://gtfs.menetbrand.com/download/kecskemet/",
        "file": "kecskemet.zip",
    },
    {
        "id": "oroshaza",
        "name": "Orosháza — helyi",
        "url": "https://gtfs.gpspositions.net/storage/exports/OROSH_V_ONK/gtfs_latest.zip",
        "file": "oroshaza.zip",
    },
    {
        "id": "urom",
        "name": "Üröm — helyi",
        "url": "https://gy-mate.hu/gtfs/gtfs_hu_urom.zip",
        "file": "urom.zip",
    },
    {
        "id": "budapest-siklo",
        "name": "Budapest — Budavári Sikló",
        "url": "https://gy-mate.hu/gtfs/gtfs_hu_funicular.zip",
        "file": "budapest-siklo.zip",
    },
    {
        "id": "budapest-libego",
        "name": "Budapest — Zugligeti Libegő",
        "url": "https://gy-mate.hu/gtfs/gtfs_hu_chairlift.zip",
        "file": "budapest-libego.zip",
    },
    {
        "id": "dunakeszi-komp",
        "name": "Dunakeszi — Horány komp",
        "url": "https://gy-mate.hu/gtfs/gtfs_hu_dunakeszi-ferry.zip",
        "file": "dunakeszi-komp.zip",
    },
    {
        "id": "szentendre-rev",
        "name": "Szentendre — Pásztor rév",
        "url": "https://gy-mate.hu/gtfs/gtfs_hu_pasztor-ferry.zip",
        "file": "szentendre-rev.zip",
    },
]


def include_all(short: str, _agency: str, _aid: str) -> bool:
    return bool(short)


def include_volan_local(short: str, agency: str, _aid: str) -> bool:
    """A Volán town network's route: what the Volán index
    (build_volan_index.include_long) leaves."""
    if re.fullmatch(r"\d{4}", short):
        return False
    return not is_helykozi(agency)


def skip_helykozi(agency: str) -> bool:
    return is_helykozi(agency)


def include_agencies(keep: Iterable[str]):
    """include_all for the routes of the agencies keep names, by agency_id
    or agency_name: SZKT's own zip calls its agency "SZKT" by id, the older
    szegedimenetrend.hu zip by name."""
    names = frozenset(keep)

    def include(short: str, agency: str, aid: str) -> bool:
        return bool(short) and (aid in names or agency in names)

    return include


def _load_feed(base: Path, feed: dict, zpath: Path | None = None) -> list[dict]:
    """Parts of one city feed, from zpath when given (a new zip not yet in
    base), else from base. A missing zip is downloaded; when the zip from
    one URL (a keyed override, say) fails to download or parse, the next URL
    is tried, down to the public one."""
    zpath = zpath or base / feed["file"]
    include = include_agencies(feed["agencies"]) if feed.get("agencies") else include_all

    def load() -> list[dict]:
        parts = load_parts(
            zpath,
            include,
            op_id=feed["id"],
            op_name=feed["name"],
            prefix=f"{feed['id']}:",
            split_platforms_m=PLATFORM_SPLIT_M,
        )
        names = feed.get("names") or {}
        for part in parts:
            for row in part["s"]:
                row[1] = stop_name(row[1], names)
        return parts

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


def stop_name(name: str, names: dict[str, str] | None = None) -> str:
    """A city feed's stop name as the index shows it: from the feed's
    "names" table when it lists the name, and with a bracket the feed left
    open (a name cut off at a length limit) closed."""
    name = (names or {}).get(name, name)
    return name + ")" * max(0, name.count("(") - name.count(")"))


def ymd(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}" if len(day) == 8 else day or "?"


def _fresh(got: list[dict], label: str, today: str | None) -> tuple[list[dict], str]:
    """Drop parts whose calendar already ended, loudly; the parts left and
    the last service day of the dropped ones ("" when none was dropped).

    A seasonal feed (Bahart) or a publisher that lets its zip run out ends
    up here; guard_feeds decides whether the rebuild may lose it."""
    ended = dict(stale_parts(got, today))
    if not ended:
        return got, ""
    for op, end in ended.items():
        print(f"warn {label}: calendar of {op} ended {ymd(end)}; skipped", file=sys.stderr)
    return [p for p in got if p["op_id"] not in ended], max(ended.values())


def _with_trips(parts: list[dict]) -> list[dict]:
    return [p for p in parts if p.get("t")]


def _last_day(parts: list[dict]) -> str:
    """Last service day (YYYYMMDD) of parts, "" when one has no calendar end."""
    ends = [calendar_end(p) for p in parts]
    return "" if not ends or "" in ends else max(ends)


# collect_parts and guard_feeds read the previous index themselves unless
# given it.
_UNREAD: object = object()


def collect_parts(
    base: Path,
    volan_zip: Path | None = None,
    *,
    today: str | None = None,
    zips: dict[str, Path] | None = None,
    out: Path | None = None,
    previous: Previous | None | object = _UNREAD,
    allow: set[str] | None = None,
    carry: Path | None = None,
) -> tuple[list[dict], list[str], dict[str, str], list[str], dict[str, dict[str, str]], dict[str, str]]:
    """Operator parts with trips, the feed ids that failed, had none or
    whose calendar already ended, of the last: feed id -> last service
    day, the feed ids whose new zip was rejected, of those the ones
    rejected for their content: feed id -> {"why": the reason, "until":
    the last service day (YYYYMMDD, "" = unknown) of the zip kept instead},
    and the feeds carried from carry: feed id -> why its own zip failed.
    A feed that only lost some operators to an ended calendar (Volán local
    agencies) is neither failed nor ended.

    carry: the previous release's city index (CARRY_ENV). A feed whose zip
    fails to download or load takes its part from there, unless allow
    names it (it is meant to go), the index lacks it or its calendar ended
    there too; with more than MAX_CARRIED such feeds, none is carried.

    zips: feed id -> a newly downloaded zip to read instead of the one in
    base. One that fails to load or has no trips is rejected and the feed
    falls back to its zip in base, so a broken download does not take a
    working feed out of the index (and the guard does not refuse it all).
    Only the one with no trips is rejected for its content: one that fails
    to load may be a download gone wrong, to be fetched again. So is one
    that loads but that guard_feeds would refuse against the previous index
    out (its calendar already ended, most of its trips gone) while the zip
    in base passes: one stale download does not block the other feeds'
    updates. That holds until the zip in base ends ("until"). A feed named
    in allow (default HUNGARIAN_TRANSPORT_ALLOW_MISSING; "all" for every
    feed) takes such a zip all the same, as guard_feeds then does: the
    reduction is meant.

    previous: the previous index as _previous(out, today) reads it, when
    the caller has it already."""
    today = today or date.today().strftime("%Y%m%d")
    if previous is _UNREAD:
        previous = _previous(out, today) if zips else None
    if (zips or carry is not None) and allow is None:
        allow = allowed_missing()
    allow = allow or set()
    parts: list[dict] = []
    failed: list[str] = []
    expired: dict[str, str] = {}
    rejected: list[str] = []
    refused: dict[str, dict[str, str]] = {}
    # Feeds whose zip did not download or load: id -> (why, where in parts).
    unloaded: dict[str, tuple[str, int]] = {}
    for feed in CITY_FEEDS:
        fid = feed["id"]
        new_zip = (zips or {}).get(fid)
        in_base = (base / feed["file"]).exists()
        got: list[dict] | None = None
        if new_zip is not None:
            new: list[dict] | None
            try:
                new = _with_trips(_load_feed(base, feed, new_zip))
                problem = "no trips"
            except Exception as err:
                new, problem = None, feed_error(fid, err)
            if not new:
                # Unusable whatever the zip in base does.
                rejected.append(fid)
                if new is not None:
                    refused[fid] = {"why": problem, "until": ""}
                    problem = f"{fid}: {problem}"
                print(f"warn new zip {problem}; the previous zip stays", file=sys.stderr)
                if not in_base:
                    failed.append(fid)
                    continue
            else:
                got = new
                why = _feed_problem(fid, previous, new, today)
                if why and (fid in allow or "all" in allow):
                    print(f"warn new zip {fid}: {why}; taken, {ALLOW_MISSING_ENV} names it", file=sys.stderr)
                    why = ""
                if why and in_base:
                    try:
                        old = _with_trips(_load_feed(base, feed))
                    except Exception:
                        old = []
                    # Only for a zip in base that passes: when both fail,
                    # the newer one stays and guard_feeds decides.
                    if old and not _feed_problem(fid, previous, old, today):
                        rejected.append(fid)
                        refused[fid] = {"why": why, "until": _last_day(old)}
                        print(f"warn new zip {fid}: {why}; the previous zip stays", file=sys.stderr)
                        got = old
        if got is None:
            try:
                got = _with_trips(_load_feed(base, feed))
            except Exception as err:
                failed.append(fid)
                why = feed_error(fid, err)
                print(f"warn {why}", file=sys.stderr)
                unloaded[fid] = (why, len(parts))
                continue
        if not got:
            failed.append(fid)
            print(f"warn {fid}: no trips", file=sys.stderr)
        else:
            got, end = _fresh(got, fid, today)
            if not got:
                failed.append(fid)
                expired[fid] = end
        parts.extend(got)
    carried = _carry(parts, failed, unloaded, carry, allow, today) if carry is not None else {}
    if volan_zip and volan_zip.exists():
        try:
            print("volan local", volan_zip)
            got = _with_trips(
                load_parts(
                    volan_zip,
                    include_volan_local,
                    split_agencies=True,
                    skip_agency=skip_helykozi,
                    prefix="",
                    split_platforms_m=PLATFORM_SPLIT_M,
                )
            )
        except Exception as err:
            got = []
            print(f"warn volan-local: {err}", file=sys.stderr)
        had_any = bool(got)
        got, end = _fresh(got, "volan-local", today)
        if not got:
            failed.append("volan-local")
            if had_any:
                expired["volan-local"] = end
        parts.extend(got)
    else:
        failed.append("volan-local")
    if not parts:
        raise RuntimeError("no city GTFS parts" + (f" ({', '.join(failed)})" if failed else ""))
    if failed:
        print("partial city index, skipped:", ", ".join(failed), file=sys.stderr)
    return parts, failed, expired, rejected, refused, carried


def _carry(parts: list[dict], failed: list[str], unloaded: dict[str, tuple[str, int]], carry: Path,
           allow: set[str], today: str) -> dict[str, str]:
    """Put the previous index's part of each unloaded feed (id -> (why, its
    place in parts)) into parts, in its place, and off failed; feed id ->
    why for those carried (collect_parts' carry)."""
    todo = {fid: v for fid, v in unloaded.items() if fid not in allow and "all" not in allow}
    if not todo:
        return {}
    if len(todo) > MAX_CARRIED:
        print(f"::warning title=City feeds not carried over::{len(todo)} city feeds failed to download"
              f" ({', '.join(sorted(todo))}); more than {MAX_CARRIED} at once is not one publisher's outage,"
              " so none keeps the previous release's timetable.")
        return {}
    carried: dict[str, str] = {}
    # From the last place to the first, so the earlier places still hold.
    for fid, (why, at) in reversed(list(todo.items())):
        old = index_parts(carry, {fid})
        got, _end = _fresh(_with_trips([_without_replaced(p) for p in old or []]), fid, today)
        if not got:
            print(f"warn {fid}: {carry.name} has no running timetable of it either; nothing to carry over",
                  file=sys.stderr)
            continue
        parts[at:at] = got
        failed.remove(fid)
        carried[fid] = why
        trips = sum(len(p["t"]) for p in got)
        print(f"::warning title=City feed carried over::{why}; the previous release's {fid} timetable"
              f" ({trips} trips) ships again.")
    return carried


def _without_replaced(part: dict) -> dict:
    """A part carried over from the previous index without the trips its
    feed's new source left out on purpose, when it is the zip that source
    replaced ("replaces"): the old Szeged zip's Volán buses are volan-szeged
    already, its tram-train MÁV's. Its stops and shapes no trip uses any
    more go with them. Any other part as it is."""
    spec = _replaces(part["op_id"])
    trips = part.get("t") or []
    if not _is_replaced(spec, part.get("feed") or "", {t[4] for t in trips if len(t) > 4}):
        return part
    dropped = set(spec.get("dropped") or ())
    keep = [t for t in trips if not (len(t) > 4 and t[4] in dropped)]
    used = sorted({p for t in keep for p in t[2]})
    s_map = {old: new for new, old in enumerate(used)}
    shapes = part.get("shapes") or []
    used_sh = sorted({t[5] for t in keep if len(t) > 5 and isinstance(t[5], int) and 0 <= t[5] < len(shapes)})
    sh_map = {old: new for new, old in enumerate(used_sh)}
    rows = []
    for t in keep:
        row = [t[0], t[1], [s_map[p] for p in t[2]], *t[3:]]
        if len(t) > 5:
            row[5] = sh_map.get(t[5], -1) if isinstance(t[5], int) else -1
        rows.append(row)
    g = part.get("g") or []
    print(f"warn {part['op_id']}: the carried timetable is the zip its new source replaced;"
          f" its {len(trips) - len(keep)} trips of route types {', '.join(map(str, sorted(dropped)))} are left out",
          file=sys.stderr)
    return {
        **part,
        "s": [part["s"][i] for i in used],
        "g": [v for i in used for v in g[2 * i:2 * i + 2]] if len(g) == 2 * len(part["s"]) else [],
        "shapes": [shapes[i] for i in used_sh],
        "t": rows,
    }


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


def _group_end(prev: dict[str, tuple[int, str]], group: str) -> str:
    """Last service day of a feed in the previous index ("" = unknown)."""
    ends = [end for op, (_n, end) in prev.items() if _feed_group(op) == group]
    return "" if not ends or "" in ends else max(ends)


def allowed_missing() -> set[str]:
    load_env_files()
    raw = os.environ.get(ALLOW_MISSING_ENV) or ""
    return {part.strip() for part in raw.split(",") if part.strip()}


class Previous(NamedTuple):
    """What guard_feeds compares a rebuild with, of the previous index."""

    # Operator id -> (trips, last service day), operator_stats().
    stats: dict[str, tuple[int, str]]
    # Feed -> its running trips.
    running: dict[str, int]
    # Feed -> all trips of its operators still running.
    total: dict[str, int]
    # Feed -> route_type -> (running trips, trips as in total) of that type.
    route_types: dict[str, dict[int, tuple[int, int]]]
    # Operator id -> its feed_version in the index ("feed"); "" = none.
    versions: dict[str, str]


def _feed_versions(idx: dict) -> dict[str, str]:
    """Operator id -> feed_version of an index's "feed" string."""
    out: dict[str, str] = {}
    for item in str(idx.get("feed") or "").split(";"):
        op, sep, version = item.partition(":")
        if sep and op:
            out[op] = version
    return out


def _route_type_trips(idx: dict, today: str, live: set[str]) -> dict[str, dict[int, tuple[int, int]]]:
    """Previous.route_types of an index already read; {} when it is not one."""
    try:
        ops = idx.get("o") or []
        stops = idx.get("s") or []
        running = {id(t) for t in running_trips(idx, today)}
        counts: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for t in idx.get("t") or []:
            op = ops[stops[t[2][0]][3]][0]
            group = _feed_group(op)
            if not group or op not in live:
                continue
            got = counts[group][t[4] if len(t) > 4 and isinstance(t[4], int) else 3]
            got[0] += id(t) in running
            got[1] += 1
        return {g: {rt: (n[0], n[1]) for rt, n in by.items()} for g, by in counts.items()}
    except (IndexError, KeyError, TypeError):
        return {}


def _previous(out: Path | None, today: str, idx: dict | None | object = _UNREAD) -> Previous | None:
    """The previous index as guard_feeds sees it; None without one. idx:
    that index, read_index(out), when the caller has it already."""
    if idx is _UNREAD:
        idx = read_index(out) if out is not None else None
    if not isinstance(idx, dict):
        return None
    prev = operator_stats_of(idx)
    if not prev:
        return None
    live = {op: n for op, n in (operator_running_trips_of(idx, today) or {}).items() if n}
    return Previous(
        prev,
        _group_trips(live),
        _group_trips({op: n for op, (n, _e) in prev.items() if op in live}),
        _route_type_trips(idx, today, set(live)),
        _feed_versions(idx),
    )


def _counts(parts: list[dict], today: str) -> tuple[dict[str, int], dict[str, int]]:
    """(running trips, all trips) per feed of parts."""
    running: dict[str, int] = {}
    total: dict[str, int] = {}
    for part in parts:
        op = part["op_id"]
        running[op] = running.get(op, 0) + len(running_trips(part, today))
        total[op] = total.get(op, 0) + len(part["t"])
    return _group_trips(running), _group_trips(total)


def _replaces(group: str) -> dict:
    """The feed's "replaces" (CITY_FEEDS), {} when it has none."""
    feed = next((f for f in CITY_FEEDS if f["id"] == group), None)
    return dict((feed or {}).get("replaces") or {})


def _is_replaced(spec: dict, version: str, route_types: Iterable[int]) -> bool:
    """Whether a feed's part (its feed_version, the route types of its
    trips) is the zip its "replaces" spec describes."""
    if not spec:
        return False
    if spec.get("unversioned") and version:
        return False
    return bool(set(spec.get("dropped") or ()) & set(route_types))


def _replaced_trips(group: str, previous: Previous, on_running: bool) -> int | None:
    """The trips of the previous index's feed that its new source still
    runs (those of the other route types than "dropped"), counted as the
    guard counts (running or all); None when that index does not hold the
    zip the feed's source replaced. That zip had both: a part with fewer
    than MIN_GUARDED_TRIPS of the kept types is not it (a bus-only feed of
    the same id would otherwise pass on almost nothing)."""
    spec = _replaces(group)
    by_type = previous.route_types.get(group) or {}
    kinds = {rt for rt, (run, total) in by_type.items() if (run if on_running else total)}
    if not _is_replaced(spec, previous.versions.get(group, ""), kinds):
        return None
    dropped = set(spec.get("dropped") or ())
    kept = sum((run if on_running else total) for rt, (run, total) in by_type.items() if rt not in dropped)
    return kept if kept >= MIN_GUARDED_TRIPS else None


def _group_problem(
    group: str,
    previous: Previous,
    after_running: dict[str, int],
    after_all: dict[str, int],
    expired: dict[str, str],
    *,
    warn: bool = False,
) -> str:
    """Why guard_feeds refuses a rebuild for this feed, or "".

    A feed whose source changed on purpose ("replaces", CITY_FEEDS) is,
    while the previous index still holds the old zip, compared with that
    zip's trips of the route types the new source keeps: it must keep that
    share of them, and at least MIN_GUARDED_TRIPS, so a broken new zip of a
    handful of trips is still refused. With warn the accepted shrink is
    said on stderr."""
    prev, before, before_all = previous.stats, previous.running, previous.total
    running = before.get(group)
    if not running:
        return ""
    on_running, keep = guard_basis(running, before_all[group], MIN_TRIP_RATIO)
    if on_running:
        had, has = running, after_running.get(group, 0)
    else:
        had, has = before_all[group], after_all.get(group, 0)
    if not has and group in expired:
        return (f"{group} calendar ended {ymd(expired[group])} (had {had} trips,"
                f" the previous index runs until {ymd(_group_end(prev, group))})")
    if not has:
        return f"{group} missing (had {had} trips)"
    if had >= MIN_GUARDED_TRIPS and has < had * keep:
        kept = _replaced_trips(group, previous, on_running)
        if kept is None:
            return f"{group} {has} trips vs {had}"
        floor = max(kept * keep, MIN_GUARDED_TRIPS)
        if has < floor:
            return (f"{group} {has} trips vs {had} ({kept} of them of the route types its new source"
                    " keeps)")
        if warn:
            print(f"warn {group} {has} trips vs {had}: the previous index has the zip its source replaced;"
                  f" taken, as {kept} of those are of the route types the new source keeps", file=sys.stderr)
    return ""


def _feed_problem(fid: str, previous: Previous | None, got: list[dict], today: str) -> str:
    """_group_problem for one city feed built from got (its parts with
    trips), quietly: what guard_feeds would say about it, or ""."""
    if previous is None:
        return ""
    ended = dict(stale_parts(got, today))
    fresh = [p for p in got if p["op_id"] not in ended]
    expired = {fid: max(ended.values())} if ended and not fresh else {}
    return _group_problem(fid, previous, *_counts(fresh, today), expired)


def guard_feeds(
    out: Path,
    parts: list[dict],
    allow: set[str] | None = None,
    *,
    expired: dict[str, str] | None = None,
    today: str | None = None,
    previous: Previous | None | object = _UNREAD,
) -> None:
    """Refuse a rebuild that loses a feed the previous index had, or most of
    its trips. Without a previous index there is nothing to compare.

    An operator whose trips in the previous index have all ended has
    nothing left to lose: a seasonal feed (Bahart after its last sailing)
    or an expired zip leaves the index without blocking every other feed.
    A feed that still runs is compared on the trips that still run, on both
    sides, when enough of them do (guard_basis), so a feed whose summer
    service ended is not refused for it; otherwise on all its trips. A feed
    dropped for an ended calendar (expired: feed id -> last day) while the
    previous index still runs it is refused, and the message says so.
    previous: _previous(out, today), when the caller has it already."""
    today = today or date.today().strftime("%Y%m%d")
    if previous is _UNREAD:
        previous = _previous(out, today)
    if previous is None:
        return
    allow = allowed_missing() if allow is None else allow
    expired = expired or {}
    prev, before = previous.stats, previous.running
    after_running, after_all = _counts(parts, today)
    for group, had in sorted(_group_trips({op: n for op, (n, _e) in prev.items()}).items()):
        if group not in before and not after_all.get(group):
            print(f"warn {group}: calendar ended {ymd(_group_end(prev, group))} in the previous index"
                  f" as well; its {had} trips leave the index", file=sys.stderr)
    problems = []
    for group in sorted(before):
        if group in allow or "all" in allow:
            continue
        why = _group_problem(group, previous, after_running, after_all, expired, warn=True)
        if why:
            problems.append(why)
    if problems:
        raise IndexGuardError(
            f"refusing to replace {out.name}: " + "; ".join(problems)
            + f" (set {ALLOW_MISSING_ENV} to accept)"
        )


# A stop the rebuilt index renumbered (a feed that changed its stop ids, as
# Szeged's did with its new source) is the new stop of the same operator
# and mode within this distance. The rebuild gives that stop its old id back
# (keep_stop_ids), so a card saved with the old id finds it whatever the
# card's version; an old id left over (two old stops that became one) is an
# alias of the stop ("al" in the index, gtfs.js volanMatchStop). The poles
# of one stop are 20-40 m apart; across a street, its other direction
# usually is too.
ALIAS_M = 50.0
# An operator keeps at most this many aliases per stop: the newest first. A
# feed that renumbers every week would otherwise grow the table by its stop
# count each time.
ALIASES_PER_STOP = 4


def _mode(route_type: object) -> int:
    """A GTFS route_type as its basic mode, the extended codes (700 bus, 800
    trolleybus, 900 tram, 100 rail...) as the basic ones, so SZKT's
    trolleybuses (800) are the old zip's (11)."""
    rt = route_type if isinstance(route_type, int) else 3
    if rt == 800:
        return 11
    for low, basic in ((100, 2), (200, 3), (400, 1), (700, 3), (900, 0), (1000, 4), (1300, 6), (1400, 7)):
        if low <= rt < low + 100:
            return basic
    return rt


class AliasStop(NamedTuple):
    id: str
    op: str
    lat: float
    lon: float
    modes: frozenset[int]
    # The stop number the card matches a saved id by (the row's third
    # element), and the feed's own id: the index's id unless the stop kept
    # an earlier one (keep_stop_ids).
    num: str = ""
    fid: str = ""


class AliasBaseline(NamedTuple):
    """The previous index as stop_aliases needs it: its stops and its own
    aliases (old id -> stop index), without keeping the whole index."""

    stops: list[AliasStop]
    aliases: list[tuple[str, int]]


def _stop_modes(n: int, trips: Iterable[list]) -> list[set[int]]:
    modes: list[set[int]] = [set() for _ in range(n)]
    for t in trips:
        mode = _mode(t[4] if len(t) > 4 else 3)
        for p in t[2]:
            if isinstance(p, int) and 0 <= p < n:
                modes[p].add(mode)
    return modes


def _alias_stop(row: list, op: str, g: list, i: int, modes: set[int], fid: object = None) -> AliasStop:
    ok = len(g) >= 2 * i + 2 and (g[2 * i] or g[2 * i + 1])
    sid = str(row[0])
    return AliasStop(sid, op, g[2 * i] / 1e5 if ok else 0.0, g[2 * i + 1] / 1e5 if ok else 0.0,
                     frozenset(modes), str(row[2]) if len(row) > 2 and row[2] is not None else "",
                     fid if isinstance(fid, str) and fid else sid)


def alias_baseline(idx: dict | None) -> AliasBaseline | None:
    """AliasBaseline of an index already read; None when it is not one."""
    if not isinstance(idx, dict):
        return None
    try:
        ops = idx.get("o") or []
        rows = idx["s"]
        g = idx.get("g") or []
        modes = _stop_modes(len(rows), idx.get("t") or [])
        stops = [_alias_stop(row, ops[row[3]][0] if len(row) > 3 else "", g, i, modes[i],
                             row[4] if len(row) > 4 else None)
                 for i, row in enumerate(rows)]
        aliases = [(str(a[0]), a[1]) for a in idx.get("al") or []
                   if isinstance(a, list) and len(a) == 2 and isinstance(a[1], int) and 0 <= a[1] < len(stops)]
        return AliasBaseline(stops, aliases)
    except (IndexError, KeyError, TypeError):
        return None


# Operator -> grid cell -> stops; a cell is about 550 x 380 m, so the stops
# within ALIAS_M of a point are in its cell or the eight around it.
StopGrid = dict[str, dict[tuple[int, int], list[AliasStop]]]


def _grid(stops: Iterable[AliasStop]) -> StopGrid:
    grid: StopGrid = defaultdict(lambda: defaultdict(list))
    for st in stops:
        if st.lat or st.lon:
            grid[st.op][(round(st.lat * 200), round(st.lon * 200))].append(st)
    return grid


def _near(grid: StopGrid, old: AliasStop) -> Iterator[tuple[float, AliasStop]]:
    """The stops of old's operator within ALIAS_M of it that a trip of its
    mode serves (any mode for an old stop without trips), with their
    distance; none for a stop without coordinates."""
    if not (old.lat or old.lon) or old.op not in grid:
        return
    cells = grid[old.op]
    cy, cx = round(old.lat * 200), round(old.lon * 200)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for st in cells.get((cy + dy, cx + dx), ()):
                if old.modes and st.modes and not old.modes & st.modes:
                    continue
                d = _meters((old.lat, old.lon), (st.lat, st.lon))
                if d <= ALIAS_M:
                    yield d, st


def _part_stops(parts: list[dict]) -> Iterator[tuple[list, AliasStop]]:
    for part in parts:
        rows = part.get("s") or []
        modes = _stop_modes(len(rows), part.get("t") or [])
        for i, row in enumerate(rows):
            yield row, _alias_stop(row, part["op_id"], part.get("g") or [], i, modes[i],
                                   row[3] if len(row) > 3 else None)


def keep_stop_ids(previous: AliasBaseline | None, parts: list[dict]) -> dict[str, str]:
    """Give the rebuild's renumbered stops the ids the previous index had
    for them, in parts' stop rows: id and stop number become the old stop's
    (the card matches a saved stop by the number in its id), and the feed's
    own id goes into the row's fourth element, which write_index writes as
    the stop's fifth (realtime feeds name the stop by it). So a card of any
    version finds a stop saved before the feed renumbered it, and a feed
    that switches source keeps its ids. Returns feed id -> the id it took.

    An old stop is one of the previous index whose id the rebuild lacks; it
    goes to a stop of the rebuild that the previous index did not name, one
    to one:
    - first the feed stop it went to last time (the row's feed id), however
      far it moved, so the ids do not drift from one rebuild to the next;
    - then by distance, the nearest pair first (ties by the old id, then
      the new): the stop of the same operator within ALIAS_M that a trip
      of the same mode serves, as stop_aliases finds it. An old stop whose
      candidates all went to nearer ones gets none here; stop_aliases then
      makes it an alias of its nearest stop (two old stops merged into one
      new one)."""
    if previous is None:
        return {}
    new_ids = {str(row[0]) for part in parts for row in part.get("s") or []}
    old_ids = {st.id for st in previous.stops}
    lost = [st for st in previous.stops if st.id not in new_ids]
    if not lost:
        return {}
    fresh: dict[str, tuple[list, AliasStop]] = {}
    for row, st in _part_stops(parts):
        if st.id not in old_ids:
            fresh.setdefault(st.id, (row, st))
    pairs: dict[str, AliasStop] = {}
    for old in lost:
        hit = fresh.get(old.fid) if old.fid != old.id else None
        if hit and hit[1].op == old.op and old.fid not in pairs:
            pairs[old.fid] = old
    taken = {old.id for old in pairs.values()}
    grid = _grid(st for nid, (_row, st) in fresh.items() if nid not in pairs)
    # Distances to the decimetre: two poles the same distance away are a tie
    # however the floating point falls, and the tie goes by the ids.
    cands = sorted(
        ((round(d, 1), old.id, st.id, old) for old in lost if old.id not in taken for d, st in _near(grid, old)),
        key=lambda c: c[:3],
    )
    for _d, oid, nid, old in cands:
        if oid not in taken and nid not in pairs:
            pairs[nid] = old
            taken.add(oid)
    for nid, old in pairs.items():
        row = fresh[nid][0]
        row[:] = [old.id, row[1], old.num, nid]
    return {nid: old.id for nid, old in sorted(pairs.items())}


def stop_aliases(previous: AliasBaseline | None, parts: list[dict]) -> dict[str, str]:
    """Old stop id -> new stop id for the stops of the previous index that
    the rebuild (parts, after keep_stop_ids) no longer has: the nearest stop
    of the same operator within ALIAS_M that a trip of the same mode serves
    (any mode for an old stop without trips). The previous index's own
    aliases are carried over, to their stop when the rebuild still has it,
    else to the stop nearest to it, so a card saved two renumberings ago
    still finds its stop. Stops without coordinates get none. In order: this
    rebuild's first, then the carried ones, at most ALIASES_PER_STOP per
    stop of an operator."""
    if previous is None:
        return {}
    new_ids: set[str] = set()
    stops_of: dict[str, int] = defaultdict(int)
    news: list[AliasStop] = []
    for _row, st in _part_stops(parts):
        new_ids.add(st.id)
        stops_of[st.op] += 1
        news.append(st)
    grid = _grid(news)

    def nearest(old: AliasStop) -> str:
        best = min(((d, st.id) for d, st in _near(grid, old)), default=None)
        return best[1] if best else ""

    out: dict[str, tuple[str, str]] = {}
    for st in previous.stops:
        if st.id not in new_ids and st.id not in out:
            got = nearest(st)
            if got:
                out[st.id] = (got, st.op)
    for old, ix in previous.aliases:
        if old in new_ids or old in out:
            continue
        target = previous.stops[ix]
        got = target.id if target.id in new_ids else nearest(target)
        if got:
            out[old] = (got, target.op)
    kept: dict[str, str] = {}
    per_op: dict[str, int] = defaultdict(int)
    for old, (new, op) in out.items():
        if per_op[op] >= ALIASES_PER_STOP * stops_of[op]:
            continue
        per_op[op] += 1
        kept[old] = new
    if len(kept) < len(out):
        print(f"warn stop aliases: {len(out) - len(kept)} of the oldest left out"
              f" (at most {ALIASES_PER_STOP} per stop of an operator)", file=sys.stderr)
    return kept


def build_city_index(
    base: Path,
    out: Path,
    volan_zip: Path | None = None,
    *,
    today: str | None = None,
    zips: dict[str, Path] | None = None,
    carry: Path | None = None,
) -> dict:
    """Build and write the city index; info["skipped"] lists the feeds left
    out, info["expired"] those of them whose calendar ended (id -> last day),
    info["rejected"] the new zips (zips, see collect_parts) not used,
    info["refused"] those of them rejected for their content (id -> why,
    until) and info["carried"] the feeds taken from carry (the previous
    release's index, see collect_parts) because their own zip failed (id ->
    why)."""
    today = today or date.today().strftime("%Y%m%d")
    # Read once for the guards and the stop aliases: about a second on the
    # release index. Only what they need is kept while the feeds load.
    idx = read_index(out)
    previous = _previous(out, today, idx)
    baseline = alias_baseline(idx)
    del idx
    allow = allowed_missing()
    parts, failed, expired, rejected, refused, carried = collect_parts(
        base, volan_zip, today=today, zips=zips, out=out, previous=previous, allow=allow, carry=carry
    )
    guard_feeds(out, parts, allow, expired=expired, today=today, previous=previous)
    # guard_feeds is the city index's guard: write_index's overall count
    # would also count trips whose calendar already ended (several feeds end
    # on 31 December) and ignore HUNGARIAN_TRANSPORT_ALLOW_MISSING.
    # The renamed stops first: the old ids left over are aliases of them.
    kept = keep_stop_ids(baseline, parts)
    info = write_index(parts, out, with_operators=True, today=today, guard_total=False,
                       aliases=stop_aliases(baseline, parts))
    info["kept_ids"] = len(kept)
    info["skipped"] = failed
    info["expired"] = expired
    info["rejected"] = rejected
    info["refused"] = refused
    info["carried"] = carried
    return info


def main() -> None:
    base = Path(".")
    volan = Path("volanbusz_gtfs.zip")
    if not volan.exists():
        download_volan(volan)
    carry = (os.environ.get(CARRY_ENV) or "").strip()
    info = build_city_index(base, OUT, volan, carry=Path(carry) if carry else None)
    print("wrote", OUT, info)


if __name__ == "__main__":
    main()
