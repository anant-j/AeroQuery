# API — Azure Functions Backend

Python Azure Functions serving the RAG pipeline over HTTP. Three endpoints for the frontend.

Optimized with module-level client reuse (OpenAI, Pinecone, Cohere) and parallel LLM calls.

## Setup

1. Install Azure Functions Core Tools:
   ```bash
   brew tap azure/functions
   brew install azure-functions-core-tools@4
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Add your API keys to `local.settings.json` (already gitignored):
   ```json
   {
     "Values": {
       "OPENAI_API_KEY": "sk-...",
       "PINECONE_API_KEY": "pcsk_...",
       "PINECONE_INDEX_NAME": "aeroquery",
       "COHERE_API_KEY": "...",
       "LLM_MODEL": "gpt-5.4-mini"
     }
   }
   ```

4. Run locally:
   ```bash
   func start
   ```

## Endpoints

### `POST /api/compare` (primary — used by frontend)

Runs RAG and bare LLM in parallel, returns both results. Embeds query once, shares retrieval.

**Request:**
```json
{
  "query": "What are the fuel requirements for VFR flight?"
}
```

**Response:**
```json
{
  "query": "...",
  "model": "gpt-5.4-mini",
  "rag": {
    "answer": "...",
    "tokens": 2322,
    "sources": [{"section": "3.12", "title": "Fuel Requirements"}]
  },
  "bare": {
    "answer": "...",
    "tokens": 850
  }
}
```

### `POST /api/ask`

Full RAG pipeline or bare LLM, depending on toggle.

**Request:**
```json
{
  "query": "What are the fuel requirements for VFR flight?",
  "use_rag": true,
  "model": "gpt-5.4-mini"
}
```

**Response (RAG):**
```json
{
  "query": "...",
  "answer": "...",
  "model": "gpt-5.4-mini",
  "tokens": 2322,
  "use_rag": true,
  "sources": [
    {"section": "3.12", "title": "Fuel Requirements"}
  ]
}
```

**Response (bare LLM, `use_rag: false`):**
```json
{
  "query": "...",
  "answer": "...",
  "model": "gpt-5.4-mini",
  "tokens": 850,
  "use_rag": false
}
```

### `POST /api/retrieve`

Retrieval + reranking only — returns chunks for WebLLM mode.

**Request:**
```json
{
  "query": "What are the fuel requirements for VFR flight?"
}
```

**Response:**
```json
{
  "query": "...",
  "chunks": [
    {"section": "3.12", "title": "Fuel Requirements", "text": "..."}
  ]
}
```

## Deployment

Deploy to Azure Functions:
```bash
az login
func azure functionapp publish <your-function-app-name>
```
