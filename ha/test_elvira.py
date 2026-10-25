#!/usr/bin/env python3
"""Unit checks for ELVIRA origin overlay. No live IK_API calls."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

# Load elvira.py as part of its package (it imports .cache) without running the
# package __init__, which needs Home Assistant.
_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "hungarian_transport"
_pkg = types.ModuleType("hungarian_transport")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules["hungarian_transport"] = _pkg
# elvira.py needs aiohttp only for its timeout and error types; `npm test`
# runs this file with a bare interpreter, so stand in for it when absent.
try:
    import aiohttp  # noqa: F401
except ImportError:
    _aiohttp = types.ModuleType("aiohttp")
    _aiohttp.ClientTimeout = lambda **kw: kw
    _aiohttp.ClientError = type("ClientError", (Exception,), {})
    sys.modules["aiohttp"] = _aiohttp
from hungarian_transport.elvira import (  # noqa: E402
    ELVIRA_DOWN_MAX,
    ELVIRA_DOWN_TTL,
    ELVIRA_ROW_CAP,
    _log_outage,
    STATIONINFO_MAX_COUNT,
    parse_elvira_services,
    rows_from_stationinfo,
    service_days,
)

import zoneinfo


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "ok" if ok else "FAIL"
    extra = f" {detail}" if detail and not ok else ""
    print(f"{mark}  {name}{extra}")
    return ok


def svc(**kwargs):
    return kwargs


failed = 0

if not check(
    "StationInfo maxCount is 500 (Kelenföld evening origin overlay)",
    STATIONINFO_MAX_COUNT == "500",
    repr(STATIONINFO_MAX_COUNT),
):
    failed += 1
if not check(
    "Python row cap matches maxCount (evening overlay not sliced at 200)",
    ELVIRA_ROW_CAP == int(STATIONINFO_MAX_COUNT) == 500,
    repr(ELVIRA_ROW_CAP),
):
    failed += 1

if not check(
    "#3880 code is helyjegy kötelező",
    parse_elvira_services({"services": [svc(code="3880", description="")]})["booking"],
):
    failed += 1
if not check(
    "SZOLGALTATAS#3880 key is helyjegy kötelező",
    parse_elvira_services({"services": [svc(key="SZOLGALTATAS#3880")]})["booking"],
):
    failed += 1
if not check(
    "Helyjegy váltása kötelező text still matches",
    parse_elvira_services({
        "services": [svc(description="Helyjegy váltása kötelező")],
    })["booking"],
):
    failed += 1
if not check(
    "#94345 is not required booking",
    not parse_elvira_services({
        "services": [svc(code="94345", description="Helyjegy váltható.")],
    })["booking"],
):
    failed += 1
if not check(
    "13880 does not false-positive as 3880",
    not parse_elvira_services({"services": [svc(code="13880")]})["booking"],
):
    failed += 1

if not check(
    "#8163379 marks the national pass (Országbérlet)",
    parse_elvira_services({"services": [svc(code="SZOLGALTATAS#8163379", description="Országbérlettel igénybe vehető")]})["nationalPass"],
):
    failed += 1
if not check(
    "a train without the pass service is not marked",
    not parse_elvira_services({"services": [svc(code="SZOLGALTATAS#3880", description="Helyjegy váltása kötelező")]})["nationalPass"],
):
    failed += 1
if not check(
    "#8586013 marks the reservation-free domestic section",
    parse_elvira_services({"services": [svc(code="SZOLGALTATAS#8586013", description="Belföldi utazásra a kijelölt viszonylaton helyjegy váltása nélkül igénybe vehető 2. kocsiosztályon.")]})["reservationFreeSection"],
):
    failed += 1
if not check(
    "Kerékpár nem szállítható does not allow bikes",
    not parse_elvira_services({"services": [svc(code="SZOLGALTATAS#3857", description="Kerékpár nem szállítható")]})["bikesAllowed"],
):
    failed += 1
if not check(
    "Kerékpár a megjelölt helyen szállítható still allows bikes",
    parse_elvira_services({"services": [svc(code="SZOLGALTATAS#8595413", description="Kerékpár a megjelölt helyen szállítható.")]})["bikesAllowed"],
):
    failed += 1

tz = zoneinfo.ZoneInfo("Europe/Budapest")
now = datetime(2026, 9, 30, 17, 20, tzinfo=tz)
latest = now + timedelta(minutes=180)
start = now + timedelta(minutes=40)
start_iso = start.strftime("%Y-%m-%dT%H:%M:%S")
gocsej = {
    "trainId": "2975814",
    "code": "966",
    "name": "GÖCSEJ",
    "start": start_iso,
    "actualOrEstimatedStart": start_iso,
    "startTrack": "6",
    "endStation": {"name": "Szombathely"},
    "services": [{"code": "3880", "description": "Helyjegy váltása kötelező"}],
}
gemenc = {
    "trainId": "1",
    "code": "800",
    "name": "GEMENC",
    "start": (now + timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%S"),
    "startTrack": "3",
    "endStation": {"name": "Baja"},
    "services": [{"code": "3880"}],
}
g43 = {
    "trainId": "2",
    "code": "3511",
    "name": "G43",
    "start": (now + timedelta(minutes=55)).strftime("%Y-%m-%dT%H:%M:%S"),
    "startTrack": "5",
    "endStation": {"name": "Budapest-Déli"},
    "services": [{"code": "94345", "description": "Helyjegy váltható."}],
}

rows = rows_from_stationinfo([gocsej, gemenc, g43], [], tz, now, latest, "Kelenföld")
by_num = {r["trainNumber"]: r for r in rows}

# An odd "start" (not a string) must not crash the whole board: the
# estimated start identifies the run instead.
odd = dict(g43, start={"x": 1}, actualOrEstimatedStart=g43["start"])
if not check("a non-string start field is skipped, not a TypeError",
             [r["trainNumber"] for r in rows_from_stationinfo([odd], [], tz, now, latest, "Kelenföld")] == ["3511"]):
    failed += 1

if not check("origin rows emit without dest StationInfo", len(rows) == 3):
    failed += 1
if not check(
    "GÖCSEJ booking true and destHit false when dest board empty",
    by_num.get("966", {}).get("booking") is True
    and by_num.get("966", {}).get("nationalPass") is False
    and by_num.get("966", {}).get("destHit") is False
    and by_num.get("966", {}).get("travel") is None
    and by_num.get("966", {}).get("platform") == "6",
    repr(by_num.get("966")),
):
    failed += 1
if not check(
    "G43 does not get booking from #94345",
    by_num.get("3511", {}).get("booking") is False,
    repr(by_num.get("3511")),
):
    failed += 1

dest_arr = [{
    "trainId": "2975814",
    "code": "966",
    "arrive": (start + timedelta(minutes=62)).strftime("%Y-%m-%dT%H:%M:%S"),
}]
hit_rows = rows_from_stationinfo([gocsej], dest_arr, tz, now, latest, "Kelenföld")
if not check(
    "dest hit still fills travel when dest board has the train",
    len(hit_rows) == 1
    and hit_rows[0]["destHit"] is True
    and hit_rows[0]["travel"] == 62
    and hit_rows[0]["booking"] is True,
    repr(hit_rows[:1]),
):
    failed += 1

morning = now.replace(hour=8, minute=10, second=0, microsecond=0)
stale_dest = [{
    "trainId": "morning-other",
    "code": "966",
    "arrive": morning.strftime("%Y-%m-%dT%H:%M:%S"),
}]
stale_rows = rows_from_stationinfo([gocsej], stale_dest, tz, now, latest, "Kelenföld")
if not check(
    "past dest code match keeps origin row without destHit",
    len(stale_rows) == 1
    and stale_rows[0]["trainNumber"] == "966"
    and stale_rows[0]["destHit"] is False
    and stale_rows[0]["travel"] is None
    and stale_rows[0]["booking"] is True
    and stale_rows[0]["platform"] == "6",
    repr(stale_rows[:1]),
):
    failed += 1

evening = datetime(2026, 9, 30, 22, 30, tzinfo=tz)
if not check(
    "look-ahead past midnight also asks tomorrow's board",
    [d.isoformat() for d in service_days(evening, evening + timedelta(minutes=180))]
    == ["2026-09-30", "2026-10-01"],
):
    failed += 1
night = datetime(2026, 10, 1, 0, 40, tzinfo=tz)
if not check(
    "after midnight yesterday's board still holds delayed trains",
    [d.isoformat() for d in service_days(night, night + timedelta(minutes=60))]
    == ["2026-09-30", "2026-10-01"],
):
    failed += 1
if not check(
    "daytime asks only today's board",
    [d.isoformat() for d in service_days(now, latest)] == ["2026-09-30"],
):
    failed += 1

late_start = evening + timedelta(minutes=50)
late_train = dict(
    gocsej,
    start=late_start.strftime("%Y-%m-%dT%H:%M:%S"),
    actualOrEstimatedStart=late_start.strftime("%Y-%m-%dT%H:%M:%S"),
)
two_runs = [
    {"trainId": "morning-run", "code": "966",
     "arrive": evening.replace(hour=9).strftime("%Y-%m-%dT%H:%M:%S")},
    {"trainId": "night-run", "code": "966",
     "arrive": (late_start + timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%S")},
]
cross = rows_from_stationinfo(
    [late_train], two_runs, tz, evening, evening + timedelta(minutes=180), "Szombathely"
)
if not check(
    "repeated train code matches the run arriving after midnight",
    len(cross) == 1 and cross[0]["destHit"] is True and cross[0]["travel"] == 70,
    repr(cross[:1]),
):
    failed += 1

delayed = dict(
    gocsej,
    trainId="",
    start=(night - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%S"),
    actualOrEstimatedStart=(night + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S"),
)
after_midnight = rows_from_stationinfo(
    [delayed, delayed], [], tz, night, night + timedelta(minutes=60), ""
)
if not check(
    "delayed train from yesterday's board is listed once with its delay",
    len(after_midnight) == 1 and after_midnight[0]["delay"] == 55 * 60,
    repr(after_midnight),
):
    failed += 1

# 2026-10-25 03:00 CEST becomes 02:00 CET. A night train leaving 01:50 CEST
# and arriving 03:20 CET travels 150 minutes, not the 90 the wall clocks show.
dst_night = datetime(2026, 10, 25, 1, 40, tzinfo=tz)
dst_train = dict(
    gocsej,
    trainId="dst-night",
    code="40400",
    start="2026-10-25T01:50:00",
    actualOrEstimatedStart="2026-10-25T01:55:00+02:00",
)
dst_arr = [{"trainId": "dst-night", "code": "40400", "arrive": "2026-10-25T03:20:00"}]
dst_rows = rows_from_stationinfo(
    [dst_train], dst_arr, tz, dst_night, dst_night + timedelta(minutes=180), "Szeged"
)
if not check(
    "travel time and delay across the autumn clock change are real minutes",
    len(dst_rows) == 1
    and dst_rows[0]["travel"] == 145
    and dst_rows[0]["delay"] == 5 * 60
    and dst_rows[0]["dep"] == int(datetime(2026, 10, 24, 23, 55, tzinfo=zoneinfo.ZoneInfo("UTC")).timestamp()),
    repr(dst_rows),
):
    failed += 1
# 02:30 CET is after 02:40 CEST, although its wall clock is earlier: a train
# that left at 02:40 CEST (00:40 UTC) is gone by 02:30 CET (01:30 UTC).
after_change = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=tz)
left = dict(gocsej, trainId="left", code="40402", start="2026-10-25T02:40:00+02:00")
left["actualOrEstimatedStart"] = left["start"]
due = dict(gocsej, trainId="due", code="40404", start="2026-10-25T02:40:00+01:00")
due["actualOrEstimatedStart"] = due["start"]
gone_rows = rows_from_stationinfo(
    [left, due], [], tz, after_change, after_change + timedelta(minutes=60), ""
)
if not check(
    "departure before now in the repeated hour is dropped, the one after kept",
    [r["trainNumber"] for r in gone_rows] == ["40404"],
    repr(gone_rows),
):
    failed += 1

# A train number repeats every day. Train 2345 runs the other way: it passes
# the destination at 17:10 and the origin at 17:40. Tomorrow's 17:10 at the
# destination (tomorrow's board is asked for in the evening) is not its
# arrival: no destHit, no 23.5-hour travel time.
_tz = zoneinfo.ZoneInfo("Europe/Budapest")
_eve = datetime(2026, 10, 21, 17, 0, tzinfo=_tz)
_back = rows_from_stationinfo(
    [{"trainId": "T-today", "code": "2345", "start": _eve.replace(minute=40).isoformat()}],
    [
        {"trainId": "T-today", "code": "2345", "arrive": _eve.replace(minute=10).isoformat()},
        {"trainId": "T-tomorrow", "code": "2345",
         "arrive": (_eve + timedelta(days=1)).replace(minute=10).isoformat()},
    ],
    _tz, _eve, _eve + timedelta(minutes=480),
)
if not check(
    "the same train number tomorrow is not this run's arrival (opposite direction)",
    [(r["destHit"], r["travel"]) for r in _back] == [(False, None)],
    repr(_back),
):
    failed += 1
_fwd = rows_from_stationinfo(
    [{"trainId": "A", "code": "2346", "start": _eve.replace(minute=40).isoformat()}],
    [
        {"trainId": "B", "code": "2346", "arrive": _eve.replace(hour=19, minute=5).isoformat()},
        {"trainId": "C", "code": "2346",
         "arrive": (_eve + timedelta(days=1)).replace(hour=19, minute=5).isoformat()},
    ],
    _tz, _eve, _eve + timedelta(minutes=480),
)
if not check(
    "the code fallback still pairs today's arrival (85 min), not tomorrow's",
    [(r["destHit"], r["travel"]) for r in _fwd] == [(True, 85)],
    repr(_fwd),
):
    failed += 1

# O-10: the "station is down" memory is bounded in time and in size.
import logging  # noqa: E402


class _Hass:
    def __init__(self):
        self.data = {}


class _Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


_rec = _Records()
_log = logging.getLogger("hungarian_transport.elvira")
_log.addHandler(_rec)
_log.setLevel(logging.INFO)
hass = _Hass()
boom = RuntimeError("timeout")
_log_outage(hass, "005510009", boom, now=0.0)
_log_outage(hass, "005510009", boom, now=45.0)
down = hass.data["hungarian_transport"]["elvira_down_since"]
if not check("a failing station is warned about once, not every poll",
             len([x for x in _rec.lines if "unavailable" in x]) == 1 and list(down) == ["005510009"], repr(_rec.lines)):
    failed += 1
_log_outage(hass, "005510009", boom, now=ELVIRA_DOWN_TTL + 1.0)
if not check("after ELVIRA_DOWN_TTL a station that never answered is forgotten and warned about again",
             len([x for x in _rec.lines if "unavailable" in x]) == 2 and down["005510009"] == ELVIRA_DOWN_TTL + 1.0,
             repr((_rec.lines, down))):
    failed += 1
_log_outage(hass, "005510009", None, now=ELVIRA_DOWN_TTL + 50.0)
if not check("an answer clears it with one line", "005510009" not in down and "answers again" in _rec.lines[-1],
             repr((_rec.lines, down))):
    failed += 1
_log_outage(hass, "008000001", boom, now=100.0)
_log_outage(hass, "008000002", None, now=ELVIRA_DOWN_TTL + 100.0)
if not check("stale entries go even when only other stations are polled", "008000001" not in down, repr(down)):
    failed += 1
for i in range(ELVIRA_DOWN_MAX + 10):
    _log_outage(hass, f"0090{i:05d}", boom, now=200_000.0 + i)
if not check("at most ELVIRA_DOWN_MAX stations are remembered, the oldest dropped first",
             len(down) == ELVIRA_DOWN_MAX and "009000000" not in down and f"0090{ELVIRA_DOWN_MAX + 9:05d}" in down,
             repr(len(down))):
    failed += 1
_log.removeHandler(_rec)

if failed:
    sys.exit(1)
print("elvira origin overlay tests passed")
