import asyncio
import contextlib
import importlib.util
import io
import unittest
from unittest import mock
from urllib.error import HTTPError

from server.config import Config
from server.hub import Hub
from server.voice import VOICES_URL, Voice, VoiceError, fake_tts, first_voice, make_tts, make_voice, voice_list

NO_PIPECAT = importlib.util.find_spec("pipecat") is None


@unittest.skipIf(NO_PIPECAT, "pipecat-ai is not installed")
class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = Hub()
        self.tts = fake_tts()
        self.played = []
        self.voice = Voice(self.hub, self.factory, lambda audio, rate, ch: self.played.append((len(audio), rate, ch)))

    async def factory(self):
        return self.tts

    async def asyncTearDown(self):
        await self.voice.close()

    def spoken(self):
        return [(e["text"], e["state"]) for e in self.hub.events if e["type"] == "spoken"]

    async def wait_for(self, count, timeout=10.0):
        for _ in range(int(timeout / 0.05)):
            if len([s for s in self.spoken() if s[1] != "speaking"]) >= count:
                return
            await asyncio.sleep(0.05)
        self.fail(f"only {self.spoken()}")

    async def test_replies_are_spoken_one_at_a_time_in_order(self):
        self.voice.say("First reply.")
        self.voice.say("  ")
        self.voice.say("Second reply.")
        await self.wait_for(2)
        self.assertEqual(self.spoken(), [("First reply.", "speaking"), ("First reply.", "done"),
                                         ("Second reply.", "speaking"), ("Second reply.", "done")])
        self.assertEqual(self.played, [(4800, 24000, 1)] * 2)

    async def test_a_failing_tts_reports_an_error_and_the_queue_goes_on(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise VoiceError("Set ELEVENLABS_API_KEY to speak replies aloud.")
            return self.tts

        self.voice._tts_factory = flaky
        self.voice.say("Lost.")
        self.voice.say("Heard.")
        await self.wait_for(2)
        errors = [e for e in self.hub.events if e["type"] == "spoken" and e["state"] == "error"]
        self.assertEqual([(e["text"], e["detail"]) for e in errors],
                         [("Lost.", "Set ELEVENLABS_API_KEY to speak replies aloud.")])
        self.assertEqual(self.tts.spoken, ["Heard."])


class SetupTests(unittest.TestCase):
    def test_no_device_means_no_voice(self):
        self.assertIsNone(make_voice(Config(voice_device=""), Hub()))

    def test_missing_key_and_voice_raise_instead_of_exiting(self):
        with self.assertRaises(VoiceError):
            make_tts("elevenlabs", "", "voice")
        with self.assertRaises(VoiceError):
            make_tts("elevenlabs", "placeholder", "")
        with self.assertRaises(VoiceError):
            make_tts("polly", "placeholder", None)

    @unittest.skipIf(NO_PIPECAT, "pipecat-ai is not installed")
    def test_building_real_services_needs_no_network(self):
        make_tts("elevenlabs", "placeholder", "placeholder-voice")
        make_tts("deepgram", "placeholder", None)

    def test_a_bad_device_is_printed_to_the_terminal(self):
        with mock.patch("server.voice.Speaker", side_effect=VoiceError("no such device")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(make_voice(Config(voice_device="Nope"), Hub()))
        self.assertIn("no such device", err.getvalue())

    def test_the_key_is_cut_out_of_an_elevenlabs_error(self):
        body = io.BytesIO(b'{"detail": "invalid key sk_secret123"}')
        error = HTTPError(VOICES_URL, 401, "Unauthorized", {}, body)
        with mock.patch("server.voice.urlopen", side_effect=error), self.assertRaises(VoiceError) as caught:
            first_voice("sk_secret123")
        self.assertNotIn("sk_secret123", str(caught.exception))
        self.assertIn("401", str(caught.exception))

    def test_voice_list_follows_pages(self):
        pages = {
            None: {"voices": [{"voice_id": "a1", "name": "One"}], "has_more": True, "next_page_token": "p2"},
            "p2": {"voices": [{"voice_id": "b2", "name": "Two"}], "has_more": False},
        }
        self.assertEqual(voice_list(lambda q: pages[q.get("next_page_token")]), [("a1", "One"), ("b2", "Two")])


if __name__ == "__main__":
    unittest.main()
