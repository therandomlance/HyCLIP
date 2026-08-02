#!/usr/bin/env bash
# Shared setup for run.sh and ingest.sh: locate the venv and figure out where the API lives.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=".venv"
if [ ! -d "$VENV" ]; then
	echo "virtualenv not found: $VENV" >&2
	exit 1
fi

read -r API_URL HOST PORT < <("$VENV/bin/python" -m config)
