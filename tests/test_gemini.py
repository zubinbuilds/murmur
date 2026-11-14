import asyncio
import io
import json
import unittest
import urllib.error
from unittest import mock

from server.agents import AgentResult, DryRunAgent, GeminiAgent, make_agent
from server.config import Config
from server.hub import Hub
from server.pipeline import Pipeline
from tests.test_pipeline import FakeAgent

RAW = "email sam that im late"


def reply(text):
    return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


def gemini_says(*answers, error=None):
    """A stand-in for urlopen: no network. Records each request it is handed."""
    fake = mock.Mock(side_effect=error or [reply(a) for a in answers])
    return mock.patch("server.gemini.urllib.request.urlopen", fake)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub, self.agent = Hub(), FakeAgent()
        self.pipeline = Pipeline(Config(gemini_key="test-key"), self.hub, engine_factory=lambda n: None, agent=self.agent)
        await self.pipeline.start()

    def cleanup_event(self):
        [event] = [e for e in self.hub.events if e["type"] == "cleanup"]
        return event

    async def test_accepted_cleanup_is_what_the_agent_gets(self):
        with gemini_says(" Email Sam that I'm late. ") as urlopen:
            await self.pipeline.run_command(RAW)
        self.assertEqual(self.agent.sent, ["Email Sam that I'm late."])
        self.assertEqual(self.cleanup_event(), {"type": "cleanup", "raw": RAW, "text": "Email Sam that I'm late."})
        request = urlopen.call_args.args[0]
        self.assertIn("/models/gemini-4-hydrogen:generateContent", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertIn(RAW, json.loads(request.data)["contents"][0]["parts"][0]["text"])

    async def test_empty_reply_falls_back_to_raw(self):
        with gemini_says("   "):
            await self.pipeline.run_command(RAW)
        self.assertEqual(self.agent.sent, [RAW])
        self.assertEqual(self.cleanup_event()["text"], RAW)
        self.assertIn("error", self.cleanup_event())

    async def test_reply_much_longer_than_the_command_falls_back_to_raw(self):
        with gemini_says("Sure! Here is a polished email to Sam explaining that you are running late today."):
            await self.pipeline.run_command(RAW)
        self.assertEqual(self.agent.sent, [RAW])
        self.assertIn("error", self.cleanup_event())

    async def test_a_failed_call_falls_back_to_raw_and_reports_the_error(self):
        with gemini_says(error=urllib.error.URLError(TimeoutError())):
            await self.pipeline.run_command(RAW)
        self.assertEqual(self.agent.sent, [RAW])
        self.assertIn("did not answer", self.cleanup_event()["error"])

    async def test_an_http_error_does_not_leak_the_key(self):
        body = io.BytesIO(b'{"error": "bad key test-key"}')
        with gemini_says(error=urllib.error.HTTPError("u", 400, "Bad", {}, body)):
            await self.pipeline.run_command(RAW)
        self.assertEqual(self.agent.sent, [RAW])
        self.assertNotIn("test-key", self.cleanup_event()["error"])

    async def test_an_answer_instead_of_a_correction_falls_back_to_raw(self):
        with gemini_says("It is 3:05 PM."):
            await self.pipeline.run_command("what time is it")
        self.assertEqual(self.agent.sent, ["what time is it"])
        self.assertIn("error", self.cleanup_event())

    async def test_typing_skips_cleanup(self):
        with gemini_says() as urlopen, mock.patch("server.typer.type_text") as type_text:
            await self.pipeline.run_command("type im gonna be late")
        urlopen.assert_not_called()
        type_text.assert_called_once_with("im gonna be late")

    async def test_no_key_or_cleanup_off_never_calls_gemini(self):
        for cfg in (Config(), Config(gemini_key="test-key", cleanup=False)):
            pipeline = Pipeline(cfg, Hub(), engine_factory=lambda n: None, agent=FakeAgent())
            with gemini_says() as urlopen:
                await pipeline.run_command(RAW)
            urlopen.assert_not_called()
            self.assertEqual(pipeline.agent.sent, [RAW])


class SlowAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.release = asyncio.Event()

    async def send(self, command):
        self.sent.append(command)
        await self.release.wait()
        return AgentResult("sent", "slow")


class CommandSurvivesNewSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_final_does_not_cancel_a_running_command(self):
        hub, agent = Hub(), SlowAgent()
        pipeline = Pipeline(Config(settle_s=0.01), hub, engine_factory=lambda n: None, agent=agent)
        pipeline._pending.append("Hey Murmur, call the dentist")
        pipeline._schedule_flush()
        await asyncio.sleep(0.1)
        self.assertEqual(agent.sent, ["call the dentist"])  # waiting inside the agent
        pipeline._schedule_flush()  # what a late final does: cancels _flush_task
        await asyncio.sleep(0.05)
        agent.release.set()
        await asyncio.sleep(0.05)
        self.assertEqual([e["state"] for e in hub.events if e["type"] == "command"], ["sending", "sent"])


class GeminiAgentTests(unittest.IsolatedAsyncioTestCase):
    def test_make_agent_picks_gemini_even_in_dry_run(self):
        self.assertIsInstance(make_agent(Config(agent="gemini", gemini_key="test-key")), GeminiAgent)
        self.assertIsInstance(make_agent(Config(agent="gemini")), DryRunAgent)  # no key

    async def test_the_answer_comes_back_as_a_reply_from_gemini(self):
        cfg = Config(agent="gemini", gemini_key="test-key", cleanup=False)
        hub = Hub()
        pipeline = Pipeline(cfg, hub, engine_factory=lambda n: None)
        await pipeline.start()
        with gemini_says("It's 14 degrees and sunny."):
            await pipeline.run_command("what's the weather")
        self.assertIn({"type": "reply", "text": "It's 14 degrees and sunny.", "from": "gemini"}, hub.events)
        self.assertEqual([e for e in hub.events if e["type"] == "command"][-1]["state"], "sent")

    async def test_a_failed_answer_marks_the_command_as_an_error(self):
        hub = Hub()
        pipeline = Pipeline(Config(agent="gemini", gemini_key="test-key", cleanup=False), hub,
                            engine_factory=lambda n: None)
        await pipeline.start()
        with gemini_says(""):
            await pipeline.run_command("what's the weather")
        self.assertEqual([e for e in hub.events if e["type"] == "command"][-1]["state"], "error")
        self.assertFalse([e for e in hub.events if e["type"] == "reply"])


if __name__ == "__main__":
    unittest.main()
