"""
Per-connection voice loop.

The receiver loop (``VoiceConnection.run``) always keeps reading from the
socket, so it can react to ``interrupt`` or a new ``audio.start`` while a turn
is in progress. Each turn (STT -> agent -> TTS) runs as its own task that is
cancelled on interrupt or barge-in.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from fastapi import WebSocket

from artemis.agent import AgentBackend, AgentContext, Done, Token, ToolEnd, ToolStart
from artemis.protocol import (
    CLOSE_HELLO_TIMEOUT,
    CLOSE_UNAUTHORIZED,
    PROTOCOL_VERSION,
    ClientMessage,
    ErrorCode,
    HandsFreeStatus,
    PipelineState,
    ProtocolError,
    ServerMessage,
    TtsChunkMessage,
    parse_client_message,
)
from artemis.handsfree import HandsFree, HandsFreeConfig
from artemis.services import ServiceError
from artemis.speech import SentenceSplitter
from artemis.vad import VadModel
from artemis.session import Session, SessionStore

logger = logging.getLogger("artemis-voice")

APPROVAL_TIMEOUT_S = 120.0
HELLO_TIMEOUT_S = 10.0
RATE_WINDOW_S = 60.0


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, *, wav: bool = False) -> str: ...


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
    access_token: str = ""
    """Required in ``hello`` when non-empty."""
    turns_per_minute: int = 30
    vad: VadModel | None = None
    """Silero VAD for hands-free mode; None = hands-free unavailable."""
    handsfree: HandsFreeConfig = HandsFreeConfig()


def _ms_since(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


class SpeechPipeline:
    """Speaks a reply sentence by sentence while the agent is still writing it.

    ``feed`` takes streamed agent text; complete sentences go to a queue that a
    background task synthesizes **in order** and sends as ``tts.chunk`` + WAV.
    So sentence 1 is playing on the client while sentence 2 is being
    synthesized and sentence 3 is still being generated.

    If TTS fails, one ``tts_unavailable`` error is sent and the rest of the
    reply continues as text only.
    """

    def __init__(self, conn: VoiceConnection, started: float) -> None:
        self._conn = conn
        self._started = started
        self._splitter = SentenceSplitter()
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._input_done = False
        self._seq = 0
        self.first_audio_ms: int | None = None
        self.failed = False
        self._task = asyncio.create_task(self._worker())

    def feed(self, text: str) -> None:
        for sentence in self._splitter.feed(text):
            self._queue.put_nowait(sentence)

    async def finish(self) -> None:
        """No more text: speak what's left and wait until every chunk is sent."""
        # Synchronous up to the await, so the worker sees all of this at once.
        self._input_done = True
        for sentence in self._splitter.flush():
            self._queue.put_nowait(sentence)
        self._queue.put_nowait(None)
        await self._task

    async def cancel(self) -> None:
        if self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _worker(self) -> None:
        while (sentence := await self._queue.get()) is not None:
            if self.failed:
                continue
            try:
                wav = await self._conn.deps.tts.synthesize(sentence)
            except ServiceError as error:
                self.failed = True
                await self._conn.send_error("tts_unavailable", str(error))
                continue

            if self._seq == 0:
                self.first_audio_ms = _ms_since(self._started)
                await self._conn.send_state("speaking")
            # Only the end-of-input marker left in the queue: this is the last chunk.
            final = self._input_done and self._queue.qsize() == 1
            await self._conn.send_audio(
                {"type": "tts.chunk", "seq": self._seq, "final": final,
                 "format": "wav", "bytes": len(wav)},
                wav,
            )
            self._seq += 1


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
        self._turn_times: deque[float] = deque()
        self._closed = False
        self._handsfree: HandsFree | None = None
        # Reply audio is playing in the browser: set by the first tts.chunk, cleared
        # by playback.idle (or interrupt). Playback often outlasts the server's turn.
        self._client_playing = False
        # With no token configured, the connection is trusted immediately.
        self.authenticated = not deps.access_token

    # ---------------------------------------------- sending

    async def send(self, message: ServerMessage) -> None:
        async with self._send_lock:
            await self.ws.send_json(message)

    async def send_state(self, value: PipelineState) -> None:
        await self.send({"type": "state", "value": value})

    async def send_error(self, code: ErrorCode, message: str) -> None:
        await self.send({"type": "error", "code": code, "message": message})

    async def send_audio(self, meta: TtsChunkMessage, wav: bytes) -> None:
        self._client_playing = True
        # Held under one lock so no other event can slip between the two frames.
        async with self._send_lock:
            await self.ws.send_json(meta)
            await self.ws.send_bytes(wav)

    # ---------------------------------------------- receive loop

    async def close(self, code: int, reason: str) -> None:
        self._closed = True
        await self.ws.close(code=code, reason=reason)

    async def run(self) -> None:
        try:
            while not self._closed:
                if self.authenticated:
                    frame = await self.ws.receive()
                else:
                    try:
                        frame = await asyncio.wait_for(self.ws.receive(), HELLO_TIMEOUT_S)
                    except TimeoutError:
                        logger.warning("Client sent no hello within %.0fs", HELLO_TIMEOUT_S)
                        await self.close(CLOSE_HELLO_TIMEOUT, "hello timeout")
                        break

                if frame["type"] == "websocket.disconnect":
                    break

                if frame.get("bytes") is not None:
                    if not self.authenticated:
                        pass
                    elif self._recording is None and self._handsfree is not None:
                        self._handsfree.feed(frame["bytes"])  # hands-free PCM
                    else:
                        self._on_audio_chunk(frame["bytes"])  # push-to-talk WebM
                elif frame.get("text") is not None:
                    await self._on_text_frame(frame["text"])
        finally:
            await self._stop_handsfree()
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

        if not self.authenticated and message["type"] != "hello":
            await self._reject("Send hello with an access token first")
            return

        await self._dispatch(message)

    async def _reject(self, reason: str) -> None:
        logger.warning("Rejected unauthenticated client: %s", reason)
        await self.send_error("unauthorized", reason)
        await self.close(CLOSE_UNAUTHORIZED, "unauthorized")

    def _token_ok(self, token: str | None) -> bool:
        if not self.deps.access_token:
            return True
        if not token:
            return False
        return hmac.compare_digest(token.encode(), self.deps.access_token.encode())

    def _rate_limited(self) -> bool:
        """Sliding-window limit on turns per connection."""
        now = time.monotonic()
        while self._turn_times and now - self._turn_times[0] > RATE_WINDOW_S:
            self._turn_times.popleft()
        if len(self._turn_times) >= self.deps.turns_per_minute:
            return True
        self._turn_times.append(now)
        return False

    async def _dispatch(self, message: ClientMessage) -> None:
        match message["type"]:
            case "hello":
                if not self._token_ok(message.get("token")):
                    await self._reject("Invalid or missing access token")
                    return
                self.authenticated = True
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
                self._client_playing = False
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
                await self._start_turn(text=text)

            case "interrupt":
                self._recording = None
                self._client_playing = False
                if await self._cancel_turn():
                    await self.send(
                        {"type": "notice", "code": "interrupted", "message": "Interrupted"}
                    )
                await self.send_state("idle")

            case "approval.response":
                future = self._approvals.get(message["id"])
                if future is not None and not future.done():
                    future.set_result(message["approved"])

            case "handsfree.start":
                if self.deps.vad is None:
                    await self.send_error(
                        "handsfree_unavailable",
                        "Hands-free needs the Silero VAD model (see README, Models)",
                    )
                    return
                await self._stop_handsfree(notify=False)
                self._client_playing = False  # unknown until the next reply; don't trust a stale flag
                self._handsfree = HandsFree(self, self.deps.vad.stream(), self.deps.handsfree)
                await self.send_handsfree("armed", "started")
                logger.info("Hands-free on")

            case "handsfree.stop":
                await self._stop_handsfree()

            case "playback.idle":
                self._client_playing = False
                if self._handsfree is not None:
                    await self._handsfree.playback_finished()

    # ---------------------------------------------- hands-free (HandsFreeHost)

    async def _stop_handsfree(self, *, notify: bool = True) -> None:
        handsfree, self._handsfree = self._handsfree, None
        if handsfree is None:
            return
        await handsfree.close()
        logger.info("Hands-free off")
        if notify and not self._closed:
            try:
                await self.send_handsfree("off", "stopped")
            except Exception:  # socket already gone
                pass

    async def send_handsfree(self, status: HandsFreeStatus, reason: str) -> None:
        await self.send({"type": "handsfree", "status": status, "reason": reason})

    async def transcribe_segment(self, wav: bytes) -> str:
        try:
            return await self.deps.stt.transcribe(wav, wav=True)
        except ServiceError as error:
            logger.warning("Hands-free transcription failed: %s", error)
            return ""

    def turn_active(self) -> bool:
        running = self._turn is not None and not self._turn.done()
        # Only trust the playback flag while hands-free reports it (playback.idle).
        return running or (self._client_playing and self._handsfree is not None)

    def approval_pending(self) -> bool:
        return any(not future.done() for future in self._approvals.values())

    async def resolve_approval_by_voice(self, approved: bool) -> None:
        for approval_id, future in self._approvals.items():
            if not future.done():
                future.set_result(approved)
                await self.send(
                    {"type": "approval.resolved", "id": approval_id, "approved": approved}
                )
                return

    async def start_voice_turn(self, text: str, stt_ms: int) -> None:
        await self._cancel_turn()
        await self._start_turn(text=text, stt_ms=stt_ms)

    async def interrupt_turn(self) -> None:
        was_playing = self._client_playing
        self._client_playing = False
        cancelled = await self._cancel_turn()
        if cancelled or was_playing:
            # Also when the server's turn is long over: the browser is still talking.
            await self.send({"type": "notice", "code": "interrupted", "message": "Interrupted"})
        await self.send_state("idle")

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

        await self._start_turn(audio=bytes(recording))

    # ---------------------------------------------- turns

    def _session(self) -> Session:
        if self.session is None:
            self.session = self.deps.sessions.get_or_create(None)
        return self.session

    async def _start_turn(
        self, *, audio: bytes | None = None, text: str | None = None, stt_ms: int | None = None
    ) -> None:
        if self._rate_limited():
            await self.send_error(
                "rate_limited",
                f"Too many requests (limit {self.deps.turns_per_minute}/min); wait a moment",
            )
            await self.send_state("idle")
            return
        task = asyncio.create_task(self._run_turn(audio=audio, text=text, stt_ms=stt_ms))
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

    async def _run_turn(
        self, *, audio: bytes | None, text: str | None, stt_ms: int | None = None
    ) -> None:
        """One turn. ``stt_ms`` is passed in when the text came from hands-free STT."""
        started = time.perf_counter()
        first_token_ms: int | None = None
        first_audio_ms: int | None = None
        session = self._session()
        stage: ErrorCode = "internal"
        speech: SpeechPipeline | None = None

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
            speech = SpeechPipeline(self, started)
            reply = ""
            streamed = False

            # aclosing: on interrupt the agent's stream is closed right away, so a
            # remote agent (Hermes) is told to stop immediately, not at GC time.
            async with contextlib.aclosing(self.deps.agent.stream(ctx, text)) as events:
                async for event in events:
                    match event:
                        case Token(text=token):
                            if first_token_ms is None:
                                first_token_ms = _ms_since(started)
                            streamed = True
                            await self.send({"type": "agent.token", "text": token})
                            speech.feed(token)
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

            if not streamed:
                speech.feed(reply)  # agent that answers in one piece
            session.add("assistant", reply)
            await self.send({"type": "agent.done", "text": reply})
            await speech.finish()
            first_audio_ms = speech.first_audio_ms

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

        finally:
            # Interrupt / barge-in / failure: stop synthesizing the rest of the reply.
            if speech is not None:
                await speech.cancel()


def _log_task_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("Turn task crashed: %r", error)

