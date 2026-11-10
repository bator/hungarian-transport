"""What the timetable indexes in www/ are, for the sensors (sensor.py).

The indexes change through a HACS update (a release ships them), the local
updater script (scripts/update_volan_index.py, which also keeps a durable
copy of each in /config/hungarian-transport), the daily MÁV index
(mav_daily.py) and local MÁV builds and uploads (mav.py). TimetableStatus
reads them in the executor, again only when a file's (mtime, size)
changed, on storage.SIGNAL_TIMETABLES (sent after a daily round, a daily
install and a local build) and every POLL_INTERVAL (the updater script and
HACS send nothing).

The MÁV index (about 1 MB unpacked) is read whole. The Volán and city
indexes (tens of MB unpacked) are streamed once: the feed and the stop
table come from the head, the trips are counted on the way, the city
operators come from the tail; nothing else is kept in memory.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
import stat
import zlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval

from . import mav, mav_index, served_index, storage

_LOGGER = logging.getLogger(__name__)

VOLAN_INDEX = "volan-index.json.gz"
CITY_INDEX = "city-index.json.gz"
POLL_INTERVAL = timedelta(minutes=30)
# A trip row of the Volán and city indexes: [calendar, "name", [stops],
# [times], ...]; no other array in them starts with an integer and a string.
TRIP_RE = re.compile(rb'\[\d{1,9},"')
TRIP_OVERLAP = 16
# The stop table "s" comes before the calendars "c"; this much of the head
# is kept at most while looking for it.
STOPS_KEY = b'"s":['
CALENDARS_KEY = b',"c":['
MAX_HEAD = 32 * 1024 * 1024
# The city index's operator list "o" comes last.
OPERATORS_KEY = b',"o":['
MAX_TAIL = 256 * 1024
CHUNK = 256 * 1024
# A city index of at most this many feeds lists them (feeds attribute).
MAX_LISTED_FEEDS = 10


def _fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return st.st_mtime_ns, st.st_size


def _utc(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def read_mav(path: Path) -> dict[str, Any] | None:
    """The served MÁV index: feed, trains, lines, fitted share; None when
    it is missing or does not read."""
    try:
        raw = path.read_bytes()
        st = path.stat()
        index = json.loads(gzip.decompress(raw))
    except (OSError, EOFError, ValueError, zlib.error):
        return None
    if not isinstance(index, dict):
        return None
    feed = index.get("feed") if isinstance(index.get("feed"), str) else None
    numbers = index.get("n") if isinstance(index.get("n"), dict) else {}
    lines = index.get("l") if isinstance(index.get("l"), dict) else {}
    return {
        "feed": feed,
        "date": served_index.feed_date(feed),
        "trains": len(numbers),
        "lines": len(lines),
        "fitted_share": round(mav_index.fitted_share(index), 4),
        "size": len(raw),
        "mtime": _utc(st.st_mtime),
    }


def scan_index(path: Path, *, operators: bool = False) -> dict[str, Any] | None:
    """A Volán or city index, streamed: its feed, trips, stops, build time
    (served_index.built_at) and, with operators, the operator count from
    the "o" list; None when it does not read."""
    unzip = zlib.decompressobj(16 + zlib.MAX_WBITS)
    head = bytearray()
    head_done = False
    tail = b""
    carry = b""
    trips = 0
    try:
        st = path.stat()
        built = served_index.built_at(path)
        with path.open("rb") as fh:
            while chunk := fh.read(CHUNK):
                data = unzip.decompress(chunk)
                if not head_done:
                    head += data
                    head_done = CALENDARS_KEY in head or len(head) > MAX_HEAD
                if operators:
                    tail = (tail + data)[-MAX_TAIL:]
                buf = carry + data
                limit = len(buf) - TRIP_OVERLAP
                for match in TRIP_RE.finditer(buf):
                    if match.start() >= limit:
                        break
                    trips += 1
                carry = buf[max(limit, 0):]
                if unzip.eof:
                    break
            if not unzip.eof:
                return None
    except (OSError, EOFError, zlib.error):
        return None
    trips += len(TRIP_RE.findall(carry))
    head_bytes = bytes(head[:MAX_HEAD])
    info: dict[str, Any] = {
        "feed": served_index.head_feed(head_bytes),
        "trips": trips,
        "stops": _stops(head_bytes),
        "size": st.st_size,
        "updated_at": _utc(built),
    }
    if operators:
        info["operators"] = _operators(tail)
    return info


def _stops(head: bytes) -> int | None:
    start, end = head.find(STOPS_KEY), head.find(CALENDARS_KEY)
    if start < 0 or end < start:
        return None
    try:
        table, _ = json.JSONDecoder().raw_decode(head[start + len(STOPS_KEY) - 1:end].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return len(table) if isinstance(table, list) else None


def _operators(tail: bytes) -> int | None:
    start = tail.rfind(OPERATORS_KEY)
    if start < 0:
        return None
    try:
        ops, _ = json.JSONDecoder().raw_decode(tail[start + len(OPERATORS_KEY) - 1:].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return len(ops) if isinstance(ops, list) else None


def city_feeds(feed: str | None) -> dict[str, str]:
    """The city index's per-operator feeds ("miskolc:1.3;veszprem:...")."""
    out: dict[str, str] = {}
    for part in (feed or "").split(";"):
        oid, sep, version = part.partition(":")
        if sep and oid:
            out[oid] = version
    return out


