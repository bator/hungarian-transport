#!/usr/bin/env python3
"""Build city-index.json.gz: municipal GTFS + Volán local (non-4-digit) routes."""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path

from gtfs_compact import load_parts, write_index

VOLAN_URL = "https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip"
OUT = Path("city-index.json.gz")
UA = "Hungarian-transport/1.0 (Home Assistant; +https://github.com/bator/hungarian-transport)"

# Debrecen DKV is not published as a public zip. Skip until a URL exists.
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


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)


def collect_parts(base: Path, volan_zip: Path | None = None) -> list[dict]:
    parts: list[dict] = []
    for feed in CITY_FEEDS:
        zpath = base / feed["file"]
        if not zpath.exists():
            print("downloading", feed["id"], feed["url"])
            download(feed["url"], zpath)
        parts.extend(
            load_parts(
                zpath,
                include_all,
                op_id=feed["id"],
                op_name=feed["name"],
                prefix=f"{feed['id']}:",
            )
        )
    if volan_zip and volan_zip.exists():
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
    return [p for p in parts if p.get("t")]


def build_city_index(base: Path, out: Path, volan_zip: Path | None = None) -> dict:
    parts = collect_parts(base, volan_zip)
    if not parts:
        raise RuntimeError("no city GTFS parts")
    return write_index(parts, out, with_operators=True, schema=27)


def main() -> None:
    base = Path(".")
    volan = Path("volanbusz_gtfs.zip")
    if not volan.exists():
        print("downloading", VOLAN_URL)
        download(VOLAN_URL, volan)
    info = build_city_index(base, OUT, volan)
    print("wrote", OUT, info)


if __name__ == "__main__":
    main()
