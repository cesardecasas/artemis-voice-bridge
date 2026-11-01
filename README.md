# Artemis Voice Bridge

Artemis Voice Bridge is the voice interface for **Artemis**, a local AI assistant. The browser records your voice and streams it to the backend. Speech is transcribed on the GPU, a reply is generated, and the reply is spoken back.

**It is fully self-contained.** Speech-to-text, text-to-speech and the web app all run on this machine. At runtime nothing is sent to a cloud service, and no model is downloaded.

> The agent is currently a placeholder that echoes what you said. It will be replaced by the **Hermes agent**; see [`PLAN.md`](PLAN.md) for the roadmap.

---

## Architecture

```
 Browser (phone / desktop)
   │  mic → MediaRecorder (WebM/Opus)
   │  WebSocket /ws
   ▼
 main.py  ── Voice app, :8080 (venv/) ─────────────────────────────┐
   │  1. collect audio chunks in memory until END_MARKER           │
   │  2. POST /transcribe ──────► whisper_server.py  :8083         │
   │                               Whisper Large-v3-Turbo,         │
   │                               OpenVINO on Arc Pro B50 (GPU.0) │
   │  3. agent reply (echo for now → Hermes later)                 │
   │  4. POST /synthesize ──────► tts_server.py      :8084         │
   │                               Kokoro-82M, ONNX Runtime (CPU)  │
   │  5. send JSON "response" + one binary WAV frame               │
   └───────────────────────────────────────────────────────────────┘
```

Each service runs in its own virtualenv, so their dependency stacks never conflict:

| Service | File | Port | Virtualenv | Python |
|---|---|---|---|---|
| Voice app (UI + WebSocket) | `main.py` | 8080 | `venv/` | 3.14 |
| Speech-to-text | `whisper_server.py` | 8083 | `venv-openvino/` | 3.12 |
| Text-to-speech | `tts_server.py` | 8084 | `venv-tts/` | 3.12 |

### Measured performance (this machine)

| Step | Result |
|---|---|
| Kokoro TTS (CPU) | ~0.2× real time: 8 s of speech in 1.6 s; model loads in under 1 s |
| Full turn (end of speech → reply audio received) | ~1.3 s for a short sentence |

---

## Setup

### 1. Virtualenvs

```bash
# Voice app
python3.14 -m venv venv
venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Whisper STT (OpenVINO)
python3.12 -m venv venv-openvino
venv-openvino/bin/pip install -r requirements-stt.txt

# Kokoro TTS
python3.12 -m venv venv-tts
venv-tts/bin/pip install -r requirements-tts.txt

# Frontend type checking (dev only)
npm install
```

### 2. Models

Models live in `models/` (git-ignored). Download them once; after that everything runs offline.

| Model | Path | Source |
|---|---|---|
| Whisper Large-v3-Turbo (OpenVINO IR) | `models/whisper-large-v3-turbo-ready/` | OpenVINO export of `openai/whisper-large-v3-turbo` |
| Kokoro-82M v1.0 (ONNX) | `models/kokoro/kokoro-v1.0.onnx` | [kokoro-onnx releases, `model-files-v1.0`](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0) |
| Kokoro voice pack | `models/kokoro/voices-v1.0.bin` | same release |

```bash
mkdir -p models/kokoro && cd models/kokoro
B=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
curl -fLO $B/kokoro-v1.0.onnx && curl -fLO $B/voices-v1.0.bin
```

SHA-256 checksums of the files in use:

```
7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5  kokoro-v1.0.onnx
bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d  voices-v1.0.bin
```

Kokoro's phonemizer uses the espeak-ng library bundled in the `espeakng-loader` wheel, so no system package is needed.

### 3. Configuration

```bash
cp .env.example .env   # optional; every value has a default
```

All settings live in `.env` and are read by [`artemis/config.py`](artemis/config.py) into typed, frozen dataclasses. Real environment variables override the file. Key settings:

| Variable | Default | Purpose |
|---|---|---|
| `ARTEMIS_APP_PORT` | `8080` | Voice app port |
| `ARTEMIS_STT_DEVICE` | `GPU.0` | OpenVINO device for Whisper |
| `ARTEMIS_TTS_VOICE` | `af_heart` | Artemis's voice (list: `GET :8084/voices`) |
| `ARTEMIS_TTS_SPEED` | `1.0` | Speaking rate (0.5–2.0) |
| `ARTEMIS_MAX_AUDIO_BYTES` | `20971520` | Max size of one recording |

See [`.env.example`](.env.example) for the full list.

---

## Running

```bash
./start-artemis-voice.sh   # starts STT → TTS → app, waiting for each /health
./stop-artemis-voice.sh    # stops all three
```

- Services that are already healthy are skipped.
- The start script forces `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`, so nothing tries to reach a model hub.
- Logs: `logs/voice.log`, `logs/whisper.log`, `logs/tts.log`. PIDs: `logs/*.pid`.
- Microphone access needs HTTPS or `localhost`. The LAN URL `https://artemis-v01` is served through your HTTPS proxy.

### Health endpoints

| URL | Returns |
|---|---|
| `GET :8080/health` | `ready` or `degraded`, plus the status of the STT and TTS services |
| `GET :8083/health` | Whisper status, model, device |
| `GET :8084/health` | Kokoro status, model, default voice |
| `GET :8084/voices` | All available voice IDs |

### Choosing a voice

```bash
venv-tts/bin/python test_tts.py --list-voices
venv-tts/bin/python test_tts.py --voice bf_emma --out /tmp/emma.wav   # listen, compare
```

