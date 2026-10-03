"""An automation says it finished when its work has, not when the work starts.

A trigger whose action starts an agent task or a workflow run gets back "launched" (or "queued")
the moment the work starts. The fire reported that as "<name> finished", with nothing in it, and
then said nothing when the work ended: the owner heard "finished" before anything had happened and
never heard what came of it.

Now the fire says nothing while the work it started is still going. The agent task, or the run,
says how it went on the trigger's route when it ends: "<name> finished" and what it produced, or
"<name> failed" and why. A spawn refused on the spot is the fire's own failure
(`test_run_prompt_action`, `test_invoke_agent_hook`), since a launch that never happened has
nothing to report later.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_gateway import _make_orchestrator, _mock_dashboard_state, _mock_sessions

import personalclaw.action_providers as AP
from personalclaw import notification_kinds
from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.gateway import GatewayOrchestrator
from personalclaw.subagent import SubagentInfo
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import run_finish, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

TRIGGER_ID = "clock:morning-inbox"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


def _store_trigger(home, *, delivery: str = "inbox") -> Trigger:
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Morning inbox",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 60},
        capabilities={"providers": ["run-prompt"]},
        workflow={"inline": {"provider": "run-prompt", "config": {"message": "Check my inbox."}}},
        delivery=delivery,
    )
    TriggerStore(base_dir=home).upsert(trigger)
    return trigger


# ── the fire ──────────────────────────────────────────────────────────────────────────────────


class _Starts(ActionProvider):
    """An action that reports what it did, as run-prompt, invoke-agent and run-workflow do."""

    result = ActionResult(success=True)

    @property
    def name(self) -> str:
        return "starts"

    @property
    def display_name(self) -> str:
        return "Starts"

    async def execute(self, action_config, ctx: ActionContext, timeout: int = 30) -> ActionResult:
        return type(self).result


def _fire(home, monkeypatch, result: ActionResult) -> list[tuple[bool, str]]:
    """Fire the stored trigger through the real dispatch; return what it delivered."""
    _store_trigger(home)
    monkeypatch.setattr(_Starts, "result", result)
    monkeypatch.setattr(AP, "get_action_provider", lambda name: _Starts())
    orch = object.__new__(GatewayOrchestrator)
    delivered: list[tuple[bool, str]] = []

    def deliver(state: Any, trigger: Any, *, ok: bool, error: str = "", summary: str = "") -> bool:
        delivered.append((ok, error or summary))
        return True

    monkeypatch.setattr("personalclaw.triggers.delivery.report_run", deliver)
    trigger = TriggerStore(base_dir=home).get(TRIGGER_ID).trigger
    asyncio.run(orch._fire_store_trigger(trigger, {"trigger_id": TRIGGER_ID}))
    return delivered


@pytest.mark.parametrize("outcome", ["launched", "queued"])
def test_a_fire_that_only_started_its_work_says_nothing_yet(home, monkeypatch, outcome):
    """🔴 Before: `[(True, "")]`, which the route read as "Morning inbox finished", sent the
    moment the agent started."""
    result = ActionResult(success=True, stdout="launched the automation's message", outcome=outcome)
    assert _fire(home, monkeypatch, result) == []


def test_a_fire_whose_work_is_done_still_says_so(home, monkeypatch):
    """And says what it did: the output its history row shows."""
    assert _fire(home, monkeypatch, ActionResult(success=True, stdout="sent")) == [(True, "sent")]


def test_a_fire_that_could_not_start_its_work_says_why(home, monkeypatch):
    refused = "run-prompt: spawn refused: incident mode active"
    result = ActionResult(success=False, error=refused)
    assert _fire(home, monkeypatch, result) == [(False, refused)]


# ── the agent task it started, when it ends ───────────────────────────────────────────────────


def _on_done() -> tuple[GatewayOrchestrator, Any]:
    """The gateway's real subagent completion callback, over a mock dashboard state."""
    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.hooks = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.dashboard_state = _mock_dashboard_state()
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(running=[], get=MagicMock(return_value=None))
        orch._init_subagents()
    return orch, manager.call_args[1]["on_done"]


def _agent(*, parent: str = "", error: str = "", trigger_id: str = TRIGGER_ID) -> SubagentInfo:
    return SubagentInfo(
        id="a1b2c3d4",
        task="Check the inbox and tell me what matters.",
        done=True,
        result="" if error else "Two new invoices; one is overdue.",
        error=error,
        parent_session_key=parent,
        trigger_id=trigger_id,
    )


def _notes(orch: GatewayOrchestrator) -> list[tuple[str, str, str]]:
    """Every note that went out, as (kind, title, body)."""
    out = []
    for call in orch.dashboard_state.notify.call_args_list:
        args, kwargs = call
        kind = kwargs.get("kind", args[0] if args else "")
        title = kwargs.get("title", args[1] if len(args) > 1 else "")
        body = kwargs.get("body", args[2] if len(args) > 2 else "")
        out.append((kind, title, body))
    return out


@pytest.mark.asyncio
async def test_the_agent_says_it_finished_when_it_has(home):
    """🔴 Before: the trigger's route heard nothing when the agent ended; the only note was the
    plain "Subagent `a1b2c3d4` completed"."""
    _store_trigger(home)
    orch, on_done = _on_done()
    await on_done([_agent()])
    assert _notes(orch) == [
        (notification_kinds.CRON, "Morning inbox finished", "Two new invoices; one is overdue.")
    ]


