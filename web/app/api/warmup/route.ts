import { getAzureFunctionConfig, getClientIp } from "../../lib/azure-functions";
import { isRateLimited } from "../../lib/rate-limit";

export async function GET(req: Request) {
  const clientIp = getClientIp(req);
  if (isRateLimited(clientIp)) {
    return new Response(JSON.stringify({ status: "rate_limited" }), {
      status: 429,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }

  const functionConfig = getAzureFunctionConfig();
  if (!functionConfig) {
    return new Response(JSON.stringify({ status: "unconfigured" }), {
      status: 503,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }

  try {
    const res = await fetch(`${functionConfig.apiUrl}/warmup`, {
      method: "GET",
      headers: { "x-functions-key": functionConfig.functionKey },
      signal: AbortSignal.timeout(45_000),
      cache: "no-store",
    });
    return new Response(JSON.stringify({ status: res.ok ? "warm" : "error" }), {
      status: res.ok ? 200 : 502,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return new Response(JSON.stringify({ status: "error" }), {
      status: 502,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  }
}
