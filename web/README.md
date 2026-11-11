# Web — Next.js Frontend + Streaming Generation

This Next.js app has two responsibilities:
1. **Frontend** — side-by-side RAG vs. bare LLM comparison, evaluation table, and expandable citations
2. **API proxy routes** (`/api/stream`, `/api/retrieve`) — forward requests to the Cloudflare Worker

For the project overview and architecture, see the root README.

## Architecture

```
Browser
  ├── Hosted mode: POST /api/stream → Cloudflare Worker → Azure Foundry generation → SSE
  └── WebLLM mode: POST /api/retrieve → Cloudflare Worker retrieval → local WebGPU generation
```

The browser only calls same-origin Next.js routes. Those server routes authenticate to the Cloudflare Worker with a server-only key.

## Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Set these values in `.env.local` for local development and configure production values in Netlify.

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
      route.ts          — SSE proxy to the Cloudflare Worker
    retrieve/
      route.ts          — server-side Cloudflare Worker proxy for WebLLM retrieval
```

## Features

- **Streaming** — tokens render as they arrive (parallel streams for RAG + bare)
- **WebLLM** — Llama 3.2 1B generates in-browser via WebGPU; RAG retrieval is proxied through the Next.js server to Cloudflare Workers, Azure Foundry, Pinecone, and Cohere
- **Expandable citations** — click §3.12 to see the actual retrieved chunk text
- **Eval table** — exploratory 50-question RAGAS v0.4 results (up to 6 metrics across 3 models and 3 configs); see the repository evaluation limitations before interpreting scores
- **Dark mode** — toggle in top-right

## Deploy

Deployed on Netlify. Push to `main` triggers auto-deploy.

```bash
netlify deploy --prod
```

Set `CLOUDFLARE_WORKER_API_URL` and `CLOUDFLARE_WORKER_KEY` in Netlify's server-side environment variables. Do not expose the Worker key through a `NEXT_PUBLIC_*` variable.


Cloudflare Turnstile does not require the site to use Cloudflare DNS. Cloudflare WAF rules only protect traffic proxied through Cloudflare; a direct Netlify hostname can bypass them, so retain the application and Netlify controls. Before placing a proxy in front of Netlify, verify end-to-end TLS, streaming, and that Netlify's trusted `context.ip` is the visitor IP.
