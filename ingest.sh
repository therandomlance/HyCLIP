#!/usr/bin/env bash
# Start the server in the background, then ingest images tagged 'hyclip:ingest' from hydrus.
source "$(dirname "$0")/env.sh"

SERVER_LOG="${HYCLIP_SERVER_LOG:-server.log}"
"$VENV/bin/uvicorn" api:app --host "$HOST" --port "$PORT" >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 60); do
	if curl -sf "$API_URL/" >/dev/null 2>&1; then
		break
	fi
	sleep 0.5
done

if ! curl -sf "$API_URL/" >/dev/null 2>&1; then
	echo "server failed to start (log: $SERVER_LOG)" >&2
	tail -n 20 "$SERVER_LOG" >&2
	exit 1
fi

echo "server log: $SERVER_LOG"
"$VENV/bin/python" ingest.py --api-url "$API_URL" "$@"
