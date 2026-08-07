"""Vendor-neutral prompt-cache marker + native-loop middleware (PROMPT-CACHE-SUBSTRATE).

Covers the two hard invariants:
  1. An undeclared provider (mode NONE) → the message list handed to complete() is
     the SAME object (byte-identical).
  2. ZERO vendor cache strings appear in llm/prompt_cache.py.

Plus the EXPLICIT marker rules (one shallow-copied hinted message, no caller-dict
mutation, deterministic boundary), the capability/instance defaults, and the
compaction generation bump.
"""

from __future__ import annotations

import inspect

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm import prompt_cache as pc_module
from personalclaw.llm.base import ModelProvider
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.llm.prompt_cache import (
    CACHE_HINT_KEY,
    PromptCache,
    mark_cacheable_prefix,
)

# ── mark_cacheable_prefix: NONE / AUTOMATIC leave the list untouched ──────────


def _sample_messages() -> list[dict]:
    return [
        {"role": "user", "content": "stable head"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "follow up"},
    ]


@pytest.mark.parametrize("mode", [PromptCache.NONE, PromptCache.AUTOMATIC])
def test_none_and_automatic_return_same_object(mode):
    msgs = _sample_messages()
    out = mark_cacheable_prefix(msgs, mode)
    assert out is msgs  # identity, not just equality
    assert not any(CACHE_HINT_KEY in m for m in out)


@pytest.mark.parametrize("mode", list(PromptCache))
def test_empty_list_returned_unchanged(mode):
    msgs: list[dict] = []
    out = mark_cacheable_prefix(msgs, mode)
    assert out is msgs


# ── mark_cacheable_prefix: EXPLICIT marks exactly one message ─────────────────


def test_explicit_marks_last_non_tool_non_volatile_and_is_shallow_copy():
    msgs = _sample_messages()
    original_last = msgs[-1]
    out = mark_cacheable_prefix(msgs, PromptCache.EXPLICIT, generation=7)

    # New list, exactly one hinted message.
    assert out is not msgs
    hinted = [i for i, m in enumerate(out) if CACHE_HINT_KEY in m]
    assert hinted == [len(out) - 1]  # documented boundary: last non-tool/non-volatile
    assert out[-1][CACHE_HINT_KEY] == {"generation": 7}

    # Shallow copy: a NEW dict for the hinted message, carrying the same content.
    assert out[-1] is not original_last
    assert out[-1]["role"] == original_last["role"]
    assert out[-1]["content"] == original_last["content"]

    # Non-mutation: the caller's dicts are untouched.
    assert not any(CACHE_HINT_KEY in m for m in msgs)
    assert original_last == {"role": "user", "content": "follow up"}

    # Every non-hinted message passes through by reference.
    for i in range(len(out) - 1):
        assert out[i] is msgs[i]


def test_explicit_skips_tool_and_volatile_tail():
    # Volatile PCS-1 note + a trailing tool result must NOT be the boundary.
    msgs = [
        {"role": "user", "content": "head"},
        {"role": "assistant", "content": "mid"},
        {"role": "tool", "tool_call_id": "c1", "content": "tool out"},
        {"role": "system", "content": "per-turn note", "_volatile": True},
    ]
    out = mark_cacheable_prefix(msgs, PromptCache.EXPLICIT, generation=0)
    # Boundary is the assistant message (index 1) — the last non-tool, non-volatile.
    assert CACHE_HINT_KEY in out[1]
    assert sum(1 for m in out if CACHE_HINT_KEY in m) == 1
    assert not any(CACHE_HINT_KEY in m for m in msgs)  # inputs unmutated


def test_explicit_falls_back_to_head_when_all_tool_or_volatile():
    msgs = [
        {"role": "system", "content": "note", "_volatile": True},
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
    ]
    out = mark_cacheable_prefix(msgs, PromptCache.EXPLICIT, generation=3)
    assert CACHE_HINT_KEY in out[0]
    assert out[0][CACHE_HINT_KEY] == {"generation": 3}
    assert sum(1 for m in out if CACHE_HINT_KEY in m) == 1


# ── Invariant 2: zero vendor cache strings in the neutral module ──────────────


def test_zero_vendor_cache_strings_in_module_source():
    src = inspect.getsource(pc_module)
    for banned in ("cache_control", "cachePoint", "ephemeral"):
        assert banned not in src, f"vendor string {banned!r} leaked into prompt_cache.py"


# ── Capability + instance defaults ────────────────────────────────────────────


