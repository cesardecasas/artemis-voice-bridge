"""Protocol tests for the /ws voice loop, with fake STT/TTS/agent services."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from starlette.testclient import WebSocketTestSession

import artemis.ws
from artemis.agent import AgentContext, AgentEvent, Done, EchoAgent, Token
from artemis.config import get_settings
from artemis.services import ServiceError, ServiceStatus
from artemis.session import SessionStore
from artemis.ws import VoiceDeps
from main import create_app

WAV = b"RIFF\x00\x00\x00\x00WAVEfake"


class FakeStt:
    def __init__(self, text: str = "hello artemis", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.calls: list[bytes] = []

    async def transcribe(self, audio: bytes, *, wav: bool = False) -> str:
        self.calls.append(audio)
        if self.fail:
            raise ServiceError("Transcription service is offline", service="stt")
        return self.text


class FakeTts:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return WAV


class SlowAgent:
    """Emits one token, then blocks until cancelled."""

    name = "slow"

    def __init__(self) -> None:
        self.cancelled = False

    async def health(self) -> ServiceStatus:
        return "ready"

    async def stream(self, ctx: AgentContext, text: str) -> AsyncGenerator[AgentEvent, None]:
        yield Token("thinking...")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield Done("never")


def make_client(
    stt: FakeStt | None = None,
    tts: FakeTts | None = None,
    agent: Any = None,
    sessions: SessionStore | None = None,
    max_audio_bytes: int = 1024,
    access_token: str = "",
    turns_per_minute: int = 30,
    max_connections: int = 4,
    vad: Any = None,
) -> TestClient:
    deps = VoiceDeps(
        stt=stt or FakeStt(),
        tts=tts or FakeTts(),
        agent=agent or EchoAgent(),
        sessions=sessions if sessions is not None else SessionStore(),
        max_audio_bytes=max_audio_bytes,
        access_token=access_token,
        turns_per_minute=turns_per_minute,
        vad=vad,
    )
    base = get_settings()
    settings = dataclasses.replace(
        base, app=dataclasses.replace(base.app, max_connections=max_connections)
    )
    return TestClient(create_app(settings=settings, deps_override=lambda: deps))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with make_client() as test_client:
        yield test_client


def recv(ws: WebSocketTestSession) -> dict[str, Any] | bytes:
    message = ws.receive()
    if message.get("bytes") is not None:
        return bytes(message["bytes"])
    import json

    return dict(json.loads(message["text"]))


def recv_until(ws: WebSocketTestSession, *, state: str = "idle") -> list[dict[str, Any] | bytes]:
    """Collect frames until a given ``state`` event (inclusive)."""
    frames: list[dict[str, Any] | bytes] = []
    while True:
        frame = recv(ws)
        frames.append(frame)
        if isinstance(frame, dict) and frame == {"type": "state", "value": state}:
            return frames


def types(frames: list[dict[str, Any] | bytes]) -> list[str]:
    return [
        "<binary>" if isinstance(frame, bytes) else
        f"state:{frame['value']}" if frame["type"] == "state" else frame["type"]
        for frame in frames
    ]


def hello(
    ws: WebSocketTestSession, session_id: str | None = None, token: str | None = None
) -> str:
    ws.send_json({"type": "hello", "session_id": session_id, "token": token})
    session = recv(ws)
    assert isinstance(session, dict) and session["type"] == "session"
    assert session["protocol"] == 1
    assert recv(ws) == {"type": "state", "value": "idle"}
    return str(session["session_id"])


def send_recording(ws: WebSocketTestSession, *chunks: bytes) -> None:
    ws.send_json({"type": "audio.start", "format": "webm/opus"})
    assert recv(ws) == {"type": "state", "value": "listening"}
    for chunk in chunks:
        ws.send_bytes(chunk)
    ws.send_json({"type": "audio.end"})


# ---------------------------------------------------------------- tests


def test_full_voice_turn() -> None:
    stt, tts = FakeStt("hello artemis"), FakeTts()
    with make_client(stt=stt, tts=tts) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        send_recording(ws, b"abc", b"def")
        frames = recv_until(ws)

    assert types(frames) == [
        "state:transcribing",
        "transcript.final",
        "state:thinking",
        *["agent.token"] * 6,
        "agent.done",
        "state:speaking",
        "tts.chunk",
        "<binary>",
        "metrics",
        "state:idle",
    ]
    assert stt.calls == [b"abcdef"]
    tokens = "".join(f["text"] for f in frames if isinstance(f, dict) and f["type"] == "agent.token")
    assert tokens == "I heard you say: hello artemis"
    chunk = next(f for f in frames if isinstance(f, dict) and f["type"] == "tts.chunk")
    assert chunk == {"type": "tts.chunk", "seq": 0, "final": True, "format": "wav", "bytes": len(WAV)}
    assert frames[frames.index(chunk) + 1] == WAV
    assert tts.calls == ["I heard you say: hello artemis"]


def test_text_input_skips_stt() -> None:
    stt = FakeStt()
    with make_client(stt=stt) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text.input", "text": "  typed message "})
        frames = recv_until(ws)

    assert stt.calls == []
    assert frames[0] == {"type": "transcript.final", "text": "typed message"}
    metrics = next(f for f in frames if isinstance(f, dict) and f["type"] == "metrics")
    assert metrics["stt_ms"] is None


def test_no_speech_returns_notice() -> None:
    with make_client(stt=FakeStt("")) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        send_recording(ws, b"noise")
        frames = recv_until(ws)

    assert types(frames) == ["state:transcribing", "notice", "state:idle"]
    assert isinstance(frames[1], dict) and frames[1]["code"] == "no_speech"


def test_empty_recording(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        hello(ws)
        send_recording(ws)
        frames = recv_until(ws)

    assert types(frames) == ["notice", "state:idle"]
    assert isinstance(frames[0], dict) and frames[0]["code"] == "empty_recording"


def test_oversized_recording_is_drained_and_rejected() -> None:
    stt = FakeStt()
    with make_client(stt=stt, max_audio_bytes=10) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        send_recording(ws, b"x" * 8, b"x" * 8, b"x" * 8)
        frames = recv_until(ws)
        # The next recording must not contain leftovers from the rejected one.
        send_recording(ws, b"ok")
        recv_until(ws)

    assert isinstance(frames[0], dict) and frames[0]["code"] == "too_large"
    assert stt.calls == [b"ok"]


def test_stt_failure_reports_error() -> None:
    with make_client(stt=FakeStt(fail=True)) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        send_recording(ws, b"abc")
        frames = recv_until(ws)

    assert types(frames) == ["state:transcribing", "error", "state:idle"]
    assert isinstance(frames[1], dict) and frames[1]["code"] == "stt_unavailable"


@pytest.mark.parametrize(
    "payload",
    ['{"type": "bogus"}', "not json", '{"type": "text.input"}', '["list"]'],
)
def test_bad_messages_get_bad_request(client: TestClient, payload: str) -> None:
    with client.websocket_connect("/ws") as ws:
        ws.send_text(payload)
        frame = recv(ws)

    assert isinstance(frame, dict) and frame["type"] == "error"
    assert frame["code"] == "bad_request"


def test_interrupt_cancels_running_turn() -> None:
    agent = SlowAgent()
    with make_client(agent=agent) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text.input", "text": "tell me a long story"})
        assert recv(ws) == {"type": "transcript.final", "text": "tell me a long story"}
        assert recv(ws) == {"type": "state", "value": "thinking"}
        assert recv(ws) == {"type": "agent.token", "text": "thinking..."}

        ws.send_json({"type": "interrupt"})
        frames = recv_until(ws)

    assert types(frames) == ["notice", "state:idle"]
    assert isinstance(frames[0], dict) and frames[0]["code"] == "interrupted"
    assert agent.cancelled


def test_barge_in_audio_start_cancels_turn() -> None:
    agent = SlowAgent()
    with make_client(agent=agent) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text.input", "text": "go"})
        recv(ws), recv(ws), recv(ws)  # transcript, thinking, token
        ws.send_json({"type": "audio.start", "format": "webm/opus"})
        assert recv(ws) == {"type": "state", "value": "listening"}

    assert agent.cancelled


def test_approval_round_trip() -> None:
    with make_client(agent=EchoAgent(demo=True, token_delay=0.001)) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text.input", "text": "run the demo"})

        request: dict[str, Any] | None = None
        while request is None:
            frame = recv(ws)
            if isinstance(frame, dict) and frame["type"] == "agent.approval_request":
                request = frame

        ws.send_json({"type": "approval.response", "id": request["id"], "approved": True})
        frames = recv_until(ws)

    done = next(f for f in frames if isinstance(f, dict) and f["type"] == "agent.done")
    assert done["text"].startswith("I heard you say")
    tokens = "".join(f["text"] for f in frames if isinstance(f, dict) and f["type"] == "agent.token")
    assert tokens.startswith("(Demo command approved.)")


def test_session_survives_reconnect() -> None:
    sessions = SessionStore()
    with make_client(sessions=sessions) as client:
        with client.websocket_connect("/ws") as ws:
            session_id = hello(ws)
            ws.send_json({"type": "text.input", "text": "remember me"})
            recv_until(ws)

        with client.websocket_connect("/ws") as ws:
            assert hello(ws, session_id) == session_id

    session = sessions.get_or_create(session_id)
    assert [(turn.role, turn.content) for turn in session.history] == [
        ("user", "remember me"),
        ("assistant", "I heard you say: remember me"),
    ]


def test_health_reports_agent(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["agent"] == {"status": "ready", "backend": "echo"}


# ---------------------------------------------------------------- security

TOKEN = "s3cret-token"


def expect_close(ws: WebSocketTestSession) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        while True:
            ws.receive_json()
    return closed.value.code


@pytest.mark.parametrize("token", [None, "", "wrong-token"])
def test_bad_token_is_rejected(token: str | None) -> None:
    stt = FakeStt()
    with make_client(stt=stt, access_token=TOKEN) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "session_id": None, "token": token})
        error = recv(ws)
        code = expect_close(ws)

    assert isinstance(error, dict) and error["code"] == "unauthorized"
    assert code == 4401


def test_messages_before_hello_are_rejected_when_auth_enabled() -> None:
    stt = FakeStt()
    with make_client(stt=stt, access_token=TOKEN) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "text.input", "text": "sneaky"})
        error = recv(ws)
        code = expect_close(ws)

    assert isinstance(error, dict) and error["code"] == "unauthorized"
    assert code == 4401
    assert stt.calls == []


def test_correct_token_is_accepted() -> None:
    with make_client(access_token=TOKEN) as client, client.websocket_connect("/ws") as ws:
        hello(ws, token=TOKEN)
        ws.send_json({"type": "text.input", "text": "hi"})
        frames = recv_until(ws)

    assert "agent.done" in types(frames)


def test_hello_timeout_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artemis.ws, "HELLO_TIMEOUT_S", 0.05)
    with make_client(access_token=TOKEN) as client, client.websocket_connect("/ws") as ws:
        code = expect_close(ws)

    assert code == 4408


def test_turn_rate_limit() -> None:
    with make_client(turns_per_minute=2) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        for _ in range(2):
            ws.send_json({"type": "text.input", "text": "hi"})
            recv_until(ws)
        ws.send_json({"type": "text.input", "text": "one too many"})
        frames = recv_until(ws)

    assert types(frames) == ["error", "state:idle"]
    assert isinstance(frames[0], dict) and frames[0]["code"] == "rate_limited"


def test_connection_limit() -> None:
    with make_client(max_connections=1) as client, client.websocket_connect("/ws") as first:
        hello(first)
        with client.websocket_connect("/ws") as second:
            code = expect_close(second)
        # The first connection keeps working.
        first.send_json({"type": "text.input", "text": "still here"})
        assert "agent.done" in types(recv_until(first))

    assert code == 1013


# ---------------------------------------------------------------- Phase 5: streamed speech


class ScriptAgent:
    """Streams fixed tokens with a pause between them (a slow LLM)."""

    name = "script"

    def __init__(self, tokens: list[str], delay: float = 0.02) -> None:
        self.tokens = tokens
        self.delay = delay

    async def health(self) -> ServiceStatus:
        return "ready"

    async def stream(self, ctx: AgentContext, text: str) -> AsyncGenerator[AgentEvent, None]:
        for token in self.tokens:
            yield Token(token)
            await asyncio.sleep(self.delay)
        yield Done("".join(self.tokens))


class GatedTts(FakeTts):
    """First sentence synthesizes instantly; later ones block until cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        if len(self.calls) == 1:
            return WAV
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return WAV


