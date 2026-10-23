#!/usr/bin/env python3
"""Compact a GTFS zip into the JSON the Lovelace card decompresses."""
from __future__ import annotations

import base64
import codecs
import csv
import gzip
import hashlib
import http.client
import io
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Callable, Iterable, Iterator

RoutePred = Callable[[str, str, str], bool]

UA = "Hungarian-transport/1.0 (Home Assistant; +https://github.com/bator/hungarian-transport)"
MAX_MEMBER = 500 * 1024 * 1024
MAX_DOWNLOAD = 200 * 1024 * 1024
# 28: trip rows carry GTFS route_type as a fifth element.
# 29: stop coordinates ("g"), a shape reference per trip and "sh", the
#     sharded shapes folder next to the index.
INDEX_SCHEMA = 29
# Shapes are split into this many files so opening one map downloads one
# small shard (shape id % n), not every route of the feed.
SHAPE_SHARDS = 64
# Douglas–Peucker tolerance: 10 m keeps streets recognisable at city zoom
# and drops ~60 % of the points.
SHAPE_TOLERANCE_M = 10.0
# A platform this far from its station (`X_3` of `X`) keeps its own place in
# a city index: Szentes, Damjanich utca has platforms on another street,
# 550 m away. City cards group stops by name, so departures stay together.
PLATFORM_SPLIT_M = 100.0
_SHAPES_MEMO: dict[tuple, dict[str, str]] = {}
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
# DNS64 on an IPv6-only network answers with these; the IPv4 host in the
# low 32 bits is the one that counts.
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_ENV_LOADED = False
_MDB_ACCESS: str | None = None


class IndexGuardError(RuntimeError):
    """A rebuilt index lost too much against the one it would replace."""


class IncompleteDownload(OSError):
    """A download that ended before its Content-Length, or whose zip has no
    readable central directory: a connection cut short, not a bad feed.
    CPython's http.client returns the bytes it got without IncompleteRead
    when the server closes early, so the length is checked here."""


def _env_value(raw: str) -> str:
    """The value of a .env line: a quoted value up to its closing quote, an
    unquoted one up to a ` #` comment. A `#` inside a word (a URL fragment)
    stays."""
    val = raw.strip()
    if val[:1] in ("'", '"'):
        end = val.find(val[0], 1)
        if end > 0:
            return val[1:end]
    return re.sub(r"(^|\s)#.*$", "", val).strip()


