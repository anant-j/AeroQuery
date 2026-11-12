# AeroQuery Web

Next.js app (deployed on Netlify) for asking questions about Canadian aviation regulations (TC AIM).

## What it does

- Chat UI that compares answer modes: a local model in the browser (WebLLM, Llama 3.2 1B) and a hosted Azure OpenAI model, each with or without RAG.
- Server routes `app/api/retrieve` and `app/api/stream` validate the request (origin check, Cloudflare Turnstile, session limits) and proxy to the Cloudflare Worker in [../api](../api), which does retrieval and generation.
- `proxy.ts` sets a nonce-based CSP; `netlify/edge-functions/api-rate-limit.ts` rate-limits API routes.

## Run locally

Requires Node 20+ and the API worker running (see [../api/README.md](../api/README.md)).

```bash
cd web
npm install
```

Create `.env.local`:

```
CLOUDFLARE_WORKER_API_URL=http://127.0.0.1:8787
CLOUDFLARE_WORKER_KEY=<same value as API_WORKER_KEY>
ALLOWED_ORIGINS=http://localhost:3000
NEXT_PUBLIC_TURNSTILE_SITE_KEY=...
TURNSTILE_SECRET_KEY=...
```

In development, Turnstile test keys are accepted. The worker URL must be HTTPS unless it is localhost.

```bash
npm run dev
```

Open http://localhost:3000.

## Scripts

- `npm run build` / `npm start`: production build and server
- `npm run lint`: ESLint
- `npm test`: security helper tests
