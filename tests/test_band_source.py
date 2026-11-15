import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from server.band_source import BandSource
from server.config import Config


class Stop(Exception):
    """Raised by the fake Band.connect so the session ends before any hardware would be used."""


class FakePipeline:
    def __init__(self):
        self.statuses = []

    def set_band_status(self, state, detail=""):
        self.statuses.append(state)


def fake_kit(calls, band_obj=None):
    class Band:
        @classmethod
        async def connect(cls, number, **kwargs):
            calls.append((number, kwargs))
            if band_obj is None:
                raise Stop
            return band_obj

    class PcmDecoder:
        def feed(self, packet):
            return packet  # the fake band's audio packets are already PCM

    package = types.ModuleType("vexo_open")
    audio = types.ModuleType("vexo_open.audio")
    audio.ogg_packets = lambda path: ["packet"]
    audio.PcmDecoder = PcmDecoder
    band = types.ModuleType("vexo_open.band")
    band.Band = Band
    package.audio, package.band = audio, band
    return {"vexo_open": package, "vexo_open.audio": audio, "vexo_open.band": band}


class FakeBand:
    """Records configure() calls; the test fires audio, motion and disconnect through emit()."""

    def __init__(self, firmware="1.1.0", features=("fast-motion", "intervals", "clock"), accel=1666,
                 motion_error=None):
        self.name = "Vexo-HACK-82"
        self.info = types.SimpleNamespace(firmware=firmware, features=list(features))
        self.accel, self.motion_error = accel, motion_error
        self.handlers, self.configured = {}, []

    def on(self, kind, handler):
        self.handlers.setdefault(kind, []).append(handler)

    def emit(self, kind, value=None):
        for handler in self.handlers.get(kind, []):
            handler(value)

    async def set_mic_gain(self, db):
        pass

    async def set_events(self, **kwargs):
        pass

    async def motion_limits(self):
        return types.SimpleNamespace(accel=self.accel)

    async def configure(self, **streams):
        if "motion" in streams and self.motion_error:
            raise self.motion_error
        self.configured.append(streams)

    async def disconnect(self):
        pass


class MotionPipeline(FakePipeline):
    def __init__(self, with_motion=True):
        super().__init__()
        self.details, self.notes, self.pcm, self.events = [], [], [], []
        if with_motion:
            self.on_motion = lambda batch: self.events.append(("motion", [s.n for s in batch]))

    def set_band_status(self, state, detail=""):
        super().set_band_status(state, detail)
        self.details.append(detail)

    def note(self, text):
        self.notes.append(text)

    def error(self, where, message):
        pass

    async def on_pcm(self, frame):
        self.pcm.append(frame)
        self.events.append(("pcm", frame))


class BandSourceConnectTests(unittest.IsolatedAsyncioTestCase):
    async def connect_args(self, cfg):
        calls = []
        with mock.patch.dict(sys.modules, fake_kit(calls)), \
                mock.patch("server.band_source.KIT_PYTHON", Path(__file__).parent):
            with self.assertRaises(Stop):
                await BandSource(cfg, FakePipeline())._session()
        return calls[0]

    async def test_usb_with_a_port_passes_it_to_the_kit(self):
        number, kwargs = await self.connect_args(Config(transport="serial", serial_port="/dev/cu.usbmodem101"))
        self.assertEqual(number, 82)
        self.assertEqual(kwargs["transport"], "serial")
        self.assertEqual(kwargs["port"], "/dev/cu.usbmodem101")

    async def test_usb_without_a_port_lets_the_kit_find_it(self):
        _, kwargs = await self.connect_args(Config(transport="serial"))
        self.assertEqual(kwargs["transport"], "serial")
        self.assertNotIn("port", kwargs)

    async def test_bluetooth_is_unchanged(self):
        _, kwargs = await self.connect_args(Config(transport="ble", serial_port="/dev/cu.usbmodem101"))
        self.assertEqual(kwargs, {"transport": "ble", "auto_reconnect": True})

    async def test_bluetooth_status_names_the_double_tap_and_the_dashboard(self):
        for transport, expect in (("ble", True), ("serial", False)):
            pipeline = MotionPipeline()
            with mock.patch.dict(sys.modules, fake_kit([])), \
                    mock.patch("server.band_source.KIT_PYTHON", Path(__file__).parent):
                with self.assertRaises(Stop):
                    await BandSource(Config(transport=transport), pipeline)._session()
            self.assertEqual("Double-tap" in pipeline.details[0] and "dashboard" in pipeline.details[0], expect)


