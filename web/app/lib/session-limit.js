import { MAX_SESSION_REQUESTS, STORAGE_KEY, REQUEST_LIMIT_TTL } from "./constants.js";

export { MAX_SESSION_REQUESTS } from "./constants.js";

function readRequestLimit(storage) {
  const now = Date.now();
  const stored = storage.getItem(STORAGE_KEY);
  if (stored === null) return { count: 0, expiresAt: now + REQUEST_LIMIT_TTL };

  const state = JSON.parse(stored);
  if (!state || !Number.isSafeInteger(state.count) || state.count < 0 ||
      state.count > MAX_SESSION_REQUESTS || !Number.isSafeInteger(state.expiresAt)) {
    return null;
  }
  if (state.expiresAt <= now) return { count: 0, expiresAt: now + REQUEST_LIMIT_TTL };
  return state;
}

export function remainingSessionRequests() {
  try {
    const state = readRequestLimit(window.localStorage);
    return state ? MAX_SESSION_REQUESTS - state.count : 0;
  } catch {
    return 0;
  }
}

export function consumeSessionRequest() {
  try {
    const storage = window.localStorage;
    const state = readRequestLimit(storage);
    if (!state || state.count >= MAX_SESSION_REQUESTS) return false;
    storage.setItem(STORAGE_KEY, JSON.stringify({ count: state.count + 1, expiresAt: state.expiresAt }));
    return true;
  } catch {
    return false;
  }
}