import azure.functions as func
import json
import logging
import os
import uuid
from datetime import datetime, timezone

MAX_QUERY_LENGTH = 1500
MAX_BODY_BYTES = 32768
REQUEST_RATE_LIMIT = 30
RATE_LIMIT_WINDOW_MS = 60000
ALLOWED_ORIGINS = {
    "https://aeroquery.anant-j.com",
    "https://www.aeroquery.anant-j.com",
    "http://localhost:3000",
    "http://localhost:3001",
}
_request_timestamps = {}

from azure.data.tables import TableServiceClient
from openai import OpenAI
from pinecone import Pinecone
import cohere
from agent import build_graph

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

logger = logging.getLogger("aeroquery.queries")

# Question log — persisted to Azure Table Storage on the storage account the
# Function App already uses (AzureWebJobsStorage). Durable, never sampled, and
# directly queryable from the Portal / Storage Explorer / SDK. No new infra.
QUESTIONS_TABLE = os.environ.get("QUESTIONS_TABLE_NAME", "QuestionLog")
_questions_table = None


def get_questions_table():
    """Lazily create/connect the question-log table client. Reused across
    requests. Returns None if no storage connection is configured."""
    global _questions_table
    if _questions_table is None:
        conn = os.environ.get("AzureWebJobsStorage")
        if not conn:
            return None
        service = TableServiceClient.from_connection_string(conn)
        service.create_table_if_not_exists(QUESTIONS_TABLE)
        _questions_table = service.get_table_client(QUESTIONS_TABLE)
    return _questions_table


def get_cors_headers(req: func.HttpRequest):
    origin = req.headers.get("Origin")
    if origin and origin in ALLOWED_ORIGINS:
        return {"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if req.method == "OPTIONS":
        return {"Access-Control-Allow-Origin": "https://aeroquery.anant-j.com", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    return {"Access-Control-Allow-Origin": "https://aeroquery.anant-j.com", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}


def enforce_rate_limit(req: func.HttpRequest):
    ip = (req.headers.get("X-Forwarded-For", "") or "unknown").split(",")[0].strip() or "unknown"
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    bucket = _request_timestamps.setdefault(ip, [])
    bucket[:] = [ts for ts in bucket if now - ts < RATE_LIMIT_WINDOW_MS]
    bucket.append(now)
    if len(bucket) > REQUEST_RATE_LIMIT:
        raise PermissionError("Rate limit exceeded")


def validate_json_payload(req: func.HttpRequest):
    try:
        body = req.get_json()
    except ValueError:
        raise ValueError("Invalid JSON")
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    query = body.get("query", "")
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    query = query.strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must be shorter than {MAX_QUERY_LENGTH} characters")
    payload_size = len(json.dumps(body).encode("utf-8"))
    if payload_size > MAX_BODY_BYTES:
        raise ValueError(f"request body must be shorter than {MAX_BODY_BYTES} bytes")
    return query


def log_question(req: func.HttpRequest, query: str, mode: str) -> None:
    """Record every incoming question to Table Storage. Captures lightweight
    "who" signals so usage can be queried later. Best-effort — logging must
    never break a request.
    """
    try:
        table = get_questions_table()
        if table is None:
            return

        headers = req.headers
        # Azure puts the real client IP first in X-Forwarded-For.
        forwarded = headers.get("X-Forwarded-For", "")
        client_ip = forwarded.split(",")[0].strip() if forwarded else "unknown"

        now = datetime.now(timezone.utc)
        # PartitionKey = day (fast per-day scans); RowKey = reverse-time ordered
        # so newest rows sort first, with a uuid suffix to guarantee uniqueness.
        ticks = int((datetime.max - now.replace(tzinfo=None)).total_seconds() * 1000)
        table.create_entity({
            "PartitionKey": now.strftime("%Y-%m-%d"),
            "RowKey": f"{ticks:020d}-{uuid.uuid4().hex}",
            "timestamp": now.isoformat(),
            "query": query,
            "mode": mode,
            "client_ip": client_ip,
            "user_agent": headers.get("User-Agent", "unknown"),
            "referer": headers.get("Referer", "unknown"),
        })
    except Exception:  # pragma: no cover - logging must never fail a request
        logger.exception("Failed to log question")


# On Azure Foundry (v1 API), the embedding "model" is the deployment name.
EMBEDDING_MODEL = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
RERANK_MODEL = "rerank-v4.0-pro"
NAMESPACE = "canada"

# Module-level clients — reused across requests, no re-init overhead
openai_client = None
pinecone_index = None
cohere_client = None


def get_openai():
    global openai_client
    if openai_client is None:
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        openai_client = OpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            base_url=f"{endpoint}/openai/v1/",
        )
    return openai_client


def get_pinecone_index():
    global pinecone_index
    if pinecone_index is None:
        pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
        pinecone_index = pc.Index(os.environ.get("PINECONE_INDEX_NAME", "aeroquery"))
    return pinecone_index


def get_cohere():
    global cohere_client
    if cohere_client is None:
        cohere_client = cohere.ClientV2(api_key=os.environ["COHERE_API_KEY"])
    return cohere_client


def embed_query(query):
    response = get_openai().embeddings.create(model=EMBEDDING_MODEL, input=[query])
    return response.data[0].embedding


def search_pinecone(query_vector, top_k=10):
    index = get_pinecone_index()

    results = index.query(
        vector=query_vector,
        top_k=top_k,
        namespace=NAMESPACE,
        include_metadata=True,
    )

    chunks = []
    for match in results.matches:
        chunks.append({
            "score": match.score,
            "text": match.metadata.get("text", ""),
            "section": match.metadata.get("section", ""),
            "title": match.metadata.get("title", ""),
        })
    return chunks


def rerank_chunks(query, chunks, top_n=5):
    co = get_cohere()
    docs = [c["text"] for c in chunks]

    response = co.rerank(
        model=RERANK_MODEL,
        query=query,
        documents=docs,
        top_n=top_n,
    )

    reranked = []
    for result in response.results:
        chunk = chunks[result.index].copy()
        chunk["rerank_score"] = result.relevance_score
        reranked.append(chunk)
    return reranked


@app.route(route="warmup", methods=["GET", "POST", "OPTIONS"])
def warmup(req: func.HttpRequest) -> func.HttpResponse:
    """Lightweight warmup — pre-initializes module-level clients so the
    first real request avoids cold-start latency. Safe to call repeatedly.
    """
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=get_cors_headers(req))
    get_openai()
    get_pinecone_index()
    get_cohere()
    get_agent_graph()
    return func.HttpResponse(
        json.dumps({"status": "warm"}),
        mimetype="application/json",
        headers=get_cors_headers(req),
    )


