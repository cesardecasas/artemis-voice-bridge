// @ts-check

/**
 * localStorage that never throws (private mode, blocked storage).
 * @param {string} key
 * @returns {string | null}
 */
export function loadStored(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/**
 * @param {string} key
 * @param {string | null} value  null removes the key
 */
export function store(key, value) {
  try {
    if (value === null) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
  } catch {
    // Storage unavailable; the value just won't survive reloads.
  }
}
