import { MAX_SESSION_REQUESTS, STORAGE_KEY, REQUEST_LIMIT_TTL } from "./constants.js";

export { MAX_SESSION_REQUESTS } from "./constants.js";

const MAX_HOURLY_REQUESTS = 60;
const HOUR_MS = 60 * 60 * 1000;

function readRequestLimit(storage) {
  const now = Date.now();
  const stored = storage.getItem(STORAGE_KEY);
  if (stored === null) return { count: 0, expiresAt: now + REQUEST_LIMIT_TTL, hourlyRequests: [] };

  const state = JSON.parse(stored);
  if (!state || !Number.isSafeInteger(state.count) || state.count < 0 ||
      state.count > MAX_SESSION_REQUESTS || !Number.isSafeInteger(state.expiresAt)) {
    return null;
  }
  const hourlyRequests = state.hourlyRequests === undefined ? [] : state.hourlyRequests;
  if (!Array.isArray(hourlyRequests) || hourlyRequests.length > MAX_HOURLY_REQUESTS ||
      hourlyRequests.some((timestamp) => !Number.isSafeInteger(timestamp) || timestamp < 0)) {
    return null;
  }
  return {
    count: state.expiresAt <= now ? 0 : state.count,
    expiresAt: state.expiresAt <= now ? now + REQUEST_LIMIT_TTL : state.expiresAt,
    hourlyRequests: hourlyRequests.filter((timestamp) => timestamp > now - HOUR_MS).sort((a, b) => a - b),
  };
}

export function remainingSessionRequests() {
  try {
    const state = readRequestLimit(window.localStorage);
    return state ? Math.min(MAX_SESSION_REQUESTS - state.count, MAX_HOURLY_REQUESTS - state.hourlyRequests.length) : 0;
  } catch {
    return 0;
  }
}

export function sessionRequestResetSeconds(requestsNeeded = 2) {
  try {
    const state = readRequestLimit(window.localStorage);
    if (!state) return null;
    const now = Date.now();
    const sessionReset = MAX_SESSION_REQUESTS - state.count < requestsNeeded ? state.expiresAt : now;
    const hourlyShortfall = requestsNeeded - (MAX_HOURLY_REQUESTS - state.hourlyRequests.length);
    const hourlyReset = hourlyShortfall > 0 ? state.hourlyRequests[hourlyShortfall - 1] + HOUR_MS : now;
    const reset = Math.max(sessionReset, hourlyReset);
    return reset > now ? Math.ceil((reset - now) / 1000) : null;
  } catch {
    return null;
  }
}

export function consumeSessionRequest() {
  try {
    const storage = window.localStorage;
    const state = readRequestLimit(storage);
    if (!state || state.count >= MAX_SESSION_REQUESTS || state.hourlyRequests.length >= MAX_HOURLY_REQUESTS) return false;
    storage.setItem(STORAGE_KEY, JSON.stringify({
      count: state.count + 1,
      expiresAt: state.expiresAt,
      hourlyRequests: [...state.hourlyRequests, Date.now()],
    }));
    return true;
  } catch {
    return false;
  }
}