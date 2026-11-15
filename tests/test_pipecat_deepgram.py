"""PipecatDeepgramSTT with a fake processor in place of DeepgramSTTService: no key, no network."""

import asyncio
import unittest
from types import SimpleNamespace

try:
    from deepgram.listen.v1.types import ListenV1Results
    from pipecat.frames.frames import (InputAudioRawFrame, InterimTranscriptionFrame, TranscriptionFrame,
                                       VADUserStoppedSpeakingFrame)
    from pipecat.processors.frame_processor import FrameProcessor
except ImportError:  # the suite also runs where Pipecat is not installed
    FrameProcessor = None

from server.stt import STTError, make_engine
from server.stt.pipecat_deepgram import PipecatDeepgramSTT


def result(is_final, words):
    return ListenV1Results.model_validate({
        "type": "Results", "channel_index": [0, 1], "duration": 1.0, "start": 0.0,
        "is_final": is_final, "speech_final": False,
        "channel": {"alternatives": [{"transcript": " ".join(w for w, _ in words), "confidence": 0.9, "words": [
            {"word": w.lower().strip(".?"), "punctuated_word": w, "start": 0.0, "end": 0.1,
             "confidence": 0.9, "speaker": s} for w, s in words]}]},
        "metadata": {"request_id": "r", "model_info": {"name": "n", "version": "v", "arch": "a"},
                     "model_uuid": "u"},
    })


if FrameProcessor is not None:
    class FakeDeepgram(FrameProcessor):
        """Counts audio; on Finalize answers the way DeepgramSTTService does with diarize on."""

        def __init__(self, fail=False):
            super().__init__()
            self.audio_frames = 0
            self.fail = fail

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                self.audio_frames += 1
                return
            if isinstance(frame, VADUserStoppedSpeakingFrame):
                if self.fail:
                    await self.push_error("bad key")
                    return
                interim = result(False, [("Hey", None), ("Murmur", None)])
                await self.push_frame(InterimTranscriptionFrame("Hey Murmur", "", "t", result=interim))
                final = result(True, [("Hey", 0), ("Murmur", 0), ("hi.", 1)])
                # one frame per speaker run, each carrying the whole result (stt.py:797)
                await self.push_frame(TranscriptionFrame("Hey Murmur", "", "t", result=final))
                await self.push_frame(TranscriptionFrame("hi.", "", "t", result=final))
                return
            await self.push_frame(frame, direction)


@unittest.skipIf(FrameProcessor is None, "pipecat-ai is not installed")
class PipecatDeepgramTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, fake):
        events = []

        async def on_event(ev):
            events.append(ev)

        engine = PipecatDeepgramSTT("placeholder", make_service=lambda key: fake)
        await engine.start(on_event)
        for _ in range(3):
            await engine.send_audio(bytes(640))
        await engine.end_turn()
        for _ in range(100):
            if fake.fail and any(e["type"] == "error" for e in events) or any(e["type"] == "final" for e in events):
                break
            await asyncio.sleep(0.02)
        await engine.stop()
        return events

    async def test_partial_then_one_final_with_speaker_labels(self):
        fake = FakeDeepgram()
        events = await self.run_turn(fake)
        self.assertEqual(fake.audio_frames, 3)
        self.assertEqual(events[0], {"type": "ready"})
        self.assertEqual(events[1], {"type": "partial", "text": "Hey Murmur", "words": [
            {"text": "Hey", "speaker": None}, {"text": "Murmur", "speaker": None}]})
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual(finals, [{"type": "final", "text": "Hey Murmur hi.", "words": [
            {"text": "Hey", "speaker": "speaker_0"}, {"text": "Murmur", "speaker": "speaker_0"},
            {"text": "hi.", "speaker": "speaker_1"}]}])
        self.assertNotIn("closed", [e["type"] for e in events])

    async def test_a_service_error_becomes_an_error_event(self):
        events = await self.run_turn(FakeDeepgram(fail=True))
        errors = [e for e in events if e["type"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("bad key", errors[0]["message"])

    async def test_a_rejected_key_ends_the_run_with_a_plain_message_and_closed(self):
        class RejectingDeepgram(FakeDeepgram):
            async def process_frame(self, frame, direction):
                await FrameProcessor.process_frame(self, frame, direction)
                if isinstance(frame, InputAudioRawFrame):
                    await self.push_error("Deepgram rejected the connection (status 401): headers: {...}",
                                          force_treat_as_permanent=True)
                    return
                await self.push_frame(frame, direction)

        events = []

        async def on_event(ev):
            events.append(ev)

        engine = PipecatDeepgramSTT("placeholder", make_service=lambda key: RejectingDeepgram())
        await engine.start(on_event)
        await engine.send_audio(bytes(640))
        for _ in range(100):
            if any(e["type"] == "closed" for e in events):
                break
            await asyncio.sleep(0.02)
        await engine.stop()
        errors = [e["message"] for e in events if e["type"] == "error"]
        self.assertEqual(errors, ["Deepgram refused DEEPGRAM_API_KEY. Check it with: "
                                  ".venv/bin/python scripts/check_keys.py"])
        self.assertIn("closed", [e["type"] for e in events])

    def test_keyterms_reach_the_deepgram_service_settings(self):
        from server.stt.pipecat_deepgram import _deepgram_service

        service = _deepgram_service("placeholder", ("Murmur", "Vexo band"))  # building it makes no network call
        self.assertEqual(service._build_connect_kwargs()["keyterm"], ["Murmur", "Vexo band"])
        self.assertNotIn("keyterm", _deepgram_service("placeholder", ())._build_connect_kwargs())

    def test_the_raw_engine_sends_one_keyterm_parameter_per_term(self):
        from server.stt.deepgram import PARAMS, DeepgramSTT

        params = DeepgramSTT("k", keyterms=("Murmur", "Vexo band")).params
        self.assertEqual([v for k, v in params if k == "keyterm"], ["Murmur", "Vexo band"])
        self.assertEqual(DeepgramSTT("k").params, list(PARAMS.items()))

    def test_make_engine_passes_the_configured_keyterms(self):
        from server.config import Config

        cfg = Config(deepgram_key="k", keyterms=("Murmur",))
        self.assertEqual([v for k, v in make_engine("deepgram", cfg).params if k == "keyterm"], ["Murmur"])

    async def test_missing_key_says_how_to_fix_it(self):
        engine = make_engine("pipecat", SimpleNamespace(deepgram_key="", keyterms=()))
        self.assertIsInstance(engine, PipecatDeepgramSTT)
        with self.assertRaises(STTError) as caught:
            await engine.start(lambda ev: None)
        self.assertIn("DEEPGRAM_API_KEY", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
