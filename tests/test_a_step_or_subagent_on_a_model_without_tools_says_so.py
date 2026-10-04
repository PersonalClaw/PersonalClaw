"""A workflow step or a subagent whose model can't use tools does not claim work it could not do.

A subagent is started to read or change things, and a workflow ``stage`` is a subagent with tools.
On a model that can't use them the native runtime started tool-less with one INFO line, so a step
sent to fix a file answered "Done, I wrote the fix." in prose, ended "completed", and its run went
on to the next step. When the model refused the tools a call offered it mid-run (a server answers a
model that can't use them with a 400, and the provider retried without them), the same happened
from that call on.

Now a run whose steps would run on such a model is refused at its start, naming the step and the
model; a subagent started on one ends "Couldn't do its task" before any call is made for it; one
whose model refused its tools mid-run ends the same way; and a step that ended so fails with a fix
about its model, not its tools. A run of the ``text`` class asked for no tools and runs as before,
and a model that uses tools is the positive control throughout. Scripted models only.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.config import AppConfig
from personalclaw.hooks import HookManager
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK, AgentEvent
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.session import SessionManager
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.subagent_persistence import create_agent_folder
from personalclaw.tool_providers.base import RiskLevel, ToolDefinition, ToolProvider, ToolResult

#: What a subagent started on the scripted model that can't use tools ends with.
REFUSED = (
    "Couldn't do its task: “fake-cloud:m-1” can't use tools, and it was started to work with "
    "them. Choose a model that uses tools for Orchestration in Settings → Models, or start it on "
    "one."
)


class _Model:
    """A scripted model that answers every call in text. *refuses_on* is the call (1-based) on which
    it refuses the tools it is offered, as a server refuses a model that can't use them, and from
    which it says it takes none."""

    _model = "m-1"
    served_ref = "fake-cloud:m-1"

    def __init__(self, *, uses_tools: bool = True, refuses_on: int = 0) -> None:
        self.supports_tools = uses_tools
        self.refuses_on = refuses_on
        self.offered: list[bool] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        self.offered.append(bool(tools))
        if tools and len(self.offered) == self.refuses_on:
            self.supports_tools = False
        yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done, I wrote the fix.")
        yield AgentEvent(kind=EVENT_COMPLETE, stop_reason="end_turn", output_tokens=8)

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> str:
        return "acked"


class _Look(ToolProvider):
    """The one tool the agent is given: a read."""

    @property
    def name(self) -> str:
        return "look"

    @property
    def display_name(self) -> str:
        return "Look"

    async def list_tools(self):
        return [
            ToolDefinition(
                name="look",
                description="Read something.",
                parameters={"type": "object"},
                risk_level=RiskLevel.SAFE,
            )
        ]

    async def invoke(self, tool_name, arguments):  # pragma: no cover - the model calls nothing
        return ToolResult(success=True, output="notes.md")


@pytest.fixture
def agent_root(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path)
    return tmp_path


def _manager(model: _Model, *, starts_unasked: bool = False) -> SubagentManager:
    def factory(_key: Any = None, **kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="fixer", provider="native", model="m-1"),
            model_provider=model,  # type: ignore[arg-type]
            tool_providers=[_Look()],
            unattended=bool(kw.get("unattended")),
        )

    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks = HookManager()
    return SubagentManager(
        sessions=SessionManager(AppConfig(), provider_factory=factory),
        ctx_builder=ctx,
        is_yolo=lambda: starts_unasked,
    )


async def _run(model: _Model, **info_kw: Any) -> tuple[SubagentInfo, list[dict]]:
    """Run one subagent on *model*; returns it and what its run audited."""
    manager = _manager(model)
    info = SubagentInfo(
        id="sa-tools", task="fix the typo in notes.md", parent_session_key="dashboard:c", **info_kw
    )
    create_agent_folder(info.id, task=info.task)
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent_tier.Stats"),
        patch("personalclaw.subagent_tier.sel") as audit,
    ):
        await asyncio.wait_for(manager._run_inner(info, f"subagent:{info.id}"), timeout=20)
    return info, [c.kwargs for c in audit.return_value.log_tool_invocation.call_args_list]


# ── a subagent ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_subagent_on_a_model_without_tools_ends_before_any_call(agent_root):
    """🔴 Before: its model was asked, answered "Done, I wrote the fix.", and it ended done with no
    error: a fix claimed, and none made."""
    model = _Model(uses_tools=False)

    info, audited = await _run(model)

    assert info.done
    assert info.error == REFUSED
    assert model.offered == [], "no call was made for a task it could not do"
    assert [a["outcome"] for a in audited] == ["refused"]
    assert audited[0]["metadata"]["reason"] == "model_cannot_use_tools"
    assert audited[0]["metadata"]["model"] == "fake-cloud:m-1"


@pytest.mark.asyncio
async def test_a_subagent_on_a_model_that_uses_tools_runs_with_them(agent_root):
    model = _Model(uses_tools=True)

    info, audited = await _run(model)

    assert info.done and info.error == "", info.error
    assert model.offered == [True], "its model was offered its tools"
    assert audited == []


@pytest.mark.asyncio
async def test_a_text_run_asked_for_no_tools_and_runs_without_them(agent_root):
    model = _Model(uses_tools=False)

    info, _audited = await _run(model, capability_class="text")

    assert info.done and info.error == "", info.error
    assert model.offered == [False]


@pytest.mark.asyncio
async def test_a_subagent_whose_model_refused_its_tools_mid_run_says_so(agent_root):
    """🔴 Before: the call that refused its tools was retried without them, answered in prose, and
    the subagent ended done."""
    model = _Model(uses_tools=True, refuses_on=1)

    info, _audited = await _run(model)

    assert model.offered == [True], "it was offered its tools, and refused them"
    assert info.done
    assert info.error == REFUSED
    from personalclaw.subagent_tier import without_tools_ending

    assert without_tools_ending(info.error)
    tombstone = json.loads((agent_root / info.id / "tombstone.json").read_text())
    assert tombstone["cause"] == "no_tools"


def test_the_ending_is_told_apart_from_a_refusal_and_from_running_out_of_room():
    from personalclaw.subagent_tier import (
        couldnt_do_it,
        out_of_room_ending,
        ran_out_of_room,
        refused_every_call,
        without_tools_ending,
    )

    refused = refused_every_call([("write_file", "it is on the deny list")])

    assert couldnt_do_it(REFUSED) and without_tools_ending(REFUSED)
    assert not out_of_room_ending(REFUSED)
    assert not without_tools_ending(refused) and not without_tools_ending(ran_out_of_room(8192))
    assert not without_tools_ending("")


# ── a workflow step, as it runs ──────────────────────────────────────────────────────────────


async def _batch(tmp_path, monkeypatch, model: _Model):
    from personalclaw.workflows import store
    from personalclaw.workflows.batch_compile import LeafTask, compile_batch
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)
    leaves = [
        LeafTask(
            task=f"fix the typo in {name}",
            objective=f"the typo in {name} is fixed",
            output_format="the file and line changed",
            boundary=f"change only {name}",
        )
        for name in ("notes.md", "guide.md")
    ]
    compiled = compile_batch(leaves)
    assert compiled.ok, compiled.findings
    spec = {"name": "subagent-batch-1", "root": compiled.spec["root"]}
    run = store.create(WorkflowRun(id="", workflow_name="subagent-batch-1"))
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(subagents=_manager(model, starts_unasked=True))
    )
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.subagent_tier.Stats"),
        patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
    ):
        status = await controller.run_to_completion(timeout=30.0)
    return status, [controller.instances[f"root.children[{i}]"] for i in range(2)]


@pytest.mark.asyncio
@pytest.mark.parametrize("refuses_on", [0, 1], ids=["from-the-start", "mid-run"])
async def test_a_step_without_tools_fails_with_a_fix_about_its_model(
    agent_root, tmp_path, monkeypatch, refuses_on
):
    """🔴 Before: the step read done on "Done, I wrote the fix.", and the run read complete. A
    step that ended "Couldn't do its task" for any reason was told to "give this step the tools its
    task needs", which it had."""
    from personalclaw.workflows.models import FailureClass, InstanceState, RunStatus

    model = _Model(uses_tools=refuses_on > 0, refuses_on=refuses_on)

    status, steps = await _batch(tmp_path, monkeypatch, model)

    for step in steps:
        assert step.state is InstanceState.FAILED, step.state
        assert step.failure.failure_class is FailureClass.USER, step.failure.to_dict()
        assert "“fake-cloud:m-1” can't use tools" in step.failure.cause_plain
        assert "for Orchestration in Settings → Models" in step.failure.remediation
        assert "give this step the tools" not in step.failure.remediation
    assert status is RunStatus.FAILED, status


