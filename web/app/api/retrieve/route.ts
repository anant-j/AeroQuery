import { getCloudflareWorkerConfig } from "../../lib/cloudflare-worker";
import { readProtectedRequest, securityErrorResponse } from "../../lib/protected-request";

export async function POST(req: Request) {
  let protectedRequest: Awaited<ReturnType<typeof readProtectedRequest>>;
  try {
    protectedRequest = await readProtectedRequest(req);
  } catch (error) {
    return securityErrorResponse(error);
  }
  const { payload, clientIp } = protectedRequest;

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
