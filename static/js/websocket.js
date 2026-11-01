// @ts-check
import { COLORS, CONNECTION_RETRY_MS, SESSION_STORAGE_KEY } from "./constants.js";
import { state } from "./state.js";
import {
  abandonReply,
  appendMessage,
  appendReplyToken,
  finishReply,
  setAiVoiceState,
  setStatus,
  showApproval,
  showPipelineState,
  showToolEnd,
  showToolStart,
  updateRecordButton,
} from "./ui.js";
import { playAudio, stopPlayback } from "./audio.js";
import { stopRecording } from "./recorder.js";
import { sendMessage } from "./send.js";

/** @typedef {import("./types.js").ServerMessage} ServerMessage */

/** @returns {string | null} */
function loadSessionId() {
  try {
    return localStorage.getItem(SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** @param {string} sessionId */
function saveSessionId(sessionId) {
  try {
    localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
  } catch {
    // Storage unavailable (private mode); the session just won't survive reloads.
  }
}

/** Show "Connected" once nothing is happening locally or on the server. */
function settleIdle() {
  if (
    state.serverState === "idle" &&
    !state.isRecording &&
    !state.isStarting &&
    !state.awaitingAudio &&
    !state.currentAudio
  ) {
    setAiVoiceState(false);
    showPipelineState("idle");
  }
}

/** Interrupt whatever Artemis is doing: stop local audio and cancel the server turn. */
export function interrupt() {
  stopPlayback();
  state.awaitingAudio = false;
  abandonReply();
  sendMessage({ type: "interrupt" });
}

/**
 * Parse a text frame into a server message. Returns null for invalid input.
 * @param {string} raw
 * @returns {ServerMessage | null}
 */
export function parseServerMessage(raw) {
  try {
    const data = JSON.parse(raw);
    if (data && typeof data === "object" && typeof data.type === "string") {
      return /** @type {ServerMessage} */ (data);
    }
  } catch (error) {
    console.error("Invalid server message:", error);
  }
  return null;
}

/**
 * Handle the binary frame that follows a `tts.chunk` message.
 * @param {ArrayBuffer} data
 */
export async function handleAudioFrame(data) {
  if (!state.awaitingAudio) {
    console.warn("Unexpected binary frame from server; ignoring");
    return;
  }

  state.awaitingAudio = false;

  if (state.isRecording || state.isStarting) {
    return; // user is already talking again; drop the stale reply audio
  }

  await playAudio(new Blob([data], { type: "audio/wav" }));
  settleIdle();
}

/** @param {ServerMessage} message */
export async function handleServerMessage(message) {
  switch (message.type) {
    case "session":
      state.sessionId = message.session_id;
      saveSessionId(message.session_id);
      console.info(`Session ${message.session_id} (agent: ${message.agent}, protocol v${message.protocol})`);
      break;

    case "state":
      state.serverState = message.value;
      state.isProcessing = ["transcribing", "thinking", "speaking"].includes(message.value);
      if (message.value === "idle") {
        settleIdle();
      } else if (!state.isRecording || message.value === "listening") {
        showPipelineState(message.value);
      }
      updateRecordButton();
      break;

    case "transcript.final":
      appendMessage("user", `You: ${message.text}`);
      break;

    case "agent.token":
      appendReplyToken(message.text);
      break;

    case "agent.tool_start":
      showToolStart(message.id, message.summary);
      break;

    case "agent.tool_end":
      showToolEnd(message.id, message.ok, message.summary);
      break;

    case "agent.approval_request":
      showApproval(message.action, message.detail, (approved) => {
        sendMessage({ type: "approval.response", id: message.id, approved });
      });
      break;

    case "agent.done":
      finishReply(message.text);
      break;

    case "tts.chunk":
      state.awaitingAudio = true;
      break;

    case "metrics":
      state.lastMetrics = message;
      console.info(
        `Turn: STT ${message.stt_ms ?? "–"} ms, first token ${message.first_token_ms ?? "–"} ms, ` +
          `first audio ${message.first_audio_ms ?? "–"} ms, total ${message.total_ms} ms`,
      );
      break;

    case "notice":
      if (message.code === "interrupted") {
        abandonReply();
      }
      setStatus(message.message, COLORS.neutral);
      break;

    case "error":
      console.error(`Artemis error [${message.code}]:`, message.message);
      setAiVoiceState(false);
      state.awaitingAudio = false;
      abandonReply();
      setStatus(`Error: ${message.message}`, COLORS.error);
      updateRecordButton();
      break;

    default: {
      /** @type {never} */
      const unknown = message;
      console.warn("Unknown server message:", unknown);
    }
  }
}

export function connect() {
  clearTimeout(state.reconnectTimer);

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  const connection = new WebSocket(wsUrl);
  connection.binaryType = "arraybuffer";
  state.socket = connection;

  connection.onopen = () => {
    setAiVoiceState(false);
    sendMessage({ type: "hello", session_id: state.sessionId ?? loadSessionId() });
    updateRecordButton();
  };

  connection.onmessage = async (event) => {
    if (event.data instanceof ArrayBuffer) {
      await handleAudioFrame(event.data);
      return;
    }

    if (typeof event.data !== "string") {
      return;
    }

    const message = parseServerMessage(event.data);
    if (message) {
      await handleServerMessage(message);
    }
  };

  connection.onerror = (error) => {
    console.error("WebSocket error:", error);
  };

  connection.onclose = () => {
    if (connection !== state.socket) {
      return;
    }

    state.isProcessing = false;
    state.awaitingAudio = false;
    state.serverState = "idle";
    state.recordingRequested = false;
    state.stopRequested = false;
    abandonReply();

    if (state.isRecording && state.mediaRecorder) {
      stopRecording();
    }

    setAiVoiceState(false);
    setStatus("Disconnected. Reconnecting...", COLORS.error);
    updateRecordButton();
    state.reconnectTimer = setTimeout(connect, CONNECTION_RETRY_MS);
  };
}
