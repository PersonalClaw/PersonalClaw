"""One Allow covers what it said it allows, a launched run's row says how it went, and the owner's
own Deny never reads as a failure.

Before, with a trigger whose action is Invoke Agent:

* **Create asked, and then its run asked again.** Create's dialog said "Creating “Morning brief”
  allows it to use the “Invoke Agent” action when it runs", the owner allowed it, and its Run now
  waited on a second approval, "Approval needed: subagent_run(…)", before its agent would start.
  The spawn gate read its own grants and knew nothing of the trigger's.
* **The run's row said "launched" for good.** The agent ran and ended; the trigger's history
  still had one row, `launched`, "spawned agent for: …", beside a note saying it had finished.
* **The owner's own Deny was a failure.** Denying that second approval sent "Morning brief
  failed" with "spawn declined, so it never started".

Now the agent starts on the Allow its trigger was given (`triggers.grants.allows_its_agent`), for
the start only; the row names the agent it started and says how it went when it ends
(`triggers.settle`); and a start the owner declined is `declined`, with no note about it.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from test_an_automation_says_it_finished_when_it_has import _agent, _notes, _on_done

from personalclaw import approval_grants
from personalclaw.guardrails import ceiling as C
from personalclaw.hooks import ScriptHook, ScriptHookStore
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.sel import sel
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.triggers.history import schedule_run_to_record
from personalclaw.triggers.models import Outcome, Trigger
from personalclaw.triggers.store import TriggerStore

TRIGGER_ID = "clock:morning-brief"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _trigger(home, *, provider: str = "invoke-agent", granted: bool = True) -> Trigger:
    config = (
        {"task_template": "Summarise my inbox."}
        if provider == "invoke-agent"
        else {"command": "echo hello"}
    )
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Morning brief",
        kind="clock",
        spec={"kind": "cron", "expr": "15 7 * * 1-5"},
        capabilities={"providers": [provider]} if granted else {},
        workflow={"inline": {"provider": provider, "config": config}},
        delivery="inbox",
    )
    TriggerStore(base_dir=home).upsert(trigger)
    return trigger


# ── one Allow starts the agent ───────────────────────────────────────────────────────────────


def _manager(asked: AsyncMock) -> SubagentManager:
    from test_subagent import _mock_ctx_builder, _mock_sessions

    sessions = _mock_sessions()
    sessions.get_approval_policy = lambda _key: ""
    return SubagentManager(
        sessions=sessions,
        ctx_builder=_mock_ctx_builder(),
        on_spawn_approval=asked,
        is_yolo=lambda: False,
    )


async def _start(manager: SubagentManager, trigger_id: str) -> SubagentInfo:
    with patch("personalclaw.subagent.SubagentManager._run", new=AsyncMock()):
        info = manager.spawn("Summarise my inbox.", trigger_id=trigger_id)
        assert info is not None and not info.done, info
        await asyncio.wait_for(manager._tasks[info.id], timeout=10)
    return info


def _spawn_rows(agent_id: str) -> list[dict[str, Any]]:
    return [
        r
        for r in sel().recent(500)
        if r.get("event_type") == "tool_invocation"
        and r.get("operation") == "subagent_run"
        and (r.get("metadata") or {}).get("subagent_id") == agent_id
    ]


@pytest.mark.asyncio
async def test_the_allow_its_trigger_was_given_starts_its_agent(home, monkeypatch):
    """🔴 Before: the spawn asked again — `asked` was awaited once, for the start Create allowed."""
    # Settings → Agent defaults → Approval mode "interactive": nothing else approves its calls.
    monkeypatch.setattr(approval_grants, "approval_mode_now", lambda: "interactive")
    _trigger(home)
    asked = AsyncMock(return_value=True)
    manager = _manager(asked)
    info = await _start(manager, TRIGGER_ID)
    asked.assert_not_awaited()
    started = [r for r in _spawn_rows(info.id) if r.get("outcome") == "auto_approved_spawn"]
    assert started and started[-1]["metadata"]["decided_by"] == approval_grants.TRIGGER
    # The start only: what the agent then does asks as any agent's calls do.
    assert manager._standing_grant(info) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "setup",
    ["not_granted", "not_an_agent", "unknown"],
)
async def test_a_start_no_allow_covers_still_asks(home, setup):
    """The control: a trigger not allowed to run its action, one whose allowed action starts no
    agent, and a trigger that does not exist each leave the start to ask."""
    if setup == "not_granted":
        _trigger(home, granted=False)
    elif setup == "not_an_agent":
        _trigger(home, provider="bash")
    asked = AsyncMock(return_value=True)
    await _start(_manager(asked), TRIGGER_ID)
    asked.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_lifecycle_hooks_allow_starts_its_agent(home):
    hook = ScriptHook(
        id="review",
        name="Review on stop",
        event="Stop",
        provider="invoke-agent",
        provider_config={"task_template": "Review the change."},
        capabilities={"providers": ["invoke-agent"]},
    )
    ScriptHookStore(config_dir=home).create(hook.to_dict())
    asked = AsyncMock(return_value=True)
    await _start(_manager(asked), "lifecycle:review")
    asked.assert_not_awaited()


@pytest.fixture
def ceiling(tmp_path, monkeypatch):
    def install(value: str) -> None:
        path = tmp_path / "operator" / "ceiling.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": 1, "scopes": {"approval": {"value": "%s"}}}' % value)
        monkeypatch.setenv(C.CEILING_PATH_ENV, str(path))
        C.reset_ceiling()

    C.reset_ceiling()
    yield install
    C.reset_ceiling()


@pytest.mark.asyncio
async def test_an_ask_ceiling_still_asks(home, ceiling):
    """The operator's `approval: ask` bounds this grant like every other."""
    _trigger(home)
    ceiling("ask")
    asked = AsyncMock(return_value=True)
    await _start(_manager(asked), TRIGGER_ID)
    asked.assert_awaited_once()
    refused = [
        r
        for r in sel().recent(500)
        if r.get("operation") == "approval.grant_refused"
        and f"grant={approval_grants.TRIGGER}," in str(r.get("resources", ""))
    ]
    assert refused, "the refused grant is audited"