class FailingTts(FakeTts):
    async def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        raise ServiceError("Speech service is offline", service="tts")


SENTENCES = ["First sentence here.", " Second one!", " And a third"]


def test_speech_starts_before_the_reply_is_finished() -> None:
    tts = FakeTts()
    agent = ScriptAgent(SENTENCES, delay=0.05)
    with make_client(tts=tts, agent=agent) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text.input", "text": "talk"})
        frames = recv_until(ws)

    names = types(frames)
    # Sentence 1 is already being spoken while tokens 2 and 3 are still arriving.
    assert names.index("tts.chunk") < names.index("agent.done")
    assert names.index("state:speaking") < names.index("agent.done")
    assert tts.calls == ["First sentence here.", "Second one!", "And a third"]

    chunks = [f for f in frames if isinstance(f, dict) and f["type"] == "tts.chunk"]
    assert [c["seq"] for c in chunks] == [0, 1, 2]
    assert [c["final"] for c in chunks] == [False, False, True]
    for chunk in chunks:  # every chunk is immediately followed by its WAV frame
        assert frames[frames.index(chunk) + 1] == WAV

    metrics = next(f for f in frames if isinstance(f, dict) and f["type"] == "metrics")
    assert metrics["first_audio_ms"] is not None
    assert metrics["first_audio_ms"] < metrics["total_ms"]


