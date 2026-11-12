import test from "node:test";
import assert from "node:assert/strict";
import { streamComparison } from "../app/lib/query-gateway.js";

async function events(stream) {
  const text = await new Response(stream).text();
  return text.split("\n").filter((line) => line.startsWith("data: ")).map((line) => JSON.parse(line.slice(6)));
}

test("comparison sends two authenticated requests and multiplexes their SSE bytes", async () => {
  const requests = [];
  const fetcher = async (url, options) => {
    requests.push({ url, options });
    const rag = JSON.parse(options.body).use_rag;
    const bytes = new TextEncoder().encode(`data: {"type":"token","token":"${rag ? "é" : "bare"}"}\n\ndata: {"type":"done"}\n\n`);
    return new Response(new ReadableStream({
      start(controller) {
        const split = rag ? bytes.indexOf(195) + 1 : 7;
        controller.enqueue(bytes.slice(0, split));
        controller.enqueue(bytes.slice(split));
        controller.close();
      },
    }));
  };
  const result = await events(streamComparison("https://worker.test", "key", "127.0.0.1", "question", new AbortController().signal, fetcher));
  assert.equal(requests.length, 2);
  assert.deepEqual(requests.map(({ options }) => JSON.parse(options.body).use_rag).sort(), [false, true]);
  for (const { url, options } of requests) {
    assert.equal(url, "https://worker.test/api/stream");
    assert.equal(options.headers["x-aeroquery-key"], "key");
    assert.equal(options.headers.Authorization, "Bearer 127.0.0.1");
    assert.equal(JSON.parse(options.body).query, "question");
  }
  for (const [mode, token] of [["rag", "é"], ["bare", "bare"]]) {
    const text = result.filter((event) => event.mode === mode).map((event) => event.chunk).join("");
    assert.match(text, new RegExp(`"token":"${token}"`));
    assert.match(text, /"type":"done"/);
  }
});

test("a backend error is surfaced with its stream identity", async () => {
  const fetcher = async (_url, options) => JSON.parse(options.body).use_rag
    ? Response.json({ error: "Rate limited" }, { status: 429 })
    : new Response('data: {"type":"done"}\n\n');
  const result = await events(streamComparison("https://worker.test", "key", "ip", "question", new AbortController().signal, fetcher));
  assert.ok(result.some((event) => event.mode === "rag" && event.error === "Rate limited"));
  assert.ok(result.some((event) => event.mode === "bare" && event.chunk.includes('"done"')));
});

test("a slow backend does not block the other stream", async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const fetcher = async (_url, options) => {
    if (JSON.parse(options.body).use_rag) await slow;
    return new Response('data: {"type":"done"}\n\n');
  };
  const reader = streamComparison("https://worker.test", "key", "ip", "question", new AbortController().signal, fetcher).getReader();
  const first = await reader.read();
  assert.match(new TextDecoder().decode(first.value), /"mode":"bare"/);
  release();
  while (!(await reader.read()).done) {}
});

test("cancelling the response aborts both backend requests", async () => {
  const signals = [];
  const fetcher = (_url, options) => {
    signals.push(options.signal);
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
    });
  };
  const stream = streamComparison("https://worker.test", "key", "ip", "question", new AbortController().signal, fetcher);
  await stream.cancel();
  assert.equal(signals.length, 2);
  assert.ok(signals.every((signal) => signal.aborted));
});
