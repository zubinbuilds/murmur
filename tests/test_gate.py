import unittest

import numpy as np

from server.gate import FRAME_BYTES, FRAME_SAMPLES, GateSettings, WhisperGate, frame_db, suggest_threshold


def noise(db: float, rng) -> bytes:
    rms = 32768 * 10 ** (db / 20)
    return rng.normal(0, rms, FRAME_SAMPLES).astype(np.int16).tobytes()


def run(gate, rng, db, frames):
    return [gate.process(noise(db, rng)) for _ in range(frames)]


class GateTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(1)

    def test_frame_db(self):
        self.assertAlmostEqual(frame_db(noise(-30, self.rng)), -30, delta=1.0)
        self.assertLess(frame_db(bytes(FRAME_BYTES)), -85)

    def test_quiet_room_never_opens(self):
        gate = WhisperGate()
        outs = run(gate, self.rng, -70, 100)
        self.assertFalse(any(o.open or o.turn_end for o in outs))
        self.assertFalse(any(any(o.frame) for o in outs))  # everything was replaced by silence

    def test_whisper_turn_opens_passes_audio_and_ends_once(self):
        gate = WhisperGate()
        outs = run(gate, self.rng, -70, 20) + run(gate, self.rng, -30, 25) + run(gate, self.rng, -70, 60)
        self.assertEqual(sum(o.turn_end for o in outs), 1)
        self.assertTrue(all(len(o.frame) == FRAME_BYTES for o in outs))
        passed = [i for i, o in enumerate(outs) if any(o.frame) and frame_db(o.frame) > -50]
        self.assertGreaterEqual(len(passed), 25)  # the whole whisper made it through, delayed

    def test_preroll_keeps_the_first_syllable(self):
        gate = WhisperGate(GateSettings(threshold_db=-45, onset_ms=60, preroll_ms=200))
        outs = run(gate, self.rng, -70, 20) + run(gate, self.rng, -30, 30) + run(gate, self.rng, -70, 60)
        loud_out = sum(1 for o in outs if frame_db(o.frame) > -50)
        self.assertEqual(loud_out, 30)  # not 30 minus the onset frames

    def test_turn_ends_after_the_hangover_not_before(self):
        gate = WhisperGate(GateSettings(hangover_ms=700))
        run(gate, self.rng, -30, 10)
        early = run(gate, self.rng, -70, 30)  # 600 ms of quiet
        self.assertFalse(any(o.turn_end for o in early))
        later = run(gate, self.rng, -70, 10)
        self.assertTrue(any(o.turn_end for o in later))

    def test_a_pause_between_words_does_not_end_the_turn(self):
        gate = WhisperGate(GateSettings(hangover_ms=700))
        outs = run(gate, self.rng, -30, 20) + run(gate, self.rng, -70, 20) + run(gate, self.rng, -30, 20)
        self.assertFalse(any(o.turn_end for o in outs))

    def test_a_very_long_turn_is_committed_anyway(self):
        gate = WhisperGate(GateSettings(max_turn_ms=1000))
        outs = run(gate, self.rng, -30, 120)
        self.assertGreaterEqual(sum(o.turn_end for o in outs), 2)

    def test_calibrated_threshold_blocks_music_and_passes_whisper(self):
        gate = WhisperGate()
        music, whisper = -38.0, -26.0
        threshold = suggest_threshold([music] * 100, [whisper] * 100)
        assert threshold is not None
        gate.set_threshold(threshold)
        music_only = run(gate, self.rng, music, 100)
        self.assertFalse(any(o.open for o in music_only))
        spoken = run(gate, self.rng, whisper, 20) + run(gate, self.rng, music, 60)
        self.assertEqual(sum(o.turn_end for o in spoken), 1)

    def test_calibration_refuses_when_whisper_is_not_louder_than_the_room(self):
        self.assertIsNone(suggest_threshold([-40.0] * 100, [-38.0] * 100))
        self.assertIsNone(suggest_threshold([-40.0] * 5, [-20.0] * 100))


if __name__ == "__main__":
    unittest.main()
