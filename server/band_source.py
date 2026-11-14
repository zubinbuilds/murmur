"""Connects to the Vexo band (or its pretend twin) and feeds decoded audio to the pipeline.

The band is a Bluetooth device that accepts one connection at a time, so this is the only
place that talks to it. Close the kit's dashboard tab before starting the server.
"""

from __future__ import annotations

import asyncio
import sys

from .config import KIT_PYTHON, SAMPLE_SPEECH


def _explain(error: Exception) -> str:
    return getattr(error, "message", None) or str(error) or error.__class__.__name__


class BandSource:
    def __init__(self, cfg, pipeline) -> None:
        self.cfg = cfg
        self.pipeline = pipeline

    async def run(self) -> None:
        """Connect, stream, and start again if the band goes away."""
        last = None
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                message = _explain(e)
                self.pipeline.set_band_status("error", message)
                if message != last:  # the status line keeps showing it; the page log gets each new error once
                    self.pipeline.error("band", message)
                last = message
                await asyncio.sleep(4)

    async def _session(self) -> None:
        if not KIT_PYTHON.exists():
            raise RuntimeError("vexo-open-kit/ is missing. Run scripts/setup.sh.")
        if str(KIT_PYTHON) not in sys.path:
            sys.path.insert(0, str(KIT_PYTHON))
        from vexo_open import audio
        from vexo_open.band import Band

        cfg = self.cfg
        name = f"Vexo-HACK-{cfg.band_number:02d}"
        self.pipeline.set_band_status("connecting", f"Looking for {name}. Double-tap the band if it is not found.")

        options = {}
        if cfg.transport == "sim":
            options["audio_packets"] = audio.ogg_packets(SAMPLE_SPEECH)
        elif cfg.transport == "serial" and cfg.serial_port:
            options["port"] = cfg.serial_port
        band = await Band.connect(cfg.band_number, transport=cfg.transport, auto_reconnect=True, **options)
        decoder = await asyncio.to_thread(audio.PcmDecoder)  # the first one takes a few seconds

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        gone = asyncio.Event()

        def on_audio(packet) -> None:
            try:
                queue.put_nowait(decoder.feed(packet))
            except asyncio.QueueFull:
                pass

        band.on("audio", on_audio)
        band.on("disconnect", lambda _e: gone.set())
        band.on("reconnecting", lambda _e: self.pipeline.set_band_status("connecting", "Link dropped, reconnecting."))
        band.on("reconnected", lambda _e: self.pipeline.set_band_status("connected", band.name))

        await band.set_mic_gain(cfg.mic_gain_db)
        streams = {"audio": True}
        if cfg.transport != "sim" and cfg.toggle_gesture != "none":
            await band.set_events(kinds=[cfg.toggle_gesture], tap_threshold=cfg.tap_threshold)
            band.on(cfg.toggle_gesture, lambda _e: asyncio.get_running_loop().create_task(
                self.pipeline.toggle_listening()))
            streams["events"] = True
        await band.configure(**streams)
        self.pipeline.set_band_status("connected" if cfg.transport != "sim" else "simulated", band.name)

        pump = asyncio.create_task(self._pump(queue))
        try:
            await gone.wait()
        finally:
            pump.cancel()
            await band.disconnect()
        self.pipeline.set_band_status("error", "The band disconnected.")

    async def _pump(self, queue: asyncio.Queue) -> None:
        while True:
            pcm = await queue.get()
            # The band sends 20 ms packets; a frame that is not exactly 20 ms is padded by the gate.
            await self.pipeline.on_pcm(pcm)
