"""Plays a recorded band session (a 16 kHz mono 16 bit WAV, as --record saves) into the pipeline in real time.

The same job as BandSource and MicSource, so one recording can be run through the gate and the speech
service again and again. It plays once and stops.
"""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

from .band_source import _explain
from .mic_source import FRAME_BYTES, RATE

FRAME_S = FRAME_BYTES / 2 / RATE  # 20 ms


def read_frames(path: Path) -> list:
    """The file as 640-byte frames, the last one padded with silence."""
    with wave.open(str(path), "rb") as w:
        shape = (w.getframerate(), w.getnchannels(), w.getsampwidth())
        if shape != (RATE, 1, 2):
            raise ValueError(f"{path.name} is {shape[0]} Hz, {shape[1]} channel(s), {8 * shape[2]} bit; "
                             f"replay needs {RATE} Hz mono 16 bit.")
        data = w.readframes(w.getnframes())
    data += bytes(-len(data) % FRAME_BYTES)
    return [data[i:i + FRAME_BYTES] for i in range(0, len(data), FRAME_BYTES)]


class ReplaySource:
    def __init__(self, cfg, pipeline, clock=None, sleep=asyncio.sleep) -> None:
        self.cfg = cfg
        self.pipeline = pipeline
        self.clock = clock  # tests pass a fake; the default is the event loop's clock
        self.sleep = sleep

    async def run(self) -> None:
        path = Path(self.cfg.replay_path)
        try:
            frames = read_frames(path)
        except (OSError, EOFError, wave.Error, ValueError) as e:
            message = f"Cannot replay {path}: {_explain(e)}"
            self.pipeline.set_band_status("error", message)
            self.pipeline.error("replay", message)
            return
        clock = self.clock or asyncio.get_running_loop().time
        self.pipeline.set_band_status("connected", f"replay: {path.name}")
        start = clock()
        for i, frame in enumerate(frames):
            wait = start + i * FRAME_S - clock()  # absolute deadlines, so slow frames do not add up to drift
            if wait > 0:
                await self.sleep(wait)
            await self.pipeline.on_pcm(frame)
        self.pipeline.set_band_status("finished", "replay finished")
