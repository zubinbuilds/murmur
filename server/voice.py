"""Speak replies aloud: Pipecat text to speech (ElevenLabs or Deepgram) played on a named output device.

Names for MURMUR_VOICE_DEVICE: `.venv/bin/python -m sounddevice` lists every device. ELEVENLABS_VOICE_ID
is a voice id from your ElevenLabs voice library; left empty, the first voice there is used.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Pipecat is imported inside the functions that run only when voice is on, so the server runs without it.

KEYS = {"deepgram": "DEEPGRAM_API_KEY", "elevenlabs": "ELEVENLABS_API_KEY"}
VOICES_URL = "https://api.elevenlabs.io/v2/voices"  # elevenlabs.io/docs/api-reference/voices/search
SPEAK_TIMEOUT_S = 120.0  # per reply, synthesis plus playback; a hung TTS connection must not stall the queue

Play = Callable[[bytes, int, int], None]  # (16-bit PCM, sample rate, channels)


class VoiceError(Exception):
    """Something the pipeline reports instead of crashing: a missing key, an unknown device, no voice."""


def make_tts(name: str, key: str, voice: Optional[str]):
    """The Pipecat TTS service. It connects only when its pipeline starts, so building one makes no network call."""
    if name not in KEYS:
        raise VoiceError(f"MURMUR_TTS must be elevenlabs or deepgram, got {name!r}.")
    if not key:
        raise VoiceError(f"Set {KEYS[name]} to speak replies aloud.")
    if name == "deepgram":
        from pipecat.services.deepgram.tts import DeepgramTTSService

        return DeepgramTTSService(api_key=key, settings=DeepgramTTSService.Settings(voice=voice) if voice else None)
    if not voice:
        raise VoiceError("ElevenLabs needs a voice: set ELEVENLABS_VOICE_ID.")
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

    return ElevenLabsTTSService(api_key=key, settings=ElevenLabsTTSService.Settings(voice=voice))


def sink(play: Play):
    from pipecat.frames.frames import OutputAudioRawFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class Sink(FrameProcessor):
        """Hands each audio frame to play() and passes every frame on, so EndFrame arrives after the audio."""

        async def process_frame(self, frame, direction) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, OutputAudioRawFrame):
                await asyncio.to_thread(play, frame.audio, frame.sample_rate, frame.num_channels)
            await self.push_frame(frame, direction)

    return Sink()


class Speaker:
    """The named output device, opened at the first frame with that frame's format."""

    def __init__(self, device: str) -> None:
        import sounddevice as sd

        try:  # resolve now, so a wrong name fails at startup; this opens nothing
            self.device = sd.query_devices(device, "output")["index"]
        except ValueError as e:
            raise VoiceError(f"MURMUR_VOICE_DEVICE: {e}. List the names with: python -m sounddevice") from e
        self.sd, self.stream = sd, None

    def __call__(self, audio: bytes, rate: int, channels: int) -> None:
        if self.stream is None:
            self.stream = self.sd.RawOutputStream(device=self.device, samplerate=rate, channels=channels, dtype="int16")
            self.stream.start()
        self.stream.write(audio)

    def close(self) -> None:
        if self.stream is not None:
            self.stream.stop()  # waits for the buffered audio to play out
            self.stream.close()


def voice_list(fetch: Callable[[dict], dict]) -> list[tuple[str, str]]:
    """(voice_id, name) for every voice, following has_more and next_page_token across pages."""
    voices, token = [], None
    while True:
        page = fetch({"page_size": 100} | ({"next_page_token": token} if token else {}))
        voices += [(v["voice_id"], v["name"]) for v in page["voices"]]
        if not page.get("has_more"):
            return voices
        token = page["next_page_token"]