def test_interrupt_stops_remaining_speech() -> None:
    tts = GatedTts()
    agent = ScriptAgent(SENTENCES, delay=0.0)
    with make_client(tts=tts, agent=agent) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text.input", "text": "talk"})
        frames = recv_until(ws, state="speaking")
        while recv(ws) != WAV:  # first sentence's audio
            pass

        ws.send_json({"type": "interrupt"})
        rest = recv_until(ws)

    assert "tts.chunk" not in types(rest)
    assert types(rest)[-2:] == ["notice", "state:idle"]
    assert tts.cancelled
    assert "state:speaking" in types(frames)


def test_tts_failure_keeps_the_text_reply() -> None:
    tts = FailingTts()
    agent = ScriptAgent(SENTENCES, delay=0.0)
    with make_client(tts=tts, agent=agent) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "text.input", "text": "talk"})
        frames = recv_until(ws)

    names = types(frames)
    assert names.count("error") == 1, "one error, not one per sentence"
    assert "tts.chunk" not in names
    assert "agent.done" in names
    assert tts.calls == ["First sentence here."], "stops trying after the first failure"
    error = next(f for f in frames if isinstance(f, dict) and f["type"] == "error")
    assert error["code"] == "tts_unavailable"


# ---------------------------------------------------------------- Phase 7: hands-free


