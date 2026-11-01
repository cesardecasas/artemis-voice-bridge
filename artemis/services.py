"""Clients for the local STT (Whisper) and TTS (Kokoro) services."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal, TypedDict

import httpx

logger = logging.getLogger("artemis-voice")

UploadFiles = Mapping[str, tuple[str, bytes, str]]

ServiceStatus = Literal["ready", "loading", "offline"]


class ServiceHealth(TypedDict):
    status: ServiceStatus
    url: str
    detail: str
    """Short human-readable description, e.g. model and device."""


class ServiceError(RuntimeError):
    """A local backing service failed. ``str(error)`` is safe to show users."""

    def __init__(self, message: str, *, service: Literal["stt", "tts"]) -> None:
        super().__init__(message)
        self.service = service


class _LocalService:
    service: Literal["stt", "tts"]
    label: str

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self.base_url = base_url.rstrip("/")

    async def _post(
        self,
        path: str,
        *,
        files: UploadFiles | None = None,
        json: object | None = None,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        try:
            response = await self._client.post(url, files=files, json=json)
            response.raise_for_status()
            return response
        except httpx.ConnectError as error:
            logger.error("%s service unavailable at %s", self.label, url)
            raise ServiceError(
                f"{self.label} service is offline", service=self.service
            ) from error
        except httpx.TimeoutException as error:
            logger.error("%s request timed out", self.label)
            raise ServiceError(
                f"{self.label} request timed out", service=self.service
            ) from error
        except httpx.HTTPStatusError as error:
            logger.error(
                "%s returned HTTP %s: %s",
                self.label,
                error.response.status_code,
                error.response.text[:200],
            )
            raise ServiceError(
                f"{self.label} service returned an error", service=self.service
            ) from error

    def _detail(self, payload: dict[str, object]) -> str:
        return str(payload.get("model", ""))

    async def health(self) -> ServiceHealth:
        try:
            response = await self._client.get(f"{self.base_url}/health", timeout=2.0)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("health payload is not an object")
        except (httpx.HTTPError, ValueError):
            return {"status": "offline", "url": self.base_url, "detail": ""}
        return {
            "status": "ready" if payload.get("status") == "ready" else "loading",
            "url": self.base_url,
            "detail": self._detail(payload),
        }


class SttClient(_LocalService):
    service = "stt"
    label = "Transcription"

    def _detail(self, payload: dict[str, object]) -> str:
        model = str(payload.get("model", "whisper")).removesuffix("-ready")
        return f"{model} · {payload.get('device', '?')}"

    async def transcribe(self, audio: bytes, *, wav: bool = False) -> str:
        """Transcribe WebM/Opus (push-to-talk) or WAV (hands-free) bytes.

        Returns "" when no speech was found.
        """
        upload = ("segment.wav", audio, "audio/wav") if wav else ("recording.webm", audio, "audio/webm")
        response = await self._post("/transcribe", files={"file": upload})
        try:
            text = response.json().get("text", "")
        except (ValueError, AttributeError) as error:
            raise ServiceError(
                "Invalid response from transcription service", service="stt"
            ) from error
        return str(text).strip()


class TtsClient(_LocalService):
    service = "tts"
    label = "Speech"

    def _detail(self, payload: dict[str, object]) -> str:
        detail = f"Kokoro · {payload.get('voice', '?')}"
        if payload.get("voice_es"):
            detail += f" · es {payload['voice_es']}"
        return detail

    async def synthesize(
        self, text: str, *, voice: str | None = None, lang: str | None = None
    ) -> bytes:
        """Render ``text`` to 16-bit mono WAV bytes (service defaults when None)."""
        body: dict[str, str] = {"text": text}
        if voice:
            body["voice"] = voice
        if lang:
            body["lang"] = lang
        response = await self._post("/synthesize", json=body)
        return response.content
