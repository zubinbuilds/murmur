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


def voiced(db, count, f0=140.0):
    """A pitched talker: harmonics of f0, scaled to db."""
    t = np.arange(FRAME_SAMPLES * count) / 16000
    sig = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, 12))
    pcm = (sig / np.sqrt(np.mean(sig ** 2)) * 32768 * 10 ** (db / 20)).astype(np.int16)
    return [pcm[i:i + FRAME_SAMPLES].tobytes() for i in range(0, len(pcm), FRAME_SAMPLES)]


class WhisperSceneTests(unittest.TestCase):
    """Whisper mode: the owner's whisper is unvoiced; people talking at the same level are voiced."""

    def setUp(self):
        self.rng = np.random.default_rng(12)
        self.gate = WhisperGate(GateSettings(whisper_only=True))
        self.run_gate(frames(-32, 100, self.rng))  # the room, so the floor is learned

    def run_gate(self, chunks):
        return [self.gate.process(c) for c in chunks]

    def test_a_whisper_burst_of_300_ms_passes_whole(self):
        burst = frames(-18, 15, self.rng)
        outs = self.run_gate(burst + frames(-32, 60, self.rng))
        self.assertEqual(sum(o.turn_end for o in outs), 1)
        passed = [o.frame for o in outs if o.frame is not SILENCE]
        self.assertTrue(all(b in passed for b in burst))  # the onset frames came out of the pre-roll

    def test_voiced_speech_with_100_ms_consonant_gaps_opens_nothing(self):
        talk = []
        for _ in range(8):  # vowels with short unvoiced consonants between them, all at the whisper's level
            talk += voiced(-18, 15) + frames(-18, 5, self.rng)
        outs = self.run_gate(talk + frames(-32, 60, self.rng))
        self.assertFalse(any(o.open for o in outs))

    def test_a_single_voiced_frame_inside_a_whisper_is_kept(self):
        whisper = frames(-18, 20, self.rng) + voiced(-18, 1) + frames(-18, 20, self.rng)
        outs = self.run_gate(whisper + frames(-32, 60, self.rng))
        passed = [o.frame for o in outs if o.frame is not SILENCE]
        self.assertIn(whisper[20], passed)

    def test_a_talker_during_a_whisper_turn_is_silenced_and_cannot_hold_it_open(self):
        talker = voiced(-18, 150)  # three seconds of someone talking, right after the owner's whisper
        outs = self.run_gate(frames(-18, 20, self.rng) + talker + frames(-32, 60, self.rng))
        passed = {o.frame for o in outs if o.frame is not SILENCE}
        self.assertFalse(passed & set(talker))
        self.assertEqual(sum(o.turn_end for o in outs), 1)
        self.assertFalse(outs[-1].open)
        closed_at = next(i for i, o in enumerate(outs) if o.turn_end)
        self.assertLess(closed_at, 20 + 50)  # ended within about a second of the talker starting, not after 3 s


if __name__ == "__main__":
    unittest.main()
