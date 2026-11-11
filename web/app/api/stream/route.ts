import { PayloadTooLargeError, readJsonBody, validateQueryPayload } from "../../lib/security.js";
import { getCloudflareWorkerConfig, getClientIp } from "../../lib/cloudflare-worker";

export async function POST(req: Request) {
  const clientIp = getClientIp(req);
  let body: unknown;
  try {
    body = await readJsonBody(req);
  } catch (error) {
    return new Response(JSON.stringify({ error: error instanceof Error ? error.message : "Invalid request" }), {
      status: error instanceof PayloadTooLargeError ? 413 : 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  let payload: ReturnType<typeof validateQueryPayload>;
  try {
    payload = validateQueryPayload(body);
  } catch (error) {
    return new Response(JSON.stringify({ error: error instanceof Error ? error.message : "Invalid request" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const workerConfig = getCloudflareWorkerConfig();
  if (!workerConfig) {
    return new Response(JSON.stringify({ error: "Cloudflare Worker configuration is missing" }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const res = await fetch(`${workerConfig.apiUrl}/api/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-aeroquery-key": workerConfig.workerKey,
        "x-aeroquery-client-ip": clientIp,
      },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    return new Response(res.body, {
      status: res.status,
      headers: {
        "Content-Type": res.headers.get("Content-Type") || "application/json",
        "Cache-Control": res.headers.get("Cache-Control") || "no-cache, no-transform",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch {
    return new Response(JSON.stringify({ error: "Cloudflare generation request failed" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
