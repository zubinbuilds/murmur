"""Where a "Hey Murmur" command goes. Instinct is an agent you text, so the real agent is iMessage."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from .imessage import IMessageBridge


@dataclass
class AgentResult:
    state: str  # "sent" | "dry-run"
    detail: str = ""


class DryRunAgent:
    """Shows what would be sent and sends nothing. The default."""

    def __init__(self, reason: str = "dry run is on (MURMUR_DRY_RUN=1)", reply_after: float = 1.5) -> None:
        self.reason = reason
        self.reply_after = reply_after
        self._on_reply: Optional[Callable[[str], None]] = None
        self._tasks: set = set()

    async def start(self, on_reply: Callable[[str], None], on_error: Callable[[str], None]) -> None:
        self._on_reply = on_reply

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()

    async def send(self, command: str) -> AgentResult:
        if self._on_reply is not None and self.reply_after >= 0:
            task = asyncio.create_task(self._pretend_reply(command))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return AgentResult("dry-run", f"Not sent: {self.reason}.")

    async def _pretend_reply(self, command: str) -> None:
        """A stand-in reply so the page can be rehearsed without texting anyone."""
        await asyncio.sleep(self.reply_after)
        if self._on_reply:
            self._on_reply(f"Practice mode, nothing was sent. I would have asked your computer: {command}")


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


def make_agent(cfg):
    if cfg.dry_run:
        return DryRunAgent()
    if not cfg.instinct_to:
        return DryRunAgent("INSTINCT_IMESSAGE_TO is not set")
    return InstinctAgent(IMessageBridge(cfg.instinct_to))
