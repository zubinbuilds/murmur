"""The "Hey Murmur" prefix. Nothing is sent to the agent unless a turn starts with it."""

from __future__ import annotations

import re
import time
from typing import Callable, Iterable, Optional


class WakeGate:
    """feed(text) returns the command to run, or None.

    "Hey Murmur, email Sam" runs "email Sam". Saying just "Hey Murmur" arms the gate: the next
    turn within `arm_seconds` is taken as the command, so you can pause after the wake phrase.
    After a command the gate stays armed for `follow_seconds`, so you can keep going without the
    wake phrase; each follow-up renews the window. 0 turns that off.
    With wake_word=False every turn is a command; a leading "Hey Murmur" is stripped if said.
    """

    def __init__(self, aliases: Iterable[str] = ("murmur", "murmurs", "murmer"), arm_seconds: float = 8.0,
                 clock: Callable[[], float] = time.monotonic, follow_seconds: float = 0.0,
                 wake_word: bool = True) -> None:
        names = "|".join(re.escape(a.strip()) for a in aliases if a.strip()) or "murmur"
        # smart_format can write the name as a possessive ("Hey Murmur's"); the "s" is not part of the command
        self._pattern = re.compile(rf"^\W*hey\W+(?:{names})(?:['’]s)?\b\W*(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
        self.arm_seconds = arm_seconds
        self.follow_seconds = follow_seconds
        self.wake_word = wake_word
        self._clock = clock
        self._armed_until = 0.0

    @property
    def armed(self) -> bool:
        return self._clock() < self._armed_until

    def disarm(self) -> None:
        self._armed_until = 0.0

    def _after_command(self) -> None:
        self._armed_until = self._clock() + self.follow_seconds if self.follow_seconds > 0 else 0.0

    def feed(self, text: str) -> Optional[str]:
        text = (text or "").strip()
        if not text:
            return None
        match = self._pattern.match(text)
        if not self.wake_word:
            return (match.group("rest").strip() or None) if match else text
        if match:
            rest = match.group("rest").strip()
            if rest:
                self._after_command()
                return rest
            self._armed_until = self._clock() + self.arm_seconds
            return None
        if self.armed:
            self._after_command()
            return text
        return None
