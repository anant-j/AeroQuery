import OpenAI from "openai";

const SYSTEM_PROMPT = `You are an expert aviation regulation assistant specializing in Canadian aviation regulations (TC AIM - Transport Canada Aeronautical Information Manual).

Rules:
1. Answer ONLY based on the provided context. Do not use any outside knowledge.
2. Cite specific section numbers (e.g., "Section 2.3.1") in your answer.
3. If the context does not contain enough information to answer the question, say: "I don't have enough information in the available regulations to answer this question."
4. Be precise and concise. Pilots need clear, unambiguous answers.
5. If multiple sections are relevant, reference all of them.`;

interface RetrievedChunk {
  section: string;
  title: string;
  text: string;
}

/**
 * Streaming endpoint. Calls Azure Function /retrieve for RAG chunks,
 * then streams OpenAI generation as SSE.
 *
 * OPENAI_API_KEY is a server-only env var (no NEXT_PUBLIC_ prefix) —
 * never sent to the browser.
 */
export async function POST(req: Request) {
  const body = await req.json();
  const query: string = body.query;
  const useRag: boolean = body.use_rag;

  if (!query) {
    return new Response(JSON.stringify({ error: "query is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    return new Response(
      JSON.stringify({ error: "OPENAI_API_KEY not configured" }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }

  const openai = new OpenAI({ apiKey });
  const model = process.env.LLM_MODEL || "gpt-5.4-mini";

  let messages: OpenAI.ChatCompletionMessageParam[];
  let sources: { section: string; title: string }[] = [];

  if (useRag) {
    const apiUrl = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL;

    const res = await fetch(`${apiUrl}/retrieve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    if (!res.ok) {
      return new Response(
        JSON.stringify({ error: "Retrieval failed" }),
        { status: 502, headers: { "Content-Type": "application/json" } },
      );
    }

    const data = await res.json();
    const chunks: RetrievedChunk[] = data.chunks;

    sources = chunks.map((c) => ({ section: c.section, title: c.title }));

    const context = chunks
      .map((c) => `[Section ${c.section}]\n${c.text}`)
      .join("\n\n---\n\n");

    messages = [
      { role: "system", content: SYSTEM_PROMPT },
      {
        role: "user",
        content: `Context from Canadian Aviation Regulations:\n\n${context}\n\n---\n\nQuestion: ${query}`,
      },
    ];
  } else {
    messages = [
      {
        role: "system",
        content:
          "You are an aviation regulation expert. Answer based on your knowledge of Canadian aviation regulations.",
      },
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
            `data: ${JSON.stringify({ type: "sources", sources, model })}\n\n`,
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
      "Cache-Control": "no-cache",
    },
  });
}
