"""A stop closes the model request in flight; it does not wait for the request to return.

Ollama's ``cancel()`` was a no-op that answered ``"no_turn"``, so Stop, Pause and an incident hold
ended a native turn only when the model's request came back — up to the instance's Request Timeout
(300 s) on a slow local model — and the model kept generating the whole time. The native loop's
stop report still said the model request was aborted.

Driven with the bundled Ollama provider against a loopback server that holds the request open: one
that has not answered yet (a model still reading its prompt), and one that has started answering.
The two protocol clients every other model app builds on (OpenAI- and Anthropic-compatible) had
the same no-op, so a stopped turn on a paid endpoint kept generating billed tokens; they close
their request the same way.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.apps.native_contract import NATIVE_DIR, load_bundle_module, namespaced_module_name
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK
from personalclaw.llm.events import AgentEvent as LLMEvent

APP = "ollama-models"
#: Well past the test's own bound: a turn that ends only when the request returns fails loudly.
_HELD_SECS = 30.0


class _HeldEndpoint:
    """A loopback HTTP server that holds a chat request open and notes when the client hangs up.

    ``started`` sends the headers and one chunk of the answer first; otherwise nothing is sent.
    Every other request (Ollama's served-window probe) is answered at once.
    """

    def __init__(self, *, started: bool = False, held: bytes = b"POST /api/chat ") -> None:
        self._started = started
        self._held = held
        self.asked = asyncio.Event()
        self.hung_up = asyncio.Event()
        self._server: asyncio.base_events.Server | None = None

    async def __aenter__(self) -> _HeldEndpoint:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()

    @property
    def endpoint(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.split(b"\r\n")
            length = next(
                (
                    int(ln.split(b":")[1])
                    for ln in lines
                    if ln.lower().startswith(b"content-length")
                ),
                0,
            )
            await reader.readexactly(length)
            if lines[0].startswith(self._held):
                break
            # Anything else (the served-window probe) is answered at once: nothing is loaded.
            body = b'{"models": []}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        self.asked.set()
        if self._started:
            chunk = json.dumps(
                {"message": {"role": "assistant", "content": "Thinking"}, "done": False}
            )
            body = (chunk + "\n").encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
                + f"{len(body):x}\r\n".encode()
                + body
                + b"\r\n"
            )
            await writer.drain()
        try:
            # The client's close is the only thing that ends this wait before the hold does.
            await asyncio.wait_for(reader.read(), timeout=_HELD_SECS)
            self.hung_up.set()
        except asyncio.TimeoutError:
            pass
        finally:
            writer.close()


@pytest.fixture()
def ollama():
    name = namespaced_module_name(APP, "provider")
    try:
        yield load_bundle_module(NATIVE_DIR / APP, APP, "provider")
    finally:
        sys.modules.pop(name, None)


def _runtime(provider) -> NativeAgentRuntime:
    return NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="m:1b"),
        model_provider=provider,
        tool_providers=[],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True], ids=["before-the-answer", "mid-answer"])
async def test_a_stop_ends_the_turn_and_closes_the_request(ollama, started):
    async with _HeldEndpoint(started=started) as server:
        provider = ollama.OllamaProvider(model="m:1b", endpoint=server.endpoint, timeout=300.0)
        rt = _runtime(provider)
        await rt.start()
        events: list = []
        answering = asyncio.Event()
        turn = asyncio.create_task(_collect(rt, events, answering))
        await asyncio.wait_for(server.asked.wait(), timeout=10)
        if started:
            await asyncio.wait_for(answering.wait(), timeout=10)

        assert await rt.cancel() == "acked"

        await asyncio.wait_for(turn, timeout=5)
        await asyncio.wait_for(server.hung_up.wait(), timeout=5)
        await provider.shutdown()

    last = events[-1]
    assert last.kind == EVENT_COMPLETE and last.stop_reason == "stopped_by_user"
    assert rt.last_stop_report()["model_request_aborted"] is True
    if started:
        assert [e.text for e in events if e.kind == EVENT_TEXT_CHUNK] == ["Thinking"]


@pytest.mark.asyncio
async def test_with_nothing_in_flight_the_provider_says_so(ollama):
    provider = ollama.OllamaProvider(model="m:1b", endpoint="http://127.0.0.1:9")
    assert await provider.cancel() == "no_turn"
    await provider.shutdown()


@pytest.mark.asyncio
async def test_the_openai_compatible_client_closes_its_request_too():
    from personalclaw.llm.openai import OpenAIProvider

    async with _HeldEndpoint(held=b"POST /v1/chat/completions ") as server:
        provider = OpenAIProvider(model="m", credential=_KEY, base_url=f"{server.endpoint}/v1")
        reading = asyncio.create_task(_drain(provider.complete(_ASKED)))
        await asyncio.wait_for(server.asked.wait(), timeout=10)

        assert await provider.cancel() == "acked"

        events = await asyncio.wait_for(reading, timeout=5)
        await asyncio.wait_for(server.hung_up.wait(), timeout=5)
        assert await provider.cancel() == "no_turn"
        await provider.shutdown()

    assert [(e.kind, e.stop_reason) for e in events] == [(EVENT_COMPLETE, "cancelled")]


@pytest.mark.asyncio
async def test_the_anthropic_compatible_client_closes_its_request_too(monkeypatch):
    """The ``anthropic`` SDK is optional and not installed for the suite, so its stream is a
    stand-in whose request never answers: the stop has to reach the await the request is parked
    on, which is where the real SDK's HTTP client closes the connection."""
    held = _HeldSdkStream()
    monkeypatch.setitem(sys.modules, "anthropic", held.module())
    from personalclaw.llm.anthropic import AnthropicProvider

    provider = AnthropicProvider(model="m", credential=_KEY)
    reading = asyncio.create_task(_drain(provider.complete(_ASKED)))
    await asyncio.wait_for(held.asked.wait(), timeout=10)

    assert await provider.cancel() == "acked"

    events = await asyncio.wait_for(reading, timeout=5)
    assert held.stopped.is_set(), "the request the SDK was waiting on was never stopped"
    assert [(e.kind, e.stop_reason) for e in events] == [(EVENT_COMPLETE, "cancelled")]


@pytest.mark.asyncio
async def test_the_relay_passes_on_what_the_request_raises_and_closes_it_when_reading_stops():
    from personalclaw.llm.inflight import InFlightRequests

    requests = InFlightRequests()

    async def refused():
        raise ValueError("the endpoint refused")
        yield  # an async generator

    with pytest.raises(ValueError, match="the endpoint refused"):
        await _drain(requests.relay(refused()))

    closed = asyncio.Event()

    async def endless():
        try:
            while True:
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="more")
                await asyncio.sleep(0)
        finally:
            closed.set()

    relay = requests.relay(endless())
    assert (await relay.__anext__()).text == "more"
    await relay.aclose()
    await asyncio.wait_for(closed.wait(), timeout=5)
    assert await requests.cancel() == "no_turn"


