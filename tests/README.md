# `tests/`: automated tests

Run everything with `./check.sh` (mypy for all three venvs, shell syntax, tsc, pytest, node tests). Individually:

```bash
venv/bin/python -m pytest tests/ -q     # backend
npm test                                # frontend
```

| File | Covers |
|---|---|
| `test_ws.py` | The `/ws` endpoint end to end with fake STT/TTS/agent: auth, limits, turns, streaming speech, interrupts, approvals, sessions (new conversation, restart), hands-free over the socket |
| `test_hermes.py` | `HermesAgent` against a fake Hermes Runs API (`httpx.MockTransport`): event mapping, approvals, clarify, stop, errors |
| `test_handsfree.py` | VAD segmenter (fake and real Silero), wake-word and approval matching, and the hands-free state machine (follow-ups, stop phrases, new conversation) |
| `test_speech.py` | `SentenceSplitter`: abbreviations, decimals, code fences, markdown |
| `test_logs.py` | JSON/text log format, session id via `log_context`, uvicorn routing |
| `test_language.py` | English/Spanish detection and accent folding used to pick the reply voice |
| `frontend.test.js` | Frontend modules in Node with a mock DOM: core state, playback queue, approvals, hands-free signals, new conversation |

## Notes

- **No real services needed:** the tests never call Whisper, Kokoro or Hermes. The real Silero test runs only if `models/silero/silero_vad.onnx` exists.
- **Test helpers:** `make_client()` builds an app with injectable fakes (`FakeStt`, `FakeTts`, `ScriptAgent`, `LoudnessVad`). `say()` feeds synthetic speech to the hands-free controller.
- **Live checks** (real models and Hermes) are done by hand when a phase changes; see the changelog in the main README for what was verified.
