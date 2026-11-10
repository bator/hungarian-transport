"""Reading a GTFS feed, and the codes every index shares.

One reading for every builder: Home Assistant's local MÁV build
(mav_index), the realtime static train numbers (realtime) and the release
and update scripts (scripts/gtfs_compact.py, which imports this module by
path as build_mav_index imports mav_index). So a feed that builds in a
release builds the same in Home Assistant:

- a member is found whatever its case, in a subfolder too (member);
- every header and value is stripped: an export with a space after each
  comma (" T1") gives the ids the other files use ("T1");
- bytes that are not UTF-8 are read as cp1250 (iso-8859-2 for the five
  bytes cp1250 leaves undefined): Hungarian feeds are one or the other.

Also the train number and suburban line code of a MÁV route name, the
encoded polyline the cards decode, and a feed's feed_version. Standard
library only: it runs inside Home Assistant, in mav_index's own process and
in the scripts.
"""
from __future__ import annotations

import codecs
import csv
import io
import re
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

LINE_CODE_RE = re.compile(r"^[GSZHM]\d{2}$", re.I)


# ---- MÁV route names -------------------------------------------------------


def train_number(name: str) -> str:
    """Train number from a MÁV name: 'IC 856 TÓPART' -> '856'. A suburban
    code like 'G43' has none."""
    s = (name or "").strip()
    if not s or LINE_CODE_RE.fullmatch(s):
        return ""
    m = re.search(r"(?<!\d)(\d{3,5})(?!\d)", s)
    if not m:
        return ""
    return m.group(1).lstrip("0") or m.group(1)


def line_code(short: str, long_name: str = "") -> str:
    """Suburban product code (G43, S30), not a train number."""
    for raw in (short, long_name):
        s = (raw or "").strip().upper()
        if LINE_CODE_RE.fullmatch(s):
            return s
    return ""


# ---- encoded polylines -----------------------------------------------------


def encode_signed(value: int) -> str:
    """One coordinate delta of a Google encoded polyline."""
    value = ~(value << 1) if value < 0 else (value << 1)
    chars: list[str] = []
    while value >= 0x20:
        chars.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chars.append(chr(value + 63))
    return "".join(chars)


def encode_polyline(pts: Iterable[tuple[float, float]], precision: int = 5) -> str:
    """Google encoded polyline; the card's decodePolyline reads precision 5."""
    factor = 10 ** precision
    plat = plng = 0
    out: list[str] = []
    for lat, lng in pts:
        ilat, ilng = int(round(float(lat) * factor)), int(round(float(lng) * factor))
        out.append(encode_signed(ilat - plat))
        out.append(encode_signed(ilng - plng))
        plat, plng = ilat, ilng
    return "".join(out)


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    factor = 10 ** precision
    coords: list[tuple[float, float]] = []
    index = lat = lng = 0
    text = encoded or ""
    while index < len(text):
        for xy in (0, 1):
            result = shift = 0
            while True:
                byte = ord(text[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if xy == 0:
                lat += delta
            else:
                lng += delta
        coords.append((lat / factor, lng / factor))
    return coords


# ---- members and rows ------------------------------------------------------
#
# A feed here is anything with namelist() and open(name) -> a binary file:
# a zipfile.ZipFile, a GtfsDir or a Feed.


def _legacy_bytes(err: UnicodeError) -> tuple[str, int]:
    """Decode error handler: bytes that are not UTF-8 are read as cp1250
    (iso-8859-2 for the five bytes cp1250 leaves undefined), so a streamed
    member needs no encoding pre-pass."""
    if not isinstance(err, UnicodeDecodeError):
        raise err
    out = []
    for b in err.object[err.start:err.end]:
        try:
            out.append(bytes([b]).decode("cp1250"))
        except UnicodeDecodeError:
            out.append(bytes([b]).decode("iso-8859-2"))
    return "".join(out), err.end


codecs.register_error("gtfs_legacy", _legacy_bytes)


class GtfsDir:
    """Unzipped GTFS folder (pfaedle output). Same read/namelist surface as ZipFile."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def namelist(self) -> list[str]:
        return [p.name for p in self.path.iterdir() if p.is_file()]

    def read(self, name: str) -> bytes:
        return (self.path / name).read_bytes()

    def open(self, name: str):
        return (self.path / name).open("rb")

    def close(self) -> None:
        return None

    def __enter__(self) -> "GtfsDir":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def member(z, name: str) -> str:
    """The real name of member name (lower case, "stops.txt"): the one of
    that name in any case, else the first in a subfolder.
    FileNotFoundError when there is none."""
    wanted = name.lower()
    members = list(z.namelist())
    real = {n.lower(): n for n in members}.get(wanted)
    if real is None:
        for n in members:
            if n.lower().rsplit("/", 1)[-1] == wanted:
                real = n
                break
    if not real:
        raise FileNotFoundError(name)
    return real


def csv_rows(z, real: str) -> Iterator[dict[str, str]]:
    """Rows of member real as dicts, every header and value stripped; a short
    row gets "" for its missing columns, fields past the header are dropped.
    Streamed: stop_times.txt of the Volán feed is several hundred MB.
    csv.reader, not DictReader: as fast, and this runs once per stop_times
    row."""
    with z.open(real) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="gtfs_legacy", newline="")
        reader = csv.reader(text, skipinitialspace=True)
        header = [h.strip() for h in next(reader, [])]
        width = len(header)
        for rec in reader:
            if not rec:
                continue
            vals = [v.strip() for v in rec]
            if len(vals) < width:
                vals += [""] * (width - len(vals))
            yield dict(zip(header, vals))


def open_csv(z, name: str) -> Iterator[dict[str, str]]:
    """csv_rows of member name. A missing member raises FileNotFoundError
    here, not on first iteration."""
    return csv_rows(z, member(z, name))


def feed_version(z) -> str:
    """feed_info.txt's feed_version; "" without one."""
    try:
        for row in open_csv(z, "feed_info.txt"):
            return row.get("feed_version") or ""
    except FileNotFoundError:
        pass
    return ""


class Feed:
    """A GTFS feed in a zip or an unzipped directory (pfaedle's gtfs-out)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._z = zipfile.ZipFile(self.path) if self.path.is_file() else GtfsDir(self.path)

    def close(self) -> None:
        self._z.close()

    def __enter__(self) -> "Feed":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def namelist(self) -> list[str]:
        return self._z.namelist()

    def open(self, name: str):
        return self._z.open(name)

    def names(self) -> set[str]:
        """Member names, lower case, without their folder."""
        return {Path(n).name.lower() for n in self.namelist()}

    def rows(self, name: str) -> Iterator[dict[str, str]]:
        """csv_rows of member name; none when the feed lacks it."""
        try:
            real = member(self, name)
        except FileNotFoundError:
            return iter(())
        return csv_rows(self, real)
