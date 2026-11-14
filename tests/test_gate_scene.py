"""Regression for the owner's band recording at 15:26, rebuilt from its measured levels (the WAV stays out of git).

Floor about -32 dB, someone else talking next to the owner in bursts at about -21 dB, the owner at about -12 dB.
"""

import unittest

import numpy as np

from server.gate import FRAME_SAMPLES, SILENCE, GateSettings, WhisperGate


def frames(db, count, rng):
    rms = 32768 * 10 ** (db / 20)
    return [rng.normal(0, rms, FRAME_SAMPLES).astype(np.int16).tobytes() for _ in range(count)]


class BandSceneTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(11)
        self.gate = WhisperGate(GateSettings())  # the defaults: uncalibrated, margin 14 dB, whispers-only off

    def run_gate(self, chunks):
        return [self.gate.process(c) for c in chunks]

    def test_quiet_start_does_not_hold_the_gate_open(self):
        outs = self.run_gate(frames(-32, 250, self.rng))
        # open at most for the 0.5 s warm-up plus the 0.7 s hangover, not for the 5 s floor window
        self.assertLessEqual(sum(o.open for o in outs), 60)
        self.assertFalse(outs[-1].open)

    def test_someone_else_at_minus_21_never_opens_a_turn_and_the_owner_does(self):
        self.run_gate(frames(-32, 100, self.rng))
        others = []
        for _ in range(6):  # ten seconds of another person's speech in bursts
            others += self.run_gate(frames(-21, 60, self.rng) + frames(-32, 40, self.rng))
        self.assertFalse(any(o.open for o in others))
        self.assertEqual(sum(o.turn_end for o in others), 0)
        owner = self.run_gate(frames(-12, 100, self.rng) + frames(-32, 60, self.rng))
        self.assertEqual(sum(o.turn_end for o in owner), 1)
        self.assertGreaterEqual(sum(o.frame is not SILENCE for o in owner), 100)  # all of it passed


if __name__ == "__main__":
    unittest.main()
