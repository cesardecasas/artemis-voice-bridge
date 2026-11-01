#!/usr/bin/env bash
# Stop Artemis services started by start-artemis-voice.sh.
set -Eeuo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$BASE_DIR/logs"

for name in voice tts whisper; do
    pid_file="$LOG_DIR/$name.pid"

    if [[ ! -f "$pid_file" ]]; then
        echo "$name: no PID file, skipping."
        continue
    fi

    pid="$(cat "$pid_file")"

    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        for _ in {1..20}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "$name: did not exit, sending SIGKILL."
            kill -9 "$pid"
        fi
        echo "$name: stopped (PID $pid)."
    else
        echo "$name: not running."
    fi

    rm -f "$pid_file"
done