def _parse_env_file(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        # A file that is also sourced by a shell says `export KEY=value`.
        line = re.sub(r"^export\s+", "", line)
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _env_value(val)


def env_candidates() -> list[Path]:
    """The .env files load_env_files() reads, first one wins for a variable:
    HUNGARIAN_TRANSPORT_ENV, then /config/hungarian-transport/.env (where
    the README puts it on Home Assistant), then the .env next to the
    scripts' folder (a repo checkout; /config/.env when the scripts sit in
    /config/hungarian-transport)."""
    extra = (os.environ.get("HUNGARIAN_TRANSPORT_ENV") or "").strip()
    here = Path(__file__).resolve().parent
    candidates = [Path(extra)] if extra else []
    candidates.extend(
        [
            Path("/config/hungarian-transport/.env"),
            here.parent / ".env",
            Path("/mnt/data/supervisor/homeassistant/hungarian-transport/.env"),
        ]
    )
    return candidates


def load_env_files() -> None:
    """Load gitignored .env if present. Never override already-set variables."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    for path in env_candidates():
        if path.is_file():
            _parse_env_file(path)


def feed_error(feed_id: str, err: BaseException) -> str:
    """Log a download failure without echoing keyed NAP / catalog URLs."""
    return f"{feed_id}: {type(err).__name__}"


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Anything but a public unicast address: private, loopback, link-local,
    carrier-grade NAT (100.64.0.0/10), documentation, multicast, reserved."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _blocked_ip(ip.ipv4_mapped)
        if ip in _NAT64:
            return _blocked_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if ip.is_site_local:
            return True
    return bool(not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def _public_addrinfo(host: str, port: int) -> list:
    """getaddrinfo of host, or ValueError unless every address is public.

    getaddrinfo also parses the numeric spellings ipaddress rejects
    (2130706433, 0x7f000001, 127.1, 0177.0.0.1), so a loopback dressed up as
    a decimal cannot slip past the literal check. A name that does not
    resolve cannot be downloaded either, so it is refused as well.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as err:
        raise ValueError(f"unresolvable url host: {type(err).__name__}") from None
    if not infos:
        raise ValueError("unresolvable url host")
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise ValueError("blocked url host") from None
        if _blocked_ip(ip):
            raise ValueError("blocked url host")
    return infos


def resolve_public_host(host: str, port: int) -> None:
    """Raise ValueError unless every address of host is public (see _public_addrinfo)."""
    _public_addrinfo(host, port)


def _connect_public(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """socket.create_connection that connects only to the addresses it has just
    checked. The URL check and urllib's own connect each resolved the name, so
    a DNS answer that changed in between (rebinding) reached a private address;
    here the one resolution is both checked and used."""
    host, port = address
    last: OSError | None = None
    for family, type_, proto, _canon, sockaddr in _public_addrinfo(host, port):
        sock = socket.socket(family, type_, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as err:
            last = err
            sock.close()
    raise last or OSError(f"cannot connect to {host}")


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    # TLS still verifies the certificate and sends SNI for the URL's name
    # (http.client wraps the socket with server_hostname=self.host).
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


def _via_proxy(req: urllib.request.Request) -> bool:
    # Through a proxy the socket goes to the proxy, which resolves the name
    # itself: the URL check before the request is all there is then.
    return req.has_proxy() or bool(getattr(req, "_tunnel_host", None))


_proxy_warned = False


def _connection_class(req: urllib.request.Request, plain: type, pinned: type) -> type:
    """pinned, or plain through a proxy; the latter said once per run on stderr
    (update.log), without the proxy address (it may carry credentials)."""
    global _proxy_warned
    if not _via_proxy(req):
        return pinned
    if not _proxy_warned:
        _proxy_warned = True
        print(
            "warn downloads go through a proxy: the address is checked before the request,"
            " but the connection is not pinned to it (the proxy resolves the name)",
            file=sys.stderr,
        )
    return plain


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_connection_class(req, http.client.HTTPConnection, _PublicHTTPConnection), req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        conn = _connection_class(req, http.client.HTTPSConnection, _PublicHTTPSConnection)
        return self.do_open(conn, req, context=self._context)


def assert_public_http_url(url: str) -> None:
    """Reject file://, loopback, link-local and RFC1918 targets before urlopen.

    Literal addresses are checked as such; names (and numeric spellings that
    are not IP literals) are resolved and every address must be public.
    """
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
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise ValueError("blocked url port") from None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        resolve_public_host(host, port)
        return
    if _blocked_ip(ip):
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


def assert_https(url: str, *, allow_insecure: bool = False) -> None:
    """Refuse a plain-http URL (any spelling of the scheme) unless the feed
    may use http."""
    scheme = urllib.parse.urlparse((url or "").strip()).scheme
    if scheme != "https" and not (allow_insecure and scheme == "http"):
        raise ValueError(f"blocked url scheme {scheme or 'none'} (https required)")


class _PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to a public host, and from https only to https
    unless the feed may use plain http."""

    def __init__(self, allow_insecure: bool = False):
        super().__init__()
        self.allow_insecure = allow_insecure

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        upgraded = upgrade_same_host_redirect(req.full_url, newurl)
        if upgraded == req.full_url:
            # The https address itself redirects to http: the server has no
            # https for it, and asking again would loop until urllib gives
            # up. Plain http it is, if the feed may use it; assert_https
            # refuses it otherwise.
            upgraded = newurl
        newurl = upgraded
        assert_public_http_url(newurl)
        assert_https(newurl, allow_insecure=self.allow_insecure)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def upgrade_same_host_redirect(old: str, new: str) -> str:
    """https://host/x -> http://host/x/ is a server adding a slash and losing
    the scheme on the way (menetbrand.com, dombovar.ptke.hu): ask for the
    same address over https instead. A redirect to another host stays as it
    is, for assert_https to judge."""
    a, b = urllib.parse.urlparse(old), urllib.parse.urlparse(new)
    if a.scheme.lower() == "https" and b.scheme.lower() == "http" and a.hostname and a.hostname == b.hostname \
            and b.port in (None, 80):
        netloc = b.netloc.rsplit(":", 1)[0] if b.port == 80 else b.netloc
        return urllib.parse.urlunparse(("https", netloc, b.path, b.params, b.query, b.fragment))
    return new


def _urlopen(req: urllib.request.Request, timeout: int = 120, *, allow_insecure: bool = False):
    """urlopen for feed downloads: redirects only to public hosts, and every
    connection (the redirected ones included) goes to an address checked at
    connect time."""
    opener = urllib.request.build_opener(
        _PublicRedirectHandler(allow_insecure), _PublicHTTPHandler(), _PublicHTTPSHandler()
    )
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
    matches = [
        feed
        for feed in feeds
        if isinstance(feed, dict)
        and (needle in str(feed.get("provider") or "").casefold() or needle in str(feed.get("id") or "").casefold())
    ]
    # An active feed beats a deprecated or inactive one (a publisher's old
    # entry next to its current one); the order is kept otherwise.
    matches.sort(key=lambda feed: str(feed.get("status") or "").lower() != "active")
    # The Mobility Database's own copy (validated, https) of any matching
    # feed beats a publisher URL, which may be plain http or gone.
    for feed in matches:
        latest = feed.get("latest_dataset") if isinstance(feed.get("latest_dataset"), dict) else {}
        hosted = (latest.get("hosted_url") or "").strip()
        if hosted:
            return hosted
    for feed in matches:
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


def feed_urls(feed: dict) -> Iterator[str]:
    """Env override, then catalog lookup, then the public fallback URL, each
    once. Lazy, so the catalog is only asked when the override fails."""
    load_env_files()
    seen: set[str] = set()

    def fresh(url: str | None) -> str:
        url = (url or "").strip()
        if not url or url in seen:
            return ""
        seen.add(url)
        return url

    env_name = (feed.get("url_env") or "").strip()
    if env_name:
        url = fresh(os.environ.get(env_name))
        if url:
            yield url
    url = fresh(catalog_url((feed.get("catalog_query") or "").strip()))
    if url:
        yield url
    url = fresh(feed.get("url"))
    if url:
        yield url


def resolved_url(feed: dict) -> str:
    """Env override, then catalog lookup, then the public fallback URL."""
    return next(feed_urls(feed), "")


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


def _meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    k = math.cos(math.radians(a[0]))
    return math.hypot((a[1] - b[1]) * k * 111320.0, (a[0] - b[0]) * 110540.0)


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


def _zip_magic(path: Path) -> bool:
    with Path(path).open("rb") as fh:
        return fh.read(4)[:2] == b"PK"


def open_gtfs_zip(zpath: Path) -> zipfile.ZipFile:
    if not _zip_magic(zpath):
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

    def open(self, name: str):
        return (self.path / name).open("rb")

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


def _member(z, name: str) -> str:
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
    return real


def _stream_shape_points(z):
    """(shape_id, seq, lat, lon) from shapes.txt without reading the member
    into memory (388 MB in the Volán feed). csv.reader, not DictReader: this
    loop runs 8 million times. Shape files are ASCII, so UTF-8 is enough."""
    real = _member(z, "shapes.txt")
    with z.open(real) as fh:
        rows = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig", errors="replace", newline=""))
        header = [h.strip() for h in next(rows, [])]
        try:
            i_id = header.index("shape_id")
            i_lat = header.index("shape_pt_lat")
            i_lon = header.index("shape_pt_lon")
            i_seq = header.index("shape_pt_sequence")
        except ValueError:
            return
        width = max(i_id, i_lat, i_lon, i_seq)
        for row in rows:
            if len(row) <= width:
                continue
            try:
                seq, lat, lon = float(row[i_seq]), float(row[i_lat]), float(row[i_lon])
            except ValueError:
                continue
            # float() takes "nan" and "inf": a point like that (or one off
            # the globe) would fail the polyline of its shape and with it the
            # whole feed. NaN fails every comparison, so it is dropped here.
            if not (math.isfinite(seq) and -90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            yield row[i_id], seq, lat, lon


def simplify_shape(pts: list[tuple[float, float]], tol_m: float) -> list[tuple[float, float]]:
    """Douglas–Peucker on a local equirectangular projection (metres)."""
    if len(pts) < 3:
        return list(pts)
    kx = 111320.0 * math.cos(math.radians(pts[0][0]))
    ky = 110540.0
    xy = [(lon * kx, lat * ky) for lat, lon in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    tol2 = tol_m * tol_m
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        dx, dy = xy[b][0] - ax, xy[b][1] - ay
        seg2 = dx * dx + dy * dy
        worst, wi = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i][0] - ax, xy[i][1] - ay
            if seg2:
                t = (px * dx + py * dy) / seg2
                t = 0.0 if t < 0 else 1.0 if t > 1 else t
                px -= t * dx
                py -= t * dy
            d2 = px * px + py * py
            if d2 > worst:
                worst, wi = d2, i
        if worst > tol2:
            keep[wi] = True
            stack.append((a, wi))
            stack.append((wi, b))
    return [p for p, k in zip(pts, keep) if k]


def _encode_shape(raw: list[tuple[float, float, float]]) -> str:
    raw.sort()
    pts: list[tuple[float, float]] = []
    last = None
    for _seq, lat, lon in raw:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        # Points closer than ~2 m add nothing at map zoom; skip them before
        # the (quadratic in the worst case) simplification.
        if last is not None and abs(lat - last[0]) < 2e-5 and abs(lon - last[1]) < 3e-5:
            continue
        pts.append((lat, lon))
        last = (lat, lon)
    if raw and pts and (raw[-1][1], raw[-1][2]) != pts[-1]:
        pts.append((raw[-1][1], raw[-1][2]))
    if len(pts) < 2:
        return ""
    return encode_polyline(simplify_shape(pts, SHAPE_TOLERANCE_M))


def load_shapes(z, memo_key: tuple | None = None) -> dict[str, str]:
    """shape_id -> simplified encoded polyline.

    Streams shapes.txt one shape at a time, which needs the file grouped by
    shape_id (every Hungarian feed is). A shape that shows up again later is
    re-read in a second pass. The Volán zip feeds both the Volán and the city
    index, so results are memoised per zip within one process.
    """
    if memo_key is not None and memo_key in _SHAPES_MEMO:
        return _SHAPES_MEMO[memo_key]
    out: dict[str, str] = {}
    split: set[str] = set()
    try:
        cur: str | None = None
        buf: list[tuple[float, float, float]] = []
        for sid, seq, lat, lon in _stream_shape_points(z):
            if sid != cur:
                if cur is not None:
                    if cur in out:
                        split.add(cur)
                    else:
                        out[cur] = _encode_shape(buf)
                cur, buf = sid, []
            buf.append((seq, lat, lon))
        if cur is not None:
            if cur in out:
                split.add(cur)
            else:
                out[cur] = _encode_shape(buf)
        if split:
            again: dict[str, list] = defaultdict(list)
            for sid, seq, lat, lon in _stream_shape_points(z):
                if sid in split:
                    again[sid].append((seq, lat, lon))
            for sid, raw in again.items():
                out[sid] = _encode_shape(raw)
    except FileNotFoundError:
        pass
    out = {k: v for k, v in out.items() if v}
    if memo_key is not None:
        _SHAPES_MEMO[memo_key] = out
    return out


def _legacy_bytes(err: UnicodeError) -> tuple[str, int]:
    """Decode error handler: bytes that are not UTF-8 are read as cp1250
    (iso-8859-2 for the five bytes cp1250 leaves undefined). Hungarian feeds
    are UTF-8 or cp1250, so a streamed member needs no encoding pre-pass."""
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


def _csv_rows(z, real: str) -> Iterator[dict[str, str]]:
    with z.open(real) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8-sig", errors="gtfs_legacy", newline="")
        reader = csv.DictReader(text, restval="")
        if reader.fieldnames:
            reader.fieldnames = [h.strip() for h in reader.fieldnames]
        yield from reader


def _open_csv(z, name: str) -> Iterator[dict[str, str]]:
    """Rows of a GTFS member, streamed: stop_times.txt of the Volán feed is
    several hundred MB, too much to hold in memory on a Home Assistant host.
    Header names are stripped; a short row gets "" for its missing columns.
    A missing member raises FileNotFoundError here, not on first iteration."""
    return _csv_rows(z, _member(z, name))


def _check_zip_directory(path: Path, name: str) -> None:
    """IncompleteDownload unless path has a zip central directory whose
    members all lie inside the file. A zip cut short loses its central
    directory (it is at the end) even without a Content-Length to compare;
    reading the directory costs nothing next to the download, unlike a CRC
    pass over the members."""
    size = path.stat().st_size
    try:
        with zipfile.ZipFile(path) as z:
            members = z.infolist()
    except (zipfile.BadZipFile, OSError, EOFError) as err:
        raise IncompleteDownload(f"{name}: no zip directory ({err})") from err
    for info in members:
        if info.header_offset + info.compress_size > size:
            raise IncompleteDownload(f"{name}: {info.filename} runs past the end of the file")


def basic_auth(auth: tuple[str, str]) -> str:
    """The Authorization header value of HTTP Basic credentials (user, password).
    Errors never name either."""
    user, password = auth
    if not user or not password:
        raise ValueError("credentials need a user name and a password")
    if ":" in user or any(ord(c) < 32 for c in user + password):
        raise ValueError("credentials hold a character HTTP Basic cannot carry")
    return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")


def download_zip(
    url: str, dest: Path, *, allow_insecure: bool = False, auth: tuple[str, str] | None = None
) -> dict:
    """Write url to dest via a sibling tempfile so a kill cannot leave a truncated zip.

    A body shorter than its Content-Length, or a zip without a readable
    central directory, raises IncompleteDownload (a failed download, tried
    again next run) instead of leaving a zip that fails to load.

    HTTPS only, redirects included, unless allow_insecure (a feed entry marked
    "insecure"; see download_feed).

    auth, HTTP Basic (user, password), goes over https only and only to the
    URL's own host: it is an unredirected header, which urllib drops on any
    redirect (a feed that redirects elsewhere gets no credentials and fails
    with 401 there). It never appears in a URL, an error or a log line."""
    assert_public_http_url(url)
    if auth is not None:
        allow_insecure = False
    assert_https(url, allow_insecure=allow_insecure)
    authorization = basic_auth(auth) if auth is not None else ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="gtfs_", suffix=".zip", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": UA, "Accept-Encoding": "identity"},
        )
        if authorization:
            req.add_unredirected_header("Authorization", authorization)
        with _urlopen(req, timeout=120, allow_insecure=allow_insecure) as resp, tmp.open("wb") as out:
            final = resp.geturl()
            assert_public_http_url(final)
            assert_https(final, allow_insecure=allow_insecure)
            if urllib.parse.urlparse(final).scheme == "http" and urllib.parse.urlparse(url).scheme == "https":
                print(f"warn insecure download {dest.name}: https redirected to plain http", file=sys.stderr)
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
        if cl and cl.isdigit() and total != int(cl):
            raise IncompleteDownload(f"{dest.name}: got {total} of {cl} bytes")
        if tmp.stat().st_size < 4 or not _zip_magic(tmp):
            raise zipfile.BadZipFile(f"{dest.name} is not a zip file")
        _check_zip_directory(tmp, dest.name)
        tmp.replace(dest)
        return {"size": dest.stat().st_size, "content_length": str(dest.stat().st_size)}
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def download_feed(
    url: str, dest: Path, *, allow_insecure: bool = False, auth: tuple[str, str] | None = None
) -> dict:
    """Download a feed, over HTTPS even when the published URL is http://.

    Plain HTTP (an unauthenticated zip that becomes card data) is used only
    for a feed whose entry says ``"insecure": True``: after HTTPS failed, or
    when its HTTPS URL redirects to http, each time with a warning on stderr.
    Otherwise the HTTPS failure is raised, and a redirect to http is one.
    With auth (HTTP Basic, see download_zip) never plain http.
    """
    if auth is not None:
        allow_insecure = False
    parsed = urllib.parse.urlparse((url or "").strip())
    # urlparse lower-cases the scheme, so "HTTP://" is plain http as well.
    insecure = parsed.scheme == "http"
    candidates = [(parsed._replace(scheme="https").geturl(), False)] if insecure else [(url, False)]
    if insecure and allow_insecure:
        candidates.append((parsed.geturl(), True))
    last: Exception | None = None
    for u, plain in candidates:
        if plain:
            print(
                f"warn insecure download {dest.name}: https failed ({type(last).__name__}), "
                "using plain http as the feed entry allows",
                file=sys.stderr,
            )
        try:
            # An https attempt may still be redirected to http when the feed
            # entry allows plain http at all (the publisher's own setup).
            if auth is not None:
                return download_zip(u, dest, allow_insecure=False, auth=auth)
            return download_zip(u, dest, allow_insecure=allow_insecure)
        except Exception as err:
            last = err
    if insecure and not allow_insecure:
        raise RuntimeError(
            f"{dest.name}: https download failed ({type(last).__name__}); "
            "plain http is not allowed for this feed"
        ) from last
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
    split_platforms_m: float | None = None,
) -> list[dict]:
    """Parse one GTFS zip into one or more operator parts. With
    split_platforms_m, a platform farther than that from its station is a
    stop of its own (same name, own position) instead of the station's."""
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
            split_platforms_m=split_platforms_m,
        )


def _densest(sids: list[str], sid2ll: dict[str, tuple[float, float]], limit_m: float) -> str:
    """The platform with the most others within limit_m, then the smallest
    total distance to them; stops.txt order breaks a tie."""

    def score(sid: str) -> tuple[int, float]:
        d = [_meters(sid2ll[sid], sid2ll[o]) for o in sids]
        return -sum(1 for m in d if m <= limit_m), sum(d)

    return min(sids, key=score)


def _split_platforms(
    sid2p: dict[str, str],
    sid2n: dict[str, str],
    sid2ll: dict[str, tuple[float, float]],
    pname: dict[str, str],
    pll: dict[str, tuple[float, float]],
    pnum: dict[str, str],
    limit_m: float,
) -> None:
    """Make every platform farther than limit_m from its station a stop of
    its own (in place).

    The distance is measured from the station's own position, or, when it
    has none, from its densest platform (most platforms within limit_m, then
    the smallest total distance): the first platform listed may be the far
    one, and a plain centroid drifts towards it. Such a station then sits at
    the centroid of the platforms it kept.

    A split platform with the station's name keeps the station number, so a
    lookup by number still lands on the station's stops; one with a name of
    its own is another stop and gets no number.
    """
    platforms: dict[str, list[str]] = defaultdict(list)
    for sid, p in sid2p.items():
        if sid != p and sid in sid2ll:
            platforms[p].append(sid)
    for p, sids in platforms.items():
        own = p in sid2ll
        ref = sid2ll[p] if own else sid2ll[_densest(sids, sid2ll, limit_m)]
        kept: list[tuple[float, float]] = []
        for sid in sids:
            ll = sid2ll[sid]
            if _meters(ll, ref) <= limit_m:
                kept.append(ll)
                continue
            sid2p[sid] = sid
            pname[sid] = sid2n[sid] or pname[p]
            pll[sid] = ll
            pnum[sid] = stop_num(p) if pname[sid] == pname[p] else ""
        if not own and kept:
            pll[p] = (sum(ll[0] for ll in kept) / len(kept), sum(ll[1] for ll in kept) / len(kept))


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
    split_platforms_m: float | None = None,
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
    sid2ll: dict[str, tuple[float, float]] = {}
    for row in _open_csv(z, "stops.txt"):
        raw = row.get("stop_id") or ""
        if not raw:
            continue
        sid2p[raw] = parent(raw)
        sid2n[raw] = row.get("stop_name") or ""
        try:
            lat, lon = float(row.get("stop_lat") or ""), float(row.get("stop_lon") or "")
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat or lon):
            sid2ll[raw] = (lat, lon)
    # `_12` is a platform suffix only when the parent id is itself a stop.
    # Kecskeméti Kisvasút uses jk_stop_8 / jk_stop_9 as distinct stations.
    for sid, p in list(sid2p.items()):
        if p != sid and p not in sid2n:
            sid2p[sid] = sid

    pname: dict[str, str] = {}
    # A parent's own position if it has one, else its first platform's.
    pll: dict[str, tuple[float, float]] = {}
    for sid, p in sid2p.items():
        pname.setdefault(p, sid2n[sid])
        if sid == p and sid in sid2ll:
            pll[p] = sid2ll[sid]
    for sid, p in sid2p.items():
        if p not in pll and sid in sid2ll:
            pll[p] = sid2ll[sid]
    # Stop number per stop when it is not stop_num(stop id); "" = none.
    pnum: dict[str, str] = {}
    if split_platforms_m:
        _split_platforms(sid2p, sid2n, sid2ll, pname, pll, pnum, split_platforms_m)

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
    trip_shape: dict[str, str] = {}
    keep_trips: set[str] = set()
    for row in _open_csv(z, "trips.txt"):
        rid = row.get("route_id") or ""
        tid = row.get("trip_id") or ""
        if not tid:
            continue
        trips_meta[tid] = (rid, row.get("service_id") or "")
        if row.get("shape_id"):
            trip_shape[tid] = row["shape_id"]
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

    shapes: dict[str, str] = {}
    if any(tid in trip_shape for tid in keep_trips):
        zp = Path(zpath)
        try:
            st = zp.stat()
            # The file, not its name: a zip renamed into place after its
            # build (update_volan_index) still hits the memo.
            memo = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            memo = None
        shapes = load_shapes(z, memo)

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
        shape_list: list[str] = []
        shape_ix: dict[str, int] = {}
        for tid in chosen:
            rid, sid = trips_meta[tid]
            pts = seqs[tid]
            enc = shapes.get(trip_shape.get(tid, ""), "")
            ref = -1
            if enc:
                ref = shape_ix.get(enc, -1)
                if ref < 0:
                    ref = shape_ix[enc] = len(shape_list)
                    shape_list.append(enc)
            trip_rows.append(
                [
                    s_ix[sid],
                    routes[rid],
                    [p_ix[p] for p, _m in pts],
                    [m for _p, m in pts],
                    # Tram, trolleybus and ferry rows must not be matched as buses.
                    route_types.get(rid, 3),
                    ref,
                ]
            )
        coords: list[int] = []
        for p in parents:
            ll = pll.get(p)
            coords.extend((round(ll[0] * 1e5), round(ll[1] * 1e5)) if ll else (0, 0))
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
            "s": [[pre + p, pname.get(p, p), pnum[p] if p in pnum else stop_num(p)] for p in parents],
            # lat, lon per stop as integers of 1e-5 degrees; 0, 0 = unknown.
            "g": coords,
            "shapes": shape_list,
            "c": cal_rows,
            "a": add_rows,
            "r": rem_rows,
            "t": trip_rows,
        }

    # keep_trips is a set: its order changes with PYTHONHASHSEED. Trip ids in
    # order make the same feed give the same index (and shapes folder), so
    # the weekly release sees no change where there is none.
    trip_order = sorted(keep_trips)
    if not split_agencies:
        oid = op_id or slug(op_name or Path(zpath).stem)
        oname = op_name or agencies.get(next(iter(agencies), ""), oid)
        return [pack(oid, oname, trip_order)]

    by_agency: dict[str, list[str]] = defaultdict(list)
    for tid in trip_order:
        rid = trips_meta[tid][0]
        by_agency[route_agency.get(rid, "")].append(tid)
    parts = []
    for aid, tids in sorted(by_agency.items()):
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


