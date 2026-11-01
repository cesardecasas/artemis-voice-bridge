// @ts-check
import { state } from "./state.js";
import { setStatus, updateRecordButton, bindRecordClick } from "./ui.js";
import { startRecording, stopRecording } from "./recorder.js";
import { connect } from "./websocket.js";

bindRecordClick((event) => {
  event.preventDefault();

  if (state.isProcessing || state.isStopping) {
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

updateRecordButton();
connect();
