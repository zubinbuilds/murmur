"""ElevenLabs Scribe v3 Live: realtime transcription with speaker diarization.

Docs: https://elevenlabs.io/docs/eleven-api/guides/how-to/speech-to-text/realtime/diarization
We commit manually: our own turn detector (see gate.py) decides when you have stopped talking.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Optional

import aiohttp

from .base import OnEvent, STTEngine, STTError

URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
ERRORS = {
    "auth_error", "quota_exceeded", "transcriber_error", "input_error", "invalid_request", "error",
    "unaccepted_terms", "rate_limited", "queue_overflow", "resource_exhausted",
    "session_time_limit_exceeded", "chunk_size_exceeded", "insufficient_audio_activity",
}


def _words(raw) -> list:
    out = []
    for w in raw or []:
        if w.get("type") in ("spacing", "audio_event"):
            continue
        text = (w.get("text") or "").strip()
        if text:
            out.append({"text": text, "speaker": w.get("speaker_id")})
    return out


def parse_message(msg: dict, diarize: bool = True) -> list:
    """Turn one ElevenLabs message into zero or more engine events."""
    kind = msg.get("message_type")
    if kind == "session_started":
        return [{"type": "ready"}]
    if kind == "partial_transcript":
        text = (msg.get("text") or "").strip()
        return [{"type": "partial", "text": text, "words": _words(msg.get("words"))}] if text else []
    if kind == "committed_transcript":
        text = (msg.get("text") or "").strip()
        # With diarization on, the labelled version arrives next as diarized_transcript.
        return [] if diarize or not text else [{"type": "final", "text": text, "words": []}]
    if kind == "diarized_transcript":
        words = _words(msg.get("words"))
        text = (msg.get("text") or "").strip()
        return [{"type": "final", "text": text, "words": words}] if text or words else []
    if kind == "speakers_merged":
        return [{"type": "merge", "from": msg.get("from_speaker_id"), "to": msg.get("to_speaker_id")}]
    if kind in ERRORS:
        return [{"type": "error", "message": f"ElevenLabs: {msg.get('message') or msg.get('error') or kind}"}]
    return []


class ElevenLabsSTT(STTEngine):
    name = "elevenlabs"

    def __init__(self, api_key: str, model: str = "scribe_v3_live", diarize: bool = True) -> None:
        self.api_key = api_key
        self.model = model
        self.diarize = diarize
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._task: Optional[asyncio.Task] = None
        self._on_event: Optional[OnEvent] = None
        self._stopping = False

    async def start(self, on_event: OnEvent) -> None:
        if not self.api_key:
            raise STTError("ELEVENLABS_API_KEY is not set. Put it in .env.")
        self._on_event = on_event
        self._stopping = False
        params = {
            "model_id": self.model,
            "audio_format": "pcm_16000",
            "commit_strategy": "manual",
            "diarize": "true" if self.diarize else "false",
        }
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                URL, params=params, headers={"xi-api-key": self.api_key}, heartbeat=20, max_msg_size=0)
        except Exception as e:
            await self._session.close()
            raise STTError(f"Could not connect to ElevenLabs: {e}")
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    for event in parse_message(json.loads(msg.data), self.diarize):
                        await self._on_event(event)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._on_event({"type": "error", "message": f"ElevenLabs connection: {e}"})
        if not self._stopping:
            await self._on_event({"type": "closed"})

    async def _send_chunk(self, pcm: bytes, commit: bool = False) -> None:
        if self._ws is None or self._ws.closed:
            return
        await self._ws.send_json({
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "commit": commit,
            "sample_rate": 16000,
        })

    async def send_audio(self, pcm: bytes) -> None:
        await self._send_chunk(pcm)

    async def end_turn(self) -> None:
        # Commit on a short chunk of silence: always valid audio, and the turn really did end.
        await self._send_chunk(bytes(3200), commit=True)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None:
            await self._session.close()
