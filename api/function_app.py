import azure.functions as func
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

MAX_QUERY_LENGTH = 1500
MAX_BODY_BYTES = 32768
REQUEST_RATE_LIMIT = 30
RATE_LIMIT_WINDOW_MS = 60000
MAX_RATE_LIMIT_CLIENTS = 8192
_request_timestamps = {}
_next_rate_limit_prune = 0


class PayloadTooLargeError(Exception):
    pass

from azure.data.tables import TableServiceClient
from openai import OpenAI
from pinecone import Pinecone
import cohere
from agent import build_graph

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

logger = logging.getLogger("aeroquery.queries")

# Question log — persisted to Azure Table Storage on the storage account the
# Function App already uses (AzureWebJobsStorage). Durable, never sampled, and
# directly queryable from the Portal / Storage Explorer / SDK. No new infra.
QUESTIONS_TABLE = os.environ.get("QUESTIONS_TABLE_NAME", "QuestionLog")
QUESTIONS_RETENTION_DAYS = max(1, int(os.environ.get("QUESTIONS_RETENTION_DAYS", "30")))
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


def enforce_rate_limit(req: func.HttpRequest):
    global _next_rate_limit_prune
    ip = (req.headers.get("X-AeroQuery-Client-IP", "") or "unknown").strip() or "unknown"
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    if now >= _next_rate_limit_prune:
        for client, timestamps in list(_request_timestamps.items()):
            recent = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW_MS]
            if recent:
                _request_timestamps[client] = recent
            else:
                del _request_timestamps[client]
        _next_rate_limit_prune = now + RATE_LIMIT_WINDOW_MS
    if ip not in _request_timestamps and len(_request_timestamps) >= MAX_RATE_LIMIT_CLIENTS:
        raise PermissionError("Rate limit capacity exceeded")
    bucket = _request_timestamps.setdefault(ip, [])
    bucket.append(now)
    if len(bucket) > REQUEST_RATE_LIMIT:
        raise PermissionError("Rate limit exceeded")


def validate_json_payload(req: func.HttpRequest):
    raw_body = req.get_body()
    if len(raw_body) > MAX_BODY_BYTES:
        raise PayloadTooLargeError("Request body is too large")
    try:
        body = json.loads(raw_body)
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
    return query


def log_question(req: func.HttpRequest, query: str, mode: str) -> None:
    """Record minimal, time-limited question metadata. Query text is opt-in.
    Best-effort — logging must never break a request.
    """
    try:
        table = get_questions_table()
        if table is None:
            return

        now = datetime.now(timezone.utc)
        # PartitionKey = day (fast per-day scans); RowKey = reverse-time ordered
        # so newest rows sort first, with a uuid suffix to guarantee uniqueness.
        ticks = int((datetime.max - now.replace(tzinfo=None)).total_seconds() * 1000)
        entity = {
            "PartitionKey": now.strftime("%Y-%m-%d"),
            "RowKey": f"{ticks:020d}-{uuid.uuid4().hex}",
            "timestamp": now.isoformat(),
            "query_length": len(query),
            "mode": mode,
        }
        if os.environ.get("QUESTIONS_LOG_QUERY_TEXT", "false").lower() == "true":
            entity["query"] = query
        table.create_entity(entity)
    except Exception:  # pragma: no cover - logging must never fail a request
        logger.exception("Failed to log question")


@app.timer_trigger(schedule="0 0 3 * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def purge_expired_question_logs(timer: func.TimerRequest) -> None:
    table = get_questions_table()
    if table is None:
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=QUESTIONS_RETENTION_DAYS)).strftime("%Y-%m-%d")
    expired = table.query_entities(
        query_filter=f"PartitionKey lt '{cutoff}'",
        select=["PartitionKey", "RowKey"],
    )
    for entity in expired:
        table.delete_entity(entity["PartitionKey"], entity["RowKey"])


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


@app.route(route="warmup", methods=["GET"])
def warmup(req: func.HttpRequest) -> func.HttpResponse:
    try:
        get_openai()
        get_pinecone_index()
        get_cohere()
        get_agent_graph()
    except Exception:
        logger.exception("Function warmup failed")
        return func.HttpResponse(
            json.dumps({"status": "error"}),
            status_code=503,
            mimetype="application/json",
        )

    return func.HttpResponse(
        json.dumps({"status": "warm"}),
        mimetype="application/json",
    )


@app.route(route="retrieve", methods=["POST"])
def retrieve(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve + rerank — embed query, search Pinecone, rerank with Cohere."""
    try:
        enforce_rate_limit(req)
    except PermissionError:
        return func.HttpResponse(json.dumps({"error": "Too many requests. Please wait a minute and try again."}), status_code=429)
    try:
        query = validate_json_payload(req)
    except PayloadTooLargeError as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=413, mimetype="application/json")
    except ValueError as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=400, mimetype="application/json")

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


@app.route(route="agent", methods=["POST"])
def agent(req: func.HttpRequest) -> func.HttpResponse:
    """LangGraph agent: classify → route → retrieve (with optional decomposition) → guard."""
    try:
        enforce_rate_limit(req)
    except PermissionError:
        return func.HttpResponse(json.dumps({"error": "Too many requests. Please wait a minute and try again."}), status_code=429)
    try:
        query = validate_json_payload(req)
    except PayloadTooLargeError as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=413, mimetype="application/json")
    except ValueError as exc:
        return func.HttpResponse(json.dumps({"error": str(exc)}), status_code=400, mimetype="application/json")

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
    )