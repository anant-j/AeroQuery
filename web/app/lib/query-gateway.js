/**
 * Multiplex two independent backend SSE streams without buffering either answer.
 * @param {string} apiUrl
 * @param {string} workerKey
 * @param {string} sessionToken
 * @param {string} query
 * @param {AbortSignal} signal
 * @param {typeof fetch} fetcher
 */
export function streamComparison(apiUrl, workerKey, sessionToken, query, signal, fetcher = fetch) {
  const abort = new AbortController();
  const encoder = new TextEncoder();
  const states = ["rag", "bare"].map((mode) => ({
    mode,
    reader: /** @type {ReadableStreamDefaultReader<Uint8Array> | undefined} */ (undefined),
    decoder: new TextDecoder(),
    next: /** @type {Promise<{ state: typeof states[number], chunk?: string, done?: boolean, error?: string }> | undefined} */ (undefined),
  }));
  const onAbort = () => abort.abort();
  if (signal.aborted) abort.abort();
  else signal.addEventListener("abort", onAbort, { once: true });

  async function read(state) {
    try {
      if (!state.reader) {
        const response = await fetcher(`${apiUrl}/api/stream`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-aeroquery-key": workerKey,
            "Authorization": `Bearer ${sessionToken}`,
          },
          body: JSON.stringify({ query, use_rag: state.mode === "rag" }),
          cache: "no-store",
          signal: abort.signal,
        });
        if (!response.ok) {
          const result = await response.json().catch(() => null);
          return { state, error: result?.error || `Generation request failed (${response.status})` };
        }
        if (!response.body) return { state, error: "Generation response has no body" };
        state.reader = response.body.getReader();
      }
      const { done, value } = await state.reader.read();
      const chunk = state.decoder.decode(value, { stream: !done });
      return { state, chunk, done };
    } catch (error) {
      if (!abort.signal.aborted) console.error("Cloudflare generation stream failed", error);
      return { state, error: "Cloudflare generation request failed" };
    }
  }

  for (const state of states) state.next = read(state);

  return new ReadableStream({
    async pull(controller) {
      while (true) {
        const active = states.filter((state) => state.next);
        if (!active.length) {
          signal.removeEventListener("abort", onAbort);
          controller.close();
          return;
        }
        const { state, chunk, done, error } = await Promise.race(active.map((state) => state.next));
        state.next = done || error ? undefined : read(state);
        if (chunk || error) {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ mode: state.mode, ...(chunk && { chunk }), ...(error && { error }) })}\n\n`));
          return;
        }
      }
    },
    async cancel() {
      signal.removeEventListener("abort", onAbort);
      abort.abort();
      await Promise.all(states.map((state) => state.reader?.cancel().catch(() => {})));
    },
  });
}
