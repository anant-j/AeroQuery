/**
 * Warmup proxy — pings the Azure Function /warmup endpoint to wake it
 * before the user submits their first query. Server-side proxy avoids
 * exposing the Function URL to CORS handling in the browser.
 */
export async function GET() {
  const apiUrl = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL;
  if (!apiUrl) {
    return new Response(JSON.stringify({ status: "skipped" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const res = await fetch(`${apiUrl}/warmup`, {
      method: "GET",
      // Don't block the page — short timeout via AbortController
      signal: AbortSignal.timeout(15000),
    });
    return new Response(JSON.stringify({ status: res.ok ? "warm" : "error" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    return new Response(JSON.stringify({ status: "error" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
}
