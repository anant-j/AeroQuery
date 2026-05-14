import azure.functions as func
import json
import os
from openai import OpenAI
from pinecone import Pinecone
import cohere
from agent import build_graph

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

EMBEDDING_MODEL = "text-embedding-3-large"
RERANK_MODEL = "rerank-v4.0-pro"
NAMESPACE = "canada"

# Module-level clients — reused across requests, no re-init overhead
openai_client = None
pinecone_index = None
cohere_client = None


def get_openai():
    global openai_client
    if openai_client is None:
        openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
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


@app.route(route="warmup", methods=["GET", "POST"])
def warmup(req: func.HttpRequest) -> func.HttpResponse:
    """Lightweight warmup — pre-initializes module-level clients so the
    first real request avoids cold-start latency. Safe to call repeatedly.
    """
    get_openai()
    get_pinecone_index()
    get_cohere()
    get_agent_graph()
    return func.HttpResponse(
        json.dumps({"status": "warm"}),
        mimetype="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.route(route="retrieve", methods=["POST"])
def retrieve(req: func.HttpRequest) -> func.HttpResponse:
    """Retrieve + rerank — embed query, search Pinecone, rerank with Cohere."""
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400)

    query = body.get("query", "")
    if not query:
        return func.HttpResponse(json.dumps({"error": "query is required"}), status_code=400)

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
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON"}), status_code=400)

    query = body.get("query", "")
    if not query:
        return func.HttpResponse(json.dumps({"error": "query is required"}), status_code=400)

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