# `static/js/`: frontend modules

Entry point: `main.js` (loaded through `../app.js`). All modules are ES modules with strict JSDoc types.

| Module | Responsibility |
|---|---|
| `main.js` | Boot: binds buttons and keys (**Space** push-to-talk, **Esc** stop, **H** hands-free), restores per-device settings, connects |
| `websocket.js` | Connection lifecycle (reconnects, close codes, token prompt), and the dispatcher for every server message. Also `interrupt()` and `newConversation()` |
| `send.js` | `sendMessage()` (typed JSON) and `sendAudioChunk()` (binary) |
| `types.js` | JSDoc typedefs for the protocol. Mirrors `artemis/protocol.py`; keep them in sync |
| `state.js` | The single shared state object (connection, server state, recording, playback, hands-free) |
| `ui.js` | Rendering: core state and copy, conversation messages (streamed replies), session label, auth form |
| `activity.js` | Activity panel: tool cards, and approval cards with countdown. Cards can also be settled by voice (`settleApproval`) |
| `audio.js` | Reply playback (gapless Web Audio queue, interruptible), chimes, and level meters for the orb |
| `recorder.js` | Push-to-talk recording (MediaRecorder, WebM/Opus) |
| `handsfree.js` | Hands-free: opens the mic, streams 16 kHz PCM through the worklet, reacts to `handsfree` status, sends `playback.idle` |
| `pcm-worklet.js` | AudioWorklet on the audio thread: mic → 16 kHz PCM16 frames of 100 ms. Not type-checked (see `../README.md`) |
| `health.js` | Status strip: polls `/health` every 15 s, shows link state and last-turn metrics, and shows the hands-free button when the server has the VAD model |
| `storage.js` | `localStorage` helpers that never throw (private mode) |
| `constants.js` | Colors, timings and storage keys |

**Flow of a turn:** `recorder.js`/`handsfree.js` → `send.js` → *server* → `websocket.js` dispatch → `ui.js` (text) + `audio.js` (voice) + `activity.js` (tools).
