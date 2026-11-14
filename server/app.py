"""The localhost web app: serves the page and a WebSocket that carries events both ways."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from aiohttp import WSMsgType, web

from .band_source import BandSource
from .config import WEB_DIR, Config
from .hub import Hub
from .pipeline import Pipeline
from .stt import ENGINES


CFG = web.AppKey("cfg", Config)
HUB = web.AppKey("hub", Hub)
PIPELINE = web.AppKey("pipeline", Pipeline)
BAND = web.AppKey("band", asyncio.Task)


def origin_allowed(request: web.Request, port: int) -> bool:
    """Only pages served by this server may drive it.

    Without this, any website you have open could connect to ws://localhost and make Vocal
    send messages for you. Browsers always send Origin on WebSocket connections.
    """
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # not a browser (a script, or the tests)
    parsed = urlparse(origin)
    return parsed.hostname in ("localhost", "127.0.0.1") and parsed.port == port


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB_DIR / "index.html")


async def websocket(request: web.Request) -> web.StreamResponse:
    if not origin_allowed(request, request.app[CFG].port):
        raise web.HTTPForbidden(text="Origin not allowed")
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    hub = request.app[HUB]
    pipeline = request.app[PIPELINE]
    await hub.attach(ws)
    pipeline.status()
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
                await handle_command(pipeline, data)
            except (ValueError, KeyError, TypeError) as e:
                pipeline.error("ui", f"Bad message: {e}")
    finally:
        hub.detach(ws)
    return ws


async def handle_command(pipeline: Pipeline, data: dict) -> None:
    cmd = data["cmd"]
    if cmd == "listen":
        await pipeline.set_listening(bool(data["on"]))
    elif cmd == "engine":
        if data["name"] not in ENGINES:
            raise ValueError(f"unknown engine {data['name']!r}")
        await pipeline.set_engine(data["name"])
    elif cmd == "calibrate":
        pipeline.calibrate(str(data["phase"]))
    elif cmd == "threshold":
        pipeline.set_threshold(float(data["db"]))
    elif cmd == "enroll":
        pipeline.enroll()
    elif cmd == "say":
        await pipeline.inject(str(data["text"])[:500])
    else:
        raise ValueError(f"unknown command {cmd!r}")


def build_app(cfg: Config, *, with_band: bool = True, engine_factory=None, agent=None) -> web.Application:
    hub = Hub()
    pipeline = Pipeline(cfg, hub, engine_factory=engine_factory, agent=agent)
    app = web.Application()
    app[CFG], app[HUB], app[PIPELINE] = cfg, hub, pipeline
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket)
    app.router.add_static("/static", WEB_DIR)

    async def on_startup(app: web.Application) -> None:
        hub.start()
        await pipeline.start()
        if with_band:
            app[BAND] = asyncio.create_task(BandSource(cfg, pipeline).run())

    async def on_cleanup(app: web.Application) -> None:
        task = app.get(BAND)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await pipeline.shutdown()
        await hub.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app
