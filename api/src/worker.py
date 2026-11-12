import hmac
import ipaddress
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from workers import Response, WorkerEntrypoint

from common import ApiError
from constants import SYSTEM_PROMPT
from persistence import _cache_get, _cache_key, _cache_put, _log_question, _question_sha
from retrieval import _public_chunks, _retrieve_chunks, _run_agent
from security import _issue_session, _read_json_body, _validate_query, _verify_session, _verify_turnstile
from streaming import _cached_stream, _stream_generation


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        origin = request.headers.get("origin", "")
        allowed = [value.strip() for value in getattr(self.env, "ALLOWED_ORIGINS", "").split(",") if value.strip()]
        if not allowed:
            return Response.json({"error": "Allowed origins are not configured"}, status=503)
        if origin not in allowed:
            return Response.json({"error": "Origin is not allowed"}, status=403)

        if request.method == "OPTIONS":
            response = Response(None, status=204)
        else:
            response = await self._handle_request(request, origin)
        response.js_object.headers.set("Access-Control-Allow-Origin", origin)
        response.js_object.headers.set("Vary", "Origin")
        response.js_object.headers.set("Access-Control-Allow-Methods", "POST, OPTIONS")
        response.js_object.headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Aeroquery-Key")
        response.js_object.headers.set("Cache-Control", "no-store")
        return response

    async def _handle_request(self, request, origin):
        correlation_id = uuid.uuid4().hex
        stage = "authentication"
        try:
            expected_key = getattr(self.env, "API_WORKER_KEY", "")
            provided_key = request.headers.get("x-aeroquery-key", "")
            if not isinstance(expected_key, str) or len(expected_key) < 32 or not expected_key.isascii():
                raise ApiError("Worker authentication is not configured", 503)
            if (
                not provided_key.isascii()
                or len(provided_key) > 256
                or not hmac.compare_digest(provided_key, expected_key)
            ):
                raise ApiError("Unauthorized", 401)

            stage = "rate_limit"
            forwarded_ip = (request.headers.get("cf-connecting-ip", "") or "").strip()
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

            if request.method != "POST" or path not in ("/auth/anonymous", "/retrieve", "/stream"):
                raise ApiError("Not found", 404)

            stage = "request_validation"
            body = await _read_json_body(request)
            if path == "/auth/anonymous":
                stage = "turnstile_verification"
                await _verify_turnstile(self.env, body.get("turnstile_token"), origin, client_ip)
                token, expires_at = await _issue_session(self.env, origin)
                return Response.json({"token": token, "expires_at": expires_at})
            stage = "session_authentication"
            anon_id = await _verify_session(self.env, request.headers.get("authorization", ""), origin)
            stage = "session_rate_limit"
            session_limit = await self.env.SESSION_RATE_LIMITER.limit({"key": anon_id})
            if not session_limit.success:
                raise ApiError("Too many session requests. Please wait a minute and try again.", 429)
            query = _validate_query(body.get("query"))

            use_rag = body.get("use_rag", False)
            if not isinstance(use_rag, bool):
                raise ApiError("use_rag must be a boolean", 400)

            mode = "webllm" if path == "/retrieve" else "openai_rag" if use_rag else "openai_bare"
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

            agent = None
            sources = []
            prompt_sources = None
            context_sufficient = None
            if use_rag:
                stage = "agent_retrieval"
                result = await _run_agent(self.env, query)
                agent = result["agent"]
                sources = _public_chunks(result["chunks"])
                prompt_sources = sources
                context_sufficient = agent["context_sufficient"]
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    "The following JSON contains the question and any untrusted reference data. Answer the question in Markdown, not JSON:\n"
                    + json.dumps({"question": query, "sources": prompt_sources, "context_sufficient": context_sufficient}, ensure_ascii=False)
                )},
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
