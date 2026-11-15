"""A guided 40 second tuning recording: it starts the server with --record, tells you when to
whisper, speak or stay silent, then stops the server and prints the analysis.

    .venv/bin/python scripts/guided_recording.py [USB PORT | ble]

USB is the default; `ble` records over Bluetooth, so the band can sit in the retainer.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDINGS = ROOT / "recordings"
STEP_S = 10
TRIM_S = 2  # the analysis skips this much at each end of a step, while you are still switching
STEPS = (
    ("quiet", "STAY SILENT"),
    ("whisper", 'WHISPER, again and again:  "Hey Murmur, text Sam I am running late"'),
    ("talk", 'SAY IT OUT LOUD, again and again:  "Hey Murmur, text Sam I am running late"'),
    ("others", "STAY SILENT while someone next to you talks"),
)
WAV_HEADER_BYTES, BYTES_PER_S = 44, 32000  # 16 kHz mono 16 bit


def segments() -> str:
    return ",".join(f"{label}:{i * STEP_S + TRIM_S}-{(i + 1) * STEP_S - TRIM_S}" for i, (label, _) in enumerate(STEPS))


assert segments() == "quiet:2-8,whisper:12-18,talk:22-28,others:32-38"


def wait_for_sound(server: subprocess.Popen, before: set, timeout_s: float = 60) -> Path:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if server.poll() is not None:
            sys.exit("The server stopped. Read its message above (is another server still running?).")
        for wav in RECORDINGS.glob("*.wav"):
            if wav not in before and wav.stat().st_size > WAV_HEADER_BYTES + BYTES_PER_S // 2:
                return wav
        time.sleep(0.2)
    sys.exit(f"No sound arrived from the band in {timeout_s:.0f} s.")


def main() -> None:
    link = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbmodem101"
    band = [] if link == "ble" else ["--usb", link]  # no --usb: the server uses Bluetooth
    before = set(RECORDINGS.glob("*.wav"))
    # The mock engine: this run only records, so nothing is sent to a speech service.
    server = subprocess.Popen([sys.executable, "-m", "server", *band, "--engine", "mock", "--record"], cwd=ROOT)
    try:
        if not band:
            print("\nDouble-tap the band so it can be found (60 s), then put it in your mouth.")
        print("\nPut the band in your mouth. Waiting for its sound...")
        wav = wait_for_sound(server, before)
        started = time.monotonic() - (wav.stat().st_size - WAV_HEADER_BYTES) / BYTES_PER_S
        for i, (_, prompt) in enumerate(STEPS):
            while time.monotonic() < started + i * STEP_S:
                time.sleep(0.05)
            print(f"\n\n=====  STEP {i + 1} of {len(STEPS)}:  {prompt}  =====")
            for left in range(STEP_S, 0, -1):
                print(f"  {left}", end=" ", flush=True)
                time.sleep(max(0.0, started + (i + 1) * STEP_S - left + 1 - time.monotonic()))
        print("\n\nDone. Stopping the server...")
    finally:
        server.send_signal(signal.SIGINT)
        server.wait(timeout=10)
    subprocess.run([sys.executable, "scripts/analyze_recording.py", str(wav), "--segments", segments()], cwd=ROOT)
    print(f"\nRecording: {wav}")


if __name__ == "__main__":
    main()
