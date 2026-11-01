// @ts-check

export const MIME_TYPE = "audio/webm;codecs=opus";
export const MAX_RECORDING_MS = 60000;
export const CONNECTION_RETRY_MS = 3000;
export const VISUALIZER_BARS = 20;
export const SESSION_STORAGE_KEY = "artemis.session_id";
export const TOKEN_STORAGE_KEY = "artemis.access_token";

/** WebSocket close codes used by the server (see artemis/protocol.py). */
export const CLOSE_CODES = {
  unauthorized: 4401,
  helloTimeout: 4408,
  tryAgainLater: 1013,
};
export const BUSY_RETRY_MS = 10000;

export const COLORS = {
  ready: "#6ef2c4",
  busy: "#7bd0ff",
  error: "#ff5c7a",
  neutral: "#ffffff",
};
