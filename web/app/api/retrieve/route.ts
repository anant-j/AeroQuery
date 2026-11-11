import { getCloudflareWorkerConfig, getClientIp } from "../../lib/cloudflare-worker";
import { PayloadTooLargeError, readJsonBody, validateQueryPayload } from "../../lib/security.js";

export async function POST(req: Request) {
  const clientIp = getClientIp(req);
  let payload: ReturnType<typeof validateQueryPayload>;
  try {
    payload = validateQueryPayload(await readJsonBody(req));
  } catch (error) {
    const tooLarge = error instanceof PayloadTooLargeError;
    return new Response(JSON.stringify({ error: error instanceof Error ? error.message : "Invalid request" }), {
      status: tooLarge ? 413 : 400,
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
    const res = await fetch(`${workerConfig.apiUrl}/api/retrieve`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-aeroquery-key": workerConfig.workerKey,
        "x-aeroquery-client-ip": clientIp,
      },
      body: JSON.stringify({ query: payload.query }),
      cache: "no-store",
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("Content-Type") || "application/json" },
    });
  } catch {
    return new Response(JSON.stringify({ error: "Cloudflare retrieval request failed" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
