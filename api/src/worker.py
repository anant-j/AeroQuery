import hashlib
import hmac
import ipaddress
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from js import AbortSignal, Object, ReadableStream, TextEncoder
from pyodide.ffi import create_proxy, to_js
from workers import Response, WorkerEntrypoint, fetch

from constants import (
    BARE_SYSTEM_PROMPT,
    CACHE_VERSION,
    DEFAULT_CACHE_TTL_SECONDS,
    MAX_BODY_BYTES,
    MAX_CHUNK_LENGTH,
    MAX_COMPLETION_TOKENS,
    MAX_QUERY_LENGTH,
    NAMESPACE,
    RAG_SYSTEM_PROMPT,
    RERANK_MODEL,
)


class ApiError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


def _js_options(options):
    return to_js(options, dict_converter=Object.fromEntries)


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


def _validate_query(query):
    if not isinstance(query, str):
        raise ApiError("query must be a string", 400)
    query = query.strip()
    if not query:
        raise ApiError("query is required", 400)
    if len(query) > MAX_QUERY_LENGTH:
        raise ApiError(f"query must be shorter than {MAX_QUERY_LENGTH} characters", 400)
    return query


async def _read_json_body(request):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ApiError("Content-Type must be application/json", 415)
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                raise ApiError("Request body is too large", 413)
        except ValueError:
            raise ApiError("Invalid Content-Length", 400)
    reader = request.body.getReader()
    body_parts = []
    body_size = 0
    while True:
        item = await reader.read()
        if item.done:
            break
        part = item.value.to_bytes()
        body_size += len(part)
        if body_size > MAX_BODY_BYTES:
            await reader.cancel()
            raise ApiError("Request body is too large", 413)
        body_parts.append(part)
    try:
        body = json.loads(b"".join(body_parts).decode("utf-8"))
    except Exception:
        raise ApiError("Invalid JSON", 400)
    if not isinstance(body, dict):
        raise ApiError("Request body must be a JSON object", 400)
    return body


async def _log_question(env, query, mode, question_sha, correlation_id, answered_from_cache=False):
    now = datetime.now(timezone.utc)
    query_text = query if getattr(env, "QUESTIONS_LOG_QUERY_TEXT", "false").lower() == "true" else None
    log_event = {"event": "question_received", "correlation_id": correlation_id, "mode": mode, "answered_from_cache": answered_from_cache}
    if query_text is not None:
        log_event["query_text"] = query_text
    print(json.dumps(log_event))
    try:
        await env.DB.prepare(
            "INSERT INTO question_logs (correlation_id, created_at, mode, question_asked, question_sha, answered_from_cache) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        ).bind(
            correlation_id,
            now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            mode,
            query_text,
            question_sha,
            int(answered_from_cache),
        ).run()
    except Exception as error:
        print(json.dumps({"event": "question_log_failed", "correlation_id": correlation_id, "error_type": type(error).__name__}))


def _public_chunks(chunks):
    return [
        {"section": chunk["section"], "title": chunk["title"], "text": chunk["text"]}
        for chunk in chunks
    ]


def _question_sha(query, mode):
    return hashlib.sha256(json.dumps([query, mode], ensure_ascii=False).encode("utf-8")).hexdigest()


