import importlib.util
import hmac
import json
import sqlite3
import sys
import tomllib
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class WorkerEntrypoint:
    pass


class Response:
    def __init__(self, body, headers=None, status=200):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.js_object = types.SimpleNamespace(headers=types.SimpleNamespace(set=self.headers.__setitem__))

    @staticmethod
    def json(body, status=200):
        return Response(body, status=status)

    def __getitem__(self, key):
        return getattr(self, key)

    def __eq__(self, other):
        return isinstance(other, Response) and self.body == other.body and self.status == other.status


class Database:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        schema_dir = Path(__file__).parents[1] / "schema"
        for schema in sorted(schema_dir.glob("*.sql")):
            self.connection.executescript(schema.read_text())

    def prepare(self, sql):
        database = self

        class Statement:
            def bind(self, *values):
                self.values = values
                return self

            async def first(self):
                row = database.connection.execute(sql, self.values).fetchone()
                return types.SimpleNamespace(cache_value=row[0]) if row else None

            async def run(self):
                database.connection.execute(sql, self.values)
                database.connection.commit()

        return Statement()


class Cache:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, value, options):
        self.values[key] = value
        self.ttls[key] = options["expirationTtl"]


js = types.ModuleType("js")
js.AbortSignal = js.Object = js.ReadableStream = js.TextEncoder = object()
js.Object = types.SimpleNamespace(fromEntries=lambda value: value)
js.Uint8Array = types.SimpleNamespace(new=lambda value: types.SimpleNamespace(to_bytes=lambda: value))
js.TextEncoder = types.SimpleNamespace(new=lambda: types.SimpleNamespace(encode=lambda value: value.encode()))


class Subtle:
    async def importKey(self, _format, key, _algorithm, _extractable, _usages):
        return key

    async def sign(self, _algorithm, key, message):
        return hmac.digest(key, message, "sha256")

    async def verify(self, _algorithm, key, signature, message):
        return hmac.compare_digest(signature, hmac.digest(key, message, "sha256"))


js.crypto = types.SimpleNamespace(subtle=Subtle())
ffi = types.ModuleType("pyodide.ffi")
ffi.create_proxy = ffi.to_js = lambda value, **kwargs: value
workers = types.ModuleType("workers")
workers.WorkerEntrypoint = WorkerEntrypoint
workers.Response = Response
workers.fetch = None