def running_trips(idx: dict, today: str | None = None) -> list[list]:
    """The trips of an index or part whose own service still runs on or
    after today (calendar_end() of that one service; unknown counts as
    running). A feed with a summer and an autumn service keeps the summer
    trips after the summer ended: they are not trips a rebuild can lose."""
    today = today or date.today().strftime("%Y%m%d")
    cal = idx.get("c") or []
    added = idx.get("a") or []
    ends: dict[int, str] = {}
    out = []
    for t in idx.get("t") or []:
        sv = t[0]
        if sv not in ends:
            ok = isinstance(sv, int) and 0 <= sv < len(cal)
            ends[sv] = calendar_end({
                "c": [cal[sv]] if ok else [],
                "a": [added[sv]] if ok and sv < len(added) else [],
            })
        if not ends[sv] or ends[sv] >= today:
            out.append(t)
    return out


# Below this many trips a guard has nothing meaningful to compare.
GUARD_MIN_TRIPS = 50
# When the running trips of a previous index are less than this share of all
# its trips, its calendar has mostly ended (see guard_basis).
GUARD_RUNNING_SHARE = 0.25
# The share of those few running trips a rebuild must then keep.
GUARD_ENDING_KEEP = 0.9


def guard_basis(running: int, total: int, ratio: float) -> tuple[bool, float]:
    """How a rebuild is compared with the previous index (or feed):
    (on the trips that still run, on both sides, rather than on all trips;
    the share of that yardstick the rebuild must keep).

    Running trips whenever at least GUARD_MIN_TRIPS still run, so a feed
    whose summer service ended keeps its autumn trips as the yardstick and
    is not refused for the summer, however long the summer was. When those
    are only a small part of the whole (under GUARD_RUNNING_SHARE), the
    calendar has mostly ended and a truncated feed must not pass, when the
    guard matters most: the rebuild has to keep GUARD_ENDING_KEEP of them
    rather than ratio. A rebuild is then either the same remaining
    services or a new period, larger still. Under GUARD_MIN_TRIPS running,
    all trips, as before the per-trip count."""
    if running < GUARD_MIN_TRIPS:
        return False, ratio
    if running < total * GUARD_RUNNING_SHARE:
        return True, max(ratio, GUARD_ENDING_KEEP)
    return True, ratio


