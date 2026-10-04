"""Starting an agent without asking is not approving what it then does.

The hook setting that starts subagents without asking you (``hooks.auto_approve_subagent_spawn``)
decides a start, and the subagent manager reads it for nothing else: a start it approves is
recorded as one whose agent's calls are still asked about. An Invoke Agent step read it as its own
"Auto-approve tools" too. With the setting on, the agent such a step started approved every call
it made, its writes included, while the step's own approval was not set and its Allow said "Its
agent asks you before it changes a file, runs a command or sends a message", and the security log
named the step's own approval mode as what decided each call. A step with no capability set was
held to reading instead, where the step says it may change things, each change asking first.

Now the setting starts the agent and nothing more. Its calls are decided as any subagent's are:
they ask you, unless the step itself approves them (``approval_mode: "auto"``, saved with your
yes), your Approval mode "Auto" does, or the hook setting that approves subagents' tool calls does
(``hooks.auto_approve_subagent_tools``), and the Allow names the one that will.

Driven through the Invoke Agent action's own fire into the real subagent manager and the real
native runtime with a scripted model, asked through the gateway's own relay into a real approval
registry and Inbox, and read back from the real audit log.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_a_tool_call_is_audited_as_it_was_decided import ASKS, _calls, _decided, _rows, _Tools
from test_native_runtime import _defn
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_pending_approval_every_surface import _approval_rows

import personalclaw.config.loader as loader
from personalclaw import approval_grants
from personalclaw.action_providers import invoke_agent_provider
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.approval_answer import YOU
from personalclaw.config import AppConfig
from personalclaw.hooks import live_hook_manager
from personalclaw.session import SessionManager
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger

TASK = "Write this week's report into weekly-report.md."
#: A workflow's step: no trigger's Allow starts its agent, so the start is the setting's to decide.
STEP = ActionContext(event="workflow_node", payload={"run_id": "run-1", "node_id": "report"})

ASKS_FIRST = "Its agent asks you before it changes a file, runs a command or sends a message."


def _hooks(**hooks: bool) -> None:
    """The hook settings in the test's config file, as the owner writes them there."""
    path = loader.config_dir() / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data["hooks"] = {**(data.get("hooks") or {}), **hooks}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


async def _until(predicate, what: str) -> None:
    """Poll for *predicate*, failing with the cause named; bounded at 20 s for a loaded host."""
    for _ in range(2000):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never happened within 20s: {what}")


def _relay(w: Any) -> Any:
    """The gateway's own approval relay for a subagent's call, onto the test's real registry."""
    from test_gateway import _make_orchestrator

    orch = _make_orchestrator()
    orch.dashboard_state = w.state
    return orch._interactive_approval(
        "subagent", session_resolver=lambda _rid: "", trigger_resolver=lambda _rid: ""
    )


def _manager(relay: Any) -> tuple[SubagentManager, _Tools, AsyncMock]:
    """The subagent manager as the gateway builds it: the hook settings read at each decision, an
    agent's calls asked through *relay*, a start asked through its own callback (returned, so a
    test can say nothing asked). Each agent is a real native runtime making one call that asks."""
    tools = _Tools()

    def factory(_key: Any = None, **kw: Any) -> NativeAgentRuntime:
        return NativeAgentRuntime(
            definition=_defn(),
            model_provider=_calls(ASKS),
            tool_providers=[tools],
            unattended=bool(kw.get("unattended")),
        )

    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("built_message", None))
    ctx.hooks = live_hook_manager()
    asked_to_start = AsyncMock(return_value=False)
    manager = SubagentManager(
        sessions=SessionManager(AppConfig(), provider_factory=factory),
        ctx_builder=ctx,
        on_tool_approval=relay,
        on_spawn_approval=asked_to_start,
        is_yolo=lambda: False,
    )
    return manager, tools, asked_to_start


async def _fire(
    manager: SubagentManager, config: dict, monkeypatch, ctx: ActionContext = STEP
) -> tuple[SubagentInfo, asyncio.Task]:
    """Fire an Invoke Agent step with *config*, as its action does: the agent it started, and
    the run its start scheduled."""
    monkeypatch.setattr(
        invoke_agent_provider, "get_action_services", lambda: SimpleNamespace(subagents=manager)
    )
    result = await InvokeAgentActionProvider().execute(config, ctx)
    assert result.success, result.error
    [info] = list(manager._agents.values())
    [run] = list(manager._tasks.values())
    return info, run


def _started_by() -> list[str]:
    """Who started each agent without asking, as its start's audit row says."""
    return [
        decided_by
        for outcome, decided_by in (_decided(r) for r in _rows("subagent_run"))
        if outcome == "auto_approved_spawn"
    ]


def _step(**config: str) -> dict:
    return {"task_template": TASK, **config}


def _trigger(config: dict) -> Trigger:
    return Trigger(
        id="clock:weekly-report",
        name="Weekly report",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * 1"},
        workflow={"inline": {"provider": "invoke-agent", "config": config}},
    )


