"""Reads Murmur's replies aloud with ElevenLabs text to speech. The page plays the MP3."""

from __future__ import annotations

import itertools
from collections import OrderedDict
from typing import Optional

import aiohttp

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
FALLBACK_MODEL = "eleven_flash_v2_5"


class TTSError(Exception):
    pass


class ElevenLabsTTS:
    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_v4_turbo") -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.model_id = model_id
        self.clips: "OrderedDict[str, bytes]" = OrderedDict()
        self._ids = itertools.count(1)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _post(self, text: str, model_id: str) -> aiohttp.ClientResponse:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return await self._session.post(
            API.format(voice=self.voice_id),
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"},
            json={"text": text, "model_id": model_id},
        )

    async def synthesize(self, text: str) -> bytes:
        resp = await self._post(text, self.model_id)
        if resp.status in (400, 404, 422) and self.model_id != FALLBACK_MODEL:
            detail = await resp.text()
            if "model" in detail.lower():  # the configured model is not available on this account
                self.model_id = FALLBACK_MODEL
                resp = await self._post(text, self.model_id)
        if resp.status != 200:
            raise TTSError(f"ElevenLabs speech failed ({resp.status}): {(await resp.text())[:200]}")
        return await resp.read()

    async def speak(self, text: str) -> str:
        """Synthesize and keep the clip; returns the id the page fetches it by."""
        audio = await self.synthesize(text)
        clip_id = str(next(self._ids))
        self.clips[clip_id] = audio
        while len(self.clips) > 30:
            self.clips.popitem(last=False)
        return clip_id

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def make_tts(cfg) -> Optional[ElevenLabsTTS]:
    if not cfg.tts_enabled or not cfg.elevenlabs_key:
        return None
    return ElevenLabsTTS(cfg.elevenlabs_key, cfg.tts_voice_id, cfg.tts_model)
