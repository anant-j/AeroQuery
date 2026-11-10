import OpenAI from "openai";
import { RAG_SYSTEM_PROMPT, BARE_SYSTEM_PROMPT } from "../../lib/prompts";
import { validateQueryPayload } from "../../lib/security.js";

interface RetrievedChunk {
  section: string;
  title: string;
  text: string;
}

const RATE_LIMIT_WINDOW_MS = 60_000;
const RATE_LIMIT_MAX_REQUESTS = 30;
const requestTimestamps = new Map<string, number[]>();

function enforceRateLimit(ip: string) {
  const now = Date.now();
  const timestamps = requestTimestamps.get(ip) ?? [];
  const recent = timestamps.filter((value) => now - value < RATE_LIMIT_WINDOW_MS);
  recent.push(now);
  requestTimestamps.set(ip, recent);

  if (recent.length > RATE_LIMIT_MAX_REQUESTS) {
    throw new Error("Too many requests. Please wait a minute and try again.");
  }
}

/**
 * Streaming endpoint. Calls Azure Function /retrieve for RAG chunks,
 * then streams Azure AI Foundry generation as SSE.
 *
 * AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT are server-only env vars
 * (no NEXT_PUBLIC_ prefix) — never sent to the browser.
 */
export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ error: "Invalid JSON" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  let query: string;
  let useRag: boolean;
  try {
    const payload = validateQueryPayload(body);
    query = payload.query;
    useRag = payload.use_rag;
  } catch (error) {
    return new Response(JSON.stringify({ error: error instanceof Error ? error.message : "Invalid request" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const clientIp = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  try {
    enforceRateLimit(clientIp);
  } catch (error) {
    return new Response(JSON.stringify({ error: error instanceof Error ? error.message : "Rate limit exceeded" }), {
      status: 429,
      headers: { "Content-Type": "application/json" },
    });
  }

  const apiKey = process.env.AZURE_OPENAI_API_KEY;
  const endpoint = process.env.AZURE_OPENAI_ENDPOINT;
  if (!apiKey || !endpoint) {
    return new Response(
      JSON.stringify({
        error: "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be configured",
      }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }

  // For Azure Foundry (v1 API), `model` is the deployment name.
  const model =
    process.env.AZURE_OPENAI_DEPLOYMENT || process.env.LLM_MODEL || "gpt-5.4-mini";
  const baseURL = `${endpoint.replace(/\/$/, "")}/openai/v1/`;

  const openai = new OpenAI({ apiKey, baseURL });

  let messages: OpenAI.ChatCompletionMessageParam[];
  let sources: { section: string; title: string; text: string }[] = [];
  let agentMeta: Record<string, unknown> | undefined;

  if (useRag) {
    const apiUrl = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL;

    const res = await fetch(`${apiUrl}/agent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    if (!res.ok) {
      return new Response(
        JSON.stringify({ error: "Agent retrieval failed" }),
        { status: 502, headers: { "Content-Type": "application/json" } },
      );
    }

    const data = await res.json();
    const chunks: RetrievedChunk[] = data.chunks;
    const agent = data.agent;

    sources = chunks.map((c) => ({ section: c.section, title: c.title, text: c.text }));
    agentMeta = agent;

    const context = chunks
      .map((c) => `[Section ${c.section}]\n${c.text}`)
      .join("\n\n---\n\n");

    const systemContent = agent?.context_sufficient === false
      ? RAG_SYSTEM_PROMPT + "\n\nIMPORTANT: The retrieval system flagged low confidence for this query. If the context does not clearly answer the question, say so."
      : RAG_SYSTEM_PROMPT;

    messages = [
      { role: "system", content: systemContent },
      {
        role: "user",
        content: `Context from Canadian Aviation Regulations:\n\n${context}\n\n---\n\nQuestion: ${query}`,
      },
    ];
  } else {
    messages = [
      { role: "system", content: BARE_SYSTEM_PROMPT },
      { role: "user", content: query },
    ];
  }

  const stream = await openai.chat.completions.create({
    model,
    messages,
    temperature: 0,
    stream: true,
    stream_options: { include_usage: true },
  });

  const encoder = new TextEncoder();

  const readableStream = new ReadableStream({
    async start(controller) {
      if (useRag && sources.length > 0) {
        controller.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({ type: "sources", sources, model, agent: agentMeta })}\n\n`,
          ),
        );
      }

      for await (const chunk of stream) {
        const token = chunk.choices[0]?.delta?.content;
        if (token) {
          controller.enqueue(
            encoder.encode(
              `data: ${JSON.stringify({ type: "token", token })}\n\n`,
            ),
          );
        }

        if (chunk.usage) {
          controller.enqueue(
            encoder.encode(
              `data: ${JSON.stringify({ type: "usage", tokens: chunk.usage.total_tokens, model })}\n\n`,
            ),
          );
        }
      }

      controller.enqueue(
        encoder.encode(`data: ${JSON.stringify({ type: "done" })}\n\n`),
      );
      controller.close();
    },
  });

  return new Response(readableStream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      "X-Content-Type-Options": "nosniff",
      "Access-Control-Allow-Origin": "https://aeroquery.anant-j.com",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    },
  });
}

export async function OPTIONS() {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": "https://aeroquery.anant-j.com",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    },
  });
}
