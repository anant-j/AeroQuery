import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { getUserUuid } from "../app/lib/user-uuid.js";

test("user UUID survives browser reload and changes after site data is cleared", () => {
  const values = new Map();
  const previousWindow = globalThis.window;
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  globalThis.window = {
    localStorage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    },
  };
  try {
    const first = getUserUuid();
    assert.match(first, /^[0-9a-f-]{36}$/);
    assert.equal(getUserUuid(), first);
    values.clear();
    assert.notEqual(getUserUuid(), first);
  } finally {
    globalThis.window = previousWindow;
    if (cryptoDescriptor) Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    else delete globalThis.crypto;
  }
});

test("legacy user key is migrated without changing the UUID", () => {
  const uuid = "12345678-1234-4234-8234-123456789abc";
  const values = new Map([["aeroquery_user_key", uuid]]);
  const previousWindow = globalThis.window;
  globalThis.window = {
    localStorage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
      removeItem: (key) => values.delete(key),
    },
  };
  try {
    assert.equal(getUserUuid(), uuid);
    assert.equal(values.get("aeroquery_user_uuid"), uuid);
    assert.equal(values.has("aeroquery_user_key"), false);
  } finally {
    globalThis.window = previousWindow;
  }
});

test("storage failure is reported and uses a page-lifetime UUID", () => {
  const previousWindow = globalThis.window;
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const originalError = console.error;
  const errors = [];
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  globalThis.window = { localStorage: { getItem: () => { throw new Error("blocked"); } } };
  console.error = (...args) => errors.push(args);
  try {
    assert.equal(getUserUuid(), getUserUuid());
    assert.equal(errors.length, 2);
  } finally {
    console.error = originalError;
    globalThis.window = previousWindow;
    if (cryptoDescriptor) Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    else delete globalThis.crypto;
  }
});
