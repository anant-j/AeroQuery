import json
import time

from js import ReadableStream, TextEncoder
from pyodide.ffi import create_proxy
from workers import Response

from common import ApiError, _js_options, _log
from persistence import _cache_put, _finish_question
from retrieval import _azure_chat


def _sse_event(event):
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


async def _stream_generation(env, messages, model, cache_key, sources=None, agent=None, log_context=None):
    generation_started = time.monotonic()
    response, _ = await _azure_chat(env, messages, stream=True, model=model)
    if not response.ok:
        raise ApiError("Azure Foundry generation failed", 502)

    encoder = TextEncoder.new()

    async def start(controller):
        buffer = b""
        tokens = []
        completed = False
        finish_reason = None
        usage = None

        def emit_line(raw_line):
            nonlocal completed, finish_reason, usage
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
            event_usage = event.get("usage")
            if event_usage:
                usage = event_usage
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
            if completed and finish_reason == "stop" and tokens:
                await _cache_put(env, cache_key, {
                    "answer": "".join(tokens), "sources": sources, "agent": agent, "model": model,
                    "source_metadata": log_context["sources"] if log_context else None,
                })
            successful = completed and finish_reason == "stop"
            if not successful:
                _log("warning", "generation_incomplete", finish_reason=finish_reason)
            if log_context:
                await _finish_question(
                    env, log_context["log_rowid"], outcome="success" if successful else "incomplete", http_status=200,
                    duration_ms=round((time.monotonic() - log_context["started"]) * 1000),
                    generation_ms=round((time.monotonic() - generation_started) * 1000),
                    retrieval_ms=log_context["retrieval_ms"], sources=log_context["sources"],
                    answer="".join(tokens), usage=usage, finish_reason=finish_reason,
                    failure_stage=None if successful else "generation_stream",
                    error_type=None if successful else "IncompleteStream",
                )
            if not completed or finish_reason != "stop":
                controller.enqueue(encoder.encode(_sse_event({
                    "type": "error",
                    "error": "Generation ended before the answer was complete. Please try again.",
                })))
                controller.close()
                return
            controller.enqueue(encoder.encode(_sse_event({"type": "done"})))
            controller.close()
        except Exception as error:
            _log("error", "generation_stream_failed", error_type=type(error).__name__)
            if log_context:
                await _finish_question(
                    env, log_context["log_rowid"], outcome="error", http_status=200,
                    duration_ms=round((time.monotonic() - log_context["started"]) * 1000),
                    generation_ms=round((time.monotonic() - generation_started) * 1000),
                    retrieval_ms=log_context["retrieval_ms"], sources=log_context["sources"],
                    answer="".join(tokens), usage=usage, finish_reason=finish_reason,
                    failure_stage="generation_stream", error_type=type(error).__name__,
                )
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
    events.append({"type": "usage", "tokens": 0, "model": value["model"], "cached": True})
    events.append({"type": "done"})
    return Response("".join(_sse_event(event) for event in events), headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Content-Type-Options": "nosniff",
    })
