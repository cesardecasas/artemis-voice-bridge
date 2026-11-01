"""
Per-connection voice loop.

The receiver loop (``VoiceConnection.run``) always keeps reading from the
socket, so it can react to ``interrupt`` or a new ``audio.start`` while a turn
is in progress. Each turn (STT -> agent -> TTS) runs as its own task that is
cancelled on interrupt or barge-in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Protocol

from fastapi import WebSocket

from artemis.agent import AgentBackend, AgentContext, Done, Token, ToolEnd, ToolStart
from artemis.protocol import (
    PROTOCOL_VERSION,
    ClientMessage,
    ErrorCode,
    PipelineState,
    ProtocolError,
    ServerMessage,
    TtsChunkMessage,
    parse_client_message,
)
from artemis.services import ServiceError
from artemis.session import Session, SessionStore

logger = logging.getLogger("artemis-voice")

APPROVAL_TIMEOUT_S = 120.0


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes) -> str: ...


class Synthesizer(Protocol):
    async def synthesize(self, text: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class VoiceDeps:
    """Everything a connection needs; tests pass fakes here."""

    stt: Transcriber
    tts: Synthesizer
    agent: AgentBackend
    sessions: SessionStore
    max_audio_bytes: int


def _ms_since(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


class VoiceConnection:
    def __init__(self, websocket: WebSocket, deps: VoiceDeps) -> None:
        self.ws = websocket
        self.deps = deps
        self.session: Session | None = None

        self._send_lock = asyncio.Lock()
        self._recording: bytearray | None = None
        self._recording_too_large = False
        self._turn: asyncio.Task[None] | None = None
        self._approvals: dict[str, asyncio.Future[bool]] = {}

    # ---------------------------------------------- sending

    async def send(self, message: ServerMessage) -> None:
        async with self._send_lock:
            await self.ws.send_json(message)

    async def send_state(self, value: PipelineState) -> None:
        await self.send({"type": "state", "value": value})

    async def send_error(self, code: ErrorCode, message: str) -> None:
        await self.send({"type": "error", "code": code, "message": message})

    async def send_audio(self, meta: TtsChunkMessage, wav: bytes) -> None:
        # Held under one lock so no other event can slip between the two frames.
        async with self._send_lock:
            await self.ws.send_json(meta)
            await self.ws.send_bytes(wav)

    # ---------------------------------------------- receive loop

    async def run(self) -> None:
        try:
            while True:
                frame = await self.ws.receive()

                if frame["type"] == "websocket.disconnect":
                    break

                if frame.get("bytes") is not None:
                    self._on_audio_chunk(frame["bytes"])
                elif frame.get("text") is not None:
                    await self._on_text_frame(frame["text"])
        finally:
            await self._cancel_turn()
            for future in self._approvals.values():
                future.cancel()
            self._approvals.clear()

    async def _on_text_frame(self, raw: str) -> None:
        try:
            message = parse_client_message(json.loads(raw))
        except (ValueError, ProtocolError) as error:
            logger.warning("Bad client message: %s", error)
            await self.send_error("bad_request", str(error))
            return

        await self._dispatch(message)

    async def _dispatch(self, message: ClientMessage) -> None:
        match message["type"]:
            case "hello":
                self.session = self.deps.sessions.get_or_create(message.get("session_id"))
                await self.send(
                    {
                        "type": "session",
                        "session_id": self.session.id,
                        "protocol": PROTOCOL_VERSION,
                        "agent": self.deps.agent.name,
                    }
                )
                await self.send_state("idle")

            case "audio.start":
                # Barge-in: speaking over Artemis cancels the current turn.
                await self._cancel_turn()
                self._recording = bytearray()
                self._recording_too_large = False
                await self.send_state("listening")

            case "audio.end":
                await self._finish_recording()

            case "text.input":
                text = message["text"].strip()
                if not text:
                    await self.send_error("bad_request", "Text is empty")
                    return
                await self._cancel_turn()
                self._start_turn(text=text)

            case "interrupt":
                self._recording = None
                if await self._cancel_turn():
                    await self.send(
                        {"type": "notice", "code": "interrupted", "message": "Interrupted"}
                    )
                await self.send_state("idle")

            case "approval.response":
                future = self._approvals.get(message["id"])
                if future is not None and not future.done():
                    future.set_result(message["approved"])

    def _on_audio_chunk(self, data: bytes) -> None:
        if self._recording is None:
            logger.debug("Ignoring %d audio bytes outside a recording", len(data))
            return
        if self._recording_too_large:
            return  # keep draining until audio.end

        self._recording.extend(data)
        if len(self._recording) > self.deps.max_audio_bytes:
            self._recording_too_large = True
            self._recording.clear()

    async def _finish_recording(self) -> None:
        recording, self._recording = self._recording, None

        if recording is None:
            await self.send_error("bad_request", "audio.end without audio.start")
            return

        if self._recording_too_large:
            limit_mb = self.deps.max_audio_bytes // (1024 * 1024)
            await self.send_error("too_large", f"Recording exceeds {limit_mb} MB limit")
            await self.send_state("idle")
            return

        logger.info("Recording complete: %d bytes", len(recording))

        if not recording:
            await self.send(
                {"type": "notice", "code": "empty_recording", "message": "Recording was empty"}
            )
            await self.send_state("idle")
            return

        self._start_turn(audio=bytes(recording))

    # ---------------------------------------------- turns

    def _session(self) -> Session:
        if self.session is None:
            self.session = self.deps.sessions.get_or_create(None)
        return self.session

    def _start_turn(self, *, audio: bytes | None = None, text: str | None = None) -> None:
        task = asyncio.create_task(self._run_turn(audio=audio, text=text))
        task.add_done_callback(_log_task_failure)
        self._turn = task

    async def _cancel_turn(self) -> bool:
        """Cancel the running turn. Returns True if one was running."""
        task, self._turn = self._turn, None
        if task is None or task.done():
            return False

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Turn failed while being cancelled")
        logger.info("Turn interrupted")
        return True

    async def _request_approval(self, action: str, detail: str) -> bool:
        approval_id = uuid.uuid4().hex[:12]
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._approvals[approval_id] = future

        await self.send(
            {
                "type": "agent.approval_request",
                "id": approval_id,
                "action": action,
                "detail": detail,
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_S)
        except TimeoutError:
            logger.info("Approval %s timed out; treating as denied", approval_id)
            return False
        finally:
            self._approvals.pop(approval_id, None)

    async def _run_turn(self, *, audio: bytes | None, text: str | None) -> None:
        started = time.perf_counter()
        stt_ms: int | None = None
        first_token_ms: int | None = None
        first_audio_ms: int | None = None
        session = self._session()
        stage: ErrorCode = "internal"

        try:
            if audio is not None:
                stage = "stt_unavailable"
                await self.send_state("transcribing")
                text = await self.deps.stt.transcribe(audio)
                stt_ms = _ms_since(started)
                logger.info("Transcription (%d ms): %s", stt_ms, text)

                if not text:
                    await self.send(
                        {"type": "notice", "code": "no_speech", "message": "No speech detected"}
                    )
                    await self.send_state("idle")
                    return

            assert text is not None
            await self.send({"type": "transcript.final", "text": text})
            session.add("user", text)

            stage = "agent_error"
            await self.send_state("thinking")
            ctx = AgentContext(session=session, request_approval=self._request_approval)
            reply = ""

            async for event in self.deps.agent.stream(ctx, text):
                match event:
                    case Token(text=token):
                        if first_token_ms is None:
                            first_token_ms = _ms_since(started)
                        await self.send({"type": "agent.token", "text": token})
                    case ToolStart(id=tool_id, name=name, summary=summary):
                        await self.send(
                            {"type": "agent.tool_start", "id": tool_id, "name": name, "summary": summary}
                        )
                    case ToolEnd(id=tool_id, ok=ok, summary=summary):
                        await self.send(
                            {"type": "agent.tool_end", "id": tool_id, "ok": ok, "summary": summary}
                        )
                    case Done(text=final):
                        reply = final

            session.add("assistant", reply)
            await self.send({"type": "agent.done", "text": reply})

            if reply.strip():
                stage = "tts_unavailable"
                await self.send_state("speaking")
                wav = await self.deps.tts.synthesize(reply)
                first_audio_ms = _ms_since(started)
                await self.send_audio(
                    {"type": "tts.chunk", "seq": 0, "final": True, "format": "wav", "bytes": len(wav)},
                    wav,
                )

            await self.send(
                {
                    "type": "metrics",
                    "stt_ms": stt_ms,
                    "first_token_ms": first_token_ms,
                    "first_audio_ms": first_audio_ms,
                    "total_ms": _ms_since(started),
                }
            )
            await self.send_state("idle")

        except asyncio.CancelledError:
            raise

        except ServiceError as error:
            code: ErrorCode = "stt_unavailable" if error.service == "stt" else "tts_unavailable"
            await self.send_error(code, str(error))
            await self.send_state("idle")

        except Exception as error:
            logger.exception("Turn failed at stage %s", stage)
            await self.send_error(stage if stage == "agent_error" else "internal", str(error))
            await self.send_state("idle")


def _log_task_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Turn task crashed: %r", error)

