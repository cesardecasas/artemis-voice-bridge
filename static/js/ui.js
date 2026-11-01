// @ts-check
import { COLORS } from "./constants.js";
import { state } from "./state.js";

/** @typedef {import("./types.js").Speaker} Speaker */
/** @typedef {import("./types.js").PipelineState} PipelineState */

/**
 * Cached DOM references (looked up lazily so tests can swap the DOM).
 * @type {{
 *   recordBtn: HTMLButtonElement | null,
 *   display: HTMLElement | null,
 *   status: HTMLElement | null,
 *   visualizer: HTMLElement | null,
 *   streamingReply: HTMLElement | null,
 *   tools: Map<string, HTMLElement>,
 * }}
 */
export const ui = {
  recordBtn: null,
  display: null,
  status: null,
  visualizer: null,
  streamingReply: null,
  tools: new Map(),
};

/** @type {Record<PipelineState, string>} */
const STATE_LABELS = {
  idle: "Connected",
  listening: "Listening...",
  transcribing: "Transcribing...",
  thinking: "Thinking...",
  speaking: "Generating speech...",
};

const ARTEMIS_PREFIX = "Artemis: ";

/**
 * @param {string} id
 * @returns {HTMLElement}
 */
function requireElement(id) {
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
 * @param {string} message
 * @param {string} [color]
 */
export function setStatus(message, color = COLORS.neutral) {
  const element = statusEl();
  element.innerText = message;
  element.style.color = color;
}

/** @param {PipelineState} value */
export function showPipelineState(value) {
  setStatus(STATE_LABELS[value], value === "idle" ? COLORS.ready : COLORS.busy);
}

/** @param {boolean} isSpeaking */
export function setAiVoiceState(isSpeaking) {
  if (!document.body) return;
  document.body.classList.toggle("ai-speaking", isSpeaking);
}

export function updateRecordButton() {
  const button = recordBtn();
  const active = state.isRecording || state.isStarting;

  button.classList.toggle("recording", active);
  // Not disabled while Artemis is busy: pressing it barges in.
  button.disabled = state.isStopping;
  button.setAttribute("aria-label", active ? "Stop recording" : "Start recording");
  button.setAttribute("aria-pressed", active ? "true" : "false");

  if (document.body) {
    document.body.classList.toggle("recording", active);
  }
}

function scrollToEnd() {
  const container = display();
  container.scrollTop = container.scrollHeight;
}

/**
 * @param {Speaker} type
 * @param {string} text
 * @returns {HTMLElement}
 */
export function appendMessage(type, text) {
  const div = document.createElement("div");
  div.className = `message ${type}`;
  div.innerText = text;
  display().appendChild(div);
  scrollToEnd();
  return div;
}

/**
 * Append a streamed token to the in-progress Artemis reply (creating it if needed).
 * @param {string} token
 */
export function appendReplyToken(token) {
  ui.streamingReply ??= appendMessage("artemis", ARTEMIS_PREFIX);
  ui.streamingReply.innerText += token;
  scrollToEnd();
}

/**
 * Replace the streamed reply with the authoritative final text.
 * @param {string} text
 */
export function finishReply(text) {
  if (ui.streamingReply) {
    ui.streamingReply.innerText = `${ARTEMIS_PREFIX}${text}`;
  } else if (text) {
    appendMessage("artemis", `${ARTEMIS_PREFIX}${text}`);
  }
  ui.streamingReply = null;
}

/** Abandon a partially streamed reply (interrupt/error). */
export function abandonReply() {
  if (ui.streamingReply) {
    ui.streamingReply.innerText += " …";
  }
  ui.streamingReply = null;
}

/**
 * @param {string} id
 * @param {string} summary
 */
export function showToolStart(id, summary) {
  ui.tools.set(id, appendMessage("tool", `⚙ ${summary}…`));
}

/**
 * @param {string} id
 * @param {boolean} ok
 * @param {string} summary
 */
export function showToolEnd(id, ok, summary) {
  const text = `${ok ? "✓" : "✗"} ${summary}`;
  const element = ui.tools.get(id);
  if (element) {
    element.innerText = text;
    ui.tools.delete(id);
  } else {
    appendMessage("tool", text);
  }
}

/**
 * Minimal approval prompt (a full approval card arrives in Phase 4).
 * @param {string} action
 * @param {string} detail
 * @param {(approved: boolean) => void} onDecide
 */
export function showApproval(action, detail, onDecide) {
  const container = appendMessage("tool", `⚠ Approval needed: ${action}\n${detail}`);
  container.classList.add("approval");

  /** @param {string} label @param {boolean} approved */
  const makeButton = (label, approved) => {
    const button = document.createElement("button");
    button.type = "button";
    button.innerText = label;
    button.addEventListener("click", () => {
      for (const b of container.querySelectorAll("button")) b.disabled = true;
      container.append(approved ? "  → approved" : "  → denied");
      onDecide(approved);
    });
    return button;
  };

  container.append(document.createElement("br"), makeButton("Approve", true), makeButton("Deny", false));
}

/** @param {(event: MouseEvent) => void} handler */
export function bindRecordClick(handler) {
  const button = recordBtn();
  button.style.touchAction = "manipulation";
  button.addEventListener("click", (event) => {
    event.preventDefault();
    handler(event);
  });
}
