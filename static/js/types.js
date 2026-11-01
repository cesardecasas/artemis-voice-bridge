// @ts-check
/**
 * Shared JSDoc types for the Artemis frontend: the WebSocket protocol.
 *
 * Mirrors `artemis/protocol.py`; the spec is `docs/protocol.md`. Keep all three
 * in sync. This file has no runtime code.
 */

/** @typedef {"idle" | "listening" | "transcribing" | "thinking" | "speaking"} PipelineState */
/** @typedef {"unauthorized" | "rate_limited" | "bad_request" | "too_large" | "stt_unavailable" | "tts_unavailable" | "agent_error" | "handsfree_unavailable" | "internal"} ErrorCode */
/** @typedef {"no_speech" | "empty_recording" | "interrupted"} NoticeCode */
/** armed = waiting for the wake word; listening = no wake word needed right now. */
/** @typedef {"off" | "armed" | "listening"} HandsFreeStatus */

// ---------------------------------------------------------------- client -> server

/** @typedef {{ type: "hello", session_id: string | null, token: string | null }} HelloMessage */
/** @typedef {{ type: "audio.start", format: "webm/opus" }} AudioStartMessage */
/** @typedef {{ type: "audio.end" }} AudioEndMessage */
/** @typedef {{ type: "text.input", text: string }} TextInputMessage */
/** @typedef {{ type: "interrupt" }} InterruptMessage */
/** @typedef {{ type: "approval.response", id: string, approved: boolean }} ApprovalResponseMessage */
/** Binary frames are now 16 kHz mono PCM16 (little-endian). */
/** @typedef {{ type: "handsfree.start", format: "pcm16", sample_rate: 16000 }} HandsFreeStartMessage */
/** @typedef {{ type: "handsfree.stop" }} HandsFreeStopMessage */
/** Artemis's reply finished playing (opens the follow-up window). */
/** @typedef {{ type: "playback.idle" }} PlaybackIdleMessage */

/**
 * @typedef {HelloMessage | AudioStartMessage | AudioEndMessage | TextInputMessage
 *   | InterruptMessage | ApprovalResponseMessage | HandsFreeStartMessage | HandsFreeStopMessage
 *   | PlaybackIdleMessage} ClientMessage
 */

// ---------------------------------------------------------------- server -> client

/** @typedef {{ type: "session", session_id: string, protocol: number, agent: string }} SessionMessage */
/** @typedef {{ type: "state", value: PipelineState }} StateMessage */
/** @typedef {{ type: "transcript.final", text: string }} TranscriptFinalMessage */
/** @typedef {{ type: "agent.token", text: string }} AgentTokenMessage */
/** @typedef {{ type: "agent.tool_start", id: string, name: string, summary: string }} AgentToolStartMessage */
/** @typedef {{ type: "agent.tool_end", id: string, ok: boolean, summary: string }} AgentToolEndMessage */
/** @typedef {{ type: "agent.approval_request", id: string, action: string, detail: string }} AgentApprovalRequestMessage */
/** @typedef {{ type: "agent.done", text: string }} AgentDoneMessage */
/**
 * Announces one binary WAV frame, which follows immediately.
 * @typedef {{ type: "tts.chunk", seq: number, final: boolean, format: "wav", bytes: number }} TtsChunkMessage
 */
/**
 * @typedef {{ type: "metrics", stt_ms: number | null, first_token_ms: number | null,
 *   first_audio_ms: number | null, total_ms: number }} MetricsMessage
 */
/** @typedef {{ type: "notice", code: NoticeCode, message: string }} NoticeMessage */
/** @typedef {{ type: "error", code: ErrorCode, message: string }} ErrorMessage */
/** @typedef {{ type: "handsfree", status: HandsFreeStatus, reason: string }} HandsFreeMessage */
/** An approval was answered another way (by voice): settle its card. */
/** @typedef {{ type: "approval.resolved", id: string, approved: boolean }} ApprovalResolvedMessage */

/**
 * @typedef {SessionMessage | StateMessage | TranscriptFinalMessage | AgentTokenMessage
 *   | AgentToolStartMessage | AgentToolEndMessage | AgentApprovalRequestMessage
 *   | AgentDoneMessage | TtsChunkMessage | MetricsMessage | NoticeMessage | ErrorMessage
 *   | HandsFreeMessage | ApprovalResolvedMessage} ServerMessage
 */

/** @typedef {"user" | "artemis" | "tool"} Speaker */

export {};
