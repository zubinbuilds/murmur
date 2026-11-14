import asyncio
import importlib.util
import json
import unittest

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from server.agents import AgentResult
from server.app import CFG, build_app, handle_command, origin_allowed
from server.config import KIT_PYTHON, Config
from server.stt.mock import MockSTT


class FakeAgent:
    def __init__(self):
        self.sent = []

    async def start(self, on_reply, on_error):
        pass

    async def stop(self):
        pass

    async def send(self, command):
        self.sent.append(command)
        return AgentResult("dry-run", "fake")


async def next_event(ws, kind, limit=200):
    for _ in range(limit):
        msg = await asyncio.wait_for(ws.receive(), 10)
        data = json.loads(msg.data)
        if data["type"] == kind:
            return data
    raise AssertionError(f"no {kind} event")


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, **kwargs):
        self.agent = FakeAgent()
        cfg = kwargs.pop("cfg", Config(settle_s=0.05, stt="mock"))
        app = build_app(cfg, agent=self.agent, engine_factory=lambda name: MockSTT(), **kwargs)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        return app

    async def test_page_and_script_are_served(self):
        await self.start(with_band=False)
        for path in ("/", "/static/app.js", "/static/style.css"):
            resp = await self.client.get(path)
            self.assertEqual(resp.status, 200, path)

    async def test_typed_test_line_runs_through_the_wake_gate_to_the_agent(self):
        await self.start(with_band=False)
        ws = await self.client.ws_connect("/ws")
        await next_event(ws, "hello")
        await ws.send_json({"cmd": "say", "text": "Hey Murmur, email Sam I am late"})
        command = await next_event(ws, "command")
        self.assertEqual(command["text"], "email Sam I am late")
        final = await next_event(ws, "command")
        self.assertEqual(final["state"], "dry-run")
        self.assertEqual(self.agent.sent, ["email Sam I am late"])

    async def test_bad_messages_are_reported_not_fatal(self):
        await self.start(with_band=False)
        ws = await self.client.ws_connect("/ws")
        await ws.send_str("not json")
        await ws.send_json({"cmd": "engine", "name": "nope"})
        err = await next_event(ws, "error")
        self.assertEqual(err["where"], "ui")
        await ws.send_json({"cmd": "calibrate", "phase": "noise"})
        self.assertEqual((await next_event(ws, "calibration"))["state"], "recording")

    async def test_other_websites_cannot_drive_the_app(self):
        app = await self.start(with_band=False)
        port = self.client.port
        app[CFG].port = port
        with self.assertRaises(WSServerHandshakeError) as cm:
            await self.client.ws_connect("/ws", headers={"Origin": "https://evil.example"})
        self.assertEqual(cm.exception.status, 403)
        ws = await self.client.ws_connect("/ws", headers={"Origin": f"http://localhost:{port}"})
        await ws.close()

    async def test_whisper_only_switch_reaches_the_pipeline(self):
        class FakePipeline:
            def __init__(self):
                self.calls = []

            def set_whisper_only(self, on):
                self.calls.append(on)

        pipeline = FakePipeline()
        await handle_command(pipeline, {"cmd": "whisper_only", "on": True})
        await handle_command(pipeline, {"cmd": "whisper_only", "on": 0})
        self.assertEqual(pipeline.calls, [True, False])
        with self.assertRaises(KeyError):
            await handle_command(pipeline, {"cmd": "whisper_only"})

    def test_origin_rules(self):
        class Req:
            def __init__(self, origin):
                self.headers = {} if origin is None else {"Origin": origin}

        self.assertTrue(origin_allowed(Req(None), 8000))
        self.assertTrue(origin_allowed(Req("http://localhost:8000"), 8000))
        self.assertTrue(origin_allowed(Req("http://127.0.0.1:8000"), 8000))
        self.assertFalse(origin_allowed(Req("http://localhost:9999"), 8000))
        self.assertFalse(origin_allowed(Req("http://localhost.evil.example:8000"), 8000))
        self.assertFalse(origin_allowed(Req("null"), 8000))

    @unittest.skipUnless(KIT_PYTHON.exists() and importlib.util.find_spec("av"),
                         "run scripts/setup.sh to fetch the Vexo kit and install av")
    async def test_pretend_band_audio_flows_end_to_end(self):
        """The kit's pretend band replays a recording; it must reach the gate and the engine."""
        cfg = Config(settle_s=0.05, stt="mock", transport="sim", band_number=82, whisper_only=False)
        await self.start(cfg=cfg, with_band=True)
        ws = await self.client.ws_connect("/ws")

        async def first_loud_level():
            while True:
                data = json.loads((await ws.receive()).data)
                if data["type"] == "level" and data["db"] > -45:
                    return data

        # one overall limit: a band that keeps failing still sends status events, so per-receive timeouts never fire
        try:
            await asyncio.wait_for(first_loud_level(), 30)
        except asyncio.TimeoutError:
            self.fail("no speech-level audio arrived from the pretend band within 30 s")
        await ws.send_json({"cmd": "listen", "on": True})
        transcript = await asyncio.wait_for(next_event(ws, "transcript", limit=2000), 30)
        self.assertEqual(transcript["speaker"], "you")
        self.assertIn("mock transcript", transcript["text"])


if __name__ == "__main__":
    unittest.main()
