import test from "node:test";
import assert from "node:assert/strict";
import { createWorkerSession } from "../app/lib/worker-session.js";

test("exchanges once, shares pending verification, and refreshes an expired session", async () => {
  let calls = 0;
  let expiry = Math.floor(Date.now() / 1000) + 60;
  const getToken = async () => { calls++; return "turnstile-token"; };
  const requests = [];
  const session = createWorkerSession("https://worker.test", "public-key", getToken, async (url, options) => {
    requests.push({ url, options });
    return Response.json({ token: `jwt-${calls}`, expires_at: expiry });
  });
  assert.deepEqual(await Promise.all([session(), session()]), ["jwt-1", "jwt-1"]);
  assert.equal(await session(), "jwt-1");
  assert.equal(requests[0].url, "https://worker.test/auth/anonymous");
  assert.equal(requests[0].options.headers["x-aeroquery-key"], "public-key");
  assert.deepEqual(JSON.parse(requests[0].options.body), { turnstile_token: "turnstile-token" });
  expiry = Math.floor(Date.now() / 1000) + 120;
  const now = Date.now;
  try {
    Date.now = () => now() + 55000;
    assert.equal(await session(), "jwt-2");
  } finally {
    Date.now = now;
  }
});

test("failed exchanges surface errors and can be retried", async () => {
  let calls = 0;
  const session = createWorkerSession("https://worker.test", "key", async () => "token", async () => {
    calls++;
    return calls === 1 ? Response.json({ error: "Verification failed" }, { status: 403 })
      : Response.json({ token: "jwt", expires_at: Math.floor(Date.now() / 1000) + 60 });
  });
  await assert.rejects(session(), /Verification failed/);
  assert.equal(await session(), "jwt");
});

test("refuses to send tokens or keys over insecure non-local URLs", async () => {
  let called = false;
  const session = createWorkerSession("http://worker.test", "key", async () => { called = true; return "token"; });
  await assert.rejects(session(), /must use HTTPS/);
  assert.equal(called, false);
});

test("identifies missing build-time Worker configuration", async () => {
  const missingUrl = createWorkerSession("", "key", async () => "token");
  await assert.rejects(missingUrl(), /NEXT_PUBLIC_CLOUDFLARE_WORKER_API_URL/);
  const missingKey = createWorkerSession("https://worker.test", "", async () => "token");
  await assert.rejects(missingKey(), /NEXT_PUBLIC_CLOUDFLARE_WORKER_KEY/);
  const missingBoth = createWorkerSession("", "", async () => "token");
  await assert.rejects(missingBoth(), /NEXT_PUBLIC_CLOUDFLARE_WORKER_API_URL, NEXT_PUBLIC_CLOUDFLARE_WORKER_KEY/);
});
