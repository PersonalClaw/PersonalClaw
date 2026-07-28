"""CONTEXT-ECONOMY §5 — dynamic tool-group activation.

Covers the group MODEL (derivation from providers, core always-on), the
ACTIVATION lifecycle (`reset_tools` final-state semantics, next-turn boundary,
per-surface defaults), and — most importantly — the **fail-open triad** that
makes hiding schemas safe:

1. dispatch is never filtered (an inactive group's tool still runs);
2. inactive groups leave a stub line, not silence;
3. `tool_search` reaches across groups and names the activation step.

Plus the no-regression lock (Success Criterion #10): with grouping off, the tool
schema block is BYTE-IDENTICAL to the ungrouped assembly.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    AgentEvent,
)
from personalclaw.tool_providers import groups as g
from personalclaw.tool_providers.base import ToolDefinition, ToolProvider, ToolResult


class _ScriptedModel:
    """A ModelProvider replaying scripted turns, recording the tools kwarg per turn."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0
        self.tools_per_turn: list[list[dict] | None] = []
        self.seen_messages: list[list[dict]] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.tools_per_turn.append(tools)
        self.seen_messages.append([dict(m) for m in messages])
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


class _Prov(ToolProvider):
    """A tool provider surfacing named tools under one provider name."""

    def __init__(self, provider_name: str, tools: list[str], *, stamp: bool = True) -> None:
        self._p = provider_name
        self._tools = tools
        self._stamp = stamp
        self.invoked: list[str] = []

    @property
    def name(self) -> str:
        return self._p

    @property
    def display_name(self) -> str:
        return self._p

    async def list_tools(self):
        return [
            ToolDefinition(
                name=t,
                description=f"does {t}",
                # stamp=False models a provider that forgot to tag its tools.
                provider=self._p if self._stamp else "",
                parameters={"type": "object", "properties": {}},
                requires_approval=False,
            )
            for t in self._tools
        ]

    async def invoke(self, tool_name: str, arguments: dict) -> ToolResult:
        self.invoked.append(tool_name)
        return ToolResult(success=True, output=f"ran {tool_name}")


def _defn() -> AgentRuntimeDefinition:
    return AgentRuntimeDefinition(
        name="a", provider="native", system_prompt="", model="m", tools=[], skills=[]
    )


def _providers() -> list[ToolProvider]:
    return [
        _Prov("personalclaw-filesystem", ["bash", "read_file"]),
        _Prov("personalclaw-schedule", ["schedule_add", "schedule_list"]),
        _Prov("personalclaw-memory", ["memory_remember", "memory_recall"]),
        _Prov("personalclaw-artifacts", ["artifact_save"]),
    ]


def _surfaced(model: _ScriptedModel, turn: int = 0) -> list[str]:
    return [t["function"]["name"] for t in (model.tools_per_turn[turn] or [])]


def _sys_text(model: _ScriptedModel, turn: int = 0) -> str:
    return "\n".join(m["content"] for m in model.seen_messages[turn] if m["role"] == "system")


async def _run(rt: NativeAgentRuntime, message: str) -> None:
    async for _ in rt.stream(message):
        pass


# ── the group model (§5.1) ──


def test_group_name_derived_from_provider():
    assert g.group_name_for_provider("personalclaw-schedule") == "schedule"
    assert g.group_name_for_provider("personalclaw-knowledge-tools") == "knowledge"
    assert g.group_name_for_provider("mcp-tools:github") == "mcp:github"
    assert g.group_name_for_provider("some-app") == "some-app"
    assert g.group_name_for_provider("") == "other"


def test_platform_providers_are_the_core_group():
    """The always-on group holds the platform bundle + the in-process core module."""
    assert g.group_name_for_provider("personalclaw-core") == g.CORE_GROUP
    assert g.group_name_for_provider("personalclaw-filesystem") == g.CORE_GROUP


