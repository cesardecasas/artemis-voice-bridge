"""
Run one Artemis service with the host/port from configuration.

    python -m artemis.serve app|stt|tts

Used by the systemd units and ``start-artemis-voice.sh``. Each service needs
its own virtualenv (see README); this module is stdlib + uvicorn only.
"""

from __future__ import annotations

import sys

import uvicorn

from artemis.config import get_settings

TARGETS = {"app": "main:app", "stt": "whisper_server:app", "tts": "tts_server:app"}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in TARGETS:
        print(f"usage: python -m artemis.serve {{{'|'.join(TARGETS)}}}", file=sys.stderr)
        return 2

    name = argv[0]
    settings = get_settings()
    host, port = {
        "app": (settings.app.host, settings.app.port),
        "stt": (settings.stt.host, settings.stt.port),
        "tts": (settings.tts.host, settings.tts.port),
    }[name]

    # log_config=None: the service configures logging itself (artemis.logs).
    # Proxy headers are trusted from 127.0.0.1 only (Caddy), uvicorn's default.
    uvicorn.run(TARGETS[name], host=host, port=port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
