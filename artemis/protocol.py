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

PipelineState = Literal["idle", "listening", "transcribing", "thinking", "speaking"]

ErrorCode = Literal[
    "bad_request",
    "too_large",
    "stt_unavailable",
    "tts_unavailable",
    "agent_error",
    "internal",
]

NoticeCode = Literal["no_speech", "empty_recording", "interrupted"]


# --------------------------------------------------
# Client -> server
# --------------------------------------------------


class HelloMessage(TypedDict):
    type: Literal["hello"]
    session_id: NotRequired[str | None]


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


ClientMessage = (
    HelloMessage
    | AudioStartMessage
    | AudioEndMessage
    | TextInputMessage
    | InterruptMessage
    | ApprovalResponseMessage
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
            return {"type": "hello", "session_id": session_id}

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

        case _:
            raise ProtocolError(f"Unknown message type {kind!r}")