def test_handsfree_needs_the_vad_model(client: TestClient) -> None:
    with client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "handsfree.start", "format": "pcm16", "sample_rate": 16000})
        error = recv(ws)
    assert isinstance(error, dict) and error["code"] == "handsfree_unavailable"


def test_handsfree_wake_word_turn_over_the_socket() -> None:
    from tests.test_handsfree import UTTERANCE, LoudnessVad

    stt = FakeStt("Artemis, hello there.")
    with make_client(stt=stt, vad=LoudnessVad()) as client, client.websocket_connect("/ws") as ws:
        hello(ws)
        ws.send_json({"type": "handsfree.start", "format": "pcm16", "sample_rate": 16000})
        assert recv(ws) == {"type": "handsfree", "status": "armed", "reason": "started"}

        ws.send_bytes(UTTERANCE)  # raw PCM, no audio.start: hands-free
        frames = recv_until(ws)

        ws.send_json({"type": "handsfree.stop"})
        assert recv(ws) == {"type": "handsfree", "status": "off", "reason": "stopped"}

    assert frames[0] == {"type": "handsfree", "status": "armed", "reason": "turn"}
    assert {"type": "transcript.final", "text": "hello there."} in frames
    metrics = next(f for f in frames if isinstance(f, dict) and f["type"] == "metrics")
    assert metrics["stt_ms"] is not None, "hands-free STT time is reported"
    assert stt.calls and stt.calls[0][:4] == b"RIFF", "segments go to STT as WAV"
