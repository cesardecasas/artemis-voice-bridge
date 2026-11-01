// @ts-check
/**
 * Shared JSDoc type definitions for the Artemis frontend.
 *
 * These mirror the server message TypedDicts in `main.py`. Keep them in sync.
 * This file has no runtime code; other modules import the types via
 * `@typedef {import("./types.js").ServerMessage}`.
 */

/**
 * @typedef {object} AudioInfo
 * @property {"wav"} format
 * @property {number} bytes
 */

/**
 * @typedef {object} StatusMessage
 * @property {"status"} type
 * @property {string} text
 */

/**
 * @typedef {object} TranscriptionMessage
 * @property {"transcription"} type
 * @property {string} text
 */

/**
 * Agent reply. When `audio` is non-null, exactly one binary WAV frame follows.
 * @typedef {object} ResponseMessage
 * @property {"response"} type
 * @property {string} text
 * @property {AudioInfo | null} audio
 */

/**
 * @typedef {object} ErrorMessage
 * @property {"error"} type
 * @property {string} text
 */

/** @typedef {StatusMessage | TranscriptionMessage | ResponseMessage | ErrorMessage} ServerMessage */

/** @typedef {"user" | "artemis"} Speaker */

export {};