def test_core_locked_tool_is_core_wherever_it_lives():
    """A CORE_LOCKED name can never end up in a deactivatable group — losing it is
    unrecoverable, so provider membership must not decide its fate."""
    d = ToolDefinition(name="grep", description="", provider="mcp-tools:github")
    assert g.group_of_tool(d) == g.CORE_GROUP
    other = ToolDefinition(name="some_tool", description="", provider="mcp-tools:github")
    assert g.group_of_tool(other) == "mcp:github"


def test_partition_puts_core_first_and_is_deterministic():
    defs = [
        ToolDefinition(name="schedule_add", description="", provider="personalclaw-schedule"),
        ToolDefinition(name="bash", description="", provider="personalclaw-filesystem"),
        ToolDefinition(name="memory_recall", description="", provider="personalclaw-memory"),
    ]
    names = [grp.name for grp in g.partition(defs)]
    assert names[0] == g.CORE_GROUP  # the always-on anchor leads
    assert g.partition(defs) == g.partition(defs)  # stable ⇒ stable serialization
    assert next(x for x in g.partition(defs) if x.name == g.CORE_GROUP).always_on


def test_partition_honors_resolved_provider_override():
    """A provider that forgot to stamp its tools still groups correctly — the
    runtime passes the same provider key its disable gate resolved."""
    d = ToolDefinition(name="zzz", description="", provider="")
    assert g.partition([d])[0].name == "other"
    grouped = g.partition([d], provider_of={"zzz": "personalclaw-schedule"})
    assert grouped[0].name == "schedule"


# ── per-surface defaults (§5.4) ──


def test_default_groups_none_when_feature_off(monkeypatch):
    """Off ⇒ every group active ⇒ the runtime skips filtering entirely."""
    monkeypatch.setattr(g, "groups_enabled", lambda: False)
    assert g.resolve_default_groups("background") is None


def test_default_groups_focus_background_surfaces(monkeypatch):
    monkeypatch.setattr(g, "groups_enabled", lambda: True)
    assert g.resolve_default_groups("background") == {g.CORE_GROUP, "memory"}
    assert g.resolve_default_groups("loops") == {g.CORE_GROUP, "workflows", "subagents"}
    # Interactive chat has no entry → all groups active (today's behavior).
    assert g.resolve_default_groups("chat") is None


def test_config_group_defaults_override_builtin(monkeypatch, tmp_path):
    monkeypatch.setattr(g, "groups_enabled", lambda: True)

    class _Cfg:
        class tools:  # noqa: N801
            group_defaults = {"background": ["schedule"]}

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg())
    )
    # core is implied even when the config entry omits it.
    assert g.resolve_default_groups("background") == {g.CORE_GROUP, "schedule"}


def test_star_means_all_groups(monkeypatch):
    monkeypatch.setattr(g, "groups_enabled", lambda: True)

    class _Cfg:
        class tools:  # noqa: N801
            group_defaults = {"loops": ["*"]}

    monkeypatch.setattr(
        "personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: _Cfg())
    )
    assert g.resolve_default_groups("loops") is None


# ── no regression: the byte-identical default path (Success Criterion #10) ──


@pytest.mark.asyncio
async def test_ungrouped_schema_is_byte_identical_to_assembly():
    """With grouping off (the chat default), the tools kwarg must be EXACTLY the
    assembled schema — no reset_tools, no stubs, no reordering. This is the lock
    that keeps enabling the feature a no-op for interactive chat."""
    model = _ScriptedModel(
        [[AgentEvent(kind=EVENT_TEXT_CHUNK, text="hi"), AgentEvent(kind=EVENT_COMPLETE)]]
    )
    rt = NativeAgentRuntime(definition=_defn(), model_provider=model, tool_providers=_providers())
    await rt.start()
    assert rt._active_groups is None  # no grouping in effect
    await _run(rt, "hello there")
    assert json.dumps(model.tools_per_turn[0]) == json.dumps(rt._tool_schema)
    assert "reset_tools" not in _surfaced(model)
    assert _sys_text(model) == ""  # no stub/catalog note injected


# ── activation lifecycle (§5.2) ──


