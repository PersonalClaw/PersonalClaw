"""Ranking a turn's tools never holds up the gateway, and never embeds a catalog twice.

Tool retrieval ranks a turn's catalog by meaning, so each tool needs a vector. A runtime used to
embed its whole catalog on its first turn, one request per tool, on the event loop that serves
every request, so a new chat, a workflow stage's agent and every restart froze the gateway until
it was done: measured on a stock install, 124 sequential requests, and nothing else answered for
21 s with the embedding server taking 175 ms a request.

These drive a real native runtime through a configured model provider's ``embed`` (the seam an
Ollama binding embeds through), so what they count is what reaches the embedding server.
"""

from __future__ import annotations

import asyncio
import hashlib
import threading
import time

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.embedding_providers import registry as embedding_registry
from personalclaw.llm.events import EVENT_COMPLETE, AgentEvent
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult

#: More tools than a turn carries in full (``tool_retrieval.DEFAULT_K`` is 48), so a turn ranks.
_TOOLS = 60
#: How long the embedding server takes to answer one request, unless a test says otherwise.
_EMBED_SECS = 0.03
#: The longest the event loop may stand still while a turn ranks its tools.
_LONGEST_STILL = 0.5


class _EmbeddingServer:
    """A configured model provider that embeds: every request it answers, as the server saw it."""

    def __init__(self) -> None:
        self.requests: list[list[str]] = []
        self.query_secs = _EMBED_SECS
        self._lock = threading.Lock()

    async def start(self) -> None:
        return None

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        with self._lock:
            self.requests.append(list(inputs))
        tools_only = all(text.startswith("tool_") for text in inputs)
        await asyncio.sleep(_EMBED_SECS if tools_only else self.query_secs)
        return [_vector(text) for text in inputs]

    def tool_texts(self, since: int = 0) -> list[str]:
        """Every tool description the server was asked to embed, in order."""
        return [t for request in self.requests[since:] for t in request if t.startswith("tool_")]


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [b / 255.0 + 0.01 for b in digest[:8]]


class _Registry:
    """The model-provider registry, holding the one configured embedding instance."""

    def __init__(self, server: _EmbeddingServer) -> None:
        self._server = server

    def build(self, name: str, **_kwargs: object) -> _EmbeddingServer:
        return self._server


@pytest.fixture
def embedding_server(monkeypatch):
    """Bind Embedding to a configured model provider's model, answering through the server."""
    import personalclaw.llm.registry as llm_registry

    server = _EmbeddingServer()
    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: _Registry(server))
    monkeypatch.setattr(
        embedding_registry, "_active_embedding_spec", lambda: ("local-embed", "embed-model")
    )
    monkeypatch.setattr(embedding_registry, "_ensure_scanned", lambda: None)
    monkeypatch.setattr(embedding_registry, "_providers", {})
    yield server
    _wait_for_background_embedding()


def _wait_for_background_embedding(timeout: float = 20.0) -> None:
    """Until no thread is embedding tool descriptions in the background (none may outlive the
    test that started it)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(t.name == "tool-vectors" and t.is_alive() for t in threading.enumerate()):
            return
        time.sleep(0.01)
    raise AssertionError("tool descriptions were still being embedded after 20s")


class _Model:
    """A chat model that answers every turn at once."""

    supports_tools = True
    _model = "scripted"

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        yield AgentEvent(kind=EVENT_COMPLETE)


class _Catalog(ToolProvider):
    @property
    def name(self) -> str:
        return "personalclaw-catalog"

    @property
    def display_name(self) -> str:
        return "Catalog"

    async def list_tools(self):
        return [
            ToolDefinition(
                name=f"tool_{i}",
                description=f"does thing {i}",
                provider="personalclaw-catalog",
                parameters={"type": "object", "properties": {}},
                requires_approval=False,
            )
            for i in range(_TOOLS)
        ]

    async def invoke(self, tool_name: str, arguments: dict) -> ToolResult:
        return ToolResult(success=True, output=f"ran {tool_name}")


async def _new_agent() -> NativeAgentRuntime:
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(
            name="a", provider="native", model="m", tools=[], skills=[]
        ),
        model_provider=_Model(),
        tool_providers=[_Catalog()],
    )
    await runtime.start()
    return runtime


async def _longest_still_during(work) -> float:
    """How long the event loop stood still, at most, while *work* ran on it."""
    gaps: list[float] = []
    running = True

    async def _beat() -> None:
        while running:
            began = time.monotonic()
            await asyncio.sleep(0.005)
            gaps.append(time.monotonic() - began)

    beat = asyncio.create_task(_beat())
    await asyncio.sleep(0.02)
    try:
        await work()
    finally:
        running = False
        await beat
    return max(gaps)


async def _turn(runtime: NativeAgentRuntime, message: str) -> None:
    async for _ in runtime.stream(message):
        pass


@pytest.mark.asyncio
async def test_a_new_agents_first_turn_leaves_the_gateway_serving(embedding_server):
    """The catalog's first embedding, and the ranking, happen while the loop serves others."""

    async def _first_turn() -> None:
        runtime = await _new_agent()
        await _turn(runtime, "which tool does thing 7")

    still = await _longest_still_during(_first_turn)
    _wait_for_background_embedding()
    assert embedding_server.tool_texts(), "the catalog was never embedded: nothing was measured"
    assert still < _LONGEST_STILL, f"the gateway answered nothing for {still:.2f}s"


@pytest.mark.asyncio
async def test_the_catalog_is_embedded_once_in_groups_and_a_second_agent_embeds_none(
    embedding_server,
):
    first = await _new_agent()
    await _turn(first, "which tool does thing 7")
    _wait_for_background_embedding()
    embedded = embedding_server.tool_texts()
    assert sorted(embedded) == sorted({f"tool_{i}: does thing {i}" for i in range(_TOOLS)})
    groups = [r for r in embedding_server.requests if any(t.startswith("tool_") for t in r)]
    assert len(groups) == 2, f"{_TOOLS} descriptions took {len(groups)} requests"

    before = len(embedding_server.requests)
    second = await _new_agent()
    await _turn(second, "which tool does thing 9")
    _wait_for_background_embedding()
    assert embedding_server.tool_texts(since=before) == []
    assert embedding_server.requests[before:] == [["which tool does thing 9"]]


@pytest.mark.asyncio
async def test_a_turn_waiting_on_a_slow_embedding_server_leaves_the_gateway_serving(
    embedding_server,
):
    """Even the one request a turn makes — its own query's vector — is made off the loop."""
    warm = await _new_agent()
    await _turn(warm, "which tool does thing 1")
    _wait_for_background_embedding()
    embedding_server.query_secs = 0.8

    async def _slow_turn() -> None:
        await _turn(await _new_agent(), "which tool does thing 3")

    still = await _longest_still_during(_slow_turn)
    assert ["which tool does thing 3"] in embedding_server.requests, "the query was not embedded"
    assert still < _LONGEST_STILL, f"the gateway answered nothing for {still:.2f}s"
