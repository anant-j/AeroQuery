const WINDOW_MS = 60_000;
const MAX_REQUESTS = 30;
const MAX_CLIENT_BUCKETS = 8_192;
const requestTimes = new Map<string, number[]>();
let nextPruneAt = 0;

export function isRateLimited(clientIp: string) {
  const now = Date.now();

  if (now >= nextPruneAt) {
    for (const [ip, timestamps] of requestTimes) {
      const recent = timestamps.filter((timestamp) => now - timestamp < WINDOW_MS);
      if (recent.length) requestTimes.set(ip, recent);
      else requestTimes.delete(ip);
    }
    nextPruneAt = now + WINDOW_MS;
  }

  const key = clientIp || "unknown";
  let timestamps = requestTimes.get(key);
  if (!timestamps && requestTimes.size >= MAX_CLIENT_BUCKETS) return true;
  timestamps ??= [];

  if (timestamps.length >= MAX_REQUESTS) return true;
  timestamps.push(now);
  requestTimes.set(key, timestamps);
  return false;
}
