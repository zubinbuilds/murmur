"""Failure cases found by trying to break a running server (E2E card B)."""

import asyncio
import unittest

from server.config import Config
from server.hub import Hub
from server.pipeline import QUICK_CLOSE_S, Pipeline
from server.stt.base import STTEngine
from tests.test_pipeline import FakeAgent


class ClosingEngine(STTEngine):
    """Opens fine, then the service hangs up when told to, like a socket refused after its handshake."""

    starts = 0

    async def start(self, on_event):
        ClosingEngine.starts += 1
        self.on_event = on_event
        await on_event({"type": "ready"})

    async def send_audio(self, pcm):
        pass

    async def end_turn(self):
        pass

    async def stop(self):
        pass


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ClosingEngine.starts = 0
        self.now = 100.0
        self.hub = Hub()
        self.engine = None

        def factory(name):
            self.engine = ClosingEngine()
            return self.engine

        self.pipeline = Pipeline(Config(stt="mock"), self.hub, engine_factory=factory, agent=FakeAgent(),
                                 clock=lambda: self.now)
        await self.pipeline.start()
        await self.pipeline.set_listening(True)

    async def asyncTearDown(self):
        await self.pipeline.shutdown()

    def messages(self):
        return [e["message"] for e in self.hub.events if e["type"] == "error"]

    async def test_a_connection_refused_as_it_opens_turns_listening_off_instead_of_looping(self):
        await self.engine.on_event({"type": "closed"})  # same instant it opened: a bad key
        await self.pipeline._reconnect_task
        self.assertFalse(self.pipeline.listening)
        self.assertEqual(ClosingEngine.starts, 1)
        self.assertIn("Listening is off", self.messages()[-1])

    async def test_a_connection_that_drops_later_is_reconnected(self):
        self.now += QUICK_CLOSE_S + 60
        await self.engine.on_event({"type": "closed"})
        await asyncio.wait_for(self.pipeline._reconnect_task, 5)  # first retry waits 1.5 s
        self.assertTrue(self.pipeline.listening)
        self.assertEqual(ClosingEngine.starts, 2)
        self.assertTrue(any(e["type"] == "note" and "restored" in e["text"] for e in self.hub.events))


class HostileMessageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer

        from server.app import build_app
        from server.stt.mock import MockSTT

        self.app = build_app(Config(stt="mock"), with_band=False, engine_factory=lambda name: MockSTT(),
                             agent=FakeAgent())
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.ws = await self.client.ws_connect("/ws")
        await self.ws.receive()  # hello

    async def errors_after(self, *raw):
        import json

        for text in raw:
            await self.ws.send_str(text)
        await self.ws.send_str('{"cmd": "enroll"}')  # a known-good command marks the end
        seen = []
        while True:
            ev = json.loads((await asyncio.wait_for(self.ws.receive(), 2)).data)  # NaN here would raise
            if ev["type"] == "error":
                seen.append(ev["message"])
            if ev["type"] == "note" and "Enrolling" in ev["text"]:
                return seen

    async def test_out_of_range_thresholds_are_refused_and_status_stays_valid_json(self):
        from server.app import HUB

        seen = await self.errors_after('{"cmd":"threshold","db":NaN}', '{"cmd":"threshold","db":1e999}',
                                       '{"cmd":"threshold","db":-1e300}')
        self.assertEqual(len(seen), 3)
        self.assertTrue(all("threshold must be -90 to 0 dB" in m for m in seen))
        self.assertEqual(self.app[HUB].state["threshold"], -45.0)

    async def test_deep_nesting_and_non_text_say_are_bad_messages_and_the_page_stays_connected(self):
        seen = await self.errors_after("[" * 100000 + "]" * 100000, '{"cmd":"say","text":null}')
        self.assertEqual(len(seen), 2)
        self.assertIn("maximum recursion depth", seen[0])
        self.assertIn("say needs text", seen[1])
        self.assertFalse(self.ws.closed)


class RetryLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_mic_that_keeps_failing_logs_its_error_once_and_keeps_retrying(self):
        from unittest import mock

        from server.mic_source import MicSource

        class NoDevice:
            def query_devices(self, device, kind):
                raise ValueError(f"No input device matching {device!r}")

        class Pipeline:
            def __init__(self):
                self.errors, self.statuses = [], []

            def set_band_status(self, state, detail=""):
                self.statuses.append(state)

            def error(self, where, message):
                self.errors.append(message)

        pipeline = Pipeline()
        with mock.patch("server.mic_source.RETRY_S", 0.01):
            task = asyncio.create_task(MicSource(Config(mic_device="Nope"), pipeline, sd=NoDevice()).run())
            await asyncio.sleep(0.2)
            task.cancel()
        self.assertGreater(len(pipeline.statuses), 3)  # it kept retrying
        self.assertEqual(pipeline.errors, ["No input device matching 'Nope'"])


class ShutdownDuringHungCallTests(unittest.TestCase):
    def test_ctrl_c_while_gemini_hangs_exits_quickly(self):
        """The real server process, with Gemini's HTTPS going to a proxy that never answers."""
        import json
        import os
        import signal
        import socket
        import subprocess
        import sys
        import time
        import urllib.request

        from aiohttp.test_utils import unused_port

        hole = socket.socket()
        hole.bind(("127.0.0.1", 0))
        hole.listen()
        self.addCleanup(hole.close)
        port = unused_port()
        proxy = f"http://127.0.0.1:{hole.getsockname()[1]}"
        env = dict(os.environ, HTTPS_PROXY=proxy, https_proxy=proxy, GEMINI_API_KEY="test-key", MURMUR_CLEANUP="1",
                   MURMUR_DRY_RUN="1", MURMUR_AGENT="instinct", INSTINCT_IMESSAGE_TO="", MURMUR_VOICE_DEVICE="")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.Popen([sys.executable, "-m", "server", "--sim", "--engine", "mock", "--no-band",
                                 "--port", str(port)], cwd=root, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

        async def send_command():
            import aiohttp

            async with aiohttp.ClientSession() as s:
                ws = await s.ws_connect(f"http://127.0.0.1:{port}/ws")
                await ws.send_str(json.dumps({"cmd": "say", "text": "Hey Murmur, text Sam I am late"}))
                await asyncio.sleep(1)  # Gemini is now stuck on the proxy
                proc.send_signal(signal.SIGINT)
                start = time.monotonic()
                while proc.poll() is None and time.monotonic() - start < 5:
                    await asyncio.sleep(0.05)
                return time.monotonic() - start

        took = asyncio.run(send_command())
        self.assertIsNotNone(proc.poll(), "the server was still running 5 s after Ctrl+C")
        self.assertLess(took, 2)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
