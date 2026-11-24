# AeroQuery API

Cloudflare Python Worker that handles auth, retrieval, and streaming generation.

## Endpoints

All are `POST` with a JSON body. Every request needs the `x-aeroquery-key` header (the public app key) and an allowed `Origin`.

| Path | Body | Returns |
|---|---|---|
| `/auth/anonymous` | `{ turnstile_token }` | `{ token, expires_at }`, a short-lived session JWT |
| `/retrieve` | `{ query }` | Top 5 reranked chunks (used by WebLLM mode) |
| `/stream` | `{ query, use_rag?, model? }` | SSE answer stream; `model` is `gpt-5.4-mini` (default) or `gpt-5.4-nano` |

`/retrieve` and `/stream` also require `Authorization: Bearer <session token>`.

With `use_rag: true`, the agent first classifies the query as simple or complex. Complex queries are split into 2–3 sub-queries. It then retrieves chunks and runs a relevance guard before generating the answer.

## Limits and storage

- Rate limits: 60 requests/min per IP and 24/min per session. Queries are capped at 1,500 characters and request bodies at 32 KB.
- Answers are cached in KV (`AEROQUERY_ANSWER_CACHE`) for 24 hours.
- Questions are logged to D1 (`question_logs`); a daily cron deletes rows older than `QUESTIONS_RETENTION_DAYS`.
- Set `API_DISABLED = "true"` in `wrangler.toml` and redeploy to switch the API off.

## Run locally

Requires Python 3.13, [uv](https://docs.astral.sh/uv/), and Node.

```bash
cd api
uv sync
cp .dev.vars.example .dev.vars   # fill in secrets
uv run pywrangler d1 migrations apply aeroquery-questions --local
uv run pywrangler dev
```

Tests: `uv run python -m unittest discover tests`

## Deploy

```bash
npx wrangler secret put <NAME>   # once for each secret in wrangler.toml [secrets]
uv run pywrangler deploy
```
