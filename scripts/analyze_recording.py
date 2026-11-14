"""What the whisper gate and the band's accelerometer make of a recording (`python -m server --record`). numpy only.

    .venv/bin/python scripts/analyze_recording.py recordings/murmur-20261114-151500.wav \\
        --segments quiet:0-10,whisper:10-20,talk:20-30,others:30-40

Prints the level percentiles, the voicing scores of the louder half of the frames, how many turns
the real gate would cut at several uncalibrated margins with whispers-only on and off, and, when
the .motion.csv beside the WAV has samples, the accelerometer's vibration energy per segment.
"""

from __future__ import annotations

import argparse
import csv
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.gate import FRAME_BYTES, FRAME_MS, SAMPLE_RATE, VOICED_PEAK, GateSettings, WhisperGate, frame_db, voicing  # noqa: E402

PERCENTILES = (5, 10, 50, 90, 95)
ACCEL_PERCENTILES = (10, 50, 90)
MARGINS_DB = (4, 6, 8, 10)
GRAVITY_WINDOW_S = 0.5  # moving mean subtracted from the magnitude: gravity and slow head motion
BIN_S = FRAME_MS / 1000


def percentiles(values, which=PERCENTILES, scale=1.0) -> str:
    return "  ".join(f"p{p} {np.percentile(values, p) * scale:6.1f}" for p in which)


def parse_segments(text: str) -> list:
    """"quiet:0-10,whisper:10-20" -> [("quiet", 0.0, 10.0), ("whisper", 10.0, 20.0)]."""
    out = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        label, _, span = part.partition(":")
        start, _, end = span.partition("-")
        out.append((label, float(start), float(end)))
    return out


def load_motion(path: Path):
    """(arrival seconds, the kit's t, magnitude in g) for every row with all three axes; None without samples."""
    if not path.exists():
        return None
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((float(r["arrival_s"]), float(r["t"] or "nan"),
                             float(np.sqrt(sum(float(r[k]) ** 2 for k in ("ax", "ay", "az"))))))
            except (TypeError, ValueError):  # a sample the band sent without this sensor
                continue
    return np.array(rows) if len(rows) >= 2 else None


def vibration(motion):
    """(sample rate, 20 ms bin start times, RMS of the magnitude with its 0.5 s moving mean removed)."""
    arrival, kit_t, mag = motion.T
    span = (kit_t[-1] - kit_t[0]) / 1000 if np.isfinite(kit_t).all() and kit_t[-1] > kit_t[0] else arrival[-1] - arrival[0]
    rate = (len(mag) - 1) / span if span > 0 else 0.0
    window = max(1, round(GRAVITY_WINDOW_S * rate))
    residual = mag - np.convolve(mag, np.ones(window) / window, mode="same")
    bins = np.floor(arrival / BIN_S).astype(int)
    bins -= bins.min()
    counts = np.bincount(bins)
    energy = np.bincount(bins, residual ** 2)
    used = counts > 0
    return rate, (np.flatnonzero(used) + np.floor(arrival.min() / BIN_S)) * BIN_S, np.sqrt(energy[used] / counts[used])


def main(path: str, segments=None, out=print) -> int:
    with wave.open(path) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            out(f"{path}: need 16 kHz mono 16-bit, got {w.getframerate()} Hz, {w.getnchannels()} ch")
            return 1
        pcm = w.readframes(w.getnframes())
    frames = [pcm[i:i + FRAME_BYTES] for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)]
    if len(frames) < 2:
        out(f"{path}: too short")
        return 1

    dbs = np.array([frame_db(f) for f in frames])
    samples = [np.frombuffer(f, dtype=np.int16) for f in frames]
    median = np.percentile(dbs, 50)
    scores = [voicing(np.concatenate([samples[i - 1], samples[i]])) for i in range(1, len(frames)) if dbs[i] > median]
    out(f"{path}: {len(frames) * BIN_S:.1f} s, {len(frames)} frames")
    out(f"level dB      {percentiles(dbs)}")
    if scores:
        share = np.mean(np.array(scores) > VOICED_PEAK)
        out(f"voicing       {percentiles(scores)}   (frames above the median level; {share:.0%} over {VOICED_PEAK})")
    for margin in MARGINS_DB:
        for whisper_only in (False, True):
            gate = WhisperGate(GateSettings(margin_db=margin, whisper_only=whisper_only))
            turns = sum(gate.process(f).turn_end for f in frames)
            out(f"margin {margin:2d} dB  whisper_only {'on ' if whisper_only else 'off'}: {turns:3d} turns, "
                f"{gate.stats['passed'] / len(frames):4.0%} of frames passed as audio, threshold at the end {gate.threshold:6.1f}")

    motion = load_motion(Path(path).with_suffix(".motion.csv"))
    if motion is None:
        out("motion: no accelerometer samples beside this recording")
        return 0
    rate, bin_t, bin_rms = vibration(motion)
    out(f"motion: {len(motion)} samples at about {rate:.0f} Hz"
        + ("  (under 200 Hz: voice vibration above half this rate cannot show up)" if rate < 200 else ""))
    frame_t = np.arange(len(frames)) * BIN_S
    medians = {}
    for label, start, end in segments or [("all", 0.0, len(frames) * BIN_S)]:
        level = dbs[(frame_t >= start) & (frame_t < end)]
        accel = bin_rms[(bin_t >= start) & (bin_t < end)]
        line = f"{label:>8} {start:5.1f}-{end:5.1f} s  level dB {percentiles(level) if len(level) else 'none'}"
        if len(accel):
            medians[label] = float(np.median(accel))
            line += f"  |  vibration milli-g {percentiles(accel, ACCEL_PERCENTILES, 1000)}"
        out(line)
    if medians.get("others"):
        if "whisper" in medians:
            out(f"whisper vs others, median vibration: {medians['whisper'] / medians['others']:.2f}x")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--segments", default="", help="label:start-end in seconds, comma separated")
    args = ap.parse_args()
    sys.exit(main(args.wav, parse_segments(args.segments)))
