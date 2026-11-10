# API — Azure Functions (Retrieval + Agent)

This Function App provides retrieval and the LangGraph query agent. It embeds queries with the Azure OpenAI `text-embedding-3-large` deployment, searches Pinecone, and reranks with Cohere `rerank-v4.0-pro`. The agent uses Azure OpenAI for classification and query decomposition.

The app requires Function-level authorization. Only the Next.js server routes should call it; browsers call same-origin Next.js routes and never receive the Function key. Final answer generation is also performed server-side by `web/app/api/stream/` using Azure OpenAI.

## Setup

1. Install Azure Functions Core Tools:
   ```bash
   brew install azure-functions-core-tools@4
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Create `local.settings.json` (gitignored) using the Azure Functions `Values` format. Use Azurite or an Azure Storage account for `AzureWebJobsStorage`.
   ```json
   {
     "Values": {
       "FUNCTIONS_WORKER_RUNTIME": "python",
       "AzureWebJobsStorage": "UseDevelopmentStorage=true",
       "AZURE_OPENAI_API_KEY": "...",
       "AZURE_OPENAI_ENDPOINT": "https://your-openai-resource-name.openai.azure.com",
       "AZURE_OPENAI_DEPLOYMENT": "gpt-5.4-mini",
       "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-large",
       "PINECONE_API_KEY": "...",
       "PINECONE_INDEX_NAME": "aeroquery",
       "COHERE_API_KEY": "...",
       "QUESTIONS_TABLE_NAME": "QuestionLog",
       "QUESTIONS_RETENTION_DAYS": "30",
       "QUESTIONS_LOG_QUERY_TEXT": "false"
     }
   }
   ```

4. Start the Function host locally:
   ```bash
   func start
   ```

5. In another terminal, run `func keys list` and set a Function key as `AZURE_FUNCTION_KEY` in `web/.env.local`. Set `AZURE_FUNCTION_API_URL=http://localhost:7071/api` there as well. In production, configure both as server-side Netlify environment variables. The page-entry warmup uses this key-protected route to initialize the Function app before the first question.

Query text logging is off by default. If enabled with `QUESTIONS_LOG_QUERY_TEXT=true`, a daily timer purges question-log entries older than `QUESTIONS_RETENTION_DAYS` (30 days by default). IP address, user-agent, and referrer are not stored.

## Endpoints

### `GET /api/warmup`

Requires a Function key. Called by the Next.js `/api/warmup` proxy when the page loads. It initializes shared clients without making model or retrieval requests. This can move a Consumption-plan cold start to page entry, but it does not eliminate cold starts or keep an idle app warm indefinitely.

### `POST /api/agent`

Requires a Function key. Called by the Next.js `/api/stream` route. The LangGraph agent classifies the query, selects a retrieval strategy, and returns context and trace metadata.

### `POST /api/retrieve`

Requires a Function key. Called by the Next.js `/api/retrieve` proxy for browser-local WebLLM generation.

## Architecture

```
function_app.py
  ├── /retrieve          → Azure OpenAI embeddings → Pinecone → Cohere rerank
  └── /agent             → LangGraph (classify → retrieve/decompose → guard)

agent.py
  ├── classify/decompose → Azure OpenAI via LangChain ChatOpenAI
  └── retrieval          → shared embedding, Pinecone search, and Cohere rerank
```

## Deploy

```bash
func azure functionapp publish aeroquery-api --python
```
