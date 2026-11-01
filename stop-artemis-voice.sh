#!/usr/bin/env bash
# Stop Artemis services: the systemd units if installed, plus any background
# processes started by start-artemis-voice.sh in fallback mode.
#   --legacy-only   only the background processes (used by the installer)
set -Eeuo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$BASE_DIR/logs"

if [[ "${1:-}" != "--legacy-only" ]] \
    && [[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/artemis-voice.target" ]]; then
    # The services by name too: stopping only the target returns before they stop.
    systemctl --user stop artemis-voice.target artemis-voice-app.service \
        artemis-voice-tts.service artemis-voice-stt.service
    echo "systemd services stopped (they start again at boot; see scripts/artemis-services.sh)."
fi

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
