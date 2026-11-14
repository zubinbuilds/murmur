"""The own-voice switch: the band's accelerometer (server/bodygate.py) deciding which turns are the wearer's."""

import unittest

import numpy as np

from server.config import Config
from server.gate import FRAME_SAMPLES, SILENCE, frame_db
from server.hub import Hub
from server.pipeline import Pipeline

RATE = 400  # Hz, motion samples a second
PER_FRAME = RATE // 50  # motion samples that arrive with each 20 ms audio frame


class Band:
    """Feeds a pipeline the way band_source does: one audio frame, then the motion since the last one."""

    def __init__(self, pipeline, clock):
        self.pipeline, self.clock = pipeline, clock
        self.rng = np.random.default_rng(4)
        self.t_ms = 0.0

    def motion(self, shake_g):
        out = []
        for _ in range(PER_FRAME):
            self.t_ms += 1000 / RATE
            buzz = shake_g * np.sin(2 * np.pi * 150 * self.t_ms / 1000)
            out.append({"t": self.t_ms, "ax": 0.0005 * self.rng.standard_normal() + buzz,
                        "ay": 0.0005 * self.rng.standard_normal(), "az": 1.0})
        return out

    async def run(self, db, frames, shake_g=0.0, with_motion=True):
        outs = []
        for _ in range(frames):
            pcm = self.rng.normal(0, 32768 * 10 ** (db / 20), FRAME_SAMPLES).astype(np.int16).tobytes()
            await self.pipeline.on_pcm(pcm)
            outs.append(self.pipeline.gate.is_open)
            if with_motion:
                self.pipeline.on_motion(self.motion(shake_g))
            self.clock[0] += 0.02
        return outs


class OwnVoiceTests(unittest.IsolatedAsyncioTestCase):
    def make(self, mode):
        self.clock = [0.0]
        self.hub = Hub()
        self.pipeline = Pipeline(Config(own_voice=mode), self.hub, engine_factory=lambda n: None,
                                 clock=lambda: self.clock[0])
        return Band(self.pipeline, self.clock)

    async def test_motion_mode_ignores_a_loud_voice_that_does_not_shake_the_band(self):
        band = self.make("motion")
        await band.run(-70, 75)  # quiet, still: the body gate warms up
        self.assertFalse(any(await band.run(-30, 50)))  # loud audio, band still: someone else

    async def test_motion_mode_opens_when_the_band_shakes_and_keeps_the_first_syllable(self):
        band = self.make("motion")
        await band.run(-70, 75)
        sent = []
        real = self.pipeline.gate.process
        self.pipeline.gate.process = lambda frame, body=None: sent.append(out := real(frame, body)) or out
        opened = await band.run(-30, 25, shake_g=0.05) + await band.run(-70, 60)
        self.assertTrue(any(opened))
        loud_out = sum(1 for o in sent if o.frame is not SILENCE and frame_db(o.frame) > -40)
        self.assertGreaterEqual(loud_out, 24)  # the frames before the body gate turned on came out of the pre-roll

    async def test_off_mode_opens_on_level_alone(self):
        band = self.make("off")
        await band.run(-70, 75)
        self.assertTrue(any(await band.run(-30, 50)))  # still band, loud audio: today's behaviour

    async def test_no_motion_for_a_second_falls_back_to_the_level_gate_with_one_note(self):
        band = self.make("motion")
        await band.run(-70, 75)
        self.assertTrue(any(await band.run(-30, 80, with_motion=False)))
        notes = [e["text"] for e in self.hub.events if e["type"] == "note" and "own-voice" in e["text"]]
        self.assertEqual(len(notes), 1)

    async def test_gate_event_and_status_report_the_body_gate(self):
        band = self.make("off")
        await band.run(-70, 100)
        gate = [e for e in self.hub.events if e["type"] == "gate"][-1]
        self.assertIn(gate["body_active"], (True, False))
        self.assertGreater(gate["body_floor"], 0)
        self.pipeline.set_own_voice("motion")
        self.assertEqual([e for e in self.hub.events if e["type"] == "status"][-1]["own_voice"], "motion")
        with self.assertRaises(ValueError):
            self.pipeline.set_own_voice("loud")


if __name__ == "__main__":
    unittest.main()
