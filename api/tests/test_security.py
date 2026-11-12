import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class WorkerEntrypoint:
    pass


class Response:
    def __init__(self, body, headers=None):
        self.body = body
        self.headers = headers

    @staticmethod
    def json(body, status=200):
        return {"body": body, "status": status}


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


js = types.ModuleType("js")
js.AbortSignal = js.Object = js.ReadableStream = js.TextEncoder = object()
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


class WorkerSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_hit_expiration_and_disabled_cache(self):
        env = types.SimpleNamespace(DB=Database(), CACHE_TTL_SECONDS="60")
        self.addCleanup(env.DB.connection.close)
        key = worker._cache_key(env, "/retrieve", "question", False)
        question_id = await worker._log_question(env, "question", "webllm", key)
        await worker._cache_put(env, question_id, {"answer": "test"})
        self.assertEqual(await worker._cache_get(env, key), {"answer": "test"})
        env.DB.connection.execute("UPDATE question_logs SET cache_expires_at = 0")
        self.assertIsNone(await worker._cache_get(env, key))
        env.CACHE_TTL_SECONDS = "0"
        await worker._cache_put(env, question_id, {"answer": "new"})
        self.assertIsNone(await worker._cache_get(env, key))
        self.assertEqual(json.loads(env.DB.connection.execute("SELECT cache_value FROM question_logs").fetchone()[0]), {"answer": "test"})

    async def test_cache_keys_separate_paths_modes_models_and_questions(self):
        env = types.SimpleNamespace(AZURE_OPENAI_DEPLOYMENT="model-a")
        key = worker._cache_key(env, "/stream", "question", True)
        self.assertNotEqual(key, worker._cache_key(env, "/stream", "question", False))
        self.assertNotEqual(key, worker._cache_key(env, "/agent", "question", False))
        self.assertNotEqual(key, worker._cache_key(env, "/stream", "Question", True))
        env.AZURE_OPENAI_DEPLOYMENT = "model-b"
        self.assertNotEqual(key, worker._cache_key(env, "/stream", "question", True))

    async def test_repeated_retrieval_uses_cache_and_still_logs(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(
            DB=Database(), API_WORKER_KEY="a" * 32,
            API_RATE_LIMITER=types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True))),
        )
        self.addCleanup(entry.env.DB.connection.close)
        request = types.SimpleNamespace(
            headers={"x-aeroquery-key": "a" * 32}, method="POST", url="https://example.test/api/retrieve"
        )
        chunks = [{"section": "1", "title": "Title", "text": "Text", "score": 1}]
        with (
            patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": " question "})),
            patch.object(worker, "_retrieve_chunks", AsyncMock(return_value=chunks)) as retrieve,
        ):
            first = await entry.fetch(request)
            second = await entry.fetch(request)
        self.assertEqual(first, second)
        self.assertEqual(retrieve.await_count, 1)
        rows = entry.env.DB.connection.execute("SELECT cache_key, answered_from_cache FROM question_logs ORDER BY rowid").fetchall()
        self.assertEqual([row[1] for row in rows], [0, 1])
        self.assertEqual(rows[0][0], rows[1][0])

    async def test_stream_cache_only_stores_complete_successful_answers(self):
        env = types.SimpleNamespace(DB=Database())
        self.addCleanup(env.DB.connection.close)
        for finish_reason, done, should_cache in (("stop", True, True), ("length", True, False), ("stop", False, False)):
            with self.subTest(finish_reason=finish_reason, done=done):
                question_id = await worker._log_question(env, "question", "openai_bare", "stream")
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
                    patch.object(worker, "_azure_chat", AsyncMock(return_value=(upstream, "model"))),
                    patch.object(worker, "_js_options", lambda value: value),
                    patch.object(worker, "TextEncoder", types.SimpleNamespace(new=lambda: types.SimpleNamespace(encode=lambda value: value))),
                    patch.object(worker, "ReadableStream", types.SimpleNamespace(new=lambda options: options)),
                ):
                    response = await worker._stream_generation(env, [], "model", question_id=question_id)
                    await response.body["start"](controller)
                cached = await worker._cache_get(env, "stream")
                self.assertEqual(cached is not None, should_cache)
                if cached:
                    replay = worker._cached_stream(cached)
                    self.assertIn('"token":"answer"', replay.body)
                    self.assertIn('"type":"done"', replay.body)
                    env.DB.connection.execute("DELETE FROM question_logs")

    async def test_stream_cache_hit_skips_agent_and_generation(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(
            DB=Database(), API_WORKER_KEY="a" * 32,
            API_RATE_LIMITER=types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True))),
        )
        self.addCleanup(entry.env.DB.connection.close)
        key = worker._cache_key(entry.env, "/stream", "question", True)
        question_id = await worker._log_question(entry.env, "question", "openai", key)
        await worker._cache_put(entry.env, question_id, {
            "answer": "cached answer", "sources": [{"section": "1", "title": "Title", "text": "Text"}],
            "agent": {"context_sufficient": True}, "model": "gpt-5.4-mini",
        })
        request = types.SimpleNamespace(
            headers={"x-aeroquery-key": "a" * 32}, method="POST", url="https://example.test/api/stream"
        )
        with (
            patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": "question", "use_rag": True})),
            patch.object(worker, "_run_agent", AsyncMock()) as agent,
            patch.object(worker, "_stream_generation", AsyncMock()) as generation,
        ):
            response = await entry.fetch(request)
        agent.assert_not_awaited()
        generation.assert_not_awaited()
        self.assertIn('"token":"cached answer"', response.body)
        self.assertIn('"type":"sources"', response.body)
        self.assertEqual(entry.env.DB.connection.execute("SELECT answered_from_cache FROM question_logs ORDER BY rowid DESC LIMIT 1").fetchone()[0], 1)

    async def test_question_log_matches_database_id_and_respects_text_setting(self):
        for enabled in ("true", "false"):
            statement = types.SimpleNamespace(bind=lambda *args: types.SimpleNamespace(run=AsyncMock()))
            bound_values = []
            statement.bind = lambda *args: (bound_values.extend(args) or types.SimpleNamespace(run=AsyncMock()))
            env = types.SimpleNamespace(DB=types.SimpleNamespace(prepare=lambda sql: statement), QUESTIONS_LOG_QUERY_TEXT=enabled)
            with patch("builtins.print") as output:
                await worker._log_question(env, "test question", "openai")
            event = json.loads(output.call_args.args[0])
            self.assertEqual(event["question_id"], bound_values[0])
            self.assertEqual(event.get("query_text"), "test question" if enabled == "true" else None)
            self.assertEqual(bound_values[4], "test question" if enabled == "true" else None)

    async def test_unauthorized_requests_are_rejected(self):
        entry = worker.Default()
        entry.env = types.SimpleNamespace(API_WORKER_KEY="a" * 32)
        request = types.SimpleNamespace(headers={})
        response = await entry.fetch(request)
        self.assertEqual(response["status"], 401)

    async def test_requests_do_not_require_a_quota_binding(self):
        entry = worker.Default()
        limiter = types.SimpleNamespace(limit=AsyncMock(return_value=types.SimpleNamespace(success=True)))
        entry.env = types.SimpleNamespace(API_WORKER_KEY="a" * 32, API_RATE_LIMITER=limiter)
        request = types.SimpleNamespace(
            headers={"x-aeroquery-key": "a" * 32, "x-aeroquery-client-ip": "127.0.0.1"},
            method="POST",
            url="https://example.test/api/retrieve",
        )
        with (
            patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": "test"})),
            patch.object(worker, "_log_question", AsyncMock()) as log_question,
            patch.object(worker, "_retrieve_chunks", AsyncMock(return_value=[])),
            patch.object(worker, "_stream_generation", AsyncMock(return_value="stream")),
        ):
            response = await entry.fetch(request)
            self.assertEqual(response["status"], 200)
            request.url = "https://example.test/api/stream"
            self.assertEqual(await entry.fetch(request), "stream")
            log_question.assert_any_await(entry.env, "test", "openai_bare", worker._cache_key(entry.env, "/stream", "test", False), False)


if __name__ == "__main__":
    unittest.main()