@app.route(route="retrieve", methods=["POST", "OPTIONS"])
def retrieve(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve + rerank — embed query, search Pinecone, rerank with Cohere."""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=get_cors_headers(req))
    try:
        enforce_rate_limit(req)
    except PermissionError:
        return func.HttpResponse(json.dumps({"error": "Too many requests. Please wait a minute and try again."}), status_code=429, headers=get_cors_headers(req))
    try:
        query = validate_json_payload(req)
    except ValueError as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=400, headers=get_cors_headers(req))

    log_question(req, query, mode="webllm")

    query_vector = embed_query(query)
    chunks = search_pinecone(query_vector)
    chunks = rerank_chunks(query, chunks)

    return func.HttpResponse(
        json.dumps({
            "query": query,
            "chunks": [{"section": c["section"], "title": c["title"], "text": c["text"]} for c in chunks],
        }),
        mimetype="application/json",
        headers=get_cors_headers(req),
    )


# ---------------------------------------------------------------------------
# LangGraph agent — lazy init (compiled once, reused across requests)
# ---------------------------------------------------------------------------
_agent_graph = None


def get_agent_graph():
    global _agent_graph
    if _agent_graph is None:
        _agent_graph = build_graph(embed_query, search_pinecone, rerank_chunks)
    return _agent_graph


@app.route(route="agent", methods=["POST", "OPTIONS"])
def agent(req: func.HttpRequest) -> func.HttpResponse:
    """LangGraph agent: classify → route → retrieve (with optional decomposition) → guard."""
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=get_cors_headers(req))
    try:
        enforce_rate_limit(req)
    except PermissionError:
        return func.HttpResponse(json.dumps({"error": "Too many requests. Please wait a minute and try again."}), status_code=429, headers=get_cors_headers(req))
    try:
        query = validate_json_payload(req)
    except ValueError as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=400, headers=get_cors_headers(req))

    log_question(req, query, mode="openai")

    graph = get_agent_graph()
    result = graph.invoke({
        "query": query,
        "query_type": "",
        "sub_queries": [],
        "chunks": [],
        "context_sufficient": True,
        "guard_reason": "",
        "steps": [],
    })

    return func.HttpResponse(
        json.dumps({
            "query": query,
            "chunks": [{"section": c["section"], "title": c["title"], "text": c["text"]} for c in result["chunks"]],
            "agent": {
                "query_type": result["query_type"],
                "sub_queries": result["sub_queries"],
                "context_sufficient": result["context_sufficient"],
                "guard_reason": result["guard_reason"],
                "steps": result["steps"],
            },
        }),
        mimetype="application/json",
        headers=get_cors_headers(req),
    )