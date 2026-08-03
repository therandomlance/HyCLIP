#!/usr/bin/env bash
# Start the HyCLIP API server.
set -euo pipefail
cd "$(dirname "$0")"

read -r API_URL HOST PORT < <(.venv/bin/python -m config)

exec .venv/bin/uvicorn api:app --host "$HOST" --port "$PORT"
