# `artemis/`: backend package

Python code shared by the three services. The voice app (`main.py`, in `venv/`) uses all of it. The Whisper and Kokoro services (`whisper_server.py` in `venv-openvino/`, `tts_server.py` in `venv-tts/`) import only the **stdlib-only** modules: `config.py`, `logs.py` and `serve.py`. Keep those free of third-party imports, or the STT/TTS services break.

| Module | What it does |
|---|---|
| `config.py` | Typed settings for every service, read from environment variables and `.env`. Every `ARTEMIS_*` variable is defined here (the README lists them). Stdlib only |
| `logs.py` | Logging for all services: text for a terminal, or JSON lines (`ARTEMIS_LOG_FORMAT=json`, used under systemd). `log_context` attaches the session id to every line a connection writes. Stdlib only |
| `serve.py` | `python -m artemis.serve app\|stt\|tts` runs one service on its configured host/port. The systemd units use it. Stdlib + uvicorn |
| `protocol.py` | The WebSocket protocol: `TypedDict`s for every message, the client-message validator, error codes, close codes. **Source of truth** for [`docs/protocol.md`](../docs/protocol.md) and `static/js/types.js` |
| `ws.py` | `VoiceConnection`, one per browser connection: auth, the receive loop, cancellable turns (STT → agent → TTS), interrupts, approvals, new conversations, and the hands-free host |
| `services.py` | HTTP clients for the local Whisper (`SttClient`) and Kokoro (`TtsClient`) services, with health checks |
| `session.py` | In-memory sessions (last 50). Unknown well-formed ids are adopted, so a conversation survives an app restart (Hermes holds the history) |
| `speech.py` | `SentenceSplitter`: streamed agent text → speakable sentences, with markdown and code stripped. Lets TTS start before the reply is finished |
| `vad.py` | Silero VAD (ONNX) and `SpeechSegmenter`: a continuous 16 kHz PCM stream → speech segments |
| `language.py` | English/Spanish detection per sentence, to pick the reply voice; `fold()` strips accents for phrase matching |
| `wake.py` | Wake word ("Artemis", fuzzy) and spoken approve/deny, matched on Whisper transcripts |
| `handsfree.py` | Hands-free state machine: armed / listening / follow-up window, stop phrases, "new conversation" |
| `agent/` | The brain behind Artemis: see [`agent/README.md`](agent/README.md) |

## Conventions

- **Strict typing:** `mypy --strict` must pass (`./check.sh`). Protocol messages are `TypedDict`s, not loose dicts.
- **Privacy:** never log transcripts, replies, tokens or keys. Log events, timings and ids only. Audio is never written to disk.
- **Async:** one `VoiceConnection` per socket. A turn is an `asyncio.Task` that can be cancelled at any await (interrupt, barge-in, new conversation).
- **Protocol changes** touch three places: `protocol.py`, `static/js/types.js` and `docs/protocol.md`. Also add a test in `tests/test_ws.py`.
