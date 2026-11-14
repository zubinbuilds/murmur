"""The interface every speech engine implements.

An engine is given 16 kHz mono 16 bit PCM, continuously, and reports through one callback:

    {"type": "ready"}
    {"type": "partial", "text": str, "words": [{"text": str, "speaker": str | None}]}
    {"type": "final",   "text": str, "words": [...]}          # words carry diarization labels
    {"type": "merge",   "from": str, "to": str}               # two speaker ids were the same person
    {"type": "error",   "message": str}
    {"type": "closed"}                                        # the service hung up
"""

from __future__ import annotations

from typing import Awaitable, Callable

OnEvent = Callable[[dict], Awaitable[None]]


class STTError(Exception):
    """Something the user can fix, with a sentence that says how."""


class STTEngine:
    name = "base"

    async def start(self, on_event: OnEvent) -> None:
        raise NotImplementedError

    async def send_audio(self, pcm: bytes) -> None:
        raise NotImplementedError

    async def end_turn(self) -> None:
        """The speaker stopped: settle what has been heard so far."""
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError
