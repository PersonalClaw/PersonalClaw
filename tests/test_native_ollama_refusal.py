"""A request Ollama refuses says OLLAMA's reason, on both chat paths.

Measured in a live Agent Rooms drill: a member bound to a model the server had not pulled failed
its turn, and the room said "Client error '404 Not Found' for url
'http://localhost:11434/api/chat' For more information check: <MDN link>" — httpx's generic
``raise_for_status`` sentence. The next member read that as a connectivity problem. Ollama's own
body, which the provider had already read and logged, said exactly what was wrong. A chat turn on
the same model showed the same sentence, so this is pinned at the provider, for both the
single-turn ``stream`` path and the stateless multi-message ``complete`` path the native runtime
uses.
"""

from __future__ import annotations

import asyncio
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

#: What a stock Ollama answers for a model that is not pulled.
NOT_PULLED = '{"error":"model \\"no-such-model:1b\\" not found, try pulling it first"}'


@pytest.fixture()
def module():
    name = namespaced_module_name(APP_NAME, "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / APP_NAME, APP_NAME, "provider")
    finally:
        sys.modules.pop(name, None)


def _provider(module, status: int, body: str):
    provider = module._factory(
        entry=ProviderEntry(
            name="Local Ollama",
            type="ollama",
            model="no-such-model:1b",
            options={"endpoint": "http://127.0.0.1:9", "default_model": "no-such-model:1b"},
        )
    )

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    provider._client = httpx.AsyncClient(
        base_url="http://127.0.0.1:9", transport=httpx.MockTransport(answer)
    )
    return provider


async def _drain(events):
    async for _ in events:
        pass


@pytest.mark.parametrize("path", ["stream", "complete"])
def test_a_model_that_is_not_pulled_is_named_by_ollamas_own_reason(module, path):
    provider = _provider(module, 404, NOT_PULLED)
    events = (
        provider.stream("hello")
        if path == "stream"
        else provider.complete([{"role": "user", "content": "hello"}])
    )

    with pytest.raises(httpx.HTTPStatusError) as refused:
        asyncio.run(_drain(events))

    assert str(refused.value) == (
        'Ollama answered 404: model "no-such-model:1b" not found, try pulling it first'
    )
    # The same exception, with the same response: anything that classifies on it is unchanged.
    assert refused.value.response.status_code == 404


def test_a_refusal_with_no_json_sentence_carries_its_body_instead(module):
    provider = _provider(module, 500, "upstream exploded")

    with pytest.raises(httpx.HTTPStatusError) as refused:
        asyncio.run(_drain(provider.complete([{"role": "user", "content": "hello"}])))

    assert str(refused.value) == "Ollama answered 500: upstream exploded"
