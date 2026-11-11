import test from "node:test";
import assert from "node:assert/strict";

import { MAX_BODY_BYTES, PayloadTooLargeError, RequestSecurityError, readJsonBody, validateQueryPayload, validateRequestHeaders, verifyTurnstile } from "../app/lib/security.js";

test("accepts a realistic aviation query", () => {
  const result = validateQueryPayload({ query: "How long after alcohol can I fly?", use_rag: true });
  assert.equal(result.query, "How long after alcohol can I fly?");
  assert.equal(result.use_rag, true);
});

test("rejects empty questions", () => {
  assert.throws(() => validateQueryPayload({ query: "   ", use_rag: true }), /query is required/i);
});

test("rejects oversized payloads", () => {
  const longQuery = "x".repeat(5000);
  assert.throws(() => validateQueryPayload({ query: longQuery, use_rag: true }), /must be shorter/i);
});

test("rejects oversized raw JSON bodies including whitespace", async () => {
  const request = new Request("https://example.test", {
    method: "POST",
    body: `${" ".repeat(MAX_BODY_BYTES)}{}`,
  });
  await assert.rejects(readJsonBody(request), PayloadTooLargeError);
});

test("parses valid JSON request bodies", async () => {
  const request = new Request("https://example.test", {
    method: "POST",
    body: JSON.stringify({ query: "VFR fuel requirements" }),
  });
  assert.deepEqual(await readJsonBody(request), { query: "VFR fuel requirements" });
});

test("enforces JSON and an explicit origin allowlist", () => {
  const request = (origin, contentType) => new Request("https://example.test/api/stream", {
    method: "POST",
    headers: { ...(origin && { origin }), "content-type": contentType },
  });
  const allowed = ["https://example.test"];
  assert.doesNotThrow(() => validateRequestHeaders(request(allowed[0], "application/json; charset=utf-8"), allowed));
  for (const candidate of [request(null, "application/json"), request("https://evil.test", "application/json")]) {
    assert.throws(() => validateRequestHeaders(candidate, allowed), (error) => error instanceof RequestSecurityError && error.status === 403);
  }
  assert.throws(() => validateRequestHeaders(request(allowed[0], "text/plain"), allowed), (error) => error.status === 415);
  assert.throws(() => validateRequestHeaders(request(allowed[0], "application/json"), []), (error) => error.status === 503);
});

test("cancels a streamed body as soon as the limit is exceeded", async () => {
  let cancelled = false;
  let reads = 0;
  const request = { body: { getReader: () => ({
    read: async () => ({ done: false, value: new Uint8Array(++reads === 1 ? MAX_BODY_BYTES : 1) }),
    cancel: async () => { cancelled = true; },
  }) } };
  await assert.rejects(readJsonBody(request), PayloadTooLargeError);
  assert.equal(reads, 2);
  assert.equal(cancelled, true);
});

test("verifies Turnstile success, hostname, action and errors", async () => {
  const options = { secret: "production-secret", hostname: "example.test", clientIp: "127.0.0.1" };
  const response = (result) => async () => Response.json(result);
  await verifyTurnstile("valid", { ...options, fetcher: response({ success: true, hostname: "example.test", action: "aeroquery" }) });
  for (const result of [
    { success: false },
    { success: true, hostname: "evil.test", action: "aeroquery" },
    { success: true, hostname: "example.test", action: "other" },
  ]) {
    await assert.rejects(verifyTurnstile("token", { ...options, fetcher: response(result) }), (error) => error.status === 403);
  }
  await assert.rejects(verifyTurnstile("", options), (error) => error.status === 403);
  await assert.rejects(verifyTurnstile("token", { ...options, fetcher: async () => { throw new Error("offline"); } }), (error) => error.status === 503);
  await assert.rejects(verifyTurnstile("token", { ...options, secret: "1x0000000000000000000000000000000AA" }), (error) => error.status === 503);
});
