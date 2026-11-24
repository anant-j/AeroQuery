# AeroQuery Web

Next.js frontend, deployed on Netlify.

- Ask questions with GPT-5.4-mini, GPT-5.4-nano, or Llama 3.2 1B running in the browser (WebLLM). Each can run with or without RAG.
- The browser calls the [API Worker](../api/README.md) directly, after getting a session token via Cloudflare Turnstile.
- Answers stream in with expandable source citations.
- The page shows a snapshot of the benchmark results, hard-coded in `app/page.tsx`. Update it by hand from `../pipeline/eval/results/car`.

Security: a CSP (`netlify.toml`), Turnstile sessions, and a per-session request limit in localStorage. Rate limiting itself is enforced by the Worker.

## Run locally

Requires Node 20+ and the API Worker running locally.

```bash
cd web
npm install
```

`.env.local`:

```
NEXT_PUBLIC_CLOUDFLARE_WORKER_API_URL=http://127.0.0.1:8787
NEXT_PUBLIC_CLOUDFLARE_WORKER_KEY=<same as the Worker's API_WORKER_KEY>
NEXT_PUBLIC_TURNSTILE_SITE_KEY=...
```

All three values are public; never put secrets in `NEXT_PUBLIC_*` variables.

```bash
npm run dev     # http://localhost:3000
npm test        # unit tests for lib helpers
npm run lint
```
