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

// ------------------------------------------------------------- playback queue

/**
 * Gapless reply playback (Phase 5). Each `tts.chunk` is one sentence; chunks
 * are decoded in arrival order and scheduled back to back on the shared
 * AudioContext, all through one analyser that drives the orb.
 *
 * `generation` is bumped by stopPlayback(): decodes still in flight from an
 * interrupted reply see a stale generation and are dropped.
 */
const playback = {
  /** @type {Set<AudioBufferSourceNode>} */
  sources: new Set(),
  /** @type {AnalyserNode | null} */
  analyser: null,
  /** Context time at which the last scheduled chunk ends. */
  endsAt: 0,
  generation: 0,
  /** Keeps decode + schedule in arrival order. */
  chain: Promise.resolve(),
};

/** Small lead so the first chunk never starts in the past. */
const SCHEDULE_LEAD_S = 0.03;

/** @param {AudioContext} ctx */
function playbackAnalyser(ctx) {
  if (!playback.analyser || playback.analyser.context !== ctx) {
    playback.analyser = ctx.createAnalyser();
    playback.analyser.fftSize = 64;
    playback.analyser.connect(ctx.destination);
  }
  return playback.analyser;
}

function onQueueDrained() {
  state.playing = false;
  stopMeter();
  renderCore();
}

/** Stop the reply and drop everything queued (interrupt / barge-in / mute). */
export function stopPlayback() {
  playback.generation += 1;
  playback.endsAt = 0;
  const sources = Array.from(playback.sources);
  playback.sources.clear();
  for (const source of sources) {
    source.onended = null;
    try {
      source.stop();
    } catch {
      // already stopped
    }
  }
  if (state.playing) {
    onQueueDrained();
  }
}

/**
 * Queue one chunk of synthesized speech (a WAV sentence) after the previous one.
 * Muted: skipped entirely.
 * @param {Blob} audioBlob
 * @returns {Promise<void>} resolves once the chunk is scheduled (or dropped)
 */
export function playAudio(audioBlob) {
  if (state.muted) {
    return Promise.resolve();
  }
  const generation = playback.generation;
  state.playing = true;
  renderCore();

  playback.chain = playback.chain
    .then(() => scheduleChunk(audioBlob, generation))
    .catch((error) => {
      console.error("Playback failed:", error);
      if (generation === playback.generation && playback.sources.size === 0) {
        onQueueDrained();
      }
    });
  return playback.chain;
}

/**
 * @param {Blob} audioBlob
 * @param {number} generation
 */
async function scheduleChunk(audioBlob, generation) {
  const ctx = await outputAudio();
  if (!ctx) {
    if (generation === playback.generation) {
      onQueueDrained();
      setStatus("Audio is blocked. Tap the core once to enable sound", COLORS.amber);
    }
    return;
  }

  const buffer = await ctx.decodeAudioData(await audioBlob.arrayBuffer());
  if (generation !== playback.generation) {
    return; // interrupted while decoding
  }

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  const analyser = playbackAnalyser(ctx);
  source.connect(analyser);

  const startAt = Math.max(ctx.currentTime + SCHEDULE_LEAD_S, playback.endsAt);
  playback.endsAt = startAt + buffer.duration;
  playback.sources.add(source);

  source.onended = () => {
    playback.sources.delete(source);
    if (playback.sources.size === 0 && generation === playback.generation) {
      onQueueDrained();
    }
  };
  source.start(startAt);

  if (state.meterId === null) {
    runMeter(analyser, () => state.playing && !state.isRecording);
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
  if (!state.playing) {
    stopMeter();
  }

  updateRecordButton();
}
