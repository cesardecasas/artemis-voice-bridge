// @ts-check

/**
 * Browser runtime state shared across modules.
 *
 * @typedef {object} AppState
 * @property {WebSocket | null} socket
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
 * @property {boolean} isProcessing
 * @property {boolean} stopRequested
 * @property {boolean} recordingRequested
 * @property {boolean} awaitingAudio  True after a `response` with audio, until its binary frame arrives.
 * @property {number} recordedBytes
 */

/** @type {AppState} */
export const state = {
  socket: null,
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
  awaitingAudio: false,
  recordedBytes: 0,
};
