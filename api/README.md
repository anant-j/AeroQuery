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

## Endpoints

### `POST /api/agent` (primary — used by OpenAI streaming)

LangGraph agent: classifies query, routes through retrieval strategy, guards result.

**Request:**
```json
{ "query": "Compare VFR and IFR fuel requirements" }
```

**Response:**
```json
{
  "query": "...",
  "chunks": [
    { "section": "3.12", "title": "Fuel Requirements", "text": "..." }
  ],
  "agent": {
    "query_type": "complex",
    "sub_queries": ["VFR fuel requirements", "IFR fuel requirements"],
    "context_sufficient": true,
    "steps": ["classify:complex", "decompose:2_sub_queries", "retrieve_multi:8_chunks", "guard:pass"]
  }
}
```

### `POST /api/retrieve` (fallback — used by WebLLM mode)

Direct retrieval without agent overhead. Embeds, searches, reranks.

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

## Architecture

```
function_app.py
  ├── /retrieve          → embed → Pinecone → Cohere rerank (direct)
  └── /agent            → LangGraph (classify → route → retrieve → guard)

agent.py (LangGraph StateGraph)
  ├── classify          → ChatOpenAI: simple vs complex
  ├── retrieve          → single retrieval (simple queries)
  ├── decompose         → ChatOpenAI: break into sub-queries
  ├── retrieve_multi    → retrieve per sub-query, merge & dedup
  └── guard             → check rerank scores, flag low confidence
```

## Deploy

```bash
func azure functionapp publish aeroquery-api
```