@pytest.mark.asyncio
async def test_inactive_group_schemas_are_dropped_but_stubbed():
    """Fail-open triad #2: an inactive group costs ONE line, not silence."""
    model = _ScriptedModel(
        [[AgentEvent(kind=EVENT_TEXT_CHUNK, text="hi"), AgentEvent(kind=EVENT_COMPLETE)]]
    )
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=["memory"],
    )
    await rt.start()
    await _run(rt, "hello")
    names = _surfaced(model)
    assert "memory_recall" in names and "bash" in names  # active + core
    assert "schedule_add" not in names and "artifact_save" not in names  # deactivated
    assert "reset_tools" in names  # the activation affordance is always offered
    note = _sys_text(model)
    assert "schedule (2 tools, INACTIVE)" in note
    assert "artifacts (1 tool, INACTIVE)" in note  # singular reads correctly
    assert 'reset_tools({"schedule": true})' in note


@pytest.mark.asyncio
async def test_reset_tools_is_final_state_not_delta():
    """agentscope's semantics: whatever you don't list, deactivates. Deltas drift
    over a long session; final state can't."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=["memory"],
    )
    await rt.start()
    out = rt._reset_tools({"groups": {"schedule": True}})
    assert rt._active_groups == {g.CORE_GROUP, "schedule"}  # memory dropped
    assert "memory" in out and "Inactive:" in out
    active = {getattr(d, "name", "") for d in rt._active_defs}
    assert "schedule_add" in active and "memory_recall" not in active


@pytest.mark.asyncio
async def test_reset_tools_cannot_deactivate_core():
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=["memory"],
    )
    await rt.start()
    rt._reset_tools({"groups": {}})  # ask for nothing
    assert g.CORE_GROUP in (rt._active_groups or set())
    assert "bash" in {getattr(d, "name", "") for d in rt._active_defs}


@pytest.mark.asyncio
async def test_reset_tools_returns_newly_activated_instructions():
    """Usage guidance arrives exactly when the tools do (the R12 router shape)."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=[],
    )
    await rt.start()
    out = rt._reset_tools({"groups": {"schedule": True}})
    assert "[schedule]" in out and "reminders" in out
    # Re-activating an already-active group doesn't repeat its instructions.
    again = rt._reset_tools({"groups": {"schedule": True}})
    assert "[schedule]" not in again


@pytest.mark.asyncio
async def test_reset_tools_rejects_bad_payload_and_names_unknown_groups():
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=["memory"],
    )
    await rt.start()
    assert rt._reset_tools({"groups": "schedule"}).startswith("Error:")
    out = rt._reset_tools({"groups": {"nope": True, "schedule": True}})
    assert "Unknown group(s) ignored: nope" in out
    assert rt._active_groups == {g.CORE_GROUP, "schedule"}  # the valid part applied


@pytest.mark.asyncio
async def test_group_change_takes_effect_next_turn_not_mid_turn():
    """§3 prefix corollary: the tool block is rewritten at the NEXT turn boundary,
    so the in-flight turn's schema (and its cache prefix) is untouched."""
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="c1",
                    title="reset_tools",
                    tool_input=json.dumps({"groups": {"schedule": True}}),
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [AgentEvent(kind=EVENT_TEXT_CHUNK, text="ok"), AgentEvent(kind=EVENT_COMPLETE)],
        ]
    )
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=["memory"],
    )
    await rt.start()
    await _run(rt, "schedule a reminder")
    # Both inferences WITHIN this turn saw the same (pre-change) tool block.
    assert _surfaced(model, 0) == _surfaced(model, 1)
    assert "schedule_add" not in _surfaced(model, 1)
    # The next turn carries the new block + the change note.
    await _run(rt, "now do it")
    assert "schedule_add" in _surfaced(model, -1)
    assert "[tool groups]" in _sys_text(model, -1)


# ── the fail-open triad (§5.3) ──


