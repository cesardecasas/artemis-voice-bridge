// @ts-check
import { COLORS, HANDSFREE_STORAGE_KEY } from "./constants.js";
import { state } from "./state.js";
import { renderCore, setStatus } from "./ui.js";
import { chime, watchMicLevel } from "./audio.js";
import { sendAudioChunk, sendMessage } from "./send.js";
import { store } from "./storage.js";

/** @typedef {import("./types.js").HandsFreeStatus} HandsFreeStatus */

/**
 * Hands-free mode (Phase 7). The mic stays open and streams 16 kHz PCM to the
 * server, which does voice activity detection and listens for "Artemis".
 * Nothing is recorded here: frames are sent and dropped.
 */
const mic = {
  /** @type {MediaStream | null} */
  stream: null,
  /** @type {AudioContext | null} */
  context: null,
  /** @type {AudioWorkletNode | null} */
  node: null,
  /** @type {(() => void) | null} */
  stopMeter: null,
};

/** @returns {boolean} hands-free is switched on in this browser */
export function handsFreeWanted() {
  return state.handsFreeWanted;
}

/** @param {boolean} on */
function showButton(on) {
  const button = document.getElementById("handsfree-btn");
  if (button) {
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.setAttribute("aria-label", on ? "Turn hands-free off" : "Turn hands-free on");
  }
}

/** Open the mic and start streaming PCM frames. */
async function openMic() {
  if (mic.stream) return;
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone requires HTTPS");
  }
  mic.stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
  });

  /** @type {typeof AudioContext} */
  const Impl = window.AudioContext ?? /** @type {any} */ (window).webkitAudioContext;
  mic.context = new Impl();
  await mic.context.audioWorklet.addModule("js/pcm-worklet.js");
  const source = mic.context.createMediaStreamSource(mic.stream);
  mic.node = new AudioWorkletNode(mic.context, "pcm-downsampler");
  mic.node.port.onmessage = (event) => {
    // Push-to-talk owns the server's audio input while it records.
    if (!state.isRecording && !state.isStarting && state.connected) {
      sendAudioChunk(/** @type {ArrayBuffer} */ (event.data));
    }
  };
  source.connect(mic.node);

  const analyser = mic.context.createAnalyser();
  analyser.fftSize = 64;
  source.connect(analyser);
  mic.stopMeter = watchMicLevel(analyser, () => state.handsFree === "listening");
}

async function closeMic() {
  mic.stopMeter?.();
  mic.node?.port.close();
  mic.stream?.getTracks().forEach((track) => track.stop());
  if (mic.context && mic.context.state !== "closed") {
    await mic.context.close().catch(() => undefined);
  }
  mic.stream = null;
  mic.context = null;
  mic.node = null;
  mic.stopMeter = null;
}

/** Ask the server to start (after the mic is open). Called again after reconnects. */
export async function resumeHandsFree() {
  if (!state.handsFreeWanted || !state.connected) return;
  try {
    await openMic();
    sendMessage({ type: "handsfree.start", format: "pcm16", sample_rate: 16000 });
  } catch (error) {
    console.error("Hands-free mic failed:", error);
    await setHandsFree(false);
    const reason = error instanceof Error ? error.message : String(error);
    setStatus(`Hands-free needs the microphone: ${reason}`, COLORS.error);
  }
}

/** @param {boolean} on */
export async function setHandsFree(on) {
  state.handsFreeWanted = on;
  store(HANDSFREE_STORAGE_KEY, on ? "1" : "0");
  showButton(on);
  if (on) {
    await resumeHandsFree();
  } else {
    sendMessage({ type: "handsfree.stop" });
    await closeMic();
    state.handsFree = "off";
    renderCore();
  }
}

/**
 * Server reports the hands-free state.
 * @param {HandsFreeStatus} status
 * @param {string} reason
 */
export function onHandsFreeStatus(status, reason) {
  const before = state.handsFree;
  state.handsFree = status;
  if (status === "listening" && before !== "listening" && reason === "wake") {
    void chime("start"); // "Artemis." → I'm listening
  }
  renderCore();
}

/** The connection dropped: the server forgot hands-free, keep the mic for the reconnect. */
export function onDisconnect() {
  state.handsFree = "off";
}

/** Tell the server Artemis has finished talking (opens the follow-up window). */
export function notifyPlaybackIdle() {
  if (state.handsFree !== "off" && state.connected) {
    sendMessage({ type: "playback.idle" });
  }
}
