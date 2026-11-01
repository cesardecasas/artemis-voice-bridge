# Artemis WebSocket Protocol (v1)

The browser and the voice app talk over a single WebSocket at `/ws`.

- **Text frames** carry JSON events with a `type` field.
- **Binary frames** carry audio only:
  - client → server: WebM/Opus chunks between `audio.start` and `audio.end` (push-to-talk); otherwise, after `handsfree.start`, a continuous stream of 16 kHz mono PCM16 frames (hands-free)
  - server → client: exactly one WAV frame, immediately after each `tts.chunk` event

Source of truth: [`artemis/protocol.py`](../artemis/protocol.py) (Python `TypedDict`s and the client-message validator). The frontend mirror is [`static/js/types.js`](../static/js/types.js). Keep all three in sync; `mypy` and `tsc` catch drift within each side.

---

## Client → server

| `type` | Fields | Meaning |
|---|---|---|
| `hello` | `session_id: string \| null`, `token: string \| null` | First message. `token` is required when the server sets `ARTEMIS_ACCESS_TOKEN`. Pass a saved `session_id` to resume a session's history |
| `audio.start` | `format: "webm/opus"` | Begin a recording. **Cancels any turn in progress (barge-in)** |
| *(binary)* | — | WebM/Opus audio chunk |
| `audio.end` | — | Recording finished; the server starts a turn |
| `text.input` | `text: string` | Typed input; skips STT. Also cancels any turn in progress |
| `interrupt` | — | Stop: cancels the running turn and discards any open recording |
| `approval.response` | `id: string`, `approved: boolean` | Answer to an `agent.approval_request` |
| `handsfree.start` | `format: "pcm16"`, `sample_rate: 16000` | Start hands-free: binary frames outside a recording are now little-endian PCM16 at 16 kHz (any frame size; the browser sends 100 ms). Fails with `handsfree_unavailable` if the VAD model is missing |
| `handsfree.stop` | — | Stop hands-free |
| `session.new` | — | Start a new conversation: cancels any turn, then the server sends a new `session` (and `state: idle`) |
| `playback.idle` | — | Artemis's reply finished playing (not sent when it was interrupted). Opens the follow-up window |

Malformed messages get `error` with code `bad_request`; the connection stays open.

## Server → client

