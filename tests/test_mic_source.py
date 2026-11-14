import unittest

import numpy as np

from server.config import Config
from server.mic_source import FRAME_BYTES, Framer, MicSource, Resampler


def blocks(total: int, size: int, value: float = 0.25):
    for start in range(0, total, size):
        yield np.full(min(size, total - start), value, dtype=np.float32)


class FakeStream:
    def __init__(self, sd, **kwargs):
        self.kwargs = kwargs
        sd.opened.append(kwargs)
        self.sd = sd

    def __enter__(self):
        for block in blocks(48000, 1000):
            self.kwargs["callback"](block[:, None], len(block), None, None)
        return self

    def __exit__(self, *exc):
        self.sd.closed = True


class FakeSoundDevice:
    def __init__(self):
        self.opened, self.closed = [], False

    def query_devices(self, device, kind):
        return {"name": device or "MacBook Air Microphone", "default_samplerate": 48000.0}

    def InputStream(self, **kwargs):
        return FakeStream(self, **kwargs)


class FakePipeline:
    def __init__(self):
        self.frames, self.statuses = [], []

    def set_band_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def error(self, where, message):
        pass

    async def on_pcm(self, frame):
        self.frames.append(frame)


class ResamplerTests(unittest.TestCase):
    def test_48k_to_16k_keeps_length_and_level_across_blocks(self):
        r = Resampler(48000)
        out = np.concatenate([r.feed(b) for b in blocks(48000, 1000)])
        self.assertEqual(len(out), 16000)
        np.testing.assert_allclose(out, 0.25, atol=1e-6)

    def test_44k1_to_16k_length(self):
        r = Resampler(44100)
        out = np.concatenate([r.feed(b) for b in blocks(44100, 512)])
        self.assertLessEqual(abs(len(out) - 16000), 1)


class FramerTests(unittest.TestCase):
    def test_partial_frame_carries_into_next_block(self):
        f = Framer(48000)
        first = f.feed(np.zeros(1000, np.float32))  # 333 samples: one frame, 13 samples wait
        self.assertEqual([len(x) for x in first], [FRAME_BYTES])
        self.assertEqual(len(f.pending), 2 * (333 - 320))
        second = f.feed(np.zeros(1000, np.float32))  # 13 + 333 samples: one frame, 26 wait
        self.assertEqual(len(second), 1)
        self.assertEqual(len(f.pending), 2 * 26)

    def test_one_second_is_fifty_640_byte_frames(self):
        f = Framer(48000)
        frames = [x for b in blocks(48000, 1000) for x in f.feed(b)]
        self.assertEqual(len(frames), 50)
        self.assertTrue(all(len(x) == FRAME_BYTES for x in frames))
        self.assertEqual(f.pending, b"")
        self.assertEqual(np.frombuffer(frames[0], np.int16)[0], round(0.25 * 32767))


class MicSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_streams_frames_then_reports_silence(self):
        sd, pipeline = FakeSoundDevice(), FakePipeline()
        source = MicSource(Config(transport="mic", mic_device="External Microphone"), pipeline, sd=sd)
        source.silence_timeout_s = 0.05
        with self.assertRaisesRegex(RuntimeError, "No audio from External Microphone"):
            await source._session()
        self.assertEqual(len(pipeline.frames), 50)
        opened = sd.opened[0]
        self.assertEqual((opened["device"], opened["channels"], opened["samplerate"]),
                         ("External Microphone", 1, 48000.0))
        self.assertTrue(sd.closed)
        self.assertEqual(pipeline.statuses[-1], ("connected", "mic: External Microphone"))

    async def test_empty_device_name_means_system_default(self):
        sd, pipeline = FakeSoundDevice(), FakePipeline()
        source = MicSource(Config(transport="mic"), pipeline, sd=sd)
        source.silence_timeout_s = 0.05
        with self.assertRaises(RuntimeError):
            await source._session()
        self.assertIsNone(sd.opened[0]["device"])


if __name__ == "__main__":
    unittest.main()
