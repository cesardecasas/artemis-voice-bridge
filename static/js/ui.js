// @ts-check
import { state } from "./state.js";

/** @typedef {import("./types.js").Speaker} Speaker */

/**
 * Cached DOM references (looked up lazily so tests can swap the DOM).
 * @type {{
 *   recordBtn: HTMLButtonElement | null,
 *   display: HTMLElement | null,
 *   status: HTMLElement | null,
 *   visualizer: HTMLElement | null,
 * }}
 */
export const ui = {
  recordBtn: null,
  display: null,
  status: null,
  visualizer: null,
};

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
export function setStatus(message, color = "#ffffff") {
  const element = statusEl();
  element.innerText = message;
  element.style.color = color;
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
  button.disabled = state.isProcessing || state.isStopping;
  button.setAttribute("aria-label", active ? "Stop recording" : "Start recording");
  button.setAttribute("aria-pressed", active ? "true" : "false");

  if (document.body) {
    document.body.classList.toggle("recording", active);
  }
}

/**
 * @param {Speaker} type
 * @param {string} text
 */
export function appendMessage(type, text) {
  const container = display();
  const div = document.createElement("div");
  div.className = `message ${type}`;
  div.innerText = text;

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
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
