import { getContext } from "@netlify/functions";
import { RequestSecurityError } from "./security.js";

export function getCloudflareWorkerConfig() {
  const apiUrl = process.env.CLOUDFLARE_WORKER_API_URL;
  const workerKey = process.env.CLOUDFLARE_WORKER_KEY;

  if (!apiUrl || !workerKey || workerKey.length < 32) return null;

  let endpoint: URL;
  try {
    endpoint = new URL(apiUrl);
  } catch {
    return null;
  }
  if (endpoint.protocol !== "https:" && !["localhost", "127.0.0.1", "::1"].includes(endpoint.hostname)) {
    return null;
  }

  return {
    apiUrl: endpoint.toString().replace(/\/+$/, ""),
    workerKey,
  };
}

export function getClientIp() {
  try {
    const clientIp = getContext().ip;
    if (clientIp) return clientIp;
  } catch {
    if (process.env.NODE_ENV !== "production") return "unknown";
  }
  if (process.env.NODE_ENV !== "production") return "unknown";
  throw new RequestSecurityError("Trusted client IP is unavailable", 503);
}