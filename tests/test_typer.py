import unittest
from unittest import mock

from server.config import Config
from server.hub import Hub
from server.pipeline import Pipeline
from tests.test_pipeline import FakeAgent


class TypeRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Routing only: type_text is replaced, so no key event ever reaches macOS."""

    async def asyncSetUp(self):
        self.hub, self.agent = Hub(), FakeAgent()
        self.pipeline = Pipeline(Config(), self.hub, engine_factory=lambda n: None, agent=self.agent)

    def typed(self):
        return [e for e in self.hub.events if e["type"] == "typed"]

    async def test_type_prefix_goes_to_the_keyboard_not_the_agent(self):
        with mock.patch("server.typer.type_text") as type_text:
            await self.pipeline.run_command("Type, see you soon.")
        type_text.assert_called_once_with("see you soon.")
        self.assertEqual(self.agent.sent, [])
        self.assertEqual(self.typed(), [{"type": "typed", "text": "see you soon.", "state": "typed",
                                         "detail": "Typed into the app in front."}])

    async def test_a_typing_failure_is_reported(self):
        with mock.patch("server.typer.type_text", side_effect=PermissionError("not allowed")):
            await self.pipeline.run_command("type hello")
        self.assertEqual(self.typed()[0]["state"], "error")
        self.assertEqual(self.typed()[0]["detail"], "not allowed")

    async def test_other_commands_and_lookalikes_still_reach_the_agent(self):
        with mock.patch("server.typer.type_text") as type_text:
            for command in ("typewriter repair near me", "email Sam that I typed it", "type"):
                await self.pipeline.run_command(command)
        type_text.assert_not_called()
        self.assertEqual(self.agent.sent, ["typewriter repair near me", "email Sam that I typed it", "type"])


if __name__ == "__main__":
    unittest.main()
