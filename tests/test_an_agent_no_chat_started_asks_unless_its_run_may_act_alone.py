"""An agent no chat started asks you before it acts, unless its own run was allowed to act alone.

Settings → Agent defaults → Approval mode shipped as ``auto``, the loosest end of its own scale.
"Auto" approves every tool call of an agent no chat started (``approval_grants.SETTING``), so by
default a trigger's Invoke Agent agent, or a subagent started outside a chat, approved every call
it made, file changes and shell commands included, while the trigger's Allow told the owner its
agent would ask before it changed a file or ran a command.

Now the shipped mode asks (``interactive``), and such an agent's call is approved without asking
only by the consent given for its own run: the step's own "Auto-approve tools" (``approval_mode:
"auto"``, saved with the owner's yes), a Run prompt action whose Allow says what its agent may do,
or the Mode of the loop that started it. Anything else asks in the Inbox, where the owner answers
it. An owner who chooses "Auto" still gets it, and the trigger's Allow and the Doctor say so.

Driven through the real ``NativeAgentRuntime`` in the real subagent manager, with a scripted model,
and asked through the gateway's own relay into a real approval registry and Inbox store.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_a_tool_call_is_audited_as_it_was_decided import ASKS, _decided, _rows, _subagents
from test_pending_approval_every_surface import world  # noqa: F401 - a fixture, used by name
from test_pending_approval_every_surface import _approval_rows

import personalclaw.config.loader as loader
from personalclaw import approval_grants
from personalclaw.approval_answer import YOU
from personalclaw.config.loader import AgentConfig, AppConfig
from personalclaw.subagent import SubagentInfo, SubagentManager

TRIGGER_ID = "clock:morning-brief"


async def _until(predicate, what: str) -> None:
    """Poll for *predicate*, failing with the cause named; bounded at 20 s for a loaded host."""
    for _ in range(2000):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never happened within 20s: {what}")


def _write_agent(**agent: Any) -> None:
    path = loader.config_dir() / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("agent", {}).update(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


# ── the shipped default ────────────────────────────────────────────────────────────────────────


def test_the_shipped_approval_mode_asks():
    """The dataclass, a first run with no file, and a file whose agent section never names the
    field all read the strictest value. The last is the loader's own default, a second copy that
    once said ``auto`` beside the dataclass."""
    assert AgentConfig().approval_mode == "interactive"
    assert AppConfig.load().agent.approval_mode == "interactive"
    _write_agent(yolo=False)
    assert AppConfig.load().agent.approval_mode == "interactive"
    assert approval_grants.setting_grant() == ""


# ── under the default, an agent no chat started asks in the Inbox ──────────────────────────────


def _relay(w):
    """The gateway's own approval relay for a subagent's call, onto the test's real registry."""
    from test_gateway import _make_orchestrator

    orch = _make_orchestrator()
    orch.dashboard_state = w.state
    return orch._interactive_approval(
        "subagent", session_resolver=lambda _rid: "", trigger_resolver=lambda _rid: TRIGGER_ID
    )


@pytest.mark.asyncio
async def test_a_triggers_agent_asks_in_the_inbox_and_runs_on_her_answer(world):  # noqa: F811
    """🔴 Before: the shipped "auto" approved the call (`decided_by: setting`) and nothing asked."""
    manager, tools = _subagents(ASKS, relay=_relay(world))
    info = SubagentInfo(id="trig01", task="write the digest", trigger_id=TRIGGER_ID)
    run = asyncio.create_task(manager._run_inner(info, "subagent:trig01"))
    try:
        await _until(
            lambda: bool(world.state._pending_approvals) or run.done(),
            "the trigger's agent never asked",
        )
        assert tools.ran == [], "the call ran before anyone answered it"
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


@pytest.mark.asyncio
async def test_a_subagent_no_session_named_asks_too_and_her_deny_stops_it(world):  # noqa: F811
    """A subagent started over the API with no chat behind it (a worker whose session its tool
    could not name) is the other agent no chat started. Her Deny in the Inbox stops the call."""
    manager, tools = _subagents(ASKS, relay=_relay(world))
    info = SubagentInfo(id="loose01", task="check the build")
    run = asyncio.create_task(manager._run_inner(info, "subagent:loose01"))
    try:
        await _until(lambda: bool(world.state._pending_approvals) or run.done(), "it never asked")
        [approval_id] = list(world.state._pending_approvals)
        assert world.state.resolve_approval(approval_id, False, by=YOU) is True
        await asyncio.wait_for(run, timeout=20)
    finally:
        if not run.done():
            run.cancel()
    assert tools.ran == []
    assert [_decided(r) for r in _rows(ASKS)] == [("rejected", "you")], _rows(ASKS)


