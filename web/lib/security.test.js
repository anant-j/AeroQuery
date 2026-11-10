import test from "node:test";
import assert from "node:assert/strict";

import { MAX_BODY_BYTES, PayloadTooLargeError, readJsonBody, validateQueryPayload } from "../app/lib/security.js";

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
