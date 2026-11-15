"""Deepgram live transcription (Nova-3) with speaker diarization.

Docs: https://developers.deepgram.com/docs/live-streaming-audio
Audio goes up as raw PCM. At the end of one of your turns we send "Finalize" so Deepgram
settles what it has instead of waiting for its own silence timer.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

import aiohttp

from .base import OnEvent, STTEngine, STTError

URL = "wss://api.deepgram.com/v1/listen"
PARAMS = {
    "model": "nova-3",
    "encoding": "linear16",
    "sample_rate": "16000",
    "channels": "1",
    "diarize": "true",
    "interim_results": "true",
    "punctuate": "true",
    "smart_format": "true",
    "endpointing": "700",
}


def parse_message(msg: dict) -> list:
    """Turn one Deepgram message into zero or more engine events."""
    kind = msg.get("type")
    if kind == "Results":
        alternatives = (msg.get("channel") or {}).get("alternatives") or []
        if not alternatives:
            return []
        alt = alternatives[0]
        text = (alt.get("transcript") or "").strip()
        if not text:
            return []
        words = []
        for w in alt.get("words") or []:
            token = (w.get("punctuated_word") or w.get("word") or "").strip()
            if token:
                speaker = w.get("speaker")
                words.append({"text": token, "speaker": None if speaker is None else f"speaker_{speaker}"})
        return [{"type": "final" if msg.get("is_final") else "partial", "text": text, "words": words}]
    if kind == "Error":
        return [{"type": "error", "message": f"Deepgram: {msg.get('description') or msg.get('message') or 'error'}"}]
    return []


class DeepgramSTT(STTEngine):
    name = "deepgram"

    def __init__(self, api_key: str, keyterms=()) -> None:
        self.api_key = api_key
        # https://developers.deepgram.com/docs/keyterm: one repeated keyterm parameter per word or phrase
        self.params = list(PARAMS.items()) + [("keyterm", t) for t in keyterms]
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._task: Optional[asyncio.Task] = None
        self._on_event: Optional[OnEvent] = None
        self._stopping = False

    async def start(self, on_event: OnEvent) -> None:
        if not self.api_key:
            raise STTError("DEEPGRAM_API_KEY is not set. Put it in .env.")
        self._on_event = on_event
        self._stopping = False
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                URL, params=self.params, headers={"Authorization": f"Token {self.api_key}"}, heartbeat=20,
                max_msg_size=0)
        except Exception as e:
            await self._session.close()
            raise STTError(f"Could not connect to Deepgram: {e}")
        self._task = asyncio.create_task(self._read())
        await on_event({"type": "ready"})

    async def _read(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    for event in parse_message(json.loads(msg.data)):
                        await self._on_event(event)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._on_event({"type": "error", "message": f"Deepgram connection: {e}"})
        if not self._stopping:
            await self._on_event({"type": "closed"})

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_bytes(pcm)

    async def end_turn(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.send_json({"type": "Finalize"})

    async def stop(self) -> None:
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.send_json({"type": "CloseStream"})
            except Exception:
                pass
            await self._ws.close()
        if self._task:
            self._task.cancel()
        if self._session is not None:
            await self._session.close()
