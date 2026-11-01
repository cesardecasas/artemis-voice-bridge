// @ts-check
import { HEALTH_POLL_MS } from "./constants.js";

/** @typedef {import("./types.js").MetricsMessage} MetricsMessage */
/** @typedef {"ready" | "loading" | "offline" | "unknown"} SvcStatus */

/**
 * Shape of `GET /health` (see HealthReport in main.py).
 * @typedef {{
 *   status: "ready" | "degraded",
 *   services: Record<"stt" | "tts", { status: SvcStatus, detail: string }>,
 *   agent: { status: SvcStatus, backend: string },
 *   handsfree: boolean,
 * }} HealthReport
 */

const AGENT_NAMES = /** @type {Record<string, string>} */ ({
  echo: "Echo · placeholder",
  hermes: "Hermes",
});

/** @param {string} detail */
function prettyModel(detail) {
  return detail.replace("whisper-large-v3-turbo", "Whisper v3 Turbo").replace("GPU.0", "Arc B50");
}

/**
 * @param {string} id  element id of a `.svc` item
 * @param {SvcStatus} status
 * @param {string} text
 */
export function setService(id, status, text) {
  const item = document.getElementById(id);
  if (!item) return;
  item.dataset.status = status;
  const value = item.querySelector(".svc-value");
  if (value instanceof HTMLElement) value.innerText = text;
}

/**
 * @param {"ready" | "loading" | "offline"} status
 * @param {string} text
 */
export function showLink(status, text) {
  setService("svc-link", status, text);
}

/** @param {string} backend */
export function agentDisplayName(backend) {
  return AGENT_NAMES[backend] ?? backend;
}

/** @param {MetricsMessage} metrics */
export function showMetrics(metrics) {
  const element = document.getElementById("metrics");
  if (!element) return;
  const parts = [
    metrics.stt_ms !== null ? `STT ${metrics.stt_ms} ms` : null,
    metrics.first_audio_ms !== null ? `first audio ${metrics.first_audio_ms} ms` : null,
    `total ${metrics.total_ms} ms`,
  ].filter(Boolean);
  element.innerText = `last turn · ${parts.join(" · ")}`;
}

export async function refreshHealth() {
  try {
    const response = await fetch("/health", { cache: "no-store" });
    const report = /** @type {HealthReport} */ (await response.json());
    const { stt, tts } = report.services;
    setService("svc-stt", stt.status, stt.status === "offline" ? "offline" : prettyModel(stt.detail));
    setService("svc-tts", tts.status, tts.status === "offline" ? "offline · text only" : tts.detail);
    setService("svc-agent", report.agent.status, agentDisplayName(report.agent.backend));
    const handsFreeButton = document.getElementById("handsfree-btn");
    if (handsFreeButton) handsFreeButton.hidden = !report.handsfree;
  } catch {
    for (const id of ["svc-stt", "svc-tts", "svc-agent"]) {
      setService(id, "offline", "unreachable");
    }
  }
}

export function startHealthPolling() {
  void refreshHealth();
  setInterval(() => void refreshHealth(), HEALTH_POLL_MS);
}