| `type` | Fields | Meaning |
|---|---|---|
| `session` | `session_id`, `protocol` (=1), `agent` | Reply to `hello` or `session.new`. The client stores `session_id`; a different id than before means a new conversation (clear the HUD). An unknown but well-formed 32-hex id in `hello` is adopted, so conversations survive a server restart |
| `state` | `value: idle \| listening \| transcribing \| thinking \| speaking` | Pipeline state. The UI is driven by this |
| `transcript.final` | `text` | What the user said (or typed) |
| `agent.token` | `text` | A streamed piece of the reply |
| `agent.tool_start` | `id`, `name`, `summary` | The agent began using a tool |
| `agent.tool_end` | `id`, `ok`, `summary` | The tool finished |
| `agent.approval_request` | `id`, `action`, `detail` | The agent wants permission. No answer within 120 s counts as denied |
| `agent.done` | `text` | The complete, authoritative reply text |
| `tts.chunk` | `seq`, `final`, `format: "wav"`, `bytes` | **One binary WAV frame follows immediately.** One chunk per sentence, `seq` counting from 0; play them back to back in order |
| `metrics` | `stt_ms`, `first_token_ms`, `first_audio_ms`, `total_ms` | Timings for the turn, measured from the turn's start |
| `notice` | `code: no_speech \| empty_recording \| interrupted`, `message` | Informational outcome; not an error |
| `error` | `code: unauthorized \| rate_limited \| bad_request \| too_large \| stt_unavailable \| tts_unavailable \| agent_error \| handsfree_unavailable \| internal`, `message` | Something failed. `message` is safe to show the user |
| `handsfree` | `status: off \| armed \| listening`, `reason` | Hands-free state. `armed`: waiting for the wake word. `listening`: no wake word needed (after "Artemis." alone, `reason: wake`; or the follow-up window, `reason: follow_up`). Other reasons: `started`, `turn`, `timeout`, `stopped` |
| `approval.resolved` | `id`, `approved: boolean` | An approval was answered by voice; settle its card (don't send `approval.response`) |

The server always ends a turn with `state: idle`, whether it succeeds, fails or is interrupted.

---

## Typical voice turn

```
→ {"type":"hello","session_id":null,"token":"…"}
← {"type":"session","session_id":"985b…","protocol":1,"agent":"echo"}
← {"type":"state","value":"idle"}

→ {"type":"audio.start","format":"webm/opus"}
← {"type":"state","value":"listening"}
→ <binary webm chunk> × N
→ {"type":"audio.end"}
← {"type":"state","value":"transcribing"}
← {"type":"transcript.final","text":"Hello Artemis, what is the weather like today?"}
← {"type":"state","value":"thinking"}
← {"type":"agent.token","text":"I"} … {"type":"agent.token","text":" today?"}
← {"type":"agent.done","text":"I heard you say: Hello Artemis, what is the weather like today?"}
← {"type":"state","value":"speaking"}
← {"type":"tts.chunk","seq":0,"final":true,"format":"wav","bytes":179244}
← <binary WAV, 179244 bytes>
← {"type":"metrics","stt_ms":130,"first_token_ms":130,"first_audio_ms":876,"total_ms":883}
← {"type":"state","value":"idle"}
```

## Interrupt

```
→ {"type":"text.input","text":"tell me a long story"}
← transcript.final, state:thinking, agent.token…
→ {"type":"interrupt"}
← {"type":"notice","code":"interrupted","message":"Interrupted"}
← {"type":"state","value":"idle"}
```

Sending `audio.start` mid-turn (barge-in) cancels the turn the same way, without the notice. The client also stops local playback.

## Approval

```
← {"type":"agent.approval_request","id":"a1b2c3","action":"Run command","detail":"rm -rf build/"}
→ {"type":"approval.response","id":"a1b2c3","approved":false}
```

To try it without Hermes, set `ARTEMIS_ECHO_DEMO=true` and say or type something containing the word "demo".

---

## Hands-free

```
→ {"type":"handsfree.start","format":"pcm16","sample_rate":16000}
← {"type":"handsfree","status":"armed","reason":"started"}
→ (binary PCM frames, continuously)          user: "Artemis, what's two plus two?"
← {"type":"handsfree","status":"armed","reason":"turn"}
← {"type":"transcript.final","text":"what's 2 plus 2?"}   … normal turn …
→ {"type":"playback.idle"}                   reply finished playing
← {"type":"handsfree","status":"listening","reason":"follow_up"}
                                             user: "And times three?" (no wake word)
← … turn …
← {"type":"handsfree","status":"armed","reason":"timeout"}  8 s later, if nothing was said
```

The server segments the stream with Silero VAD, transcribes each segment with the local Whisper service, and acts only on segments that start with the wake word, except in the `listening` window. While Artemis is busy (a turn is running, or its reply is still playing: from the first `tts.chunk` until `playback.idle`), the wake word is required for questions ("Artemis, <question>" barges in), but a bare stop phrase ("stop", "enough", "be quiet") interrupts on its own. Stop phrases are never sent to the agent. With an approval card open, "approve"/"yes" or "deny"/"no" answers it, and the server sends `approval.resolved`. Segments are never stored, and text without the wake word is never logged. Details: `artemis/handsfree.py`.

---

## Authentication and limits

When `ARTEMIS_ACCESS_TOKEN` is set:

- The first message must be a `hello` carrying the token, sent within **10 s**. Anything else gets `error: unauthorized` and the socket is closed.
- The token is compared in constant time. It travels inside the encrypted WebSocket, never in the URL, so it doesn't appear in Caddy or uvicorn logs.

Close codes:

| Code | Meaning | Client behavior |
|---|---|---|
| `4401` | Missing or invalid token | Forget the stored token, show the token form, don't auto-reconnect |
| `4408` | No `hello` within 10 s | Reconnect |
| `1013` | Too many open connections (`ARTEMIS_MAX_CONNECTIONS`) | Retry after 10 s |

Each connection may start at most `ARTEMIS_TURNS_PER_MINUTE` turns per rolling minute. Turns over the limit get `error: rate_limited` and `state: idle`.

---

## History

- **Phase 5 (done):** multiple `tts.chunk` events per turn (one per sentence, increasing `seq`). Chunks may arrive before `agent.done`. `final: true` marks the last chunk when the server knows it at send time; if the reply ends right after a chunk was sent, no chunk carries it. The end of a turn's audio is always signalled by `state: idle`.
