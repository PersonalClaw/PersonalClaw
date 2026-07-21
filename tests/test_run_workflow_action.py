"""The ``run-workflow`` action (T2) — run a saved Workflow on a trigger's cadence.

Covers: missing workflow_id (error), unknown workflow (error), no-steps (error),
the success path (resolve → render expanded steps as an active instruction →
frame → fire-and-forget auto-approved spawn), cwd/session threading, and
services-unavailable. The Workflow resolution + step render are stubbed so the
test stays unit-scoped.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider


def _ctx() -> ActionContext:
    return ActionContext(event="Schedule", context="", payload={"session_key": "cron:w"})


def _services(spawn_sink, scheduled):
    fake_sub = SimpleNamespace(spawn=lambda **kw: spawn_sink.update(kw))

    def _bg(coro):
        scheduled.append(coro)
        return asyncio.ensure_future(coro)

    return SimpleNamespace(subagents=fake_sub, spawn_background=_bg)


def _wf(steps=("step one",)):
    return SimpleNamespace(
        id="wf1",
        name="release",
        description="ship it",
        steps=[SimpleNamespace(title=s) for s in steps],
    )


def test_missing_workflow_id_is_error():
    res = asyncio.run(RunWorkflowActionProvider().execute({}, _ctx()))
    assert res.success is False and "workflow_id" in res.error


def test_unknown_workflow_is_error(monkeypatch):
    import personalclaw.action_providers.run_workflow_provider as mod

    async def _none(_id):
        return None

    monkeypatch.setattr(mod, "resolve_workflow", _none)
    res = asyncio.run(RunWorkflowActionProvider().execute({"workflow_id": "ghost"}, _ctx()))
    assert res.success is False and "ghost" in res.error


def test_no_steps_is_error(monkeypatch):
    import personalclaw.action_providers.run_workflow_provider as mod

    async def _empty(_id):
        return _wf(steps=())

    monkeypatch.setattr(mod, "resolve_workflow", _empty)
    res = asyncio.run(RunWorkflowActionProvider().execute({"workflow_id": "wf1"}, _ctx()))
    assert res.success is False and "no steps" in res.error


def test_success_invokes_with_framing(monkeypatch):
    import personalclaw.action_providers.run_workflow_provider as mod

    async def _resolve(_id):
        return _wf()

    async def _render(wf):
        return f"Run the workflow **{wf.name}**: do the thing"

    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "resolve_workflow", _resolve)
    monkeypatch.setattr(mod, "render_workflow_instruction", _render)
    monkeypatch.setattr(mod, "validate_spawn_cwd", lambda cwd: "")  # cwd allowed
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))

    async def go():
        res = await RunWorkflowActionProvider().execute(
            {"workflow_id": "release", "cwd": "/repo", "agent": "PersonalClaw"}, _ctx()
        )
        await asyncio.sleep(0.05)
        return res

    res = asyncio.run(go())
    assert res.success is True and "release" in res.stdout
    assert scheduled, "the workflow turn must run as a fire-and-forget background spawn"
    task = spawn_sink.get("task", "")
    # Invoke-semantics: the workflow is the active instruction, autonomously framed.
    assert "Run the workflow" in task and "AUTONOMOUS RUN" in task
    assert spawn_sink.get("cwd") == "/repo"
    assert spawn_sink.get("agent") == "PersonalClaw"
    assert spawn_sink.get("approval_mode") == "auto"


def test_session_opt_in_pins_parent(monkeypatch):
    import personalclaw.action_providers.run_workflow_provider as mod

    async def _resolve(_id):
        return _wf()

    async def _render(wf):
        return "do it"

    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "resolve_workflow", _resolve)
    monkeypatch.setattr(mod, "render_workflow_instruction", _render)
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))

    async def go():
        await RunWorkflowActionProvider().execute(
            {"workflow_id": "release", "session": "cron:pinned"}, _ctx()
        )
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert spawn_sink.get("parent_session_key") == "cron:pinned"


def test_render_workflow_instruction_expands_steps(monkeypatch):
    """The real renderer flattens ref-steps via composition expansion and frames
    the workflow as an active instruction (invoke-semantics, not 'preferred')."""
    import personalclaw.action_providers.run_workflow_provider as mod
    from personalclaw.workflows.models import Workflow, WorkflowStep

    child = Workflow(id="c", name="child", steps=[WorkflowStep(id="s1", title="sub task")])
    parent = Workflow(
        id="p",
        name="release",
        description="ship it",
        steps=[WorkflowStep(id="s1", title="build"), WorkflowStep(id="s2", ref="c")],
    )

    async def _list(*a, **k):
        return ([parent, child], 2)

    import personalclaw.workflows.registry as reg

    monkeypatch.setattr(reg, "list_all_workflows", _list)

    out = asyncio.run(mod.render_workflow_instruction(parent))
    assert "Run the workflow **release**" in out
    assert "1. build" in out
    # Ref step flattened in, tagged with its source sub-workflow.
    assert "sub task" in out and "from: child" in out


def test_invalid_cwd_is_honest_error(monkeypatch):
    import personalclaw.action_providers.run_workflow_provider as mod

    async def _resolve(_id):
        return _wf()

    async def _render(wf):
        return "do it"

    spawn_sink: dict = {}
    scheduled: list = []
    monkeypatch.setattr(mod, "resolve_workflow", _resolve)
    monkeypatch.setattr(mod, "render_workflow_instruction", _render)
    monkeypatch.setattr(
        mod, "validate_spawn_cwd", lambda cwd: "cwd is not under any allowed root: ['~/ok']"
    )
    monkeypatch.setattr(mod, "get_action_services", lambda: _services(spawn_sink, scheduled))
    res = asyncio.run(
        RunWorkflowActionProvider().execute({"workflow_id": "release", "cwd": "/bad"}, _ctx())
    )
    assert res.success is False and "allowed root" in res.error
    assert not scheduled


def test_services_unavailable_is_error(monkeypatch):
    import personalclaw.action_providers.run_workflow_provider as mod

    async def _resolve(_id):
        return _wf()

    async def _render(wf):
        return "do it"

    monkeypatch.setattr(mod, "resolve_workflow", _resolve)
    monkeypatch.setattr(mod, "render_workflow_instruction", _render)
    monkeypatch.setattr(
        mod,
        "get_action_services",
        lambda: SimpleNamespace(subagents=None, spawn_background=lambda c: None),
    )
    res = asyncio.run(RunWorkflowActionProvider().execute({"workflow_id": "release"}, _ctx()))
    assert res.success is False and "subagent manager unavailable" in res.error
