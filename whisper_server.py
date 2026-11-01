"""
Artemis speech-to-text service: Whisper Large-v3-Turbo on OpenVINO.

Endpoints:
    GET  /health      -> service status
    POST /transcribe  -> {"text": "..."} for an uploaded WebM/Opus, MP3 or WAV file
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

import av
import av.audio.resampler
import numpy as np
import openvino_genai as ov_genai
from fastapi import FastAPI, File, HTTPException, UploadFile
from numpy.typing import NDArray
from pydantic import BaseModel

from artemis.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artemis-whisper")

settings = get_settings().stt

MODEL_DIR = settings.model_dir
DEVICE = settings.device
MAX_BYTES = settings.max_audio_bytes
SAMPLE_RATE = 16000

pipeline: ov_genai.WhisperPipeline | None = None
inference_lock = asyncio.Lock()


class HealthResponse(BaseModel):
    status: Literal["ready", "loading"]
    model: str
    device: str


class TranscribeResponse(BaseModel):
    text: str


def generation_options(max_new_tokens: int = 448) -> dict[str, Any]:
    """Whisper generation options built from settings.

    ``initial_prompt`` primes the decoder with vocabulary it should expect,
    which is how it learns to spell names like "Artemis" and "Hermes".
    """
    options: dict[str, Any] = {
        "max_new_tokens": max_new_tokens,
        "task": "transcribe",
    }
    if settings.language:
        options["language"] = f"<|{settings.language}|>"
    if settings.initial_prompt:
        options["initial_prompt"] = settings.initial_prompt
    return options


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global pipeline

    logger.info("Loading Whisper on %s", DEVICE)

    loaded = ov_genai.WhisperPipeline(
        str(MODEL_DIR),
        DEVICE
    )

    # Warm up: the first GPU inference compiles kernels and is much slower.
    started = time.perf_counter()
    await asyncio.to_thread(
        loaded.generate,
        [0.0] * SAMPLE_RATE,
        **generation_options(max_new_tokens=8),
    )
    logger.info("Warm-up done in %.2fs", time.perf_counter() - started)

    pipeline = loaded

    logger.info(
        "Whisper GPU service ready (language=%s, prompt=%r)",
        settings.language or "auto",
        settings.initial_prompt,
    )

    yield

    pipeline = None


app = FastAPI(title="Artemis STT", lifespan=lifespan)


@app.get("/health")
async def health() -> HealthResponse:
    return HealthResponse(
        status="ready" if pipeline is not None else "loading",
        model=MODEL_DIR.name,
        device=DEVICE,
    )


def decode_audio(data: bytes) -> NDArray[np.float32]:
    """
    Decode browser WebM/Opus recordings or MP3/WAV
    into 16 kHz mono float32 audio for Whisper.
    """

    samples: list[NDArray[Any]] = []

    with av.open(io.BytesIO(data), mode="r") as container:
        audio_stream = next(
            (s for s in container.streams if s.type == "audio"),
            None
        )

        if audio_stream is None:
            raise ValueError("No audio stream found")

        resampler = av.audio.resampler.AudioResampler(
            format="s16",
            layout="mono",
            rate=SAMPLE_RATE
        )

        for frame in container.decode(audio=audio_stream.index):
            for resampled in resampler.resample(frame):
                samples.append(
                    resampled.to_ndarray().reshape(-1).copy()
                )

        # Flush remaining resampler output.
        for resampled in resampler.resample(None):
            samples.append(
                resampled.to_ndarray().reshape(-1).copy()
            )

    if not samples:
        return np.array([], dtype=np.float32)

    audio = np.concatenate(samples)

    return audio.astype(np.float32) / 32768.0


def transcribe_bytes(data: bytes) -> str:
    if pipeline is None:
        raise RuntimeError("Whisper pipeline not loaded")

    audio = decode_audio(data)

    if len(audio) == 0:
        return ""

    duration = len(audio) / SAMPLE_RATE

    logger.info(
        "Decoded audio duration: %.2f seconds",
        duration
    )

    rms = float(np.sqrt(np.mean(audio ** 2)))

    logger.info("Audio RMS: %.5f", rms)

    # Basic silence detection.
    if rms < 0.001:
        logger.info("Silence detected")
        return ""

    result = pipeline.generate(audio.tolist(), **generation_options())

    text = str(result.texts[0] if hasattr(result, "texts") else result).strip()

    # On noise Whisper emits bare punctuation such as "." or "..."; treat that
    # as "no speech" so the client never shows an empty-looking turn.
    if not any(char.isalnum() for char in text):
        logger.info("No words in transcription output %r", text)
        return ""

    return text


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)) -> TranscribeResponse:
    data = await file.read(MAX_BYTES + 1)

    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio file too large"
        )

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Empty audio file"
        )

    try:
        async with inference_lock:
            text = await asyncio.to_thread(
                transcribe_bytes,
                data
            )

        return TranscribeResponse(text=text)

    except Exception as error:
        logger.exception("Transcription failed")
        raise HTTPException(
            status_code=500,
            detail="Transcription failed"
        ) from error


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
