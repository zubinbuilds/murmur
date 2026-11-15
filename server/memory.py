"""What Murmur has heard recently, so the agent gets context with each "Hey Murmur" request.

It keeps your lines, other people's lines and Murmur's own replies. Other people's words are never
acted on (only yours can start a command) but they can help explain what you meant, for example
when someone next to you says the name of the place you are at.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class Line:
    at: float
    who: str  # "you" | "other" | "murmur"
    text: str
    speaker_id: Optional[str] = None


class Memory:
    def __init__(self, max_lines: int = 120, max_age_s: float = 30 * 60,
                 clock: Callable[[], float] = time.time) -> None:
        self.lines: deque = deque(maxlen=max_lines)
        self.max_age_s = max_age_s
        self.clock = clock
        self._names: dict = {}

    def add(self, who: str, text: str, speaker_id: Optional[str] = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        last = self.lines[-1] if self.lines else None
        if last and last.who == who and last.speaker_id == speaker_id and self.clock() - last.at < 4:
            last.text = f"{last.text} {text}"  # pieces of one turn arrive separately; keep them on one line
            last.at = self.clock()
            return
        self.lines.append(Line(self.clock(), who, text, speaker_id))

    def clear(self) -> None:
        self.lines.clear()
        self._names.clear()

    def _label(self, line: Line) -> str:
        if line.who == "you":
            return "Me"
        if line.who == "murmur":
            return "Murmur"
        key = line.speaker_id or "?"
        if key not in self._names:
            self._names[key] = f"Person {chr(ord('A') + len(self._names) % 26)}"
        return self._names[key]

    def transcript(self) -> str:
        cutoff = self.clock() - self.max_age_s
        rows = []
        for line in self.lines:
            if line.at < cutoff:
                continue
            stamp = time.strftime("%H:%M", time.localtime(line.at))
            rows.append(f"[{stamp}] {self._label(line)}: {line.text}")
        return "\n".join(rows)
