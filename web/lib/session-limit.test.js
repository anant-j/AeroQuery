import test from "node:test";
import assert from "node:assert/strict";
import { MAX_SESSION_REQUESTS, consumeSessionRequest, remainingSessionRequests, sessionRequestResetSeconds } from "../app/lib/session-limit.js";

test("limits requests in browser storage", () => {
  const values = new Map();
  global.window = { localStorage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) } };
  try {
    for (let count = 0; count < 24; count++) {
      assert.equal(consumeSessionRequest(), true);
      assert.equal(remainingSessionRequests(), 24 - count - 1);
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

test("enforces 60 requests in a rolling hour across two-minute windows", () => {
  const values = new Map();
  const originalNow = Date.now;
  const start = 10_000_000;
  let now = start;
  Date.now = () => now;
  global.window = { localStorage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) } };
  try {
    assert.equal(consumeSessionRequest(), true);
    now += 10_000;
    for (let count = 0; count < 23; count++) assert.equal(consumeSessionRequest(), true);
    now = start + 130_000;
    for (let count = 0; count < 24; count++) assert.equal(consumeSessionRequest(), true);
    now = start + 250_000;
    for (let count = 0; count < 12; count++) assert.equal(consumeSessionRequest(), true);
    assert.equal(remainingSessionRequests(), 0);
    assert.equal(consumeSessionRequest(), false);
    assert.equal(sessionRequestResetSeconds(), 3360);

    now = start + 3_600_000;
    assert.equal(remainingSessionRequests(), 1);
    assert.equal(sessionRequestResetSeconds(1), null);
    assert.equal(sessionRequestResetSeconds(2), 10);
    now += 10_000;
    assert.equal(remainingSessionRequests(), 24);
    assert.equal(sessionRequestResetSeconds(2), null);
    for (let count = 0; count < 24; count++) assert.equal(consumeSessionRequest(), true);
    assert.equal(consumeSessionRequest(), false);
  } finally {
    Date.now = originalNow;
    delete global.window;
  }
});

test("countdown waits for both limits and the requested number of slots", () => {
  const originalNow = Date.now;
  const now = 10_000_000;
  Date.now = () => now;
  let state;
  global.window = { localStorage: { getItem: () => JSON.stringify(state) } };
  try {
    state = {
      count: 24,
      expiresAt: now + 90_000,
      hourlyRequests: [now - 3_580_000, now - 3_540_000, ...Array(58).fill(now)],
    };
    assert.equal(sessionRequestResetSeconds(1), 90);
    assert.equal(sessionRequestResetSeconds(2), 90);
    state.expiresAt = now + 10_000;
    assert.equal(sessionRequestResetSeconds(1), 20);
    assert.equal(sessionRequestResetSeconds(2), 60);
  } finally {
    Date.now = originalNow;
    delete global.window;
  }
});

test("fails closed when storage is malformed or unavailable", () => {
  global.window = { localStorage: { getItem: () => "-1" } };
  assert.equal(remainingSessionRequests(), 0);
  global.window = { localStorage: { getItem: () => JSON.stringify({ count: 0, expiresAt: Date.now() + 1_000, hourlyRequests: ["invalid"] }) } };
  assert.equal(remainingSessionRequests(), 0);
  global.window = { localStorage: { getItem: () => { throw new Error("unavailable"); } } };
  assert.equal(consumeSessionRequest(), false);
  delete global.window;
});
