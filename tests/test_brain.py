import asyncio
import unittest
from types import SimpleNamespace as NS

from server.agents import AgentResult, DryRunAgent
from server.brain import TOOL_NAME, ClaudeBrain, Decision, PassThroughBrain, make_brain, parse_response
from server.config import Config
from server.hub import Hub
from server.memory import Memory
from server.pipeline import Pipeline


def tool_use(message):
    return NS(type="tool_use", name=TOOL_NAME, input={"message": message})


def text(t):
    return NS(type="text", text=t)


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.beta = NS(messages=FakeMessages(response))


class ParseTests(unittest.TestCase):
    def test_tool_call_means_send_and_any_text_with_it_is_dropped(self):
        d = parse_response(NS(stop_reason="tool_use", content=[text("On it."), tool_use(" Post a tweet ")]))
        self.assertEqual(d, Decision("send", "Post a tweet"))

    def test_text_only_means_answer(self):
        d = parse_response(NS(stop_reason="end_turn", content=[text("It's 4 pm.")]))
        self.assertEqual(d, Decision("answer", "It's 4 pm."))

    def test_refusal_gets_a_short_spoken_answer(self):
        d = parse_response(NS(stop_reason="refusal", content=[]))
        self.assertEqual(d.kind, "answer")

    def test_empty_tool_message_is_not_sent(self):
        d = parse_response(NS(stop_reason="tool_use", content=[tool_use("  ")]))
        self.assertEqual(d.kind, "answer")


class ClaudeBrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_carries_the_transcript_and_the_tool(self):
        client = FakeClient(NS(stop_reason="tool_use", content=[tool_use("hi")]))
        brain = ClaudeBrain(client=client)
        await brain.decide("post about the demo", "[16:00] Person A: welcome to Y Combinator")
        call = client.beta.messages.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["tools"][0]["name"], TOOL_NAME)
        self.assertEqual(call["tool_choice"], {"type": "auto"})
        prompt = call["messages"][0]["content"]
        self.assertIn("Y Combinator", prompt)
        self.assertIn("<request>post about the demo</request>", prompt)
        self.assertNotIn("Instinct", call["tools"][0]["description"])

    def test_no_key_means_pass_through(self):
        self.assertIsInstance(make_brain(Config()), PassThroughBrain)


class MemoryTests(unittest.TestCase):
    def test_lines_are_labelled_and_pieces_of_one_turn_join(self):
        t = [1000.0]
        m = Memory(clock=lambda: t[0])
        m.add("you", "I'm demoing")
        t[0] += 1
        m.add("you", "live today")
        m.add("other", "welcome to YC", "speaker_1")
        m.add("murmur", "Posted.")
        lines = [l.split("] ", 1)[1] for l in m.transcript().splitlines()]
        self.assertEqual(lines, ["Me: I'm demoing live today", "Person A: welcome to YC", "Murmur: Posted."])

    def test_old_lines_drop_out(self):
        t = [1000.0]
        m = Memory(max_age_s=60, clock=lambda: t[0])
        m.add("you", "old")
        t[0] += 120
        m.add("you", "new")
        self.assertNotIn("old", m.transcript())


class FakeAgent:
    def __init__(self):
        self.sent = []

    async def start(self, on_reply, on_error):
        self.on_reply = on_reply

    async def stop(self):
        pass

    async def send(self, message):
        self.sent.append(message)
        return AgentResult("sent", "ok")


class ScriptedBrain:
    def __init__(self, decision):
        self.decision = decision
        self.seen = []

    async def decide(self, request, transcript):
        self.seen.append((request, transcript))
        return self.decision


class FakeTTS:
    def __init__(self):
        self.clips = {}
        self.said = []

    async def speak(self, text):
        self.said.append(text)
        self.clips["1"] = b"mp3"
        return "1"

    async def close(self):
        pass


class PipelineBrainTests(unittest.IsolatedAsyncioTestCase):
    async def make(self, decision):
        self.hub = Hub()
        self.agent = FakeAgent()
        self.brain = ScriptedBrain(decision)
        self.tts = FakeTTS()
        self.pipeline = Pipeline(Config(settle_s=0.05, stt="mock"), self.hub, agent=self.agent,
                                 brain=self.brain, tts=self.tts)
        await self.pipeline.start()

    async def asyncTearDown(self):
        await self.pipeline.shutdown()

    def events(self, kind):
        return [e for e in self.hub.events if e["type"] == kind]

    async def test_send_rewrites_the_request_waits_for_the_reply_and_speaks_it(self):
        await self.make(Decision("send", "Please post a tweet that I'm demoing Murmur live at YC."))
        self.pipeline._on_final({"text": "hi", "words": [{"text": "hi", "speaker": "speaker_0"}]})  # you speak first
        self.pipeline._on_final({"text": "welcome to YC", "words": [{"text": w, "speaker": "speaker_1"} for w in "welcome to YC".split()]})
        await self.pipeline.inject("Hey Murmur, post a tweet about the demo", speaker="speaker_0")
        self.assertEqual(self.agent.sent, ["Please post a tweet that I'm demoing Murmur live at YC."])
        request, transcript = self.brain.seen[0]
        self.assertEqual(request, "post a tweet about the demo")
        self.assertIn("welcome to YC", transcript)  # other people's words reach the brain as context
        self.assertEqual(self.events("thinking")[-1]["on"], True)

        self.agent.on_reply("Posted! x.com/you/status/1")
        await asyncio.sleep(0.05)
        self.assertEqual(self.events("thinking")[-1]["on"], False)
        self.assertEqual(self.events("reply")[-1]["text"], "Posted! x.com/you/status/1")
        self.assertEqual(self.tts.said, ["Posted! x.com/you/status/1"])
        self.assertEqual(self.events("speak")[-1]["clip"], "1")
        self.assertIn("Murmur: Posted!", self.pipeline.memory.transcript())

    async def test_answer_is_shown_and_spoken_without_texting_anyone(self):
        await self.make(Decision("answer", "It's about four."))
        await self.pipeline.inject("Hey Murmur, what time is it")
        self.assertEqual(self.agent.sent, [])
        self.assertEqual(self.events("reply")[-1]["text"], "It's about four.")
        await asyncio.sleep(0.05)
        self.assertEqual(self.tts.said, ["It's about four."])

    async def test_no_wake_phrase_means_no_brain_call(self):
        await self.make(Decision("send", "x"))
        await self.pipeline.inject("post a tweet about the demo")
        self.assertEqual(self.brain.seen, [])
        self.assertFalse(self.events("turn")[-1]["wake"])

    async def test_turn_event_marks_wake_turns(self):
        await self.make(Decision("answer", "ok"))
        await self.pipeline.inject("Hey Murmur, hello")
        self.assertTrue(self.events("turn")[-1]["wake"])


class DryRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_sends_nothing_but_gives_a_practice_reply(self):
        replies = []
        agent = DryRunAgent(reply_after=0.01)
        await agent.start(replies.append, lambda m: None)
        result = await agent.send("post a tweet")
        await asyncio.sleep(0.05)
        self.assertEqual(result.state, "dry-run")
        self.assertIn("nothing was sent", replies[0])
