#!/usr/bin/env bash
# One-time setup: fetch the Vexo kit, make a virtualenv, install dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."

# The kit commit this project was written against. Change it on purpose, not by accident.
KIT_REF="${KIT_REF:-1e4291e}"

if [ ! -d vexo-open-kit ]; then
  git clone https://github.com/VexoAI-Org/vexo-open-kit.git vexo-open-kit
  git -C vexo-open-kit checkout --quiet "$KIT_REF"
else
  echo "vexo-open-kit/ already present (leaving it as it is)"
fi

# pipecat-ai needs Python 3.11 or newer; macOS ships 3.9 as python3.
PY=""
for candidate in python3 python3.13 python3.12 python3.11; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Murmur needs Python 3.11 or newer. Install it (for example: brew install python@3.12) and run this again." >&2
  exit 1
fi
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo ".venv/ uses Python older than 3.11. Delete it (rm -rf .venv) and run this again." >&2
  exit 1
fi

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env: open it and paste your API keys."
fi

echo "Setup done. Start the app with: .venv/bin/python -m server --sim"