@pytest.mark.asyncio
async def test_the_agent_says_it_failed_and_why(home):
    _store_trigger(home, delivery="none")  # a failure still has its own route, the Inbox
    orch, on_done = _on_done()
    await on_done([_agent(error="the model provider answered 503")])
    assert _notes(orch) == [
        (notification_kinds.CRON_FAILED, "Morning inbox failed", "the model provider answered 503")
    ]


@pytest.mark.asyncio
async def test_the_report_goes_out_wherever_the_reply_goes(home):
    """An agent a chat's event started replies into that chat, and its trigger still hears."""
    _store_trigger(home)
    orch, on_done = _on_done()
    session = MagicMock(running=False, task=None, key="kitchen", mode="")
    session._recovery_chat_triggered = False
    session._pending_subagent_failures = []
    orch.dashboard_state.get_session = MagicMock(return_value=session)
    with patch("personalclaw.gateway.run_chat", new_callable=AsyncMock):
        await on_done([_agent(parent="dashboard:kitchen")])
    assert [title for _kind, title, _body in _notes(orch)] == ["Morning inbox finished"]


@pytest.mark.asyncio
async def test_a_quiet_trigger_leaves_the_plain_note(home):
    """`delivery: none` stays quiet about a success; the agent's own note is unchanged."""
    _store_trigger(home, delivery="none")
    orch, on_done = _on_done()
    await on_done([_agent()])
    assert [kind for kind, _t, _b in _notes(orch)] == [notification_kinds.SUBAGENT]


@pytest.mark.asyncio
async def test_an_agent_no_trigger_started_gets_its_own_note(home):
    """No trigger's route tells it: its own note does, named by its task."""
    _store_trigger(home)
    orch, on_done = _on_done()
    await on_done([_agent(trigger_id="")])
    assert [(kind, title) for kind, title, _b in _notes(orch)] == [
        (notification_kinds.SUBAGENT, "Check the inbox and tell me what matters.")
    ]


# ── the workflow run it started, when it ends ─────────────────────────────────────────────────


class _Reports:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, trigger_id: str, **fields: str) -> bool:
        self.calls.append((trigger_id, fields))
        return True


def _ctl(status_error: str = "", *, kind: OriginKind = OriginKind.HOOK) -> Any:
    from types import SimpleNamespace

    run = WorkflowRun(
        id="run-7",
        workflow_name="triage-inbox",
        error_message=status_error,
        origin=RunOrigin(kind=kind, trigger_id=TRIGGER_ID),
    )
    return SimpleNamespace(run=run, services=SimpleNamespace(report_to_trigger=_Reports()))


def test_a_finished_run_reports_to_its_trigger():
    ctl = _ctl()
    run_finish.report_to_its_trigger(ctl.services, ctl.run, RunStatus.COMPLETE)
    assert ctl.services.report_to_trigger.calls == [
        (TRIGGER_ID, {"error": "", "summary": "", "run_id": "run-7"})
    ]


@pytest.mark.parametrize(
    ("status", "message", "said"),
    [
        (RunStatus.FAILED, "engine error: the disk went away", "engine error: the disk went away"),
        (RunStatus.FAILED, "", "The workflow run failed."),
        (RunStatus.ESCALATED, "", "The workflow run has stopped."),
    ],
)
def test_a_run_that_did_not_finish_says_why(status, message, said):
    ctl = _ctl(message)
    run_finish.report_to_its_trigger(ctl.services, ctl.run, status)
    assert ctl.services.report_to_trigger.calls == [
        (TRIGGER_ID, {"error": said, "summary": "", "run_id": "run-7"})
    ]


@pytest.mark.parametrize("status", [RunStatus.CANCELLED, RunStatus.DECLINED])
def test_a_run_a_person_stopped_says_nothing(status):
    ctl = _ctl()
    run_finish.report_to_its_trigger(ctl.services, ctl.run, status)
    assert ctl.services.report_to_trigger.calls == []


def test_a_sub_run_is_not_a_triggers():
    """A sub-run's origin names its parent's node in the same field."""
    ctl = _ctl(kind=OriginKind.SUBAGENT_TOOL)
    run_finish.report_to_its_trigger(ctl.services, ctl.run, RunStatus.COMPLETE)
    assert ctl.services.report_to_trigger.calls == []


SPEC = {
    "name": "triage-inbox",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "sort", "config": {"expr": "sorted"}}],
    },
}


@pytest.mark.asyncio
async def test_the_supervisor_hands_every_run_the_report(home):
    """Through the real supervisor and controller: the run a trigger started reports when it
    ends, and not before."""
    report = _Reports()
    supervisor = WorkflowWatchdog(state=None, services=EngineServices(report_to_trigger=report))
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=SPEC["name"],
            origin=RunOrigin(kind=OriginKind.HOOK, trigger_id=TRIGGER_ID),
        )
    )
    store.write_spec(run.id, copy.deepcopy(SPEC))
    controller: RunController = await supervisor.launch(run, copy.deepcopy(SPEC))
    await asyncio.wait_for(controller._terminal.wait(), timeout=10)
    assert controller.run.status == RunStatus.COMPLETE
    assert report.calls == [(TRIGGER_ID, {"error": "", "summary": "", "run_id": run.id})]
