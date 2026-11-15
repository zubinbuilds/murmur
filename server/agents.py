"""Where a "Hey Murmur" command goes. Instinct is an agent you text, so the real agent is iMessage."""

from __future__ import annotations

import asyncio
import shlex
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


COMMAND_TIMEOUT_S = 60
REPLY_LIMIT = 2000  # characters of stdout kept as the reply


class CommandAgent:
    """Runs a program per command (MURMUR_AGENT_CMD), the command as its last argument; stdout is the reply.
    It is an explicit choice, so dry run does not block it."""

    def __init__(self, argv: list, timeout: float = COMMAND_TIMEOUT_S) -> None:
        self.argv, self.timeout = argv, timeout
        self._on_reply: Optional[Callable[[str, str], None]] = None

    async def start(self, on_reply: Callable[[str, str], None], on_error: Callable[[str], None]) -> None:
        self._on_reply = on_reply

    async def stop(self) -> None:
        return None

    async def send(self, command: str) -> AgentResult:
        name = self.argv[0]
        proc = await asyncio.create_subprocess_exec(*self.argv, command, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"{name} ran past {self.timeout:g} s and was stopped.")
        if proc.returncode:
            tail = err.decode(errors="replace").strip()[-300:]
            raise RuntimeError(f"{name} exited with {proc.returncode}" + (f": {tail}" if tail else "."))
        reply = out.decode(errors="replace").strip()[:REPLY_LIMIT]
        if reply and self._on_reply:
            self._on_reply(reply, "agent")
        return AgentResult("sent", f"Ran {name}.")


def make_agent(cfg):
    if cfg.agent == "command":
        argv = shlex.split(cfg.agent_cmd)
        return CommandAgent(argv) if argv else DryRunAgent("MURMUR_AGENT_CMD is not set")
    if cfg.agent == "type":
        return DryRunAgent("commands are typed, not sent")  # Pipeline.run_command types them
    if cfg.agent == "gemini":
        if not cfg.gemini_key:
            return DryRunAgent("GEMINI_API_KEY is not set")
        return GeminiAgent(cfg.gemini_key, cfg.gemini_model)
    if cfg.dry_run:
        return DryRunAgent()
    if not cfg.instinct_to:
        return DryRunAgent("INSTINCT_IMESSAGE_TO is not set")
    return InstinctAgent(IMessageBridge(cfg.instinct_to))