@pytest.mark.asyncio
async def test_dispatch_of_inactive_group_tool_still_works():
    """Triad #1, the load-bearing invariant: deactivation removes SCHEMAS only.
    _tool_index is never filtered, so a model that calls a hidden tool by name
    succeeds — group activation is context economy, not a security boundary."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    provs = _providers()
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=provs, tool_groups=[]
    )
    await rt.start()
    assert "artifact_save" not in {getattr(d, "name", "") for d in rt._active_defs}
    assert await rt._invoke("artifact_save", {}) == "ran artifact_save"
    assert "artifact_save" in provs[3].invoked


@pytest.mark.asyncio
async def test_tool_search_reaches_across_inactive_groups():
    """Triad #3: search ranks the FULL catalog and names the activation step, so a
    hidden tool is one search + one call (or one activation) away."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=[],
    )
    await rt.start()
    out = await rt._invoke("tool_search", {"query": "artifact_save"})
    assert "artifact_save" in out
    assert "INACTIVE group 'artifacts'" in out
    assert 'reset_tools({"artifacts": true})' in out
    assert "still callable by name" in out


@pytest.mark.asyncio
async def test_grouping_composes_with_retrieval_reduction():
    """Both reductions at once (the interaction path): retrieval spends its K budget
    only WITHIN active groups, the deferred-schema catalog lists only active-group
    tools, and inactive groups are represented by stubs rather than catalog lines."""
    big = _Prov("personalclaw-schedule", [f"sched_{i}" for i in range(40)])
    active = _Prov("personalclaw-memory", [f"mem_{i}" for i in range(40)])
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[_Prov("personalclaw-filesystem", ["bash"]), big, active],
        tool_groups=["memory"],
    )
    await rt.start()
    # Force retrieval to actually reduce within the active pool (41 active > k).
    rt._tool_retriever._k = 10
    await _run(rt, "remember something about mem_7")
    surfaced = _surfaced(model)
    # Nothing from the inactive group carries a schema...
    assert not any(n.startswith("sched_") for n in surfaced)
    note = _sys_text(model)
    # ...and none of its tools appear in the deferred-schema CATALOG either — the
    # group's single stub line represents them instead.
    assert "sched_0:" not in note
    assert "schedule (40 tools, INACTIVE)" in note
    # The active group's deferred tail IS catalogued (progressive disclosure intact).
    assert "[tool catalog]" in note
    assert "mem_" in note


@pytest.mark.asyncio
async def test_tool_schema_expands_an_inactive_group_tool():
    """Progressive disclosure composes with groups: the schema expander reads the
    FULL catalog, so an inactive group's tool can be called correctly."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=[],
    )
    await rt.start()
    out = json.loads(await rt._invoke("tool_schema", {"tool_name": "artifact_save"}))
    assert out["name"] == "artifact_save"


@pytest.mark.asyncio
async def test_reset_tools_needs_no_approval():
    """It changes what the model SEES, not what it can do — RiskLevel.SAFE, so it
    must never park the loop on the approval gate."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=_providers(), tool_groups=[]
    )
    await rt.start()
    assert rt._requires_approval("reset_tools") is False


@pytest.mark.asyncio
async def test_user_disabled_tool_stays_gone_regardless_of_groups(monkeypatch):
    """Assembly ORDER matters: the hard gates run BEFORE grouping, so activating a
    group can never resurrect a user-disabled tool."""
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.load_disabled",
        lambda: {"personalclaw-schedule:schedule_add"},
    )
    monkeypatch.setattr(
        "personalclaw.tool_providers.tool_prefs.load_disabled_providers", lambda: set()
    )
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=["schedule"],
    )
    await rt.start()
    rt._reset_tools({"groups": {"schedule": True}})
    assert "schedule_add" not in {getattr(d, "name", "") for d in rt._active_defs}
    assert "schedule_add" not in rt._tool_index  # not dispatchable either
    assert "schedule_list" in {getattr(d, "name", "") for d in rt._active_defs}


