"""Our own delay between the last whispered word and the command, at the real defaults (card 1s)."""

import asyncio
import time
import unittest

from server.config import Config
from server.hub import Hub
from server.pipeline import Pipeline
from tests.test_flush import TimedAgent, words


class LagTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = Config(cleanup=False)
        self.agent = TimedAgent()
        self.pipeline = Pipeline(self.cfg, Hub(), engine_factory=lambda n: None, agent=self.agent)
        self.pipeline.engine = self.engine = object()
        await self.pipeline.start()

    async def asyncTearDown(self):
        await self.pipeline.shutdown()

    async def event(self, kind, text):
        await self.pipeline._on_stt_event({"type": kind, "text": text, "words": words(text)}, self.engine)

    def test_added_delay_is_under_a_second(self):
        self.assertLessEqual(self.cfg.turn_silence_ms / 1000 + self.cfg.settle_s, 0.9)

    async def test_a_final_then_silence_is_acted_on_within_the_settle(self):
        start = time.monotonic()
        await self.event("final", "Hey Murmur, text Sam I'm late")
        await asyncio.sleep(self.cfg.settle_s + 0.3)
        [(command, at)] = self.agent.sent
        self.assertEqual(command, "text Sam I'm late")
        self.assertLessEqual(at - start, self.cfg.settle_s + 0.15)

    async def test_a_partial_during_the_settle_postpones_it(self):
        await self.event("final", "Hey Murmur, text Sam")
        await asyncio.sleep(self.cfg.settle_s / 2)
        await self.event("partial", "I'm")
        partial_at = time.monotonic()
        await asyncio.sleep(self.cfg.settle_s / 2 + 0.05)
        self.assertEqual(self.agent.sent, [])  # past the first settle, but the partial restarted it
        await self.event("final", "I'm late")
        await asyncio.sleep(self.cfg.settle_s + 0.3)
        [(command, at)] = self.agent.sent
        self.assertEqual(command, "text Sam I'm late")
        self.assertGreaterEqual(at - partial_at, self.cfg.settle_s)


if __name__ == "__main__":
    unittest.main()