# ── the owner's Deny is a decline ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_owners_deny_is_recorded_as_declined_and_nobody_else_s_is():
    from test_subagent import _mock_ctx_builder, _mock_sessions

    async def ended(answer: Any) -> SubagentInfo:
        manager = SubagentManager(
            sessions=_mock_sessions(),
            ctx_builder=_mock_ctx_builder(),
            on_spawn_approval=AsyncMock(return_value=answer),
        )
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn("Summarise my inbox.")
            assert info is not None
            await manager._tasks[info.id]
        return info

    assert (await ended(False)).declined is True
    expired = approval_grants.ToolDecision(False, "expired", approval_grants.NOBODY)
    assert (await ended(expired)).declined is False, "nobody answering is not the owner's decision"


# ── the row says how it went ─────────────────────────────────────────────────────────────────


def agent_work_id(agent_id: str) -> str:
    from personalclaw.subagent import agent_work_id as named

    return named(agent_id)


def settle_agent_run(info: SubagentInfo, **kw: Any) -> bool:
    from personalclaw.triggers.settle import settle_agent_run as settle

    return settle(info, **kw)


def _launched(home, agent: str = "a1b2c3d4") -> ScheduleRun:
    run = ScheduleRun(
        run_id="manual-1",
        job_id=TRIGGER_ID,
        trigger="manual",
        started_at=1000.0,
        finished_at=1000.5,
        status="launched",
        summary="spawned agent for: Summarise my inbox.",
        work_id=agent_work_id(agent),
    )
    ScheduleRunStore(home).append_sync(run)
    return run


def _row(home) -> dict[str, Any]:
    stored = ScheduleRunStore(home)._get_run_sync(TRIGGER_ID, "manual-1")
    assert stored is not None
    return stored


