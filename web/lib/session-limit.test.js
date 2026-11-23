import test from "node:test";
import assert from "node:assert/strict";
import { MAX_SESSION_REQUESTS, consumeSessionRequest, remainingSessionRequests, sessionRequestResetSeconds } from "../app/lib/session-limit.js";

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

test("starts a two-minute window and reports its countdown when full", () => {
  const values = new Map();
  const originalNow = Date.now;
  let now = 1_000_000;
  Date.now = () => now;
  global.window = { localStorage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) } };
  try {
    for (let count = 0; count < MAX_SESSION_REQUESTS; count++) {
      assert.equal(consumeSessionRequest(), true);
    }

    assert.equal(JSON.parse(values.get("aeroquery.sessionRequests")).expiresAt, now + 2 * 60 * 1000);
    assert.equal(sessionRequestResetSeconds(), 120);
    now += 119_500;
    assert.equal(sessionRequestResetSeconds(), 1);
    now += 500;
    assert.equal(sessionRequestResetSeconds(), null);
    assert.equal(remainingSessionRequests(), MAX_SESSION_REQUESTS);
  } finally {
    Date.now = originalNow;
    delete global.window;
  }
});

test("reports the reset countdown with one request remaining", () => {
  const originalNow = Date.now;
  let now = 1_000_000;
  Date.now = () => now;
  global.window = { localStorage: { getItem: () => JSON.stringify({ count: MAX_SESSION_REQUESTS - 1, expiresAt: 1_002_000 }) } };
  try {
    assert.equal(remainingSessionRequests(), 1);
    assert.equal(sessionRequestResetSeconds(), 2);
    now += 2_000;
    assert.equal(sessionRequestResetSeconds(), null);
    assert.equal(remainingSessionRequests(), MAX_SESSION_REQUESTS);
  } finally {
    Date.now = originalNow;
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