Then set `ARTEMIS_TTS_VOICE` in `.env` and restart.

---

## WebSocket protocol (current)

Client → server:

| Frame | Meaning |
|---|---|
| binary | WebM/Opus audio chunk (every 100 ms while recording) |
| binary `00 00 00 00` | End of recording |

Server → client (JSON text frames, typed in `main.py` and `static/js/types.js`):

| `type` | Fields | Meaning |
|---|---|---|
| `status` | `text` | Progress (`Transcribing...`, `Generating speech...`, `No speech detected`) |
| `transcription` | `text` | What you said |
| `response` | `text`, `audio: {format: "wav", bytes} \| null` | Artemis's reply. If `audio` is set, **one binary WAV frame follows immediately** |
| `error` | `text` | Something failed (the reply text is still sent if only TTS failed) |

Synthesized audio is never written to disk. It goes straight from the TTS service, through the WebSocket, to a browser `Blob` URL, which is revoked after playback. Recordings are also buffered only in memory.

> Phase 3 of the plan replaces this with a richer typed event protocol (`state`, `agent.token`, `agent.tool_*`, `interrupt`, …) for Hermes.

---

## Project layout

```
main.py                 Voice app: WebSocket voice loop, /health, static hosting
whisper_server.py       STT service (OpenVINO Whisper on the B50)
tts_server.py           TTS service (Kokoro-82M on CPU)
artemis/
  config.py             Typed settings shared by all three services (stdlib only)
static/
  index.html            UI: status pill, transcript, record orb, visualizer
  app.js                Boot file → js/main.js
  js/
    types.js            JSDoc types for server messages (mirror of main.py)
    constants.js        END_MARKER, MIME type, limits
    state.js            Typed runtime state shared by modules
    ui.js               DOM updates (status, button, transcript)
    websocket.js        Connection, message parsing/dispatch, binary audio frames
    recorder.js         Mic permission, MediaRecorder, chunk upload, max duration
    audio.js            Playback of WAV blobs, visualizer animation, cleanup
    main.js             Wires the record button and starts the connection
tests/frontend.test.js  Node tests with a lightweight DOM mock
test_whisper_gpu.py     Whisper GPU benchmark
test_tts.py             Kokoro TTS benchmark and voice audition
start-/stop-artemis-voice.sh, check.sh
PLAN.md                 Roadmap with action items
```

---

## Types and checks

The codebase is typed end to end for maintainability:

- **Python:** full type hints, checked by **mypy in `strict` mode** (`pyproject.toml`). mypy is installed once in `venv/` and pointed at each service's own venv with `--python-executable`, so it sees the right packages.
- **Frontend:** plain ES modules with **JSDoc types plus `// @ts-check`**, checked by **TypeScript in `strict` mode** (`jsconfig.json`, `noEmit`). There's no build step; the browser still loads the files as they are.
- **Shared contract:** WebSocket messages are `TypedDict`s in `main.py` and mirrored as JSDoc typedefs in `static/js/types.js`. The frontend `switch` over message types has an exhaustiveness check (`never`), so a new message type that isn't handled is a type error.

Run everything:

```bash
./check.sh          # mypy (app, STT, TTS) + tsc + node tests
npm run typecheck   # frontend types only
npm test            # frontend tests only
```

---

## Changelog

### 1.1.0: Local TTS and typed codebase (Phases 0–1 of `PLAN.md`)

**Self-contained speech**
- Replaced `edge_tts` (Microsoft cloud) with a local **Kokoro-82M** service, `tts_server.py`, on port 8084 (`/health`, `/voices`, `/synthesize`). It warms up at startup.
- Speech is sent to the browser as a **binary WAV WebSocket frame**. No more mp3 files in `media/`, and the `/media` static route was removed.
- Recordings are buffered **in memory** instead of temporary `.webm` files.
- The start script forces offline mode for Hugging Face libraries.

**Backend**
- New `artemis/config.py`: typed settings from `.env` and environment variables; ports, devices, model paths and voice are no longer hardcoded.
- `main.py` restructured: `receive_recording` / `handle_turn` functions, a shared `httpx.AsyncClient`, a `ServiceError` for user-facing service failures, and typed messages (`TypedDict`).
- If TTS fails, the reply **text is still delivered**, followed by an error.
- Oversized recordings now drain the rest of their stream, so leftover chunks don't leak into the next turn.
- New `GET /health` on the app, aggregating STT and TTS status.
- `whisper_server.py`: typed, reads the shared config, unused `librosa` import removed.

**Frontend**
- Plays the binary WAV frame through a `Blob` URL, which is revoked after playback.
- Message handling split into testable `parseServerMessage`, `handleServerMessage` and `handleAudioFrame`.
- All modules `// @ts-check` with JSDoc types; strict TypeScript passes.

**Tooling**
- `.gitignore`, `.env.example`, pinned `requirements*.txt` for each venv, `pyproject.toml` (mypy strict), `jsconfig.json`.
- `start-artemis-voice.sh` rewritten: three services, one generic `start_service` helper, `.env` support, PID files. New `stop-artemis-voice.sh` and `check.sh`.
- `test_tts.py` benchmark and voice audition tool.
- Frontend tests: 3 → 8, covering message parsing and the response → binary-audio flow.

### 1.0.0: Initial prototype
- Browser recorder → WebSocket → OpenVINO Whisper on the B50 → echo reply → `edge_tts`.
