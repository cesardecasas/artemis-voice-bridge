// @ts-check

/** Sent as a binary frame to mark the end of a recording. */
export const END_MARKER = new Uint8Array([0, 0, 0, 0]);
export const MIME_TYPE = "audio/webm;codecs=opus";
export const MAX_RECORDING_MS = 60000;
export const CONNECTION_RETRY_MS = 3000;
export const VISUALIZER_BARS = 20;