_ASKED = [{"role": "user", "content": "a question the endpoint is slow to answer"}]


def _key():
    from personalclaw.llm.credentials import Credential

    return Credential(name="t", kind="api_key", secret="placeholder", source="none")


_KEY = _key()


class _HeldSdkStream:
    """An ``anthropic`` module whose ``messages.stream`` request never answers."""

    def __init__(self) -> None:
        self.asked = asyncio.Event()
        self.stopped = asyncio.Event()

    def module(self) -> types.ModuleType:
        held = self

        class _Stream:
            async def __aenter__(self):
                held.asked.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    held.stopped.set()
                    raise

            async def __aexit__(self, *exc: object) -> None:
                return None

        class _Messages:
            def stream(self, **_request: object) -> _Stream:
                return _Stream()

        class AsyncAnthropic:
            def __init__(self, **_client: object) -> None:
                self.messages = _Messages()

            async def close(self) -> None:
                return None

        fake = types.ModuleType("anthropic")
        fake.AsyncAnthropic = AsyncAnthropic  # type: ignore[attr-defined]
        return fake


async def _drain(stream) -> list:
    return [ev async for ev in stream]


async def _collect(rt: NativeAgentRuntime, events: list, answering: asyncio.Event) -> None:
    async for ev in rt.stream("a question the model is slow to answer"):
        events.append(ev)
        if ev.kind == EVENT_TEXT_CHUNK:
            answering.set()
