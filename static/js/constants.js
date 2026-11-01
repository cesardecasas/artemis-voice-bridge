// @ts-check

export const MIME_TYPE = "audio/webm;codecs=opus";
export const MAX_RECORDING_MS = 60000;
export const CONNECTION_RETRY_MS = 3000;
export const BUSY_RETRY_MS = 10000;
export const HEALTH_POLL_MS = 15000;
export const VISUALIZER_BARS = 32;
/** Must match APPROVAL_TIMEOUT_S in artemis/ws.py. */
export const APPROVAL_TIMEOUT_MS = 120000;
/** Keep the activity feed bounded. */
export const MAX_ACTIVITY_ITEMS = 40;

export const SESSION_STORAGE_KEY = "artemis.session_id";
export const TOKEN_STORAGE_KEY = "artemis.access_token";
export const MUTED_STORAGE_KEY = "artemis.muted";

/** WebSocket close codes used by the server (see artemis/protocol.py). */
export const CLOSE_CODES = {
  unauthorized: 4401,
  helloTimeout: 4408,
  tryAgainLater: 1013,
};

/** Moonlight palette (mirrors the CSS custom properties in css/artemis.css). */
export const COLORS = {
  ready: "#6ef2c4",
  dusk: "#a9b4ff",
  busy: "#7bd0ff",
  amber: "#ffc45c",
  error: "#ff8094",
  neutral: "#93a9bb",
};
