# Web — Next.js Frontend + Streaming Generation

This Next.js app has two responsibilities:
1. **Frontend** — side-by-side RAG vs. bare LLM comparison, evaluation table, and expandable citations
2. **Streaming API route** (`/api/stream`) — calls the Azure Function agent for RAG and streams Azure OpenAI generation via SSE

For the project overview and architecture, see the root README.

## Architecture

```
Browser
  ├── Hosted mode: POST /api/stream → Azure Function /agent → Azure OpenAI generation → SSE
  └── WebLLM mode: POST /api/retrieve → server-side Azure Function proxy → local WebGPU generation
```

The browser only calls same-origin Next.js routes. Those server routes authenticate to Azure Functions with a Function key. A page-entry request to `/api/warmup` starts client initialization in the background so it can overlap the visitor's time on the page.

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Copy `.env.example` to `.env.local` for local development and configure the same server-side values in Netlify:
   ```
    AZURE_FUNCTION_API_URL=https://aeroquery-api.azurewebsites.net/api
    AZURE_FUNCTION_KEY=...
    AZURE_OPENAI_API_KEY=...
    AZURE_OPENAI_ENDPOINT=https://your-openai-resource-name.openai.azure.com
    AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini
   ```
  `AZURE_FUNCTION_KEY` and `AZURE_OPENAI_API_KEY` are server-only. Never prefix them with `NEXT_PUBLIC_`. Pinecone, Cohere, embedding, and storage credentials belong in Azure Function settings, not Netlify.

3. Run locally:
   ```bash
   npm run dev
   ```

## Structure

```
app/
  page.tsx              — Main UI: query form, streaming results, eval table, WebLLM
  layout.tsx            — Root layout, fonts, metadata
  globals.css           — Tailwind + custom styles
  lib/
    prompts.ts          — Shared system prompts (RAG + bare LLM)
  api/
    stream/
      route.ts          — SSE endpoint (calls Azure Function /agent and streams Azure OpenAI)
    retrieve/
      route.ts          — server-side Azure Function proxy for WebLLM retrieval
```

## Features

- **Streaming** — tokens render as they arrive (parallel streams for RAG + bare)
- **WebLLM** — Llama 3.2 1B generates in-browser via WebGPU; RAG retrieval is proxied through the Next.js server to Azure Functions, Azure OpenAI, Pinecone, and Cohere
- **Expandable citations** — click §3.12 to see the actual retrieved chunk text
- **Eval table** — exploratory 50-question RAGAS v0.4 results (up to 6 metrics across 3 models and 3 configs); see the repository evaluation limitations before interpreting scores
- **Dark mode** — toggle in top-right

## Deploy

Deployed on Netlify. Push to `main` triggers auto-deploy.

```bash
netlify deploy --prod
```

Set `AZURE_FUNCTION_API_URL`, `AZURE_FUNCTION_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_DEPLOYMENT` in Netlify's server-side environment variables. Do not expose the Function key through a `NEXT_PUBLIC_*` variable.
