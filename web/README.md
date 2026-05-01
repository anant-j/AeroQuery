# Web — Next.js Frontend + Streaming Generation

Next.js app with two responsibilities:
1. **Frontend** — side-by-side RAG vs bare LLM comparison, eval benchmark table, expandable citations
2. **Streaming API route** (`/api/stream`) — calls Azure Function for retrieval, streams OpenAI generation via SSE

## Architecture

```
Browser
  ├── OpenAI mode:  POST /api/stream → Azure /retrieve → OpenAI streaming → SSE to browser
  └── WebLLM mode:  fetch Azure /retrieve → generate locally in browser (WebGPU)
```

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Create `.env.production` (gitignored):
   ```
   NEXT_PUBLIC_API_URL=https://aeroquery-api.azurewebsites.net/api
   OPENAI_API_KEY=sk-...
   ```
   `OPENAI_API_KEY` has no `NEXT_PUBLIC_` prefix — it stays server-side only, never sent to the browser.

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
      route.ts          — SSE streaming endpoint (calls Azure /retrieve + OpenAI)
```

## Features

- **Streaming** — tokens render as they arrive (parallel streams for RAG + bare)
- **WebLLM** — Llama 3.2 1B runs in-browser via WebGPU, streams locally
- **Expandable citations** — click §3.12 to see the actual retrieved chunk text
- **Eval table** — RAGAS v0.4 benchmark (6 metrics × 3 models × 3 configs)
- **Dark mode** — toggle in top-right

## Deploy

Deployed on Netlify. Push to `main` triggers auto-deploy.

```bash
netlify deploy --prod
```

Set `OPENAI_API_KEY` in Netlify dashboard → Site settings → Environment variables.
