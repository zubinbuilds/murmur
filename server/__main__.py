"""Run with:  .venv/bin/python -m server [--sim | --mic [DEVICE]] [--engine elevenlabs|deepgram|pipecat|mock]"""

from __future__ import annotations

import argparse
import os
import sys

from aiohttp import web

from .app import build_app
from .config import OWN_VOICE_MODES, Config
from .stt import ENGINES


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m server", description=__doc__)
    parser.add_argument("--sim", action="store_true", help="use the kit's pretend band (no hardware)")
    parser.add_argument("--engine", choices=ENGINES, help="speech engine (default: MURMUR_STT from .env)")
    parser.add_argument("--band", type=int, help="band number, e.g. 82 for Vexo-HACK-82")
    parser.add_argument("--port", type=int, help="port for the web page (default 8000)")
    parser.add_argument("--no-band", action="store_true", help="serve the page only; do not connect to a band")
    parser.add_argument("--mic", nargs="?", const="", metavar="DEVICE",
                        help="listen on a Mac input instead of the band (default device if no name)")
    parser.add_argument("--own-voice", choices=OWN_VOICE_MODES,
                        help="motion: a turn needs the band's accelerometer to feel you speak (default off)")
    parser.add_argument("--record", action="store_true",
                        help="save the raw band audio (before the gate) as a WAV in recordings/")
    parser.add_argument("--replay", metavar="WAV",
                        help="play a recording (16 kHz mono 16 bit, as --record saves) once instead of the band")
    parser.add_argument("--usb", nargs="?", const="", metavar="PORT",
                        help="reach the band over its USB cable (the kit finds the port if no name)")
    args = parser.parse_args()

    try:  # Pipecat logs every frame at DEBUG through loguru; keep the terminal readable
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level="WARNING")
    except ImportError:
        pass

    cfg = Config.from_env()
    if args.record:
        cfg.record = True
    if args.own_voice:
        cfg.own_voice = args.own_voice
    if args.sim:
        cfg.transport = "sim"
        cfg.whisper_only = False  # the kit's sample recording is a normal speaking voice
    if args.mic is not None:
        cfg.transport = "mic"
        cfg.mic_device = args.mic or cfg.mic_device
    if args.usb is not None:
        cfg.transport = "serial"
        cfg.serial_port = args.usb or cfg.serial_port
    if args.replay:
        cfg.transport = "replay"  # whisper_only stays as configured: a band recording is the real input
        cfg.replay_path = args.replay
    if args.engine:
        cfg.stt = args.engine
    if args.band:
        cfg.band_number = args.band
    if args.port:
        cfg.port = args.port

    print(f"Murmur: open http://{cfg.host}:{cfg.port}")
    print(f"  band {cfg.band_number} ({cfg.transport}), speech: {cfg.stt}, "
          f"{'DRY RUN: nothing will be sent' if cfg.dry_run else 'LIVE: commands are texted to Instinct'}")
    web.run_app(build_app(cfg, with_band=not args.no_band), host=cfg.host, port=cfg.port, print=None)
    # run_app has cleaned up; a worker thread stuck in a network call (Gemini, the voice list) would otherwise
    # hold the exit for up to 15 s, because Python joins those threads at exit
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
