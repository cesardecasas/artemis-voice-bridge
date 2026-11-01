# Artemis Voice Bridge

Artemis Voice Bridge is the voice interface for **Artemis**, a local AI assistant. The browser records your voice and streams it to the backend. Speech is transcribed on the GPU, a reply is generated, and the reply is spoken back.

**It is fully self-contained.** Speech-to-text, text-to-speech and the web app all run on this machine. At runtime nothing is sent to a cloud service, and no model is downloaded.

> The agent is currently a placeholder that echoes what you said. It will be replaced by the **Hermes agent**; see [`PLAN.md`](PLAN.md) for the roadmap.

---

## Architecture

```
 Browser (phone / desktop)
   │  mic → MediaRecorder (WebM/Opus)
   │  WebSocket /ws  (typed JSON events + binary audio, see docs/protocol.md)
   ▼
 main.py → artemis.ws.VoiceConnection ── Voice app, :8080 (venv/) ─────┐
   │  receive loop (always listening for interrupt / barge-in)         │
   │  turn task (cancellable):                                         │
   │    1. SttClient ──────────► whisper_server.py  :8083              │
   │                              Whisper Large-v3-Turbo, OpenVINO B50 │
   │    2. AgentBackend.stream ─► Hermes gateway  :8642 (Runs API, SSE)│
   │                              → Gemma 4 on local vLLM  :8003       │
   │       tokens / tool events / approval requests → browser          │
   │    3. TtsClient ──────────► tts_server.py      :8084              │
   │                              Kokoro-82M, ONNX Runtime (CPU)       │
   │    4. tts.chunk + binary WAV frame → browser                      │
   └───────────────────────────────────────────────────────────────────┘
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
| Whisper warm-up at startup | 0.5 s (so the first real request isn't slow) |
| Kokoro TTS (CPU) | ~0.2× real time: 8 s of speech in 1.6 s; model loads in under 1 s |
| Full turn (end of speech → reply audio received) | ~0.9 s for a short sentence (STT 130 ms, TTS ~750 ms) |
| Hermes + Gemma 4, short question (reasoning off) | First audio ~0.9 s |
| Hermes + Gemma 4, five-sentence story (reasoning off) | First audio 1.7 s (21.6 s with Hermes's default reasoning) |
| Long replies (sentence streaming) | First sentence audible at ~0.74 s while the agent was still writing (full reply done at 3.8 s) |

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
| `ARTEMIS_APP_HOST` | `127.0.0.1` | Bind address. Localhost only, because Caddy proxies in |
| `ARTEMIS_APP_PORT` | `8080` | Voice app port |
| `ARTEMIS_ACCESS_TOKEN` | *(empty = no auth)* | Shared secret every browser must present (see "Access and security") |
| `ARTEMIS_MAX_CONNECTIONS` | `4` | Simultaneous WebSocket connections |
| `ARTEMIS_TURNS_PER_MINUTE` | `30` | Per-connection turn limit |
| `ARTEMIS_STT_DEVICE` | `GPU.0` | OpenVINO device for Whisper |
| `ARTEMIS_STT_LANGUAGE` | `en` | Whisper language; empty = auto-detect |
| `ARTEMIS_STT_INITIAL_PROMPT` | `Artemis, Hermes.` | Names Whisper should expect (see below) |
| `ARTEMIS_TTS_VOICE` | `af_heart` | Artemis's voice (list: `GET :8084/voices`) |
| `ARTEMIS_TTS_SPEED` | `1.0` | Speaking rate (0.5–2.0) |
| `ARTEMIS_MAX_AUDIO_BYTES` | `20971520` | Max size of one recording |
| `ARTEMIS_AGENT_BACKEND` | `echo` | `echo` (placeholder) or `hermes` (see [`docs/hermes.md`](docs/hermes.md)) |
| `ARTEMIS_ECHO_DEMO` | `false` | Echo agent emits a fake tool call + approval for messages containing "demo" |
| `ARTEMIS_HERMES_URL` | `http://127.0.0.1:8642` | Hermes gateway API server |
| `ARTEMIS_HERMES_KEY` | *(empty)* | Same value as `API_SERVER_KEY` in `~/.hermes/.env` |
| `ARTEMIS_HERMES_REASONING` | `off` | Hidden reasoning for voice turns: `off`, `low`, `medium`, `high`, `default` (= Hermes config). `off` answers about 8× sooner with Gemma 4 |
| `ARTEMIS_HERMES_INSTRUCTIONS` | *(built-in)* | Replaces the voice instructions sent with every Hermes run |

