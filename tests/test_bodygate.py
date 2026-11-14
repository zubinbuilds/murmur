"""BodyGate on synthetic accelerometer data shaped like the band's: 1 g gravity, sensor noise, whole milli-g."""

import math
import unittest

import numpy as np

from server.bodygate import HANGOVER_MS, BodyGate

NOISE_DENSITY_G = 90e-6  # per root hertz, an LSM6DS3-class accelerometer


def stream(rate, seconds, motion=lambda t: (0.0, 0.0, 0.0), seed=0):
    """Samples at `rate`: gravity on z, plus motion(t in s) -> (ax, ay, az) extra g, plus noise, rounded to 1 mg."""
    rng = np.random.default_rng(seed)
    noise = NOISE_DENSITY_G * math.sqrt(rate / 2)
    out = []
    for i in range(int(seconds * rate)):
        t = i / rate
        m = motion(t)
        g = (m[0], m[1], 1.0 + m[2])
        ax, ay, az = (round(v + rng.normal(0, noise), 3) for v in g)
        out.append({"t": t * 1000, "ax": ax, "ay": ay, "az": az})
    return out


def timeline(rate, samples, seed=1):
    """Feed in irregular batches of 1 to 60 samples; (t of the batch's last sample in ms, active) after each."""
    gate, rng, out, i = BodyGate(rate), np.random.default_rng(seed), [], 0
    while i < len(samples):
        n = int(rng.integers(1, 61))
        batch = samples[i:i + n]
        out.append((batch[-1]["t"], gate.feed(batch)))
        i += n
    return out


def burst(start, end, amp, hz, axis=0, ramp=0.0):
    def motion(t):
        if not start <= t < end:
            return (0.0, 0.0, 0.0)
        edge = min(t - start, end - t)
        v = amp * math.sin(2 * math.pi * hz * (t - start)) * (min(1.0, edge / ramp) if ramp else 1.0)
        return tuple(v if k == axis else 0.0 for k in range(3))
    return motion


def head_turn(t):
    """90 degrees over 2 s, held 1 s, and back over 2 s, starting at 2 s: gravity swings from z onto x."""
    if t < 2 or t >= 7:
        angle = 0.0
    elif t < 4:
        angle = (math.pi / 4) * (1 - math.cos(math.pi * (t - 2) / 2))
    elif t < 5:
        angle = math.pi / 2
    else:
        angle = (math.pi / 4) * (1 + math.cos(math.pi * (t - 5) / 2))
    return (math.sin(angle), 0.0, math.cos(angle) - 1.0)


def active_between(tl, lo_ms, hi_ms):
    return [a for t, a in tl if lo_ms <= t < hi_ms]


class BodyGateTests(unittest.TestCase):
    def test_a_still_band_never_triggers(self):
        for rate in (100, 416, 1666):
            with self.subTest(rate=rate):
                self.assertFalse(any(a for _, a in timeline(rate, stream(rate, 10))))

    def test_voice_vibration_turns_it_on_and_it_lets_go_after_the_hangover(self):
        rate = 1666
        tl = timeline(rate, stream(rate, 5, burst(2.0, 3.0, 0.03, 150)))
        self.assertFalse(any(active_between(tl, 0, 2000)))
        self.assertTrue(all(active_between(tl, 2100, 3000)))
        self.assertTrue(all(active_between(tl, 3000, 3000 + HANGOVER_MS - 50)))
        self.assertFalse(any(active_between(tl, 3000 + HANGOVER_MS + 100, 5000)))

    def test_jaw_motion_turns_it_on_at_100_and_1666_hz(self):
        for rate in (100, 1666):
            with self.subTest(rate=rate):
                tl = timeline(rate, stream(rate, 5, burst(2.0, 3.0, 0.05, 5, axis=2, ramp=0.1)))
                self.assertFalse(any(active_between(tl, 0, 2000)))
                self.assertTrue(all(active_between(tl, 2150, 3000)))
                self.assertFalse(any(active_between(tl, 3000 + HANGOVER_MS + 200, 5000)))

    def test_slow_head_turns_do_not_trigger(self):
        for rate in (100, 1666):
            with self.subTest(rate=rate):
                self.assertFalse(any(a for _, a in timeline(rate, stream(rate, 9, head_turn))))

    def test_batch_size_does_not_change_the_result(self):
        rate = 416
        samples = stream(rate, 4, burst(2.0, 2.5, 0.05, 5, axis=1))
        whole, pieces = BodyGate(rate), BodyGate(rate)
        whole.feed(samples)
        for i in range(0, len(samples), 7):
            pieces.feed(samples[i:i + 7])
        self.assertEqual((whole.active, whole.energy, whole.floor), (pieces.active, pieces.energy, pieces.floor))

    def test_a_gap_in_time_restarts_it_off(self):
        rate = 416
        gate = BodyGate(rate)
        gate.feed(stream(rate, 3, burst(1.5, 3.0, 0.05, 5)))
        self.assertTrue(gate.active)
        later = [dict(s, t=s["t"] + 10_000) for s in stream(rate, 0.05)]
        self.assertFalse(gate.feed(later))

    def test_band_counter_wrap_is_not_a_gap(self):
        rate = 416
        gate = BodyGate(rate)
        samples = stream(rate, 3, burst(1.5, 3.0, 0.05, 5))
        wrapped = [dict(s, t=(s["t"] + 2 ** 32 - 2500) % 2 ** 32) for s in samples]  # wraps at 2.5 s
        self.assertTrue(gate.feed(wrapped))

    def test_rates_outside_100_to_1666_are_refused(self):
        for rate in (50, 2000):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                BodyGate(rate)


if __name__ == "__main__":
    unittest.main()
