"""
Artemis voice bridge: browser UI + WebSocket voice loop.

Flow for one turn:
    browser mic --(binary WebM/Opus chunks + END_MARKER)--> /ws
    /ws --> Whisper STT service (local, OpenVINO on the B50)
    /ws --> agent (temporary echo)
    /ws --> Kokoro TTS service (local, CPU)
    /ws --(JSON "response" event, then one binary WAV frame)--> browser

Nothing leaves the machine: STT and TTS are local services, and audio is
kept in memory (no temporary files on disk).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Literal, TypedDict

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from artemis.config import BASE_DIR, get_settings

# --------------------------------------------------
# Configuration
# --------------------------------------------------

settings = get_settings().app

STATIC_DIR = BASE_DIR / "static"

END_MARKER = b"\x00\x00\x00\x00"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artemis-voice")


# --------------------------------------------------
# WebSocket messages (server -> client)
# --------------------------------------------------


class AudioInfo(TypedDict):
    format: Literal["wav"]
    bytes: int


class StatusMessage(TypedDict):
    type: Literal["status"]
    text: str


class TranscriptionMessage(TypedDict):
    type: Literal["transcription"]
    text: str


class ResponseMessage(TypedDict):
    """Agent reply. When ``audio`` is set, one binary WAV frame follows."""

    type: Literal["response"]
    text: str
    audio: AudioInfo | None


class ErrorMessage(TypedDict):
    type: Literal["error"]
    text: str


ServerMessage = StatusMessage | TranscriptionMessage | ResponseMessage | ErrorMessage


class ServiceError(RuntimeError):
    """A local backing service (STT/TTS) failed; the message is user-facing."""


# --------------------------------------------------
# Shared HTTP client
# --------------------------------------------------


class _Clients:
    http: httpx.AsyncClient | None = None


clients = _Clients()


def http() -> httpx.AsyncClient:
    if clients.http is None:
        raise RuntimeError("HTTP client not initialized")
    return clients.http


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    clients.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
    try:
        yield
    finally:
        await clients.http.aclose()
        clients.http = None


app = FastAPI(title="Artemis Voice Bridge", lifespan=lifespan)


# --------------------------------------------------
# Local service calls
# --------------------------------------------------


UploadFiles = Mapping[str, tuple[str, bytes, str]]


async def _post(
    service: str,
    url: str,
    *,
    files: UploadFiles | None = None,
    json: object | None = None,
) -> httpx.Response:
    """POST to a local service, mapping transport errors to ``ServiceError``."""
    try:
        response = await http().post(url, files=files, json=json)
        response.raise_for_status()
        return response
    except httpx.ConnectError as error:
        logger.error("%s service unavailable at %s", service, url)
        raise ServiceError(f"{service} service is offline") from error
    except httpx.TimeoutException as error:
        logger.error("%s request timed out", service)
        raise ServiceError(f"{service} request timed out") from error
    except httpx.HTTPStatusError as error:
        logger.error(
            "%s returned HTTP %s: %s",
            service,
            error.response.status_code,
            error.response.text[:200],
        )
        raise ServiceError(f"{service} service returned an error") from error


async def transcribe_audio(audio: bytes) -> str:
    """Transcribe WebM/Opus bytes with the local Whisper service."""
    response = await _post(
        "Transcription",
        f"{settings.stt_url}/transcribe",
        files={"file": ("recording.webm", audio, "audio/webm")},
    )

    try:
        payload = response.json()
        text = payload.get("text", "")
    except (ValueError, AttributeError) as error:
        logger.exception("Invalid transcription response")
        raise ServiceError("Invalid response from transcription service") from error

    return str(text).strip()


async def synthesize_speech(text: str) -> bytes:
    """Render ``text`` to WAV bytes with the local Kokoro service."""
    response = await _post(
        "Speech",
        f"{settings.tts_url}/synthesize",
        json={"text": text},
    )
    return response.content


# --------------------------------------------------
# Agent (temporary echo until Hermes is connected)
# --------------------------------------------------


async def generate_reply(transcription: str) -> str:
    return f"I heard you say: {transcription}"


# --------------------------------------------------
# WebSocket voice endpoint
# --------------------------------------------------


async def send(websocket: WebSocket, message: ServerMessage) -> None:
    await websocket.send_json(message)


async def receive_recording(websocket: WebSocket) -> bytes:
    """Collect binary chunks until ``END_MARKER``. Raises if over the size limit."""
    buffer = bytearray()
    too_large = False

    while True:
        data = await websocket.receive_bytes()

        if data == END_MARKER:
            break

        if too_large:
            # Keep draining so leftover chunks don't leak into the next turn.
            continue

        buffer.extend(data)

        if len(buffer) > settings.max_audio_bytes:
            too_large = True
            buffer.clear()

    if too_large:
        limit_mb = settings.max_audio_bytes // (1024 * 1024)
        raise ValueError(f"Recording exceeds {limit_mb} MB limit")

    return bytes(buffer)


async def handle_turn(websocket: WebSocket, audio: bytes) -> None:
    """Run one voice turn: STT -> agent -> TTS -> client."""
    logger.info("Recording complete: %d bytes", len(audio))

    if not audio:
        await send(websocket, {"type": "error", "text": "Recording was empty"})
        return

    await send(websocket, {"type": "status", "text": "Transcribing..."})
    transcription = await transcribe_audio(audio)
    logger.info("Transcription: %s", transcription)

    if not transcription:
        await send(websocket, {"type": "status", "text": "No speech detected"})
        return

    await send(websocket, {"type": "transcription", "text": transcription})

    reply = await generate_reply(transcription)

    await send(websocket, {"type": "status", "text": "Generating speech..."})

    try:
        wav = await synthesize_speech(reply)
    except ServiceError:
        # Still deliver the text so the conversation is not lost.
        await send(websocket, {"type": "response", "text": reply, "audio": None})
        raise

    await send(
        websocket,
        {
            "type": "response",
            "text": reply,
            "audio": {"format": "wav", "bytes": len(wav)},
        },
    )
    await websocket.send_bytes(wav)
    logger.info("Response delivered (%d bytes of audio)", len(wav))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("Client connected")

    try:
        # Keep the WebSocket open for multiple recordings.
        while True:
            try:
                audio = await receive_recording(websocket)
                await handle_turn(websocket, audio)

            except WebSocketDisconnect:
                raise

            except ServiceError as error:
                await send(websocket, {"type": "error", "text": str(error)})

            except Exception as error:
                logger.exception("Processing error")
                try:
                    await send(websocket, {"type": "error", "text": str(error)})
                except Exception:
                    break

    except WebSocketDisconnect:
        logger.info("Client disconnected")

    except Exception:
        logger.exception("WebSocket failure")

    finally:
        logger.info("WebSocket handler finished")


# --------------------------------------------------
# Health
# --------------------------------------------------


class ServiceHealth(TypedDict):
    status: Literal["ready", "loading", "offline"]
    url: str


class HealthReport(TypedDict):
    status: Literal["ready", "degraded"]
    services: dict[str, ServiceHealth]


async def _service_health(url: str) -> ServiceHealth:
    try:
        response = await http().get(f"{url}/health", timeout=2.0)
        response.raise_for_status()
        status = response.json().get("status")
        return {"status": "ready" if status == "ready" else "loading", "url": url}
    except (httpx.HTTPError, ValueError, AttributeError):
        return {"status": "offline", "url": url}


@app.get("/health")
async def health() -> HealthReport:
    """App is up; also reports the local STT and TTS services."""
    services = {
        "stt": await _service_health(settings.stt_url),
        "tts": await _service_health(settings.tts_url),
    }
    all_ready = all(service["status"] == "ready" for service in services.values())
    return {"status": "ready" if all_ready else "degraded", "services": services}


# --------------------------------------------------
# Static files
# --------------------------------------------------

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


# --------------------------------------------------
# Application entry point
# --------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