def test_provider_capability_defaults_to_none():
    cap = ProviderCapability(
        type="x",
        capabilities=frozenset(),
        supports_streaming=False,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=0,
    )
    assert cap.prompt_cache is PromptCache.NONE


def test_model_provider_instance_defaults_to_none():
    class _Bare(ModelProvider):
        async def start(self) -> None: ...

        async def shutdown(self) -> None: ...

        async def stream(self, message: str):  # pragma: no cover - not driven here
            yield AgentEvent(kind=EVENT_COMPLETE)

        async def approve_tool(self, request_id) -> None: ...

        async def reject_tool(self, request_id) -> None: ...

        def context_usage_pct(self) -> float:
            return 0.0

    assert _Bare().prompt_cache is PromptCache.NONE


def test_branded_spec_threads_prompt_cache_into_capability():
    # Import via the canonical app-facing path (personalclaw.sdk.model), which fixes
    # the package import order for the sdk.model <-> provider_helpers deferred cycle.
    from personalclaw.sdk.model import (
        BrandedProviderSpec,
        ProviderResolutionError,
        get_default_registry,
        register_branded_app,
    )

    spec = BrandedProviderSpec(
        type="pcs3_branded_test",
        protocol="openai",
        capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
        prompt_cache=PromptCache.AUTOMATIC,
    )
    try:
        register_branded_app(spec)
        cap = get_default_registry().capability_of("pcs3_branded_test")
        assert cap.prompt_cache is PromptCache.AUTOMATIC
    except ProviderResolutionError:
        pytest.skip("registry rejected the fixture type (already registered)")


# ── Middleware: NONE is byte-identical; EXPLICIT is a new list ────────────────


class _RecordingModel:
    """A ModelProvider stub that records the exact object handed to complete()."""

    supports_tools = True
    _model = "rec"

    def __init__(self, prompt_cache: PromptCache | None = None) -> None:
        if prompt_cache is not None:
            self.prompt_cache = prompt_cache
        self.seen = None

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.seen = messages
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok")
        yield AgentEvent(kind=EVENT_COMPLETE, input_tokens=1, output_tokens=1)


def _defn() -> AgentRuntimeDefinition:
    return AgentRuntimeDefinition(name="T", provider="native", model="rec")


async def _drain(rt, msg="hi"):
    return [ev async for ev in rt.stream(msg)]


def test_middleware_two_lines_none_is_same_object():
    """The two middleware lines directly: undeclared provider → same object."""
    msgs = _sample_messages()
    fake = _RecordingModel()  # no prompt_cache attr at all
    mode = getattr(fake, "prompt_cache", PromptCache.NONE)
    assert mode is PromptCache.NONE
    assert mark_cacheable_prefix(msgs, mode) is msgs


@pytest.mark.asyncio
async def test_runtime_hands_same_object_to_complete_when_undeclared():
    model = _RecordingModel()  # defaults NONE via getattr
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[])
    await rt.start()
    await _drain(rt)
    # Byte-identical invariant: the object complete() saw is rt's own message list.
    assert model.seen is rt._messages


@pytest.mark.asyncio
async def test_runtime_explicit_builds_new_list_without_mutating_history():
    model = _RecordingModel(prompt_cache=PromptCache.EXPLICIT)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[])
    await rt.start()
    await _drain(rt)
    # A NEW list with exactly one neutrally-hinted message reached complete()...
    assert model.seen is not rt._messages
    assert sum(1 for m in model.seen if CACHE_HINT_KEY in m) == 1
    # ...and self._messages itself carries no hint (never mutated).
    assert not any(CACHE_HINT_KEY in m for m in rt._messages)


# ── Compaction bumps the cache generation ─────────────────────────────────────


def test_compaction_bumps_cache_generation(monkeypatch):
    from personalclaw import context_compaction as cc

    rt = NativeAgentRuntime(definition=_defn(), model_provider=_RecordingModel(), tool_providers=[])
    rt._messages = [{"role": "user", "content": "x" * 400}]
    rt._last_context_pct = 99.0  # over the compaction threshold
    assert rt._cache_generation == 0

    # Force a compaction that shrinks the history.
    monkeypatch.setattr(cc, "should_compact", lambda saves: True)
    monkeypatch.setattr(cc, "total_chars", lambda msgs: 400 if msgs is rt._messages else 40)
    monkeypatch.setattr(cc, "compact", lambda msgs: [{"role": "user", "content": "x"}])

    rt._maybe_compact()
    assert rt._cache_generation == 1
