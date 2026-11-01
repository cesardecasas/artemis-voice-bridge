// @ts-check
import { state } from "./state.js";

/** @typedef {import("./types.js").ClientMessage} ClientMessage */

/** @returns {boolean} */
export function isConnected() {
  return state.socket !== null && state.socket.readyState === WebSocket.OPEN;
}

/**
 * Send a typed protocol message. Returns false if the socket is not open.
 * @param {ClientMessage} message
 * @returns {boolean}
 */
export function sendMessage(message) {
  if (!isConnected() || !state.socket) {
    return false;
  }
  state.socket.send(JSON.stringify(message));
  return true;
}

/**
 * Send binary audio: a WebM chunk (between audio.start and audio.end) or a
 * hands-free PCM16 frame (after handsfree.start).
 * @param {Blob | ArrayBuffer} chunk
 * @returns {boolean}
 */
export function sendAudioChunk(chunk) {
  if (!isConnected() || !state.socket) {
    return false;
  }
  state.socket.send(chunk);
  return true;
}
