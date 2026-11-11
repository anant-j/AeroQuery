export function getCloudflareWorkerConfig() {
  const apiUrl = process.env.CLOUDFLARE_WORKER_API_URL;
  const workerKey = process.env.CLOUDFLARE_WORKER_KEY;

  if (!apiUrl || !workerKey || workerKey.length < 32) return null;

  let endpoint: URL;
  try {
    endpoint = new URL(apiUrl);
  } catch {
    return null;
  }
  if (endpoint.protocol !== "https:" && !["localhost", "127.0.0.1", "::1"].includes(endpoint.hostname)) {
    return null;
  }

  return {
    apiUrl: endpoint.toString().replace(/\/+$/, ""),
    workerKey,
  };
}

export function getClientIp(request: Request) {
  return request.headers.get("x-nf-client-connection-ip")?.trim() || "unknown";
}