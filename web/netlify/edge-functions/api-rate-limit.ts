import { MAX_BODY_BYTES } from "../../app/lib/constants.js";

export default async function apiRateLimit(request: Request) {
  const contentLength = request.headers.get("content-length");
  if (contentLength && /^\d+$/.test(contentLength) && Number(contentLength) > MAX_BODY_BYTES) {
    return Response.json({ error: "request body is too large" }, { status: 413 });
  }
}

export const config = {
  path: ["/api/stream", "/api/stream/", "/api/retrieve", "/api/retrieve/"],
  rateLimit: {
    windowLimit: 20,
    windowSize: 60,
    aggregateBy: ["ip", "domain"],
  },
};