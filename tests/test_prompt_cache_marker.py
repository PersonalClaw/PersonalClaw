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
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config.loader import AppConfig
from personalclaw.context import ContextBuilder
from personalclaw.llm import prompt_cache as pc_module
from personalclaw.llm.anthropic import _VOLATILE_MESSAGE_KEY, _translate_messages
from personalclaw.llm.base import ModelProvider
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.llm.prompt_cache import (
    CACHE_HINT_KEY,
    PromptCache,
    effective_cache_mode,
    mark_cacheable_prefix,
)
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader

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


# ── §C6: the `agent.prompt_cache_enabled` switch ──────────────────────────────
#
# Five-point config wiring (dataclass+_meta, load(), to_dict(), the _EDITABLE_CONFIG
# PATCH allowlist, the frontend control) plus the two behavioural clauses: disabled
# reads as NONE through the SAME code path, and the §C2/§C3 ordering repairs are NOT
# gated by it.


@pytest.fixture()
def cache_switch(tmp_path, monkeypatch):
    """Write a config.json holding ``agent.prompt_cache_enabled`` and point the loader
    at it. Deliberately goes through the real ``AppConfig.load()`` so a test using this
    also exercises load()'s explicit mapping — drop that mapping and these go red."""

    def _write(enabled: bool | None) -> Path:
        agent: dict = {} if enabled is None else {"prompt_cache_enabled": enabled}
        p = tmp_path / "config.json"
        p.write_text(json.dumps({"agent": agent}), encoding="utf-8")
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: p)
        return p

    return _write


def test_default_is_on(cache_switch):
    """Default True (§C6): this atom alone must not disable what PCS-4 just enabled.
    A config with no `prompt_cache_enabled` key reads as ENABLED."""
    cache_switch(None)
    assert AppConfig.load().agent.prompt_cache_enabled is True
    assert AppConfig().agent.prompt_cache_enabled is True


@pytest.mark.parametrize("declared", list(PromptCache))
def test_enabled_passes_the_declared_mode_through(declared):
    assert effective_cache_mode(declared, enabled=True) is declared


@pytest.mark.parametrize("declared", list(PromptCache))
def test_disabled_collapses_every_declared_mode_to_none(declared):
    """ "Middleware treats disabled as NONE" — for every mode, including NONE itself."""
    assert effective_cache_mode(declared, enabled=False) is PromptCache.NONE


def test_disabled_still_runs_the_marker_call_one_path_not_a_bypass():
    """The switch is NOT a branch around ``mark_cacheable_prefix``: the call happens
    either way and NONE's existing untouched-list contract does the work. So a
    disabled EXPLICIT provider takes the exact route an undeclared provider takes."""
    msgs = _sample_messages()
    off = mark_cacheable_prefix(msgs, effective_cache_mode(PromptCache.EXPLICIT, enabled=False))
    undeclared = mark_cacheable_prefix(msgs, PromptCache.NONE)
    assert off is msgs and undeclared is msgs  # same object, same path
    assert not any(CACHE_HINT_KEY in m for m in off)


# ── the switch through the real native loop ───────────────────────────────────


@pytest.mark.asyncio
async def test_runtime_explicit_with_switch_off_hands_back_the_same_object(cache_switch):
    """The whole point of the atom: PCS-4 made the marker unconditional for an EXPLICIT
    adapter. With the switch off, complete() sees rt's own list — no marker at all."""
    cache_switch(False)
    model = _RecordingModel(prompt_cache=PromptCache.EXPLICIT)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[])
    await rt.start()
    await _drain(rt)
    assert model.seen is rt._messages
    assert not any(CACHE_HINT_KEY in m for m in model.seen)


@pytest.mark.asyncio
async def test_runtime_explicit_with_switch_on_still_marks(cache_switch):
    """Positive control for the test above — proves the fixture drives the real read
    rather than the switch-off assertion passing for some unrelated reason."""
    cache_switch(True)
    model = _RecordingModel(prompt_cache=PromptCache.EXPLICIT)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[])
    await rt.start()
    await _drain(rt)
    assert model.seen is not rt._messages
    assert sum(1 for m in model.seen if CACHE_HINT_KEY in m) == 1


def test_unreadable_config_reads_as_enabled(monkeypatch):
    """An unparseable config must not silently change what is served: the field's
    default is True, so a failed read reports ENABLED."""
    rt = NativeAgentRuntime(definition=_defn(), model_provider=_RecordingModel(), tool_providers=[])
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: (_ for _ in ()).throw(OSError)))
    assert rt._prompt_cache_enabled() is True


# ── NO DUAL PATH: the §C2/§C3 ordering repairs are not gated by the switch ────


def test_ordering_repairs_are_not_gated_by_the_switch(cache_switch, tmp_path):
    """PCS-1's stability-ordered wire and PCS-2's date relocation are CORRECTNESS
    repairs, not cache features. Turning caching off must not restore the old ordering
    — a second maintained ordering is exactly the dual path the clean-break doctrine
    forbids. This is the ratchet that makes gating them go red.
    """
    cache_switch(False)
    assert AppConfig.load().agent.prompt_cache_enabled is False  # the switch really is off

    # §C2 — stable assembled context still leads; the volatile per-turn note still
    # rides at the TAIL rather than being hoisted into the out-of-band system=.
    system, out = _translate_messages(
        [
            {"role": "system", "content": "stable assembled context"},
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "per-turn tool catalog", _VOLATILE_MESSAGE_KEY: True},
        ]
    )
    assert system == "stable assembled context"
    assert "per-turn tool catalog" not in system
    assert out[-1] == {"role": "user", "content": "per-turn tool catalog"}

    # §C3 — the assembled context still ENDS with the date line.
    ctx = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    ).build_session_context(session_key="s1")
    assert ctx.count("[CURRENT DATE]") == 1
    tail = ctx[ctx.rindex("[CURRENT DATE]") :]
    assert "\n\n" not in tail.rstrip("\n"), "content leaked after the date line"