with patch.dict(sys.modules, {"js": js, "pyodide": types.ModuleType("pyodide"), "pyodide.ffi": ffi, "workers": workers}), patch.object(sys, "path", [str(Path(__file__).parents[1] / "src"), *sys.path]):
    spec = importlib.util.spec_from_file_location("worker_under_test", Path(__file__).parents[1] / "src" / "worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    import persistence
    import security
    import streaming


class WorkerSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_body_size_and_content_type_are_enforced(self):
        async def read_body(parts, content_type="application/json"):
            items = iter([*(types.SimpleNamespace(done=False, value=types.SimpleNamespace(to_bytes=lambda part=part: part)) for part in parts), types.SimpleNamespace(done=True)])
            reader = types.SimpleNamespace(read=AsyncMock(side_effect=lambda: next(items)), cancel=AsyncMock())
            request = types.SimpleNamespace(headers={"content-type": content_type}, body=types.SimpleNamespace(getReader=lambda: reader))
            return await worker._read_json_body(request)

        self.assertEqual(await read_body([b'{"query":"test"}']), {"query": "test"})
        for parts, content_type, status in [
            ([b"x" * (security.MAX_BODY_BYTES + 1)], "application/json", 413),
            ([b"{}"], "text/plain", 415),
            ([b"invalid"], "application/json", 400),
        ]:
            with self.assertRaises(worker.ApiError) as error:
                await read_body(parts, content_type)
            self.assertEqual(error.exception.status, status)

    async def test_sessions_enforce_signature_expiry_and_origin(self):
        env = types.SimpleNamespace(SESSION_JWT_SECRET="secret-" * 8)
        with patch.object(security.time, "time", return_value=1000):
            token, expires = await worker._issue_session(env, "https://example.test")
            self.assertEqual(expires, 1300)
            await worker._verify_session(env, f"Bearer {token}", "https://example.test")
            for candidate, origin in [
                (token.rsplit(".", 1)[0] + "." + ("A" if token.rsplit(".", 1)[1][0] != "A" else "B") + token.rsplit(".", 1)[1][1:], "https://example.test"),
                (token, "https://evil.test"),
                ("invalid", "https://example.test"),
            ]:
                with self.assertRaises(worker.ApiError):
                    await worker._verify_session(env, f"Bearer {candidate}", origin)
        with patch.object(security.time, "time", return_value=1300), self.assertRaises(worker.ApiError):
            await worker._verify_session(env, f"Bearer {token}", "https://example.test")

    async def test_turnstile_checks_hostname_action_and_single_use_result(self):
        env = types.SimpleNamespace(TURNSTILE_SECRET_KEY="real-secret")
        for result, success in [
            ({"success": True, "hostname": "example.test", "action": "aeroquery"}, True),
            ({"success": True, "hostname": "evil.test", "action": "aeroquery"}, False),
            ({"success": True, "hostname": "example.test", "action": "other"}, False),
            ({"success": False, "error-codes": ["timeout-or-duplicate"]}, False),
        ]:
            response = types.SimpleNamespace(ok=True, json=AsyncMock(return_value=result))
            with patch.object(security, "fetch", AsyncMock(return_value=response)), patch.object(security, "AbortSignal", types.SimpleNamespace(timeout=lambda value: value)):
                if success:
                    await worker._verify_turnstile(env, "token", "https://example.test", "127.0.0.1")
                else:
                    with self.assertRaises(worker.ApiError):
                        await worker._verify_turnstile(env, "token", "https://example.test", "127.0.0.1")

    async def test_cors_preflight_and_authentication_fail_closed(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(
            ALLOWED_ORIGINS="https://example.test", API_WORKER_KEY="a" * 32,
            SESSION_JWT_SECRET="secret-" * 8,
            API_RATE_LIMITER=types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True))),
        )
        request = types.SimpleNamespace(method="OPTIONS", url="https://worker.test/api/retrieve", headers={"origin": "https://example.test"})
        response = await entry.fetch(request)
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://example.test")
        request.headers["origin"] = "https://evil.test"
        self.assertEqual((await entry.fetch(request)).status, 403)
        request.headers = {"origin": "https://example.test", "x-aeroquery-key": "a" * 32, "cf-connecting-ip": "192.0.2.1", "x-aeroquery-client-ip": "1.1.1.1"}
        request.method = "POST"
        with patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": "test"})):
            response = await entry.fetch(request)
        self.assertEqual(response.status, 401)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://example.test")
        entry.env.API_RATE_LIMITER.limit.assert_awaited_with({"key": "192.0.2.1"})

    async def test_auth_exchange_and_protected_request(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(
            ALLOWED_ORIGINS="https://example.test", API_WORKER_KEY="a" * 32,
            SESSION_JWT_SECRET="secret-" * 8,
            API_RATE_LIMITER=types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True))),
        )
        request = types.SimpleNamespace(
            method="POST", url="https://worker.test/auth/anonymous",
            headers={"origin": "https://example.test", "x-aeroquery-key": "a" * 32, "cf-connecting-ip": "192.0.2.1"},
        )
        with patch.object(worker, "_read_json_body", AsyncMock(return_value={"turnstile_token": "single-use"})), patch.object(worker, "_verify_turnstile", AsyncMock()) as verify:
            response = await entry.fetch(request)
        self.assertEqual(response.status, 200)
        verify.assert_awaited_once_with(entry.env, "single-use", "https://example.test", "192.0.2.1")
        request.headers["authorization"] = "Bearer " + response.body["token"]
        request.url = "https://worker.test/api/retrieve"
        with patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": "test"})), patch.object(worker, "_cache_get", AsyncMock(return_value={"chunks": []})), patch.object(worker, "_log_question", AsyncMock()):
            self.assertEqual((await entry.fetch(request)).status, 200)

    async def test_configured_kv_binding_supports_cache_reads_and_writes(self):
        config_path = Path(__file__).parents[1] / "wrangler.toml"
        with config_path.open("rb") as config_file:
            config = tomllib.load(config_file)
        cache = Cache()
        env = types.SimpleNamespace(**{config["kv_namespaces"][0]["binding"]: cache})
        await worker._cache_put(env, "key", {"answer": "test"})
        self.assertEqual(await worker._cache_get(env, "key"), {"answer": "test"})

    async def test_kv_cache_uses_ttl_and_fails_open(self):
        cache = types.SimpleNamespace(get=AsyncMock(return_value='{"answer":"cached"}'), put=AsyncMock())
        env = types.SimpleNamespace(AEROQUERY_ANSWER_CACHE=cache, CACHE_TTL_SECONDS="120")
        with patch.object(persistence, "_js_options", lambda value: value):
            await worker._cache_put(env, "key", {"answer": "cached"})
        cache.put.assert_awaited_once_with("key", '{"answer": "cached"}', {"expirationTtl": 120})
        self.assertEqual(await worker._cache_get(env, "key"), {"answer": "cached"})
        cache.get.side_effect = RuntimeError("unavailable")
        self.assertIsNone(await worker._cache_get(env, "key"))
        env.CACHE_TTL_SECONDS = "0"
        await worker._cache_put(env, "disabled", {})
        self.assertIsNone(await worker._cache_get(env, "disabled"))
        self.assertEqual(cache.put.await_count, 1)

    async def test_cache_hit_expiration_and_disabled_cache(self):
        env = types.SimpleNamespace(AEROQUERY_ANSWER_CACHE=Cache(), CACHE_TTL_SECONDS="60")
        key = worker._cache_key(env, "/retrieve", "question", False)
        await worker._cache_put(env, key, {"answer": "test"})
        self.assertEqual(await worker._cache_get(env, key), {"answer": "test"})
        self.assertEqual(env.AEROQUERY_ANSWER_CACHE.ttls[key], 60)
        del env.AEROQUERY_ANSWER_CACHE.values[key]
        self.assertIsNone(await worker._cache_get(env, key))
        env.CACHE_TTL_SECONDS = "0"
        await worker._cache_put(env, key, {"answer": "new"})
        self.assertIsNone(await worker._cache_get(env, key))
        self.assertNotIn(key, env.AEROQUERY_ANSWER_CACHE.values)

    async def test_cache_keys_separate_paths_modes_models_and_questions(self):
        env = types.SimpleNamespace(AZURE_OPENAI_DEPLOYMENT="model-a")
        key = worker._cache_key(env, "/stream", "question", True)
        self.assertNotEqual(key, worker._cache_key(env, "/stream", "question", False))
        self.assertNotEqual(key, worker._cache_key(env, "/stream", "Question", True))
        env.AZURE_OPENAI_DEPLOYMENT = "model-b"
        self.assertNotEqual(key, worker._cache_key(env, "/stream", "question", True))

    async def test_repeated_retrieval_uses_cache_and_still_logs(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(
            DB=Database(), AEROQUERY_ANSWER_CACHE=Cache(), API_WORKER_KEY="a" * 32, ALLOWED_ORIGINS="https://example.test",
            API_RATE_LIMITER=types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True))),
        )
        self.addCleanup(entry.env.DB.connection.close)
        request = types.SimpleNamespace(
            headers={"origin": "https://example.test", "x-aeroquery-key": "a" * 32}, method="POST", url="https://example.test/api/retrieve"
        )
        chunks = [{"section": "1", "title": "Title", "text": "Text", "score": 1}]
        with (
            patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": " question "})),
            patch.object(worker, "_verify_session", AsyncMock()),
            patch.object(worker, "_retrieve_chunks", AsyncMock(return_value=chunks)) as retrieve,
        ):
            first = await entry.fetch(request)
            second = await entry.fetch(request)
        self.assertEqual(first, second)
        self.assertEqual(retrieve.await_count, 1)
        rows = entry.env.DB.connection.execute("SELECT question_sha, answered_from_cache FROM question_logs ORDER BY rowid").fetchall()
        self.assertEqual([row[1] for row in rows], [0, 1])
        self.assertEqual(rows[0][0], worker._question_sha("question", "webllm"))
        self.assertEqual(rows[0][0], rows[1][0])

    async def test_stream_cache_only_stores_complete_successful_answers(self):
        env = types.SimpleNamespace(AEROQUERY_ANSWER_CACHE=Cache())
        for finish_reason, done, should_cache in (("stop", True, True), ("length", True, False), ("stop", False, False)):
            with self.subTest(finish_reason=finish_reason, done=done):
                lines = [
                    'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":null}]}\n',
                    f'data: {{"choices":[{{"delta":{{}},"finish_reason":"{finish_reason}"}}]}}\n',
                ]
                if done:
                    lines.append("data: [DONE]\n")
                items = iter([types.SimpleNamespace(done=False, value=types.SimpleNamespace(to_bytes=lambda: "".join(lines).encode())), types.SimpleNamespace(done=True)])
                reader = types.SimpleNamespace(read=AsyncMock(side_effect=lambda: next(items)))
                upstream = types.SimpleNamespace(ok=True, body=types.SimpleNamespace(getReader=lambda: reader))
                controller = types.SimpleNamespace(enqueue=lambda value: None, close=lambda: None, error=lambda error: self.fail(str(error)))
                with (
                    patch.object(streaming, "_azure_chat", AsyncMock(return_value=(upstream, "model"))),
                    patch.object(streaming, "_js_options", lambda value: value),
                    patch.object(streaming, "TextEncoder", types.SimpleNamespace(new=lambda: types.SimpleNamespace(encode=lambda value: value))),
                    patch.object(streaming, "ReadableStream", types.SimpleNamespace(new=lambda options: options)),
                ):
                    response = await worker._stream_generation(env, [], "model", cache_key="stream")
                    await response.body["start"](controller)
                cached = await worker._cache_get(env, "stream")
                self.assertEqual(cached is not None, should_cache)
                if cached:
                    replay = worker._cached_stream(cached)
                    self.assertIn('"token":"answer"', replay.body)
                    self.assertIn('"type":"done"', replay.body)
                    env.AEROQUERY_ANSWER_CACHE.values.clear()

    async def test_stream_cache_hit_skips_agent_and_generation(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(
            DB=Database(), AEROQUERY_ANSWER_CACHE=Cache(), API_WORKER_KEY="a" * 32, ALLOWED_ORIGINS="https://example.test",
            API_RATE_LIMITER=types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True))),
        )
        self.addCleanup(entry.env.DB.connection.close)
        key = worker._cache_key(entry.env, "/stream", "question", True)
        await worker._cache_put(entry.env, key, {
            "answer": "cached answer", "sources": [{"section": "1", "title": "Title", "text": "Text"}],
            "agent": {"context_sufficient": True}, "model": "gpt-5.4-mini",
        })
        request = types.SimpleNamespace(
            headers={"origin": "https://example.test", "x-aeroquery-key": "a" * 32}, method="POST", url="https://example.test/api/stream"
        )
        with (
            patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": "question", "use_rag": True})),
            patch.object(worker, "_verify_session", AsyncMock()),
            patch.object(worker, "_run_agent", AsyncMock()) as agent,
            patch.object(worker, "_stream_generation", AsyncMock()) as generation,
        ):
            response = await entry.fetch(request)
        agent.assert_not_awaited()
        generation.assert_not_awaited()
        self.assertIn('"token":"cached answer"', response.body)
        self.assertIn('"type":"sources"', response.body)
        self.assertEqual(entry.env.DB.connection.execute("SELECT mode, answered_from_cache FROM question_logs ORDER BY rowid DESC LIMIT 1").fetchone(), ("openai_rag", 1))

    def test_mode_migration_renames_existing_rag_logs(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        schema_dir = Path(__file__).parents[1] / "schema"
        for name in ("0001_question_logs.sql", "0002_answer_cache.sql"):
            connection.executescript((schema_dir / name).read_text())
        connection.executemany(
            "INSERT INTO question_logs (id, created_at, query_length, mode) VALUES (?, ?, ?, ?)",
            [("rag", "2026-01-01", 1, "openai"), ("bare", "2026-01-01", 1, "openai_bare")],
        )
        connection.executescript((schema_dir / "0003_rename_openai_rag.sql").read_text())
        self.assertEqual(connection.execute("SELECT mode FROM question_logs ORDER BY id").fetchall(), [("openai_bare",), ("openai_rag",)])

    def test_logging_migration_preserves_rows_and_removes_cache_data(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        schema_dir = Path(__file__).parents[1] / "schema"
        for name in ("0001_question_logs.sql", "0002_answer_cache.sql", "0003_rename_openai_rag.sql"):
            connection.executescript((schema_dir / name).read_text())
        connection.execute(
            "INSERT INTO question_logs (id, created_at, query_length, mode, query_text, cache_key, cache_value, cache_expires_at, answered_from_cache) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("correlation", "2026-01-01", 8, "openai_rag", "question", "legacy-key", "answer", 123, 1),
        )
        connection.executescript((schema_dir / "0004_logging_only.sql").read_text())
        columns = {row[1] for row in connection.execute("PRAGMA table_info(question_logs)")}
        self.assertEqual(columns, {"correlation_id", "created_at", "mode", "question_asked", "question_sha", "answered_from_cache"})
        self.assertEqual(connection.execute("SELECT correlation_id, question_asked, question_sha, answered_from_cache FROM question_logs").fetchone(), ("correlation", "question", None, 1))

    def test_question_sha_depends_only_on_question_and_mode(self):
        key = worker._question_sha("question", "openai_rag")
        self.assertEqual(key, worker._question_sha("question", "openai_rag"))
        self.assertNotEqual(key, worker._question_sha("question", "openai_bare"))
        self.assertNotEqual(key, worker._question_sha("Question", "openai_rag"))

    async def test_question_log_matches_database_id_and_respects_text_setting(self):
        for enabled in ("true", "false"):
            statement = types.SimpleNamespace(bind=lambda *args: types.SimpleNamespace(run=AsyncMock()))
            bound_values = []
            statement.bind = lambda *args: (bound_values.extend(args) or types.SimpleNamespace(run=AsyncMock()))
            env = types.SimpleNamespace(DB=types.SimpleNamespace(prepare=lambda sql: statement), QUESTIONS_LOG_QUERY_TEXT=enabled)
            with patch("builtins.print") as output:
                await worker._log_question(env, "test question", "openai_rag", "sha", "correlation")
            event = json.loads(output.call_args.args[0])
            self.assertEqual(event["correlation_id"], bound_values[0])
            self.assertNotIn("test question", output.call_args.args[0])
            self.assertNotIn("query_text", event)
            self.assertEqual(bound_values[3], "test question" if enabled == "true" else None)

    async def test_unauthorized_requests_are_rejected(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(API_WORKER_KEY="a" * 32, ALLOWED_ORIGINS="https://example.test")
        request = types.SimpleNamespace(headers={"origin": "https://example.test"}, method="POST")
        response = await entry.fetch(request)
        self.assertEqual(response["status"], 401)

    async def test_requests_do_not_require_a_quota_binding(self):
        entry = worker.Default()
        limiter = types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True)))
        entry.env = types.SimpleNamespace(API_WORKER_KEY="a" * 32, API_RATE_LIMITER=limiter, ALLOWED_ORIGINS="https://example.test")
        request = types.SimpleNamespace(
            headers={"origin": "https://example.test", "x-aeroquery-key": "a" * 32, "cf-connecting-ip": "127.0.0.1"},
            method="POST",
            url="https://example.test/api/retrieve",
        )
        with (
            patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": "test"})),
            patch.object(worker, "_verify_session", AsyncMock()),
            patch.object(worker, "_log_question", AsyncMock()) as log_question,
            patch.object(worker, "_retrieve_chunks", AsyncMock(return_value=[])),
            patch.object(worker, "_stream_generation", AsyncMock(return_value=Response("stream"))),
        ):
            response = await entry.fetch(request)
            self.assertEqual(response["status"], 200)
            request.url = "https://example.test/api/stream"
            self.assertEqual((await entry.fetch(request)).body, "stream")
            self.assertEqual(log_question.await_args.args[:4], (entry.env, "test", "openai_bare", worker._question_sha("test", "openai_bare")))
            self.assertFalse(log_question.await_args.args[5])


if __name__ == "__main__":
    unittest.main()