"""Tests for the manual's local server: only loopback Host/Origin reach /svc.

Runs without aiohttp (a stub stands in for it), so plain `python3
docs/manual/tools/test_server.py` works on any machine.
"""

import asyncio
import importlib.util
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import aiohttp  # noqa: F401
except ImportError:
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class Response:
        def __init__(self, *, status=200, text="", **_kw):
            self.status, self.text = status, text

    web.Response = Response
    web.Request = object
    web.middleware = lambda fn: fn
    web.json_response = lambda data, status=200: Response(status=status, text=str(data))
    aiohttp.web = web
    aiohttp.ClientSession = object
    aiohttp.ClientError = Exception
    aiohttp.ClientTimeout = lambda **_kw: None
    sys.modules["aiohttp"], sys.modules["aiohttp.web"] = aiohttp, web

spec = importlib.util.spec_from_file_location("manual_server", os.path.join(HERE, "server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

failed = False


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(("ok  " if ok else "FAIL") + "  " + name + (f" — {detail}" if detail and not ok else ""))
    failed |= not ok


PORT = 8765
for host, origin, want in [
    ("127.0.0.1:8765", None, True),
    ("localhost:8765", None, True),
    ("127.0.0.1:8765", "http://127.0.0.1:8765", True),
    ("LOCALHOST:8765", "http://localhost:8765", True),
    ("evil.example:8765", "http://evil.example:8765", False),  # a rebound name, same-origin to the browser
    ("evil.example:8765", None, False),
    ("127.0.0.1:8765", "http://evil.example:8765", False),
    ("127.0.0.1:8765", "null", False),
    ("127.0.0.1:9999", None, False),
    ("127.0.0.1", None, False),
    (None, None, False),
    ("127.0.0.1:8765", "https://127.0.0.1:8765", False),
]:
    check(f"Host {host!r} Origin {origin!r} -> {want}", server.local_request_ok(host, origin, PORT) is want)


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers
        self.app = {"port": PORT}


async def handler(_request):
    return "handled"


async def run(headers):
    return await server.local_only(FakeRequest(headers), handler)


check("the middleware passes a loopback request on", asyncio.run(run({"Host": "127.0.0.1:8765"})) == "handled")
blocked = asyncio.run(run({"Host": "evil.example:8765", "Origin": "http://evil.example:8765"}))
check("the middleware answers 403 to a rebound name", getattr(blocked, "status", None) == 403, repr(blocked))

# FUTÁR paths: only endpoint names reach go.bkk.hu. "%2e%2e" passed the old
# ".." check, and the client's URL decodes and normalises it to "..".
for path, want in [
    ("arrivals-and-departures-for-stop.json", True),
    ("plan-trip.json", True),
    ("sub/route_details.json", True),
    ("", False),
    ("../x.json", False),
    ("%2e%2e/x.json", False),
    ("%2E%2E%2Fx.json", False),
    ("a/%2e%2e/b.json", False),
    ("/search.json", False),
    ("search.json?key=x", False),
    ("search.json\n", False),
    ("search", False),
    ("a//b.json", False),
]:
    check(f"FUTÁR path {path!r} -> {want}", server.futar_path_ok(path) is want)


class NoSession:
    def get(self, *_a, **_kw):
        raise AssertionError("a bad path reached the upstream call")


server.BKK_API_KEY = "test-key"
refused = asyncio.run(server.futar(NoSession(), {"path": "%2e%2e/%2e%2e/x.json"}))
check("futar answers 400 to an encoded .. without calling FUTÁR", getattr(refused, "status", None) == 400,
      repr(refused))

if failed:
    sys.exit(1)
print("manual server tests passed")