@pytest.mark.asyncio
async def test_switch_off_keeps_the_volatile_tag_on_the_per_turn_note(cache_switch):
    """The §C2 repair as the LOOP produces it (not just as the adapter translates it):
    with caching off the per-turn note is still tagged volatile, so any cache-aware
    adapter still relocates it. Gating the tag on the switch would fail here."""
    cache_switch(False)
    model = _RecordingModel(prompt_cache=PromptCache.EXPLICIT)
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=[])
    await rt.start()
    # Force a per-turn note so the tagging branch is reached regardless of tool surface.
    note = "per-turn tool catalog"
    rt._prepare_turn_tools = lambda message: (None, note)  # type: ignore[method-assign]
    await _drain(rt)
    notes = [m for m in rt._messages if m.get("role") == "system"]
    assert notes, "the per-turn note never reached the history"
    assert all(m.get("_volatile") is True for m in notes)


# ── the PATCH allowlist round-trips (write it, then read it back) ─────────────


def _patch_app():
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.mark.asyncio
async def test_patch_round_trips_through_the_editable_allowlist(cache_switch):
    """Point 4 of the five: the field is PATCHable, the write lands in config.json, and
    a fresh load() reads it back — proving the allowlist entry and load()'s mapping
    agree. Then flip it back, so the round trip is proven in BOTH directions."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert _EDITABLE_CONFIG.get("agent.prompt_cache_enabled") == {"type": "bool"}

    cfg_path = cache_switch(None)  # start from the default (ON)
    async with TestClient(TestServer(_patch_app())) as c:
        resp = await c.patch(
            "/api/config/personalclaw",
            json={"path": "agent.prompt_cache_enabled", "value": False},
        )
        assert resp.status == 200
        assert json.loads(cfg_path.read_text())["agent"]["prompt_cache_enabled"] is False
        assert AppConfig.load().agent.prompt_cache_enabled is False

        resp = await c.patch(
            "/api/config/personalclaw",
            json={"path": "agent.prompt_cache_enabled", "value": True},
        )
        assert resp.status == 200
        assert json.loads(cfg_path.read_text())["agent"]["prompt_cache_enabled"] is True
        assert AppConfig.load().agent.prompt_cache_enabled is True


@pytest.mark.asyncio
async def test_patch_rejects_a_non_bool(cache_switch):
    """The allowlist's declared type is enforced, so a stray string can't wedge the
    switch into a truthy-but-not-boolean state."""
    cache_switch(None)
    async with TestClient(TestServer(_patch_app())) as c:
        resp = await c.patch(
            "/api/config/personalclaw",
            json={"path": "agent.prompt_cache_enabled", "value": "yes"},
        )
        assert resp.status == 400


def test_to_dict_and_save_carry_the_field(cache_switch):
    """Point 3: to_dict() emits the field (via asdict), so save() persists it rather
    than silently dropping the user's choice on the next write."""
    cache_switch(None)
    cfg = AppConfig.load()
    assert cfg.to_dict()["agent"]["prompt_cache_enabled"] is True
    cfg.agent.prompt_cache_enabled = False
    cfg.save()
    assert AppConfig.load().agent.prompt_cache_enabled is False


def test_the_field_carries_meta_for_the_settings_surface():
    """Point 1: the dataclass field declares _meta, which is what renders it as a
    labelled control rather than an anonymous key."""
    from dataclasses import fields as dc_fields

    from personalclaw.config.loader import AgentConfig

    f = next(f for f in dc_fields(AgentConfig) if f.name == "prompt_cache_enabled")
    assert f.default is True
    assert f.metadata.get("label") == "Prompt Caching"
    assert f.metadata.get("help")


# ── The app-facing SDK surface (PCS-8) ────────────────────────────────────────
#
# An app whose provider owns its OWN wire (bedrock-models' Converse client) must READ
# the neutral marker to translate it into its vendor's syntax — core never learns that
# syntax. Apps may only reach core through ``personalclaw.sdk.*``
# (tests/test_apps_import_boundary.py), so the marker key has to be ON that facade or
# the out-of-repo consumer is stranded with a hand-copied string literal.


def test_the_marker_key_is_on_the_app_facing_sdk_facade():
    """``CACHE_HINT_KEY`` is re-exported by ``personalclaw.sdk.model`` and is the SAME
    object as the neutral definition — not a second copy that could drift."""
    from personalclaw.llm import prompt_cache as neutral
    from personalclaw.sdk import model as sdk_model

    # Imported the way an APP imports it — `from personalclaw.sdk.model import X` — and not
    # only as an attribute read, because the inert-surface ratchet counts a `sdk_export` as
    # consumed by scanning for exactly that ImportFrom shape. An attribute read through the
    # module object leaves the export looking dead to the ratchet while an app depends on it.
    from personalclaw.sdk.model import CACHE_HINT_KEY as sdk_cache_hint_key

    assert sdk_cache_hint_key is neutral.CACHE_HINT_KEY
    assert sdk_model.CACHE_HINT_KEY is neutral.CACHE_HINT_KEY
    assert "CACHE_HINT_KEY" in sdk_model.__all__, (
        "CACHE_HINT_KEY must be in personalclaw.sdk.model.__all__ — an app's provider "
        "reads it to translate the marker into its own wire form (PCS-8)."
    )
