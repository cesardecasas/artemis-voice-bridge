// @ts-check
import { CONNECTION_RETRY_MS } from "./constants.js";
import { state } from "./state.js";
import { appendMessage, setAiVoiceState, setStatus, updateRecordButton } from "./ui.js";
import { playAudio } from "./audio.js";
import { stopRecording } from "./recorder.js";

/** @typedef {import("./types.js").ServerMessage} ServerMessage */

const READY_COLOR = "#00ff00";
const ERROR_COLOR = "#ff0000";

/** Return to the idle "Connected" state unless a new recording has started. */
function settleIdle() {
  if (!state.isRecording && !state.isStarting) {
    setAiVoiceState(false);
    setStatus("Connected", READY_COLOR);
  }
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
 * Handle the binary frame that follows a `response` message with audio.
 * @param {ArrayBuffer} data
 */
export async function handleAudioFrame(data) {
  if (!state.awaitingAudio) {
    console.warn("Unexpected binary frame from server; ignoring");
    return;
  }

  state.awaitingAudio = false;
  await playAudio(new Blob([data], { type: "audio/wav" }));
  settleIdle();
}

/** @param {ServerMessage} message */
export async function handleServerMessage(message) {
  switch (message.type) {
    case "transcription":
      appendMessage("user", `You: ${message.text}`);
      break;

    case "response":
      appendMessage("artemis", `Artemis: ${message.text}`);
      state.isProcessing = false;
      state.awaitingAudio = message.audio !== null;
      updateRecordButton();

      if (!state.awaitingAudio) {
        settleIdle();
      }
      break;

    case "status":
      setStatus(message.text);
      if (message.text === "No speech detected") {
        setAiVoiceState(false);
        state.isProcessing = false;
        updateRecordButton();
      }
      break;

    case "error":
      console.error("Artemis error:", message.text);
      setAiVoiceState(false);
      state.isProcessing = false;
      state.awaitingAudio = false;
      setStatus(`Error: ${message.text}`, ERROR_COLOR);
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
    setStatus("Connected", READY_COLOR);
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
    state.recordingRequested = false;
    state.stopRequested = false;

    if (state.isRecording && state.mediaRecorder) {
      stopRecording();
    }

    setAiVoiceState(false);
    setStatus("Disconnected. Reconnecting...", ERROR_COLOR);
    updateRecordButton();
    state.reconnectTimer = setTimeout(connect, CONNECTION_RETRY_MS);
  };
}
