"""The localhost web app: serves the page and a WebSocket that carries events both ways."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlparse

from aiohttp import WSCloseCode, WSMsgType, web

from .band_source import BandSource
from .config import WEB_DIR, Config
from .hub import Hub
from .mic_source import MicSource
from .pipeline import Pipeline
from .stt import ENGINES


CFG = web.AppKey("cfg", Config)
HUB = web.AppKey("hub", Hub)
PIPELINE = web.AppKey("pipeline", Pipeline)
BAND = web.AppKey("band", asyncio.Task)
_background: set = set()  # strong references, so running "say" tasks are not garbage-collected


def origin_allowed(request: web.Request, port: int) -> bool:
    """Only pages served by this server may drive it.

    Without this, any website you have open could connect to ws://localhost and make Murmur
    send messages for you. Browsers always send Origin on WebSocket connections.
    """
    origin = request.headers.get("Origin")
    if origin is None:
        return True  # not a browser (a script, or the tests)
    parsed = urlparse(origin)
    return parsed.hostname in ("localhost", "127.0.0.1") and parsed.port == port


async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB_DIR / "index.html")


async def revalidate(request: web.Request, response: web.StreamResponse) -> None:
    # Without this the browser may keep running an old app.js after the page files change.
    response.headers["Cache-Control"] = "no-cache"


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
            except (ValueError, KeyError, TypeError, RecursionError) as e:  # RecursionError: deeply nested JSON
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
        db = float(data["db"])
        if not -90 <= db <= 0:  # also refuses NaN and inf, which would make every status event invalid JSON
            raise ValueError(f"threshold must be -90 to 0 dB, got {db}")
        pipeline.set_threshold(db)
    elif cmd == "enroll":
        pipeline.enroll()
    elif cmd == "whisper_only":
        pipeline.set_whisper_only(bool(data["on"]))
    elif cmd == "own_voice":
        pipeline.set_own_voice(str(data["mode"]))
    elif cmd == "clear":
        pipeline.hub.clear()
    elif cmd == "say":
        if not isinstance(data["text"], str):
            raise TypeError("say needs text")
        # in the background: a command can wait 15 s on Gemini, and this page's other commands
        # (and Ctrl+C, which waits for this handler) must not wait behind it
        task = asyncio.create_task(pipeline.inject(data["text"][:500]))
        _background.add(task)
        task.add_done_callback(_background.discard)
    else:
        raise ValueError(f"unknown command {cmd!r}")


def build_app(cfg: Config, *, with_band: bool = True, engine_factory=None, agent=None) -> web.Application:
    hub = Hub()
    pipeline = Pipeline(cfg, hub, engine_factory=engine_factory, agent=agent)
    app = web.Application()
    app.on_response_prepare.append(revalidate)
    app[CFG], app[HUB], app[PIPELINE] = cfg, hub, pipeline
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket)
    app.router.add_static("/static", WEB_DIR)

    async def on_startup(app: web.Application) -> None:
        hub.start()
        await pipeline.start()
        if with_band:
            source = MicSource if cfg.transport == "mic" else BandSource
            app[BAND] = asyncio.create_task(source(cfg, pipeline).run())

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

    async def on_shutdown(app: web.Application) -> None:
        # an open page otherwise holds Ctrl+C for about 15 s while aiohttp waits on its socket
        for ws in list(hub.clients):
            await ws.close(code=WSCloseCode.GOING_AWAY, message=b"Server shutting down")

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    return app
