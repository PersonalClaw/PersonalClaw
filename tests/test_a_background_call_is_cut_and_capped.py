"""A background call is cut at its own ceiling and capped in what it may write.

Measured: a memory consolidation on a local model, sent with no output cap, wrote 26,164 tokens
over 1,224 s and ended with no answer. Nothing cut it, because a provider that keeps a Request
Timeout was held to that alone and a Request Timeout bounds only how long an answer takes to start,
and the local model was held the whole time while a chat's reply waited behind it.

These tests drive the real guard, the real chain walk and the real background runtime over fake
models; no real model is called.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.routing import rates as rates_mod

HERE_REF, RELAY_REF = "here:tiny", "relay:swift"
#: Every call a model was sent, in order.
ASKED: list[str] = []


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


def _time_limit(monkeypatch, secs: float) -> None:
    """Hold Background calls to *secs*, below the 30 s Settings allows, so a cut is seen in real
    time: the limit is read from the one place the code reads it, at each call."""
    from personalclaw.config.loader import BackgroundConfig

    monkeypatch.setattr(
        "personalclaw.config.loader.background_limits",
        lambda: BackgroundConfig(call_timeout_secs=secs),
    )


class _Model:
    """``here`` keeps a Request Timeout, as a local runtime does, and writes without end;
    ``relay`` answers at once. ``built`` records every build's ``max_tokens``."""

    supports_tools = False

    def __init__(self, name: str, answer: str) -> None:
        self.name = name
        self.answer = answer
        self.request_timeout_secs = 600.0 if name == "here" else None

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        ASKED.append(self.name)
        if self.name == "here":
            for _ in range(400):
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="thinking ")
                await asyncio.sleep(0.01)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.answer)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=6_604, output_tokens=40)

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
def built(monkeypatch) -> list[tuple[str, Any]]:
    """The Background chain: a model on this machine, then a cloud model. Returns every build as
    ``(entry, max_tokens)``."""
    builds: list[tuple[str, Any]] = []
    ASKED.clear()
    answers = {"here": '{"history_entry": "From here."}', "relay": '{"history_entry": "Relay."}'}
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kw: Any):
        builds.append((entry.name, kw.get("max_tokens")))
        return _Model(entry.name, answers[entry.name])

    registry.register_type(_capability("offline-weights", in_process=True), _factory)
    registry.register_type(_capability("cloud-api"), _factory)
    registry.register_entry(ProviderEntry(name="here", type="offline-weights", model="tiny"))
    registry.register_entry(ProviderEntry(name="relay", type="cloud-api", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [RELAY_REF], "background": [HERE_REF, RELAY_REF]},
    )
    return builds


class _BackgroundSessions:
    def __init__(self) -> None:
        self.runtime: Any = None

    async def get_or_create(self, key: str, agent: str | None = None, **_kw: Any):
        from personalclaw.agents.defaults import LITE_AGENT_NAME
        from personalclaw.providers import provider_bridge

        if self.runtime is None:
            self.runtime = provider_bridge._build_native_runtime(
                use_case="chat",
                session_key=key,
                agent=agent or LITE_AGENT_NAME,
                model_override=None,
                cwd=None,
                model_axis="background",
            )
            await self.runtime.start()
        return self.runtime, False, True

    def release(self, key: str) -> None:
        return None

    async def recycle_background(self) -> None:
        return None


def test_a_one_shot_background_call_is_cut_at_its_ceiling_and_the_next_model_answers(
    built, monkeypatch
):
    """🔴 A model with a Request Timeout was held to that alone: it wrote until it stopped."""
    from personalclaw.llm_helpers import one_shot_completion

    _time_limit(monkeypatch, 0.3)

    async def scenario() -> tuple[str, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        answer = await one_shot_completion("Consolidate chat-3.", use_case="background")
        return answer, loop.time() - started

    answer, took = asyncio.run(scenario())

    assert answer == '{"history_entry": "Relay."}'
    assert took < 2.0, took


def test_a_consolidation_on_the_background_session_is_cut_and_handed_on(
    built, monkeypatch, tmp_path
):
    """🔴 The measured consolidation: 1,224 s on the local model, uncut, and no answer."""
    from personalclaw.history import ConversationLog, HistoryConsolidator

    _time_limit(monkeypatch, 0.3)

    async def scenario():
        consolidator = HistoryConsolidator(
            ConversationLog(tmp_path / "log"), memory=None, sessions=_BackgroundSessions()
        )
        return await asyncio.wait_for(
            consolidator._call_llm("Consolidate chat-3.", "dashboard:chat-3"), timeout=3.0
        )

    assert asyncio.run(scenario()) == {"history_entry": "Relay."}
    assert ASKED == ["here", "relay"], "a call cut at its ceiling is not sent again"


def test_every_model_of_the_background_session_is_built_with_an_output_cap(built):
    """🔴 The measured consolidation was sent with no output cap at all. It is the Background
    output limit, 4,096 tokens unless the owner changes it."""
    from personalclaw.config.loader import BackgroundConfig

    async def scenario() -> None:
        sessions = _BackgroundSessions()
        runtime, _new, _resumed = await sessions.get_or_create("_bg")
        runtime.failover.build(RELAY_REF)

    asyncio.run(scenario())

    cap = BackgroundConfig().max_output_tokens
    assert built == [("here", cap), ("relay", cap)]
