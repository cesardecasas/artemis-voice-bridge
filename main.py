"""
Artemis voice bridge: browser UI + WebSocket voice loop.

    browser ──/ws (artemis.protocol)──► VoiceConnection (artemis.ws)
                                          ├─ SttClient ──► whisper_server.py :8083
                                          ├─ AgentBackend (echo now, Hermes later)
                                          └─ TtsClient ──► tts_server.py     :8084

Everything runs locally; audio is kept in memory only.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Literal, TypedDict

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from artemis.agent import create_agent
from artemis.config import BASE_DIR, Settings, get_settings
from artemis.protocol import CLOSE_TRY_AGAIN_LATER
from artemis.services import ServiceHealth, ServiceStatus, SttClient, TtsClient
from artemis.session import SessionStore
from artemis.ws import VoiceConnection, VoiceDeps

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("artemis-voice")

STATIC_DIR = BASE_DIR / "static"


class AgentHealth(TypedDict):
    status: ServiceStatus
    backend: str


class HealthReport(TypedDict):
    status: Literal["ready", "degraded"]
    services: dict[str, ServiceHealth]
    agent: AgentHealth
    sessions: int
    connections: int
    auth: bool


class _Runtime:
    """Live dependencies, created in the app lifespan."""

    def __init__(self) -> None:
        self.deps: VoiceDeps | None = None
        self.stt: SttClient | None = None
        self.tts: TtsClient | None = None
        self.connections = 0

    def require(self) -> VoiceDeps:
        if self.deps is None:
            raise RuntimeError("App not started")
        return self.deps


DepsOverride = Callable[[], VoiceDeps]


def create_app(
    settings: Settings | None = None, deps_override: DepsOverride | None = None
) -> FastAPI:
    """Build the app. Tests pass ``deps_override`` to swap in fake services."""
    cfg = settings or get_settings()
    runtime = _Runtime()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if deps_override is not None:
            runtime.deps = deps_override()
            yield
            return

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            runtime.stt = SttClient(client, cfg.app.stt_url)
            runtime.tts = TtsClient(client, cfg.app.tts_url)
            agent = create_agent(cfg.agent, client)
            runtime.deps = VoiceDeps(
                stt=runtime.stt,
                tts=runtime.tts,
                agent=agent,
                sessions=SessionStore(),
                max_audio_bytes=cfg.app.max_audio_bytes,
                access_token=cfg.app.access_token,
                turns_per_minute=cfg.app.turns_per_minute,
            )
            logger.info("Agent backend: %s", agent.name)
            if not cfg.app.access_token:
                logger.warning(
                    "ARTEMIS_ACCESS_TOKEN is not set: anyone who can reach this app can use it"
                )
            yield

        runtime.deps = None

    app = FastAPI(title="Artemis Voice Bridge", lifespan=lifespan)

    @app.get("/health")
    async def health() -> HealthReport:
        """App is up; also reports the local STT/TTS services and the agent."""
        deps = runtime.require()
        offline: ServiceHealth = {"status": "offline", "url": "", "detail": ""}
        services = {
            "stt": await runtime.stt.health() if runtime.stt else offline,
            "tts": await runtime.tts.health() if runtime.tts else offline,
        }
        agent: AgentHealth = {"status": await deps.agent.health(), "backend": deps.agent.name}
        all_ready = agent["status"] == "ready" and all(
            service["status"] == "ready" for service in services.values()
        )
        return {
            "status": "ready" if all_ready else "degraded",
            "services": services,
            "agent": agent,
            "sessions": len(deps.sessions),
            "connections": runtime.connections,
            "auth": bool(deps.access_token),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()

        if runtime.connections >= cfg.app.max_connections:
            logger.warning("Refusing connection: limit of %d reached", cfg.app.max_connections)
            await websocket.close(code=CLOSE_TRY_AGAIN_LATER, reason="too many connections")
            return

        runtime.connections += 1
        logger.info("Client connected (%d open)", runtime.connections)
        try:
            await VoiceConnection(websocket, runtime.require()).run()
        except Exception:
            logger.exception("WebSocket failure")
        finally:
            runtime.connections -= 1
            logger.info("Client disconnected (%d open)", runtime.connections)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()


if __name__ == "__main__":
    settings = get_settings().app
    uvicorn.run(app, host=settings.host, port=settings.port)