def _cache_key(env, path, query, use_rag):
    settings = [
        CACHE_VERSION, path, query, use_rag,
        getattr(env, "AZURE_OPENAI_DEPLOYMENT", "") or getattr(env, "LLM_MODEL", "") or "gpt-5.4-mini",
        getattr(env, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large"),
        getattr(env, "AZURE_OPENAI_ENDPOINT", ""),
        getattr(env, "PINECONE_INDEX_HOST", ""), NAMESPACE, RERANK_MODEL,
    ]
    return hashlib.sha256(json.dumps(settings, ensure_ascii=False).encode("utf-8")).hexdigest()


def _cache_ttl(env):
    try:
        return max(0, int(getattr(env, "CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL_SECONDS


async def _cache_get(env, key):
    if not _cache_ttl(env):
        return None
    try:
        value = await env.ANSWER_CACHE.get(key)
        return json.loads(value) if value is not None else None
    except Exception as error:
        print(json.dumps({"event": "cache_read_failed", "error_type": type(error).__name__}))
        return None


async def _cache_put(env, key, value):
    ttl = _cache_ttl(env)
    if not ttl:
        return
    try:
        await env.ANSWER_CACHE.put(key, json.dumps(value), _js_options({"expirationTtl": max(60, ttl)}))
    except Exception as error:
        print(json.dumps({"event": "cache_write_failed", "error_type": type(error).__name__}))


def _sse_event(event):
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


async def _stream_generation(env, messages, model, sources=None, agent=None, cache_key=None, correlation_id=None):
    response, _ = await _azure_chat(env, messages, stream=True)
    if not response.ok:
        raise ApiError("Azure Foundry generation failed", 502)

    encoder = TextEncoder.new()

    async def start(controller):
        buffer = b""
        tokens = []
        completed = False
        finish_reason = None

        def emit_line(raw_line):
            nonlocal completed, finish_reason
            line = raw_line.decode("utf-8").rstrip("\r")
            if not line.startswith("data: "):
                return
            data = line[6:].strip()
            if data == "[DONE]":
                completed = True
                return
            if not data:
                return
            event = json.loads(data)
            if event.get("error"):
                raise ValueError("Upstream stream error")
            choices = event.get("choices") or []
            token = choices[0].get("delta", {}).get("content") if choices else None
            if token:
                tokens.append(token)
                controller.enqueue(encoder.encode(_sse_event({"type": "token", "token": token})))
            if choices and choices[0].get("finish_reason"):
                finish_reason = choices[0]["finish_reason"]
            usage = event.get("usage")
            if usage:
                controller.enqueue(encoder.encode(_sse_event({
                    "type": "usage",
                    "tokens": usage.get("total_tokens"),
                    "model": model,
                })))

        try:
            if sources:
                controller.enqueue(encoder.encode(_sse_event({
                    "type": "sources",
                    "sources": sources,
                    "model": model,
                    "agent": agent,
                })))
            reader = response.body.getReader()
            while True:
                item = await reader.read()
                if item.done:
                    break
                buffer += item.value.to_bytes()
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    emit_line(line)
            if buffer:
                emit_line(buffer)
            if cache_key and completed and finish_reason == "stop" and tokens:
                await _cache_put(env, cache_key, {"answer": "".join(tokens), "sources": sources, "agent": agent, "model": model})
            controller.enqueue(encoder.encode(_sse_event({"type": "done"})))
            controller.close()
        except Exception as error:
            print(json.dumps({"event": "generation_stream_failed", "correlation_id": correlation_id, "error_type": type(error).__name__}))
            controller.error(error)

    stream = ReadableStream.new(_js_options({"start": create_proxy(start)}))
    return Response(stream, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Content-Type-Options": "nosniff",
    })


def _cached_stream(value):
    events = []
    if value.get("sources"):
        events.append({"type": "sources", "sources": value["sources"], "model": value["model"], "agent": value.get("agent")})
    events.append({"type": "token", "token": value["answer"]})
    events.append({"type": "usage", "tokens": 0, "model": value["model"]})
    events.append({"type": "done"})
    return Response("".join(_sse_event(event) for event in events), headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Content-Type-Options": "nosniff",
    })


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        correlation_id = uuid.uuid4().hex
        stage = "authentication"
        try:
            expected_key = getattr(self.env, "API_WORKER_KEY", "")
            provided_key = request.headers.get("x-aeroquery-key", "")
            if not isinstance(expected_key, str) or len(expected_key) < 32:
                raise ApiError("Worker authentication is not configured", 503)
            if (
                not provided_key.isascii()
                or len(provided_key) > 256
                or not hmac.compare_digest(provided_key, expected_key)
            ):
                raise ApiError("Unauthorized", 401)

            stage = "rate_limit"
            forwarded_ip = (request.headers.get("x-aeroquery-client-ip", "") or "").strip()
            try:
                client_ip = ipaddress.ip_address(forwarded_ip).compressed
            except ValueError:
                client_ip = "unknown"
            rate_limit = await self.env.API_RATE_LIMITER.limit({"key": client_ip})
            if not rate_limit.success:
                raise ApiError("Too many requests. Please wait a minute and try again.", 429)

            path = urlparse(request.url).path.rstrip("/")
            if path.startswith("/api/"):
                path = path[4:]

            if request.method != "POST" or path not in ("/retrieve", "/agent", "/stream"):
                raise ApiError("Not found", 404)

            stage = "request_validation"
            body = await _read_json_body(request)
            query = _validate_query(body.get("query"))

            use_rag = body.get("use_rag", False)
            if not isinstance(use_rag, bool):
                raise ApiError("use_rag must be a boolean", 400)

            mode = "webllm" if path == "/retrieve" else "openai_rag" if path == "/agent" or use_rag else "openai_bare"
            cache_key = _cache_key(self.env, path, query, use_rag if path == "/stream" else False)
            cached = await _cache_get(self.env, cache_key)
            stage = "question_logging"
            await _log_question(self.env, query, mode, _question_sha(query, mode), correlation_id, cached is not None)
            if cached is not None:
                return _cached_stream(cached) if path == "/stream" else Response.json(cached)

            if path == "/retrieve":
                stage = "retrieval"
                chunks = await _retrieve_chunks(self.env, query)
                result = {"query": query, "chunks": _public_chunks(chunks)}
                await _cache_put(self.env, cache_key, result)
                return Response.json(result)

            if path == "/agent":
                stage = "agent_retrieval"
                result = await _run_agent(self.env, query)
                result = {
                    "query": query,
                    "chunks": _public_chunks(result["chunks"]),
                    "agent": result["agent"],
                }
                await _cache_put(self.env, cache_key, result)
                return Response.json(result)

            agent = None
            sources = []
            if use_rag:
                stage = "agent_retrieval"
                result = await _run_agent(self.env, query)
                agent = result["agent"]
                sources = _public_chunks(result["chunks"])
                context = "\n\n---\n\n".join(
                    f"[Section {chunk['section']}]\n{chunk['text']}"
                    for chunk in result["chunks"]
                )
                system_prompt = RAG_SYSTEM_PROMPT
                if agent["context_sufficient"] is False:
                    system_prompt += (
                        "\n\nIMPORTANT: The retrieval system flagged low confidence for this query. "
                        "If the context does not clearly answer the question, say so."
                    )
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": (
                        "Context from Canadian Aviation Regulations:\n\n"
                        f"{context}\n\n---\n\nQuestion: {query}"
                    )},
                ]
            else:
                messages = [
                    {"role": "system", "content": BARE_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ]

            model = (
                getattr(self.env, "AZURE_OPENAI_DEPLOYMENT", "")
                or getattr(self.env, "LLM_MODEL", "")
                or "gpt-5.4-mini"
            )
            stage = "generation"
            return await _stream_generation(self.env, messages, model, sources, agent, cache_key, correlation_id)
        except ApiError as error:
            return Response.json({"error": str(error)}, status=error.status)
        except Exception as error:
            print(json.dumps({
                "event": "request_failed",
                "correlation_id": correlation_id,
                "stage": stage,
                "error_type": type(error).__name__,
            }))
            return Response.json({"error": "Request failed"}, status=502)
    async def scheduled(self, controller, env, ctx):
        worker_env = self.env
        retention_days = max(1, int(getattr(worker_env, "QUESTIONS_RETENTION_DAYS", "30")))
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        await worker_env.DB.prepare(
            "DELETE FROM question_logs WHERE created_at < ?"
        ).bind(cutoff.isoformat(timespec="milliseconds").replace("+00:00", "Z")).run()