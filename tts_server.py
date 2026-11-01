"""
Artemis local text-to-speech service (Kokoro-82M, ONNX Runtime on CPU).

Runs fully offline: the model and voice pack are read from ``models/kokoro/``,
and phonemization uses the espeak-ng library bundled with ``espeakng-loader``.

Endpoints:
    GET  /health      -> service status
    GET  /voices      -> available voice ids
    POST /synthesize  -> 16-bit mono WAV bytes for the given text
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from kokoro_onnx import Kokoro
from numpy.typing import NDArray
from pydantic import BaseModel, Field

from artemis.config import get_settings
from artemis.logs import configure_logging

configure_logging("tts")
logger = logging.getLogger("artemis-tts")

settings = get_settings().tts

WARMUP_TEXT = "Artemis online."
WARMUP_TEXT_ES = "Artemis en línea."


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=2.0)
    lang: str | None = Field(default=None, pattern=r"^[a-z]{2,3}(-[a-z]{2})?$")
    """Pronunciation (``en-us``, ``en-gb``, ``es``...); None = ``ARTEMIS_TTS_LANG``."""


class HealthResponse(BaseModel):
    status: Literal["ready", "loading"]
    model: str
    voice: str
    voice_es: str
    device: str


class VoicesResponse(BaseModel):
    default: str
    voices: list[str]


class _Engine:
    """Holds the loaded model. Kokoro inference is serialized with a lock."""

    def __init__(self) -> None:
        self.kokoro: Kokoro | None = None
        self.voices: frozenset[str] = frozenset()
        self.lock = asyncio.Lock()

    def load(self) -> None:
        for path in (settings.model_path, settings.voices_path):
            if not path.is_file():
                raise FileNotFoundError(
                    f"Missing TTS model file: {path} (see README, 'Models')"
                )

        started = time.perf_counter()
        self.kokoro = Kokoro(str(settings.model_path), str(settings.voices_path))
        self.voices = frozenset(self.kokoro.get_voices())

        for name, voice in (("ARTEMIS_TTS_VOICE", settings.voice), ("ARTEMIS_TTS_VOICE_ES", settings.voice_es)):
            if voice and voice not in self.voices:
                raise ValueError(f"{name}={voice!r} not found in voice pack")

        logger.info(
            "Kokoro loaded in %.2fs (%d voices, default %s)",
            time.perf_counter() - started,
            len(self.voices),
            settings.voice,
        )

    def synthesize(self, text: str, voice: str, speed: float, lang: str) -> bytes:
        if self.kokoro is None:
            raise RuntimeError("TTS model not loaded")

        started = time.perf_counter()
        samples: NDArray[np.float32]
        samples, sample_rate = self.kokoro.create(
            text, voice=voice, speed=speed, lang=lang
        )
        elapsed = time.perf_counter() - started
        duration = len(samples) / sample_rate if sample_rate else 0.0

        logger.info(
            "Synthesized %.2fs of audio in %.2fs (RTF %.2f, %s/%s)",
            duration,
            elapsed,
            elapsed / duration if duration else 0.0,
            voice,
            lang,
        )

        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format="WAV", subtype="PCM_16")
        return buffer.getvalue()


engine = _Engine()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("Loading Kokoro from %s", settings.model_path)
    await asyncio.to_thread(engine.load)

    # Warm up so the first real reply is not slowed by lazy initialization.
    await asyncio.to_thread(
        engine.synthesize, WARMUP_TEXT, settings.voice, settings.speed, settings.lang
    )
    if settings.voice_es:
        await asyncio.to_thread(
            engine.synthesize, WARMUP_TEXT_ES, settings.voice_es, settings.speed, "es"
        )
    logger.info("TTS service ready")

    yield

    engine.kokoro = None


app = FastAPI(title="Artemis TTS", lifespan=lifespan)


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ready" if engine.kokoro is not None else "loading",
        model=settings.model_path.name,
        voice=settings.voice,
        voice_es=settings.voice_es,
        device="CPU",
    )


@app.get("/voices")
async def voices() -> VoicesResponse:
    return VoicesResponse(default=settings.voice, voices=sorted(engine.voices))


@app.post(
    "/synthesize",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {}}}},
)
async def synthesize(request: SynthesizeRequest) -> Response:
    if engine.kokoro is None:
        raise HTTPException(status_code=503, detail="TTS model still loading")

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty")
    if len(text) > settings.max_chars:
        raise HTTPException(
            status_code=413,
            detail=f"Text exceeds {settings.max_chars} characters",
        )

    voice = request.voice or settings.voice
    if voice not in engine.voices:
        raise HTTPException(status_code=400, detail=f"Unknown voice {voice!r}")

    speed = request.speed if request.speed is not None else settings.speed
    lang = request.lang or settings.lang

    try:
        async with engine.lock:
            wav = await asyncio.to_thread(engine.synthesize, text, voice, speed, lang)
    except Exception as error:
        logger.exception("Synthesis failed")
        raise HTTPException(status_code=500, detail="Synthesis failed") from error

    return Response(content=wav, media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