See [`.env.example`](.env.example) for the full list.

### Teaching Whisper names

Whisper didn't know the word "Artemis" (it heard *"Earthmas"*). `ARTEMIS_STT_INITIAL_PROMPT` primes the decoder with names it should expect. **Keep it a short, comma-separated list of names.** In testing, a full sentence such as *"A conversation with Artemis, the AI assistant."* leaked into transcripts when there was only background noise. The `hotwords` option was also tried; it dropped "Hermes" from real speech.

If Whisper returns only punctuation (e.g. `...` on noise), the STT service treats it as no speech.

---

## Running

```bash
./start-artemis-voice.sh   # starts STT → TTS → app, waiting for each /health
./stop-artemis-voice.sh    # stops all three
```

- Services that are already healthy are skipped.
- The start script forces `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`, so nothing tries to reach a model hub.
- Logs: `logs/voice.log`, `logs/whisper.log`, `logs/tts.log`. PIDs: `logs/*.pid`.
- Microphone access needs HTTPS or `localhost`. Use `https://artemis-v01` (Caddy) from other devices.

### Using the HUD

| Action | How |
|---|---|
| Talk | Tap the core, or **hold Space** (push-to-talk) |
| Send | Tap the core again, or release Space |
| Interrupt Artemis | Tap the core while it speaks, or press **Esc** |
| Cancel a recording | **Esc** (the audio is discarded) |
| Type instead | The box at the bottom (Enter to send) |
| Mute the voice | Speaker button, top right (remembered per device) |

The core's color tells you what Artemis is doing: **green** standing by, **cyan** listening, **blue** transcribing, **dusk** thinking, **silver** speaking, grey offline. Amber is reserved for approval requests and red for errors. The status strip polls `/health` every 15 s and shows the last turn's latency.

### Health endpoints

| URL | Returns |
|---|---|
| `GET :8080/health` | `ready` or `degraded`, the STT/TTS service status, agent backend status, session/connection counts, whether auth is on |
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

## Access and security

```
phone / laptop ──https://artemis-v01──► Caddy :443 (tls internal) ──► 127.0.0.1:8080 (voice app)
```

- **The app listens on `127.0.0.1` only.** The only way in is through Caddy (`/etc/caddy/Caddyfile` → `reverse_proxy 127.0.0.1:8080`). Port 8080 is not reachable from the LAN.
- **Access token.** `ARTEMIS_ACCESS_TOKEN` in `.env` (created with a random 32-character value; the file is `chmod 600` and git-ignored). The browser sends it in the first WebSocket message, so it never appears in URLs or logs.
- **Pairing a device:** open `https://artemis-v01/#token=<your token>` once. The page saves the token and removes it from the address bar; the `#…` part is never sent to the server. Or just open the page and paste the token into the form that appears.
  ```bash
  grep ARTEMIS_ACCESS_TOKEN .env   # show your token
  ```
- **Rotating the token:** change it in `.env` and restart the app. Each device will show the token form again.
- **Limits:** at most `ARTEMIS_MAX_CONNECTIONS` open connections and `ARTEMIS_TURNS_PER_MINUTE` turns per connection; a client must authenticate within 10 s.
- `/health` and the static UI files are public (they contain no data); everything that reaches STT, TTS or the agent requires the token.

---

## WebSocket protocol

Full spec with examples: **[`docs/protocol.md`](docs/protocol.md)**. In short:

- Text frames carry typed JSON events; binary frames carry audio only.
- Client → server: `hello`, `audio.start`, *(binary chunks)*, `audio.end`, `text.input`, `interrupt`, `approval.response`.
- Server → client: `session`, `state`, `transcript.final`, `agent.token`, `agent.tool_start`/`tool_end`, `agent.approval_request`, `agent.done`, `tts.chunk` (+ one binary WAV frame), `metrics`, `notice`, `error`.
- The UI is driven by `state` events (`idle → listening → transcribing → thinking → speaking → idle`).
- **Interrupting:** press **Esc** to stop Artemis or cancel a recording. Starting a new recording while Artemis is busy cancels the current turn (barge-in).
- **Sessions:** the session id is kept in `localStorage`, so a reload or reconnect resumes the same conversation history (held in server memory, last 50 sessions).

