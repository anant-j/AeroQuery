import { getClientIp } from "./cloudflare-worker";
import { PayloadTooLargeError, RequestSecurityError, readJsonBody, validateQueryPayload, validateRequestHeaders, verifyTurnstile } from "./security.js";

export async function readProtectedRequest(request: Request) {
  const allowedOrigins = (process.env.ALLOWED_ORIGINS || "").split(",").map((origin) => origin.trim()).filter(Boolean);
  validateRequestHeaders(request, allowedOrigins);
  const body = await readJsonBody(request);
  const payload = validateQueryPayload(body);
  const clientIp = getClientIp();
  await verifyTurnstile(body.turnstile_token, {
    secret: process.env.TURNSTILE_SECRET_KEY,
    hostname: new URL(request.headers.get("origin")!).hostname,
    clientIp: clientIp === "unknown" ? undefined : clientIp,
    allowTestKeys: process.env.NODE_ENV !== "production",
  });
  return { payload, clientIp };
}

export function securityErrorResponse(error: unknown) {
  const status = error instanceof PayloadTooLargeError ? 413 : error instanceof RequestSecurityError ? error.status : 400;
  const message = error instanceof RequestSecurityError || error instanceof PayloadTooLargeError || error instanceof SyntaxError
    ? error.message : "Invalid request";
  return Response.json({ error: message }, { status });
}