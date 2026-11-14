from __future__ import annotations

from .base import STTEngine, STTError
from .deepgram import DeepgramSTT
from .elevenlabs import ElevenLabsSTT
from .mock import MockSTT
from .pipecat_deepgram import PipecatDeepgramSTT

ENGINES = ("elevenlabs", "deepgram", "pipecat", "mock")


def make_engine(name: str, cfg) -> STTEngine:
    if name == "elevenlabs":
        return ElevenLabsSTT(cfg.elevenlabs_key)
    if name == "deepgram":
        return DeepgramSTT(cfg.deepgram_key)
    if name == "pipecat":
        return PipecatDeepgramSTT(cfg.deepgram_key)
    if name == "mock":
        return MockSTT()
    raise STTError(f"Unknown speech engine {name!r}. Choose one of: {', '.join(ENGINES)}.")


__all__ = ["ENGINES", "STTEngine", "STTError", "make_engine"]
