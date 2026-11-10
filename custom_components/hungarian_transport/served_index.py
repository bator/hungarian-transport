"""The served MÁV index: what an index file is, and whether another replaces it.

www/mav-index.json.gz is the index the cards draw trains from. Local builds
and uploads (mav.py), the daily index from GitHub Pages (mav_daily.py), a
release (HACS unpacks it) and the update script's restore
(scripts/update_volan_index.py) may each replace it, and the timetable
sensors and notifications (timetables.py, timetable_notify.py) tell what it
is. All of them ask this module:

- what an index file is: the feed_version it was built from (read_feed,
  from the head of its gzip, where every index of this integration has its
  "feed", the Volán and city ones too), the date that feed names
  (feed_date), its rank for the same-feed rule (read_rank: pfaedle-fitted
  or not, and its size) and when it was built (built_at);
- whether a candidate of the served index's own feed replaces it
  (replaces: mav_index.same_feed_replaces, against the file), and of
  another feed, whether it is newer (newer_feed) and whether the local feed
  in /config/hungarian-transport loses to it (superseded).

Standard library only: the update script imports it by path, without Home
Assistant, as it imports mav_index.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

try:
    from . import mav_index
except ImportError:  # imported by path (scripts/update_volan_index.py)
    import mav_index  # type: ignore[no-redef]

# mav_index.write puts "feed" right after "v"; the city index joins every
# operator's feed into it, so its head can be long.
FEED_RE = re.compile(rb'"feed":("(?:[^"\\]|\\.)*"|null)')
HEAD_BYTES = 256 * 1024
# The first run of eight digits that is not part of a longer number. MÁV's
# feed_version starts with it ("20261022.M2ChangeID"), a Volán feed has it
# after the operator ("volan:01716.20261102"), a GTFS date is nothing else.
# A run that is no real date leaves the feed undated; a later run does not
# stand in for it, as scripts/mav_pages_site.py checks the first eight
# digits of what it publishes.
DATE_RE = re.compile(r"(?<!\d)\d{8}(?!\d)")

# A build of the local feed before it is made: its kind and size are not
# known, so by the same-feed rule it replaces no index of its feed.
LOCAL_BUILD = mav_index.IndexRank(fitted=None, size=None)


def feed_date(value: str | None) -> date | None:
    """The date a feed_version, or a GTFS date (YYYYMMDD), names; None when
    it names none."""
    match = DATE_RE.search(value or "")
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(), "%Y%m%d").date()
    except ValueError:
        return None


def head_feed(head: bytes) -> str | None:
    """The "feed" in the unpacked head of an index; None when it has none,
    names null or does not parse."""
    match = FEED_RE.search(head)
    if match is None:
        return None
    try:
        feed = json.loads(match.group(1))
    except ValueError:
        return None
    return feed if isinstance(feed, str) else None


def read_feed(path: Path) -> str | None:
    """The feed_version an index file was built from; None when the file is
    missing, is no gzip or names none."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES)
    except (OSError, EOFError, zlib.error):
        return None
    return head_feed(head)


def read_rank(path: Path) -> mav_index.IndexRank | None:
    """An index file's rank for the same-feed rule; None when it does not read."""
    return mav_index.read_rank(path)


def built_at(path: Path) -> float:
    """When an index file was built: its gzip header's mtime, which a copy
    and a HACS unpack keep (mav_index.write stamps the build time), else the
    file's own mtime (the Volán and city indexes are written with none).
    OSError when the file cannot be read."""
    with Path(path).open("rb") as fh:
        head = fh.read(8)
    if len(head) == 8 and head[:2] == b"\x1f\x8b":
        stamp = int.from_bytes(head[4:8], "little")
        if stamp:
            return float(stamp)
    return Path(path).stat().st_mtime


def replaces(new: mav_index.IndexRank, served: Path) -> bool:
    """Whether an index of rank new replaces served, an index of its own
    feed_version: mav_index.same_feed_replaces. One that does not read is
    replaced by any."""
    current = read_rank(served)
    return current is None or mav_index.same_feed_replaces(new, current)


def newer_feed(feed: str | None, than: str | None) -> bool:
    """Whether feed names a later date than the feed than: an undated feed
    is never newer, and any dated one is newer than an undated one."""
    day, other = feed_date(feed), feed_date(than)
    return day is not None and (other is None or day > other)


@dataclass(frozen=True)
class FeedInfo:
    """The local feed's feed_info.txt (first row); empty fields when it has none."""

    version: str = ""
    start: str = ""
    end: str = ""

    @property
    def day(self) -> date | None:
        """How recent the feed is: its feed_version's date, else its start,
        else its end."""
        for value in (self.version, self.start, self.end):
            if (day := feed_date(value)) is not None:
                return day
        return None


def source_info(src: Path) -> FeedInfo:
    """The feed_info.txt of the GTFS zip at src."""
    try:
        with mav_index.Feed(src) as feed:
            for row in feed.rows("feed_info.txt"):
                return FeedInfo(
                    (row.get("feed_version") or "").strip(),
                    (row.get("feed_start_date") or "").strip(),
                    (row.get("feed_end_date") or "").strip(),
                )
    except (OSError, ValueError, zipfile.BadZipFile, csv.Error):
        pass
    return FeedInfo()


def superseded(local: FeedInfo, index_feed: str | None, today: date) -> bool:
    """Whether an index of index_feed beats the local feed (not built over it).

    Only an index that names its feed can, and not one of the local feed's
    own feed_version (the same-feed rule decides that one). It does when the
    local feed has expired (it ends at the end of its end date), or when
    index_feed dates a newer feed. A local feed that cannot be dated keeps
    its place, as does one of the same date.
    """
    if not index_feed or index_feed == local.version:
        return False
    end = feed_date(local.end)
    if end is not None and end < today:
        return True
    mine, theirs = local.day, feed_date(index_feed)
    return mine is not None and theirs is not None and theirs > mine
