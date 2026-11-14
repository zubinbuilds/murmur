"""Settings, read from environment variables and an optional .env file in the repo root."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT_PYTHON = ROOT / "vexo-open-kit" / "python"
SAMPLE_SPEECH = ROOT / "vexo-open-kit" / "dashboard" / "speech" / "sample-speech.opus"
WEB_DIR = ROOT / "web"
RECORDINGS_DIR = ROOT / "recordings"
OWN_VOICE_MODES = ("off", "motion")


def load_dotenv(path: Path | None = None) -> None:
    """Put KEY=value lines from .env into the environment (real environment variables win)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _get(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _num(name: str, default: float, cast=float):
    try:
        return cast(_get(name, str(default)))
    except ValueError:
        raise SystemExit(f"{name} must be a number, got {_get(name)!r}.")


@dataclass
class Config:
    band_number: int = 82
    transport: str = "ble"  # "ble" (real band), "sim" (pretend band replaying a sample recording) or "mic" (a Mac input)
    stt: str = "elevenlabs"  # elevenlabs | deepgram | pipecat (Deepgram Nova-3 through Pipecat) | mock
    elevenlabs_key: str = ""
    deepgram_key: str = ""
    instinct_to: str = ""
    dry_run: bool = True
    wake_aliases: tuple = ("murmur", "murmurs", "murmer")
    mic_gain_db: float = 20.0
    toggle_gesture: str = "double-tap"  # tap | double-tap | none
    tap_threshold: float = 1.2
    gate_threshold_db: float = -45.0
    gate_margin_db: float = 14.0
    turn_silence_ms: int = 700
    settle_s: float = 1.2  # wait this long after the last transcript piece before acting on a turn
    host: str = "127.0.0.1"
    port: int = 8000
    mic_device: str = ""  # input device name when transport is "mic"; empty means the system default
    serial_port: str = ""  # USB port when transport is "serial", e.g. /dev/cu.usbmodem101; empty lets the kit find the band
    replay_path: str = ""  # WAV played in real time when transport is "replay" (--replay)
    agent: str = "instinct"  # instinct (texted over iMessage) | gemini (Gemini answers on this Mac)
    gemini_key: str = ""
    gemini_model: str = "gemini-4-hydrogen"
    cleanup: bool = True  # tidy each command with Gemini before acting on it, when a Gemini key is set
    # whisper mode: keep the owner's whisper, drop people talking at the same volume (gate.py WHISPER_* constants)
    whisper_only: bool = True
    record: bool = False  # write the raw band audio, before the gate, to recordings/ for analyze_recording.py
    own_voice: str = "off"  # off | motion (a turn needs the band's accelerometer to feel the wearer speaking)
    voice_device: str = ""  # output device that speaks replies aloud; empty means replies are not spoken
    tts: str = "elevenlabs"  # elevenlabs | deepgram
    elevenlabs_voice_id: str = ""  # empty means the first voice in your ElevenLabs library

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        aliases = tuple(a.strip().lower() for a in _get("MURMUR_WAKE_ALIASES", "murmur,murmurs,murmer").split(",") if a.strip())
        return cls(
            band_number=_num("MURMUR_BAND", 82, int),
            transport=_get("MURMUR_TRANSPORT", "ble").lower(),
            stt=_get("MURMUR_STT", "elevenlabs").lower(),
            elevenlabs_key=_get("ELEVENLABS_API_KEY"),
            deepgram_key=_get("DEEPGRAM_API_KEY"),
            instinct_to=_get("INSTINCT_IMESSAGE_TO"),
            dry_run=_get("MURMUR_DRY_RUN", "1").lower() not in ("0", "false", "no", "off"),
            wake_aliases=aliases or ("murmur",),
            mic_gain_db=_num("MURMUR_MIC_GAIN_DB", 20.0),
            toggle_gesture=_get("MURMUR_TOGGLE_GESTURE", "double-tap").lower(),
            tap_threshold=_num("MURMUR_TAP_THRESHOLD", 1.2),
            gate_threshold_db=_num("MURMUR_GATE_THRESHOLD_DB", -45.0),
            gate_margin_db=_num("MURMUR_GATE_MARGIN_DB", 14.0),
            turn_silence_ms=_num("MURMUR_TURN_SILENCE_MS", 700, int),
            mic_device=_get("MURMUR_MIC_DEVICE"),
            serial_port=_get("MURMUR_SERIAL_PORT"),
            agent=_get("MURMUR_AGENT", "instinct").lower(),
            gemini_key=_get("GEMINI_API_KEY"),
            gemini_model=_get("GEMINI_MODEL", "gemini-4-hydrogen"),
            cleanup=_get("MURMUR_CLEANUP", "1").lower() not in ("0", "false", "no", "off"),
            whisper_only=_get("MURMUR_WHISPER_ONLY", "1").lower() not in ("0", "false", "no", "off"),
            record=_get("MURMUR_RECORD", "0").lower() not in ("0", "false", "no", "off"),
            own_voice=_get("MURMUR_OWN_VOICE", "off").lower(),
            voice_device=_get("MURMUR_VOICE_DEVICE"),
            tts=_get("MURMUR_TTS", "elevenlabs").lower(),
            elevenlabs_voice_id=_get("ELEVENLABS_VOICE_ID"),
        )
