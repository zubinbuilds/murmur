"""Gate statistics events, the raw recording, and the recording analysis. Fakes only, no audio device."""

import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

from scripts import analyze_recording
from server.config import Config
from server.gate import FRAME_SAMPLES
from server.hub import Hub
from server.pipeline import GATE_STATS_FRAMES, Pipeline


def noise(db, count, seed=2):
    rng = np.random.default_rng(seed)
    return [rng.normal(0, 32768 * 10 ** (db / 20), FRAME_SAMPLES).astype(np.int16).tobytes() for _ in range(count)]


def voice(db, count):
    t = np.arange(FRAME_SAMPLES * count) / 16000
    sig = sum(np.sin(2 * np.pi * 150 * h * t) / h for h in range(1, 15))
    pcm = (sig / np.sqrt(np.mean(sig ** 2)) * 32768 * 10 ** (db / 20)).astype(np.int16)
    return [pcm[i:i + FRAME_SAMPLES].tobytes() for i in range(0, len(pcm), FRAME_SAMPLES)]


class GateStatsTests(unittest.IsolatedAsyncioTestCase):
    async def feed(self, frames, **cfg):
        hub = Hub()
        pipeline = Pipeline(Config(**cfg), hub, engine_factory=lambda n: None)
        for f in frames:
            await pipeline.on_pcm(f)
        return [e for e in hub.events if e["type"] == "gate"]

    async def test_one_event_a_second_with_every_count(self):
        events = await self.feed(noise(-30, 2 * GATE_STATS_FRAMES + 10))
        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual(set(first), {"type", "frames", "above", "voiced", "passed", "floor", "threshold"})
        self.assertEqual(first["frames"], GATE_STATS_FRAMES)
        self.assertEqual(first["above"], GATE_STATS_FRAMES)  # -30 dB is over the -45 default
        self.assertEqual(first["voiced"], 0)
        self.assertGreater(first["passed"], 0)

    async def test_voiced_frames_are_counted_and_none_pass(self):
        [event] = await self.feed(voice(-30, GATE_STATS_FRAMES), whisper_only=True)
        self.assertGreaterEqual(event["voiced"], GATE_STATS_FRAMES - 1)
        self.assertEqual(event["passed"], 0)


class RecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_raw_audio_is_written_and_closed_on_shutdown(self):
        frames = voice(-30, 10)  # voiced on purpose: the recording is taken before the gate drops anything
        with tempfile.TemporaryDirectory() as tmp, mock.patch("server.pipeline.RECORDINGS_DIR", Path(tmp)):
            pipeline = Pipeline(Config(record=True), Hub(), engine_factory=lambda n: None)
            await pipeline.start()
            for f in frames:
                await pipeline.on_pcm(f)
            await pipeline.shutdown()
            [path] = Path(tmp).glob("murmur-*.wav")
            with wave.open(str(path)) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
                self.assertEqual(w.readframes(w.getnframes()), b"".join(frames))

    async def test_nothing_is_written_when_recording_is_off(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("server.pipeline.RECORDINGS_DIR", Path(tmp)):
            pipeline = Pipeline(Config(), Hub(), engine_factory=lambda n: None)
            await pipeline.start()
            await pipeline.on_pcm(noise(-30, 1)[0])
            await pipeline.shutdown()
            self.assertEqual(list(Path(tmp).iterdir()), [])


class MotionRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_motion_lines_up_with_the_audio_and_waits_for_its_first_frame(self):
        clock = [100.0]
        with tempfile.TemporaryDirectory() as tmp, mock.patch("server.pipeline.RECORDINGS_DIR", Path(tmp)):
            pipeline = Pipeline(Config(record=True), Hub(), engine_factory=lambda n: None, clock=lambda: clock[0])
            await pipeline.start()
            pipeline.on_motion({"t": 1, "n": 1, "ax": 0, "ay": 0, "az": 1})  # before any audio: dropped
            for f in noise(-30, 5):
                await pipeline.on_pcm(f)
            clock[0] = 100.25
            pipeline.on_motion([{"t": 5, "n": 2, "ax": 0.01, "ay": 0.0, "az": 1.0},
                                {"t": 6, "n": 3, "ax": None, "ay": None, "az": None}])  # a gyro-only sample
            await pipeline.shutdown()
            [csv_path] = Path(tmp).glob("murmur-*.motion.csv")
            rows = csv_path.read_text().splitlines()
        self.assertEqual(rows[0], "arrival_s,wav_s,t,n,ax,ay,az")
        self.assertEqual(rows[1:], ["0.25,0.1,5,2,0.01,0.0,1.0", "0.25,0.1,6,3,,,"])

    async def test_without_recording_motion_is_ignored(self):
        pipeline = Pipeline(Config(), Hub(), engine_factory=lambda n: None)
        pipeline.on_motion({"t": 1, "ax": 0, "ay": 0, "az": 1})  # no error, nothing kept


class AnalyzeTests(unittest.TestCase):
    def test_vibration_per_segment_separates_a_shaking_band_from_a_still_one(self):
        rng = np.random.default_rng(9)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
                w.writeframes(b"".join(noise(-34, 200)))  # 4 s
            rate = 400
            t = np.arange(4 * rate) / rate
            shake = np.where(t < 2, 0.02, 0.002) * rng.standard_normal(len(t))  # 20 mg, then 2 mg
            with open(path.with_suffix(".motion.csv"), "w") as f:
                f.write("arrival_s,wav_s,t,n,ax,ay,az\n")
                f.writelines(f"{s:.4f},{s:.3f},{s * 1000:.1f},{i},0,0,{1 + z:.5f}\n" for i, (s, z) in enumerate(zip(t, shake)))
            lines = []
            analyze_recording.main(str(path), analyze_recording.parse_segments("whisper:0-2,others:2-4"), out=lines.append)
        ratio = float(next(l for l in lines if l.startswith("whisper vs others")).split(":")[1].rstrip("x"))
        self.assertGreater(ratio, 5)
        self.assertTrue(any("about 400 Hz" in l for l in lines))

    def test_segments_parse(self):
        self.assertEqual(analyze_recording.parse_segments("quiet:0-10, whisper:10-20.5"),
                         [("quiet", 0.0, 10.0), ("whisper", 10.0, 20.5)])

    def test_the_report_covers_levels_voicing_and_every_margin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "r.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
                w.writeframes(b"".join(noise(-34, 300) + noise(-15, 50, seed=3) + noise(-34, 60, seed=4)))
            lines = []
            self.assertEqual(analyze_recording.main(path, out=lines.append), 0)
        report = "\n".join(lines)
        self.assertIn("level dB", report)
        self.assertIn("voicing", report)
        self.assertEqual(sum("whisper_only" in line for line in lines), 8)  # 4 margins x on/off


if __name__ == "__main__":
    unittest.main()
