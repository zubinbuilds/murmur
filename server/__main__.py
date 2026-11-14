"""Run with:  .venv/bin/python -m server [--sim] [--engine elevenlabs|deepgram|mock]"""

from __future__ import annotations

import argparse

from aiohttp import web

from .app import build_app
from .config import Config
from .stt import ENGINES


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m server", description=__doc__)
    parser.add_argument("--sim", action="store_true", help="use the kit's pretend band (no hardware)")
    parser.add_argument("--engine", choices=ENGINES, help="speech engine (default: VOCAL_STT from .env)")
    parser.add_argument("--band", type=int, help="band number, e.g. 82 for Vexo-HACK-82")
    parser.add_argument("--port", type=int, help="port for the web page (default 8000)")
    parser.add_argument("--no-band", action="store_true", help="serve the page only; do not connect to a band")
    args = parser.parse_args()

    cfg = Config.from_env()
    if args.sim:
        cfg.transport = "sim"
    if args.engine:
        cfg.stt = args.engine
    if args.band:
        cfg.band_number = args.band
    if args.port:
        cfg.port = args.port

    print(f"Vocal: open http://{cfg.host}:{cfg.port}")
    print(f"  band {cfg.band_number} ({cfg.transport}), speech: {cfg.stt}, "
          f"{'DRY RUN: nothing will be sent' if cfg.dry_run else 'LIVE: commands are texted to Instinct'}")
    web.run_app(build_app(cfg, with_band=not args.no_band), host=cfg.host, port=cfg.port, print=None)


if __name__ == "__main__":
    main()
