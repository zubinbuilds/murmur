"""Check the API keys in .env: one cheap authenticated read per service. Standard library only.

    .venv/bin/python scripts/check_keys.py

Prints OK or FAIL per service with the HTTP status. Keys are never printed.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server.config import Config  # noqa: E402

# https://developers.deepgram.com/reference/manage/projects/list
DEEPGRAM_URL = "https://api.deepgram.com/v1/projects"
# https://elevenlabs.io/docs/api-reference/user/get
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/user"
# https://aistudio.google.com/api-reference/models/list-models (the docs show the key as ?key=)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
TIMEOUT_S = 10


def get(url: str, headers: dict) -> tuple:
    """(status or None, body text). Never raises for an HTTP or network error."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode(errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"no connection: {getattr(e, 'reason', e)}"


def _fail(status, body: str, key: str) -> str:
    reason = " ".join(body.replace(key, "***").split())[:200]
    return f"FAIL ({status or 'no HTTP status'}): {reason}"


def check_deepgram(key: str, http=get) -> tuple:
    status, body = http(DEEPGRAM_URL, {"Authorization": f"Token {key}"})
    if status != 200:
        return False, _fail(status, body, key)
    return True, f"OK (200): {len(json.loads(body).get('projects') or [])} project(s)"


def check_elevenlabs(key: str, http=get) -> tuple:
    status, body = http(ELEVENLABS_URL, {"xi-api-key": key})
    if status != 200:
        return False, _fail(status, body, key)
    sub = json.loads(body).get("subscription") or {}
    return True, (f"OK (200): tier {sub.get('tier', '?')}, "
                  f"{sub.get('character_count', '?')} of {sub.get('character_limit', '?')} characters used")


def check_gemini(key: str, model: str, http=get) -> tuple:
    # shortcut: one page of up to 1000 models (the documented maximum); follow nextPageToken if that is ever exceeded
    status, body = http(f"{GEMINI_URL}?{urllib.parse.urlencode({'key': key, 'pageSize': 1000})}", {})
    if status != 200:
        return False, _fail(status, body, key)
    names = [m.get("name", "").removeprefix("models/") for m in json.loads(body).get("models") or []]
    gemini4 = [n for n in names if "gemini-4" in n]
    found = model in names
    line = (f"{'OK' if found else 'FAIL'} (200): GEMINI_MODEL {model!r} is "
            f"{'available' if found else 'NOT in the list'}. gemini-4 models: {', '.join(gemini4) or 'none'}")
    return found, line


def main(cfg: Config | None = None, http=get, out=print) -> int:
    cfg = cfg or Config.from_env()
    checks = [
        ("Deepgram", "DEEPGRAM_API_KEY", cfg.deepgram_key, lambda k: check_deepgram(k, http)),
        ("ElevenLabs", "ELEVENLABS_API_KEY", cfg.elevenlabs_key, lambda k: check_elevenlabs(k, http)),
        ("Gemini", "GEMINI_API_KEY", cfg.gemini_key, lambda k: check_gemini(k, cfg.gemini_model, http)),
    ]
    failed = 0
    for name, var, key, check in checks:
        if not key:
            out(f"{name:<11} not set ({var} is empty in .env)")
            continue
        try:
            ok, line = check(key)
        except (ValueError, AttributeError) as e:  # a 200 whose body is not the documented JSON
            ok, line = False, f"FAIL (200): unexpected reply: {str(e).replace(key, '***')[:200]}"
        failed += not ok
        out(f"{name:<11} {line}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
