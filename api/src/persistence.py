import hashlib
import json
from datetime import datetime, timezone

from common import _js_options, _log
from constants import CACHE_VERSION, DEFAULT_CACHE_TTL_SECONDS, NAMESPACE, RERANK_MODEL


async def _log_question(env, query, mode, cache_key, answered_from_cache=False, *, user_uuid, session_id, ip_address, model=None, corpus_version=None):
    now = datetime.now(timezone.utc)
    query_text = query if getattr(env, "QUESTIONS_LOG_QUERY_TEXT", "false").lower() == "true" else None
    try:
        row = await env.DB.prepare(
            "INSERT INTO question_logs (created_at, mode, question_asked, cache_key, answered_from_cache, user_uuid, session_id, ip_address, model, corpus_version, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING rowid AS log_rowid"
        ).bind(
            now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            mode,
            query_text,
            cache_key,
            int(answered_from_cache),
            user_uuid,
            session_id,
            ip_address,
            model,
            corpus_version,
            "pending",
        ).first()
        if row is None:
            raise RuntimeError("Question log insert returned no row")
        return int(row.log_rowid)
    except Exception as error:
        _log("error", "question_log_failed", error_type=type(error).__name__)
        return None


def _source_metadata(chunks):
    return [
        {key: chunk[key] for key in ("id", "section", "title", "source_url", "score", "rerank_score") if key in chunk}
        for chunk in chunks
    ]


async def _finish_question(env, log_rowid, *, outcome, http_status, duration_ms, answer=None, failure_stage=None,
                           error_type=None, retrieval_ms=None, generation_ms=None, sources=None, usage=None, finish_reason=None):
    usage = usage or {}
    try:
        if log_rowid is not None:
            await env.DB.prepare(
                "UPDATE question_logs SET outcome = ?, http_status = ?, duration_ms = ?, answer = ?, failure_stage = ?, "
                "error_type = ?, retrieval_ms = ?, generation_ms = ?, sources_json = ?, input_tokens = ?, "
                "output_tokens = ?, finish_reason = ? WHERE rowid = ?"
            ).bind(
                outcome, http_status, duration_ms, answer, failure_stage, error_type, retrieval_ms, generation_ms,
                json.dumps(sources) if sources is not None else None,
                usage.get("prompt_tokens"), usage.get("completion_tokens"),
                finish_reason, log_rowid,
            ).run()
    except Exception as error:
        _log("error", "question_log_update_failed", error_type=type(error).__name__)


def _cache_key(env, path, query, use_rag, model=None):
    settings = [
        CACHE_VERSION, path, query, use_rag,
        model or getattr(env, "AZURE_OPENAI_DEPLOYMENT", "") or getattr(env, "LLM_MODEL", "") or "gpt-5.4-mini",
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
        _log("error", "cache_read_failed", error_type=type(error).__name__)
        return None


async def _cache_put(env, key, value):
    ttl = _cache_ttl(env)
    if not ttl:
        return
    try:
        await env.AEROQUERY_ANSWER_CACHE.put(key, json.dumps(value), _js_options({"expirationTtl": max(60, ttl)}))
    except Exception as error:
        _log("error", "cache_write_failed", error_type=type(error).__name__)
