#!/usr/bin/env bash
# Start all Artemis services: Whisper STT, Kokoro TTS, and the voice web app.
# Each service is skipped if it is already healthy.
set -Eeuo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

# Load configuration (.env is optional; real env vars take precedence).
if [[ -f "$BASE_DIR/.env" ]]; then
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        key="${key#export }"
        [[ -z "${!key+x}" ]] && export "$key=${value}"
    done < "$BASE_DIR/.env"
fi

# Self-contained: never reach out to Hugging Face or other model hubs.
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1

STT_PORT="${ARTEMIS_STT_PORT:-8083}"
TTS_PORT="${ARTEMIS_TTS_PORT:-8084}"
APP_PORT="${ARTEMIS_APP_PORT:-8080}"
STT_HOST="${ARTEMIS_STT_HOST:-127.0.0.1}"
TTS_HOST="${ARTEMIS_TTS_HOST:-127.0.0.1}"
APP_HOST="${ARTEMIS_APP_HOST:-127.0.0.1}"

STT_PYTHON="$BASE_DIR/venv-openvino/bin/python"
TTS_PYTHON="$BASE_DIR/venv-tts/bin/python"
APP_PYTHON="$BASE_DIR/venv/bin/python"

LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"

echo "========================================"
echo "     ARTEMIS VOICE SYSTEM STARTUP"
echo "========================================"

for file in \
    "$BASE_DIR/main.py" \
    "$BASE_DIR/whisper_server.py" \
    "$BASE_DIR/tts_server.py" \
    "$STT_PYTHON" \
    "$TTS_PYTHON" \
    "$APP_PYTHON"
do
    if [[ ! -f "$file" ]]; then
        echo "ERROR: Missing $file (see README, 'Setup')"
        exit 1
    fi
done

healthy() {
    [[ "$(curl -sS --max-time 2 -o /dev/null -w '%{http_code}' \
        "http://127.0.0.1:$1/health" 2>/dev/null)" == "200" ]]
}

# start_service <label> <python> <module:app> <host> <port> <log name> <timeout s>
start_service() {
    local label="$1" python="$2" target="$3" host="$4" port="$5" name="$6" timeout="$7"
    local log="$LOG_DIR/$name.log"

    if healthy "$port"; then
        echo "$label already running on :$port."
        return 0
    fi

    "$python" -m uvicorn "$target" --host "$host" --port "$port" > "$log" 2>&1 &
    local pid=$!
    echo "$pid" > "$LOG_DIR/$name.pid"
    echo "$label PID $pid, waiting for /health..."

    for _ in $(seq 1 "$timeout"); do
        if healthy "$port"; then
            echo "$label ready."
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "ERROR: $label exited during startup."
            tail -n 30 "$log"
            exit 1
        fi
        sleep 1
    done

    echo "ERROR: $label did not become ready in ${timeout}s."
    tail -n 30 "$log"
    exit 1
}

echo ""
echo "[1/3] Whisper STT (OpenVINO, ${ARTEMIS_STT_DEVICE:-GPU.0})"
start_service "Whisper STT" "$STT_PYTHON" whisper_server:app "$STT_HOST" "$STT_PORT" whisper 90

echo ""
echo "[2/3] Kokoro TTS (CPU)"
start_service "Kokoro TTS" "$TTS_PYTHON" tts_server:app "$TTS_HOST" "$TTS_PORT" tts 60

echo ""
echo "[3/3] Artemis voice interface"
start_service "Voice interface" "$APP_PYTHON" main:app "$APP_HOST" "$APP_PORT" voice 30

echo ""
echo "========================================"
echo "       ARTEMIS VOICE IS READY"
echo "========================================"
echo "Browser:   https://artemis-v01"
echo "Voice app: http://127.0.0.1:$APP_PORT (localhost only)"
echo "Whisper:   http://127.0.0.1:$STT_PORT"
echo "Kokoro:    http://127.0.0.1:$TTS_PORT"
echo ""
echo "Logs: $LOG_DIR/{voice,whisper,tts}.log"
echo "Stop: ./stop-artemis-voice.sh"
echo "========================================"
