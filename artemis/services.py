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

    async def health(self) -> ServiceHealth:
        try:
            response = await self._client.get(f"{self.base_url}/health", timeout=2.0)
            response.raise_for_status()
            status = response.json().get("status")
        except (httpx.HTTPError, ValueError, AttributeError):
            return {"status": "offline", "url": self.base_url}
        return {
            "status": "ready" if status == "ready" else "loading",
            "url": self.base_url,
        }


class SttClient(_LocalService):
    service = "stt"
    label = "Transcription"

    async def transcribe(self, audio: bytes) -> str:
        """Transcribe WebM/Opus bytes. Returns "" when no speech was found."""
        response = await self._post(
            "/transcribe",
            files={"file": ("recording.webm", audio, "audio/webm")},
        )
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

    async def synthesize(self, text: str) -> bytes:
        """Render ``text`` to 16-bit mono WAV bytes."""
        response = await self._post("/synthesize", json={"text": text})
        return response.content