@pytest.mark.asyncio
async def test_a_step_on_a_model_that_uses_tools_is_done(agent_root, tmp_path, monkeypatch):
    from personalclaw.workflows.models import InstanceState

    model = _Model(uses_tools=True)

    _status, steps = await _batch(tmp_path, monkeypatch, model)

    for step in steps:
        assert step.state is not InstanceState.FAILED, step.failure and step.failure.to_dict()
    assert model.offered and all(model.offered), "each step's model was offered its tools"


# ── a workflow run, before it starts ─────────────────────────────────────────────────────────

WITHOUT, WITH = "Pocket:tiny-1", "Workshop:able-1"

SPEC = {
    "name": "fix-it",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [
            {"kind": "infer", "id": "plan", "config": {"prompt": "plan it"}},
            {"kind": "stage", "id": "fix", "label": "Fix the file", "config": {"prompt": "fix"}},
            {"kind": "stage", "id": "own", "config": {"prompt": "check", "model": WITH}},
            {"kind": "stage", "id": "bound", "config": {"prompt": "x", "model": "{{inputs.m}}"}},
            {"kind": "stage", "id": "again", "config": {"prompt": "fix again"}},
        ],
    },
}


@pytest.mark.asyncio
async def test_each_step_with_tools_is_asked_once_per_model_and_named_in_order():
    from personalclaw.workflows.preflight import tool_less_steps

    asked: list[tuple[str, str, str]] = []

    async def probe(axis: str, agent: str, model: str) -> str:
        asked.append((axis, agent, model))
        return "" if model == WITH else WITHOUT

    found = await tool_less_steps(SPEC, probe=probe)

    assert found == [("Fix the file", WITHOUT), ("again", WITHOUT)]
    assert asked == [("orchestration", "", ""), ("orchestration", "", WITH)], (
        "an infer step has no tools, a bound model resolves only at dispatch, and each model is "
        "asked once"
    )


