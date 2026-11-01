// @ts-check
import { VISUALIZER_BARS } from "./constants.js";
import { state } from "./state.js";
import { COLORS } from "./constants.js";
import { setAiVoiceState, setStatus, updateRecordButton, visualizerEl } from "./ui.js";

/** @returns {HTMLElement[]} */
function ensureVisualizerBars() {
  const visualizer = visualizerEl();

  if (visualizer.children.length === 0) {
    for (let index = 0; index < VISUALIZER_BARS; index += 1) {
      const bar = document.createElement("div");
      bar.className = "bar";
      visualizer.appendChild(bar);
    }
  }

  return /** @type {HTMLElement[]} */ (Array.from(visualizer.getElementsByClassName("bar")));
}

function resetVisualizerBars() {
  for (const bar of ensureVisualizerBars()) {
    bar.style.height = "5px";
  }
}

function stopPlaybackAnimation() {
  if (state.playbackAnimationId !== null) {
    cancelAnimationFrame(state.playbackAnimationId);
  }
  state.playbackAnimationId = null;

  if (!state.isRecording) {
    resetVisualizerBars();
  }
}

function startPlaybackAnimation() {
  if (state.isRecording) {
    return;
  }

  const bars = ensureVisualizerBars();
  if (bars.length === 0) {
    return;
  }

  let phase = 0;

  const draw = () => {
    const audio = state.currentAudio;

    if (!audio || audio.paused || audio.ended || state.isRecording) {
      stopPlaybackAnimation();
      return;
    }

    phase += 0.18;

    for (let index = 0; index < bars.length; index += 1) {
      const wave = (Math.sin(phase + index * 0.55) + 1) / 2;
      const jitter = Math.random() * 0.35;
      const value = 8 + (wave + jitter) * 28;
      bars[index].style.height = `${Math.max(5, value)}px`;
    }

    state.playbackAnimationId = requestAnimationFrame(draw);
  };

  state.playbackAnimationId = requestAnimationFrame(draw);
}

/** Stop any reply that is currently playing (interrupt / barge-in). */
export function stopPlayback() {
  const audio = state.currentAudio;
  if (!audio) {
    return;
  }
  audio.pause();
  // Fire the same cleanup path as a natural end (revokes the blob URL).
  audio.dispatchEvent(new Event("ended"));
  state.currentAudio = null;
  setAiVoiceState(false);
  stopPlaybackAnimation();
}

/**
 * Play synthesized speech received from the server.
 * The object URL is revoked once playback ends, so nothing is kept around.
 *
 * @param {Blob} audioBlob
 */
export async function playAudio(audioBlob) {
  stopPlaybackAnimation();

  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio = null;
  }

  const url = URL.createObjectURL(audioBlob);
  const audio = new Audio(url);
  let finished = false;

  const finishPlayback = () => {
    if (finished) {
      return;
    }

    finished = true;
    URL.revokeObjectURL(url);
    setAiVoiceState(false);
    stopPlaybackAnimation();

    if (state.currentAudio === audio) {
      state.currentAudio = null;
    }
  };

  try {
    setAiVoiceState(true);
    state.currentAudio = audio;

    audio.addEventListener("ended", finishPlayback, { once: true });
    audio.addEventListener("error", finishPlayback, { once: true });

    await audio.play();
    startPlaybackAnimation();
  } catch (error) {
    finishPlayback();
    console.error("Playback failed:", error);
    setStatus("Audio playback blocked or unavailable", COLORS.error);
  }
}

export function startVisualizer() {
  const bars = ensureVisualizerBars();

  function draw() {
    if (!state.analyser || !state.dataArray || !state.isRecording) {
      return;
    }

    state.analyser.getByteFrequencyData(state.dataArray);

    for (let index = 0; index < bars.length; index += 1) {
      const value = (state.dataArray[index] ?? 0) / 2;
      bars[index].style.height = `${Math.max(5, value)}px`;
    }

    state.animationId = requestAnimationFrame(draw);
  }

  draw();
}

export async function cleanupRecording() {
  clearTimeout(state.recordingTimer);
  if (state.animationId !== null) {
    cancelAnimationFrame(state.animationId);
  }
  state.animationId = null;

  if (state.microphoneStream) {
    state.microphoneStream.getTracks().forEach((track) => track.stop());
    state.microphoneStream = null;
  }

  if (state.audioContext && state.audioContext.state !== "closed") {
    const context = state.audioContext;
    state.audioContext = null;

    try {
      await context.close();
    } catch (error) {
      console.error("Audio context cleanup error:", error);
    }
  }

  state.audioContext = null;
  state.analyser = null;
  state.dataArray = null;
  state.mediaRecorder = null;
  state.isRecording = false;
  state.recordingRequested = false;
  state.stopRequested = false;
  stopPlaybackAnimation();

  updateRecordButton();
}