def test_the_row_says_how_the_agent_it_started_went(home):
    """🔴 Before: `ScheduleRunStore` had no way to rewrite a row, and the row stayed `launched`."""
    _launched(home)
    store = ScheduleRunStore(home)
    assert store.settle_sync(
        TRIGGER_ID,
        agent_work_id("a1b2c3d4"),
        status="success",
        summary="Two invoices are due.",
        finished_at=1060.0,
    )
    row = _row(home)
    assert (row["status"], row["summary"], row["trace"]) == (
        "success",
        "Two invoices are due.",
        "Two invoices are due.",
    )
    assert row["finished_at"] == 1060.0 and row["duration_ms"] == 60_000
    (indexed,) = store._list_all_sync(0, 10, TRIGGER_ID)[0]
    assert indexed["status"] == "success" and "trace" not in indexed
    # Settled once: an ending that arrives again, for a row already settled, changes nothing.
    assert not store.settle_sync(TRIGGER_ID, agent_work_id("a1b2c3d4"), status="failure")
    assert _row(home)["status"] == "success"


def test_an_agent_that_ends_before_its_row_is_written_still_says_how(home):
    """The fire records its row after its action returns, and an agent refused at once can end
    first: the row is then written as it ended."""
    store = ScheduleRunStore(home)
    assert store.settle_sync(
        TRIGGER_ID,
        agent_work_id("a1b2c3d4"),
        status="failure",
        summary="the model answered 503",
        error="the model answered 503",
        finished_at=1002.0,
    )
    _launched(home)
    row = _row(home)
    assert (row["status"], row["error"]) == ("failure", "the model answered 503")
    assert row["duration_ms"] == 2000


def test_settling_an_agent_run_stamps_its_trigger(home):
    _trigger(home)
    _launched(home)
    info = SubagentInfo(
        id="a1b2c3d4", task="t", done=True, error="Reaped after 1843s", trigger_id=TRIGGER_ID
    )
    assert settle_agent_run(info, base_dir=home)
    assert _row(home)["status"] == "failure"
    live = TriggerStore(base_dir=home).get(TRIGGER_ID).trigger
    assert live.last_failure_at and live.last_error_summary == "Reaped after 1843s"


def test_a_declined_run_reads_as_the_owners_decision_not_a_failure(home):
    _trigger(home)
    _launched(home)
    info = SubagentInfo(
        id="a1b2c3d4",
        task="t",
        done=True,
        error="spawn declined, so it never started",
        trigger_id=TRIGGER_ID,
        declined=True,
    )
    assert settle_agent_run(info, base_dir=home)
    row = _row(home)
    from personalclaw.triggers.settle import DECLINED_LINE

    assert (row["status"], row["summary"], row["error"]) == ("declined", DECLINED_LINE, "")
    record = schedule_run_to_record(row, trigger_id=TRIGGER_ID)
    assert record.outcome == Outcome.SKIPPED_GATE.value and record.outcome != Outcome.FAILED.value
    assert not TriggerStore(base_dir=home).get(TRIGGER_ID).trigger.last_failure_at


# ── through the gateway's own completion callback ────────────────────────────────────────────


def _store_trigger_with_row(home) -> None:
    _trigger(home)
    _launched(home)


@pytest.mark.asyncio
async def test_the_agent_ending_settles_its_row(home):
    """🔴 Before: the note went out and the row stayed `launched`."""
    _store_trigger_with_row(home)
    orch, on_done = _on_done()
    agent = _agent(trigger_id=TRIGGER_ID)
    await on_done([agent])
    assert _row(home)["status"] == "success"
    assert [title for _k, title, _b in _notes(orch)] == ["Morning brief finished"]


@pytest.mark.asyncio
async def test_the_owners_deny_sends_no_note_and_the_row_says_declined(home):
    """🔴 Before: "Morning brief failed" / "spawn declined, so it never started"."""
    _store_trigger_with_row(home)
    orch, on_done = _on_done()
    agent = _agent(trigger_id=TRIGGER_ID, error="spawn declined, so it never started")
    agent.declined = True
    await on_done([agent])
    assert _notes(orch) == []
    assert _row(home)["status"] == "declined"
