# `deploy/`: service definitions

| Path | Contents |
|---|---|
| `systemd/artemis-voice.target` | Groups the three services: start, stop and enable them together |
| `systemd/artemis-voice-stt.service.in` | Whisper STT (`venv-openvino`, Arc B50 GPU), port 8083 |
| `systemd/artemis-voice-tts.service.in` | Kokoro TTS (`venv-tts`, CPU), port 8084 |
| `systemd/artemis-voice-app.service.in` | Voice app (`venv`), port 8080, behind Caddy |

These are **templates**, so don't copy them by hand. `scripts/artemis-services.sh install` fills in `@BASE_DIR@` (this checkout) and `@PORT@` (from `.env`), writes them to `~/.config/systemd/user/`, enables them at boot, and starts them. Re-run it after editing a template, changing a port, or moving the folder.

## What the units do

- **`systemd --user`:** they run as you, with your GPU access, and need no root. Lingering is enabled for this user, so they start at boot without a login.
- **Restart on failure** after 3 s, up to 5 times in 5 minutes. Then systemd gives up; check `scripts/artemis-services.sh logs`.
- **Readiness:** `ExecStartPost` waits for `/health`, so `start` returns only once the models are loaded, and a service that never becomes healthy counts as failed.
- **Offline:** `HF_HUB_OFFLINE` and friends are set. Nothing reaches the internet.
- **Logs** go to the journal as JSON lines (`ARTEMIS_LOG_FORMAT=json`); journald handles rotation.
- **Hardening:** `NoNewPrivileges`, and `ProtectSystem=full` (`/usr`, `/etc` and `/boot` read-only).
- **Ordering:** the app starts after STT/TTS and the Hermes gateway (`hermes-gateway.service`, which belongs to Hermes, not to this repo), but doesn't require them: it reports missing services in `/health`.

Caddy (HTTPS for `artemis-v01`) is a system service configured outside this repo.
