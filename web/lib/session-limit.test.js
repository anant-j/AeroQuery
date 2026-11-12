import test from "node:test";
import assert from "node:assert/strict";
import { MAX_SESSION_REQUESTS, consumeSessionRequest, remainingSessionRequests } from "../app/lib/session-limit.js";

test("limits requests in browser storage", () => {
  const values = new Map();
  global.window = { localStorage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) } };
  try {
    for (let count = 0; count < MAX_SESSION_REQUESTS; count++) {
      assert.equal(consumeSessionRequest(), true);
      assert.equal(remainingSessionRequests(), MAX_SESSION_REQUESTS - count - 1);
    }
    assert.equal(consumeSessionRequest(), false);
  } finally {
    delete global.window;
  }
});

test("fails closed when storage is malformed or unavailable", () => {
  global.window = { localStorage: { getItem: () => "-1" } };
  assert.equal(remainingSessionRequests(), 0);
  global.window = { localStorage: { getItem: () => { throw new Error("unavailable"); } } };
  assert.equal(consumeSessionRequest(), false);
  delete global.window;
});
