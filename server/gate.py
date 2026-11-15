"""Whisper gate and turn detection.

The band's microphone hears whatever is loud enough, including music and other people. The
gate sits between the band and the speech service and decides, 20 ms at a time, whether a
frame is *your whisper* (loud enough at the mic) or background. Frames that fail are replaced
by digital silence, so the service never hears them but the stream keeps its timing and the
connection stays alive.

The same state machine is the turn detector: a turn starts when the level stays above the
threshold for `onset_ms`, and ends after `hangover_ms` of quiet. The end of a turn is when we
tell the speech service to commit what it has heard.

Output is delayed by `preroll_ms` so that the first syllable, which came before the gate was
sure, still goes through.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 320
FRAME_BYTES = FRAME_SAMPLES * 2
SILENCE = bytes(FRAME_BYTES)
MIN_LAG, MAX_LAG = SAMPLE_RATE // 400, SAMPLE_RATE // 70  # pitch periods of a speaking voice, 70 to 400 Hz
# Peak autocorrelation above this is a voiced (pitched) frame. Measured: synthetic voices at 110 to 220 Hz
# score 0.89 or more, white and lightly filtered noise 0.23 or less (95th percentile), and 75% of the loud
# frames in the kit's normal-voice sample score above it. A whisper has no pitch.
VOICED_PEAK = 0.5
FLOOR_WINDOW_FRAMES = 250  # 5 s of levels; their quietest tenth is the room, even mid-sentence
FLOOR_WARMUP_FRAMES = 25  # 0.5 s: enough to use the floor at startup instead of holding the gate open for 5 s
# Whisper mode, from the owner's band recording at 15:26: voicing, not level, separates their whisper from
# talkers at the same volume. Loud unvoiced runs: whisper up to 380 ms, owner talking 160 ms, room 120 ms.
WHISPER_MARGIN_DB = 5.0  # sweep: 5 to 6 all 99/0/0/0; 4 leaks the owner's talk, 7 breaks the whisper, a softer whisper needs the room
WHISPER_ONSET_MS = 200  # loud unvoiced in a row before a whisper turn opens; talk and room bursts are shorter
WHISPER_PREROLL_MS = 300  # covers the 200 ms onset plus the first syllable before it
VOICED_RUN_MS = 100  # 10% of real whisper frames score voiced alone; only a run this long is someone talking
DIGITAL_SILENCE_DB = -85.0  # all-zero frames (dropouts, our own gated output) say nothing about the room


def frame_db(frame: bytes) -> float:
    """RMS level of 16 bit samples in dB below full scale (0 = loudest, about -90 = silence)."""
    x = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    if x.size == 0:
        return -90.0
    rms = float(np.sqrt(np.mean(x * x)))
    return 20.0 * math.log10(max(rms, 1.0) / 32768.0)


def voicing(window: np.ndarray) -> float:
    """Highest normalized autocorrelation at a pitch lag: near 1 for a voice, near 0 for noise or a whisper."""
    x = window.astype(np.float64)
    x -= x.mean()
    n = len(x)
    r = np.correlate(x, x, "full")[n - 1:]  # r[k] = sum of x[i] * x[i + k]
    energy = np.cumsum(x * x)
    k = np.arange(MIN_LAG, MAX_LAG + 1)
    denom = np.sqrt(energy[n - 1 - k] * (energy[-1] - energy[k - 1]))  # energy of x[:n-k] times x[k:]
    ok = denom > 0
    return float(np.max(r[k][ok] / denom[ok])) if ok.any() else 0.0


@dataclass
class GateSettings:
    threshold_db: float = -45.0
    # uncalibrated: stay this far above the room's floor. 14 from the owner's band recording at 15:26:
    # 10 let a nearby talker through, 18 began to clip the owner's own speech
    margin_db: float = 14.0
    calibrated: bool = False  # True: use threshold_db as it is (set by the calibration steps)
    onset_ms: int = 60
    hangover_ms: int = 700
    preroll_ms: int = 200
    max_turn_ms: int = 20000
    whisper_only: bool = False  # voiced frames (someone speaking aloud) are treated as quiet and sent as silence


@dataclass
class GateOutput:
    frame: bytes  # what to send on: the real audio, or silence if it was gated out
    db: float  # level of the newest frame that came in
    open: bool
    turn_end: bool  # a turn finished with this frame: commit now


class WhisperGate:
    def __init__(self, settings: GateSettings | None = None) -> None:
        self.s = settings or GateSettings()
        self.floor_db = -70.0
        self._levels: deque = deque(maxlen=FLOOR_WINDOW_FRAMES)
        self._voiced_levels: deque = deque(maxlen=FLOOR_WINDOW_FRAMES)  # whisper mode: only for the warm-up
        self.stats: Counter = Counter()  # frames, above, voiced, passed; the pipeline reads and clears it
        self._delay: deque = deque()  # [frame, passed] pairs, oldest first
        self._open = False
        self._run = 0  # consecutive loud frames while closed
        self._quiet = 0  # consecutive quiet frames while open
        self._open_frames = 0
        self._prev = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        self._voiced_run = 0  # consecutive voiced-scoring frames, whisper mode

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def threshold(self) -> float:
        if self.s.calibrated:
            return self.s.threshold_db
        margin = WHISPER_MARGIN_DB if self.s.whisper_only else self.s.margin_db
        return max(self.s.threshold_db, self.floor_db + margin)

    def set_threshold(self, db: float) -> None:
        self.s.threshold_db = float(db)
        self.s.calibrated = True

    @staticmethod
    def _frames(ms: int) -> int:
        return max(1, round(ms / FRAME_MS))

    def _track_floor(self, db: float, voiced: bool = False) -> None:
        """Runs open or closed: a floor learned only while closed froze when a band's own noise held the gate open.

        Voiced frames stay out of the floor (whisper mode): nearby talk lifted it 6 dB in 9 s on the owner's band.
        """
        if db > DIGITAL_SILENCE_DB:
            (self._voiced_levels if voiced else self._levels).append(db)
        if len(self._levels) >= FLOOR_WARMUP_FRAMES:
            self.floor_db = float(np.percentile(self._levels, 10))
        elif len(self._levels) + len(self._voiced_levels) >= FLOOR_WARMUP_FRAMES:  # only talk heard so far
            self.floor_db = float(np.percentile(list(self._levels) + list(self._voiced_levels), 10))

    def process(self, frame: bytes, body: Optional[bool] = None) -> GateOutput:
        """body: the own-voice gate's verdict, or None when it is not in use. While it is False a frame
        counts as quiet but keeps its audio, so the pre-roll can still send it once the body gate opens."""
        if len(frame) != FRAME_BYTES:
            frame = (frame + SILENCE)[:FRAME_BYTES]
        db = frame_db(frame)
        samples = np.frombuffer(frame, dtype=np.int16)
        whisper = self.s.whisper_only
        voiced = whisper and voicing(np.concatenate([self._prev, samples])) > VOICED_PEAK
        self._prev = samples
        self._track_floor(db, voiced)
        above = db > self.threshold
        loud = above and body is not False
        self.stats["frames"] += 1
        self.stats["above"] += above
        self._voiced_run = self._voiced_run + 1 if voiced else 0
        sustained = self._frames(VOICED_RUN_MS)
        if self._voiced_run >= sustained:  # someone talking: silence the run back to its start, and it is quiet
            if self._voiced_run == sustained:
                # one frame more than the run: a frame's voicing window also covers the frame before it
                for item in list(self._delay)[-sustained:]:
                    item[0] = SILENCE
                self.stats["voiced"] += sustained
            loud = False
            frame = SILENCE
            self.stats["voiced"] += 1
        turn_end = False
        passed = False

        if self._open:
            passed = True
            self._open_frames += 1
            self._quiet = 0 if loud else self._quiet + 1
            if self._quiet >= self._frames(self.s.hangover_ms):
                self._open = False
                self._run = 0
                turn_end = True
            elif self._open_frames >= self._frames(self.s.max_turn_ms):
                self._open_frames = 0  # a very long turn: commit but stay open
                turn_end = True
        else:
            self._run = self._run + 1 if loud and not voiced else 0
            if self._run >= self._frames(WHISPER_ONSET_MS if whisper else self.s.onset_ms):
                self._open = True
                self._quiet = 0
                self._open_frames = self._run
                passed = True
                for item in self._delay:  # the pre-roll: everything still waiting in the delay line
                    item[1] = True

        self._delay.append([frame, passed])
        out = SILENCE
        if len(self._delay) > self._frames(WHISPER_PREROLL_MS if whisper else self.s.preroll_ms):
            old, ok = self._delay.popleft()
            out = old if ok else SILENCE
            self.stats["passed"] += out is not SILENCE
        return GateOutput(out, db, self._open, turn_end)


def suggest_threshold(noise_dbs, whisper_dbs, min_gap_db: float = 6.0):
    """Pick a gate threshold between the room's loudest background and a typical whisper.

    Returns None when the two are too close for a level gate to tell apart.
    """
    if len(noise_dbs) < 10 or len(whisper_dbs) < 10:
        return None
    noise_hi = float(np.percentile(noise_dbs, 95))
    whisper = float(np.percentile(whisper_dbs, 70))
    if whisper - noise_hi < min_gap_db:
        return None
    return round(noise_hi + 0.4 * (whisper - noise_hi), 1)
