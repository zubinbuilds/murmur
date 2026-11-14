"""Which diarized speaker is you.

Diarization gives anonymous labels (speaker_0, speaker_1, ...) that restart with every speech
session. The mic is on your body and the gate drops most other sound, so by default the first
speaker the service identifies in a session is taken to be you. Pressing "Enroll" instead
counts words for a few seconds and picks the speaker who said the most.

Words from any other speaker are dropped before they reach the transcript or the agent. Once
there is an owner, a final word with no speaker label is dropped too, since it cannot be shown
to be yours; partials stay permissive because they are only displayed. Until a labelled word
arrives there is no owner and every word counts, so an engine without diarization still works.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional


class OwnerFilter:
    ENROLL_WORDS = 8

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.owner: Optional[str] = None
        self.enrolling = False
        self._counts: Counter = Counter()
        self._merged: dict = {}

    def resolve(self, speaker: Optional[str]) -> Optional[str]:
        seen = set()
        while speaker in self._merged and speaker not in seen:
            seen.add(speaker)
            speaker = self._merged[speaker]
        return speaker

    def apply_merge(self, from_id: Optional[str], to_id: Optional[str]) -> None:
        if not from_id or not to_id or from_id == to_id:
            return
        self._merged[from_id] = to_id
        if self.owner == from_id:
            self.owner = to_id
        if from_id in self._counts:
            self._counts[to_id] += self._counts.pop(from_id)

    def start_enrollment(self) -> None:
        self.enrolling = True
        self._counts.clear()

    def finish_enrollment(self) -> Optional[str]:
        self.enrolling = False
        if self._counts:
            self.owner = self._counts.most_common(1)[0][0]
        return self.owner

    def observe(self, words: list) -> bool:
        """Learn from one batch of words. True when this batch finished an enrollment."""
        for w in words:
            speaker = self.resolve(w.get("speaker"))
            if speaker is None:
                continue
            if self.enrolling:
                self._counts[speaker] += 1
            elif self.owner is None:
                self.owner = speaker
        if self.enrolling and sum(self._counts.values()) >= self.ENROLL_WORDS:
            self.finish_enrollment()
            return True
        return False

    def is_owner(self, speaker: Optional[str], strict: bool = False) -> bool:
        if self.owner is None:
            return True
        speaker = self.resolve(speaker)
        if speaker is None:
            return not strict
        return speaker == self.resolve(self.owner)

    def split(self, words: list, fallback_text: str = "", strict: bool = False) -> list:
        """Group consecutive words by speaker: [{"speaker", "mine", "text"}, ...]. strict for finals."""
        if not words:
            text = fallback_text.strip()
            return [{"speaker": None, "mine": self.is_owner(None, strict), "text": text}] if text else []
        runs: list = []
        for w in words:
            speaker = self.resolve(w.get("speaker"))
            text = (w.get("text") or "").strip()
            if not text:
                continue
            if runs and runs[-1]["speaker"] == speaker:
                runs[-1]["text"] += " " + text
            else:
                runs.append({"speaker": speaker, "mine": self.is_owner(speaker, strict), "text": text})
        return runs
