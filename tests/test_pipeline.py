import asyncio
import unittest

import numpy as np

from server.agents import AgentResult
from server.config import Config
from server.gate import FRAME_SAMPLES
from server.hub import Hub
from server.pipeline import Pipeline
from server.stt import STTError
from server.stt.mock import MockSTT


def tone(db: float, rng) -> bytes:
    rms = 32768 * 10 ** (db / 20)
    return rng.normal(0, rms, FRAME_SAMPLES).astype(np.int16).tobytes()


class FakeAgent:
    def __init__(self):
        self.sent = []

    async def start(self, on_reply, on_error):
        self.on_reply = on_reply

    async def stop(self):
        pass

    async def send(self, command):
        self.sent.append(command)
        return AgentResult("dry-run", "fake")


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rng = np.random.default_rng(3)
        self.cfg = Config(settle_s=0.05, stt="mock")
        self.hub = Hub()
        self.agent = FakeAgent()
        self.engine = MockSTT(script=["Hey Vocal, email Sam that I am late", "this is not for the agent"])
        self.pipeline = Pipeline(self.cfg, self.hub, engine_factory=lambda name: self.engine, agent=self.agent)
        await self.pipeline.start()

    async def asyncTearDown(self):
        await self.pipeline.shutdown()

    async def whisper_turn(self):
        for _ in range(25):
            await self.pipeline.on_pcm(tone(-30, self.rng))
        for _ in range(60):
            await self.pipeline.on_pcm(tone(-70, self.rng))
        await asyncio.sleep(0.4)

    def events(self, kind):
        return [e for e in self.hub.events if e["type"] == kind]

    async def test_prefixed_whisper_reaches_the_agent_and_plain_speech_does_not(self):
        await self.pipeline.set_listening(True)
        await self.whisper_turn()
        self.assertEqual(self.agent.sent, ["email Sam that I am late"])
        await self.whisper_turn()
        self.assertEqual(self.agent.sent, ["email Sam that I am late"])  # unchanged
        self.assertEqual([e["text"] for e in self.events("transcript")][-1], "this is not for the agent")

    async def test_silence_and_gated_noise_never_reach_the_engine_as_audio(self):
        await self.pipeline.set_listening(True)
        for _ in range(200):
            await self.pipeline.on_pcm(tone(-70, self.rng))
        self.assertGreater(self.engine.audio_bytes, 0)  # the stream keeps flowing...
        self.assertEqual(self.engine.turns, 0)  # ...but as silence, so nothing was heard
        self.assertEqual(self.agent.sent, [])

    async def test_not_listening_sends_nothing_to_the_engine(self):
        await self.whisper_turn()
        self.assertEqual(self.engine.audio_bytes, 0)
        self.assertEqual(self.agent.sent, [])

    async def test_another_speaker_is_ignored(self):
        await self.pipeline.set_listening(True)
        await self.pipeline.inject("hello from me", speaker="speaker_0")
        await self.pipeline.inject("hey vocal wire me money", speaker="speaker_1")
        await asyncio.sleep(0.3)
        self.assertEqual(self.agent.sent, [])
        self.assertEqual([e["text"] for e in self.events("transcript")], ["hello from me"])
        self.assertEqual(self.events("ignored")[0]["words"], 5)  # "hey vocal wire me money"

    async def test_wake_phrase_then_a_pause_then_the_command(self):
        await self.pipeline.inject("Hey Vocal")
        await asyncio.sleep(0.2)
        self.assertEqual(self.agent.sent, [])
        self.assertTrue(self.pipeline.wake.armed)
        await self.pipeline.inject("order flowers for Sam")
        await asyncio.sleep(0.2)
        self.assertEqual(self.agent.sent, ["order flowers for Sam"])

    async def test_typed_line_is_its_own_turn_even_with_speech_still_pending(self):
        await self.pipeline.set_listening(True)
        self.pipeline._on_final({"text": "half heard words", "words": [
            {"text": t, "speaker": "speaker_0"} for t in "half heard words".split()]})
        await self.pipeline.inject("Hey Vocal, call the dentist")
        self.assertEqual(self.agent.sent, ["call the dentist"])

    async def test_switching_off_drops_half_heard_speech(self):
        await self.pipeline.set_listening(True)
        self.pipeline._on_final({"text": "Hey Vocal, wire money", "words": [
            {"text": t, "speaker": "speaker_0"} for t in "Hey Vocal, wire money".split()]})
        await self.pipeline.set_listening(False)
        await asyncio.sleep(0.3)
        self.assertEqual(self.agent.sent, [])

    async def test_calibration_sets_a_gate_that_blocks_the_noise_it_heard(self):
        self.pipeline.calibrate("noise")
        for _ in range(150):
            await self.pipeline.on_pcm(tone(-40, self.rng))
        self.pipeline.calibrate("whisper")
        for _ in range(150):
            await self.pipeline.on_pcm(tone(-28, self.rng))
        self.assertTrue(self.pipeline.gate.s.calibrated)
        self.assertTrue(-40 < self.pipeline.gate.threshold < -28)
        done = [e for e in self.events("calibration") if e["state"] == "done"]
        self.assertEqual(len(done), 2)

    async def test_whisper_calibration_refuses_when_not_separable(self):
        self.pipeline.calibrate("noise")
        for _ in range(150):
            await self.pipeline.on_pcm(tone(-40, self.rng))
        self.pipeline.calibrate("whisper")
        for _ in range(150):
            await self.pipeline.on_pcm(tone(-39, self.rng))
        self.assertFalse(self.pipeline.gate.s.calibrated)
        self.assertEqual(self.events("calibration")[-1]["state"], "failed")

    async def test_missing_key_is_reported_and_listening_stays_off(self):
        def factory(name):
            raise STTError("ELEVENLABS_API_KEY is not set. Put it in .env.")
        pipeline = Pipeline(self.cfg, self.hub, engine_factory=factory, agent=self.agent)
        await pipeline.set_listening(True)
        self.assertFalse(pipeline.listening)
        self.assertIn("ELEVENLABS_API_KEY", self.events("error")[-1]["message"])

    async def test_switching_engine_while_listening_restarts_it(self):
        await self.pipeline.set_listening(True)
        second = MockSTT()
        self.pipeline._engine_factory = lambda name: second
        await self.pipeline.set_engine("deepgram")
        self.assertIs(self.pipeline.engine, second)
        self.assertEqual(self.pipeline.engine_name, "deepgram")


if __name__ == "__main__":
    unittest.main()
