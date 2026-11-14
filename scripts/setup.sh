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

[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env: open it and paste your API keys."
fi

echo "Setup done. Start the app with: .venv/bin/python -m server --sim"
