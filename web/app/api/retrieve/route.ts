import { getAzureFunctionConfig, getClientIp } from "../../lib/azure-functions";
import { isRateLimited } from "../../lib/rate-limit";
import { PayloadTooLargeError, readJsonBody, validateQueryPayload } from "../../lib/security.js";

export async function POST(req: Request) {
  const clientIp = getClientIp(req);
  if (isRateLimited(clientIp)) {
    return new Response(JSON.stringify({ error: "Too many requests. Please wait a minute and try again." }), {
      status: 429,
      headers: { "Content-Type": "application/json" },
    });
  }

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

  const functionConfig = getAzureFunctionConfig();
  if (!functionConfig) {
    return new Response(JSON.stringify({ error: "Azure Function configuration is missing" }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const res = await fetch(`${functionConfig.apiUrl}/retrieve`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-functions-key": functionConfig.functionKey,
        "x-aeroquery-client-ip": clientIp,
      },
      body: JSON.stringify({ query: payload.query }),
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "Content-Type": res.headers.get("Content-Type") || "application/json" },
    });
  } catch {
    return new Response(JSON.stringify({ error: "Azure retrieval request failed" }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
