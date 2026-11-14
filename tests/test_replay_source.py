import tempfile
import unittest
import wave
from pathlib import Path

from server.config import Config
from server.replay_source import FRAME_S, ReplaySource


def write_wav(path: Path, samples: int, rate: int = 16000, channels: int = 1, width: int = 2) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * (samples * channels * width // 2))


class FakePipeline:
    def __init__(self, clock=None, cost=0.0):
        self.frames, self.statuses, self.errors = [], [], []
        self.clock, self.cost = clock, cost

    def set_band_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def error(self, where, message):
        self.errors.append((where, message))

    async def on_pcm(self, frame):
        self.frames.append(frame)
        if self.clock:
            self.clock.now += self.cost  # each frame takes this long to process


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.wav = Path(self.dir.name) / "take.wav"

    async def replay(self, pipeline, clock):
        await ReplaySource(Config(replay_path=str(self.wav)), pipeline, clock=clock, sleep=clock.sleep).run()

    async def test_frames_are_640_bytes_with_the_last_padded_and_it_stops_at_the_end(self):
        write_wav(self.wav, 320 * 3 + 100)  # three full frames and a short one
        clock = FakeClock()
        pipeline = FakePipeline()
        await self.replay(pipeline, clock)
        self.assertEqual(len(pipeline.frames), 4)
        self.assertTrue(all(len(f) == 640 for f in pipeline.frames))
        self.assertEqual(pipeline.frames[-1][200:], bytes(440))  # padding is silence
        self.assertEqual(pipeline.statuses, [("connected", "replay: take.wav"), ("finished", "replay finished")])

    async def test_pacing_uses_absolute_deadlines_so_slow_frames_do_not_drift(self):
        write_wav(self.wav, 320 * 50)
        clock = FakeClock()
        pipeline = FakePipeline(clock, cost=0.005)
        await self.replay(pipeline, clock)
        self.assertEqual(len(pipeline.frames), 50)
        self.assertAlmostEqual(clock.now, 49 * FRAME_S + 0.005, places=6)  # not 50 x 25 ms
        self.assertTrue(all(abs(s - 0.015) < 1e-9 for s in clock.sleeps))

    async def test_the_wrong_format_is_a_clear_error_and_nothing_plays(self):
        write_wav(self.wav, 4800, rate=48000, channels=2)
        pipeline = FakePipeline()
        await self.replay(pipeline, FakeClock())
        self.assertEqual(pipeline.frames, [])
        self.assertEqual(pipeline.errors[0][0], "replay")
        self.assertIn("48000 Hz, 2 channel(s), 16 bit; replay needs 16000 Hz mono 16 bit", pipeline.errors[0][1])
        self.assertEqual(pipeline.statuses[-1][0], "error")

    async def test_a_missing_file_is_a_clear_error(self):
        pipeline = FakePipeline()
        await self.replay(pipeline, FakeClock())
        self.assertIn("Cannot replay", pipeline.errors[0][1])
        self.assertIn("take.wav", pipeline.errors[0][1])


if __name__ == "__main__":
    unittest.main()
