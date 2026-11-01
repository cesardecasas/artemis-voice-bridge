#!/usr/bin/env bash
# Run every static check and test: mypy (all three services), tsc, pytest, node tests.
set -Eeuo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"

MYPY="venv/bin/mypy"

echo "== mypy: app"
"$MYPY" main.py artemis/ tests/

echo "== mypy: STT service"
"$MYPY" --python-executable venv-openvino/bin/python whisper_server.py

echo "== mypy: TTS service"
"$MYPY" --python-executable venv-tts/bin/python tts_server.py test_tts.py

echo "== tsc: frontend"
npx --no-install tsc -p jsconfig.json

echo "== pytest: backend protocol"
venv/bin/python -m pytest tests/ -q

echo "== node tests"
npm test --silent

echo ""
echo "All checks passed."
