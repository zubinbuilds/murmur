"""When a heard turn is acted on: partials keep it waiting, and in whisper mode an open gate cannot hold it for long.

Times are scaled down (settle_s 0.3 s) so the suite stays fast; the rules are the same at the real default (Config.settle_s).
"""

import asyncio
import time
import unittest

from server.agents import AgentResult
from server.config import Config
from server.hub import Hub
from server.pipeline import GATE_WAIT_WHISPER_S, Pipeline

SETTLE = 0.3


class TimedAgent:
    def __init__(self):
        self.sent = []  # (command, time it arrived)

    async def start(self, on_reply, on_error):
        pass

    async def stop(self):
        pass

    async def send(self, command):
        self.sent.append((command, time.monotonic()))
        return AgentResult("dry-run", "timed")


def words(text):
    return [{"text": t, "speaker": "speaker_0"} for t in text.split()]


class FlushTests(unittest.IsolatedAsyncioTestCase):
    async def make(self, whisper_only):
        self.agent = TimedAgent()
        self.pipeline = Pipeline(Config(settle_s=SETTLE, whisper_only=whisper_only, cleanup=False), Hub(),
                                 engine_factory=lambda n: None, agent=self.agent)
        self.pipeline.engine = self.engine = object()  # events count only from the current engine
        await self.pipeline.start()

    async def asyncTearDown(self):
        await self.pipeline.shutdown()

    async def event(self, kind, text):
        await self.pipeline._on_stt_event({"type": kind, "text": text, "words": words(text)}, self.engine)

    async def test_partials_keep_the_turn_waiting_and_the_flush_has_the_whole_text(self):
        await self.make(whisper_only=True)
        await self.event("final", "Hey Murmur, text Sam")
        for partial in ("I'm", "I'm running", "I'm running late", "I'm running late again", "I'm running late again", "I'm running late again"):
            await asyncio.sleep(0.12)  # shorter than the settle time: without the restart the turn would flush here
            await self.event("partial", partial)
        await self.event("final", "I'm running late again")
        last = time.monotonic()
        await asyncio.sleep(SETTLE + 0.4)
        self.assertEqual([c for c, _ in self.agent.sent], ["text Sam I'm running late again"])
        self.assertGreaterEqual(self.agent.sent[0][1] - last, SETTLE - 0.05)

    async def test_whisper_mode_does_not_wait_long_on_a_gate_held_open(self):
        await self.make(whisper_only=True)
        self.pipeline.gate._open = True  # room noise holding the gate open, and it never closes
        start = time.monotonic()
        await self.event("final", "Hey Murmur, call Mom")
        await asyncio.sleep(SETTLE + GATE_WAIT_WHISPER_S + 0.5)
        [(command, at)] = self.agent.sent
        self.assertEqual(command, "call Mom")
        self.assertLessEqual(at - start, SETTLE + GATE_WAIT_WHISPER_S + 0.3)

    async def test_voice_mode_still_waits_for_the_gate_to_close(self):
        await self.make(whisper_only=False)
        self.pipeline.gate._open = True
        await self.event("final", "Hey Murmur, call Mom")
        await asyncio.sleep(SETTLE + GATE_WAIT_WHISPER_S + 0.3)
        self.assertEqual(self.agent.sent, [])  # past the whisper cap, still waiting as before
        self.pipeline.gate._open = False
        await asyncio.sleep(0.4)
        self.assertEqual([c for c, _ in self.agent.sent], ["call Mom"])


if __name__ == "__main__":
    unittest.main()
