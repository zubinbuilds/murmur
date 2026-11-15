"""Fan-out of events from the pipeline to every open browser page."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Optional

REPLAYED = {"transcript", "turn", "command", "reply", "note"}


class Hub:
    def __init__(self) -> None:
        self.clients: set = set()
        self.events: deque = deque(maxlen=2000)  # everything emitted, newest last (tests read this)
        self.history: deque = deque(maxlen=100)  # what a page that just opened should see
        self.state: dict = {}
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._pump())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def emit(self, event: dict) -> None:
        self.events.append(event)
        if event["type"] == "status":
            self.state.update({k: v for k, v in event.items() if k != "type"})
        if event["type"] in REPLAYED:
            self.history.append(event)
        if self._queue is not None:
            self._queue.put_nowait(json.dumps(event))

    async def attach(self, ws) -> None:
        self.clients.add(ws)
        await ws.send_str(json.dumps({"type": "hello", "state": self.state, "history": list(self.history)}))

    def detach(self, ws) -> None:
        self.clients.discard(ws)

    async def _pump(self) -> None:
        queue = self._queue
        assert queue is not None
        while True:
            text = await queue.get()
            for ws in list(self.clients):
                try:
                    await ws.send_str(text)
                except Exception:
                    self.clients.discard(ws)
