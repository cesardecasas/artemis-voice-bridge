// @ts-check
import { END_MARKER, MAX_RECORDING_MS, MIME_TYPE } from "./constants.js";
import { state } from "./state.js";
import { setStatus, updateRecordButton } from "./ui.js";
import { cleanupRecording, startVisualizer } from "./audio.js";

export async function startRecording() {
  if (state.isRecording || state.isStarting || state.isStopping || state.isProcessing) {
    return;
  }

  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    state.recordingRequested = false;
    setStatus("WebSocket not connected", "#ff0000");
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    state.recordingRequested = false;
    setStatus("Microphone requires HTTPS or localhost", "#ff0000");
    return;
  }

  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported(MIME_TYPE)) {
    state.recordingRequested = false;
    setStatus("WebM/Opus recording not supported", "#ff0000");
    return;
  }

  state.isStarting = true;
  state.stopRequested = false;
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
    state.dataArray = new Uint8Array(state.analyser.frequencyBinCount);

    const recorder = new MediaRecorder(state.microphoneStream, { mimeType: MIME_TYPE });
    state.mediaRecorder = recorder;

    recorder.ondataavailable = (event) => {
      if (event.data.size <= 0) {
        return;
      }

      state.recordedBytes += event.data.size;
      console.debug("Audio chunk:", event.data.size, "bytes");

      if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(event.data);
      }
    };

    recorder.onstop = async () => {
      clearTimeout(state.recordingTimer);

      const bytes = state.recordedBytes;

      if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.send(END_MARKER);
        state.isProcessing = bytes > 0;

        if (bytes > 0) {
          setStatus("Processing...");
        } else {
          setStatus("Recording was empty");
        }
      } else {
        state.isProcessing = false;
        setStatus("Connection lost", "#ff0000");
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
      setStatus("Recording failed", "#ff0000");

      if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.socket.close();
      }

      await cleanupRecording();
      updateRecordButton();
    };

    recorder.start(100);

    state.isRecording = true;
    state.isStarting = false;

    setStatus("Listening...");
    updateRecordButton();
    startVisualizer();

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
    setStatus(`Microphone error: ${reason}`, "#ff0000");
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
  if (state.animationId !== null) {
    cancelAnimationFrame(state.animationId);
  }
  state.animationId = null;

  setStatus("Finishing recording...");
  updateRecordButton();

  if (state.mediaRecorder.state !== "inactive") {
    state.mediaRecorder.stop();
  }
}
