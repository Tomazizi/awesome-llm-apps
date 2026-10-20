#!/usr/bin/env bash
# Gold Trace — one-command run (creates .venv on first use)
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi
exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
