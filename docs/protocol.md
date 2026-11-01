# Artemis WebSocket Protocol (v1)

The browser and the voice app talk over a single WebSocket at `/ws`.

- **Text frames** carry JSON events with a `type` field.
- **Binary frames** carry audio only:
  - client → server: WebM/Opus chunks, only between `audio.start` and `audio.end`
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

Malformed messages get `error` with code `bad_request`; the connection stays open.

## Server → client

| `type` | Fields | Meaning |
|---|---|---|
| `session` | `session_id`, `protocol` (=1), `agent` | Reply to `hello`. The client stores `session_id` |
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
| `error` | `code: unauthorized \| rate_limited \| bad_request \| too_large \| stt_unavailable \| tts_unavailable \| agent_error \| internal`, `message` | Something failed. `message` is safe to show the user |

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

## Planned (not yet implemented)

- **Phase 5 (done):** multiple `tts.chunk` events per turn (one per sentence, increasing `seq`). Chunks may arrive before `agent.done`. `final: true` marks the last chunk when the server knows it at send time; if the reply ends right after a chunk was sent, no chunk carries it. The end of a turn's audio is always signalled by `state: idle`.
