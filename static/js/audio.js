// @ts-check
import { COLORS, VISUALIZER_BARS } from "./constants.js";
import { state } from "./state.js";
import { coreEl, renderCore, setStatus, updateRecordButton, visualizerEl } from "./ui.js";

/** @type {AudioContext | null} Shared context for reply playback and chimes. */
let outputContext = null;

/** @returns {typeof AudioContext | undefined} */
function audioContextClass() {
  return window.AudioContext ?? /** @type {any} */ (window).webkitAudioContext;
}

/**
 * The shared output context, resumed if possible. Browsers only allow resuming
 * after a user gesture, so this is first called from a click or key press.
 * @returns {Promise<AudioContext | null>}
 */
async function outputAudio() {
  const Impl = audioContextClass();
  if (!Impl) return null;
  try {
    outputContext ??= new Impl();
    if (outputContext.state === "suspended") {
      await outputContext.resume();
    }
    return outputContext.state === "running" ? outputContext : null;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------- level meter

/** @returns {HTMLElement[]} */
function bars() {
  const visualizer = visualizerEl();
  if (visualizer.children.length === 0) {
    for (let index = 0; index < VISUALIZER_BARS; index += 1) {
      const bar = document.createElement("span");
      bar.className = "bar";
      visualizer.appendChild(bar);
    }
  }
  return /** @type {HTMLElement[]} */ (Array.from(visualizer.children));
}

export function stopMeter() {
  if (state.meterId !== null) {
    cancelAnimationFrame(state.meterId);
  }
  state.meterId = null;
  coreEl().style.setProperty("--level", "0");
  for (const bar of bars()) {
    bar.style.height = "5px";
  }
}

/**
 * Drive the orb (`--level`) and waveform bars from an analyser.
 * @param {AnalyserNode} analyser
 * @param {() => boolean} shouldRun  checked every frame; the meter stops when false
 */
function runMeter(analyser, shouldRun) {
  stopMeter();
  const data = new Uint8Array(analyser.frequencyBinCount);
  const barEls = bars();
  const core = coreEl();
  // Voice energy sits in the lower bins; skip the top quarter.
  const usable = Math.max(1, Math.floor(data.length * 0.75));

  const draw = () => {
    if (!shouldRun()) {
      stopMeter();
      return;
    }
    analyser.getByteFrequencyData(data);

    let sum = 0;
    for (let index = 0; index < usable; index += 1) sum += data[index] ?? 0;
    const level = Math.min(1, sum / usable / 140);
    core.style.setProperty("--level", level.toFixed(3));

    // Mirror the spectrum so the waveform is symmetric around the center.
    const half = barEls.length / 2;
    for (let index = 0; index < barEls.length; index += 1) {
      const distance = Math.abs(index - half + 0.5) / half;
      const value = data[Math.floor(distance * (usable - 1))] ?? 0;
      barEls[index].style.height = `${Math.max(5, 5 + (value / 255) * 44)}px`;
    }

    state.meterId = requestAnimationFrame(draw);
  };

  state.meterId = requestAnimationFrame(draw);
}

/**
 * Fallback when the audio can't be routed through Web Audio: a gentle synthetic wave.
 * @param {() => boolean} shouldRun
 */
function runSyntheticMeter(shouldRun) {
  stopMeter();
  const barEls = bars();
  const core = coreEl();
  let phase = 0;

  const draw = () => {
    if (!shouldRun()) {
      stopMeter();
      return;
    }
    phase += 0.18;
    core.style.setProperty("--level", ((Math.sin(phase) + 1) / 4).toFixed(3));
    for (let index = 0; index < barEls.length; index += 1) {
      const wave = (Math.sin(phase + index * 0.55) + 1) / 2;
      barEls[index].style.height = `${8 + wave * 28}px`;
    }
    state.meterId = requestAnimationFrame(draw);
  };

  state.meterId = requestAnimationFrame(draw);
}

/** Start the mic meter for the current recording. */
export function startMicMeter() {
  if (state.analyser) {
    const analyser = state.analyser;
    runMeter(analyser, () => state.isRecording && state.analyser === analyser);
  }
}

// ------------------------------------------------------------- chimes

/**
 * Short synthesized cue (no audio files). Silent when muted.
 * @param {"start" | "stop" | "error"} kind
 */
export async function chime(kind) {
  if (state.muted) return;
  const ctx = await outputAudio();
  if (!ctx) return;

  /** @type {Record<typeof kind, [number, number, number][]>} [start Hz, end Hz, offset s] */
  const notes = {
    start: [[660, 880, 0]],
    stop: [[880, 620, 0]],
    error: [[330, 300, 0], [330, 300, 0.16]],
  };

  for (const [from, to, offset] of notes[kind]) {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    const t = ctx.currentTime + offset;
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(from, t);
    oscillator.frequency.exponentialRampToValueAtTime(to, t + 0.12);
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(0.07, t + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
    oscillator.connect(gain).connect(ctx.destination);
    oscillator.start(t);
    oscillator.stop(t + 0.2);
  }
}

// ------------------------------------------------------------- playback

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
  stopMeter();
  renderCore();
}

/**
 * Route the element through an analyser so the orb follows the real voice.
 * @param {HTMLAudioElement} audio
 * @returns {Promise<AnalyserNode | null>}
 */
async function analysePlayback(audio) {
  const ctx = await outputAudio();
  if (!ctx) return null;
  try {
    const source = ctx.createMediaElementSource(audio);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 64;
    source.connect(analyser);
    analyser.connect(ctx.destination);
    return analyser;
  } catch {
    return null;
  }
}

/**
 * Play synthesized speech received from the server. Muted: skipped entirely.
 * The object URL is revoked once playback ends, so nothing is kept around.
 * @param {Blob} audioBlob
 */
export async function playAudio(audioBlob) {
  stopPlayback();
  if (state.muted) {
    return;
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
    if (state.currentAudio === audio) {
      state.currentAudio = null;
      stopMeter();
    }
    renderCore();
  };

  audio.addEventListener("ended", finishPlayback, { once: true });
  audio.addEventListener("error", finishPlayback, { once: true });

  state.currentAudio = audio;
  renderCore();

  try {
    const analyser = await analysePlayback(audio);
    await audio.play();
    const playing = () => state.currentAudio === audio && !audio.paused && !audio.ended;
    if (analyser) {
      runMeter(analyser, playing);
    } else {
      runSyntheticMeter(playing);
    }
  } catch (error) {
    finishPlayback();
    console.error("Playback failed:", error);
    setStatus("Audio playback blocked. Tap the core once to enable sound", COLORS.amber);
  }
}

// ------------------------------------------------------------- recording cleanup

export async function cleanupRecording() {
  clearTimeout(state.recordingTimer);

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
  state.mediaRecorder = null;
  state.isRecording = false;
  state.recordingRequested = false;
  state.stopRequested = false;
  if (!state.currentAudio) {
    stopMeter();
  }

  updateRecordButton();
}
