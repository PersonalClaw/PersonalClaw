"""A workflow run that ends needing the user tells them: an Inbox row and its one notification.

A research run started from the Workflows page ran for hours and ended ``escalated`` ("The run
continued past “investigate”, which escalated."), and nothing followed: no bell entry, no Inbox row,
no channel message. Only a run started as a loop, and a run a trigger started (on the trigger's own
route), ever said how they ended. Now any run that fails or escalates raises one Inbox row that
opens the run, and its one notification, the way a loop's escalation does. A run that completed,
was cancelled or was declined says nothing, and a run a trigger started, or a sub-run, is told of
once, by its trigger or its parent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import attention, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

_ENDING = "The run continued past “investigate”, which escalated."


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


class _State:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str, dict]] = []

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None) -> None:
        self.notes.append((kind, title, body, meta or {}))


@pytest.fixture
def raised(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _emit(state: Any, **kw: Any) -> str:
        calls.append(kw)
        return "item-1"

    monkeypatch.setattr("personalclaw.inbox.emit_attention_item", _emit)
    return calls


def _run(status: RunStatus, **over: Any) -> WorkflowRun:
    base: dict[str, Any] = dict(
        id="run-7f3c",
        workflow_name="deep-research",
        status=status,
        error_message=_ENDING if status == RunStatus.ESCALATED else "",
    )
    base.update(over)
    return WorkflowRun(**base)


def test_an_escalated_run_raises_a_row_that_opens_it(raised: list) -> None:
    run = _run(RunStatus.ESCALATED)
    assert attention.announce_run_end(_State(), run, RunStatus.ESCALATED) == "item-1"
    [call] = raised
    assert call["title"] == "Workflow run stopped before it finished"
    assert call["body"] == f"deep-research — {_ENDING}"
    assert call["refs"] == {"workflow": "run-7f3c"}
    assert call["dedup_key"] == "workflow-run:run-7f3c:escalated"
    # The pair a workflow gate rides: the user's rule for "a run needs you" decides how loudly.
    assert (call["source"], call["kind"], call["item_kind"]) == (
        "loop",
        "needs_input",
        "needs_input",
    )


def test_a_failed_run_says_why(raised: list) -> None:
    run = _run(RunStatus.FAILED, error_message="engine error: the provider is down")
    attention.announce_run_end(_State(), run, RunStatus.FAILED)
    [call] = raised
    assert call["title"] == "Workflow run failed"
    assert "the provider is down" in call["body"]


@pytest.mark.parametrize("status", [RunStatus.COMPLETE, RunStatus.CANCELLED, RunStatus.DECLINED])
def test_an_ending_that_needs_nothing_from_the_user_says_nothing(raised: list, status) -> None:
    state = _State()
    attention.announce_run_end(state, _run(status), status)
    assert raised == [] and state.notes == []


def test_a_run_a_trigger_started_is_told_of_on_its_trigger_s_route_only(raised: list) -> None:
    run = _run(RunStatus.ESCALATED, origin=RunOrigin(kind=OriginKind.HOOK, trigger_id="t-1"))
    attention.announce_run_end(_State(), run, RunStatus.ESCALATED)
    assert raised == []


def test_a_sub_run_is_its_parent_s_step(raised: list) -> None:
    attention.announce_run_end(
        _State(), _run(RunStatus.FAILED, parent_run_id="parent-1"), RunStatus.FAILED
    )
    assert raised == []


def test_a_loop_s_ending_is_still_announced_as_a_loop_s(raised: list) -> None:
    """The floor: a run started as a loop keeps its own words and refs."""
    run = _run(RunStatus.ESCALATED, loop_kind="general", title="Autumn haiku")
    attention.announce_run_end(_State(), run, RunStatus.ESCALATED)
    [call] = raised
    assert call["title"] == "Loop stopped before it finished"
    assert call["refs"]["loop"] == "run-7f3c"


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id, self.result = agent_id, result
        self.done, self.error, self.reaped, self.agent = True, "", False, ""


class _Subagents:
    """The work reports progress each cycle and its judge never accepts it, so the run stops at
    its cycle ceiling: an escalation."""

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        judge = "You are verifying work you did not do" in str(kw.get("task"))
        payload: dict[str, Any] = {
            "summary": "wrote it",
            "meaningful_progress": True,
            "evidence": "x",
        }
        if judge:
            payload = {
                "reasoning": "not yet",
                "verdict": "REJECT",
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["x"],
                "proof": "x",
                "cannot_judge": "",
            }
        info = _Info(f"s{len(self.infos) + 1}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        return self.infos.get(agent_id)

    async def cancel(self, agent_id: str, *, reason: str = "") -> bool:  # pragma: no cover
        return False


def test_the_engine_announces_a_workflow_run_that_escalated(raised: list) -> None:
    """Through the real controller, with a run started from the Workflows page (not as a loop)."""
    spec = read_template("general-project").to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="general-project",
            title="Research the release",
            inputs={"task": "research the release", "exit_condition": "a summary exists"},
            policy_overrides={"attended": False, "max_cycles": 1},
        )
    )
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(subagents=_Subagents(), attention_state=_State())
    )

    async def _go() -> RunStatus:
        await controller.start()
        return await asyncio.wait_for(controller.run_to_completion(), timeout=20.0)

    assert asyncio.run(_go()) is RunStatus.ESCALATED
    assert [(c["title"], c["refs"]) for c in raised] == [
        ("Workflow run stopped before it finished", {"workflow": run.id})
    ], raised
