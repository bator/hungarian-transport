#!/usr/bin/env python3
"""Build city-index.json.gz: municipal GTFS + Volán local (non-4-digit) routes."""
from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import NamedTuple

from build_volan_index import download_volan, is_helykozi
from gtfs_compact import (
    PLATFORM_SPLIT_M,
    IndexGuardError,
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
# "stop_ids", "stop_aliases": the static tables a feed that renumbered its
# stops keeps the old ids by (keep_stop_ids, stop_aliases); a feed without
# them keeps its own ids.
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

# Every stop of SZKT's zip has a new id (szeged:SZ_…); the stops of the
# index before it (v1.10.22, the szegedimenetrend.hu zip) were
# szeged:<number>. The trams' and trolleybuses' stops keep those ids
# (keep_stop_ids), from these tables, generated once from that index and
# SZKT's zip (scripts/stop_id_map.py).
# --- stop id tables: generated by scripts/stop_id_map.py, do not edit by hand ---
# szeged: hungarian-transport.zip (sha256 530c2b0f520a4f38) and
# szeged.zip (sha256 0cec3569bac00359, feed 20261102-20261231).
# Feed stop id -> the old id (and stop number) the index gives it.
SZEGED_STOP_IDS = {
    "SZ_1038": "2429618792",
    "SZ_1039": "2429618795",
    "SZ_1145": "2429618808",
    "SZ_1171": "2429618860",
    "SZ_1172": "2429618856",
    "SZ_1482": "2429618847",
    "SZ_1483": "2429618844",
    "SZ_1630": "2431596048",
    "SZ_1631": "2429618937",
    "SZ_1642": "2429618951",
    "SZ_1865": "2429618986",
    "SZ_1866": "2429618993",
    "SZ_1867": "2429619001",
    "SZ_1942": "2429103536",
    "SZ_1943": "2429103537",
    "SZ_2102": "1103186269",
    "SZ_2103": "929138473",
    "SZ_2183": "2429619139",
    "SZ_2289": "2429619149",
    "SZ_2290": "2429619146",
    "SZ_2678": "2429618684",
    "SZ_2679": "2429618688",
    "SZ_2682": "1403707798",
    "SZ_2683": "1111400588",
    "SZ_2684": "904956869",
    "SZ_2705": "2429619035",
    "SZ_2706": "2429619038",
    "SZ_2714": "2429619158",
    "SZ_273": "2429618681",
    "SZ_282": "1403707769",
    "SZ_283": "2430584853",
    "SZ_320": "1111400526",
    "SZ_330": "2429618713",
    "SZ_331": "2429618709",
    "SZ_405": "1704188810",
    "SZ_406": "2429618730",
    "SZ_419": "2429619064",
    "SZ_437": "2429619152",
    "SZ_560": "1418196707",
    "SZ_561": "1111400613",
    "SZ_578": "1886954398",
    "SZ_579": "1886954386",
    "SZ_62196": "1103186211",
    "SZ_62197": "1103186226",
    "SZ_62241": "1103186207",
    "SZ_62242": "1103186251",
    "SZ_62243": "1403707874",
    "SZ_62244": "1403707871",
    "SZ_62245": "1103186283",
    "SZ_62246": "1103186254",
    "SZ_62248": "1103186260",
    "SZ_62249": "1103186215",
    "SZ_62250": "1403708073",
    "SZ_62251": "1099501784",
    "SZ_62253": "1099501779",
    "SZ_62254": "2429618759",
    "SZ_62256": "1403707920",
    "SZ_62259": "1111400577",
    "SZ_62262": "1403707911",
    "SZ_62265": "1111400607",
    "SZ_62266": "1111400548",
    "SZ_62267": "1111400514",
    "SZ_62268": "1111400578",
    "SZ_62269": "1403707906",
    "SZ_62270": "1403707903",
    "SZ_62277": "1403707945",
    "SZ_62278": "1403707935",
    "SZ_62279": "1403707909",
    "SZ_62280": "938209381",
    "SZ_62281": "2431596050",
    "SZ_62282": "1119053202",
    "SZ_62283": "1403707882",
    "SZ_62284": "1403707878",
    "SZ_62285": "1409624814",
    "SZ_62286": "1409624818",
    "SZ_62287": "1403707830",
    "SZ_62288": "1403707829",
    "SZ_62289": "2401194680",
    "SZ_62290": "1700400484",
    "SZ_62291": "1700400133",
    "SZ_62296": "2429103555",
    "SZ_62298": "2429618674",
    "SZ_62299": "2429618678",
    "SZ_62305": "2429619168",
    "SZ_62306": "2429619120",
    "SZ_62307": "2429619123",
    "SZ_62309": "2429618827",
    "SZ_62310": "2429618892",
    "SZ_62311": "2295508437",
    "SZ_62312": "2295509593",
    "SZ_62313": "2430584917",
    "SZ_62314": "2429619007",
    "SZ_62315": "2429619004",
    "SZ_62316": "2429618767",
    "SZ_62320": "2429618853",
    "SZ_62321": "2429618850",
    "SZ_62326": "1403707926",
    "SZ_62327": "1403707931",
    "SZ_62328": "904899621",
    "SZ_62329": "1403707852",
    "SZ_62330": "904899636",
    "SZ_62331": "1111400551",
    "SZ_62332": "1111400508",
    "SZ_62338": "1403707775",
    "SZ_62339": "1403707791",
    "SZ_62340": "1403707847",
    "SZ_62341": "1403707843",
    "SZ_62342": "1403707820",
    "SZ_62347": "2429618917",
    "SZ_62348": "2429618914",
    "SZ_62355": "2429103596",
    "SZ_62356": "2196056160",
    "SZ_62361": "1907917121",
    "SZ_62362": "1886954384",
    "SZ_62363": "2429619048",
    "SZ_62364": "2429619051",
    "SZ_62365": "2429618805",
    "SZ_62366": "2429619187",
    "SZ_62367": "1103186265",
    "SZ_62368": "1103186236",
    "SZ_62370": "1403707856",
    "SZ_62371": "1111400513",
    "SZ_62372": "1111400621",
    "SZ_62373": "410221061",
    "SZ_62374": "1111400623",
    "SZ_62375": "2429618760",
    "SZ_62376": "1111400632",
    "SZ_62377": "2429618964",
    "SZ_62378": "2429618966",
    "SZ_62380": "2429618970",
    "SZ_62381": "2429618928",
    "SZ_62383": "2429618667",
    "SZ_62384": "2429618670",
    "SZ_62385": "2429619044",
    "SZ_62386": "2429618658",
    "SZ_62387": "30766172",
    "SZ_62388": "2429618732",
    "SZ_62389": "1704188829",
    "SZ_62394": "2429619191",
    "SZ_62396": "2429619054",
    "SZ_62397": "2429619060",
    "SZ_62398": "1111400538",
    "SZ_62399": "904956866",
    "SZ_62400": "2429618946",
    "SZ_62401": "2429618940",
    "SZ_62405": "2429619129",
    "SZ_62406": "2429619126",
    "SZ_62408": "2429618663",
    "SZ_62411": "902426436",
    "SZ_62412": "1403707868",
    "SZ_62413": "1403707796",
    "SZ_62414": "1403707793",
    "SZ_62416": "2429618706",
    "SZ_62418": "1403709765",
    "SZ_62419": "1886954382",
    "SZ_62420": "1886954381",
    "SZ_62428": "2431596045",
    "SZ_62438": "2630284945",
    "SZ_62442": "1886954376",
    "SZ_62443": "1907916566",
    "SZ_62444": "1907916563",
    "SZ_62445": "2429619194",
    "SZ_62446": "1403707816",
    "SZ_73799": "2429618870",
    "SZ_73800": "2429618874",
    "SZ_878": "1103186257",
    "SZ_989": "2429618779",
    "SZ_990": "2429618776",
    "SZ_993": "2429618789",
    "SZ_994": "2429618786",
}
# Old id left over -> the feed stop it is an alias of.
SZEGED_STOP_ALIASES = {
    "1704188813": "SZ_2714",
    "2429618924": "SZ_62381",
    "2429619154": "SZ_437",
    "9817227564": "SZ_1867",
}
# --- end of the generated stop id tables ---

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
        "stop_ids": SZEGED_STOP_IDS,
        "stop_aliases": SZEGED_STOP_ALIASES,
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
        keep_stop_ids(parts, feed)
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


# A feed that renumbers its stops (Szeged's SZKT zip gave every stop a new
# id) would leave the cards saved with the old ids behind. Such a feed names
# a static table in CITY_FEEDS, generated once from the last index before
# the renumbering (scripts/stop_id_map.py): "stop_ids", feed stop id -> the
# old id, without the feed's prefix. keep_stop_ids gives those stops the
# old id and stop number back when the feed loads (a card of any version
# matches a saved stop by the number in its id) and keeps the feed's own
# id, which realtime feeds name the stop by, as the stop row's fourth
# element (write_index writes it as the fifth, after the operator).
# "stop_aliases", old id -> feed stop id, are the old ids left over (two old
# stops that became one), written as the index's "al" (stop_aliases, read
# by gtfs.js volanMatchStop). Neither depends on the index in place: every
# build (release, weekly Pages, the local updater) gives the same stops the
# same ids, a build without the feed loses nothing, and the tables do not
# grow. Feeds without a table keep the feed's ids.


def keep_stop_ids(parts: list[dict], feed: dict) -> int:
    """Apply feed's "stop_ids" table to its parts' stop rows, in place: a
    listed stop takes the old id and stop number and keeps the feed's id
    as the row's fourth element. A stop whose old id the feed has given
    another stop itself keeps its own. Returns how many took an old id;
    warns when less than half of the table is still in the feed (it
    renumbered again: regenerate the table)."""
    table: dict[str, str] = feed.get("stop_ids") or {}
    if not table:
        return 0
    prefix = f"{feed['id']}:"
    own = {str(row[0]) for part in parts for row in part["s"]}
    listed = kept = 0
    for part in parts:
        for row in part["s"]:
            sid = str(row[0])
            old = table.get(sid.removeprefix(prefix)) if sid.startswith(prefix) else None
            if old is None:
                continue
            listed += 1
            if prefix + old not in own:
                row[:] = [prefix + old, row[1], old, sid]
                kept += 1
    if listed * 2 < len(table):
        print(f"warn {feed['id']}: only {listed} of the {len(table)} stops of its stop id table are in the"
              " feed; regenerate it (scripts/stop_id_map.py)", file=sys.stderr)
    return kept


def stop_aliases(parts: list[dict]) -> dict[str, str]:
    """Old stop id -> the id in parts of the stop it is an alias of, from the
    "stop_aliases" tables of CITY_FEEDS, for write_index's "al". The stop is
    found by the feed's id (a row's fourth element once it kept an old id),
    so a part carried from an earlier index (CARRY_ENV) resolves too; an
    alias whose stop the parts lack is left out."""
    out: dict[str, str] = {}
    for feed in CITY_FEEDS:
        table: dict[str, str] = feed.get("stop_aliases") or {}
        if not table:
            continue
        prefix = f"{feed['id']}:"
        at: dict[str, str] = {}
        for part in parts:
            if part["op_id"] == feed["id"]:
                for row in part["s"]:
                    at.setdefault(str(row[3]) if len(row) > 3 and row[3] else str(row[0]), str(row[0]))
        for old, fid in table.items():
            if prefix + fid in at:
                out[prefix + old] = at[prefix + fid]
    return out


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
    # Read once for the guards: about a second on the release index. Only
    # what they need is kept while the feeds load.
    previous = _previous(out, today, read_index(out))
    allow = allowed_missing()
    parts, failed, expired, rejected, refused, carried = collect_parts(
        base, volan_zip, today=today, zips=zips, out=out, previous=previous, allow=allow, carry=carry
    )
    guard_feeds(out, parts, allow, expired=expired, today=today, previous=previous)
    # guard_feeds is the city index's guard: write_index's overall count
    # would also count trips whose calendar already ended (several feeds end
    # on 31 December) and ignore HUNGARIAN_TRANSPORT_ALLOW_MISSING.
    # The stops that kept an old id (keep_stop_ids, when the feeds loaded).
    kept = sum(1 for part in parts for row in part["s"] if len(row) > 3)
    info = write_index(parts, out, with_operators=True, today=today, guard_total=False,
                       aliases=stop_aliases(parts))
    info["kept_ids"] = kept
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