Audio is never written to disk: recordings are buffered in memory, and reply audio goes straight to a browser `Blob` URL that is revoked after playback.

---

## Project layout

```
main.py                 App factory (create_app): /health, /ws, static hosting
whisper_server.py       STT service (OpenVINO Whisper on the B50)
tts_server.py           TTS service (Kokoro-82M on CPU)
artemis/
  config.py             Typed settings shared by all three services (stdlib only)
  protocol.py           WebSocket message types + client-message validation
  ws.py                 VoiceConnection: receive loop, cancellable turns, approvals
  services.py           SttClient / TtsClient for the local services
  session.py            In-memory sessions with conversation history
  speech.py             SentenceSplitter: streamed text → speakable sentences (strips markdown/code)
  agent/
    base.py             AgentBackend protocol + events (Token, ToolStart, ToolEnd, Done)
    echo.py             EchoAgent placeholder (with optional demo tool/approval events)
    hermes.py           HermesAgent: Hermes Runs API (SSE events, approvals, stop)
    __init__.py         create_agent() picks the backend from ARTEMIS_AGENT_BACKEND
static/
  index.html            HUD markup: status strip, conversation, core orb, activity, composer
  app.js                Boot file → js/main.js
  css/artemis.css       Moonlight theme (palette tokens, core states, phone layout)
  fonts/                Sora + JetBrains Mono (woff2, SIL OFL; licenses alongside)
  manifest.json         PWA manifest (local icons only)
  favicon.ico, icons/   App icons, generated by scripts/make_icons.py
  js/
    types.js            JSDoc protocol types (mirror of artemis/protocol.py)
    constants.js        Limits, palette, close codes, storage keys
    state.js            Typed runtime state shared by modules
    storage.js          localStorage helpers that never throw
    send.js             Typed sendMessage / sendAudioChunk
    ui.js               Core state rendering, hint line, conversation, pairing form
    activity.js         Tool cards and approval cards (with countdown)
    health.js           Status strip: /health polling, link state, turn metrics
    websocket.js        Connection, hello/session, event dispatch, interrupt()
    recorder.js         Mic, MediaRecorder, audio.start/chunks/audio.end
    audio.js            Gapless Web Audio reply queue, level meter, chimes
    main.js             Tap/Space/Esc controls, composer, mute, boot
docs/protocol.md        WebSocket protocol spec
docs/hermes.md          Hermes setup, event mapping, reasoning/latency, approvals
scripts/make_icons.py   Regenerates all icons (Pillow, run with venv-openvino)
tests/
  test_ws.py            Backend protocol tests (pytest, fake STT/TTS/agent)
  test_speech.py        Sentence splitter tests
  test_hermes.py        HermesAgent against a fake Hermes Runs API
  frontend.test.js      Frontend tests (node --test, lightweight DOM mock)
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
- **Shared contract:** WebSocket messages are `TypedDict`s in `artemis/protocol.py`, mirrored as JSDoc typedefs in `static/js/types.js`. Client messages are validated by `parse_client_message`. The frontend `switch` over message types has an exhaustiveness check (`never`), so a new message type that isn't handled is a type error.
- **Agent interface:** `AgentBackend` is a `typing.Protocol`, so any class with `name`, `stream()` and `health()` plugs in, and mypy verifies it.

Run everything:

```bash
./check.sh          # mypy (app, STT, TTS, tests) + tsc + pytest + node tests
venv/bin/python -m pytest tests/ -q   # backend protocol tests only
npm run typecheck   # frontend types only
npm test            # frontend tests only
```

---

## Changelog

### 1.7.0: Hermes integration (Phase 6 of `PLAN.md`)

- **`HermesAgent`** (`artemis/agent/hermes.py`) connects Artemis to Hermes Agent through the gateway's Runs API: streamed tokens, tool cards, approval requests answered from the HUD, and interrupt/barge-in that stops the Hermes run. Select it with `ARTEMIS_AGENT_BACKEND=hermes`. Setup and details: [`docs/hermes.md`](docs/hermes.md).
- Each Artemis session maps to the Hermes session `artemis-<id>` (conversation kept server-side), with a stable `X-Hermes-Session-Key` for long-term memory and an `Idempotency-Key` per run.
- **Voice instructions** sent with every run: short spoken sentences, code and long output in fenced blocks (shown, never spoken).
- **Reasoning off for voice** (`ARTEMIS_HERMES_REASONING`, default `off`): Hermes is configured for `high` reasoning, which made Gemma 4 think silently for 20–35 s. A per-request override cut a story's first audio from 21.6 s to 1.7 s. Only Artemis's runs are affected.
- Finished tool cards keep the command (`✓ df -h /`); Hermes clarification prompts are answered automatically with "make a reasonable assumption".
- Agent streams are now closed promptly on interrupt (`contextlib.aclosing`), so Hermes is told to stop immediately. `AgentBackend.stream` is typed as an `AsyncGenerator`.
- Verified live through Caddy → Artemis → Hermes → local vLLM: chat, a `df -h /` tool call, and an interrupt (Hermes logged the stop).
- Tests: 17 Hermes tests against a fake Runs API (streaming, tools, approvals in both formats, clarify, stop on cancel, failures, SSE parsing). 55 backend + 21 frontend.

### 1.6.0: Sentence-streamed speech (Phase 5 of `PLAN.md`)

- **Artemis starts talking before the reply is finished.** `artemis/speech.py` splits streamed agent text into sentences (handling abbreviations, initials, decimals, quotes, ellipses, and very long runs without punctuation). Each complete sentence goes to a background `SpeechPipeline` that synthesizes in order and sends one `tts.chunk` + WAV per sentence while the agent keeps generating.
- Markdown is cleaned before speaking (no "asterisk asterisk"), and fenced code blocks are never spoken.
- **Gapless playback:** the client decodes chunks in arrival order and schedules each one exactly where the previous one ends on a shared `AudioContext` (replaces `new Audio()`), with one analyser driving the orb.
- **Interrupt / barge-in** now also cancels the sentences still waiting for TTS, and the client flushes its queue; the next reply starts immediately.
- If TTS fails mid-reply, one `tts_unavailable` error is sent and the reply continues as text.
- `EchoAgent` demo mode (`ARTEMIS_ECHO_DEMO=1`, say "demo") now streams a slow multi-sentence reply to show this off.
- Measured with real Kokoro: first sentence audible at 738 ms; the agent finished writing at 3.8 s. Interrupt after sentence 2 stopped all further audio immediately.
- Tests: 12 splitter tests, 3 streaming tests (speech before `agent.done`, interrupt cancels pending TTS, TTS failure keeps text); frontend queue tests (back-to-back scheduling, flush on interrupt). 38 backend + 21 frontend.
- **README rebuilt:** the 1.5.0 copy was corrupted (see below).

### 1.5.0: Artemis HUD (Phase 4 of `PLAN.md`)

- **New UI** built from the approved "Moonlight" mockup, with green and dusk as signature colors. Desktop: conversation | core | activity. Phones: core on top, conversation below, activity hidden while empty.
- **Core orb** (SVG + CSS) with a distinct look per state. The level meter follows the live mic while listening and the real TTS audio (through an `AnalyserNode`) while speaking.
- **Controls:** tap the core, hold Space for push-to-talk, Esc to cancel or interrupt, a text composer, a mute toggle, and short synthesized chimes (Web Audio, no sound files).
- **Activity feed:** tool cards (spinner → ✓/✗ with duration) and amber approval cards with Approve/Deny and a countdown that matches the server's 120 s timeout. Interrupts and errors mark running tools as cut off.
- **Status strip:** Hearing / Voice / Agent / Link, with model and device from `/health`, plus per-turn latency from `metrics`.
- `/health` service entries now include a `detail` string (e.g. `whisper-large-v3-turbo · GPU.0`, `Kokoro · af_heart`).
- **Self-contained fonts:** Sora and JetBrains Mono are bundled in `static/fonts/` (SIL OFL licenses included). `static/` still has no external URLs.
- Boot fade-in on first connect; `prefers-reduced-motion` disables all animation.
- Tests: frontend tests rewritten for the new UI with a richer DOM mock (19 total).
- *Note:* the README committed with 1.5.0 was corrupted by a faulty edit script (25 MB of repeated text). It was rebuilt in 1.6.0.

### 1.4.0: Access token and limits (Phase 3c of `PLAN.md`)

- **Access token:** `ARTEMIS_ACCESS_TOKEN` is required in the `hello` message (constant-time comparison), with a 10 s hello deadline. Bad or missing tokens get `error: unauthorized` and close code `4401`.
- **Device pairing** via `https://artemis-v01/#token=…`; the fragment never reaches the server and is removed from the address bar. There's also a token form in the UI.
- **Bound to `127.0.0.1`** (was `0.0.0.0`): only Caddy can reach the app.
- **Limits:** max concurrent connections (close `1013`, client retries after 10 s) and a per-connection turns-per-minute limit (`error: rate_limited`).
- `/health` reports connection count and whether auth is enabled.
- Tests: 8 new backend security tests (23 total) and 2 frontend pairing tests (14 total). Verified end to end through Caddy over `wss://`: no token → `4401`, wrong token → `4401`, correct token → accepted. The token appears nowhere in the logs.

