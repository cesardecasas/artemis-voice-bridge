// @ts-check
import { state } from "./state.js";
import { setStatus, updateRecordButton, bindRecordClick } from "./ui.js";
import { startRecording, stopRecording } from "./recorder.js";
import { connect, consumeTokenFromUrl, interrupt } from "./websocket.js";

bindRecordClick((event) => {
  event.preventDefault();

  // While Artemis is busy, a press still starts a new recording (barge-in).
  if (state.isStopping) {
    return;
  }

  if (state.isStarting) {
    state.recordingRequested = false;
    state.stopRequested = true;
    setStatus("Cancelling recording...");
    return;
  }

  if (state.isRecording) {
    state.recordingRequested = false;
    stopRecording();
    return;
  }

  state.recordingRequested = true;
  startRecording();
});

// Esc stops Artemis mid-reply (or cancels a recording in progress).
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") {
    return;
  }
  if (state.isRecording) {
    state.recordingRequested = false;
    state.discardRecording = true;
    stopRecording();
    return;
  }
  interrupt();
});

consumeTokenFromUrl();
updateRecordButton();
connect();
