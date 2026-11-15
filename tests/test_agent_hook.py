"""MURMUR_AGENT=command runs a program per command; MURMUR_AGENT=type types each command. No network."""

import sys
import unittest
from unittest import mock

from server.agents import CommandAgent, DryRunAgent, make_agent
from server.config import Config
from server.hub import Hub
from server.pipeline import Pipeline


def agent_for(code: str, timeout: float = 5) -> CommandAgent:
    return CommandAgent([sys.executable, "-c", code], timeout=timeout)


class CommandAgentTest(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, agent: CommandAgent, command: str):
        replies = []
        await agent.start(lambda text, source: replies.append((text, source)), lambda m: None)
        return await agent.send(command), replies

    async def test_stdout_comes_back_as_the_reply(self):
        result, replies = await self.run_agent(agent_for("import sys; print('done: ' + sys.argv[1])"), "open notes")
        self.assertEqual(result.state, "sent")
        self.assertEqual(replies, [("done: open notes", "agent")])

    async def test_quotes_and_semicolons_arrive_as_one_argument(self):
        command = 'say "hi"; rm -rf ~ && echo $HOME'
        _, replies = await self.run_agent(agent_for("import sys; print(repr(sys.argv[1:]))"), command)
        self.assertEqual(replies[0][0], repr([command]))

    async def test_non_zero_exit_is_an_error(self):
        agent = agent_for("import sys; sys.stderr.write('no window'); sys.exit(3)")
        with self.assertRaisesRegex(RuntimeError, "exited with 3: no window"):
            await self.run_agent(agent, "click send")

    async def test_timeout_stops_the_program(self):
        with self.assertRaisesRegex(RuntimeError, "ran past"):
            await self.run_agent(agent_for("import time; time.sleep(30)", timeout=0.3), "wait")

    async def test_long_output_is_capped_and_empty_output_sends_no_reply(self):
        _, replies = await self.run_agent(agent_for("print('x' * 5000)"), "go")
        self.assertEqual(len(replies[0][0]), 2000)
        _, replies = await self.run_agent(agent_for("pass"), "go")
        self.assertEqual(replies, [])


class MakeAgentTest(unittest.TestCase):
    def test_command_agent_ignores_dry_run_and_needs_a_program(self):
        agent = make_agent(Config(agent="command", agent_cmd="./act.sh --fast", dry_run=True))
        self.assertIsInstance(agent, CommandAgent)
        self.assertEqual(agent.argv, ["./act.sh", "--fast"])
        self.assertIsInstance(make_agent(Config(agent="command")), DryRunAgent)


class TypeAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_command_is_typed(self):
        hub = Hub()
        events = []
        hub.emit = events.append
        pipeline = Pipeline(Config(agent="type", gemini_key=""), hub, engine_factory=lambda n: None)
        with mock.patch("server.typer.type_text") as type_text:
            await pipeline.run_command("see you at six")
        type_text.assert_called_once_with("see you at six")
        self.assertEqual([e["type"] for e in events], ["typed"])


if __name__ == "__main__":
    unittest.main()
