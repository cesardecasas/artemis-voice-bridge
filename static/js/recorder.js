// @ts-check
import { COLORS, MAX_RECORDING_MS, MIME_TYPE } from "./constants.js";
import { state } from "./state.js";
import { setStatus, updateRecordButton } from "./ui.js";
import { chime, cleanupRecording, startMicMeter, stopMeter, stopPlayback } from "./audio.js";
import { isConnected, sendAudioChunk, sendMessage } from "./send.js";

export async function startRecording() {
  // Allowed while Artemis is busy: starting a recording barges in and cancels the turn.
  if (state.isRecording || state.isStarting || state.isStopping) {
    return;
  }

  if (!isConnected()) {
    state.recordingRequested = false;
    setStatus("WebSocket not connected", COLORS.error);
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    state.recordingRequested = false;
    setStatus("Microphone requires HTTPS or localhost", COLORS.error);
    return;
  }

  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported(MIME_TYPE)) {
    state.recordingRequested = false;
    setStatus("WebM/Opus recording not supported", COLORS.error);
    return;
  }

  state.isStarting = true;
  state.stopRequested = false;
  state.discardRecording = false;
  state.recordedBytes = 0;
  updateRecordButton();

  try {
    state.microphoneStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });

    if (!state.recordingRequested || state.stopRequested) {
      await cleanupRecording();
      setStatus("Recording cancelled");
      return;
    }

    /** @type {typeof AudioContext} */
    const AudioContextImpl =
      window.AudioContext ?? /** @type {any} */ (window).webkitAudioContext;
    state.audioContext = new AudioContextImpl();
    const source = state.audioContext.createMediaStreamSource(state.microphoneStream);
    state.analyser = state.audioContext.createAnalyser();
    state.analyser.fftSize = 64;

    source.connect(state.analyser);

    const recorder = new MediaRecorder(state.microphoneStream, { mimeType: MIME_TYPE });
    state.mediaRecorder = recorder;

    recorder.ondataavailable = (event) => {
      if (event.data.size <= 0) {
        return;
      }

      state.recordedBytes += event.data.size;
      console.debug("Audio chunk:", event.data.size, "bytes");

      sendAudioChunk(event.data);
    };

    recorder.onstop = async () => {
      clearTimeout(state.recordingTimer);

      if (state.discardRecording) {
        state.discardRecording = false;
        sendMessage({ type: "interrupt" });
        setStatus("Recording cancelled");
      } else if (!sendMessage({ type: "audio.end" })) {
        // Otherwise the server answers with `state` events (transcribing, thinking, ...).
        state.isProcessing = false;
        setStatus("Connection lost", COLORS.error);
      }

      state.isStopping = false;
      await cleanupRecording();
      updateRecordButton();
    };

    recorder.onerror = async (event) => {
      console.error("Recorder error:", event);
      state.recordingRequested = false;
      state.isRecording = false;
      state.isProcessing = false;
      setStatus("Recording failed", COLORS.error);

      // Discard the partial recording on the server.
      sendMessage({ type: "interrupt" });

      await cleanupRecording();
      updateRecordButton();
    };

    // Barge-in: silence Artemis locally; the server cancels its turn on audio.start.
    stopPlayback();
    if (!sendMessage({ type: "audio.start", format: "webm/opus" })) {
      throw new Error("Connection lost");
    }

    recorder.start(100);

    state.isRecording = true;
    state.isStarting = false;

    updateRecordButton();
    updateRecordButton();
    startMicMeter();
    void chime("start");

    state.recordingTimer = setTimeout(() => {
      if (state.isRecording) {
        state.recordingRequested = false;
        stopRecording();
      }
    }, MAX_RECORDING_MS);
  } catch (error) {
    console.error("Microphone error:", error);
    state.recordingRequested = false;
    state.stopRequested = false;
    state.isRecording = false;
    state.isProcessing = false;
    const reason = error instanceof Error ? error.message : String(error);
    setStatus(`Microphone error: ${reason}`, COLORS.error);
    await cleanupRecording();
  } finally {
    state.isStarting = false;
    updateRecordButton();
  }
}

export function stopRecording() {
  if (state.isStarting && !state.isRecording) {
    state.stopRequested = true;
    state.recordingRequested = false;
    setStatus("Cancelling recording...");
    updateRecordButton();
    return;
  }

  if (!state.isRecording || !state.mediaRecorder || state.isStopping) {
    return;
  }

  state.isStopping = true;
  state.isRecording = false;
  state.recordingRequested = false;

  clearTimeout(state.recordingTimer);
  stopMeter();
  if (!state.discardRecording) {
    void chime("stop");
  }

  setStatus("Sending…");
  updateRecordButton();

  if (state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }
}
