"""Local server for the user-manual screenshots.

Serves the harness page, the card bundle and its timetable indexes, and
answers the two services the cards call, the way the integration would:

- hungarian_transport.futar: calls BKK FUTÁR with BKK_API_KEY,
- hungarian_transport.elvira_between: forwarded to a running Home Assistant
  (HA_URL, HA_TOKEN) with return_response. Without them the MÁV rows simply
  have no platform or seat-reservation marks.

Neither secret ever reaches the browser. Run it with the integration test
venv (it needs aiohttp):

    BKK_API_KEY=... HA_URL=http://homeassistant.local:8123 HA_TOKEN=... \
        .venv/bin/python docs/manual/tools/server.py
"""

import asyncio
import os
import sys

import aiohttp
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.environ.get("WWW", os.path.join(HERE, "www"))
BKK_API = "https://go.bkk.hu/api/query/v1/ws/otp/api/where"
BKK_API_KEY = os.environ.get("BKK_API_KEY", "").strip()
HA_URL = os.environ.get("HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN", "").strip()


def outage(code: str, detail: str) -> web.Response:
    """An expected outage, shaped like the integration's service response."""
    return web.json_response({"response": {"data": None, "error": code, "detail": detail}})


async def futar(session: aiohttp.ClientSession, data: dict) -> web.Response:
    if not BKK_API_KEY:
        return outage("futar_auth", "no BKK_API_KEY")
    path = str(data.get("path") or "")
    if not path or ".." in path or path.startswith("/"):
        return web.json_response({"error": "bad path"}, status=400)
    query = {k: str(v) for k, v in (data.get("params") or {}).items()}
    query.update({"key": BKK_API_KEY, "version": "4", "appVersion": "apiary-1.0"})
    try:
        async with session.get(
            f"{BKK_API}/{path}", params=query, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status != 200:
                return outage("futar_unavailable", f"HTTP {resp.status}")
            payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return outage("futar_unavailable", type(err).__name__)
    return web.json_response({"response": {"data": payload}})


async def elvira(session: aiohttp.ClientSession, data: dict) -> web.Response:
    if not (HA_URL and HA_TOKEN):
        return web.json_response({"response": {"departures": [], "error": "elvira_unavailable", "detail": "no HA_URL/HA_TOKEN"}})
    try:
        async with session.post(
            f"{HA_URL}/api/services/hungarian_transport/elvira_between?return_response",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json=data,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                return web.json_response({"error": f"HTTP {resp.status}"}, status=502)
            res = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return web.json_response({"error": type(err).__name__}, status=502)
    return web.json_response({"response": res.get("service_response")})


async def svc(request: web.Request) -> web.Response:
    body = await request.json()
    domain, service = body.get("domain"), body.get("service")
    data = body.get("service_data") or {}
    session = request.app["session"]
    if domain == "hungarian_transport" and service == "futar":
        return await futar(session, data)
    if domain == "hungarian_transport" and service == "elvira_between":
        return await elvira(session, data)
    return web.json_response({"error": f"{domain}.{service} not available"}, status=404)


@web.middleware
async def no_cache(request: web.Request, handler):
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


async def main() -> None:
    port = int(os.environ.get("PORT", "8765"))
    if not os.path.isfile(os.path.join(WWW, "hungarian-transport.js")):
        sys.exit(f"{WWW} has no hungarian-transport.js: see docs/manual/tools/README.md")
    print("BKK key set:", bool(BKK_API_KEY), "| Home Assistant for ELVIRA:", bool(HA_URL and HA_TOKEN), flush=True)
    session = aiohttp.ClientSession()
    app = web.Application(middlewares=[no_cache])
    app["session"] = session
    app.router.add_post("/svc", svc)
    app.router.add_static("/ht/", WWW, show_index=False)
    app.router.add_static("/", HERE, show_index=False)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    print(f"listening on http://127.0.0.1:{port}/index.html", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main())
