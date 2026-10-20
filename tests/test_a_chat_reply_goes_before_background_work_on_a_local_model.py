"""A chat's reply on a local model goes before background work, and a wait says what it waited for.

Measured: a chat turn on a local model waited its whole 600 s first-token limit, then fell back to
the next model with "did not start answering within 600 seconds". The model had not been slow: the
same chat's background chores (its title, its organising, its consolidation) held it the whole
time, and the reply was sent behind them. A chat's reply resolves unguarded, so it took no turn in
the local queue the chores take theirs in.

These tests drive the real background guard and the real native loop over a fake local model
server that answers one request at a time, holds a request open until the test lets it go, and
records what it was sent. No real model is called.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.routing import rates as rates_mod

SUBSTITUTION = "model_substitution"
HERE_REF, RELAY_REF = "here:tiny", "relay:swift"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rates_mod._overlay_cache = None
    _clear_queues()
    yield tmp_path
    _clear_queues()
    rates_mod._overlay_cache = None


def _clear_queues() -> None:
    queue = sys.modules.get("personalclaw.guardrails.local_queue")
    if queue is not None:
        queue._QUEUES.clear()
        queue._LISTENERS.clear()


def _wait_for_a_busy_model(secs: float) -> None:
    """Set how long a reply waits for a busy local model (Settings → Models → Background)."""
    from personalclaw.config.transactions import mutate_config

    mutate_config(lambda doc: doc.setdefault("background", {}).update(busy_model_wait_secs=secs))


class _Server:
    """A model server that serves one request at a time, in arrival order, holding a request whose
    last message has a gate until the gate is set."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.lock = asyncio.Lock()
        self.served: list[str] = []
        self.sent = 0
        self.most_sent_at_once = 0
        self.gates: dict[str, asyncio.Event] = {}

    def hold(self, prompt: str) -> asyncio.Event:
        self.gates[prompt] = gate = asyncio.Event()
        return gate


class _Model:
    supports_tools = False

    def __init__(self, server: _Server) -> None:
        self.server = server

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        s = self.server
        last = str(messages[-1].get("content", "")) if messages else ""
        prompt = next((p for p in s.gates if p in last), last)
        s.sent += 1
        s.most_sent_at_once = max(s.most_sent_at_once, s.sent)
        try:
            async with s.lock:
                s.served.append(prompt)
                gate = s.gates.get(prompt)
                if gate is not None:
                    await gate.wait()
        finally:
            s.sent -= 1
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"{s.name} answered")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=10, output_tokens=5)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


def _capability(type_: str, **declared: Any) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
        **declared,
    )


@pytest.fixture
def machine(monkeypatch):
    """A model on this machine (``here``) and one in the cloud (``relay``). ``active`` is what
    Settings → Models binds: background chores on the local model, the chat on it first."""
    servers = {"here": _Server("here"), "relay": _Server("relay")}
    active = {"background": [HERE_REF], "chat": [HERE_REF, RELAY_REF]}
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(servers[entry.name])

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: active)
    return servers, active


async def _until(check, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not check():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.01)


async def _chat_runtime():
    from personalclaw.providers import provider_bridge

    runtime = provider_bridge._build_native_runtime(
        use_case="chat",
        session_key="dashboard:chat-7",
        agent=None,
        model_override=None,
        cwd=None,
    )
    await runtime.start()
    runtime.set_approval_policy("auto")
    runtime.announce_failover()
    return runtime


async def _chore(prompt: str) -> str:
    """A background chore on the local model, through the guard every chore goes through."""
    from personalclaw.llm_helpers import one_shot_completion

    return await one_shot_completion(prompt, use_case="background")


def test_a_reply_with_another_model_moves_on_and_says_it_waited_behind_background_work(machine):
    """🔴 The measured run: the reply waited its whole first-token limit behind the chat's own
    chores, and the line blamed the model's reading time."""
    servers, _active = machine
    _wait_for_a_busy_model(1.0)

    async def scenario() -> list[LLMEvent]:
        gate = servers["here"].hold("Consolidate chat-7")
        chore = asyncio.create_task(_chore("Consolidate chat-7"))
        await _until(lambda: servers["here"].served == ["Consolidate chat-7"])
        runtime = await _chat_runtime()
        try:
            return await asyncio.wait_for(
                _collect(runtime.stream("Pack for the hike")), timeout=3.0
            )
        finally:
            gate.set()
            await chore

    events = asyncio.run(scenario())

    kinds = [ev.kind for ev in events]
    assert kinds == [SUBSTITUTION, EVENT_TEXT_CHUNK, EVENT_COMPLETE], kinds
    assert events[0].text == (
        f"Ran on {RELAY_REF} instead of {HERE_REF}: it waited 1 s behind background work on this "
        "machine."
    )
    assert events[1].text == "relay answered"
    assert servers["here"].served == ["Consolidate chat-7"], "the reply was never sent behind it"


def test_a_reply_goes_before_queued_background_work_and_alone(machine):
    """With no other model to ask, the reply waits for the call already running, then goes before
    every chore still waiting, and the local model is never sent two requests at once."""
    servers, active = machine
    active["chat"] = [HERE_REF]

    async def scenario() -> None:
        gate = servers["here"].hold("Title chat-7")
        first = asyncio.create_task(_chore("Title chat-7"))
        await _until(lambda: servers["here"].served == ["Title chat-7"])
        queued = asyncio.create_task(_chore("Organise chat-7"))
        await asyncio.sleep(0.05)
        runtime = await _chat_runtime()
        reply = asyncio.create_task(_collect(runtime.stream("Pack for the hike")))
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(first, queued, reply)

    asyncio.run(scenario())

    assert servers["here"].served == ["Title chat-7", "Pack for the hike", "Organise chat-7"]
    assert servers["here"].most_sent_at_once == 1


def test_a_waiting_reply_is_shown_in_its_chat(machine):
    from personalclaw.guardrails import local_queue

    servers, _active = machine
    _wait_for_a_busy_model(5.0)

    async def scenario() -> list[dict]:
        gate = servers["here"].hold("Consolidate chat-7")
        chore = asyncio.create_task(_chore("Consolidate chat-7"))
        await _until(lambda: servers["here"].served == ["Consolidate chat-7"])
        runtime = await _chat_runtime()
        reply = asyncio.create_task(_collect(runtime.stream("Pack for the hike")))
        await _until(lambda: bool(local_queue.waits()))
        rows = local_queue.waits()
        gate.set()
        await asyncio.gather(chore, reply)
        return rows

    (row,) = asyncio.run(scenario())

    assert (row["step"], row["session"], row["busy_with"], row["next"]) == (
        "Your reply",
        "dashboard:chat-7",
        "background work",
        RELAY_REF,
    )


def test_one_local_model_reached_at_two_addresses_is_one_queue(monkeypatch):
    """Two entries for one runtime on this machine, at two addresses, still send one model's
    requests to one machine: they take turns."""
    from personalclaw.guardrails.local_queue import queue_key

    registry = ProviderRegistry()
    registry.register_type(_capability("local-runtime"), lambda **_kw: None)
    for name, port in (("chat-runtime", 11435), ("background-runtime", 11436)):
        registry.register_entry(
            ProviderEntry(
                name=name,
                type="local-runtime",
                model="gemma",
                options={"endpoint": f"http://127.0.0.1:{port}"},
            )
        )
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)

    assert queue_key("chat-runtime", "gemma") == queue_key("background-runtime", "gemma") != ""


async def _collect(stream) -> list[LLMEvent]:
    return [event async for event in stream]
