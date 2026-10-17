#!/usr/bin/env python3
"""Compact a GTFS zip into the JSON the Lovelace card decompresses."""
from __future__ import annotations

import csv
import gzip
import io
import ipaddress
import json
import os
import re
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

RoutePred = Callable[[str, str, str], bool]

UA = "Hungarian-transport/1.0 (Home Assistant; +https://github.com/bator/hungarian-transport)"
MAX_MEMBER = 500 * 1024 * 1024
MAX_DOWNLOAD = 200 * 1024 * 1024
# 28: trip rows carry GTFS route_type as a fifth element.
INDEX_SCHEMA = 28
_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "unix",
        "supervisor",
        "hassio",
        "homeassistant",
        "127.0.0.1",
        "0.0.0.0",
        "::1",
        "::",
    }
)
_ENV_LOADED = False
_MDB_ACCESS: str | None = None


def _parse_env_file(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        os.environ[key] = val


def load_env_files() -> None:
    """Load gitignored .env if present. Never override already-set variables."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    extra = (os.environ.get("HUNGARIAN_TRANSPORT_ENV") or "").strip()
    here = Path(__file__).resolve().parent
    candidates = []
    if extra:
        candidates.append(Path(extra))
    candidates.extend(
        [
            here.parent / ".env",
            Path("/config/hungarian-transport/.env"),
            Path("/mnt/data/supervisor/homeassistant/hungarian-transport/.env"),
        ]
    )
    for path in candidates:
        if path.is_file():
            _parse_env_file(path)


def feed_error(feed_id: str, err: BaseException) -> str:
    """Log a download failure without echoing keyed NAP / catalog URLs."""
    return f"{feed_id}: {type(err).__name__}"


def assert_public_http_url(url: str) -> None:
    """Reject file://, loopback, and RFC1918 targets before urlopen."""
    parsed = urllib.parse.urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked url scheme {parsed.scheme or 'none'}")
    if parsed.username or parsed.password:
        raise ValueError("blocked url userinfo")
    host = (parsed.hostname or "").strip("[]").lower()
    if not host:
        raise ValueError("blocked url host")
    if (
        host in _BLOCKED_HOSTS
        or host.endswith(".local")
        or host.endswith(".internal")
        or host.endswith(".localhost")
    ):
        raise ValueError("blocked url host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError("blocked url host")


def _safe_http_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    try:
        assert_public_http_url(text)
    except ValueError:
        return ""
    return text


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _urlopen(req: urllib.request.Request, timeout: int = 120):
    opener = urllib.request.build_opener(_PublicRedirectHandler)
    return opener.open(req, timeout=timeout)


def _http_json(url: str, *, method: str = "GET", data: dict | None = None, headers: dict | None = None, timeout: int = 20):
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _transitland_url(query: str) -> str:
    key = (os.environ.get("TRANSITLAND_API_KEY") or "").strip()
    if not key or not query:
        return ""
    q = urllib.parse.quote(query)
    data = _http_json(
        f"https://transit.land/api/v2/rest/feeds?search={q}&limit=8",
        headers={"apikey": key},
    )
    feeds = (data or {}).get("feeds") if isinstance(data, dict) else data
    if not isinstance(feeds, list):
        return ""
    needle = query.casefold()
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        oid = str(feed.get("onestop_id") or "")
        name = str(feed.get("name") or "")
        if needle not in oid.casefold() and needle not in name.casefold():
            continue
        urls = feed.get("urls") if isinstance(feed.get("urls"), dict) else {}
        current = _safe_http_url(urls.get("static_current") or "")
        if current:
            return current
    return ""


def _mdb_access() -> str:
    global _MDB_ACCESS
    if _MDB_ACCESS:
        return _MDB_ACCESS
    refresh = (os.environ.get("MOBILITY_DATABASE_REFRESH_TOKEN") or "").strip()
    if not refresh:
        return ""
    data = _http_json(
        "https://api.mobilitydatabase.org/v1/tokens",
        method="POST",
        data={"refresh_token": refresh},
    )
    token = (data or {}).get("access_token") if isinstance(data, dict) else ""
    _MDB_ACCESS = (token or "").strip()
    return _MDB_ACCESS


def _mdb_url(query: str) -> str:
    token = _mdb_access()
    if not token or not query:
        return ""
    data = _http_json(
        "https://api.mobilitydatabase.org/v1/gtfs_feeds?limit=100&offset=0&country_code=HU",
        headers={"Authorization": f"Bearer {token}"},
    )
    feeds = data if isinstance(data, list) else []
    needle = query.casefold()
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        provider = str(feed.get("provider") or "")
        fid = str(feed.get("id") or "")
        if needle not in provider.casefold() and needle not in fid.casefold():
            continue
        latest = feed.get("latest_dataset") if isinstance(feed.get("latest_dataset"), dict) else {}
        hosted = (latest.get("hosted_url") or "").strip()
        if hosted:
            return hosted
        info = feed.get("source_info") if isinstance(feed.get("source_info"), dict) else {}
        producer = (info.get("producer_url") or "").strip()
        if producer:
            return producer
    return ""


def catalog_url(query: str) -> str:
    """Latest zip URL from Mobility Database (HU) then Transitland. Empty on miss."""
    if not query:
        return ""
    skip = (os.environ.get("HUNGARIAN_TRANSPORT_SKIP_CATALOG") or "").strip().lower()
    if skip in ("1", "true", "yes"):
        return ""
    try:
        found = _safe_http_url(_mdb_url(query))
        if found:
            return found
    except Exception:
        pass
    try:
        return _safe_http_url(_transitland_url(query))
    except Exception:
        return ""


def resolved_url(feed: dict) -> str:
    """Env override, then catalog lookup, then the public fallback URL."""
    load_env_files()
    env_name = (feed.get("url_env") or "").strip()
    if env_name:
        override = (os.environ.get(env_name) or "").strip()
        if override:
            return override
    found = catalog_url((feed.get("catalog_query") or "").strip())
    if found:
        return found
    return (feed.get("url") or "").strip()


def parent(sid: str) -> str:
    parts = sid.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return sid


def hm(t: str | None) -> int | None:
    if not t:
        return None
    a = t.split(":")
    if len(a) < 2:
        return None
    try:
        return int(a[0]) * 60 + int(a[1])
    except ValueError:
        return None


def stop_num(pid: str) -> str:
    m = re.search(r"(\d+)$", pid)
    return m.group(1) if m else ""


def slug(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower()
    folded = re.sub(r"^.*\s+-\s+", "", folded)
    folded = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return folded or "local"


LINE_CODE_RE = re.compile(r"^[GSZHM]\d{2}$", re.I)


def line_code(short: str, long_name: str = "") -> str:
    """Suburban product code (G43, S40). Not a train number."""
    for raw in (short, long_name):
        s = (raw or "").strip().upper()
        if LINE_CODE_RE.fullmatch(s):
            return s
    return ""


def train_number(name: str) -> str:
    """Train number from a MÁV name: 'IC 856 TÓPART' → '856'. Not 'G43' → '43'."""
    s = (name or "").strip()
    if not s or LINE_CODE_RE.fullmatch(s):
        return ""
    m = re.search(r"(?<!\d)(\d{3,5})(?!\d)", s)
    if not m:
        return ""
    raw = m.group(1)
    return raw.lstrip("0") or raw


def _encode_signed(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    chars: list[str] = []
    while value >= 0x20:
        chars.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chars.append(chr(value + 63))
    return "".join(chars)


def encode_polyline(pts: Iterable[tuple[float, float]], precision: int = 5) -> str:
    """Google encoded polyline; matches the card's decodePolyline (precision 5)."""
    factor = 10 ** precision
    plat = 0
    plng = 0
    out: list[str] = []
    for lat, lng in pts:
        ilat = int(round(float(lat) * factor))
        ilng = int(round(float(lng) * factor))
        out.append(_encode_signed(ilat - plat))
        out.append(_encode_signed(ilng - plng))
        plat, plng = ilat, ilng
    return "".join(out)


def decode_polyline(encoded: str, precision: int = 5) -> list[tuple[float, float]]:
    factor = 10 ** precision
    coords: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lng = 0
    text = encoded or ""
    while index < len(text):
        for xy in (0, 1):
            result = 0
            shift = 0
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


def open_gtfs_zip(zpath: Path) -> zipfile.ZipFile:
    head = zpath.read_bytes()[:4]
    if head[:2] != b"PK":
        raise zipfile.BadZipFile(f"{zpath} is not a zip file")
    z = zipfile.ZipFile(zpath)
    try:
        for info in z.infolist():
            if info.file_size > MAX_MEMBER:
                raise zipfile.BadZipFile(
                    f"{info.filename} uncompressed size {info.file_size} exceeds limit"
                )
    except Exception:
        z.close()
        raise
    return z


class GtfsDir:
    """Unzipped GTFS folder (pfaedle output). Same read/namelist surface as ZipFile."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def namelist(self) -> list[str]:
        return [p.name for p in self.path.iterdir() if p.is_file()]

    def read(self, name: str) -> bytes:
        return (self.path / name).read_bytes()

    def close(self) -> None:
        return None

    def __enter__(self) -> "GtfsDir":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_gtfs(path: Path):
    """Open a GTFS zip or an unzipped directory."""
    path = Path(path)
    if path.is_dir():
        names = {p.name.lower() for p in path.iterdir() if p.is_file()}
        if "routes.txt" not in names or "trips.txt" not in names:
            raise FileNotFoundError(f"{path} is not a GTFS directory")
        return GtfsDir(path)
    return open_gtfs_zip(path)


def feed_version(z: zipfile.ZipFile) -> str:
    try:
        rows = list(_open_csv(z, "feed_info.txt"))
        if rows:
            return (rows[0].get("feed_version") or "").strip()
    except FileNotFoundError:
        pass
    return ""


def _open_csv(z, name: str):
    wanted = name.lower()
    members = list(z.namelist())
    by_lower = {n.lower(): n for n in members}
    real = by_lower.get(wanted)
    if real is None:
        for member in members:
            if member.lower().rsplit("/", 1)[-1] == wanted:
                real = member
                break
    if not real:
        raise FileNotFoundError(name)
    raw = z.read(real)
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1250", "iso-8859-2", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    return csv.DictReader(io.StringIO(text))


def download_zip(url: str, dest: Path) -> dict:
    """Write url to dest via a sibling tempfile so a kill cannot leave a truncated zip."""
    assert_public_http_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="gtfs_", suffix=".zip", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept-Encoding": "identity"},
        )
        with _urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
            assert_public_http_url(resp.geturl())
            cl = resp.headers.get("Content-Length")
            if cl and cl.isdigit() and int(cl) > MAX_DOWNLOAD:
                raise ValueError("download too large")
            total = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise ValueError("download too large")
                out.write(chunk)
        if tmp.stat().st_size < 4 or tmp.read_bytes()[:2] != b"PK":
            raise zipfile.BadZipFile(f"{dest.name} is not a zip file")
        tmp.replace(dest)
        return {"size": dest.stat().st_size, "content_length": str(dest.stat().st_size)}
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def download_feed(url: str, dest: Path) -> dict:
    """Prefer HTTPS when the published URL is still http://."""
    candidates = [url]
    if url.startswith("http://"):
        candidates.insert(0, "https://" + url[len("http://") :])
    last: Exception | None = None
    for u in candidates:
        try:
            return download_zip(u, dest)
        except Exception as err:
            last = err
    raise last or RuntimeError(f"download failed: {dest.name}")


def load_parts(
    zpath: Path,
    include_route: RoutePred,
    *,
    prefix: str = "",
    split_agencies: bool = False,
    op_id: str = "",
    op_name: str = "",
    skip_agency: Callable[[str], bool] | None = None,
) -> list[dict]:
    """Parse one GTFS zip into one or more operator parts."""
    with open_gtfs_zip(zpath) as z:
        return _load_parts(
            z,
            include_route,
            prefix=prefix,
            split_agencies=split_agencies,
            op_id=op_id,
            op_name=op_name,
            skip_agency=skip_agency,
            zpath=zpath,
        )


def _load_parts(
    z: zipfile.ZipFile,
    include_route: RoutePred,
    *,
    prefix: str,
    split_agencies: bool,
    op_id: str,
    op_name: str,
    skip_agency: Callable[[str], bool] | None,
    zpath: Path,
) -> list[dict]:
    version = feed_version(z)

    agencies: dict[str, str] = {}
    try:
        for row in _open_csv(z, "agency.txt"):
            aid = row.get("agency_id") or ""
            agencies[aid] = (row.get("agency_name") or aid or op_name).strip()
    except FileNotFoundError:
        pass

    sid2p: dict[str, str] = {}
    sid2n: dict[str, str] = {}
    for row in _open_csv(z, "stops.txt"):
        raw = row.get("stop_id") or ""
        if not raw:
            continue
        sid2p[raw] = parent(raw)
        sid2n[raw] = row.get("stop_name") or ""
    # `_12` is a platform suffix only when the parent id is itself a stop.
    # Kecskeméti Kisvasút uses jk_stop_8 / jk_stop_9 as distinct stations.
    for sid, p in list(sid2p.items()):
        if p != sid and p not in sid2n:
            sid2p[sid] = sid

    pname: dict[str, str] = {}
    for sid, p in sid2p.items():
        pname.setdefault(p, sid2n[sid])

    routes: dict[str, str] = {}
    route_types: dict[str, int] = {}
    route_agency: dict[str, str] = {}
    keep_routes: set[str] = set()
    for row in _open_csv(z, "routes.txt"):
        rid = row.get("route_id") or ""
        if not rid:
            continue
        short = (row.get("route_short_name") or "").strip() or (row.get("route_long_name") or "").strip()
        aid = row.get("agency_id") or ""
        aname = agencies.get(aid, aid or op_name)
        if skip_agency and skip_agency(aname):
            continue
        if not include_route(short, aname, aid):
            continue
        routes[rid] = short
        try:
            route_types[rid] = int(row.get("route_type") or 3)
        except ValueError:
            route_types[rid] = 3
        route_agency[rid] = aid
        keep_routes.add(rid)

    trips_meta: dict[str, tuple[str, str]] = {}
    keep_trips: set[str] = set()
    for row in _open_csv(z, "trips.txt"):
        rid = row.get("route_id") or ""
        tid = row.get("trip_id") or ""
        if not tid:
            continue
        trips_meta[tid] = (rid, row.get("service_id") or "")
        if rid in keep_routes:
            keep_trips.add(tid)

    cal: dict[str, list[str]] = {}
    try:
        for row in _open_csv(z, "calendar.txt"):
            sid = row.get("service_id") or ""
            if not sid:
                continue
            days = "".join(
                row.get(d, "0")
                for d in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            )
            cal[sid] = [days, row.get("start_date") or "20200101", row.get("end_date") or "20991231"]
    except FileNotFoundError:
        pass

    ex_add: dict[str, list[str]] = defaultdict(list)
    ex_rem: dict[str, list[str]] = defaultdict(list)
    try:
        for row in _open_csv(z, "calendar_dates.txt"):
            sid = row.get("service_id") or ""
            date = row.get("date") or ""
            if not sid or not date:
                continue
            if row.get("exception_type") == "1":
                ex_add[sid].append(date)
            elif row.get("exception_type") == "2":
                ex_rem[sid].append(date)
    except FileNotFoundError:
        pass

    seqs: dict[str, list[tuple[str, int]]] = {}

    def flush(tid: str, items: list[tuple[str, int | None]]) -> None:
        if tid not in keep_trips or not items:
            return
        pts: list[tuple[str, int]] = []
        for sid, m in items:
            p = sid2p.get(sid, sid)
            if m is None:
                continue
            if pts and pts[-1][0] == p:
                continue
            pts.append((p, m))
        if len(pts) >= 2:
            seqs[tid] = pts

    timed: list[tuple[str, int, str, int | None]] = []
    for row in _open_csv(z, "stop_times.txt"):
        tid = row.get("trip_id") or ""
        if tid not in keep_trips:
            continue
        try:
            seqn = int(row["stop_sequence"]) if row.get("stop_sequence") not in (None, "") else 10**9
        except (TypeError, ValueError):
            seqn = 10**9
        timed.append(
            (
                tid,
                seqn,
                row.get("stop_id") or "",
                hm(row.get("departure_time") or row.get("arrival_time")),
            )
        )
    timed.sort(key=lambda x: (x[0], x[1]))
    cur: str | None = None
    seq: list[tuple[str, int | None]] = []
    for tid, _seqn, sid, m in timed:
        if cur is None:
            cur = tid
        if tid != cur:
            flush(cur, seq)
            seq = []
            cur = tid
        seq.append((sid, m))
    if cur:
        flush(cur, seq)

    def pack(op: str, name: str, trip_ids: Iterable[str]) -> dict:
        used_p: set[str] = set()
        used_s: set[str] = set()
        chosen = []
        for tid in trip_ids:
            pts = seqs.get(tid)
            if not pts:
                continue
            chosen.append(tid)
            used_s.add(trips_meta[tid][1])
            for p, _m in pts:
                used_p.add(p)
        pre = prefix
        parents = sorted(used_p, key=lambda p: (pname.get(p, ""), p))
        p_ix = {p: i for i, p in enumerate(parents)}
        services = sorted(used_s)
        s_ix = {s: i for i, s in enumerate(services)}
        trip_rows = []
        for tid in chosen:
            rid, sid = trips_meta[tid]
            pts = seqs[tid]
            trip_rows.append(
                [
                    s_ix[sid],
                    routes[rid],
                    [p_ix[p] for p, _m in pts],
                    [m for _p, m in pts],
                    # Tram, trolleybus and ferry rows must not be matched as buses.
                    route_types.get(rid, 3),
                ]
            )
        cal_rows = []
        add_rows = []
        rem_rows = []
        for sid in services:
            days, start, end = cal.get(sid, ["0000000", "20200101", "20991231"])
            cal_rows.append([days, start, end])
            add_rows.append(ex_add.get(sid, []))
            rem_rows.append(ex_rem.get(sid, []))
        return {
            "op_id": op,
            "op_name": name,
            "feed": version,
            "s": [[pre + p, pname.get(p, p), stop_num(p)] for p in parents],
            "c": cal_rows,
            "a": add_rows,
            "r": rem_rows,
            "t": trip_rows,
        }

    if not split_agencies:
        oid = op_id or slug(op_name or Path(zpath).stem)
        oname = op_name or agencies.get(next(iter(agencies), ""), oid)
        return [pack(oid, oname, keep_trips)]

    by_agency: dict[str, list[str]] = defaultdict(list)
    for tid in keep_trips:
        rid = trips_meta[tid][0]
        by_agency[route_agency.get(rid, "")].append(tid)
    parts = []
    for aid, tids in by_agency.items():
        aname = agencies.get(aid, aid or op_name or "Helyi")
        oid = slug(aname)
        if oid in {"helykozi-busz", "mav-szemelyszallitasi-zrt"}:
            continue
        display = aname
        if " - " in display:
            display = display.split(" - ", 1)[1]
        if oid == "volanbusz":
            oid = "volan-helyi"
            display = "Volán helyi"
        elif not oid.startswith("volan-"):
            # Keep municipal ids (szeged, pecs, …) unique when Volán local
            # agencies reuse the city name.
            oid = "volan-" + oid
        packed = pack(oid, display, tids)
        packed["s"] = [[f"{oid}:{row[0]}", row[1], row[2]] for row in packed["s"]]
        if packed["t"]:
            parts.append(packed)
    return parts


def existing_trips(out: Path) -> int | None:
    if not out.exists():
        return None
    try:
        with gzip.open(out, "rt", encoding="utf-8") as fh:
            idx = json.load(fh)
        return len(idx.get("t") or [])
    except (OSError, json.JSONDecodeError, gzip.BadGzipFile):
        return None


def write_index(parts: list[dict], out: Path, *, with_operators: bool, schema: int = INDEX_SCHEMA) -> dict:
    """Merge operator parts into one gzipped compact index."""
    ops: list[tuple[str, str]] = []
    seen_op: dict[str, int] = {}
    all_s: list[list] = []
    all_c: list = []
    all_a: list = []
    all_r: list = []
    all_t: list = []
    feeds: list[str] = []
    for part in parts:
        oid = part["op_id"]
        if oid not in seen_op:
            seen_op[oid] = len(ops)
            ops.append((oid, part["op_name"]))
        op_ix = seen_op[oid]
        s_off = len(all_s)
        c_off = len(all_c)
        for row in part["s"]:
            if with_operators:
                all_s.append([row[0], row[1], row[2], op_ix])
            else:
                all_s.append([row[0], row[1], row[2]])
        all_c.extend(part["c"])
        all_a.extend(part["a"])
        all_r.extend(part["r"])
        for t in part["t"]:
            all_t.append([t[0] + c_off, t[1], [p + s_off for p in t[2]], t[3], *t[4:]])
        if part.get("feed"):
            feeds.append(f"{oid}:{part['feed']}")
    prev = existing_trips(out)
    if prev is not None and prev >= 50 and len(all_t) < prev * 0.5:
        raise RuntimeError(
            f"refusing to replace {out.name}: {len(all_t)} trips vs previous {prev}"
        )
    idx = {
        "v": schema,
        "feed": ";".join(feeds),
        "s": all_s,
        "c": all_c,
        "a": all_a,
        "r": all_r,
        "t": all_t,
    }
    if with_operators:
        idx["o"] = [[oid, name] for oid, name in ops]
    raw = json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=9))
    tmp.replace(out)
    return {
        "feed_version": idx["feed"],
        "trips": len(all_t),
        "stops": len(all_s),
        "ops": len(ops),
        "bytes": out.stat().st_size,
    }
