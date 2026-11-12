import hashlib
import json
from datetime import datetime, timezone

from common import _js_options
from constants import CACHE_VERSION, DEFAULT_CACHE_TTL_SECONDS, NAMESPACE, RERANK_MODEL


async def _log_question(env, query, mode, question_sha, correlation_id, answered_from_cache=False):
    now = datetime.now(timezone.utc)
    query_text = query if getattr(env, "QUESTIONS_LOG_QUERY_TEXT", "false").lower() == "true" else None
    log_event = {"event": "question_received", "correlation_id": correlation_id, "mode": mode, "answered_from_cache": answered_from_cache}
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
        value = await env.AEROQUERY_ANSWER_CACHE.get(key)
        return json.loads(value) if value is not None else None
    except Exception as error:
        print(json.dumps({"event": "cache_read_failed", "error_type": type(error).__name__}))
        return None


async def _cache_put(env, key, value):
    ttl = _cache_ttl(env)
    if not ttl:
        return
    try:
        await env.AEROQUERY_ANSWER_CACHE.put(key, json.dumps(value), _js_options({"expirationTtl": max(60, ttl)}))
    except Exception as error:
        print(json.dumps({"event": "cache_write_failed", "error_type": type(error).__name__}))
