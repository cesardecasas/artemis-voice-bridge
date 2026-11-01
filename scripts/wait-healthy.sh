#!/usr/bin/env bash
# Wait until http://127.0.0.1:<port>/health answers 200, up to <timeout> seconds.
# Used as ExecStartPost by the systemd units, so `systemctl start` waits for
# the model to load and a service that never becomes healthy counts as failed.
set -Eeuo pipefail
port="${1:?usage: wait-healthy.sh <port> [timeout s]}"
timeout="${2:-60}"
for ((i = 0; i < timeout; i++)); do
    code="$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health" 2>/dev/null || true)"
    [[ "$code" == "200" ]] && exit 0
    sleep 1
done
echo "No healthy /health on :$port after ${timeout}s" >&2
exit 1
