import hmac
import ipaddress
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from js import Object, ReadableStream, TextEncoder
from pyodide.ffi import create_proxy, to_js
from workers import Response, WorkerEntrypoint, fetch


MAX_QUERY_LENGTH = 1500
MAX_BODY_BYTES = 32768
RERANK_MODEL = "rerank-v4.0-pro"
NAMESPACE = "canada"
RAG_SYSTEM_PROMPT = """You are an expert aviation regulation assistant specializing in Canadian aviation regulations (TC AIM - Transport Canada Aeronautical Information Manual).

Rules:
1. Answer ONLY based on the provided context. Do not use any outside knowledge.
2. Cite specific section numbers (e.g., "Section 2.3.1") in your answer.
3. If the context does not contain enough information to answer the question, say: "I don't have enough information in the available regulations to answer this question."
4. Be precise and concise. Pilots need clear, unambiguous answers.
5. If multiple sections are relevant, reference all of them."""
BARE_SYSTEM_PROMPT = "You are an aviation regulation expert. Answer based on your knowledge of Canadian aviation regulations."

class ApiError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


def _js_options(options):
    return to_js(options, dict_converter=Object.fromEntries)


async def _json_request(url, payload, headers=None):
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    response = await fetch(url, _js_options({
        "method": "POST",
        "headers": request_headers,
        "body": json.dumps(payload),
    }))
    if not response.ok:
        raise ApiError("An upstream service request failed", 502)
    return await response.json()


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
    payload = {"model": model, "messages": messages, "temperature": 0}
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    response = await fetch(
        f"{_azure_base(env)}/chat/completions",
        _js_options({
            "method": "POST",
            "headers": {"Content-Type": "application/json", **_azure_headers(env)},
            "body": json.dumps(payload),
        }),
    )
    return response, model


async def _embed_query(env, query):
    model = getattr(env, "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
    result = await _json_request(
        f"{_azure_base(env)}/embeddings",
        {"model": model, "input": [query]},
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
        {"Api-Key": api_key, "X-Pinecone-Api-Version": "2025-01"},
    )
    chunks = []
    for match in result.get("matches", []):
        metadata = match.get("metadata") or {}
        chunks.append({
            "score": match.get("score", 0),
            "text": metadata.get("text", ""),
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
        line.strip()
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


async def _log_question(env, query, mode):
    now = datetime.now(timezone.utc)
    query_text = query if getattr(env, "QUESTIONS_LOG_QUERY_TEXT", "false").lower() == "true" else None
    try:
        await env.DB.prepare(
            "INSERT INTO question_logs (id, created_at, query_length, mode, query_text) "
            "VALUES (?, ?, ?, ?, ?)"
        ).bind(
            uuid.uuid4().hex,
            now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            len(query),
            mode,
            query_text,
        ).run()
    except Exception as error:
        print(json.dumps({"event": "question_log_failed", "error_type": type(error).__name__}))


def _public_chunks(chunks):
    return [
        {"section": chunk["section"], "title": chunk["title"], "text": chunk["text"]}
        for chunk in chunks
    ]


def _sse_event(event):
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


async def _stream_generation(env, messages, model, sources=None, agent=None):
    response, _ = await _azure_chat(env, messages, stream=True)
    if not response.ok:
        raise ApiError("Azure Foundry generation failed", 502)

    encoder = TextEncoder.new()

    async def start(controller):
        if sources:
            controller.enqueue(encoder.encode(_sse_event({
                "type": "sources",
                "sources": sources,
                "model": model,
                "agent": agent,
            })))

        reader = response.body.getReader()
        buffer = b""

        def emit_line(raw_line):
            line = raw_line.decode("utf-8").rstrip("\r")
            if not line.startswith("data: "):
                return
            data = line[6:].strip()
            if not data or data == "[DONE]":
                return
            event = json.loads(data)
            choices = event.get("choices") or []
            token = choices[0].get("delta", {}).get("content") if choices else None
            if token:
                controller.enqueue(encoder.encode(_sse_event({"type": "token", "token": token})))
            usage = event.get("usage")
            if usage:
                controller.enqueue(encoder.encode(_sse_event({
                    "type": "usage",
                    "tokens": usage.get("total_tokens"),
                    "model": model,
                })))

        try:
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
            controller.enqueue(encoder.encode(_sse_event({"type": "done"})))
            controller.close()
        except Exception as error:
            print(json.dumps({"event": "generation_stream_failed", "error_type": type(error).__name__}))
            controller.error(error)

    stream = ReadableStream.new(_js_options({"start": create_proxy(start)}))
    return Response(stream, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Content-Type-Options": "nosniff",
    })


class Default(WorkerEntrypoint):
    async def fetch(self, request):
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

            body = await _read_json_body(request)
            query = _validate_query(body.get("query"))

            if path == "/retrieve":
                await _log_question(self.env, query, "webllm")
                chunks = await _retrieve_chunks(self.env, query)
                return Response.json({"query": query, "chunks": _public_chunks(chunks)})

            if path == "/agent":
                await _log_question(self.env, query, "openai")
                result = await _run_agent(self.env, query)
                return Response.json({
                    "query": query,
                    "chunks": _public_chunks(result["chunks"]),
                    "agent": result["agent"],
                })

            use_rag = body.get("use_rag", False)
            if not isinstance(use_rag, bool):
                raise ApiError("use_rag must be a boolean", 400)

            agent = None
            sources = []
            if use_rag:
                await _log_question(self.env, query, "openai")
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
            return await _stream_generation(self.env, messages, model, sources, agent)
        except ApiError as error:
            return Response.json({"error": str(error)}, status=error.status)
        except Exception as error:
            print(json.dumps({"event": "request_failed", "error_type": type(error).__name__}))
            return Response.json({"error": "Request failed"}, status=502)

    async def scheduled(self, controller, env, ctx):
        worker_env = self.env
        retention_days = max(1, int(getattr(worker_env, "QUESTIONS_RETENTION_DAYS", "30")))
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        await worker_env.DB.prepare(
            "DELETE FROM question_logs WHERE created_at < ?"
        ).bind(cutoff.isoformat(timespec="milliseconds").replace("+00:00", "Z")).run()