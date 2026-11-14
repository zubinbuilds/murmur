import asyncio
import unittest

from aiohttp import WSCloseCode, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from server.app import build_app
from server.config import Config
from server.stt.mock import MockSTT


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_closes_open_pages_so_ctrl_c_is_quick(self):
        app = build_app(Config(stt="mock"), with_band=False, engine_factory=lambda name: MockSTT())
        client = TestClient(TestServer(app))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        ws = await client.ws_connect("/ws")
        await ws.receive()  # hello

        await app.shutdown()

        msg = await asyncio.wait_for(ws.receive(), 2)
        while msg.type == WSMsgType.TEXT:  # events already queued before the close
            msg = await asyncio.wait_for(ws.receive(), 2)
        self.assertEqual(msg.type, WSMsgType.CLOSE)
        self.assertEqual(msg.data, WSCloseCode.GOING_AWAY)


if __name__ == "__main__":
    unittest.main()
