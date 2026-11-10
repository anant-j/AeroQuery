# Web — Next.js Frontend + Streaming Generation

This Next.js app has two responsibilities:
1. **Frontend** — side-by-side RAG vs. bare LLM comparison, evaluation table, and expandable citations
2. **Streaming API route** (`/api/stream`) — calls the Azure Function agent for RAG and streams Azure OpenAI generation via SSE

For the project overview and architecture, see the root README.

## Architecture

```
Browser
  ├── Hosted mode: POST /api/stream → Azure Function /agent for RAG → Azure OpenAI generation → SSE
  └── WebLLM mode: fetch Azure Function /retrieve → generate locally in browser (WebGPU)
```

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. For local development, create `.env.local`; configure the same server-side variables in Netlify for deployment:
   ```
   NEXT_PUBLIC_API_URL=https://aeroquery-api.azurewebsites.net/api
    API_URL=https://aeroquery-api.azurewebsites.net/api
    AZURE_OPENAI_API_KEY=...
    AZURE_OPENAI_ENDPOINT=https://your-openai-resource-name.openai.azure.com
    AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini
   ```
`API_URL` is optional and defaults to `NEXT_PUBLIC_API_URL`. Keep `AZURE_OPENAI_API_KEY` server-side; never give it a `NEXT_PUBLIC_` prefix. Pinecone, Cohere, and Azure Storage credentials belong in the Azure Function settings, not Netlify.

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
```

## Features

- **Streaming** — tokens render as they arrive (parallel streams for RAG + bare)
- **WebLLM** — Llama 3.2 1B runs in-browser via WebGPU and generates locally; RAG retrieval still uses the hosted Azure Function, Azure OpenAI embeddings, Pinecone, and Cohere
- **Expandable citations** — click §3.12 to see the actual retrieved chunk text
- **Eval table** — exploratory 50-question RAGAS v0.4 results (up to 6 metrics across 3 models and 3 configs); see the repository evaluation limitations before interpreting scores
- **Dark mode** — toggle in top-right

## Deploy

Deployed on Netlify. Push to `main` triggers auto-deploy.

```bash
netlify deploy --prod
```

Set `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_DEPLOYMENT` in Netlify's environment variables. Set `NEXT_PUBLIC_API_URL` to the Azure Functions API URL; `API_URL` may be set separately for the server-side route.
