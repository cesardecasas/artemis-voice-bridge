"""
Typed configuration shared by every Artemis service.

Values come from environment variables, optionally loaded from a ``.env``
file in the project root. Real environment variables always win over the
file, so ``PORT=9000 ./start-artemis-voice.sh`` works as expected.

This module is deliberately stdlib-only: it is imported by three services
that run in three different virtualenvs (app, STT, TTS).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


def _load_env_file(path: Path) -> None:
    """Load simple ``KEY=VALUE`` lines into ``os.environ`` without overriding."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from error


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number, got {raw!r}") from error


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Main web app / WebSocket bridge (``main.py``)."""

    host: str
    port: int
    max_audio_bytes: int
    stt_url: str
    tts_url: str


@dataclass(frozen=True, slots=True)
class SttSettings:
    """Whisper speech-to-text service (``whisper_server.py``)."""

    host: str
    port: int
    model_dir: Path
    device: str
    max_audio_bytes: int


@dataclass(frozen=True, slots=True)
class TtsSettings:
    """Kokoro text-to-speech service (``tts_server.py``)."""

    host: str
    port: int
    model_path: Path
    voices_path: Path
    voice: str
    speed: float
    lang: str
    max_chars: int


@dataclass(frozen=True, slots=True)
class Settings:
    app: AppSettings
    stt: SttSettings
    tts: TtsSettings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build settings once per process."""
    _load_env_file(ENV_FILE)

    max_audio_bytes = _int("ARTEMIS_MAX_AUDIO_BYTES", 20 * 1024 * 1024)

    stt = SttSettings(
        host=_str("ARTEMIS_STT_HOST", "127.0.0.1"),
        port=_int("ARTEMIS_STT_PORT", 8083),
        model_dir=_path(
            "ARTEMIS_STT_MODEL_DIR",
            BASE_DIR / "models" / "whisper-large-v3-turbo-ready",
        ),
        device=_str("ARTEMIS_STT_DEVICE", "GPU.0"),
        max_audio_bytes=max_audio_bytes,
    )

    tts = TtsSettings(
        host=_str("ARTEMIS_TTS_HOST", "127.0.0.1"),
        port=_int("ARTEMIS_TTS_PORT", 8084),
        model_path=_path(
            "ARTEMIS_TTS_MODEL", BASE_DIR / "models" / "kokoro" / "kokoro-v1.0.onnx"
        ),
        voices_path=_path(
            "ARTEMIS_TTS_VOICES", BASE_DIR / "models" / "kokoro" / "voices-v1.0.bin"
        ),
        voice=_str("ARTEMIS_TTS_VOICE", "af_heart"),
        speed=_float("ARTEMIS_TTS_SPEED", 1.0),
        lang=_str("ARTEMIS_TTS_LANG", "en-us"),
        max_chars=_int("ARTEMIS_TTS_MAX_CHARS", 2000),
    )

    app = AppSettings(
        host=_str("ARTEMIS_APP_HOST", "0.0.0.0"),
        port=_int("ARTEMIS_APP_PORT", 8080),
        max_audio_bytes=max_audio_bytes,
        stt_url=_str("ARTEMIS_STT_URL", f"http://{stt.host}:{stt.port}"),
        tts_url=_str("ARTEMIS_TTS_URL", f"http://{tts.host}:{tts.port}"),
    )

    return Settings(app=app, stt=stt, tts=tts)
