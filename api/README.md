# AeroQuery API

Cloudflare Python Worker that powers retrieval and generation for AeroQuery (Canadian TC CAR regulations).

## What it does

All endpoints are `POST` with a JSON body `{ "query": string, "use_rag"?: boolean }`.

| Path | Purpose |
| --- | --- |
| `/retrieve` | Embed query, search Pinecone, rerank with Cohere; returns top chunks (used by the in-browser WebLLM mode). |
| `/stream` | Streams an Azure OpenAI answer over SSE. With `use_rag: true` it runs the agent and grounds the answer in retrieved sections; otherwise answers without context. |

Pipeline: Azure OpenAI embeddings -> Pinecone (namespace `canada`) -> Cohere `rerank-v4.0-pro` -> Azure OpenAI chat.

## Security and limits

- Requests need the `x-aeroquery-key` header matching `API_WORKER_KEY` (min 32 chars) [for instance generated via `openssl rand -hex 32`].
- Per-IP rate limit of 30 requests/minute (client IP forwarded via `x-aeroquery-client-ip`).
- Max query length 1500 chars, max body 32 KB.
- Questions are logged to D1 (`question_logs`, schema in `schema/`); a daily cron deletes rows older than `QUESTIONS_RETENTION_DAYS` (default 30). Set `QUESTIONS_LOG_QUERY_TEXT` to `false` to avoid storing query text.

## Run locally

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), and Node (for Wrangler).

```bash
cd api
uv sync
```

Create `.dev.vars` with the secrets:

```
API_WORKER_KEY=<32+ char random string>
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=...
PINECONE_API_KEY=...
PINECONE_INDEX_HOST=...
COHERE_API_KEY=...
```

Optional: `AZURE_OPENAI_DEPLOYMENT` (default `gpt-5.4-mini`), `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` (default `text-embedding-3-large`).

```bash
uv run pywrangler d1 migrations apply aeroquery-questions --local
uv run pywrangler dev
```

Tests: `uv run python -m unittest discover tests`

## Deploy

```bash
uv run pywrangler deploy
```

Set each secret with `npx wrangler secret put <NAME>`.
