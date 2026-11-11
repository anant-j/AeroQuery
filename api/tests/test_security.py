import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


class WorkerEntrypoint:
    pass


class Response:
    @staticmethod
    def json(body, status=200):
        return {"body": body, "status": status}


js = types.ModuleType("js")
js.AbortSignal = js.Object = js.ReadableStream = js.TextEncoder = object()
ffi = types.ModuleType("pyodide.ffi")
ffi.create_proxy = ffi.to_js = lambda value, **kwargs: value
workers = types.ModuleType("workers")
workers.WorkerEntrypoint = WorkerEntrypoint
workers.Response = Response
workers.fetch = None

with patch.dict(sys.modules, {"js": js, "pyodide": types.ModuleType("pyodide"), "pyodide.ffi": ffi, "workers": workers}):
    spec = importlib.util.spec_from_file_location("worker_under_test", Path(__file__).parents[1] / "src" / "worker.py")
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)


class WorkerSecurityTests(unittest.IsolatedAsyncioTestCase):
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
        with patch.object(worker, "_read_json_body", AsyncMock(return_value={"query": "test"})), \
             patch.object(worker, "_log_question", AsyncMock()), \
             patch.object(worker, "_retrieve_chunks", AsyncMock(return_value=[])), \
             patch.object(worker, "_stream_generation", AsyncMock(return_value="stream")):
            response = await entry.fetch(request)
            self.assertEqual(response["status"], 200)
            request.url = "https://example.test/api/stream"
            self.assertEqual(await entry.fetch(request), "stream")


if __name__ == "__main__":
    unittest.main()