@pytest.mark.asyncio
async def test_unattended_strip_precedes_grouping():
    """The other hard gate: an interactive tool stays stripped from an unattended
    run even when its group is active."""
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=[_Prov("personalclaw-schedule", ["ask_user", "schedule_add"])],
        tool_groups=["schedule"],
        unattended=True,
    )
    await rt.start()
    names = {getattr(d, "name", "") for d in rt._active_defs}
    assert "ask_user" not in names
    assert "schedule_add" in names


# ── per-capability gating (§5.5) ──


def test_capability_probe_fails_open_on_every_uncertainty(monkeypatch):
    """A wrongly-HIDDEN group is the capability regression this module promises not
    to cause, so every uncertainty resolves toward available."""
    assert g.capability_available("") is True  # no declaration
    assert g.capability_available("bogus_kind:x") is True  # unknown probe kind
    assert g.capability_available("tool_provider:definitely-not-registered") is False

    def _boom(_use_case):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("personalclaw.providers.provider_bridge.can_resolve_use_case", _boom)
    assert g.capability_available("model:orchestration") is True  # error → available


def test_always_on_group_is_never_gated(monkeypatch):
    """No probe may remove `core` — it holds the primitives an agent can't recover
    from losing."""
    gated_core = g.ToolGroup(
        name=g.CORE_GROUP, display="Core", always_on=True, capability="tool_provider:nope"
    )
    assert g.offerable(gated_core) is True
    assert g.offerable(g.ToolGroup(name="x", display="X", capability="tool_provider:nope")) is False


@pytest.mark.asyncio
async def test_unofferable_group_is_neither_active_nor_stubbed(monkeypatch):
    """§5.5: the model never sees tools that cannot work — no schemas AND no stub."""
    monkeypatch.setattr(g, "_GROUP_CAPABILITY", {"schedule": "tool_provider:nope"})
    model = _ScriptedModel(
        [[AgentEvent(kind=EVENT_TEXT_CHUNK, text="hi"), AgentEvent(kind=EVENT_COMPLETE)]]
    )
    rt = NativeAgentRuntime(
        definition=_defn(),
        model_provider=model,
        tool_providers=_providers(),
        tool_groups=["schedule", "memory"],
    )
    await rt.start()
    assert "schedule" in rt._unofferable
    assert "schedule" not in (rt._active_groups or set())  # requested, but withheld
    await _run(rt, "hello")
    assert "schedule_add" not in _surfaced(model)
    note = _sys_text(model)
    assert "schedule (" not in note  # NOT stub-listed either
    assert "artifacts (" in note  # an offerable inactive group still stubs


@pytest.mark.asyncio
async def test_reset_tools_refuses_an_unofferable_group_and_says_why(monkeypatch):
    """Silently returning an unchanged set would leave the model retrying; name it."""
    monkeypatch.setattr(g, "_GROUP_CAPABILITY", {"schedule": "tool_provider:nope"})
    model = _ScriptedModel([[AgentEvent(kind=EVENT_COMPLETE)]])
    rt = NativeAgentRuntime(
        definition=_defn(), model_provider=model, tool_providers=_providers(), tool_groups=[]
    )
    await rt.start()
    out = rt._reset_tools({"groups": {"schedule": True, "memory": True}})
    assert "Unavailable in this install" in out and "schedule" in out
    assert "memory" in (rt._active_groups or set())  # the valid part still applied
    assert "schedule" not in (rt._active_groups or set())
    # ...and it isn't listed as merely "Inactive" (which would imply activatable).
    inactive_line = next((p for p in out.split(". ") if p.startswith("Inactive:")), "")
    assert "schedule" not in inactive_line


def test_subagents_group_declares_its_model_capability():
    """The shipped gating: subagent tools inference through a ModelProvider, so with
    no model resolvable they'd fail at the first turn."""
    assert g._GROUP_CAPABILITY.get("subagents") == "model:orchestration"
    defs = [ToolDefinition(name="subagent_run", description="", provider="personalclaw-subagents")]
    assert g.partition(defs)[0].capability == "model:orchestration"
