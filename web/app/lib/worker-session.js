export function createWorkerSession(apiUrl, apiKey, getTurnstileToken, fetcher = fetch) {
  let session;
  let pending;
  return async function getSession() {
    if (session && session.expires_at * 1000 > Date.now() + 10000) return session.token;
    if (!pending) {
      pending = (async () => {
        const missing = [
          !apiUrl && "NEXT_PUBLIC_CLOUDFLARE_WORKER_API_URL",
          !apiKey && "NEXT_PUBLIC_CLOUDFLARE_WORKER_KEY",
        ].filter(Boolean);
        if (missing.length) throw new Error(`Missing build environment variables: ${missing.join(", ")}.`);
        const endpoint = new URL(apiUrl);
        if (endpoint.protocol !== "https:" &&
            !(endpoint.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(endpoint.hostname))) {
          throw new Error("Worker URL must use HTTPS.");
        }
        const token = await getTurnstileToken();
        const response = await fetcher(`${apiUrl}/auth/anonymous`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-aeroquery-key": apiKey },
          body: JSON.stringify({ turnstile_token: token }),
          cache: "no-store",
        });
        if (!response.ok) {
          const result = await response.json().catch(() => null);
          throw new Error(result?.error || "Verification failed.");
        }
        const result = await response.json();
        if (typeof result.token !== "string" || !result.token ||
            typeof result.expires_at !== "number" || result.expires_at * 1000 <= Date.now()) {
          throw new Error("Invalid verification response.");
        }
        session = result;
        return session.token;
      })().finally(() => { pending = undefined; });
    }
    return pending;
  };
}
