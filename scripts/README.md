# `scripts/`: operational helpers

| Script | Use |
|---|---|
| `artemis-services.sh` | Manage the systemd services: `install`, `uninstall`, `start`/`stop`/`restart [stt\|tts\|app]`, `status`, `logs [stt\|tts\|app] [journalctl args]` |
| `wait-healthy.sh` | `wait-healthy.sh <port> [timeout]`: waits for `/health` to answer. Used by the units' `ExecStartPost` |
| `make_icons.py` | Regenerates `static/icons/` (needs Pillow: `venv-openvino/bin/python scripts/make_icons.py`) |

Examples:

```bash
scripts/artemis-services.sh status
scripts/artemis-services.sh restart app            # after changing app code or .env
scripts/artemis-services.sh logs app -f            # follow the app's JSON logs
scripts/artemis-services.sh logs -n 200 | jq -r 'select(.level != "info")'   # warnings and errors
```

`start-artemis-voice.sh` and `stop-artemis-voice.sh` in the project root call into these when the services are installed.
