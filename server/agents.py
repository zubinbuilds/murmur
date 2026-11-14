"""Where a "Hey Murmur" command goes. Instinct is an agent you text, so the real agent is iMessage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from . import gemini
from .imessage import IMessageBridge


@dataclass
class AgentResult:
    state: str  # "sent" | "dry-run"
    detail: str = ""


class DryRunAgent:
    """Shows what would be sent and sends nothing. The default."""

    def __init__(self, reason: str = "dry run is on (MURMUR_DRY_RUN=1)") -> None:
        self.reason = reason

    async def start(self, on_reply: Callable[[str], None], on_error: Callable[[str], None]) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, command: str) -> AgentResult:
        return AgentResult("dry-run", f"Not sent: {self.reason}.")


class InstinctAgent:
    """Texts Instinct and relays its replies back to the page."""

    def __init__(self, bridge: IMessageBridge) -> None:
        self.bridge = bridge
        self._task: Optional[asyncio.Task] = None

    async def start(self, on_reply: Callable[[str], None], on_error: Callable[[str], None]) -> None:
        self._task = asyncio.create_task(self.bridge.watch(on_reply, on_error))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def send(self, command: str) -> AgentResult:
        await self.bridge.send(command)
        return AgentResult("sent", f"Texted {self.bridge.handle}.")


ANSWER_PROMPT = ("Answer this whispered request in one to three short sentences that will be read aloud. "
                 "Plain text only: no lists, no markdown.\n\nRequest: ")


class GeminiAgent:
    """Gemini answers the command itself. It acts on nobody's behalf, so dry run does not block it."""

    def __init__(self, key: str, model: str) -> None:
        self.key, self.model = key, model
        self._on_reply: Optional[Callable[[str, str], None]] = None

    async def start(self, on_reply: Callable[[str, str], None], on_error: Callable[[str], None]) -> None:
        self._on_reply = on_reply

    async def stop(self) -> None:
        return None

    async def send(self, command: str) -> AgentResult:
        answer = (await gemini.ask(ANSWER_PROMPT + command, self.key, self.model)).strip()
        if not answer:
            raise RuntimeError("Gemini sent an empty answer.")
        if self._on_reply:
            self._on_reply(answer, "gemini")
        return AgentResult("sent", "Asked Gemini.")


def make_agent(cfg):
    if cfg.agent == "gemini":
        if not cfg.gemini_key:
            return DryRunAgent("GEMINI_API_KEY is not set")
        return GeminiAgent(cfg.gemini_key, cfg.gemini_model)
    if cfg.dry_run:
        return DryRunAgent()
    if not cfg.instinct_to:
        return DryRunAgent("INSTINCT_IMESSAGE_TO is not set")
    return InstinctAgent(IMessageBridge(cfg.instinct_to))
