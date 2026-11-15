"""The uncalibrated gate on a band whose own quiet level sits above the -45 dB default (live, 15:01: -34 dB)."""

import unittest

import numpy as np

from server.gate import FLOOR_WINDOW_FRAMES, FRAME_BYTES, FRAME_SAMPLES, WHISPER_MARGIN_DB, GateSettings, WhisperGate


def frames(db: float, count: int, rng) -> list:
    rms = 32768 * 10 ** (db / 20)
    return [rng.normal(0, rms, FRAME_SAMPLES).astype(np.int16).tobytes() for _ in range(count)]


class BandFloorTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(5)

    def settle(self, gate):
        """Five seconds of the band's quiet: the floor learns it and the stuck startup turn ends."""
        return [gate.process(f) for f in frames(-34, FLOOR_WINDOW_FRAMES + 50, self.rng)]

    def test_the_band_noise_turn_ends_and_a_louder_whisper_is_one_turn(self):
        for whisper_only in (False, True):
            gate = WhisperGate(GateSettings(whisper_only=whisper_only))
            self.settle(gate)
            self.assertFalse(gate.is_open)
            margin = WHISPER_MARGIN_DB if whisper_only else GateSettings().margin_db
            self.assertAlmostEqual(gate.threshold, -34 + margin, delta=1.5)
            outs = [gate.process(f) for f in frames(-15, 50, self.rng) + frames(-34, 60, self.rng)]
            self.assertEqual(sum(o.turn_end for o in outs), 1, whisper_only)
            self.assertFalse(gate.is_open)

    def test_dropouts_do_not_drag_the_floor_back_down(self):
        gate = WhisperGate()
        self.settle(gate)
        for _ in range(2 * FLOOR_WINDOW_FRAMES):
            gate.process(bytes(FRAME_BYTES))
        self.assertAlmostEqual(gate.floor_db, -34, delta=1.5)

    def test_nearby_talk_does_not_lift_the_whisper_mode_floor(self):
        """Live 16:04: 9 s of people talking lifted the floor 6 dB and the owner's whisper stopped clearing it."""
        t = np.arange(FRAME_SAMPLES * 450) / 16000
        talk = sum(np.sin(2 * np.pi * 140 * h * t) / h for h in range(1, 12))
        talk = (talk / np.sqrt(np.mean(talk ** 2)) * 32768 * 10 ** (-20 / 20)).astype(np.int16)
        talk_frames = [talk[i:i + FRAME_SAMPLES].tobytes() for i in range(0, len(talk), FRAME_SAMPLES)]
        floors = {}
        for whisper_only in (True, False):
            gate = WhisperGate(GateSettings(whisper_only=whisper_only))
            self.settle(gate)
            for f in talk_frames:
                gate.process(f)
            floors[whisper_only] = gate.floor_db
        self.assertAlmostEqual(floors[True], -34, delta=1.5)  # the room's floor, held through the talk
        self.assertGreater(floors[False], -25)  # voice mode: talk is the room, as before

    def test_calibration_overrides_the_learned_floor(self):
        gate = WhisperGate()
        self.settle(gate)
        gate.set_threshold(-40.0)
        self.assertEqual(gate.threshold, -40.0)
        self.assertTrue(any(gate.process(f).open for f in frames(-34, 10, self.rng)))


if __name__ == "__main__":
    unittest.main()