def existing_trips(out: Path, today: str | None = None) -> tuple[int, int] | None:
    """(trips whose service still runs, all trips) of an existing index;
    None when there is no readable index."""
    idx = _read_index(out)
    if idx is None:
        return None
    try:
        return len(running_trips(idx, today)), len(idx.get("t") or [])
    except (IndexError, TypeError):
        return None


def _read_index(out: Path) -> dict | None:
    if not out.exists():
        return None
    try:
        with gzip.open(out, "rt", encoding="utf-8") as fh:
            idx = json.load(fh)
    except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError, gzip.BadGzipFile):
        return None
    return idx if isinstance(idx, dict) else None


def operator_stats(out: Path) -> dict[str, tuple[int, str]] | None:
    """(trips, last service day YYYYMMDD or "") per operator id of an
    existing index with operators; None when there is no readable index. A
    trip belongs to the operator of its first stop; the last service day
    follows calendar_end() over the services the operator's trips use."""
    idx = _read_index(out)
    if idx is None:
        return None
    try:
        ops = idx.get("o") or []
        stops = idx.get("s") or []
        cal = idx.get("c") or []
        added = idx.get("a") or []
        counts: dict[str, int] = defaultdict(int)
        services: dict[str, set[int]] = defaultdict(set)
        for t in idx.get("t") or []:
            op = ops[stops[t[2][0]][3]][0]
            counts[op] += 1
            services[op].add(t[0])
        out_stats: dict[str, tuple[int, str]] = {}
        for op, n in counts.items():
            ix = sorted(i for i in services[op] if isinstance(i, int) and 0 <= i < len(cal))
            end = calendar_end({
                "c": [cal[i] for i in ix],
                "a": [added[i] if i < len(added) else [] for i in ix],
            })
            out_stats[op] = (n, end)
        return out_stats
    except (IndexError, KeyError, TypeError):
        return None


