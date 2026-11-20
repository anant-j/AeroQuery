import test from "node:test";
import assert from "node:assert/strict";
import { formatRelativeDelta } from "../app/lib/benchmark-delta.js";

test("formats relative score changes against the bare baseline", () => {
  assert.equal(formatRelativeDelta(0.464, 0.604), "+30.2%");
  assert.equal(formatRelativeDelta(0.464, 0.597), "+28.7%");
  assert.equal(formatRelativeDelta(0.5, 0.4), "-20.0%");
  assert.equal(formatRelativeDelta(0.5, 0.5), "0.0%");
  assert.equal(formatRelativeDelta(0.5, 0.499999), "0.0%");
});

test("reports unavailable changes when the baseline is missing or zero", () => {
  assert.equal(formatRelativeDelta(null, 0.9), "N/A");
  assert.equal(formatRelativeDelta(0, 0.9), "N/A");
});
