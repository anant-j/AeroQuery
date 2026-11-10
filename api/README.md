# API — Azure Functions (Retrieval + Agent)

This service provides retrieval and the LangGraph query agent. It embeds queries with the Azure OpenAI `text-embedding-3-large` deployment, searches Pinecone, and reranks with Cohere `rerank-v4.0-pro`. The agent uses Azure OpenAI for classification and query decomposition.

For the project overview and architecture, see the root README. Final answer generation happens in the Next.js API route (`web/app/api/stream/`) using Azure OpenAI. Keep the Azure OpenAI, Pinecone, Cohere, and storage credentials in server-side settings; do not expose them to the browser.

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

3. Create `local.settings.json` (gitignored) with the Azure Functions `Values` format. `AzureWebJobsStorage` must point to Azurite or an Azure Storage account for local question logging.
   ```json
   {
     "Values": {
       "FUNCTIONS_WORKER_RUNTIME": "python",
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
      "AZURE_OPENAI_API_KEY": "...",
      "AZURE_OPENAI_ENDPOINT": "https://your-openai-resource-name.openai.azure.com",
      "AZURE_OPENAI_DEPLOYMENT": "gpt-5.4-mini",
      "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
       "PINECONE_API_KEY": "pcsk_...",
       "PINECONE_INDEX_NAME": "aeroquery",
      "COHERE_API_KEY": "...",
      "QUESTIONS_TABLE_NAME": "QuestionLog"
     },
    "Host": { "CORS": ["http://localhost:3000", "http://localhost:3001"] }
   }
   ```

4. Run locally:
   ```bash
   func start
   ```

## Endpoints

### `POST /api/agent` (primary — used by the Azure OpenAI streaming route)

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
  ├── classify          → Azure OpenAI via LangChain ChatOpenAI: simple vs complex
  ├── retrieve          → single retrieval (simple queries)
  ├── decompose         → Azure OpenAI: break into sub-queries
  ├── retrieve_multi    → retrieve per sub-query, merge & dedup
  └── guard             → check rerank scores, flag low confidence
```

## Deploy

```bash
func azure functionapp publish aeroquery-api
```
