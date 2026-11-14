import struct
import tempfile
import unittest
import wave
from pathlib import Path

from server.config import Config
from server.replay_source import FRAME_S, ReplaySource


def write_wav(path: Path, frames: int, extra: int = 0, rate: int = 16000, channels: int = 1) -> None:
    """Each 20 ms frame's samples hold its frame number plus one, so tests can tell which frame played."""
    samples = [i // 320 + 1 for i in range(frames * 320 + extra)]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", s) * channels for s in samples))


class Done(Exception):
    """Ends a run: the source itself loops until the server stops."""


class FakeClock:
    def __init__(self, end):
        self.now, self.end, self.sleeps = 0.0, end, []

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.now > self.end:
            raise Done


class FakePipeline:
    """Listening is on inside the given (on, off) time windows, as if the owner pressed Start and Stop."""

    def __init__(self, clock, windows, cost=0.0):
        self.clock, self.windows, self.cost = clock, windows, cost
        self.played, self.statuses, self.errors = [], [], []

    @property
    def listening(self):
        return any(on <= self.clock.now < off for on, off in self.windows)

    def set_band_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def error(self, where, message):
        self.errors.append((where, message))

    async def on_pcm(self, frame):
        if any(frame):  # silence after the end is not part of the file
            self.played.append((round(self.clock.now, 3), struct.unpack_from("<h", frame)[0], frame))
        self.clock.now += self.cost


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.wav = Path(self.dir.name) / "take.wav"

    async def replay(self, windows, end, cost=0.0):
        clock = FakeClock(end)
        pipeline = FakePipeline(clock, windows, cost)
        source = ReplaySource(Config(replay_path=str(self.wav)), pipeline, clock=clock, sleep=clock.sleep)
        with self.assertRaises(Done):
            await source.run()
        return pipeline, clock

    async def test_it_waits_for_start_then_plays_the_file_once_padded(self):
        write_wav(self.wav, 3, extra=100)  # three full frames and a short one
        pipeline, _ = await self.replay([(0.5, 2.0)], end=3.0)
        self.assertEqual(pipeline.statuses[0], ("connected", "replay ready: take.wav, press Start"))
        self.assertEqual([n for _, n, _ in pipeline.played], [1, 2, 3, 4])
        self.assertGreaterEqual(pipeline.played[0][0], 0.5)  # nothing before Start
        self.assertTrue(all(len(f) == 640 for _, _, f in pipeline.played))
        self.assertEqual(pipeline.played[-1][2][200:], bytes(440))  # padding is silence
        self.assertEqual([s for s, _ in pipeline.statuses], ["connected", "connected", "finished", "connected"])
        self.assertEqual(pipeline.statuses[2], ("finished", "replay finished"))

    async def test_stop_in_the_middle_stops_and_start_plays_again_from_the_beginning(self):
        write_wav(self.wav, 50)  # 1 s
        pipeline, _ = await self.replay([(0.0, 0.3), (0.6, 5.0)], end=5.0)
        numbers = [n for _, n, _ in pipeline.played]
        first, second = numbers[:numbers.index(1, 1)], numbers[numbers.index(1, 1):]
        self.assertEqual(first, list(range(1, 16)))  # 0.3 s of 20 ms frames, then Stop
        self.assertEqual(second, list(range(1, 51)))  # the whole file again, from the start
        self.assertEqual([s for s, _ in pipeline.statuses].count("finished"), 1)

    async def test_pacing_uses_absolute_deadlines_so_slow_frames_do_not_drift(self):
        write_wav(self.wav, 50)
        pipeline, clock = await self.replay([(0.0, 10.0)], end=1.5, cost=0.005)
        self.assertEqual(len(pipeline.played), 50)
        self.assertAlmostEqual(pipeline.played[-1][0], 49 * FRAME_S, places=6)  # not 49 x 25 ms
        self.assertTrue(all(abs(s - 0.015) < 1e-9 for s in clock.sleeps[:49]))

    async def test_the_wrong_format_is_a_clear_error_and_nothing_plays(self):
        write_wav(self.wav, 15, rate=48000, channels=2)
        clock = FakeClock(1.0)
        pipeline = FakePipeline(clock, [(0.0, 1.0)])
        await ReplaySource(Config(replay_path=str(self.wav)), pipeline, clock=clock, sleep=clock.sleep).run()
        self.assertEqual(pipeline.played, [])
        self.assertEqual(pipeline.errors[0][0], "replay")
        self.assertIn("48000 Hz, 2 channel(s), 16 bit; replay needs 16000 Hz mono 16 bit", pipeline.errors[0][1])
        self.assertEqual(pipeline.statuses[-1][0], "error")

    async def test_a_missing_file_is_a_clear_error(self):
        clock = FakeClock(1.0)
        pipeline = FakePipeline(clock, [(0.0, 1.0)])
        await ReplaySource(Config(replay_path=str(self.wav)), pipeline, clock=clock, sleep=clock.sleep).run()
        self.assertIn("Cannot replay", pipeline.errors[0][1])
        self.assertIn("take.wav", pipeline.errors[0][1])


if __name__ == "__main__":
    unittest.main()
