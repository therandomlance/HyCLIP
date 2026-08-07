#!/usr/bin/env bash
# Run all HyCLIP tests in dependency order.
set -euo pipefail
cd "$(dirname "$0")"

.venv/bin/python test/test_db.py
.venv/bin/python test/test_api.py
.venv/bin/python test/test_model.py
