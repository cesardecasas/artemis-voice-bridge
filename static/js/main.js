// @ts-check
import { HANDSFREE_STORAGE_KEY, MUTED_STORAGE_KEY } from "./constants.js";
import { state } from "./state.js";
import { appendMessage, bindRecordClick, requireElement, setStatus, showMuted, updateRecordButton } from "./ui.js";
import { startRecording, stopRecording } from "./recorder.js";
import { connect, consumeTokenFromUrl, interrupt, newConversation } from "./websocket.js";
import { onPlaybackIdle, stopPlayback } from "./audio.js";
import { notifyPlaybackIdle, setHandsFree } from "./handsfree.js";
import { startHealthPolling } from "./health.js";
import { sendMessage } from "./send.js";
import { loadStored, store } from "./storage.js";

/** Start a recording (barge-in if Artemis is speaking). */
function beginTalking() {
  if (state.isRecording || state.isStarting || state.isStopping) {
    return;
  }
  state.recordingRequested = true;
  void startRecording();
}

/** Stop the recording and send it. */
function finishTalking() {
  if (state.isStarting) {
    state.recordingRequested = false;
    state.stopRequested = true;
    setStatus("Cancelling recording…");
    return;
  }
  if (state.isRecording) {
    state.recordingRequested = false;
    stopRecording();
  }
}

// ------------------------------------------------------------- the core

bindRecordClick(() => {
  if (state.isStopping) {
    return;
  }
  if (state.isRecording || state.isStarting) {
    finishTalking();
    return;
  }
  // Tapping while Artemis talks only interrupts; tap again to speak.
  if (state.playing || state.awaitingAudio) {
    interrupt();
    return;
  }
  beginTalking();
});

// ------------------------------------------------------------- keyboard

/** @param {EventTarget | null} target */
function isTyping(target) {
  return target instanceof HTMLElement && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
}

// Space: hold to talk. Esc: cancel a recording or interrupt Artemis.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (state.isRecording) {
      state.recordingRequested = false;
      state.discardRecording = true;
      stopRecording();
      return;
    }
    interrupt();
    return;
  }

  if (event.code === "Space" && !isTyping(event.target)) {
    event.preventDefault();
    if (!event.repeat) {
      beginTalking();
    }
    return;
  }

  if (event.key.toLowerCase() === "h" && !event.repeat && !isTyping(event.target)
      && !event.ctrlKey && !event.metaKey && !event.altKey) {
    const button = document.getElementById("handsfree-btn");
    if (button && !button.hidden) void setHandsFree(!state.handsFreeWanted);
  }
});

document.addEventListener("keyup", (event) => {
  if (event.code === "Space" && !isTyping(event.target)) {
    event.preventDefault();
    finishTalking();
  }
});

// ------------------------------------------------------------- composer

const composer = /** @type {HTMLFormElement} */ (requireElement("composer"));
const textInput = /** @type {HTMLInputElement} */ (requireElement("text-input"));

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text) {
    return;
  }
  stopPlayback();
  if (sendMessage({ type: "text.input", text })) {
    appendMessage("user", text);
    textInput.value = "";
  } else {
    setStatus("Not connected", "#ff8094");
  }
});

// ------------------------------------------------------------- mute

state.muted = loadStored(MUTED_STORAGE_KEY) === "1";
showMuted(state.muted);

requireElement("new-chat-btn").addEventListener("click", () => {
  newConversation();
});

requireElement("mute-btn").addEventListener("click", () => {
  state.muted = !state.muted;
  store(MUTED_STORAGE_KEY, state.muted ? "1" : "0");
  showMuted(state.muted);
  if (state.muted) {
    stopPlayback();
  }
});

// ------------------------------------------------------------- hands-free

state.handsFreeWanted = loadStored(HANDSFREE_STORAGE_KEY) === "1";
requireElement("handsfree-btn").setAttribute("aria-pressed", state.handsFreeWanted ? "true" : "false");
requireElement("handsfree-btn").addEventListener("click", () => {
  void setHandsFree(!state.handsFreeWanted);
});
// A reply finished playing: in hands-free mode, listen for a follow-up.
onPlaybackIdle(notifyPlaybackIdle);

// ------------------------------------------------------------- boot

consumeTokenFromUrl();
updateRecordButton();
startHealthPolling();
connect();
