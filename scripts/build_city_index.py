#!/usr/bin/env python3
"""Build city-index.json.gz: municipal GTFS + Volán local (non-4-digit) routes."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from gtfs_compact import download_feed, feed_error, load_parts, resolved_url, write_index

VOLAN_URL = "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"
OUT = Path("city-index.json.gz")

# Debrecen DKV is listed as "Nem publikus" on menetbrand. Skip.
# Weekendbus (Csömör/Pécel) still hosts a zip, but the calendar ended 2025-12-31.
# Feeds that still need a form or login: MÁV, NAP, KEKO.
# Menetbrand /download/<slug> is the public zip when the publisher has no fixed
# filename (Kaposvár), a CAPTCHA (Kiskunhalas), or until a keyed URL is in .env.
# Bahart: official NAP URL via BAHART_GTFS_URL; Transitland / Mobility Database
# as catalog fallback; menetbrand if neither is set.
CITY_FEEDS = [
    {
        "id": "miskolc",
        "name": "Miskolc — MVK",
        "url": "http://gtfs.cdata.hu/mvkzrt.zip",
        "file": "miskolc.zip",
    },
    {
        "id": "pecs",
        "name": "Pécs — Tüke Busz",
        "url": "http://mobilitas.biokom.hu/gtfs",
        "file": "pecs.zip",
    },
    {
        "id": "szeged",
        "name": "Szeged — SZKT",
        "url": "http://szegedimenetrend.hu/google_transit.zip",
        "file": "szeged.zip",
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


def collect_parts(base: Path, volan_zip: Path | None = None) -> list[dict]:
    parts: list[dict] = []
    failed: list[str] = []
    for feed in CITY_FEEDS:
        zpath = base / feed["file"]
        try:
            if not zpath.exists():
                print("downloading", feed["id"])
                download_feed(resolved_url(feed), zpath)
            parts.extend(
                load_parts(
                    zpath,
                    include_all,
                    op_id=feed["id"],
                    op_name=feed["name"],
                    prefix=f"{feed['id']}:",
                )
            )
        except Exception as err:
            failed.append(feed["id"])
            print(f"warn {feed_error(feed['id'], err)}", file=sys.stderr)
    if volan_zip and volan_zip.exists():
        try:
            print("volan local", volan_zip)
            parts.extend(
                load_parts(
                    volan_zip,
                    include_volan_local,
                    split_agencies=True,
                    skip_agency=skip_helykozi,
                    prefix="",
                )
            )
        except Exception as err:
            failed.append("volan-local")
            print(f"warn volan-local: {err}", file=sys.stderr)
    parts = [p for p in parts if p.get("t")]
    if not parts:
        raise RuntimeError("no city GTFS parts" + (f" ({', '.join(failed)})" if failed else ""))
    if failed:
        print("partial city index, skipped:", ", ".join(failed), file=sys.stderr)
    return parts


def build_city_index(base: Path, out: Path, volan_zip: Path | None = None) -> dict:
    parts = collect_parts(base, volan_zip)
    return write_index(parts, out, with_operators=True)


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
