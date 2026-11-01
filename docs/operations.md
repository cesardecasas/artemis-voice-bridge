# Operating Artemis

Day-to-day running: services, logs, upgrades, backups, troubleshooting. Setup from scratch is in the main [README](../README.md#setup).

## The services

| Unit | Port | Process | Notes |
|---|---|---|---|
| `artemis-voice-stt.service` | 8083 | `venv-openvino` · Whisper on the Arc B50 | Takes ~5–60 s to load the model onto the GPU |
| `artemis-voice-tts.service` | 8084 | `venv-tts` · Kokoro on CPU | |
| `artemis-voice-app.service` | 8080 | `venv` · web UI, `/ws`, agent bridge | Behind Caddy at `https://artemis-v01` |
| `artemis-voice.target` | — | groups the three | Enabled at boot |

Outside this repo, Artemis also depends on `hermes-gateway.service` (user unit, Hermes Runs API on :8642), vLLM with Gemma 4 on :8003, and Caddy (system service). The app keeps running if any of them is down and shows it in the status strip.

```bash
scripts/artemis-services.sh status                 # state + /health per service
scripts/artemis-services.sh restart app            # after editing app code, static files or .env
scripts/artemis-services.sh restart                # everything
./stop-artemis-voice.sh                            # stop all (they come back at boot)
```

**When to restart what:**

| You changed | Restart |
|---|---|
| `artemis/`, `main.py`, `.env` app settings | `restart app` |
| Frontend (`static/`) | nothing (reload the browser) |
| `whisper_server.py`, `ARTEMIS_STT_*` | `restart stt` |
| `tts_server.py`, `ARTEMIS_TTS_*` | `restart tts` |
| A port, or a unit template in `deploy/` | `scripts/artemis-services.sh install` |

## Logs

All services log JSON lines to the systemd journal. Journald rotates them (it caps the journal's disk use; `journalctl --user --disk-usage` shows the size).

```bash
scripts/artemis-services.sh logs app -f                       # follow
scripts/artemis-services.sh logs --since "1 hour ago"         # all three
scripts/artemis-services.sh logs app -n 500 | jq -r 'select(.level=="warning" or .level=="error") | "\(.ts) \(.msg)"'
scripts/artemis-services.sh logs app | jq -r 'select(.session_id=="<id>") | "\(.ts) \(.msg)"'   # one conversation
```

Each line has `ts`, `level`, `service` (`app`/`stt`/`tts`), `logger` and `msg`. App lines written for a connection also have `session_id`, which matches the HUD's session label and the Hermes session `artemis-<id>`.

**Never in the logs:** what you said, what Artemis answered, the access token, and the Hermes key. Hermes keeps its own transcripts in its sessions.

`logs/` in the project only holds output from the fallback mode (`./start-artemis-voice.sh` without the units installed).

## Upgrading

```bash
cd ~/artemis-voice-bridge
git pull
venv/bin/pip install -r requirements.txt                # if requirements*.txt changed:
venv-openvino/bin/pip install -r requirements-stt.txt   #   only the ones that changed
venv-tts/bin/pip install -r requirements-tts.txt
./check.sh                                              # must say "All checks passed."
scripts/artemis-services.sh install                     # picks up unit/port changes; restarts everything
```

To roll back: `git checkout <previous commit>` and run the same steps. Read the changelog in the README for new `.env` variables or models.

## Backups

| What | Where | How to restore |
|---|---|---|
| Configuration and secrets | `.env` (mode 600) | Keep a copy somewhere private. **It holds the access token and the Hermes key, so it never goes into git** |
| Code | GitHub (private repo) | `git clone` |
| Models | `models/` (~2 GB) | Re-download (README → Models; checksums listed) or copy from a backup |
| Conversations | Hermes (`~/.hermes`) | Part of Hermes's own backup |
| Virtualenvs | `venv*/` | Rebuild from `requirements*.txt` (README → Setup) |

## Troubleshooting

| Symptom | Check |
|---|---|
| HUD says **offline** / won't connect | `scripts/artemis-services.sh status`; `systemctl status caddy` |
| A service is `failed` | `scripts/artemis-services.sh logs stt -n 50`. After 5 starts in 5 min systemd stops retrying (`start-limit-hit`): fix the cause, then `scripts/artemis-services.sh restart`, which clears the limit (plain `systemctl restart` doesn't) |
| STT slow or failing after a driver update | `logs stt`: it should say `Loading Whisper on GPU.0`. Check `clinfo -l` lists the B50 |
| Agent **offline** in the status strip | `systemctl --user status hermes-gateway`; [`hermes.md`](hermes.md#troubleshooting) |
| Hands-free button missing | `models/silero/silero_vad.onnx` missing (`logs app` says "Hands-free unavailable") |
| "Address already in use" | Old background processes: `./stop-artemis-voice.sh --legacy-only`, then `restart` |
| Services don't start at boot | `loginctl show-user $USER -p Linger` must say `yes` (`sudo loginctl enable-linger $USER`) |

Back to fallback mode (no systemd): `scripts/artemis-services.sh uninstall`, then `./start-artemis-voice.sh`.
