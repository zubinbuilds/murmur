"""Connects to the Vexo band (or its pretend twin) and feeds decoded audio to the pipeline.

The band is a Bluetooth device that accepts one connection at a time, so this is the only
place that talks to it. Close the kit's dashboard tab before starting the server.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections import deque

from .config import KIT_PYTHON, SAMPLE_SPEECH

# Accelerometer rates (samples a second). The wearer's own speech shakes the band in the mouth; other
# voices do not. Over Bluetooth, audio shares the link, and the kit's docs say to stay at 416 with one
# sensor; USB carries up to 1666 with the accelerometer alone; firmware 1.0 has 100 with both sensors.
MOTION_CAP = {"serial": 1666, "ble": 416}
MOTION_RATE_FW10 = 100
MOTION_BUFFER = 4096  # samples kept between audio frames, so a stalled audio stream cannot grow memory


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
        looking = f"Looking for {name}."
        if cfg.transport == "ble":  # the kit's getting-started.md: one Bluetooth connection, found for 60 s after a double-tap
            looking += (" Double-tap the band (it can be found for 60 s after), and close the Vexo dashboard"
                        " or any other computer connected to it.")
        self.pipeline.set_band_status("connecting", looking)

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

        motion: deque = deque(maxlen=MOTION_BUFFER)
        on_motion = getattr(self.pipeline, "on_motion", None)
        if on_motion is not None:
            band.on("motion", motion.append)
        about = await self._start_motion(band)
        self.pipeline.set_band_status("connected" if cfg.transport != "sim" else "simulated",
                                      f"{band.name}, {about}")

        pump = asyncio.create_task(self._pump(queue, motion, on_motion))
        try:
            await gone.wait()
        finally:
            pump.cancel()
            await band.disconnect()
        self.pipeline.set_band_status("error", "The band disconnected.")

    async def _start_motion(self, band) -> str:
        """Turns on the accelerometer next to audio; returns firmware, features and rate for the status line."""
        info = band.info
        features = list(info.features) if info is not None else []
        about = f"firmware {info.firmware if info is not None else '?'} ({', '.join(features) or 'no 1.1 features'})"
        if self.cfg.transport not in MOTION_CAP:  # the pretend band: motion stays off
            return f"{about}, motion off"
        try:
            if "fast-motion" in features:
                limits = await band.motion_limits()
                rate = min(MOTION_CAP[self.cfg.transport], limits.accel or MOTION_CAP["ble"])
                await band.configure(motion=rate, sensors="accel")
                return f"{about}, motion {rate}/s accel"
            await band.configure(motion=MOTION_RATE_FW10)
            return f"{about}, motion {MOTION_RATE_FW10}/s both sensors"
        except Exception as e:  # motion is a measurement aid; audio must keep working without it
            self.pipeline.note(f"Motion is off, audio carries on: {_explain(e)}")
            return f"{about}, motion off"

    async def _pump(self, queue: asyncio.Queue, motion: deque, on_motion) -> None:
        while True:
            pcm = await queue.get()
            # The band sends 20 ms packets; a frame that is not exactly 20 ms is padded by the gate.
            await self.pipeline.on_pcm(pcm)
            if motion and on_motion is not None:
                batch = list(motion)  # the kit's motion Messages (t ms, ax ay az in g, n) since the last frame
                motion.clear()
                result = on_motion(batch)
                if inspect.isawaitable(result):
                    await result
