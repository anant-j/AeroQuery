# API — Cloudflare Python Worker

The Worker serves retrieval, agent routing, and hosted SSE generation. Azure is used only for Foundry chat and embedding requests; Pinecone remains the vector database and Cohere performs reranking. Question metadata is written to D1, with a daily Cron Trigger removing records older than the configured retention period.

The browser continues to call same-origin Next.js routes. Netlify forwards those routes to the Worker using a server-side shared key; no provider credentials or Worker key are exposed to the browser.

## Local development

1. Install `uv` and Node.js, then create the D1 database:

  ```bash
  brew install uv
  npx wrangler login
  cd api
  uv sync --group dev
  uv run pywrangler d1 create aeroquery-questions
  ```

2. Put the returned database ID in `wrangler.toml`, replacing `REPLACE_WITH_D1_DATABASE_ID`. Choose an unused integer for the `API_RATE_LIMITER` binding's `namespace_id` in your Cloudflare account.

3. Copy `.dev.vars.example` to `.dev.vars` and fill in the Foundry, Pinecone, Cohere, and local Worker key values. Use a randomly generated key of at least 32 bytes. Keep `.dev.vars` private.

4. Apply the schema and run the Worker:

  ```bash
  uv run pywrangler d1 migrations apply aeroquery-questions --local
  uv run pywrangler dev --ip 127.0.0.1 --port 8799
  ```

5. Set the same local `CLOUDFLARE_WORKER_KEY` and `CLOUDFLARE_WORKER_API_URL=http://127.0.0.1:8799` in `web/.env.local`, then start the web app with `npm run dev` from `web/`.

The Worker key is sent only from the Next.js server routes. For production, generate a high-entropy key (for example, `openssl rand -hex 32`), store it as the Worker secret `API_WORKER_KEY`, and configure the same value as Netlify's `CLOUDFLARE_WORKER_KEY`. Do not send it to the browser. The D1 database is initialized empty; no Azure Table data is imported.

## Configuration

Set these as Cloudflare Worker secrets:

- `API_WORKER_KEY`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT`
- `PINECONE_API_KEY`
- `PINECONE_INDEX_HOST` (the index's data-plane host from Pinecone)
- `COHERE_API_KEY`

Set `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `QUESTIONS_RETENTION_DAYS`, and `QUESTIONS_LOG_QUERY_TEXT` as Worker variables. Query-text logging defaults to disabled. The D1 binding name is `DB`.

In Netlify, set `CLOUDFLARE_WORKER_API_URL` to the Worker origin (without `/api`) and `CLOUDFLARE_WORKER_KEY` to the same value as the Worker secret `API_WORKER_KEY`.

## Endpoints

- `POST /api/retrieve` embeds with Foundry, searches Pinecone, and reranks with Cohere for browser-side WebLLM generation.
- `POST /api/agent` classifies and retrieves context, including decomposition for complex queries.
- `POST /api/stream` optionally retrieves context, then streams Foundry chat-completion events as SSE.

Each request requires `x-aeroquery-key`; the Next.js server routes add it. The Worker enforces a Cloudflare Rate Limiting binding per validated client IP (30 requests per minute, approximate per Cloudflare location). Keep the shared key private and rotate it if exposed. D1 stores timestamp, query length, mode, and optional query text; query-text logging is off by default. The Cron Trigger at 03:00 UTC deletes rows older than `QUESTIONS_RETENTION_DAYS` (30 by default).

## Abuse and usage limits

The Worker does not enforce an application-wide daily or concurrent request limit. Cloudflare plan limits apply to the Worker, but do not cap usage or charges from Azure, Pinecone, Cohere, or Netlify. Monitor those services separately and configure provider budget alerts or limits where available. Chat output is limited to 2048 completion tokens, retrieved chunks to 8000 characters, and upstream fetches to 60 seconds. Confirm these settings work with your model deployment.

Cloudflare's IP rate-limit binding is approximate and per location. The web deployment additionally uses a Netlify per-IP edge rule; check the deploy log and verify it is active. The web layer obtains IPs from Netlify's request context, not a client-provided header. If another proxy fronts Netlify, verify the context identifies the real visitor before relying on IP limits. Multiple visitors can share an IP.

## Deploy

```bash
cd api
uv run pywrangler d1 migrations apply aeroquery-questions --remote
uv run pywrangler secret put API_WORKER_KEY
uv run pywrangler secret put AZURE_OPENAI_API_KEY
uv run pywrangler secret put AZURE_OPENAI_ENDPOINT
uv run pywrangler secret put PINECONE_API_KEY
uv run pywrangler secret put PINECONE_INDEX_HOST
uv run pywrangler secret put COHERE_API_KEY
uv run pywrangler deploy
```

Add the deployed Worker origin and the matching Worker key to Netlify's server-side environment variables as `CLOUDFLARE_WORKER_API_URL` and `CLOUDFLARE_WORKER_KEY`.
