// @ts-check

/** @typedef {import("./types.js").PipelineState} PipelineState */
/** @typedef {import("./types.js").MetricsMessage} MetricsMessage */

/**
 * Browser runtime state shared across modules.
 *
 * @typedef {object} AppState
 * @property {WebSocket | null} socket
 * @property {string | null} sessionId  Server session; persisted so reloads keep context.
 * @property {PipelineState} serverState  Last `state` event from the server.
 * @property {MetricsMessage | null} lastMetrics
 * @property {MediaRecorder | null} mediaRecorder
 * @property {MediaStream | null} microphoneStream
 * @property {AudioContext | null} audioContext
 * @property {AnalyserNode | null} analyser
 * @property {Uint8Array<ArrayBuffer> | null} dataArray
 * @property {number | null} animationId
 * @property {number | null} playbackAnimationId
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
  sessionId: null,
  serverState: "idle",
  lastMetrics: null,
  mediaRecorder: null,
  microphoneStream: null,
  audioContext: null,
  analyser: null,
  dataArray: null,
  animationId: null,
  playbackAnimationId: null,
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
