import unittest

import numpy as np

from server.config import Config
from server.gate import FRAME_SAMPLES, SILENCE, VOICED_PEAK, GateSettings, WhisperGate, voicing
from server.hub import Hub
from server.pipeline import Pipeline

RATE = 16000


def frames(signal: np.ndarray, db: float) -> list:
    """signal scaled to an RMS of db below full scale, cut into 20 ms int16 frames."""
    scaled = signal / np.sqrt(np.mean(signal ** 2)) * 32768 * 10 ** (db / 20)
    pcm = np.clip(scaled, -32768, 32767).astype(np.int16)
    return [pcm[i:i + FRAME_SAMPLES].tobytes() for i in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES)]


def voice(f0: float = 150.0, seconds: float = 1.0) -> np.ndarray:
    """A harmonic, pitched signal: what a voice speaking aloud looks like to the gate."""
    t = np.arange(int(RATE * seconds)) / RATE
    return sum(np.sin(2 * np.pi * f0 * h * t) / h for h in range(1, 15))


def whisper(seconds: float = 1.0) -> np.ndarray:
    """Lightly low-passed noise: a whisper has energy but no pitch."""
    noise = np.random.default_rng(7).normal(0, 1, int(RATE * seconds))
    return np.convolve(noise, np.ones(4) / 4, "same")


def run(gate: WhisperGate, chunks: list) -> list:
    return [gate.process(chunk) for chunk in chunks]


class VoicingTests(unittest.TestCase):
    def test_a_voice_scores_above_the_constant_and_a_whisper_below(self):
        window = lambda sig: np.frombuffer(b"".join(frames(sig, -30)[:2]), dtype=np.int16)
        self.assertGreater(voicing(window(voice())), VOICED_PEAK)
        self.assertLess(voicing(window(whisper())), VOICED_PEAK)
        self.assertEqual(voicing(np.zeros(2 * FRAME_SAMPLES, dtype=np.int16)), 0.0)


class WhisperOnlyGateTests(unittest.TestCase):
    def test_a_voice_aloud_never_opens_the_gate_and_goes_out_as_silence(self):
        out = run(WhisperGate(GateSettings(whisper_only=True)), frames(voice(), -30))
        self.assertFalse(any(o.open for o in out))
        self.assertTrue(all(o.frame == SILENCE for o in out))

    def test_a_whisper_at_the_same_level_opens_it(self):
        out = run(WhisperGate(GateSettings(whisper_only=True)), frames(whisper(), -30))
        self.assertTrue(out[-1].open)
        self.assertTrue(any(o.frame != SILENCE for o in out))

    def test_off_behaves_exactly_as_before(self):
        chunks = frames(voice(), -30) + frames(whisper(), -30) + [SILENCE] * 60
        today = run(WhisperGate(GateSettings()), chunks)
        off = run(WhisperGate(GateSettings(whisper_only=False)), chunks)
        self.assertEqual([(o.frame, o.open, o.turn_end) for o in off], [(o.frame, o.open, o.turn_end) for o in today])
        self.assertTrue(today[49].open)  # the voice opened the gate, as it always did


class PipelineToggleTests(unittest.TestCase):
    def test_set_whisper_only_reaches_the_gate_and_the_status(self):
        hub = Hub()
        pipeline = Pipeline(Config(), hub, engine_factory=lambda n: None)
        self.assertTrue(pipeline.gate.s.whisper_only)  # on by default
        pipeline.set_whisper_only(False)
        self.assertFalse(pipeline.gate.s.whisper_only)
        self.assertFalse([e for e in hub.events if e["type"] == "status"][-1]["whisper_only"])


if __name__ == "__main__":
    unittest.main()
