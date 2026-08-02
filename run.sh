#!/usr/bin/env bash
# Start the HyCLIP API server.
source "$(dirname "$0")/env.sh"

exec "$VENV/bin/uvicorn" api:app --host "$HOST" --port "$PORT"