class MotionTests(unittest.IsolatedAsyncioTestCase):
    async def run_session(self, cfg, band, pipeline, script=None):
        """Runs one session against the fake band; script(band) fires events, then the band disconnects."""
        with mock.patch.dict(sys.modules, fake_kit([], band)), \
                mock.patch("server.band_source.KIT_PYTHON", Path(__file__).parent):
            session = asyncio.create_task(BandSource(cfg, pipeline)._session())
            for _ in range(50):
                await asyncio.sleep(0)
                if pipeline.statuses[-1:] in (["connected"], ["simulated"]):
                    break
            if script:
                await script(band)
            band.emit("disconnect")
            await session

    def motion_calls(self, band):
        return [c for c in band.configured if "motion" in c]

    async def test_usb_streams_the_accelerometer_at_the_link_limit(self):
        band, pipeline = FakeBand(accel=1666), MotionPipeline()
        await self.run_session(Config(transport="serial"), band, pipeline)
        self.assertEqual(self.motion_calls(band), [{"motion": 1666, "sensors": "accel"}])
        self.assertEqual(band.configured[0], {"audio": True, "events": True})
        self.assertIn("firmware 1.1.0 (fast-motion, intervals, clock), motion 1666/s accel", pipeline.details[-2])

    async def test_bluetooth_stays_at_416_next_to_audio(self):
        band = FakeBand(accel=833)
        await self.run_session(Config(transport="ble"), band, MotionPipeline())
        self.assertEqual(self.motion_calls(band), [{"motion": 416, "sensors": "accel"}])

    async def test_firmware_1_0_falls_back_to_100_with_both_sensors(self):
        band, pipeline = FakeBand(firmware="1.0.0", features=()), MotionPipeline()
        await self.run_session(Config(transport="ble"), band, pipeline)
        self.assertEqual(self.motion_calls(band), [{"motion": 100}])
        self.assertIn("no 1.1 features), motion 100/s both sensors", pipeline.details[-2])

    async def test_a_motion_failure_is_noted_once_and_audio_carries_on(self):
        band, pipeline = FakeBand(motion_error=RuntimeError("denied")), MotionPipeline()

        async def script(b):
            b.emit("audio", b"\x01\x00" * 320)
            await asyncio.sleep(0.01)

        await self.run_session(Config(transport="serial"), band, pipeline, script)
        self.assertEqual(pipeline.notes, ["Motion is off, audio carries on: denied"])
        self.assertIn("motion off", pipeline.details[-2])
        self.assertEqual(len(pipeline.pcm), 1)

    async def test_samples_since_the_last_frame_reach_on_motion_after_it(self):
        band, pipeline = FakeBand(), MotionPipeline()

        async def script(b):
            for n in range(3):
                b.emit("motion", types.SimpleNamespace(n=n, t=n * 0.6, ax=0.0, ay=0.0, az=1.0))
            b.emit("audio", b"frame")
            await asyncio.sleep(0.01)

        await self.run_session(Config(transport="serial"), band, pipeline, script)
        self.assertEqual(pipeline.events, [("pcm", b"frame"), ("motion", [0, 1, 2])])

    async def test_a_pipeline_without_on_motion_still_gets_audio(self):
        band, pipeline = FakeBand(), MotionPipeline(with_motion=False)

        async def script(b):
            b.emit("motion", types.SimpleNamespace(n=0))
            b.emit("audio", b"frame")
            await asyncio.sleep(0.01)

        await self.run_session(Config(transport="serial"), band, pipeline, script)
        self.assertNotIn("motion", band.handlers)
        self.assertEqual(pipeline.pcm, [b"frame"])

    async def test_the_pretend_band_leaves_motion_off(self):
        band, pipeline = FakeBand(), MotionPipeline()
        await self.run_session(Config(transport="sim"), band, pipeline)
        self.assertEqual(self.motion_calls(band), [])
        self.assertIn("motion off", pipeline.details[-2])


if __name__ == "__main__":
    unittest.main()
