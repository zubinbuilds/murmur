"""Own-voice detector from the band's accelerometer.

The band sits in the wearer's mouth, so their speech and jaw movement shake it, while other
people's voices and room sound barely do. `active` is True while that vibration stands clearly
above its recent noise floor, and for a short hangover after.

feed() takes the Vexo kit's motion samples as they come (vexo_open Message: t in band
milliseconds, ax/ay/az in g), in batches of any size, at any rate from 100 to 1666 Hz. Each
sample's own t sets the filter's time step, so irregular batches and lost samples are fine.

Every threshold below is a first guess from synthetic data; retune them on a real recording.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Iterable, Mapping, Optional

import numpy as np

HIGHPASS_HZ = 3.0  # removes gravity and slow head turns; jaw motion (about 4 to 8 Hz) and voice pass
HIGHPASS_ORDER = 3  # cascaded first-order stages; fewer lets slow head turns through
STEP_MS = 20.0  # vibration energy (RMS of the three axes) is measured over steps this long
FLOOR_WINDOW_S = 5.0  # the noise floor is a low percentile of the steps in this window
FLOOR_PERCENTILE = 10
FLOOR_MIN_G = 0.0005  # the band reports whole milli-g, so a floor below half of one means nothing
WARMUP_S = 1.0  # never active before this much has been seen, while the floor settles
ON_RATIO = 3.0  # a step is loud when its energy is above floor x this
ON_STEPS = 2  # this many loud steps in a row turn it on, so one noisy step does not
HANGOVER_MS = 300.0  # stays on this long after the last loud run
GAP_MS = 200.0  # a jump in t larger than this (lost samples, a restarted stream) restarts the filter
MIN_RATE_HZ, MAX_RATE_HZ = 100.0, 1666.0
T_WRAP_MS = 2 ** 32  # the band's millisecond counter starts again at 0 after this

FLOOR_STEPS = int(FLOOR_WINDOW_S * 1000 / STEP_MS)
WARMUP_STEPS = int(WARMUP_S * 1000 / STEP_MS)


class BodyGate:
    def __init__(self, rate_hz: float) -> None:
        if not MIN_RATE_HZ <= rate_hz <= MAX_RATE_HZ:
            raise ValueError(f"BodyGate works from {MIN_RATE_HZ:g} to {MAX_RATE_HZ:g} Hz, got {rate_hz}.")
        self.period_ms = 1000.0 / rate_hz
        self.rc_s = 1.0 / (2 * math.pi * HIGHPASS_HZ)
        self.active = False
        self.energy = 0.0  # g RMS over the last step
        self.floor = FLOOR_MIN_G
        self._history: deque = deque(maxlen=FLOOR_STEPS)
        self._t: Optional[float] = None  # band time of the last sample
        self._restart(None)

    def feed(self, samples: Iterable[Mapping]) -> bool:
        """Take accelerometer samples ({"t", "ax", "ay", "az"}, oldest first); returns active."""
        for s in samples:
            x = (float(s["ax"]), float(s["ay"]), float(s["az"]))
            t = float(s["t"])
            dt = None if self._t is None else (t - self._t) % T_WRAP_MS
            self._t = t
            if dt is None or dt > GAP_MS:
                self._restart(x)
                continue
            self._clock += dt or self.period_ms  # a repeated stamp counts as one nominal period
            while self._clock >= self._step_end:
                self._close_step()
            self._filter(x, (dt or self.period_ms) / 1000.0)
        return self.active

    def _restart(self, x) -> None:
        zero = (0.0, 0.0, 0.0)
        self._prev = [x or zero] + [zero] * (HIGHPASS_ORDER - 1)  # each stage's previous input
        self._out = [zero] * HIGHPASS_ORDER  # each stage's previous output
        self._clock = 0.0  # ms since the restart, unwrapped
        self._step_end = STEP_MS
        self._sumsq, self._n, self._loud_run = 0.0, 0, 0
        self._last_on: Optional[float] = None
        self.active = False

    def _filter(self, x, dt_s: float) -> None:
        a = self.rc_s / (self.rc_s + dt_s)
        for k in range(HIGHPASS_ORDER):
            y = tuple(a * (yp + xi - xp) for yp, xi, xp in zip(self._out[k], x, self._prev[k]))
            self._prev[k], self._out[k], x = x, y, y
        self._sumsq += x[0] * x[0] + x[1] * x[1] + x[2] * x[2]
        self._n += 1

    def _close_step(self) -> None:
        now = self._step_end
        self._step_end += STEP_MS
        if self._n:
            self.energy = math.sqrt(self._sumsq / self._n)
            # shortcut: speech filling the whole floor window lifts the floor; switch to a floor taken
            # only from inactive steps if the real recording shows long unbroken speech
            self._history.append(self.energy)
            self._sumsq, self._n = 0.0, 0
            if len(self._history) >= WARMUP_STEPS:
                self.floor = max(float(np.percentile(self._history, FLOOR_PERCENTILE)), FLOOR_MIN_G)
                self._loud_run = self._loud_run + 1 if self.energy > self.floor * ON_RATIO else 0
                if self._loud_run >= ON_STEPS:
                    self._last_on = now
        self.active = self._last_on is not None and now - self._last_on <= HANGOVER_MS
