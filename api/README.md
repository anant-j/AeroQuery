# API — Azure Functions (Retrieval)

Serves the retrieval layer: embed query → Pinecone search → Cohere rerank → return chunks.

Generation happens in the Next.js API route (`web/app/api/stream/`), not here. This keeps the OpenAI generation key on Netlify and the retrieval keys (Pinecone, Cohere, OpenAI embeddings) on Azure.

## Setup

1. Install Azure Functions Core Tools:
   ```bash
   brew install azure-functions-core-tools@4
   ```

2. Create venv and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Add API keys to `local.settings.json` (gitignored by default):
   ```json
   {
     "Values": {
       "FUNCTIONS_WORKER_RUNTIME": "python",
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
       "OPENAI_API_KEY": "sk-...",
       "PINECONE_API_KEY": "pcsk_...",
       "PINECONE_INDEX_NAME": "aeroquery",
       "COHERE_API_KEY": "..."
     },
     "Host": { "CORS": "*" }
   }
   ```

4. Run locally:
   ```bash
   func start
   ```

## Endpoint

### `POST /api/retrieve`

Embeds the query, searches Pinecone (top-10), reranks with Cohere (top-5), returns chunks.

**Request:**
```json
{ "query": "What are the fuel requirements for VFR flight?" }
```

**Response:**
```json
{
  "query": "...",
  "chunks": [
    { "section": "3.12", "title": "Fuel Requirements", "text": "..." }
  ]
}
```

Called by:
- `web/app/api/stream/route.ts` — for OpenAI streaming (server-side)
- `web/app/page.tsx` — for WebLLM mode (client-side, direct fetch)

## Architecture

```
function_app.py
  ├── embed_query()        → OpenAI text-embedding-3-large
  ├── search_pinecone()    → Pinecone cosine similarity (top-10)
  └── rerank_chunks()      → Cohere rerank-v4.0-pro (top-5)
```

Module-level client reuse — OpenAI, Pinecone, and Cohere clients are initialized once and shared across requests.

## Deploy

```bash
func azure functionapp publish aeroquery-api
```
