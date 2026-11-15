"""Words the speech service should recognise more readily (Deepgram keyterm prompting).

load_keyterms() joins, in order: MURMUR_KEYTERMS, the user's own list (MURMUR_VOCAB_FILE), the bundled
vocab.txt (unless MURMUR_VOCAB=0) and, only with MURMUR_VOCAB_APPS=1, the names of the apps in
/Applications. Off by default because the names are sent to the speech service.
"""

from __future__ import annotations

import sys
from pathlib import Path

BUNDLED = Path(__file__).with_name("vocab.txt")
APPS_DIR = Path("/Applications")
# https://developers.deepgram.com/docs/keyterm: "Key Terms are limited to 500 tokens per request." and
# "Stay well under the 500 token limit; focus on the most important 20-50 terms"
CAP = 50


def _lines(path) -> list:
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"Murmur: vocabulary list not read ({e}).", file=sys.stderr)
        return []
    return [s for s in (line.strip() for line in text.splitlines()) if s and not s.startswith("#")]


def _apps() -> list:
    try:
        return sorted(p.stem for p in APPS_DIR.glob("*.app"))
    except OSError:
        return []


def load_keyterms(cfg) -> tuple:
    terms = list(cfg.keyterms)
    if cfg.vocab_file:
        terms += _lines(cfg.vocab_file)
    if cfg.vocab:
        terms += _lines(BUNDLED)
    if cfg.vocab_apps:
        terms += _apps()
    seen, out = set(), []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return tuple(out[:CAP])
