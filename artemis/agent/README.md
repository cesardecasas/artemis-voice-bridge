# `artemis/agent/`: agent backends

Artemis is the voice and the HUD; the agent is the brain. Pick one with `ARTEMIS_AGENT_BACKEND`.

| File | Backend | Notes |
|---|---|---|
| `base.py` | — | The `AgentBackend` protocol: `stream(text, ctx)` yields `AgentEvent`s (tokens, tool start/end, done). `ctx.request_approval(action, detail)` asks the user and awaits the answer |
| `hermes.py` | `hermes` | The real one. Talks to the Hermes gateway's **Runs API** on `127.0.0.1:8642`: starts a run, follows its SSE events, answers approvals and clarifications, stops runs on interrupt. Full guide: [`docs/hermes.md`](../../docs/hermes.md) |
| `echo.py` | `echo` (default) | Repeats what it heard. Needs no Hermes. With `ARTEMIS_ECHO_DEMO=true`, a message containing "demo" fakes tool calls and an approval, for testing the HUD |
| `__init__.py` | — | `create_agent(settings, client)` builds the configured backend |

## Hermes in short

- **Sessions:** each Artemis session `<id>` is the Hermes session `artemis-<id>` (header `X-Hermes-Session-Key: artemis:voice:<id>`). Hermes keeps the history, so conversations persist across reconnects and restarts.
- **Voice instructions** are sent with every run (short spoken answers, no markdown). Override them with `ARTEMIS_HERMES_INSTRUCTIONS`.
- **Reasoning is off by default** (`ARTEMIS_HERMES_REASONING=off`): the first word arrives in about 1–4 s instead of 20–35 s.
- **Approvals** follow Hermes's `approvals.mode` (currently `smart`). When Hermes asks, the HUD shows an amber card that can also be answered by voice.
- **Secrets:** `ARTEMIS_HERMES_KEY` is sent only as a bearer header to localhost and is never logged.

## Adding a backend

Implement `AgentBackend` (`name`, `stream`, `health`), add it to `create_agent`, and test it with a fake transport the way `tests/test_hermes.py` does.
