import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from server.band_source import BandSource
from server.config import Config


class Stop(Exception):
    """Raised by the fake Band.connect so the session ends before any hardware would be used."""


class FakePipeline:
    def __init__(self):
        self.statuses = []

    def set_band_status(self, state, detail=""):
        self.statuses.append(state)


def fake_kit(calls):
    class Band:
        @classmethod
        async def connect(cls, number, **kwargs):
            calls.append((number, kwargs))
            raise Stop

    package = types.ModuleType("vexo_open")
    audio = types.ModuleType("vexo_open.audio")
    audio.ogg_packets = lambda path: ["packet"]
    band = types.ModuleType("vexo_open.band")
    band.Band = Band
    package.audio, package.band = audio, band
    return {"vexo_open": package, "vexo_open.audio": audio, "vexo_open.band": band}


class BandSourceConnectTests(unittest.IsolatedAsyncioTestCase):
    async def connect_args(self, cfg):
        calls = []
        with mock.patch.dict(sys.modules, fake_kit(calls)), \
                mock.patch("server.band_source.KIT_PYTHON", Path(__file__).parent):
            with self.assertRaises(Stop):
                await BandSource(cfg, FakePipeline())._session()
        return calls[0]

    async def test_usb_with_a_port_passes_it_to_the_kit(self):
        number, kwargs = await self.connect_args(Config(transport="serial", serial_port="/dev/cu.usbmodem101"))
        self.assertEqual(number, 82)
        self.assertEqual(kwargs["transport"], "serial")
        self.assertEqual(kwargs["port"], "/dev/cu.usbmodem101")

    async def test_usb_without_a_port_lets_the_kit_find_it(self):
        _, kwargs = await self.connect_args(Config(transport="serial"))
        self.assertEqual(kwargs["transport"], "serial")
        self.assertNotIn("port", kwargs)

    async def test_bluetooth_is_unchanged(self):
        _, kwargs = await self.connect_args(Config(transport="ble", serial_port="/dev/cu.usbmodem101"))
        self.assertEqual(kwargs, {"transport": "ble", "auto_reconnect": True})


if __name__ == "__main__":
    unittest.main()
