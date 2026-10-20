"""An Ollama instance says whether ITS model calls tools, from what its server said.

The Ollama provider declared ``supports_tools = True`` for every model. A model that can't use
tools (``qwen2.5vl:7b`` is one: Ollama's own record of it lists no ``tools``) made the server
refuse each request that offered them; the provider retried without the tools and kept that on
the one instance, so the agent's loop, which reads the declaration, was told nothing, and a loop
created on such a model was never refused.

Now each instance answers for its model: no once the server's record of the model lists no
``tools`` or the server refused a request for its tools, yes for one it lists them for, and yes
for a model the server has not described yet (its first call finds out). What one instance heard
holds for every instance on that server and model, so a new session does not pay the refused
round-trip again. Fake servers only.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
import pytest

from personalclaw.apps.native_contract import (
    NATIVE_DIR,
    load_bundle_module,
    namespaced_module_name,
)
from personalclaw.llm.registry import ProviderEntry

APP_NAME = "ollama-models"
ENDPOINT = "http://127.0.0.1:9"
#: What a stock Ollama answers a request that offers tools to a model that can't use them.
REFUSED = '{"error":"registry.ollama.ai/library/qwen2.5vl:7b does not support tools"}'
TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
ANSWER = (
    json.dumps({"message": {"role": "assistant", "content": "It says hello."}, "done": False})
    + "\n"
    + json.dumps({"done": True, "done_reason": "stop", "prompt_eval_count": 9, "eval_count": 4})
    + "\n"
)


@pytest.fixture()
def module():
    name = namespaced_module_name(APP_NAME, "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / APP_NAME, APP_NAME, "provider")
    finally:
        sys.modules.pop(name, None)


def _provider(module, model: str, sent: list[dict] | None = None, *, endpoint: str = ENDPOINT):
    """An instance on *model*; each request it sends is kept in *sent*, and the server refuses one
    that offers tools to ``qwen2.5vl:7b``."""
    provider = module._factory(
        entry=ProviderEntry(
            name="Local Ollama",
            type="ollama",
            model=model,
            options={"endpoint": endpoint, "default_model": model},
        )
    )

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if sent is not None:
            sent.append(body)
        if body.get("tools") and body["model"] == "qwen2.5vl:7b":
            return httpx.Response(400, text=REFUSED)
        return httpx.Response(200, text=ANSWER)

    provider._client = httpx.AsyncClient(base_url=endpoint, transport=httpx.MockTransport(answer))
    return provider


def _complete(provider, tools=TOOLS) -> str:
    async def go() -> str:
        said = ""
        async for event in provider.complete([{"role": "user", "content": "read it"}], tools=tools):
            said += getattr(event, "text", "") or ""
        return said

    return asyncio.run(go())


def test_a_model_that_refused_its_tools_says_it_takes_none_from_then_on(module):
    """🔴 Before: ``supports_tools`` stayed True on the instance that was refused, and on every
    instance after it, so the agent's loop never knew its turn had run without tools."""
    sent: list[dict] = []
    provider = _provider(module, "qwen2.5vl:7b", sent)
    assert provider.supports_tools, "a model the server has not described is asked with its tools"

    assert _complete(provider) == "It says hello.", "the turn still completes, without them"

    assert [bool(b.get("tools")) for b in sent] == [True, False]
    assert provider.supports_tools is False
    _complete(provider)
    assert [bool(b.get("tools")) for b in sent] == [True, False, False], "not refused twice"
    assert _provider(module, "qwen2.5vl:7b").supports_tools is False, "a new session knows too"


def test_a_model_that_calls_tools_is_sent_them_and_keeps_saying_so(module):
    sent: list[dict] = []
    provider = _provider(module, "gemma4:12b", sent)

    _complete(provider)

    assert [b.get("tools") for b in sent] == [TOOLS]
    assert provider.supports_tools is True


def test_one_models_refusal_says_nothing_about_another_or_another_server(module):
    _complete(_provider(module, "qwen2.5vl:7b"))

    assert _provider(module, "gemma4:12b").supports_tools is True
    assert _provider(module, "qwen2.5vl:7b", endpoint="http://127.0.0.1:8").supports_tools is True


@pytest.mark.asyncio
async def test_the_servers_record_of_each_model_says_it_before_any_call(module):
    """Ollama's ``POST /api/show`` lists ``tools`` among what a model serves when it calls them.
    What the catalog read there is what an instance on that model declares, before any request."""
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    served = {
        "gemma4:12b": ["completion", "vision", "tools"],
        "qwen2.5vl:7b": ["completion", "vision"],
        "old-server-model:1b": [],
    }

    async def tags(_request):
        return web.json_response({"models": [{"name": n, "details": {}} for n in served]})

    async def show(request):
        return web.json_response({"capabilities": served[(await request.json())["name"]]})

    app = web.Application()
    app.router.add_get("/api/tags", tags)
    app.router.add_post("/api/show", show)
    server = TestServer(app)
    await server.start_server()
    endpoint = str(server.make_url("")).rstrip("/")
    try:
        await module.OllamaCatalog(endpoint=endpoint).list_models()
    finally:
        await server.close()

    def declared(model: str) -> bool:
        return bool(_provider(module, model, endpoint=endpoint).supports_tools)

    assert declared("qwen2.5vl:7b") is False
    assert declared("gemma4:12b") is True
    assert declared("old-server-model:1b") is True, "a record that lists nothing says nothing"
