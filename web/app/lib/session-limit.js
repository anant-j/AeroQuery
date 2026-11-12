import { MAX_SESSION_REQUESTS, STORAGE_KEY } from "./constants.js";

export { MAX_SESSION_REQUESTS } from "./constants.js";

export function remainingSessionRequests(storage) {
  try {
    const stored = storage.getItem(STORAGE_KEY);
    if (stored === null) return MAX_SESSION_REQUESTS;
    if (!/^[0-8]$/.test(stored)) return 0;
    return MAX_SESSION_REQUESTS - Number(stored);
  } catch {
    return 0;
  }
}

export function consumeSessionRequest(storage) {
  const remaining = remainingSessionRequests(storage);
  if (remaining === 0) return false;
  try {
    storage.setItem(STORAGE_KEY, String(MAX_SESSION_REQUESTS - remaining + 1));
    return true;
  } catch {
    return false;
  }
}