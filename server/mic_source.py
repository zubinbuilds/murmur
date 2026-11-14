"""Feeds a mic plugged into the Mac to the pipeline: a backup for the band, 16 kHz mono int16 out."""

from __future__ import annotations

import asyncio

import numpy as np

from .band_source import _explain

RATE = 16000
FRAME_BYTES = 640  # 20 ms at 16 kHz, int16


class Resampler:
    """Box-filter resampling to 16 kHz: each output sample is the mean of the input it covers.

    For 48 kHz that is the plain mean of every three samples, which also keeps hiss above
    8 kHz from folding back into the speech band. Leftover input carries into the next block.
    """

    def __init__(self, rate: float) -> None:
        self.step = rate / RATE
        self.buf = np.zeros(0)
        self.pos = 0.0  # where the next output sample starts, in samples of buf

    def feed(self, block: np.ndarray) -> np.ndarray:
        buf = np.concatenate([self.buf, block])
        n = int((len(buf) - self.pos) // self.step)
        edges = self.pos + self.step * np.arange(n + 1)
        area = np.interp(edges, np.arange(len(buf) + 1), np.concatenate(([0.0], np.cumsum(buf))))
        keep = int(edges[-1])
        self.buf, self.pos = buf[keep:], edges[-1] - keep
        return np.diff(area) / self.step


class Framer:
    """Float blocks at the device rate in, 640-byte int16 frames out; a partial frame waits."""

    def __init__(self, rate: float) -> None:
        self.resampler = Resampler(rate)
        self.pending = b""

    def feed(self, block: np.ndarray) -> list[bytes]:
        pcm = np.clip(np.rint(self.resampler.feed(block) * 32767), -32768, 32767).astype(np.int16)
        data = self.pending + pcm.tobytes()
        cut = len(data) - len(data) % FRAME_BYTES
        self.pending = data[cut:]
        return [data[i:i + FRAME_BYTES] for i in range(0, cut, FRAME_BYTES)]


class MicSource:
    silence_timeout_s = 3.0  # no callback for this long means the device went away

    def __init__(self, cfg, pipeline, sd=None) -> None:
        self.cfg = cfg
        self.pipeline = pipeline
        self.sd = sd  # sounddevice, or a fake in tests

    async def run(self) -> None:
        """Open the mic, stream, and start again if it fails."""
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.pipeline.set_band_status("error", _explain(e))
                self.pipeline.error("mic", _explain(e))
                await asyncio.sleep(4)

    async def _session(self) -> None:
        sd = self.sd
        if sd is None:
            import sounddevice as sd
        device = self.cfg.mic_device or None
        info = sd.query_devices(device, "input")
        name, rate = info["name"], float(info["default_samplerate"])
        self.pipeline.set_band_status("connecting", f"Opening mic: {name}")

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        def offer(block) -> None:
            try:
                queue.put_nowait(block)
            except asyncio.QueueFull:
                pass

        def callback(indata, frames, time, status) -> None:  # runs on the audio thread
            loop.call_soon_threadsafe(offer, indata[:, 0].copy())

        framer = Framer(rate)
        with sd.InputStream(device=device, channels=1, samplerate=rate, dtype="float32", callback=callback):
            self.pipeline.set_band_status("connected", f"mic: {name}")
            while True:
                try:
                    block = await asyncio.wait_for(queue.get(), self.silence_timeout_s)
                except asyncio.TimeoutError:
                    raise RuntimeError(f"No audio from {name} for {self.silence_timeout_s:g} s.") from None
                for frame in framer.feed(block):
                    await self.pipeline.on_pcm(frame)
