"""
WebSocket protocol between the browser and the voice app.

Text frames carry JSON events (below). Binary frames carry audio only:
  * client -> server: WebM/Opus chunks between ``audio.start`` and ``audio.end``
  * server -> client: exactly one WAV frame right after each ``tts.chunk`` event

The frontend mirror of these types lives in ``static/js/types.js``;
the human-readable spec is ``docs/protocol.md``. Keep all three in sync.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

PROTOCOL_VERSION = 1

# WebSocket close codes (4000-4999 are application-defined).
CLOSE_UNAUTHORIZED = 4401
CLOSE_HELLO_TIMEOUT = 4408
CLOSE_TRY_AGAIN_LATER = 1013  # standard: server overloaded (too many connections)

PipelineState = Literal["idle", "listening", "transcribing", "thinking", "speaking"]

ErrorCode = Literal[
    "unauthorized",
    "rate_limited",
    "bad_request",
    "too_large",
    "stt_unavailable",
    "tts_unavailable",
    "agent_error",
    "handsfree_unavailable",
    "internal",
]

HandsFreeStatus = Literal["off", "armed", "listening"]
"""armed = waiting for the wake word; listening = no wake word needed right now."""

NoticeCode = Literal["no_speech", "empty_recording", "interrupted"]


# --------------------------------------------------
# Client -> server
# --------------------------------------------------


class HelloMessage(TypedDict):
    type: Literal["hello"]
    session_id: NotRequired[str | None]
    token: NotRequired[str | None]
    """Access token; required when the server has ``ARTEMIS_ACCESS_TOKEN`` set."""


class AudioStartMessage(TypedDict):
    type: Literal["audio.start"]
    format: Literal["webm/opus"]


class AudioEndMessage(TypedDict):
    type: Literal["audio.end"]


class TextInputMessage(TypedDict):
    type: Literal["text.input"]
    text: str


class InterruptMessage(TypedDict):
    type: Literal["interrupt"]


class ApprovalResponseMessage(TypedDict):
    type: Literal["approval.response"]
    id: str
    approved: bool


class HandsFreeStartMessage(TypedDict):
    """Start hands-free: binary frames are now 16 kHz mono PCM16 (little-endian)."""

    type: Literal["handsfree.start"]
    format: Literal["pcm16"]
    sample_rate: Literal[16000]


class HandsFreeStopMessage(TypedDict):
    type: Literal["handsfree.stop"]


class PlaybackIdleMessage(TypedDict):
    """The client finished playing Artemis's reply (opens the follow-up window)."""

    type: Literal["playback.idle"]


ClientMessage = (
    HelloMessage
    | AudioStartMessage
    | AudioEndMessage
    | TextInputMessage
    | InterruptMessage
    | ApprovalResponseMessage
    | HandsFreeStartMessage
    | HandsFreeStopMessage
    | PlaybackIdleMessage
)


# --------------------------------------------------
# Server -> client
# --------------------------------------------------


class SessionMessage(TypedDict):
    type: Literal["session"]
    session_id: str
    protocol: int
    agent: str


class StateMessage(TypedDict):
    type: Literal["state"]
    value: PipelineState


class TranscriptFinalMessage(TypedDict):
    type: Literal["transcript.final"]
    text: str


class AgentTokenMessage(TypedDict):
    type: Literal["agent.token"]
    text: str


class AgentToolStartMessage(TypedDict):
    type: Literal["agent.tool_start"]
    id: str
    name: str
    summary: str


class AgentToolEndMessage(TypedDict):
    type: Literal["agent.tool_end"]
    id: str
    ok: bool
    summary: str


class AgentApprovalRequestMessage(TypedDict):
    type: Literal["agent.approval_request"]
    id: str
    action: str
    detail: str


class AgentDoneMessage(TypedDict):
    type: Literal["agent.done"]
    text: str


class TtsChunkMessage(TypedDict):
    """Announces one binary WAV frame, which follows immediately."""

    type: Literal["tts.chunk"]
    seq: int
    final: bool
    format: Literal["wav"]
    bytes: int


class MetricsMessage(TypedDict):
    type: Literal["metrics"]
    stt_ms: int | None
    first_token_ms: int | None
    first_audio_ms: int | None
    total_ms: int


class NoticeMessage(TypedDict):
    """Informational outcome that is not an error (e.g. nothing was said)."""

    type: Literal["notice"]
    code: NoticeCode
    message: str


class ErrorMessage(TypedDict):
    type: Literal["error"]
    code: ErrorCode
    message: str


class HandsFreeMessage(TypedDict):
    type: Literal["handsfree"]
    status: HandsFreeStatus
    reason: str
    """Why it changed: started, wake, follow_up, timeout, turn, stopped, stop."""


class ApprovalResolvedMessage(TypedDict):
    """An approval was answered some other way (by voice): settle its card."""

    type: Literal["approval.resolved"]
    id: str
    approved: bool


ServerMessage = (
    SessionMessage
    | StateMessage
    | TranscriptFinalMessage
    | AgentTokenMessage
    | AgentToolStartMessage
    | AgentToolEndMessage
    | AgentApprovalRequestMessage
    | AgentDoneMessage
    | TtsChunkMessage
    | MetricsMessage
    | NoticeMessage
    | ErrorMessage
    | HandsFreeMessage
    | ApprovalResolvedMessage
)


# --------------------------------------------------
# Parsing
# --------------------------------------------------


class ProtocolError(ValueError):
    """A client message was malformed."""


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ProtocolError(f"'{key}' must be a string")
    return value


def parse_client_message(data: object) -> ClientMessage:
    """Validate decoded JSON from the client into a typed message."""
    if not isinstance(data, dict):
        raise ProtocolError("Message must be a JSON object")

    kind = data.get("type")

    match kind:
        case "hello":
            session_id = data.get("session_id")
            if session_id is not None and not isinstance(session_id, str):
                raise ProtocolError("'session_id' must be a string or null")
            token = data.get("token")
            if token is not None and not isinstance(token, str):
                raise ProtocolError("'token' must be a string or null")
            return {"type": "hello", "session_id": session_id, "token": token}

        case "audio.start":
            fmt = data.get("format", "webm/opus")
            if fmt != "webm/opus":
                raise ProtocolError(f"Unsupported audio format {fmt!r}")
            return {"type": "audio.start", "format": "webm/opus"}

        case "audio.end":
            return {"type": "audio.end"}

        case "text.input":
            return {"type": "text.input", "text": _require_str(data, "text")}

        case "interrupt":
            return {"type": "interrupt"}

        case "approval.response":
            approved = data.get("approved")
            if not isinstance(approved, bool):
                raise ProtocolError("'approved' must be a boolean")
            return {
                "type": "approval.response",
                "id": _require_str(data, "id"),
                "approved": approved,
            }

        case "handsfree.start":
            if data.get("format", "pcm16") != "pcm16" or data.get("sample_rate", 16000) != 16000:
                raise ProtocolError("Hands-free audio must be pcm16 at 16000 Hz")
            return {"type": "handsfree.start", "format": "pcm16", "sample_rate": 16000}

        case "handsfree.stop":
            return {"type": "handsfree.stop"}

        case "playback.idle":
            return {"type": "playback.idle"}

        case _:
            raise ProtocolError(f"Unknown message type {kind!r}")
