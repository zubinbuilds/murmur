"""Settings, read from environment variables and an optional .env file in the repo root."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT_PYTHON = ROOT / "vexo-open-kit" / "python"
SAMPLE_SPEECH = ROOT / "vexo-open-kit" / "dashboard" / "speech" / "sample-speech.opus"
WEB_DIR = ROOT / "web"


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
    transport: str = "ble"  # "ble" (real band) or "sim" (pretend band replaying a sample recording)
    stt: str = "elevenlabs"  # elevenlabs | deepgram | mock
    elevenlabs_key: str = ""
    deepgram_key: str = ""
    instinct_to: str = ""
    dry_run: bool = True
    wake_aliases: tuple = ("vocal", "vocals")
    mic_gain_db: float = 20.0
    toggle_gesture: str = "double-tap"  # tap | double-tap | none
    tap_threshold: float = 1.2
    gate_threshold_db: float = -45.0
    gate_margin_db: float = 10.0
    turn_silence_ms: int = 700
    settle_s: float = 1.2  # wait this long after the last transcript piece before acting on a turn
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        aliases = tuple(a.strip().lower() for a in _get("VOCAL_WAKE_ALIASES", "vocal,vocals").split(",") if a.strip())
        return cls(
            band_number=_num("VOCAL_BAND", 82, int),
            transport=_get("VOCAL_TRANSPORT", "ble").lower(),
            stt=_get("VOCAL_STT", "elevenlabs").lower(),
            elevenlabs_key=_get("ELEVENLABS_API_KEY"),
            deepgram_key=_get("DEEPGRAM_API_KEY"),
            instinct_to=_get("INSTINCT_IMESSAGE_TO"),
            dry_run=_get("VOCAL_DRY_RUN", "1").lower() not in ("0", "false", "no", "off"),
            wake_aliases=aliases or ("vocal",),
            mic_gain_db=_num("VOCAL_MIC_GAIN_DB", 20.0),
            toggle_gesture=_get("VOCAL_TOGGLE_GESTURE", "double-tap").lower(),
            tap_threshold=_num("VOCAL_TAP_THRESHOLD", 1.2),
            gate_threshold_db=_num("VOCAL_GATE_THRESHOLD_DB", -45.0),
            gate_margin_db=_num("VOCAL_GATE_MARGIN_DB", 10.0),
            turn_silence_ms=_num("VOCAL_TURN_SILENCE_MS", 700, int),
        )
