import json

from js import AbortSignal
from workers import fetch

from common import ApiError
from constants import MAX_CHUNK_LENGTH, MAX_COMPLETION_TOKENS, MAX_QUERY_LENGTH, NAMESPACE, RERANK_MODEL


async def _json_request(url, payload, service, headers=None):
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        response = await fetch(
            url,
            method="POST",
            headers=request_headers,
            body=json.dumps(payload),
            signal=AbortSignal.timeout(60000),
        )
    except Exception as error:
        print(json.dumps({
            "event": "upstream_exception",
            "service": service,
            "error_type": type(error).__name__,
        }))
        raise
    if not response.ok:
        print(json.dumps({
            "event": "upstream_http_error",
            "service": service,
            "status": response.status,
        }))
        raise ApiError("An upstream service request failed", 502)
    try:
        return await response.json()
    except Exception as error:
        print(json.dumps({
            "event": "upstream_response_error",
            "service": service,
            "error_type": type(error).__name__,
        }))
        raise


def _azure_base(env):
    endpoint = getattr(env, "AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    if not endpoint:
        raise ApiError("Azure Foundry is not configured")
    return f"{endpoint}/openai/v1"


def _azure_headers(env):
    api_key = getattr(env, "AZURE_OPENAI_API_KEY", "")
    if not api_key:
        raise ApiError("Azure Foundry is not configured")
    return {"Authorization": f"Bearer {api_key}"}


async def _azure_chat(env, messages, stream=False):
    model = (
        getattr(env, "AZURE_OPENAI_DEPLOYMENT", "")
        or getattr(env, "LLM_MODEL", "")
        or "gpt-5.4-mini"
    )
    payload = {"model": model, "messages": messages, "temperature": 0, "max_completion_tokens": MAX_COMPLETION_TOKENS}
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    try:
        response = await fetch(
            f"{_azure_base(env)}/chat/completions",
            method="POST",
            headers={"Content-Type": "application/json", **_azure_headers(env)},
            body=json.dumps(payload),
            signal=AbortSignal.timeout(60000),
        )
    except Exception as error:
        print(json.dumps({
            "event": "upstream_exception",
            "service": "azure_chat",
            "error_type": type(error).__name__,
        }))
        raise
    return response, model


async def _embed_query(env, query):
    model = getattr(env, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    result = await _json_request(
        f"{_azure_base(env)}/embeddings",
        {"model": model, "input": [query]},
        "azure_embeddings",
        _azure_headers(env),
    )
    return result["data"][0]["embedding"]


async def _search_pinecone(env, vector, top_k=10):
    host = getattr(env, "PINECONE_INDEX_HOST", "").strip().rstrip("/")
    api_key = getattr(env, "PINECONE_API_KEY", "")
    if not host or not api_key:
        raise ApiError("Pinecone is not configured")
    if not host.startswith("https://"):
        host = f"https://{host}"

    result = await _json_request(
        f"{host}/query",
        {
            "vector": vector,
            "topK": top_k,
            "namespace": NAMESPACE,
            "includeMetadata": True,
        },
        "pinecone_query",
        {"Api-Key": api_key, "X-Pinecone-Api-Version": "2025-01"},
    )
    chunks = []
    for match in result.get("matches", []):
        metadata = match.get("metadata") or {}
        chunks.append({
            "score": match.get("score", 0),
            "text": metadata.get("text", "")[:MAX_CHUNK_LENGTH],
            "section": metadata.get("section", ""),
            "title": metadata.get("title", ""),
        })
    return chunks


async def _rerank_chunks(env, query, chunks, top_n=5):
    if not chunks:
        return []
    api_key = getattr(env, "COHERE_API_KEY", "")
    if not api_key:
        raise ApiError("Cohere is not configured")
    response = await _json_request(
        "https://api.cohere.com/v2/rerank",
        {
            "model": RERANK_MODEL,
            "query": query,
            "documents": [chunk["text"] for chunk in chunks],
            "top_n": top_n,
        },
        "cohere_rerank",
        {"Authorization": f"Bearer {api_key}"},
    )
    reranked = []
    for result in response.get("results", []):
        chunk = chunks[result["index"]].copy()
        chunk["rerank_score"] = result["relevance_score"]
        reranked.append(chunk)
    return reranked


async def _retrieve_chunks(env, query):
    vector = await _embed_query(env, query)
    chunks = await _search_pinecone(env, vector)
    return await _rerank_chunks(env, query, chunks)


async def _classify_query(env, query):
    response, _ = await _azure_chat(env, [
        {"role": "system", "content": (
            "Classify the user's aviation regulation question as either 'simple' or 'complex'.\n"
            "- 'simple': a direct factual question about one topic (e.g., 'What are VFR fuel requirements?')\n"
            "- 'complex': a comparison, multi-part, or cross-reference question "
            "(e.g., 'Compare VFR and IFR fuel requirements', 'What are the differences between day and night VFR?')\n"
            "Respond with ONLY the word 'simple' or 'complex'."
        )},
        {"role": "user", "content": query},
    ])
    if not response.ok:
        raise ApiError("Azure Foundry request failed", 502)
    result = await response.json()
    query_type = result["choices"][0]["message"]["content"].strip().lower()
    return query_type if query_type in ("simple", "complex") else "simple"


async def _decompose_query(env, query):
    response, _ = await _azure_chat(env, [
        {"role": "system", "content": (
            "Break this aviation regulation question into 2-3 simpler sub-questions "
            "that can each be answered independently. Each sub-question should target "
            "a specific regulation or topic.\n"
            "Return ONLY the sub-questions, one per line. No numbering, no extra text."
        )},
        {"role": "user", "content": query},
    ])
    if not response.ok:
        raise ApiError("Azure Foundry request failed", 502)
    result = await response.json()
    sub_queries = [
        line.strip()[:MAX_QUERY_LENGTH]
        for line in result["choices"][0]["message"]["content"].strip().splitlines()
        if line.strip()
    ][:3]
    return sub_queries or [query]


async def _run_agent(env, query):
    query_type = await _classify_query(env, query)
    steps = [f"classify:{query_type}"]
    sub_queries = []

    if query_type == "simple":
        chunks = await _retrieve_chunks(env, query)
        steps.append("retrieve")
    else:
        sub_queries = await _decompose_query(env, query)
        steps.append(f"decompose:{len(sub_queries)}_sub_queries")
        chunks = []
        seen_sections = set()
        for sub_query in sub_queries:
            for chunk in await _retrieve_chunks(env, sub_query):
                key = (chunk["section"], chunk["text"][:100])
                if key not in seen_sections:
                    seen_sections.add(key)
                    chunks.append(chunk)
        steps.append(f"retrieve_multi:{len(chunks)}_chunks")

    if not chunks:
        context_sufficient = False
        guard_reason = "no_chunks_retrieved"
        steps.append("guard:fail_no_chunks")
    else:
        top_score = max(
            (chunk.get("rerank_score", chunk.get("score", 0)) for chunk in chunks),
            default=0,
        )
        if top_score < 0.1:
            context_sufficient = False
            guard_reason = "low_relevance"
            steps.append(f"guard:fail_low_relevance({top_score:.3f})")
        else:
            context_sufficient = True
            guard_reason = ""
            steps.append("guard:pass")

    return {
        "query": query,
        "chunks": chunks,
        "agent": {
            "query_type": query_type,
            "sub_queries": sub_queries,
            "context_sufficient": context_sufficient,
            "guard_reason": guard_reason,
            "steps": steps,
        },
    }


def _public_chunks(chunks):
    return [
        {"section": chunk["section"], "title": chunk["title"], "text": chunk["text"]}
        for chunk in chunks
    ]