def operator_running_trips(out: Path, today: str | None = None) -> dict[str, int] | None:
    """Trips per operator id of an existing index whose own service still
    runs (running_trips); None when there is no readable index."""
    idx = _read_index(out)
    if idx is None:
        return None
    try:
        ops = idx.get("o") or []
        stops = idx.get("s") or []
        counts: dict[str, int] = defaultdict(int)
        for t in running_trips(idx, today):
            counts[ops[stops[t[2][0]][3]][0]] += 1
        return dict(counts)
    except (IndexError, KeyError, TypeError):
        return None


def operator_trips(out: Path) -> dict[str, int] | None:
    """Trips per operator id of an existing index with operators; None when
    there is no readable index. A trip belongs to the operator of its first stop."""
    stats = operator_stats(out)
    return None if stats is None else {op: n for op, (n, _end) in stats.items()}


def shapes_stem(out: Path) -> str:
    """'volan' for volan-index.json.gz; the shapes folder is named after it."""
    name = out.name
    for suffix in ("-index.json.gz", ".json.gz"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name.split(".")[0]


def write_shapes(shapes: list[str], out: Path) -> dict | None:
    """Write shapes into <stem>-shapes-<hash>/NN.json.gz next to the index.

    The folder name carries a content hash, so an index and its shapes are
    swapped atomically: the folder exists before the index points at it.
    Shard k holds the shapes with id % n == k, at position id // n.
    """
    if not shapes:
        return None
    digest = hashlib.sha1("\n".join(shapes).encode("utf-8")).hexdigest()[:10]
    name = f"{shapes_stem(out)}-shapes-{digest}"
    final = out.parent / name
    if not final.is_dir():
        tmp = Path(tempfile.mkdtemp(prefix=name + ".", dir=str(out.parent)))
        try:
            for k in range(SHAPE_SHARDS):
                body = json.dumps({"v": 1, "p": shapes[k::SHAPE_SHARDS]}, separators=(",", ":"))
                (tmp / f"{k:02d}.json.gz").write_bytes(gzip.compress(body.encode("utf-8"), 9, mtime=0))
            os.chmod(tmp, 0o755)
            os.replace(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return {"dir": name, "n": SHAPE_SHARDS}


def prune_shapes(out: Path, keep: str | None) -> None:
    """Drop shapes folders (and interrupted temp folders) the index no longer uses."""
    prefix = shapes_stem(out) + "-shapes-"
    for entry in out.parent.glob(prefix + "*"):
        if entry.is_dir() and entry.name != keep:
            shutil.rmtree(entry, ignore_errors=True)


def index_shapes(path: Path) -> str | None:
    """Shapes folder an index file points at, read from the start of the file."""
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(256)
    except (OSError, EOFError):
        return None
    m = re.search(rb'"sh":\{"dir":"([A-Za-z0-9_.-]+)"', head)
    return m.group(1).decode("ascii") if m else None


_YMD = re.compile(r"\d{8}")


def calendar_end(part: dict) -> str:
    """Last service day (YYYYMMDD) a part can run on: the latest calendar.txt
    end_date of a service with any weekday set, or the latest added
    calendar_dates.txt date. "" when the feed carries no calendar at all."""
    end = ""
    for row in part.get("c") or []:
        if len(row) >= 3 and set(str(row[0])) != {"0"} and _YMD.fullmatch(str(row[2])) and row[2] > end:
            end = row[2]
    for dates in part.get("a") or []:
        for d in dates or []:
            if _YMD.fullmatch(str(d)) and d > end:
                end = d
    return end


def stale_parts(parts: list[dict], today: str | None = None) -> list[tuple[str, str]]:
    """(op_id, last service day) of the parts whose calendar ended before today."""
    today = today or date.today().strftime("%Y%m%d")
    out = []
    for part in parts:
        end = calendar_end(part)
        if end and end < today:
            out.append((part["op_id"], end))
    return out


def write_index(
    parts: list[dict],
    out: Path,
    *,
    with_operators: bool,
    schema: int = INDEX_SCHEMA,
    today: str | None = None,
    guard_total: bool = True,
) -> dict:
    """Merge operator parts into one gzipped compact index (and its shapes).

    Refuses an index built from a feed whose calendar already ended (a stale
    zip the publisher forgot): the card would show a timetable nothing runs
    on. With guard_total, also one that would lose most of the trips of the
    one in place. The city index passes guard_total=False: guard_feeds()
    compares it feed by feed, counting only what still runs and honouring
    HUNGARIAN_TRANSPORT_ALLOW_MISSING, so feeds whose calendars end together
    (at year end) do not freeze it.
    """
    stale = stale_parts(parts, today)
    if stale:
        raise IndexGuardError(
            f"refusing to write {out.name}: calendar ended for "
            + ", ".join(f"{op} ({end[:4]}-{end[4:6]}-{end[6:]})" for op, end in stale)
        )
    ops: list[tuple[str, str]] = []
    seen_op: dict[str, int] = {}
    all_s: list[list] = []
    all_g: list[int] = []
    all_c: list = []
    all_a: list = []
    all_r: list = []
    all_t: list = []
    all_shapes: list[str] = []
    shape_seen: dict[str, int] = {}
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
        g = part.get("g") or []
        all_g.extend(g if len(g) == 2 * len(part["s"]) else [0, 0] * len(part["s"]))
        remap = []
        for enc in part.get("shapes") or []:
            ix = shape_seen.get(enc)
            if ix is None:
                ix = shape_seen[enc] = len(all_shapes)
                all_shapes.append(enc)
            remap.append(ix)
        all_c.extend(part["c"])
        all_a.extend(part["a"])
        all_r.extend(part["r"])
        for t in part["t"]:
            row = [t[0] + c_off, t[1], [p + s_off for p in t[2]], t[3], *t[4:5]]
            if len(t) > 5:
                row.append(remap[t[5]] if t[5] is not None and t[5] >= 0 else -1)
            all_t.append(row)
        if part.get("feed"):
            feeds.append(f"{oid}:{part['feed']}")
    # The summer trips of a feed are not lost when the summer is over: the
    # comparison is on the trips that still run, on both sides, unless only
    # a handful of the previous ones do, then on all (guard_basis).
    prev = existing_trips(out, today) if guard_total else None
    if prev is not None:
        on_running, keep = guard_basis(*prev, 0.5)
        if on_running:
            had, what = prev[0], "running trips"
            has = len(running_trips({"c": all_c, "a": all_a, "t": all_t}, today))
        else:
            had, has, what = prev[1], len(all_t), "trips"
        if had >= GUARD_MIN_TRIPS and has < had * keep:
            raise IndexGuardError(
                f"refusing to replace {out.name}: {has} {what} vs previous {had}"
            )
    sh = write_shapes(all_shapes, out)
    # "v" and "sh" first: index_schema() and index_shapes() read only the head.
    idx: dict = {"v": schema}
    if sh:
        idx["sh"] = sh
    idx.update({
        "feed": ";".join(feeds),
        "s": all_s,
        "g": all_g,
        "c": all_c,
        "a": all_a,
        "r": all_r,
        "t": all_t,
    })
    if with_operators:
        idx["o"] = [[oid, name] for oid, name in ops]
    raw = json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    tmp.replace(out)
    prune_shapes(out, sh["dir"] if sh else None)
    return {
        "feed_version": idx["feed"],
        "trips": len(all_t),
        "stops": len(all_s),
        "ops": len(ops),
        "bytes": out.stat().st_size,
        "shapes": len(all_shapes),
    }
