import { MAX_QUERY_LENGTH, MAX_BODY_BYTES } from "./constants.js";

export { MAX_QUERY_LENGTH, MAX_BODY_BYTES } from "./constants.js";

export class PayloadTooLargeError extends Error {}
export class RequestSecurityError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export function validateRequestHeaders(request, allowedOrigins) {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") {
    throw new RequestSecurityError("Content-Type must be application/json", 415);
  }
  const origin = request.headers.get("origin");
  if (!allowedOrigins?.length) {
    throw new RequestSecurityError("Allowed origins are not configured", 503);
  }
  if (!origin || !allowedOrigins.includes(origin)) {
    throw new RequestSecurityError("Origin is not allowed", 403);
  }
}

export async function verifyTurnstile(token, { secret, hostname, clientIp, allowTestKeys = false, fetcher = fetch }) {
  if (!secret || (!allowTestKeys && /^[123]x0{30,}/.test(secret))) {
    throw new RequestSecurityError("Verification is not configured", 503);
  }
  if (typeof token !== "string" || !token || token.length > 2048) {
    throw new RequestSecurityError("Verification is required", 403);
  }
  let result;
  try {
    const response = await fetcher("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secret, response: token, ...(clientIp && { remoteip: clientIp }) }),
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("Verification unavailable");
    result = await response.json();
  } catch {
    throw new RequestSecurityError("Verification is unavailable", 503);
  }
  const isTestResponse = allowTestKeys && /^[123]x0{30,}/.test(secret) && result.hostname === "example.com";
  if (!result.success || (!isTestResponse && (result.hostname !== hostname || result.action !== "aeroquery"))) {
    throw new RequestSecurityError("Verification failed", 403);
  }
}

export async function readJsonBody(request) {
  const reader = request.body?.getReader();
  const decoder = new TextDecoder();
  let body = "";
  let size = 0;
  if (reader) {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel().catch(() => {});
        throw new PayloadTooLargeError("request body is too large");
      }
      body += decoder.decode(value, { stream: true });
    }
    body += decoder.decode();
  }

  try {
    return JSON.parse(body);
  } catch {
    throw new SyntaxError("Invalid JSON");
  }
}

function normalizeString(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function validateQueryPayload(payload, { maxQueryLength = MAX_QUERY_LENGTH } = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("request body must be a JSON object");
  }

  const query = normalizeString(payload.query);
  if (!query) {
    throw new Error("query is required");
  }

  if (query.length > maxQueryLength) {
    throw new Error(`query must be shorter than ${maxQueryLength} characters`);
  }

  const useRag = payload.use_rag;
  if (useRag !== undefined && typeof useRag !== "boolean") {
    throw new Error("use_rag must be a boolean");
  }

  return {
    query,
    use_rag: useRag === true,
  };
}
