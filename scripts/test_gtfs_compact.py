#!/usr/bin/env python3
"""Sanity checks for the compact GTFS builder. No live downloads."""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import math
import os
import re
import socket
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gtfs_compact as gc  # noqa: E402
from build_city_index import CITY_FEEDS  # noqa: E402
from gtfs_compact import (  # noqa: E402
    INDEX_SCHEMA,
    SHAPE_SHARDS,
    assert_public_http_url,
    index_shapes,
    simplify_shape,
    catalog_url,
    decode_polyline,
    encode_polyline,
    hm,
    line_code,
    load_parts,
    open_gtfs,
    resolved_url,
    train_number,
    write_index,
)
from build_mav_index import _mav_module  # noqa: E402


def gtfs_zip(files: dict[str, str]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    path = Path(tmp.name)
    with zipfile.ZipFile(path, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return path


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    failed = False
    failed |= not check("hm empty", hm("") is None)
    failed |= not check("hm short", hm("8") is None)
    failed |= not check("hm garbage", hm("ab:cd") is None)
    failed |= not check("hm overnight", hm("25:10:00") == 1510)
    failed |= not check("train_number IC 856", train_number("IC 856 TÓPART") == "856")
    failed |= not check("train_number 9400", train_number("9400") == "9400")
    failed |= not check("train_number ignores G43", train_number("G43") == "")
    failed |= not check("line_code G43", line_code("G43", "") == "G43")
    failed |= not check("line_code S40 from long", line_code("", "S40") == "S40")
    rt = encode_polyline([(47.49889, 19.025), (47.18361, 18.42472), (46.44083, 16.98667)])
    dec = decode_polyline(rt)
    failed |= not check(
        "polyline roundtrip 3 pts",
        len(dec) == 3 and abs(dec[0][0] - 47.49889) < 1e-5 and abs(dec[-1][1] - 16.98667) < 1e-5,
        str(dec),
    )

    ids = [f["id"] for f in CITY_FEEDS]
    failed |= not check("city feeds unique ids", len(ids) == len(set(ids)), str(ids))
    for expected in (
        "miskolc",
        "pecs",
        "szeged",
        "szombathely",
        "veszprem",
        "paks",
        "dombovar",
        "kecskemet-kisvasut",
        "budaors",
        "bahart",
        "hodmezovasarhely",
        "kaposvar",
        "kiskunhalas",
        "szekesfehervar",
        "tatabanya",
        "varpalota",
    ):
        failed |= not check(f"city feed {expected}", expected in ids)
    failed |= not check("weekendbus stays out", "weekendbus" not in ids)
    failed |= not check("debrecen stays out", "debrecen" not in ids)

    os.environ["HUNGARIAN_TRANSPORT_SKIP_CATALOG"] = "1"
    bahart = next(f for f in CITY_FEEDS if f["id"] == "bahart")
    failed |= not check("bahart has public fallback", "menetbrand.com/download/bahart" in bahart["url"])
    failed |= not check("bahart url_env", bahart.get("url_env") == "BAHART_GTFS_URL")
    failed |= not check("bahart catalog_query", bahart.get("catalog_query") == "bahart")

    prev = os.environ.get("BAHART_GTFS_URL")
    os.environ["BAHART_GTFS_URL"] = "https://example.invalid/official.zip"
    try:
        failed |= not check(
            "bahart env override",
            resolved_url(bahart) == "https://example.invalid/official.zip",
        )
    finally:
        if prev is None:
            os.environ.pop("BAHART_GTFS_URL", None)
        else:
            os.environ["BAHART_GTFS_URL"] = prev
    failed |= not check("bahart falls back without env", resolved_url(bahart) == bahart["url"])

    files = {
        "agency.txt": "agency_id,agency_name\nA,Szeged - Local\n",
        "stops.txt": "stop_id,stop_name\n1,Alpha\n2,Beta\n",
        "routes.txt": "route_id,route_short_name,agency_id\nR1,12,A\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,S1,T1\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nS1,1,1,1,1,1,1,1,20200101,20991231\n",
        "calendar_dates.txt": "service_id,date,exception_type\nS1,20260101,1\nS1,20260102,2\nS1,20260103,99\n",
        # Interleaved trip ids and reversed stop_sequence — the parser must still
        # emit Alpha then Beta.
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,08:10:00,08:10:00,2,2\n"
            "T1,08:00:00,08:00:00,1,1\n"
        ),
    }
    zpath = gtfs_zip(files)
    try:
        parts = load_parts(zpath, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT")
        trip = parts[0]["t"][0]
        failed |= not check("stop_times sorted by sequence", trip[2] == [0, 1], str(trip))
        failed |= not check("minutes follow the sorted stops", trip[3] == [480, 490], str(trip[3]))
        failed |= not check("exception_type 1 is add", parts[0]["a"][0] == ["20260101"], str(parts[0]["a"]))
        failed |= not check("exception_type 2 is remove", parts[0]["r"][0] == ["20260102"], str(parts[0]["r"]))
        failed |= not check("unknown exception_type ignored", "20260103" not in parts[0]["a"][0] + parts[0]["r"][0])
        failed |= not check("route_type defaults to bus", trip[4] == 3, str(trip))

        tram_files = dict(files)
        tram_files["routes.txt"] = "route_id,route_short_name,agency_id,route_type\nR1,2,A,0\n"
        tz = gtfs_zip(tram_files)
        try:
            tparts = load_parts(tz, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT")
            failed |= not check("route_type 0 (tram) kept on the trip", tparts[0]["t"][0][4] == 0, str(tparts[0]["t"][0]))
            tout = Path(tempfile.mkdtemp()) / "tram.json.gz"
            write_index(tparts, tout, with_operators=True)
            tidx = json.loads(gzip.decompress(tout.read_bytes()))
            failed |= not check("write_index keeps route_type", tidx["t"][0][4] == 0 and tidx["v"] == 29, str(tidx["t"][0]))
        finally:
            tz.unlink()

        # Distinct stations named like jk_stop_8 must not collapse to one parent.
        rail_files = dict(files)
        rail_files["stops.txt"] = "stop_id,stop_name\njk_stop_8,Kiskunmajsa\njk_stop_9,Kecskemét KK\n"
        rail_files["stop_times.txt"] = (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T1,10:30:00,10:30:00,jk_stop_8,1\n"
            "T1,13:30:00,13:30:00,jk_stop_9,2\n"
        )
        rz = gtfs_zip(rail_files)
        try:
            rparts = load_parts(rz, lambda short, *_: bool(short), op_id="kisvasut", prefix="kisvasut:")
            failed |= not check("jk_stop_N stays two stations", len(rparts[0]["s"]) == 2, str(rparts[0]["s"]))
            failed |= not check("jk_stop_N keeps the trip", len(rparts[0]["t"]) == 1, str(rparts[0]["t"]))
        finally:
            rz.unlink()

        # Stop coordinates and shapes.txt end up in the index and its shapes folder.
        shape_files = dict(files)
        shape_files["stops.txt"] = "stop_id,stop_name,stop_lat,stop_lon\n1,Alpha,46.25000,20.14000\n2,Beta,46.26000,20.16000\n"
        shape_files["trips.txt"] = "route_id,service_id,trip_id,shape_id\nR1,S1,T1,SH1\n"
        # SH2 is split in two runs (ungrouped file); SH1 has a straight-line
        # midpoint that simplification must drop.
        shape_files["shapes.txt"] = (
            "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
            "SH1,46.25000,20.14000,1\nSH2,47.0,19.0,1\nSH1,46.25500,20.15000,2\n"
            "SH1,46.26000,20.16000,3\nSH2,47.1,19.1,2\n"
        )
        sz = gtfs_zip(shape_files)
        try:
            sparts = load_parts(sz, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT")
            sdir = Path(tempfile.mkdtemp())
            sout = sdir / "city-index.json.gz"
            sinfo = write_index(sparts, sout, with_operators=True)
            sidx = json.loads(gzip.decompress(sout.read_bytes()))
            folder = (sidx.get("sh") or {}).get("dir", "")
            failed |= not check("stop coordinates stored as 1e-5 degrees",
                                sidx["g"] == [4625000, 2014000, 4626000, 2016000], str(sidx["g"]))
            failed |= not check("trip points at shape 0", sidx["t"][0][5] == 0, str(sidx["t"][0]))
            failed |= not check("index head names its shapes folder",
                                index_shapes(sout) == folder and folder.startswith("city-shapes-"), folder)
            shards = sorted(p.name for p in (sdir / folder).iterdir())
            failed |= not check("shapes folder has every shard", len(shards) == SHAPE_SHARDS, str(len(shards)))
            shard0 = json.loads(gzip.decompress((sdir / folder / "00.json.gz").read_bytes()))
            pts = decode_polyline(shard0["p"][0])
            failed |= not check("straight midpoint simplified away", len(pts) == 2, str(pts))
            failed |= not check("only used shapes are written", sinfo["shapes"] == 1, str(sinfo))
            # A rebuild with other shapes swaps the folder and removes the old one.
            shape_files["shapes.txt"] = shape_files["shapes.txt"].replace("46.26000,20.16000,3", "46.27000,20.17000,3")
            sz2 = gtfs_zip(shape_files)
            try:
                write_index(load_parts(sz2, lambda short, *_: bool(short), op_id="szeged", op_name="Szeged — SZKT"),
                            sout, with_operators=True)
                folders = [p.name for p in sdir.iterdir() if p.is_dir()]
                failed |= not check("old shapes folder pruned after swap",
                                    len(folders) == 1 and folders[0] == index_shapes(sout) != folder, str(folders))
            finally:
                sz2.unlink()
        finally:
            sz.unlink()
        from gtfs_compact import load_shapes, open_gtfs_zip  # noqa: E402
        uz = gtfs_zip(shape_files)
        try:
            with open_gtfs_zip(uz) as z:
                ushapes = load_shapes(z)
            failed |= not check("ungrouped shape is read whole",
                                len(decode_polyline(ushapes.get("SH2", ""))) == 2, str(ushapes.get("SH2")))
        finally:
            uz.unlink()
        failed |= not check("simplify keeps a real corner",
                            len(simplify_shape([(47.0, 19.0), (47.001, 19.0), (47.001, 19.001)], 10)) == 3)

        volan_files = dict(files)
        volan_files["agency.txt"] = "agency_id,agency_name\nA,Szeged\n"
        volan_files["routes.txt"] = "route_id,route_short_name,agency_id\nR1,1,A\n"
        vz = gtfs_zip(volan_files)
        try:
            vparts = load_parts(vz, lambda short, *_: bool(short), split_agencies=True)
            failed |= not check(
                "volan local op_id is prefixed",
                vparts[0]["op_id"] == "volan-szeged",
                vparts[0]["op_id"],
            )
        finally:
            vz.unlink()

        out = Path(tempfile.mkdtemp()) / "idx.json.gz"
        info = write_index(parts, out, with_operators=True)
        failed |= not check("write_index emits trips", info["trips"] == 1)

        fat = {
            "v": 27,
            "s": [],
            "c": [],
            "a": [],
            "r": [],
            "t": [[0, "x", [0, 1], [1, 2]]] * 80,
        }
        fat_path = Path(tempfile.mkdtemp()) / "fat.json.gz"
        fat_path.write_bytes(gzip.compress(json.dumps(fat).encode("utf-8")))
        try:
            write_index(parts, fat_path, with_operators=True)
            failed |= not check("sanity threshold refuses a collapse", False, "wrote anyway")
        except RuntimeError as err:
            failed |= not check("sanity threshold refuses a collapse", "refusing" in str(err), str(err))
    finally:
        zpath.unlink()

    html = Path(tempfile.mkdtemp()) / "not.zip"
    html.write_text("<html>nope</html>")
    try:
        load_parts(html, lambda *_: True)
        failed |= not check("html rejected as zip", False)
    except Exception:
        failed |= not check("html rejected as zip", True)

    def url_ok(u: str) -> bool:
        try:
            assert_public_http_url(u)
            return True
        except ValueError:
            return False

    # Names resolve through a table, so the checks need no network; numeric
    # spellings go to the real getaddrinfo, which parses them without DNS.
    table = {
        "gtfs.kti.hu": ["193.6.200.73"],
        "internal.example": ["10.0.0.5"],
        "mixed.example": ["93.184.216.34", "192.168.1.9"],
        "mapped.example": ["::ffff:127.0.0.1"],
        "cgnat.example": ["100.64.12.1"],
        "nat64.example": ["64:ff9b::c106:c849"],
        "nat64-private.example": ["64:ff9b::a00:5"],
    }
    real_gai = socket.getaddrinfo

    def fake_gai(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in table[host]]
        if host.endswith(".example"):
            raise socket.gaierror(-2, "Name or service not known")
        return real_gai(host, port, *args, **kwargs)

    gc.socket.getaddrinfo = fake_gai
    try:
        failed |= not check("https public url allowed", url_ok("https://gtfs.kti.hu/file.zip"))
        failed |= not check("file scheme blocked", not url_ok("file:///config/www/x.zip"))
        failed |= not check("loopback blocked", not url_ok("http://127.0.0.1/x.zip"))
        failed |= not check("rfc1918 blocked", not url_ok("http://192.168.1.1/x.zip"))
        failed |= not check("supervisor host blocked", not url_ok("http://supervisor/core"))
        failed |= not check("decimal loopback blocked", not url_ok("http://2130706433/x.zip"))
        failed |= not check("hex loopback blocked", not url_ok("http://0x7f000001/x.zip"))
        failed |= not check("short loopback blocked", not url_ok("http://127.1/x.zip"))
        failed |= not check("octal loopback blocked", not url_ok("http://0177.0.0.1/x.zip"))
        failed |= not check("ipv4-mapped loopback blocked", not url_ok("http://[::ffff:127.0.0.1]/x.zip"))
        failed |= not check("link-local blocked", not url_ok("http://169.254.169.254/latest"))
        failed |= not check("name resolving to rfc1918 blocked", not url_ok("https://internal.example/x.zip"))
        failed |= not check("name with one private address blocked", not url_ok("https://mixed.example/x.zip"))
        failed |= not check("name resolving to mapped loopback blocked", not url_ok("https://mapped.example/x.zip"))
        failed |= not check("unresolvable name blocked", not url_ok("https://nope.example/x.zip"))
        failed |= not check("carrier-grade NAT literal blocked", not url_ok("http://100.64.0.1/x.zip"))
        failed |= not check("name resolving to carrier-grade NAT blocked", not url_ok("https://cgnat.example/x.zip"))
        failed |= not check("multicast blocked", not url_ok("http://224.0.1.1/x.zip"))
        failed |= not check("NAT64 of a public IPv4 allowed", url_ok("https://[64:ff9b::c106:c849]/x.zip"))
        failed |= not check("name resolving to NAT64 of a public IPv4 allowed", url_ok("https://nat64.example/x.zip"))
        failed |= not check("NAT64 of a private IPv4 blocked", not url_ok("https://nat64-private.example/x.zip"))
        failed |= not check("NAT64 of loopback blocked", not url_ok("https://[64:ff9b::7f00:1]/x.zip"))
        failed |= not check("IPv6 site-local blocked", not url_ok("https://[fec0::1]/x.zip"))
        failed |= not check("IPv6 documentation range blocked", not url_ok("https://[2001:db8::1]/x.zip"))
        handler = gc._PublicRedirectHandler()
        origin = urllib.request.Request("https://gtfs.kti.hu/a.zip")
        try:
            handler.redirect_request(origin, None, 302, "Found", {}, "http://127.1/x.zip")
            failed |= not check("redirect to loopback blocked", False)
        except ValueError:
            failed |= not check("redirect to loopback blocked", True)
        try:
            handler.redirect_request(origin, None, 302, "Found", {}, "https://internal.example/x.zip")
            failed |= not check("redirect to private name blocked", False)
        except ValueError:
            failed |= not check("redirect to private name blocked", True)
        moved = handler.redirect_request(origin, None, 302, "Found", {}, "https://gtfs.kti.hu/b.zip")
        failed |= not check("redirect to public host followed", moved is not None and moved.full_url.endswith("/b.zip"))
        # The same host losing https on a redirect is asked over https again;
        # nothing is ever fetched over plain http for an https feed.
        same = handler.redirect_request(origin, None, 302, "Found", {}, "HTTP://gtfs.kti.hu/b.zip")
        failed |= not check(
            "https redirected to plain http on the same host stays https",
            same is not None and same.full_url == "https://gtfs.kti.hu/b.zip", same and same.full_url,
        )
        try:
            handler.redirect_request(origin, None, 302, "Found", {}, "http://93.184.216.34/b.zip")
            failed |= not check("https redirected to plain http on another host refused", False)
        except ValueError as err:
            failed |= not check("https redirected to plain http on another host refused", "https required" in str(err), str(err))
        loose = gc._PublicRedirectHandler(allow_insecure=True)
        moved = loose.redirect_request(origin, None, 302, "Found", {}, "http://gtfs.kti.hu/b.zip")
        failed |= not check(
            "an insecure feed's same-host http redirect is asked over https first",
            moved is not None and moved.full_url == "https://gtfs.kti.hu/b.zip", moved and moved.full_url,
        )
        moved = loose.redirect_request(origin, None, 302, "Found", {}, "http://93.184.216.34/b.zip")
        failed |= not check(
            "plain http redirect to another host followed for an insecure feed",
            moved is not None and moved.full_url == "http://93.184.216.34/b.zip", moved and moved.full_url,
        )
        # dombovar.ptke.hu style: https://host/x -> http://host/x/ is asked
        # as https://host/x/, which redirects to http://host/x/ again.
        # Upgrading that once more would loop until urllib gives up.
        slash = urllib.request.Request("https://gtfs.kti.hu/gtfs/")
        moved = loose.redirect_request(slash, None, 301, "Moved", {}, "http://gtfs.kti.hu/gtfs/")
        failed |= not check(
            "an https address redirecting to itself over http is followed over http for an insecure feed",
            moved is not None and moved.full_url == "http://gtfs.kti.hu/gtfs/", moved and moved.full_url,
        )
        try:
            handler.redirect_request(slash, None, 301, "Moved", {}, "http://gtfs.kti.hu/gtfs/")
            failed |= not check("an https address redirecting to itself over http is refused for an https feed", False)
        except ValueError as err:
            failed |= not check("an https address redirecting to itself over http is refused for an https feed",
                                "https required" in str(err), str(err))

        # download_zip checks the scheme of the URL it was given and of the
        # one it ended up at, whatever its spelling.
        class FakeResp:
            def __init__(self, final: str):
                self.final = final
                self.headers = {"Content-Length": "4"}
                self.body = [b"PK\x03\x04", b""]

            def geturl(self) -> str:
                return self.final

            def read(self, _n: int) -> bytes:
                return self.body.pop(0)

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        real_urlopen = gc._urlopen
        opened: list[tuple[str, bool]] = []
        final_url = {"u": ""}

        def fake_urlopen(req, timeout=120, *, allow_insecure=False):
            opened.append((req.full_url, allow_insecure))
            return FakeResp(final_url["u"] or req.full_url)

        gc._urlopen = fake_urlopen
        zdir = Path(tempfile.mkdtemp())
        try:
            def zip_err(u: str, **kw) -> BaseException | None:
                try:
                    gc.download_zip(u, zdir / "z.zip", **kw)
                except Exception as e:  # noqa: BLE001
                    return e
                return None

            err = zip_err("HTTP://gtfs.kti.hu/a.zip")
            failed |= not check("download_zip refuses an upper-case HTTP:// url", isinstance(err, ValueError) and not opened,
                                f"{err!r} {opened}")
            failed |= not check("download_zip takes plain http when allowed",
                                zip_err("http://gtfs.kti.hu/a.zip", allow_insecure=True) is None
                                and opened[-1] == ("http://gtfs.kti.hu/a.zip", True), repr(opened))
            final_url["u"] = "http://gtfs.kti.hu/moved.zip"
            (zdir / "z.zip").unlink(missing_ok=True)
            err = zip_err("https://gtfs.kti.hu/a.zip")
            failed |= not check("download_zip refuses an https download that ended on http",
                                isinstance(err, ValueError) and not (zdir / "z.zip").exists(), repr(err))
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                err = zip_err("https://gtfs.kti.hu/a.zip", allow_insecure=True)
            failed |= not check("an insecure feed redirected to http is downloaded, loudly",
                                err is None and "redirected to plain http" in stderr.getvalue(), repr(err))
            final_url["u"] = ""
            failed |= not check("download_zip over https", zip_err("https://gtfs.kti.hu/a.zip") is None
                                and opened[-1] == ("https://gtfs.kti.hu/a.zip", False), repr(opened))
        finally:
            gc._urlopen = real_urlopen

        # Plain http is upgraded; the fallback needs the feed's explicit say-so.
        zips: list[str] = []
        real_zip = gc.download_zip

        def fake_zip(u: str, dest: Path, **_kw) -> dict:
            zips.append(u)
            if u.startswith("https://"):
                raise OSError("no tls")
            dest.write_bytes(b"PK")
            return {"size": 2}

        gc.download_zip = fake_zip
        dest = Path(tempfile.mkdtemp()) / "feed.zip"
        try:
            err = None
            try:
                gc.download_feed("http://feed.example/f.zip", dest)
            except Exception as e:  # noqa: BLE001
                err = e
            failed |= not check(
                "http feed without insecure flag is not fetched over http",
                isinstance(err, RuntimeError) and zips == ["https://feed.example/f.zip"] and not dest.exists(),
                f"{err!r} {zips}",
            )
            zips.clear()
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                gc.download_feed("http://feed.example/f.zip", dest, allow_insecure=True)
            failed |= not check(
                "insecure feed falls back to http after https, loudly",
                zips == ["https://feed.example/f.zip", "http://feed.example/f.zip"]
                and dest.exists() and "insecure" in stderr.getvalue(),
                f"{zips} {stderr.getvalue()!r}",
            )
            zips.clear()
            dest.unlink(missing_ok=True)
            err = None
            try:
                gc.download_feed("HTTP://feed.example/f.zip", dest)
            except Exception as e:  # noqa: BLE001
                err = e
            failed |= not check(
                "an upper-case HTTP:// feed is upgraded, not fetched over http",
                isinstance(err, RuntimeError) and zips == ["https://feed.example/f.zip"] and not dest.exists(),
                f"{err!r} {zips}",
            )
            zips.clear()
            try:
                gc.download_feed("https://feed.example/f.zip", dest, allow_insecure=True)
                failed |= not check("https feed never downgrades", False)
            except OSError:
                failed |= not check("https feed never downgrades", zips == ["https://feed.example/f.zip"], str(zips))
        finally:
            gc.download_zip = real_zip
            dest.unlink(missing_ok=True)
        insecure_feeds = {f["id"] for f in CITY_FEEDS if f.get("insecure")}
        http_feeds = {f["id"] for f in CITY_FEEDS if f["url"].startswith("http://")}
        failed |= not check("only the http:// city feeds are marked insecure", insecure_feeds == http_feeds, str(http_feeds ^ insecure_feeds))
    finally:
        gc.socket.getaddrinfo = real_gai

    tags = (Path(__file__).resolve().parents[1] / "src" / "tags.js").read_text(encoding="utf-8")
    m = re.search(r"^export const INDEX_SCHEMA = (\d+);", tags, re.M)
    failed |= not check(
        "src/tags.js INDEX_SCHEMA matches scripts/gtfs_compact.py",
        m is not None and int(m.group(1)) == INDEX_SCHEMA,
        f"card {m.group(1) if m else None} vs builder {INDEX_SCHEMA}",
    )
    # The Mobility Database's hosted copy of any matching feed beats a
    # publisher URL, even one listed first.
    real_json, real_access = gc._http_json, gc._mdb_access
    gc._mdb_access = lambda: "token"
    gc._http_json = lambda *_a, **_k: [
        {"provider": "Bahart", "id": "mdb-1", "source_info": {"producer_url": "http://bahart.example/gtfs.zip"},
         "latest_dataset": {}},
        {"provider": "Balatoni Hajózási (Bahart)", "id": "mdb-2",
         "source_info": {"producer_url": "http://bahart.example/2.zip"},
         "latest_dataset": {"hosted_url": "https://files.mobilitydatabase.org/mdb-2/latest.zip"}},
        {"provider": "Other", "id": "mdb-3", "latest_dataset": {"hosted_url": "https://files.example/other.zip"}},
    ]
    try:
        failed |= not check("catalog prefers the hosted copy over a producer url",
                            gc._mdb_url("bahart") == "https://files.mobilitydatabase.org/mdb-2/latest.zip",
                            gc._mdb_url("bahart"))
        gc._http_json = lambda *_a, **_k: [
            {"provider": "Bahart", "id": "mdb-1", "status": "deprecated",
             "latest_dataset": {"hosted_url": "https://files.mobilitydatabase.org/mdb-1/latest.zip"}},
            {"provider": "Bahart", "id": "mdb-4", "status": "active",
             "latest_dataset": {"hosted_url": "https://files.mobilitydatabase.org/mdb-4/latest.zip"}},
        ]
        failed |= not check("catalog prefers an active feed over a deprecated one listed first",
                            gc._mdb_url("bahart") == "https://files.mobilitydatabase.org/mdb-4/latest.zip",
                            gc._mdb_url("bahart"))
        gc._http_json = lambda *_a, **_k: [
            {"provider": "Bahart", "id": "mdb-1", "source_info": {"producer_url": "https://bahart.example/gtfs.zip"}}]
        failed |= not check("catalog falls back to the producer url", gc._mdb_url("bahart") == "https://bahart.example/gtfs.zip")
    finally:
        gc._http_json, gc._mdb_access = real_json, real_access

    # .env: `export KEY=…`, comments after a value, quotes; a `#` inside a
    # URL stays. The Home Assistant file wins over /config/.env.
    env_keys = ("T_EXPORTED", "T_COMMENT", "T_QUOTED", "T_SINGLE", "T_HASH", "T_EMPTY", "T_SET")
    saved_env = {k: os.environ.pop(k, None) for k in env_keys}
    env_file = Path(tempfile.mkdtemp()) / ".env"
    env_file.write_text(
        "export T_EXPORTED=https://a.example/x.zip\n"
        "T_COMMENT=https://b.example/y.zip?apikey=k  # keyed NAP URL\n"
        'T_QUOTED="a b" # spaced\n'
        "T_SINGLE='q # r'\n"
        "T_HASH=https://c.example/z#frag\n"
        "T_EMPTY= # nothing yet\n"
        "T_SET=from-file\n",
        encoding="utf-8",
    )
    os.environ["T_SET"] = "from-env"
    try:
        gc._parse_env_file(env_file)
        got_env = {k: os.environ.get(k) for k in env_keys}
        failed |= not check("an .env line parses export, comments and quotes", got_env == {
            "T_EXPORTED": "https://a.example/x.zip", "T_COMMENT": "https://b.example/y.zip?apikey=k",
            "T_QUOTED": "a b", "T_SINGLE": "q # r", "T_HASH": "https://c.example/z#frag", "T_EMPTY": "",
            "T_SET": "from-env",
        }, repr(got_env))
    finally:
        for k, v in saved_env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    saved_extra = os.environ.pop("HUNGARIAN_TRANSPORT_ENV", None)
    try:
        order = gc.env_candidates()
        failed |= not check(
            "/config/hungarian-transport/.env wins over the .env next to the scripts' folder",
            order[0] == Path("/config/hungarian-transport/.env")
            and order[1] == Path(gc.__file__).resolve().parent.parent / ".env", repr(order))
        os.environ["HUNGARIAN_TRANSPORT_ENV"] = str(env_file)
        failed |= not check("HUNGARIAN_TRANSPORT_ENV comes first", gc.env_candidates()[0] == env_file)
    finally:
        os.environ.pop("HUNGARIAN_TRANSPORT_ENV", None)
        if saved_extra is not None:
            os.environ["HUNGARIAN_TRANSPORT_ENV"] = saved_extra

    # A shape point that is not a coordinate is dropped, the last one too:
    # a trailing (0, 999) must not end the line, a NaN must not fail the feed.
    shape_zip = gtfs_zip({"shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                          "s1,47.0,19.0,1\ns1,47.01,19.01,2\ns1,47.02,19.02,3\ns1,0,999,4\n"
                          "s2,47.0,19.0,1\ns2,47.01,19.01,2\ns2,nan,19.03,3\n"
                          "s3,47.0,19.0,1\ns3,47.01,inf,2\ns3,47.02,19.02,3\ns3,47.03,19.03,nan\n"})
    try:
        with zipfile.ZipFile(shape_zip) as z:
            shape_lines = {k: decode_polyline(v) for k, v in gc.load_shapes(z).items()}
        failed |= not check(
            "non-finite and off-globe shape points are dropped, the last one included",
            set(shape_lines) == {"s1", "s2", "s3"}
            and shape_lines["s1"][-1] == (47.02, 19.02) and shape_lines["s2"][-1] == (47.01, 19.01)
            and shape_lines["s3"] == [(47.0, 19.0), (47.02, 19.02)]
            and all(-90 <= la <= 90 and -180 <= lo <= 180 for pts in shape_lines.values() for la, lo in pts),
            repr(shape_lines))
    except Exception as err:  # noqa: BLE001
        failed |= not check("non-finite and off-globe shape points are dropped, the last one included", False, repr(err))
    finally:
        shape_zip.unlink(missing_ok=True)

    # The zip check reads the first bytes, not a whole 100 MB feed.
    small = gtfs_zip({"stops.txt": "stop_id,stop_name\n1,A\n"})
    real_read_bytes = Path.read_bytes

    def no_read_bytes(self):
        raise AssertionError(f"{self} read whole")

    Path.read_bytes = no_read_bytes
    try:
        with gc.open_gtfs_zip(small) as z:
            failed |= not check("open_gtfs_zip reads only the head of the file", "stops.txt" in z.namelist())
    except AssertionError as err:
        failed |= not check("open_gtfs_zip reads only the head of the file", False, str(err))
    finally:
        Path.read_bytes = real_read_bytes
        small.unlink(missing_ok=True)

    import inspect
    cat_src = inspect.getsource(catalog_url)
    failed |= not check(
        "catalog prefers MDB HU filter over Transitland first hit",
        cat_src.find("_mdb_url") < cat_src.find("_transitland_url")
        and "_safe_http_url" in cat_src,
    )

    szf = (47.183611, 18.424722)
    mid = (47.3167, 18.7908)
    kel = (47.464444, 19.02)
    mav = _mav_module()
    chord = mav.RouteGrid([szf, kel])
    failed |= not check(
        "2-point chord misses an intermediate station",
        chord.gap(mid) > mav.STOP_TOLERANCE_M,
        str(round(chord.gap(mid))),
    )
    failed |= not check(
        "2-point chord passes its own end stations",
        chord.gap(szf) < 1 and chord.gap(kel) < 1,
    )

    stub_dir = Path(tempfile.mkdtemp()) / "official-chord"
    stub_dir.mkdir()
    (stub_dir / "agency.txt").write_text("agency_id,agency_name\nMAV,MÁV\n")
    (stub_dir / "routes.txt").write_text(
        "route_id,route_short_name,route_long_name,route_type\n"
        "R857,,IC 857 TÓPART,2\n"
    )
    (stub_dir / "trips.txt").write_text(
        "route_id,service_id,trip_id,shape_id\nR857,S1,T857,shp857\n"
    )
    (stub_dir / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n"
        f"838,Székesfehérvár,{szf[0]},{szf[1]}\n"
        f"900,Martonvásár,{mid[0]},{mid[1]}\n"
        f"3640,Budapest-Kelenföld,{kel[0]},{kel[1]}\n"
    )
    (stub_dir / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T857,07:00:00,07:00:00,838,1\n"
        "T857,07:25:00,07:25:00,900,2\n"
        "T857,07:50:00,07:50:00,3640,3\n"
    )
    (stub_dir / "shapes.txt").write_text(
        "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        f"shp857,{szf[0]},{szf[1]},0\n"
        f"shp857,{kel[0]},{kel[1]},1\n"
    )
    (stub_dir / "feed_info.txt").write_text("feed_publisher_name,feed_version\nMÁV,official-stub\n")
    stub_payload = mav.build(stub_dir)
    stub_var = (stub_payload["n"].get("857") or [[None, []]])[0]
    stub_geom = decode_polyline(stub_payload["p"][stub_var[0]]) if stub_var[0] is not None else []
    failed |= not check(
        "official 2-point chord replaced by stop sequence",
        stub_payload["_stats"]["stop_lines"] >= 1 and len(stub_geom) >= 3,
        f"stats={stub_payload['_stats']} npts={len(stub_geom)}",
    )

    rail_dir = Path(tempfile.mkdtemp()) / "pfaedle-out"
    rail_dir.mkdir()
    szf = (47.183611, 18.424722)
    kel = (47.464444, 19.02)
    npts = 41
    shape_rows = ["shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence"]
    for i in range(npts):
        t = i / (npts - 1)
        # A curving line (like the rails), so simplification keeps its bends.
        bend = 0.02 * math.sin(t * math.pi * 4)
        lat = szf[0] + (kel[0] - szf[0]) * t + bend
        lon = szf[1] + (kel[1] - szf[1]) * t
        shape_rows.append(f"shp857,{lat:.6f},{lon:.6f},{i}")
    (rail_dir / "agency.txt").write_text("agency_id,agency_name\nMAV,MÁV\n")
    (rail_dir / "routes.txt").write_text(
        "route_id,route_short_name,route_long_name,route_type\n"
        "R857,,IC 857 TÓPART,2\n"
    )
    (rail_dir / "trips.txt").write_text(
        "route_id,service_id,trip_id,shape_id\nR857,S1,T857,shp857\n"
    )
    (rail_dir / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n"
        f"838,Székesfehérvár,{szf[0]},{szf[1]}\n"
        f"3640,Budapest-Kelenföld,{kel[0]},{kel[1]}\n"
    )
    (rail_dir / "stop_times.txt").write_text(
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T857,07:00:00,07:00:00,838,1\n"
        "T857,07:50:00,07:50:00,3640,2\n"
    )
    (rail_dir / "shapes.txt").write_text("\n".join(shape_rows) + "\n")
    (rail_dir / "feed_info.txt").write_text("feed_publisher_name,feed_version\nMÁV,test-pfaedle\n")
    try:
        with open_gtfs(rail_dir) as src:
            failed |= not check("open_gtfs directory", "shapes.txt" in src.namelist())
        idx_out = Path(tempfile.mkdtemp()) / "mav-index.json.gz"
        payload = mav.build(rail_dir)
        info = mav.write(payload, idx_out)
        idx = json.loads(gzip.open(idx_out).read())
        var = (idx["n"].get("857") or [[None, []]])[0]
        ix = var[0]
        geom = decode_polyline(idx["p"][ix]) if ix is not None else []
        failed |= not check("pfaedle dir indexes 857", ix is not None, str(idx.get("n")))
        failed |= not check(
            "variant lists its own stations",
            [idx["st"][i][0] for i in var[1]] == ["Székesfehérvár", "Budapest-Kelenföld"],
            str(var[1]),
        )
        failed |= not check(
            "pfaedle dir keeps the bends of its shape",
            len(geom) >= 5,
            f"npts={len(geom)} info={info}",
        )
        failed |= not check(
            "pfaedle dir spans Szfvár–Kelenföld",
            geom and abs(geom[0][0] - szf[0]) < 1e-3 and abs(geom[-1][1] - kel[1]) < 1e-3,
            str(geom[:1] + geom[-1:]),
        )
    finally:
        pass

    # audit_city_shapes: a stop beside its shape passes, one 400 m away and a
    # trip without a shape are reported, per operator.
    import audit_city_shapes as acs
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        shape_dir = base / "city-shapes-test"
        shape_dir.mkdir()
        line = [(46.2500, 20.1400), (46.2600, 20.1400), (46.2700, 20.1400)]
        with gzip.open(shape_dir / "00.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"v": 1, "p": [gc.encode_polyline(line)]}, fh)
        with gzip.open(shape_dir / "01.json.gz", "wt", encoding="utf-8") as fh:
            json.dump({"v": 1, "p": []}, fh)
        g = []
        for lat, lon in [(46.2500, 20.1405), (46.2650, 20.1400), (46.2700, 20.1452)]:
            g += [round(lat * 1e5), round(lon * 1e5)]
        idx = {
            "v": 29, "o": [["szeged", "Szeged"]], "g": g,
            "s": [["a", "Alfa", "", 0], ["b", "Béta", "", 0], ["c", "Céda", "", 0]],
            "sh": {"dir": "city-shapes-test", "n": 2},
            "t": [[0, "1", [0, 1], [0, 5], 3, 0], [0, "2", [1, 2], [0, 5], 3, 0], [0, "3", [0, 2], [0, 5], 3, 1],
                  [0, "4", [1, 0], [0, 5], 3, 0]],
        }
        with gzip.open(base / "city-index.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(idx, fh)
        rep = acs.audit(base / "city-index.json.gz")
        op = rep["operators"].get("szeged", {})
        failed |= not check(
            "city shape audit: on route, off route, out of order, no shape",
            op.get("patterns") == 4 and op.get("ok") == 1 and op.get("off_route") == 1 and op.get("no_shape") == 1
            and op.get("out_of_order") == 1
            and any(w.get("stop") == "Céda" and 350 < w.get("m", 0) < 450 for w in rep["worst"]),
            json.dumps(op),
        )
        loop = [(46.2598, 20.1408), (46.2610, 20.1390), (46.2580, 20.1380), (46.2575, 20.1400), (46.2598, 20.1408)]
        mars, tavasz = (46.2578, 20.1395), (46.2610, 20.1391)
        failed |= not check(
            "city shape audit: a loop may start past the trip's first stop",
            acs.ordered_miss([mars, tavasz], loop, 100.0) is None
            and acs.ordered_miss([mars, tavasz], loop[:-1], 100.0) is not None,
        )
        failed |= not check(
            "city shape audit: --min-ok fails a weak operator",
            acs.main([str(base / "city-index.json.gz"), "--min-ok", "0.9"]) == 1
            and acs.main([str(base / "city-index.json.gz")]) == 0,
        )
        # A short out-and-back whose ends are 80 m apart is no loop to walk
        # round; a long route closing within the tolerance is.
        k = 1 / (111320.0 * math.cos(math.radians(46.25)))
        u_turn = [(46.2500, 20.1400), (46.2500 + 300 / 110540.0, 20.1400),
                  (46.2500 + 300 / 110540.0, 20.1400 + 80 * k), (46.2500, 20.1400 + 80 * k)]
        open_end = [(46.25, 20.14), (46.27, 20.14), (46.27, 20.16), (46.25, 20.16), (46.25, 20.14 + 150 * k)]
        failed |= not check(
            "city shape audit: only a long shape with meeting ends is a loop",
            acs.is_loop(loop, 100.0) and not acs.is_loop(u_turn, 100.0) and not acs.is_loop(open_end, 100.0)
            and acs.is_loop(open_end[:-1] + [(46.25, 20.14 + 30 * k)], 100.0),
        )
        decoded: list[str] = []
        real_decode = acs.decode_polyline

        def counting_decode(enc: str):
            decoded.append(enc)
            return real_decode(enc)

        acs.decode_polyline = counting_decode
        try:
            acs.audit(base / "city-index.json.gz")
        finally:
            acs.decode_polyline = real_decode
        failed |= not check("city shape audit: a shape shared by three patterns is decoded once", len(decoded) == 1,
                            str(len(decoded)))
        # The Volán index has no operator list: its trips are one operator.
        volan = {k: v for k, v in idx.items() if k != "o"}
        volan["s"] = [row[:3] for row in idx["s"]]
        with gzip.open(base / "volan-index.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(volan, fh)
        vrep = acs.audit(base / "volan-index.json.gz")
        failed |= not check("city shape audit: an index without operators reports as one",
                            list(vrep["operators"]) == ["volan"] and vrep["operators"]["volan"]["patterns"] == 4,
                            json.dumps(vrep["operators"]))

    # A platform far from its station keeps its own place (city builds only):
    # Szentes, Damjanich utca has platforms 3-4 on another street.
    far_files = {
        "agency.txt": "agency_id,agency_name\nA,Szentes\n",
        "stops.txt": (
            "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
            "hkir_9,Damjanich utca,46.670162,20.252863,1,\n"
            "hkir_9_1,Damjanich utca,46.669931,20.253118,,hkir_9\n"
            "hkir_9_3,Damjanich utca,46.672600,20.259211,,hkir_9\n"
            "hkir_8,Drahos utca,46.66,20.24,,\n"
        ),
        "routes.txt": "route_id,route_short_name,agency_id\nR3,3,A\nR1,1,A\n",
        "trips.txt": "route_id,service_id,trip_id\nR3,S1,T3\nR1,S1,T1\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nS1,1,1,1,1,1,1,1,20200101,20991231\n",
        "stop_times.txt": (
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
            "T3,08:00:00,08:00:00,hkir_8,1\nT3,08:05:00,08:05:00,hkir_9_3,2\n"
            "T1,09:00:00,09:00:00,hkir_8,1\nT1,09:05:00,09:05:00,hkir_9_1,2\n"
        ),
    }
    fz = gtfs_zip(far_files)
    try:
        def place(parts, route):
            p = parts[0]
            trip = next(t for t in p["t"] if t[1] == route)
            i = trip[2][-1]
            return p["s"][i], (p["g"][2 * i] / 1e5, p["g"][2 * i + 1] / 1e5)
        merged = load_parts(fz, lambda short, *_: bool(short), op_id="szentes", prefix="szentes:")
        split = load_parts(fz, lambda short, *_: bool(short), op_id="szentes", prefix="szentes:",
                           split_platforms_m=gc.PLATFORM_SPLIT_M)
        m3, mll3 = place(merged, "3")
        s3, sll3 = place(split, "3")
        s1, sll1 = place(split, "1")
        failed |= not check(
            "without the split every platform is the station",
            m3[0] == "szentes:hkir_9" and abs(mll3[1] - 20.25286) < 1e-4, f"{m3} {mll3}",
        )
        failed |= not check(
            "a far platform keeps its own place, name and station number",
            s3[0] == "szentes:hkir_9_3" and s3[1] == "Damjanich utca" and s3[2] == "9"
            and abs(sll3[0] - 46.6726) < 1e-4 and abs(sll3[1] - 20.25921) < 1e-4,
            f"{s3} {sll3}",
        )
        failed |= not check(
            "a near platform is still the station",
            s1[0] == "szentes:hkir_9" and abs(sll1[1] - 20.25286) < 1e-4, f"{s1} {sll1}",
        )
    finally:
        fz.unlink(missing_ok=True)

    # A station without a position of its own, whose first platform listed is
    # the far one: the platforms that belong together stay the station.
    near = [(46.669931, 20.253118), (46.670300, 20.252700), (46.670100, 20.252500)]
    rows = ["stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station", "hkir_9,Damjanich utca,,,1,",
            "hkir_9_3,Damjanich utca,46.672600,20.259211,,hkir_9"]
    rows += [f"hkir_9_{n},Damjanich utca,{lat},{lon},,hkir_9" for n, (lat, lon) in zip((1, 2, 6), near)]
    rows += ["hkir_9_4,Kossuth tér,46.672700,20.259500,,hkir_9", "hkir_9_5,,46.667000,20.247000,,hkir_9",
             "hkir_8,Drahos utca,46.66,20.24,,"]
    trips = ["route_id,service_id,trip_id"]
    times = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    for n in (1, 2, 3, 4, 5, 6):
        trips.append(f"R{n},S1,T{n}")
        times += [f"T{n},08:00:00,08:00:00,hkir_8,1", f"T{n},08:05:00,08:05:00,hkir_9_{n},2"]
    fz = gtfs_zip({
        **far_files,
        "stops.txt": "\n".join(rows) + "\n",
        "routes.txt": "route_id,route_short_name,agency_id\n" + "".join(f"R{n},{n},A\n" for n in range(1, 7)),
        "trips.txt": "\n".join(trips) + "\n",
        "stop_times.txt": "\n".join(times) + "\n",
    })
    try:
        split = load_parts(fz, lambda short, *_: bool(short), op_id="szentes", prefix="szentes:",
                           split_platforms_m=gc.PLATFORM_SPLIT_M)
        got = {route: place(split, route) for route in "123456"}
        centre = (sum(ll[0] for ll in near) / 3, sum(ll[1] for ll in near) / 3)
        failed |= not check(
            "without its own position the station is its densest platforms, at their centroid",
            {got[r][0][0] for r in "126"} == {"szentes:hkir_9"}
            and abs(got["1"][1][0] - centre[0]) < 2e-5 and abs(got["1"][1][1] - centre[1]) < 2e-5,
            repr({r: got[r] for r in "126"}),
        )
        failed |= not check(
            "the far first platform is split off with the station number",
            got["3"][0] == ["szentes:hkir_9_3", "Damjanich utca", "9"], repr(got["3"]),
        )
        failed |= not check(
            "a split platform with a name of its own gets no station number",
            got["4"][0] == ["szentes:hkir_9_4", "Kossuth tér", ""], repr(got["4"]),
        )
        failed |= not check(
            "a split platform without a name takes the station's name and number",
            got["5"][0] == ["szentes:hkir_9_5", "Damjanich utca", "9"], repr(got["5"]),
        )
    finally:
        fz.unlink(missing_ok=True)

    # https -> http on the same host (a server adding a slash) is asked over
    # https again; to another host it is still refused.
    up = gc.upgrade_same_host_redirect
    failed |= not check(
        "same-host https->http redirect is upgraded, others are not",
        up("https://gtfs.menetbrand.com/download/tatabanya", "http://gtfs.menetbrand.com/download/tatabanya/")
        == "https://gtfs.menetbrand.com/download/tatabanya/"
        and up("https://dombovar.ptke.hu/gtfs", "http://dombovar.ptke.hu:80/gtfs/") == "https://dombovar.ptke.hu/gtfs/"
        and up("https://a.example/x", "http://b.example/x/") == "http://b.example/x/"
        and up("http://a.example/x", "http://a.example/x/") == "http://a.example/x/",
    )
    failed |= not check(
        "city feeds name their final https address (no redirect)",
        all(not re.search(r"menetbrand\.com/download/[a-z]+$|ptke\.hu/gtfs$", f["url"]) for f in CITY_FEEDS),
        str([f["url"] for f in CITY_FEEDS if "menetbrand" in f["url"] or "ptke" in f["url"]]),
    )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
