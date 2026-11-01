// @ts-check
import { APPROVAL_TIMEOUT_MS, MAX_ACTIVITY_ITEMS } from "./constants.js";
import { state } from "./state.js";

/** Open approval cards by request id: settle(approved, message). */
/** @type {Map<string, (approved: boolean, message: string) => void>} */
const openApprovals = new Map();

/** Running tool cards by id, with their start time. */
/** @type {Map<string, { card: HTMLElement, startedAt: number, name: string }>} */
const runningTools = new Map();

const CHECK_SVG =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5 10 17 19 7"/></svg>';
const CROSS_SVG =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M7 7l10 10"/><path d="M17 7 7 17"/></svg>';
const WARN_SVG =
  '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 2.5 20h19z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/></svg>';

/** @returns {HTMLElement | null} */
function feed() {
  return document.getElementById("activity");
}

/** @param {HTMLElement} card */
function push(card) {
  const container = feed();
  if (!container) return;
  container.appendChild(card);
  while (container.children.length > MAX_ACTIVITY_ITEMS) {
    container.children[0].remove();
  }
  container.scrollTop = container.scrollHeight;
}

/**
 * @param {string} tag
 * @param {string} className
 * @param {string} [text]
 * @returns {HTMLElement}
 */
function el(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.innerText = text;
  return node;
}

// ------------------------------------------------------------- tools

/**
 * @param {string} id
 * @param {string} name
 * @param {string} summary
 */
export function showToolStart(id, name, summary) {
  const card = el("div", "tool tool-running");
  card.append(
    el("span", "tool-icon"),
    (() => {
      const body = el("div", "tool-body");
      body.append(el("div", "tool-title", summary), el("div", "tool-meta", `${name} · running…`));
      return body;
    })(),
  );
  runningTools.set(id, { card, startedAt: performance.now(), name });
  push(card);
}

/**
 * @param {string} id
 * @param {boolean} ok
 * @param {string} summary
 */
export function showToolEnd(id, ok, summary) {
  const running = runningTools.get(id);
  runningTools.delete(id);

  const card = running?.card ?? el("div", "tool");
  if (!running) {
    card.append(el("span", "tool-icon"), el("div", "tool-body"));
    push(card);
  }

  const seconds = running ? ((performance.now() - running.startedAt) / 1000).toFixed(1) : null;
  card.className = `tool ${ok ? "tool-ok" : "tool-failed"}`;

  const [icon, body] = /** @type {HTMLElement[]} */ (Array.from(card.children));
  icon.innerHTML = ok ? CHECK_SVG : CROSS_SVG;
  body.replaceChildren(
    el("div", "tool-title", summary),
    el("div", "tool-meta", [running?.name, seconds && `${seconds} s`].filter(Boolean).join(" · ")),
  );
}

/** Mark every running tool as cut off (interrupt / disconnect). */
export function abandonTools() {
  for (const id of Array.from(runningTools.keys())) {
    showToolEnd(id, false, "Interrupted");
  }
}

// ------------------------------------------------------------- approvals

/**
 * An approval was answered by voice: settle its card without notifying the server.
 * @param {string} id
 * @param {boolean} approved
 */
export function settleApproval(id, approved) {
  openApprovals.get(id)?.(approved, approved ? "✓ Approved by voice" : "✕ Denied by voice");
}

/**
 * Amber approval card with Approve/Deny and a countdown matching the server timeout.
 * @param {string} id
 * @param {string} action
 * @param {string} detail
 * @param {(approved: boolean) => void} onDecide
 * @returns {HTMLElement}
 */
export function showApproval(id, action, detail, onDecide) {
  const card = el("div", "approval");
  card.setAttribute("role", "group");
  card.setAttribute("aria-label", `Approval request: ${action}`);

  const head = el("div", "approval-head");
  head.innerHTML = WARN_SVG;
  const headLabel = el("span", "label", "Approval needed");
  head.append(headLabel);

  const actions = el("div", "approval-actions");
  const deny = /** @type {HTMLButtonElement} */ (el("button", "btn", "Deny"));
  const approve = /** @type {HTMLButtonElement} */ (el("button", "btn btn-amber", "Approve"));
  deny.type = "button";
  approve.type = "button";
  const timer = el("span", "approval-timer");
  actions.append(deny, approve, timer);
  if (state.handsFree !== "off") {
    actions.append(el("div", "approval-voice", "or say \u201capprove\u201d / \u201cdeny\u201d"));
  }

  card.append(head, el("div", "approval-action", action), el("div", "codeline", detail), actions);

  const deadline = Date.now() + APPROVAL_TIMEOUT_MS;
  let settled = false;
  const tick = () => {
    const left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    timer.innerText = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left`;
    if (left === 0) settle(false, "Denied · timed out", false);
  };
  const interval = setInterval(tick, 1000);
  tick();

  /**
   * @param {boolean} approved
   * @param {string} message
   * @param {boolean} notify  Tell the server (false when the server already timed out).
   */
  function settle(approved, message, notify) {
    clearInterval(interval);
    if (settled) return;
    settled = true;
    openApprovals.delete(id);
    card.classList.add(approved ? "approval-approved" : "approval-denied");
    headLabel.innerText = approved ? "Approved" : "Denied";
    actions.replaceWith(el("div", "approval-result", message));
    if (notify) onDecide(approved);
  }

  openApprovals.set(id, (approved, message) => settle(approved, message, false));
  approve.addEventListener("click", () => settle(true, "✓ Approved", true));
  deny.addEventListener("click", () => settle(false, "✕ Denied · Artemis will not run it", true));

  push(card);
  approve.focus({ preventScroll: true });
  return card;
}
