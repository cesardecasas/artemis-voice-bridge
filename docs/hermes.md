# Hermes integration

Artemis talks to [Hermes Agent](https://github.com/NousResearch/hermes-agent) (installed in `~/.hermes/`, v0.22.5 at the time of writing) through the **Runs API** of the Hermes gateway's API server. `artemis/agent/hermes.py` implements it.

```
Artemis (VoiceConnection)          Hermes gateway (systemd: hermes-gateway.service)
  HermesAgent.stream()                API server, 127.0.0.1:8642
    POST /v1/runs  ───────────────►   new run in session "artemis-<session id>"
    GET  /v1/runs/{id}/events ◄────   SSE: message.delta, tool.*, approval.request, run.*
    POST /v1/runs/{id}/approval ──►   once | deny
    POST /v1/runs/{id}/stop ──────►   interrupt / barge-in
                                      LLM: gemma4-26b-a4b on local vLLM (127.0.0.1:8003)
```

Everything stays on this machine: Hermes's model provider is `custom` at `http://127.0.0.1:8003/v1` (vLLM, Gemma 4 26B int4), so the self-contained rule holds.

## Setup

1. Enable the API server in `~/.hermes/.env` (it binds to `127.0.0.1:8642` by default):

   ```bash
   API_SERVER_ENABLED=true
   API_SERVER_KEY=<a long random string>
   ```

2. Restart the gateway: `systemctl --user restart hermes-gateway.service`. Your messaging platforms (Discord) reconnect within a few seconds. Check with `curl http://127.0.0.1:8642/health`.

3. In Artemis's `.env`:

   ```bash
   ARTEMIS_AGENT_BACKEND=hermes
   ARTEMIS_HERMES_URL=http://127.0.0.1:8642
   ARTEMIS_HERMES_KEY=<same value as API_SERVER_KEY>
   ARTEMIS_HERMES_REASONING=off
   ```

4. Restart Artemis. The HUD's status strip shows **Agent: Hermes**, and `/health` reports `"agent": {"status": "ready", "backend": "hermes"}`.

## Event mapping

| Hermes SSE event | Artemis |
|---|---|
| `message.delta` | `Token` → `agent.token`, spoken sentence by sentence |
| `message.interim` (not `already_streamed`) | `Token`: commentary like "Let me check that." is spoken too |
| `tool.started` | `ToolStart` → tool card with the command preview (e.g. `df -h /`) |
| `tool.completed` | `ToolEnd` → ✓ or ✗ (`error` flag); `BLOCKED…` previews show "Not approved" |
| `approval.request` | `request_approval(description, command)` → amber card; answer posted as `once` or `deny` |
| `clarify.request` | Answered automatically: "make a reasonable assumption and say what you assumed" (voice can't answer free-text prompts yet) |
| `run.completed` | `Done(output)` |
| `run.failed` / `run.cancelled` / `run.interrupted` | `HermesError` → `error: agent_error` in the HUD |
| `reasoning.available`, `subagent.*`, others | Ignored |

Approval answers use the exact prompt identity when Hermes provides one (`{"request_id": prompt_id, "execution_generation": n, "choice": "once"}`, required by the canonical gateway), otherwise just `{"choice": ...}`. Artemis never sends `session` or `always`: each approval covers one command.

## Sessions and memory

- Each Artemis session maps to the Hermes session **`artemis-<session id>`**. Hermes keeps the conversation server-side, so Artemis sends only the new message.
- `X-Hermes-Session-Key: artemis:voice:<session id>` gives long-term memory a stable scope per conversation.
- Every run carries an `Idempotency-Key`, so a retried request can't start a second run.

## Voice instructions

Each run sends `instructions` that Hermes layers on top of its own system prompt (tools, memory and skills are unaffected): short spoken sentences, no tables or lists, and code and long output in fenced blocks. Artemis shows code blocks on screen but never speaks them (see `artemis/speech.py`). Override with `ARTEMIS_HERMES_INSTRUCTIONS`.

## Reasoning and latency

Hermes's global config sets `reasoning_effort: "high"`. Gemma 4 then reasons silently before answering, and in a voice interface that's dead air. Artemis sends a per-request `model_options` override that affects only its own runs; Discord and other platforms keep their setting.

Measured on this machine (five-sentence story, fresh session, straight against Hermes):

| `ARTEMIS_HERMES_REASONING` | First token | Total |
|---|---|---|
| `default` (Hermes config: high) | 27.3 s | 28.9 s |
| `high` | 35.2 s | 36.7 s |
| `low` | 31.5 s | 33.3 s |
| **`off`** (Artemis default) | **3.7 s** | **5.4 s** |

With Gemma 4, `low` barely helps: any effort level turns thinking on. Through Artemis with `off`, the story's first audio arrived after **1.7 s** (it took 21.6 s with Hermes's default). Short questions: first audio after about 0.9 s.

Use `high` if you'd rather wait for better answers on hard tasks.

## Approvals: what to expect

Hermes decides *when* to ask, through `approvals.mode` in `~/.hermes/config.yaml`:

- **`smart`** (Hermes default, and what this install uses): a helper model rates each dangerous command. Low-risk ones run without asking, clearly dangerous ones are refused, and only uncertain ones reach a human. In testing, `rm -rf` on a scratch folder in `/tmp` was allowed without a prompt.
- **`manual`**: every command matching a dangerous pattern asks for approval.
- **`off`**: never asks (YOLO).

This setting is global: it also applies to Discord. API-server sessions whose client can answer (Artemis can) do get approval prompts; `unattended_mode` only applies to clients that can't.

## Troubleshooting

| Symptom | Cause |
|---|---|
| "Hermes is not reachable. Is its API server enabled?" | `API_SERVER_ENABLED` missing, or the gateway wasn't restarted |
| "Hermes rejected the API key" | `ARTEMIS_HERMES_KEY` ≠ `API_SERVER_KEY` |
| Long silence before answers | Reasoning is on; set `ARTEMIS_HERMES_REASONING=off` |
| HUD shows Agent offline | `GET /health` on the Hermes API failed; check `systemctl --user status hermes-gateway` |

Logs: Artemis writes `Hermes run <id> started / stopped / approval: once|deny` to `logs/voice.log`, but never prompts, replies or the API key.
