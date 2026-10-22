#!/usr/bin/env python3
"""Build city-index.json.gz: municipal GTFS + Volán local (non-4-digit) routes."""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path

from gtfs_compact import (
    PLATFORM_SPLIT_M,
    IndexGuardError,
    calendar_end,
    download_feed,
    feed_error,
    feed_urls,
    guard_basis,
    load_env_files,
    load_parts,
    operator_running_trips,
    operator_stats,
    running_trips,
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
        "url": "https://dombovar.ptke.hu/gtfs/",
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


def _load_feed(base: Path, feed: dict, zpath: Path | None = None) -> list[dict]:
    """Parts of one city feed, from zpath when given (a new zip not yet in
    base), else from base. A missing zip is downloaded; when the zip from
    one URL (a keyed override, say) fails to download or parse, the next URL
    is tried, down to the public one."""
    zpath = zpath or base / feed["file"]

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
) -> tuple[list[dict], list[str], dict[str, str], list[str], dict[str, dict[str, str]]]:
    """Operator parts with trips, the feed ids that failed, had none or
    whose calendar already ended, of the last: feed id -> last service
    day, the feed ids whose new zip was rejected, and of those the ones
    rejected for their content: feed id -> {"why": the reason, "until":
    the last service day (YYYYMMDD, "" = unknown) of the zip kept instead}.
    A feed that only lost some operators to an ended calendar (Volán local
    agencies) is neither failed nor ended.

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
    if zips and allow is None:
        allow = allowed_missing()
    allow = allow or set()
    parts: list[dict] = []
    failed: list[str] = []
    expired: dict[str, str] = {}
    rejected: list[str] = []
    refused: dict[str, dict[str, str]] = {}
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
                print(f"warn {feed_error(fid, err)}", file=sys.stderr)
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
    return parts, failed, expired, rejected, refused


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


# What guard_feeds compares a rebuild with: (operator stats, running trips
# per feed, all trips per feed of the operators still running) of the
# previous index.
Previous = tuple[dict[str, tuple[int, str]], dict[str, int], dict[str, int]]


def _previous(out: Path | None, today: str) -> Previous | None:
    """The previous index as guard_feeds sees it; None without one."""
    prev = operator_stats(out) if out is not None else None
    if not prev:
        return None
    live = {op: n for op, n in (operator_running_trips(out, today) or {}).items() if n}
    return prev, _group_trips(live), _group_trips({op: n for op, (n, _e) in prev.items() if op in live})


def _counts(parts: list[dict], today: str) -> tuple[dict[str, int], dict[str, int]]:
    """(running trips, all trips) per feed of parts."""
    running: dict[str, int] = {}
    total: dict[str, int] = {}
    for part in parts:
        op = part["op_id"]
        running[op] = running.get(op, 0) + len(running_trips(part, today))
        total[op] = total.get(op, 0) + len(part["t"])
    return _group_trips(running), _group_trips(total)


def _group_problem(
    group: str,
    previous: Previous,
    after_running: dict[str, int],
    after_all: dict[str, int],
    expired: dict[str, str],
) -> str:
    """Why guard_feeds refuses a rebuild for this feed, or ""."""
    prev, before, before_all = previous
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
        return f"{group} {has} trips vs {had}"
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
    prev, before, _before_all = previous
    after_running, after_all = _counts(parts, today)
    for group, had in sorted(_group_trips({op: n for op, (n, _e) in prev.items()}).items()):
        if group not in before and not after_all.get(group):
            print(f"warn {group}: calendar ended {ymd(_group_end(prev, group))} in the previous index"
                  f" as well; its {had} trips leave the index", file=sys.stderr)
    problems = []
    for group in sorted(before):
        if group in allow or "all" in allow:
            continue
        why = _group_problem(group, previous, after_running, after_all, expired)
        if why:
            problems.append(why)
    if problems:
        raise IndexGuardError(
            f"refusing to replace {out.name}: " + "; ".join(problems)
            + f" (set {ALLOW_MISSING_ENV} to accept)"
        )


def build_city_index(
    base: Path,
    out: Path,
    volan_zip: Path | None = None,
    *,
    today: str | None = None,
    zips: dict[str, Path] | None = None,
) -> dict:
    """Build and write the city index; info["skipped"] lists the feeds left
    out, info["expired"] those of them whose calendar ended (id -> last day),
    info["rejected"] the new zips (zips, see collect_parts) not used and
    info["refused"] those of them rejected for their content (id -> why,
    until)."""
    today = today or date.today().strftime("%Y%m%d")
    # Read once for both: about a second on the release index.
    previous = _previous(out, today)
    allow = allowed_missing()
    parts, failed, expired, rejected, refused = collect_parts(
        base, volan_zip, today=today, zips=zips, out=out, previous=previous, allow=allow
    )
    guard_feeds(out, parts, allow, expired=expired, today=today, previous=previous)
    # guard_feeds is the city index's guard: write_index's overall count
    # would also count trips whose calendar already ended (several feeds end
    # on 31 December) and ignore HUNGARIAN_TRANSPORT_ALLOW_MISSING.
    info = write_index(parts, out, with_operators=True, today=today, guard_total=False)
    info["skipped"] = failed
    info["expired"] = expired
    info["rejected"] = rejected
    info["refused"] = refused
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