class _ToolModel:
    """A model provider of the registry below: it declares what its type says about tools, and is
    never called."""

    def __init__(self, uses_tools: bool) -> None:
        self.supports_tools = uses_tools

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):  # pragma: no cover - not called
        raise AssertionError("a run's start never calls a model")
        yield


def _capability(type_: str) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT, Capability.CODE_TOOLS}),
        supports_streaming=True,
        supports_tools=True,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
    )


@pytest.fixture
def orchestration(monkeypatch, tmp_path) -> dict[str, list[str]]:
    """Settings → Providers holds Pocket (its model can't use tools) and Workshop (its can), and
    the run's definition is saved; returns Settings → Models for the test to bind."""
    from personalclaw.guardrails.breaker import reset_breakers
    from personalclaw.workflows import defs as defs_mod

    registry = ProviderRegistry()
    for type_, uses in (("pocket", False), ("workshop", True)):
        registry.register_type(
            _capability(type_), lambda *, entry, session_key=None, _u=uses, **_kw: _ToolModel(_u)
        )
    registry.register_entry(ProviderEntry(name="Pocket", type="pocket", model="tiny-1"))
    registry.register_entry(ProviderEntry(name="Workshop", type="workshop", model="able-1"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    active: dict[str, list[str]] = {"chat": [WITH]}
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: active)
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: True
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)

    class _Defs(defs_mod.WorkflowDefProvider):
        def __init__(self) -> None:
            self.d: dict = {}

        @property
        def name(self) -> str:
            return "tools-mem"

        @property
        def readonly(self) -> bool:
            return False

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return list(self.d.values()), len(self.d)

        async def get_def(self, name: str):
            return self.d.get(name)

        async def save_def(self, **f):
            self.d[f["name"]] = dict(f)
            return self.d[f["name"]]

        async def delete_def(self, name: str) -> bool:
            return self.d.pop(name, None) is not None

    reset_breakers()
    defs_mod.register_provider(_Defs())
    yield active
    defs_mod.unregister_provider("tools-mem")
    reset_breakers()


class _Supervisor:
    def __init__(self) -> None:
        self.launched: list[str] = []

    def controller(self, run_id: str):
        return None

    async def launch(self, run, spec, *, depth: int = 0):
        self.launched.append(run.id)
        return object()


async def _start(sup: _Supervisor) -> dict:
    from personalclaw.workflows import service

    await service.author_def(name=SPEC["name"], root=SPEC["root"])
    return await service.start_run(name=SPEC["name"], inputs={"m": WITH}, supervisor=sup)


@pytest.mark.asyncio
async def test_a_run_whose_steps_would_run_without_tools_is_refused_naming_them(orchestration):
    """🔴 Before: the run started, and each of those steps claimed its work."""
    orchestration["orchestration"] = [WITHOUT]
    sup = _Supervisor()

    body = await _start(sup)

    assert not body["ok"] and body["code"] == "WF_RUN_PREFLIGHT_FAILED", body
    assert "step “Fix the file” works with tools" in body["message"], body
    assert "“Pocket:tiny-1” can't use tools" in body["message"]
    findings = [f for f in body["preflight"]["findings"] if f["code"] == "WF_PRE_MODEL_NO_TOOLS"]
    assert len(findings) == 2, findings
    assert {f["kind"] for f in findings} == {"tools"}
    assert "for Orchestration in Settings → Models" in findings[0]["remediation"]
    assert sup.launched == [], "no run was launched"


@pytest.mark.asyncio
async def test_a_run_whose_steps_use_tools_starts(orchestration):
    orchestration["orchestration"] = [WITH]
    sup = _Supervisor()

    body = await _start(sup)

    assert body["ok"], body
    assert len(sup.launched) == 1
