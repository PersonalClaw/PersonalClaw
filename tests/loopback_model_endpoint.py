"""A model server on 127.0.0.1 whose answer bodies end where a test says.

It speaks the two wires core's adapters read over HTTP: an OpenAI-compatible
``/v1/chat/completions`` stream (SSE ``data`` lines) and Ollama's ``/api/chat`` (NDJSON), and it
closes each body cleanly after its last frame, which is how a connection that ended part way, or a
proxy that cut the body short, reaches a client: no error, just the end of the bytes. Anything
else (Ollama's served-window probe) is answered at once with nothing loaded.
"""

from __future__ import annotations

import json

from aiohttp import web
from aiohttp.test_utils import TestServer

MODEL = "m"


def chunk(delta: dict, finish: str | None = None) -> dict:
    """An OpenAI-compatible ``chat.completion.chunk`` with one choice."""
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def text(words: str) -> dict:
    return chunk({"role": "assistant", "content": words})


def tool_delta(arguments: str, *, name: str = "write_note", call_id: str = "call-1") -> dict:
    """A chunk carrying one tool call's name and (a fragment of) its arguments."""
    return chunk(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ]
        }
    )


def usage(prompt_tokens: int = 12, completion_tokens: int = 3) -> dict:
    """The usage-only chunk an endpoint sends after the last choice."""
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def whole_answer(words: str) -> list:
    """A whole OpenAI-compatible answer of *words*: text, its finish_reason, usage, ``[DONE]``."""
    return [text(words), chunk({}, "stop"), usage(), "[DONE]"]


class ModelEndpoint:
    """Answers each request with the next of ``bodies`` (the last one again once they run out).

    An OpenAI-compatible body is a list of SSE ``data`` payloads (a dict, or ``"[DONE]"``); an
    Ollama body is a list of NDJSON objects. ``requests`` is every request body received.
    """

    def __init__(self, *bodies: list) -> None:
        self.bodies = list(bodies)
        self.requests: list[dict] = []
        self._server: TestServer | None = None

    def _next(self) -> list:
        return self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]

    async def _openai(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(await request.json())
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for frame in self._next():
            data = frame if isinstance(frame, str) else json.dumps(frame)
            await resp.write(f"data: {data}\n\n".encode())
        await resp.write_eof()
        return resp

    async def _ollama(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(await request.json())
        resp = web.StreamResponse(headers={"Content-Type": "application/x-ndjson"})
        await resp.prepare(request)
        for line in self._next():
            await resp.write((json.dumps(line) + "\n").encode())
        await resp.write_eof()
        return resp

    async def _anything_else(self, request: web.Request) -> web.Response:
        return web.json_response({"models": []})

    async def __aenter__(self) -> ModelEndpoint:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self._openai)
        app.router.add_post("/api/chat", self._ollama)
        app.router.add_route("*", "/{tail:.*}", self._anything_else)
        self._server = TestServer(app, host="127.0.0.1")
        await self._server.start_server()
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        await self._server.close()

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.port}"