### 1.3.0: Typed event protocol and backend package (Phase 3a–3b of `PLAN.md`)

**Protocol v1** (see [`docs/protocol.md`](docs/protocol.md))
- Replaced the 4-byte `END_MARKER` and the ad-hoc `status`/`response` messages with typed events: `hello`/`session`, `audio.start`/`audio.end`, `text.input`, `interrupt`, `approval.response` ↔ `state`, `transcript.final`, `agent.token`, `agent.tool_start`/`tool_end`, `agent.approval_request`, `agent.done`, `tts.chunk`, `metrics`, `notice`, `error` (with codes).
- The UI is driven by `state` events instead of string matching.

**Backend** (`main.py` split into the `artemis/` package)
- `artemis/ws.py`: each connection has an always-on receive loop, and each turn runs as a cancellable task. This enables **interrupt** and **barge-in**, which wasn't possible before because the loop blocked during processing.
- `artemis/agent/`: `AgentBackend` protocol with streamed events, plus `EchoAgent` (streams word by word). `ARTEMIS_AGENT_BACKEND` selects the backend; `ARTEMIS_ECHO_DEMO` exercises tool and approval events.
- Approval requests are round-tripped to the browser (120 s timeout → denied).
- `artemis/session.py`: sessions with conversation history survive reconnects and reloads.
- `artemis/services.py`: `SttClient` / `TtsClient`; `ServiceError` now records which service failed.
- `main.py` is now an app factory (`create_app`), so tests can inject fake services.
- `/health` also reports the agent backend and the session count.
- Turn metrics (STT, first token, first audio, total).