# ── a run that was allowed to act alone still does ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_mode", "capability"),
    [
        # Invoke Agent with "Auto-approve tools", saved with the owner's yes, and write access.
        ("auto", "mutating"),
        # Run prompt: its agent always runs unasked, read-only unless its Allow grants more.
        ("auto", "research"),
    ],
)
async def test_an_automation_allowed_to_act_alone_still_runs_without_asking(
    approval_mode, capability
):
    asked = AsyncMock()
    manager, tools = _subagents(ASKS, relay=asked)
    info = SubagentInfo(
        id="auto01",
        task="t",
        trigger_id=TRIGGER_ID,
        approval_mode=approval_mode,
        capability_class=capability,
    )
    await asyncio.wait_for(manager._run_inner(info, "subagent:auto01"), timeout=20)
    asked.assert_not_awaited()
    decided = [_decided(r) for r in _rows(ASKS)]
    if capability == "mutating":
        assert tools.ran == [ASKS]
        assert decided == [("auto_approved", approval_grants.APPROVAL_MODE)], decided
    else:
        # Read-only: a call that changes something is refused, not asked about.
        assert tools.ran == []
        assert decided and decided[0][0] == "denied", decided


def _manager_for(policy_of: dict[str, str]) -> SubagentManager:
    from test_subagent import _mock_ctx_builder, _mock_sessions

    sessions = _mock_sessions()
    sessions.get_approval_policy = MagicMock(side_effect=lambda key: policy_of.get(key, ""))
    ctx = _mock_ctx_builder()
    ctx.hooks.auto_approve_subagent_tools = False
    return SubagentManager(sessions=sessions, ctx_builder=ctx, is_yolo=lambda: False)


def test_a_loops_child_runs_on_its_loops_mode():
    """A subagent a loop's worker starts takes the loop's Mode, which its owner chose for that
    loop: an Unattended worker's standing grant covers it, an Attended worker's asks."""
    manager = _manager_for({"dashboard:loop-unatt": "auto", "dashboard:loop-att": ""})
    child_of = lambda key: SubagentInfo(id=key[-4:], task="t", parent_session_key=key)  # noqa: E731
    assert manager._grant_now(child_of("dashboard:loop-unatt"), audit=False) == (
        approval_grants.PARENT_TRUST
    )
    assert manager._grant_now(child_of("dashboard:loop-att"), audit=False) == ""


# ── the owner's own "Auto" ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_owners_auto_still_approves_an_agent_no_chat_started():
    """The control: a stored "Auto" is the owner's setting and keeps doing what it says."""
    _write_agent(approval_mode="auto")
    asked = AsyncMock()
    manager, tools = _subagents(ASKS, relay=asked)
    info = SubagentInfo(id="own01", task="t", trigger_id=TRIGGER_ID)
    await asyncio.wait_for(manager._run_inner(info, "subagent:own01"), timeout=20)
    asked.assert_not_awaited()
    assert tools.ran == [ASKS]
    assert [_decided(r) for r in _rows(ASKS)] == [("auto_approved", approval_grants.SETTING)]


def _invoke_agent_trigger() -> Any:
    from personalclaw.triggers.models import Trigger

    return Trigger(
        id=TRIGGER_ID,
        name="Morning brief",
        kind="clock",
        spec={"kind": "cron", "expr": "15 7 * * 1-5"},
        workflow={
            "inline": {
                "provider": "invoke-agent",
                "config": {"task_template": "Summarise my inbox."},
            }
        },
    )


def test_the_allow_says_what_its_agent_does_under_each_mode():
    """The Allow is what the owner agrees to, so it reads the run's real grant: under the default
    its agent asks; under her "Auto" it acts without asking, and the sentence says why."""
    from personalclaw.triggers import grants

    trigger = _invoke_agent_trigger()
    assert grants.what_its_agent_may_do(trigger) == (
        "Its agent asks you before it changes a file, runs a command or sends a message."
    )
    _write_agent(approval_mode="auto")
    assert grants.what_its_agent_may_do(trigger) == (
        "Its agent may change files, run commands and send messages without asking you, "
        "because Settings → Agent defaults → Approval mode is Auto."
    )


@pytest.mark.asyncio
async def test_the_doctor_names_a_stored_auto_as_a_loosened_control():
    from personalclaw.resilience.doctor import DoctorContext, all_probes

    [probe] = [p for p in all_probes() if p.id == "security.approval_mode"]
    asks = await probe.run(DoctorContext())
    assert asks.ok, asks
    assert "asks you" in asks.detail

    _write_agent(approval_mode="auto")
    loose = await probe.run(DoctorContext())
    assert not loose.ok
    assert "approves every tool call it makes" in loose.detail
    assert "Settings → Agent defaults → Approval mode" in loose.remedy
