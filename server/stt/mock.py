"""A speech engine that needs no key: for tests and for trying the page with the pretend band.

It "transcribes" a turn only if real (non-silent) audio arrived during it. The text comes
from a script if one is given, otherwise it is a placeholder.
"""

from __future__ import annotations

from typing import Optional

from .base import OnEvent, STTEngine


class MockSTT(STTEngine):
    name = "mock"

    def __init__(self, script: Optional[list] = None, speaker: str = "speaker_0") -> None:
        self.script = list(script or [])
        self.speaker = speaker
        self.audio_bytes = 0
        self.turns = 0
        self._heard = False
        self._on_event: Optional[OnEvent] = None

    async def start(self, on_event: OnEvent) -> None:
        self._on_event = on_event
        await on_event({"type": "ready"})

    async def send_audio(self, pcm: bytes) -> None:
        self.audio_bytes += len(pcm)
        if any(pcm):
            self._heard = True

    async def end_turn(self) -> None:
        if not self._heard:
            return
        self._heard = False
        self.turns += 1
        text = self.script.pop(0) if self.script else "(mock transcript: speech detected)"
        words = [{"text": t, "speaker": self.speaker} for t in text.split()]
        await self._on_event({"type": "partial", "text": text, "words": words})
        await self._on_event({"type": "final", "text": text, "words": words})

    async def stop(self) -> None:
        return None
