// @ts-check
import { COLORS } from "./constants.js";
import { state } from "./state.js";

/** @typedef {import("./types.js").PipelineState} PipelineState */
/** @typedef {PipelineState | "offline"} CoreState */
/** @typedef {"user" | "artemis"} Speaker */

/**
 * Cached DOM references (looked up lazily so tests can swap the DOM).
 * @type {{
 *   recordBtn: HTMLButtonElement | null,
 *   core: HTMLElement | null,
 *   title: HTMLElement | null,
 *   display: HTMLElement | null,
 *   status: HTMLElement | null,
 *   visualizer: HTMLElement | null,
 *   streamingReply: { article: HTMLElement, text: HTMLElement } | null,
 *   renderedState: CoreState | null,
 * }}
 */
export const ui = {
  recordBtn: null,
  core: null,
  title: null,
  display: null,
  status: null,
  visualizer: null,
  streamingReply: null,
  renderedState: null,
};

/** Title and default hint shown under the core for each state. */
export const CORE_COPY = /** @type {Record<CoreState, [string, string]>} */ ({
  idle: ["Standing by", "Tap the core or hold Space to talk"],
  listening: ["Listening", "Tap the core or release Space to send"],
  transcribing: ["Transcribing", "Whisper on the Arc B50"],
  thinking: ["Thinking", "Working on it"],
  speaking: ["Speaking", "Tap the core or press Esc to interrupt"],
  offline: ["Offline", "Reconnecting…"],
});

/**
 * @param {string} id
 * @returns {HTMLElement}
 */
export function requireElement(id) {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing #${id} element`);
  }
  return element;
}

/** @returns {HTMLButtonElement} */
function recordBtn() {
  ui.recordBtn ??= /** @type {HTMLButtonElement} */ (requireElement("record-btn"));
  return ui.recordBtn;
}

/** @returns {HTMLElement} */
export function coreEl() {
  ui.core ??= requireElement("core");
  return ui.core;
}

/** @returns {HTMLElement} */
function titleEl() {
  ui.title ??= requireElement("state-title");
  return ui.title;
}

/** @returns {HTMLElement} */
function display() {
  ui.display ??= requireElement("display");
  return ui.display;
}

/** @returns {HTMLElement} */
function statusEl() {
  ui.status ??= requireElement("status");
  return ui.status;
}

/** @returns {HTMLElement} */
export function visualizerEl() {
  ui.visualizer ??= requireElement("visualizer");
  return ui.visualizer;
}

/**
 * What the core should show. Local activity (recording, playback) wins over
 * the server's pipeline state, because it is what the user is experiencing.
 * @returns {CoreState}
 */
export function effectiveState() {
  if (!state.connected) return "offline";
  if (state.isRecording || state.isStarting) return "listening";
  if (state.currentAudio || state.awaitingAudio) return "speaking";
  return state.serverState;
}

/**
 * Show a message in the hint line under the core. It stays until the core
 * changes state or another message replaces it.
 * @param {string} message
 * @param {string} [color]
 */
export function setStatus(message, color = COLORS.neutral) {
  const element = statusEl();
  element.innerText = message;
  element.style.color = color;
}

/** Re-render the core (state class, title, default hint) from current state. */
export function renderCore() {
  const value = effectiveState();
  if (value === ui.renderedState) {
    return;
  }
  ui.renderedState = value;

  const [title, hint] = CORE_COPY[value];
  coreEl().className = `core is-${value}`;
  titleEl().innerText = title;
  setStatus(hint);
}

export function updateRecordButton() {
  const button = recordBtn();
  const active = state.isRecording || state.isStarting;

  button.disabled = state.isStopping || !state.connected;
  button.setAttribute("aria-label", active ? "Stop recording and send" : "Start recording");
  button.setAttribute("aria-pressed", active ? "true" : "false");
  renderCore();
}

// ------------------------------------------------------------- conversation

function timeNow() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * Append a conversation message.
 * @param {Speaker} speaker
 * @param {string} text
 * @returns {{ article: HTMLElement, text: HTMLElement }}
 */
export function appendMessage(speaker, text) {
  const article = document.createElement("article");
  article.className = `msg msg-${speaker}`;

  const role = document.createElement("div");
  role.className = "msg-role";
  const who = document.createElement("span");
  who.innerText = speaker === "user" ? "You" : "Artemis";
  const time = document.createElement("time");
  time.innerText = timeNow();
  role.append(who, time);

  const body = document.createElement("p");
  body.className = "msg-text";
  body.innerText = text;

  article.append(role, body);
  const container = display();
  container.appendChild(article);
  container.scrollTop = container.scrollHeight;
  return { article, text: body };
}

/**
 * Append a streamed token to the in-progress Artemis reply (creating it if needed).
 * @param {string} token
 */
export function appendReplyToken(token) {
  if (!ui.streamingReply) {
    ui.streamingReply = appendMessage("artemis", "");
    ui.streamingReply.article.classList.add("msg-streaming");
  }
  ui.streamingReply.text.innerText += token;
  const container = display();
  container.scrollTop = container.scrollHeight;
}

/**
 * Replace the streamed reply with the authoritative final text.
 * @param {string} text
 */
export function finishReply(text) {
  if (ui.streamingReply) {
    ui.streamingReply.text.innerText = text;
    ui.streamingReply.article.classList.remove("msg-streaming");
  } else if (text) {
    appendMessage("artemis", text);
  }
  ui.streamingReply = null;
}

/** Mark a partially streamed reply as cut off (interrupt/error). */
export function abandonReply() {
  if (ui.streamingReply) {
    ui.streamingReply.article.classList.remove("msg-streaming");
    ui.streamingReply.article.classList.add("msg-interrupted");
  }
  ui.streamingReply = null;
}

// ------------------------------------------------------------- header bits

/** @param {string} sessionId */
export function showSession(sessionId) {
  const label = document.getElementById("session-label");
  if (label) {
    label.innerText = `session ${sessionId.slice(0, 4)}·${sessionId.slice(4, 8)} · local`;
  }
}

/** @param {boolean} muted */
export function showMuted(muted) {
  const button = document.getElementById("mute-btn");
  if (button) {
    button.setAttribute("aria-pressed", muted ? "true" : "false");
    button.setAttribute("aria-label", muted ? "Unmute Artemis's voice" : "Mute Artemis's voice");
  }
}

// ------------------------------------------------------------- pairing form

/**
 * Show the access-token form; `onSubmit` receives the trimmed token.
 * @param {(token: string) => void} onSubmit
 */
export function showAuthForm(onSubmit) {
  const form = /** @type {HTMLFormElement} */ (requireElement("auth-form"));
  const input = /** @type {HTMLInputElement} */ (requireElement("auth-token"));
  form.hidden = false;
  input.value = "";
  input.focus();
  form.onsubmit = (event) => {
    event.preventDefault();
    const token = input.value.trim();
    if (token) {
      onSubmit(token);
    }
  };
}

export function hideAuthForm() {
  const form = document.getElementById("auth-form");
  if (form) {
    form.hidden = true;
  }
}

/** @param {(event: MouseEvent) => void} handler */
export function bindRecordClick(handler) {
  recordBtn().addEventListener("click", (event) => {
    event.preventDefault();
    handler(event);
  });
}