class _Reader:
    """The executor side: each index read again only when its file changed."""

    def __init__(self, www: Path, durable: Path) -> None:
        self.www = www
        self.durable = durable
        self._cache: dict[Path, tuple[tuple[int, int], Any]] = {}

    def _cached(self, path: Path, read: Callable[[Path], Any]) -> Any:
        key = _fingerprint(path)
        if key is None:
            self._cache.pop(path, None)
            return None
        hit = self._cache.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        value = read(path)
        # A file replaced while it was read is read again next time.
        if _fingerprint(path) == key:
            self._cache[path] = (key, value)
        return value

    def _durable_same(self, name: str, info: dict[str, Any] | None) -> bool:
        """Whether the updater's durable copy is this index (feed and size)."""
        copy = self.durable / name
        key = _fingerprint(copy)
        if info is None or key is None or key[1] != info["size"]:
            return False
        return self._cached(copy, served_index.read_feed) == info["feed"]

    def read(self) -> dict[str, Any]:
        mav_info = self._cached(self.www / mav.INDEX_NAME, read_mav)
        volan = self._cached(self.www / VOLAN_INDEX, scan_index)
        city = self._cached(self.www / CITY_INDEX, lambda p: scan_index(p, operators=True))
        return {
            "mav": mav_info and {**mav_info, "durable": self._durable_same(mav.INDEX_NAME, mav_info)},
            "volan": volan and {**volan, "durable": self._durable_same(VOLAN_INDEX, volan)},
            "city": city,
        }


class TimetableStatus:
    """The indexes and the daily round as the sensors show them.

    next_run gives the daily updater's next round (None when the daily
    index is turned off)."""

    def __init__(
        self,
        hass: HomeAssistant,
        www: Path,
        *,
        daily_enabled: bool,
        next_run: Callable[[], datetime | None] | None = None,
    ) -> None:
        self._hass = hass
        self._reader = _Reader(www, mav.source_path(hass).parent)
        self.daily_enabled = daily_enabled
        self._next_run = next_run or (lambda: None)
        self.indexes: dict[str, Any] = {"mav": None, "volan": None, "city": None}
        self.record: dict[str, Any] = {}
        self._listeners: list[CALLBACK_TYPE] = []
        self._lock = asyncio.Lock()
        self._again = False

    @property
    def next_run(self) -> datetime | None:
        return self._next_run() if self.daily_enabled else None

    @callback
    def async_add_listener(self, update: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            if update in self._listeners:
                self._listeners.remove(update)

        return remove

    async def async_refresh(self) -> None:
        """Read what changed, then update every sensor. A request during a
        refresh runs one more refresh after it."""
        if self._lock.locked():
            self._again = True
            return
        async with self._lock:
            while True:
                self._again = False
                try:
                    self.indexes = await self._hass.async_add_executor_job(self._reader.read)
                    self.record = dict(await storage.async_load(self._hass))
                except Exception:  # noqa: BLE001 - the next signal or poll tries again
                    _LOGGER.warning("Could not read the timetable indexes", exc_info=True)
                for update in list(self._listeners):
                    update()
                if not self._again:
                    break

    @callback
    def _request(self, *_args: Any) -> None:
        self._hass.async_create_background_task(self.async_refresh(), "hungarian_transport_timetables")

    @callback
    def start(self) -> CALLBACK_TYPE:
        """Refresh on SIGNAL_TIMETABLES and every POLL_INTERVAL until stopped."""
        unsubs = [
            async_dispatcher_connect(self._hass, storage.SIGNAL_TIMETABLES, self._request),
            async_track_time_interval(self._hass, self._request, POLL_INTERVAL),
        ]

        @callback
        def stop() -> None:
            for unsub in unsubs:
                unsub()
            self._listeners.clear()

        return stop