**Frontend**
- Streaming reply rendering (one bubble, finalized by `agent.done`), tool-activity lines, minimal Approve/Deny buttons.
- **Esc** interrupts Artemis or cancels a recording; recording while Artemis is busy barges in.
- Session id saved in `localStorage`.
- New `send.js` with typed `sendMessage`; colors centralized in `COLORS`.

**Tests**
- New `tests/test_ws.py` (pytest, 15 tests): full turn, text input, no speech, empty recording, oversized-recording drain, STT failure, bad messages, interrupt, barge-in, approval round trip, session resume, health.
- Frontend tests rewritten for the protocol (12 tests).
- `check.sh` now runs mypy on the tests, plus pytest.

### 1.2.0: Self-containment cleanup (Phase 2 of `PLAN.md`)

**Speech-to-text**
- Whisper is primed with `initial_prompt="Artemis, Hermes."` and the language is fixed to English, both configurable. This fixes "Artemis" being heard as "Earthmas". Prompt options were A/B tested against speech and synthetic noise (see "Teaching Whisper names").
- Whisper warms up at startup (0.5 s), so the first request isn't slow.
- Punctuation-only output (`.`, `...`) is returned as empty, so the UI shows "No speech detected" instead of an empty-looking turn.

**Self-contained frontend**
- New local icon set (moon crescent + orbital ring) generated by `scripts/make_icons.py`: 192/512, maskable 512, apple-touch 180, and a multi-size `favicon.ico` (fixes the `/favicon.ico` 404).
- `manifest.json` no longer loads its icon from flaticon. Name, colors and scope now match the UI (`#04070d`).
- iOS home-screen meta tags added.
- Removed `static/sw.js`. It was never registered, and its cache-first logic would have served stale code.
- Audit: `static/` contains no external URLs.

**Housekeeping**
- `edge-tts` uninstalled from `venv/`.

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
