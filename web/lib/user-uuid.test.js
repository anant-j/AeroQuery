import test from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { getUserUuid, UserUuidStorageError } from "../app/lib/user-uuid.js";

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
    assert.equal(values.get("aeroquery.userUuid"), first);
    assert.equal(getUserUuid(), first);
    values.set("aeroquery.userUuid", "invalid");
    const repaired = getUserUuid();
    assert.match(repaired, /^[0-9a-f-]{36}$/);
    assert.equal(values.get("aeroquery.userUuid"), repaired);
    values.clear();
    assert.notEqual(getUserUuid(), first);
  } finally {
    globalThis.window = previousWindow;
    if (cryptoDescriptor) Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    else delete globalThis.crypto;
  }
});

test("retries transient storage failures and reports persistent failures", () => {
  const previousWindow = globalThis.window;
  const cryptoDescriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  const originalError = console.error;
  const errors = [];
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  const values = new Map();
  let failures = 1;
  globalThis.window = { localStorage: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      if (failures-- > 0) throw new Error("blocked");
      values.set(key, value);
    },
  } };
  console.error = (...args) => errors.push(args);
  try {
    assert.equal(getUserUuid(), values.get("aeroquery.userUuid"));
    globalThis.window = { localStorage: { getItem: () => { throw new Error("blocked"); } } };
    assert.throws(() => getUserUuid(), UserUuidStorageError);
    assert.equal(errors.length, 1);
  } finally {
    console.error = originalError;
    globalThis.window = previousWindow;
    if (cryptoDescriptor) Object.defineProperty(globalThis, "crypto", cryptoDescriptor);
    else delete globalThis.crypto;
  }
});