def first_voice(key: str) -> str:
    """The first voice id in the ElevenLabs library. Blocking network call: run it in a thread."""

    def fetch(params: dict) -> dict:
        req = Request(f"{VOICES_URL}?{urlencode(params)}", headers={"xi-api-key": key})
        try:
            with urlopen(req, timeout=15) as r:
                return json.load(r)
        except HTTPError as e:
            # this text reaches the page, so a key the server echoes back is cut out
            detail = e.read(300).decode(errors="replace").replace(key, "***")
            raise VoiceError(f"ElevenLabs voices answered {e.code}: {detail}") from e

    voices = voice_list(fetch)
    if not voices:
        raise VoiceError("Your ElevenLabs library has no voices; set ELEVENLABS_VOICE_ID.")
    return voices[0][0]


def fake_tts():
    """Test stand-in for the TTS: 0.1 s of silence per utterance; .spoken lists what it was asked to say."""
    from pipecat.frames.frames import TTSAudioRawFrame, TTSSpeakFrame
    from pipecat.processors.frame_processor import FrameProcessor

    class FakeTTS(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self.spoken: list[str] = []

        async def process_frame(self, frame, direction) -> None:
            await super().process_frame(frame, direction)
            if isinstance(frame, TTSSpeakFrame):
                self.spoken.append(frame.text)
                frame = TTSAudioRawFrame(audio=bytes(4800), sample_rate=24000, num_channels=1)
            await self.push_frame(frame, direction)

    return FakeTTS()


class Voice:
    """say() queues text and returns at once; a background task speaks one reply at a time, in order."""

    def __init__(self, hub, tts_factory: Callable[[], Awaitable[object]], play: Play,
                 on_close: Optional[Callable[[], None]] = None) -> None:
        self.hub = hub
        self._tts_factory = tts_factory
        self._play = play
        self._on_close = on_close
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None

    def say(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._queue.put_nowait(text)
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._on_close:
            self._on_close()

    def _emit(self, text: str, state: str, detail: str = "") -> None:
        self.hub.emit({"type": "spoken", "text": text, "state": state, "detail": detail})

    async def _run(self) -> None:
        while True:
            text = await self._queue.get()
            self._emit(text, "speaking")
            try:
                await asyncio.wait_for(self._speak(text), SPEAK_TIMEOUT_S)
            except Exception as e:  # TimeoutError included; CancelledError is not an Exception
                self._emit(text, "error", str(e) or type(e).__name__)
            else:
                self._emit(text, "done")

    async def _speak(self, text: str) -> None:
        from pipecat.frames.frames import EndFrame, TTSSpeakFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineWorker
        from pipecat.workers.runner import WorkerRunner

        # a fresh pipeline per reply: EndFrame marks when this reply has finished playing
        worker = PipelineWorker(Pipeline([await self._tts_factory(), sink(self._play)]))
        errors: list[str] = []

        @worker.event_handler("on_pipeline_error")
        async def on_error(_worker, frame) -> None:
            errors.append(str(frame.error))

        runner = WorkerRunner(handle_sigint=False)  # the web server owns Ctrl+C
        await runner.add_workers(worker)
        await worker.queue_frames([TTSSpeakFrame(text), EndFrame()])
        await runner.run()
        if errors:
            raise VoiceError(errors[0])


def make_voice(cfg, hub) -> Optional[Voice]:
    """None when MURMUR_VOICE_DEVICE is empty or names no output device (that is reported as an error event)."""
    if not cfg.voice_device:
        return None
    try:
        speaker = Speaker(cfg.voice_device)
    except VoiceError as e:
        print(f"Murmur: voice is off. {e}", file=sys.stderr)  # this runs before any page is connected
        hub.emit({"type": "error", "where": "voice", "message": str(e)})
        return None
    key = cfg.elevenlabs_key if cfg.tts == "elevenlabs" else cfg.deepgram_key
    voice_id = cfg.elevenlabs_voice_id if cfg.tts == "elevenlabs" else None

    async def tts():
        nonlocal voice_id
        if cfg.tts == "elevenlabs" and not voice_id and key:
            voice_id = await asyncio.to_thread(first_voice, key)
        return make_tts(cfg.tts, key, voice_id)

    return Voice(hub, tts, speaker, speaker.close)
