// @ts-check

/** @typedef {import("./types.js").PipelineState} PipelineState */
/** @typedef {import("./types.js").MetricsMessage} MetricsMessage */

/**
 * Browser runtime state shared across modules.
 *
 * @typedef {object} AppState
 * @property {WebSocket | null} socket
 * @property {boolean} connected  WebSocket open and session established.
 * @property {string | null} sessionId  Server session; persisted so reloads keep context.
 * @property {string} agentName  Agent backend reported by the server ("echo", "hermes").
 * @property {PipelineState} serverState  Last `state` event from the server.
 * @property {MetricsMessage | null} lastMetrics
 * @property {boolean} muted  Don't play Artemis's voice or chimes (persisted).
 * @property {MediaRecorder | null} mediaRecorder
 * @property {MediaStream | null} microphoneStream
 * @property {AudioContext | null} audioContext  Mic analysis context (per recording).
 * @property {AnalyserNode | null} analyser  Mic analyser.
 * @property {number | null} meterId  requestAnimationFrame id of the level meter.
 * @property {ReturnType<typeof setTimeout> | undefined} recordingTimer
 * @property {ReturnType<typeof setTimeout> | undefined} reconnectTimer
 * @property {HTMLAudioElement | null} currentAudio
 * @property {boolean} isRecording
 * @property {boolean} isStarting
 * @property {boolean} isStopping
 * @property {boolean} isProcessing  Server is transcribing, thinking or speaking.
 * @property {boolean} stopRequested
 * @property {boolean} recordingRequested
 * @property {boolean} discardRecording  Cancel (Esc): on stop, tell the server to drop the audio.
 * @property {boolean} awaitingAudio  True after `tts.chunk`, until its binary frame arrives.
 * @property {number} recordedBytes
 */

/** @type {AppState} */
export const state = {
  socket: null,
  connected: false,
  sessionId: null,
  agentName: "",
  serverState: "idle",
  lastMetrics: null,
  muted: false,
  mediaRecorder: null,
  microphoneStream: null,
  audioContext: null,
  analyser: null,
  meterId: null,
  recordingTimer: undefined,
  reconnectTimer: undefined,
  currentAudio: null,
  isRecording: false,
  isStarting: false,
  isStopping: false,
  isProcessing: false,
  stopRequested: false,
  recordingRequested: false,
  discardRecording: false,
  awaitingAudio: false,
  recordedBytes: 0,
};
