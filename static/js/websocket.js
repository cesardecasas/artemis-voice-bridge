// @ts-check
import {
  BUSY_RETRY_MS,
  CLOSE_CODES,
  COLORS,
  CONNECTION_RETRY_MS,
  SESSION_STORAGE_KEY,
  TOKEN_STORAGE_KEY,
} from "./constants.js";
import { state } from "./state.js";
import {
  abandonReply,
  appendMessage,
  appendReplyToken,
  finishReply,
  hideAuthForm,
  renderCore,
  setStatus,
  showAuthForm,
  showSession,
  updateRecordButton,
} from "./ui.js";
import { abandonTools, showApproval, showToolEnd, showToolStart } from "./activity.js";
import { chime, playAudio, stopPlayback } from "./audio.js";
import { agentDisplayName, refreshHealth, showLink, showMetrics } from "./health.js";
import { stopRecording } from "./recorder.js";
import { sendMessage } from "./send.js";
import { loadStored, store } from "./storage.js";

/** @typedef {import("./types.js").ServerMessage} ServerMessage */

/**
 * Device pairing: `https://artemis-v01/#token=XYZ` saves the token, then removes it
 * from the address bar. The URL fragment is never sent to the server, so the token
 * doesn't end up in proxy or server logs.
 */
export function consumeTokenFromUrl() {
  const match = window.location.hash.match(/(?:^#|&)token=([^&]+)/);
  if (!match) {
    return;
  }
  store(TOKEN_STORAGE_KEY, decodeURIComponent(match[1]));
  history.replaceState(null, "", window.location.pathname + window.location.search);
}

/** Ask for the access token, then reconnect with it. */
function requestToken() {
  showAuthForm((token) => {
    store(TOKEN_STORAGE_KEY, token);
    hideAuthForm();
    connect();
  });
}

/** Interrupt whatever Artemis is doing: stop local audio and cancel the server turn. */
export function interrupt() {
  stopPlayback();
  state.awaitingAudio = false;
  abandonReply();
  abandonTools();
  sendMessage({ type: "interrupt" });
  renderCore();
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
    renderCore();
    return; // user is already talking again; drop the stale reply audio
  }

  await playAudio(new Blob([data], { type: "audio/wav" }));
  renderCore();
}

/** @param {ServerMessage} message */
export async function handleServerMessage(message) {
  switch (message.type) {
    case "session":
      state.sessionId = message.session_id;
      state.connected = true;
      state.agentName = message.agent;
      store(SESSION_STORAGE_KEY, message.session_id);
      hideAuthForm();
      showSession(message.session_id);
      showLink("ready", "wss · secured");
      document.getElementById("agent-label")?.replaceChildren(agentDisplayName(message.agent));
      document.body?.classList.remove("booting");
      updateRecordButton();
      break;

    case "state":
      state.serverState = message.value;
      state.isProcessing = ["transcribing", "thinking", "speaking"].includes(message.value);
      renderCore();
      break;

    case "transcript.final":
      appendMessage("user", message.text);
      break;

    case "agent.token":
      appendReplyToken(message.text);
      break;

    case "agent.tool_start":
      showToolStart(message.id, message.name, message.summary);
      break;

    case "agent.tool_end":
      showToolEnd(message.id, message.ok, message.summary);
      break;

    case "agent.approval_request":
      void chime("start");
      showApproval(message.action, message.detail, (approved) => {
        sendMessage({ type: "approval.response", id: message.id, approved });
      });
      break;

    case "agent.done":
      finishReply(message.text);
      break;

    case "tts.chunk":
      // The binary WAV frame always follows; playAudio skips it when muted.
      state.awaitingAudio = true;
      renderCore();
      break;

    case "metrics":
      state.lastMetrics = message;
      showMetrics(message);
      break;

    case "notice":
      if (message.code === "interrupted") {
        abandonReply();
        abandonTools();
      }
      setStatus(message.message, COLORS.neutral);
      break;

    case "error":
      console.error(`Artemis error [${message.code}]:`, message.message);
      state.awaitingAudio = false;
      abandonReply();
      abandonTools();
      void chime("error");
      renderCore();
      setStatus(message.message, COLORS.error);
      if (message.code === "tts_unavailable" || message.code === "stt_unavailable") {
        void refreshHealth();
      }
      break;

    default: {
      /** @type {never} */
      const unknown = message;
      console.warn("Unknown server message:", unknown);
    }
  }
}

function markDisconnected() {
  state.connected = false;
  state.isProcessing = false;
  state.awaitingAudio = false;
  state.serverState = "idle";
  state.recordingRequested = false;
  state.stopRequested = false;
  abandonReply();
  abandonTools();
  stopPlayback();
  if (state.isRecording && state.mediaRecorder) {
    stopRecording();
  }
  updateRecordButton();
}

export function connect() {
  clearTimeout(state.reconnectTimer);

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${protocol}//${window.location.host}/ws`;

  showLink("loading", "connecting");
  const connection = new WebSocket(wsUrl);
  connection.binaryType = "arraybuffer";
  state.socket = connection;

  connection.onopen = () => {
    sendMessage({
      type: "hello",
      session_id: state.sessionId ?? loadStored(SESSION_STORAGE_KEY),
      token: loadStored(TOKEN_STORAGE_KEY),
    });
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

  connection.onclose = (event) => {
    if (connection !== state.socket) {
      return;
    }
    markDisconnected();

    if (event.code === CLOSE_CODES.unauthorized) {
      // Wrong or missing token: don't hammer the server, ask the user.
      store(TOKEN_STORAGE_KEY, null);
      showLink("offline", "token required");
      setStatus("Access token required", COLORS.amber);
      requestToken();
      return;
    }

    if (event.code === CLOSE_CODES.tryAgainLater) {
      showLink("offline", "too many connections");
      setStatus("Too many open connections. Retrying…", COLORS.error);
      state.reconnectTimer = setTimeout(connect, BUSY_RETRY_MS);
      return;
    }

    showLink("offline", "reconnecting");
    state.reconnectTimer = setTimeout(connect, CONNECTION_RETRY_MS);
  };
}