# ── the setting starts the agent, and its calls ask ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("capability", ["", "mutating"])
async def test_an_agent_the_setting_started_asks_before_its_call_runs(
    world, monkeypatch, capability  # noqa: F811
):
    """🔴 Before: with the setting on, the call ran unasked (``auto_approved`` by
    ``approval_mode``, which the step never carried), or, with no capability set, was refused as
    a read-only run's, where the step asks before each change."""
    _hooks(auto_approve_subagent_spawn=True)
    manager, tools, asked_to_start = _manager(_relay(world))
    step = _step(capability=capability) if capability else _step()
    info, run = await _fire(manager, step, monkeypatch)
    try:
        await _until(
            lambda: bool(world.state._pending_approvals) or run.done(), "its call never asked"
        )
        assert tools.ran == [], "the call ran before anyone answered it"
        decided = [_decided(r) for r in _rows(ASKS)]
        assert world.state._pending_approvals, f"its call was never asked about: {decided}"
        [approval_id] = list(world.state._pending_approvals)
        [row] = _approval_rows(world.store, open_only=True)
        assert row.refs["approval"] == approval_id
        assert world.state.resolve_approval(approval_id, True, by=YOU) is True
        await asyncio.wait_for(run, timeout=20)
    finally:
        if not run.done():
            run.cancel()
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("approved", "you")], _rows(ASKS)
    # The start was the setting's: nobody was asked, and its row says the setting started it.
    asked_to_start.assert_not_awaited()
    assert info.approval_mode == ""
    assert _started_by() == [approval_grants.HOOK_SETTING]


@pytest.mark.asyncio
async def test_the_setting_still_starts_the_agent_without_asking(monkeypatch):
    """The control: the setting does what it says. A workflow step's agent starts on it with
    nobody asked, and with the setting off the same start asks."""
    _hooks(auto_approve_subagent_spawn=True)
    manager, _tools, asked_to_start = _manager(AsyncMock(return_value=False))
    info, run = await _fire(manager, _step(capability="mutating"), monkeypatch)
    await asyncio.wait_for(run, timeout=20)
    asked_to_start.assert_not_awaited()
    assert info.done and not info.error, info.error
    assert len(_started_by()) == 1

    _hooks(auto_approve_subagent_spawn=False)
    manager, _tools, asked_to_start = _manager(AsyncMock(return_value=False))
    info, run = await _fire(manager, _step(capability="mutating"), monkeypatch)
    await asyncio.wait_for(run, timeout=20)
    asked_to_start.assert_awaited_once()
    assert info.error, "a start nobody allowed ran"
    assert len(_started_by()) == 1, "a start the setting did not cover started unasked"


@pytest.mark.asyncio
async def test_a_step_that_approves_its_own_calls_still_does(monkeypatch):
    """The other control: the step's own "Auto-approve tools", saved with the owner's yes, still
    lets its agent act without asking, the setting on or off."""
    _hooks(auto_approve_subagent_spawn=True)
    asked = AsyncMock(return_value=False)
    manager, tools, _asked_to_start = _manager(asked)
    info, run = await _fire(
        manager, _step(approval_mode="auto", capability="mutating"), monkeypatch
    )
    await asyncio.wait_for(run, timeout=20)
    asked.assert_not_awaited()
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [
        ("auto_approved", approval_grants.APPROVAL_MODE)
    ], _rows(ASKS)
    assert info.approval_mode == "auto"


# ── what approves its calls is what approves any subagent's ────────────────────────────────────


@pytest.mark.asyncio
async def test_the_setting_for_subagents_calls_approves_them_and_says_so(
    world, monkeypatch  # noqa: F811
):
    """With the hook setting that approves subagents' tool calls on, the call runs unasked as any
    subagent's does, its row names that setting, and the Allow says why its agent will not ask.
    🔴 Before: the row named the step's own approval mode, and with the start setting off the
    Allow said its agent would ask."""
    _hooks(auto_approve_subagent_spawn=True, auto_approve_subagent_tools=True)
    manager, tools, _asked_to_start = _manager(_relay(world))
    _info, run = await _fire(manager, _step(capability="mutating"), monkeypatch)
    await asyncio.wait_for(run, timeout=20)
    assert tools.ran == [ASKS]
    assert not world.state._pending_approvals
    assert [_decided(r) for r in _rows(ASKS)] == [
        ("auto_approved", approval_grants.HOOK_SETTING)
    ], _rows(ASKS)

    said = (
        "Its agent may change files, run commands and send messages without asking you, because "
        "the hook settings approve every subagent's tool calls (hooks.auto_approve_subagent_tools)."
    )
    trigger = _trigger(_step(capability="mutating"))
    assert grants.what_its_agent_may_do(trigger) == said
    _hooks(auto_approve_subagent_spawn=False)
    assert grants.what_its_agent_may_do(trigger) == said, "the start setting decided its calls"


# ── what its Allow says ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("capability", ["", "mutating"])
def test_its_allow_says_its_agent_asks_with_the_setting_on(capability):
    """🔴 Before: with the setting on, the Allow said its agent changes files and runs commands
    without asking you, or, with no capability set, that it only reads."""
    _hooks(auto_approve_subagent_spawn=True)
    step = _step(capability=capability) if capability else _step()
    assert grants.what_its_agent_may_do(_trigger(step)) == ASKS_FIRST


def test_its_run_is_built_as_its_allow_says_with_the_setting_on():
    """The run is built from the same policy the Allow is said from: no approval of its own, and
    the class a watched run gets (it may change things, each change asking first)."""
    from personalclaw.automation_posture import agent_run_policy
    from personalclaw.subagent import CAPABILITY_MUTATING

    _hooks(auto_approve_subagent_spawn=True)
    policy = agent_run_policy("invoke-agent", _step())
    assert policy.approval_mode == ""
    assert policy.capability_class == CAPABILITY_MUTATING
    assert policy.asks
