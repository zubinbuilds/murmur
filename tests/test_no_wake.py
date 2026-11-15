"""Card 3w: no wake phrase by default, and any wearer is heard in whisper mode."""

import unittest

from server.config import Config
from server.hub import Hub
from server.pipeline import Pipeline
from server.wake import WakeGate
from tests.test_pipeline import FakeAgent


def words(text, speaker):
    return [{"text": t, "speaker": speaker} for t in text.split()]


class NoWakeWordTests(unittest.TestCase):
    def test_every_turn_is_a_command_and_a_spoken_wake_phrase_is_stripped(self):
        wake = WakeGate(("murmur",), wake_word=False)
        self.assertEqual(wake.feed("text Sam I'm late"), "text Sam I'm late")
        self.assertEqual(wake.feed("Hey Murmur, text Sam"), "text Sam")
        self.assertIsNone(wake.feed("Hey Murmur."))
        self.assertIsNone(wake.feed("  "))


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = Hub()
        self.agent = FakeAgent()
        self.pipeline = Pipeline(Config(settle_s=60, cleanup=False), self.hub, engine_factory=lambda n: None,
                                 agent=self.agent)
        self.addAsyncCleanup(self.pipeline.shutdown)

    def transcripts(self):
        return [e["text"] for e in self.hub.events if e["type"] == "transcript"]

    async def test_a_turn_without_the_wake_phrase_runs_by_default(self):
        await self.pipeline.inject("order flowers for Sam")
        self.assertEqual(self.agent.sent, ["order flowers for Sam"])
        self.assertIs(self.hub.state["wake_word"], False)

    async def test_a_second_speaker_is_kept_in_whisper_mode(self):
        self.pipeline._on_final({"text": "", "words": words("hello", "speaker_0")})
        self.pipeline._on_final({"text": "", "words": words("call Mum", "speaker_1")})
        self.assertEqual(self.transcripts(), ["hello", "call Mum"])
        self.assertFalse([e for e in self.hub.events if e["type"] == "ignored"])

    async def test_a_second_speaker_is_dropped_in_voice_mode(self):
        self.pipeline.set_whisper_only(False)  # the page's switch, flipped live
        self.pipeline._on_final({"text": "", "words": words("hello", "speaker_0")})
        self.pipeline._on_final({"text": "", "words": words("call Mum", "speaker_1")})
        self.assertEqual(self.transcripts(), ["hello"])


if __name__ == "__main__":
    unittest.main()
