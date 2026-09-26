"""A run started as a loop tells the user it ended — complete, failed, or stopped for a decision.

Measured 2026-09-25 on a live drive: one unattended General loop completed, one was cancelled and
one stopped at its cycle ceiling with "This run stopped and needs a decision", and there was not one
notification or inbox row for any of them. A loops-table loop has always announced the same moments
(`loop/watchdog.py:_NOTIFY_EVENTS`); a General loop is a workflow run (PP-16), and the workflow
engine only ever raised a row for a GATE.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw import notification_kinds
from personalclaw.workflows import attention, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun


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
        id="r1",
        workflow_name="general-project",
        status=status,
        loop_kind="general",
        title="Autumn haiku",
    )
    base.update(over)
    return WorkflowRun(**base)


def test_a_completed_loop_says_so(raised: list) -> None:
    state = _State()
    attention.announce_loop_end(state, _run(RunStatus.COMPLETE), RunStatus.COMPLETE)
    assert state.notes == [
        (
            notification_kinds.LOOP_COMPLETE,
            "Loop complete",
            "Autumn haiku",
            {"loop_id": "r1", "loop_kind": "general", "run_id": "r1"},
        )
    ]
    assert raised == []


def test_a_failed_loop_says_why() -> None:
    state = _State()
    run = _run(RunStatus.FAILED, error_message="engine error: the provider is down")
    attention.announce_loop_end(state, run, RunStatus.FAILED)
    [(kind, title, body, _meta)] = state.notes
    assert (kind, title) == (notification_kinds.LOOP_FAILED, "Loop failed")
    assert "the provider is down" in body


def test_a_loop_that_stopped_for_a_decision_raises_a_standing_request(raised: list) -> None:
    state = _State()
    run = _run(
        RunStatus.ESCALATED,
        attention={
            "kind": "escalation",
            "reason": "max_iterations",
            "detail": "reached 6 iterations",
        },
    )
    assert attention.announce_loop_end(state, run, RunStatus.ESCALATED) == "item-1"
    [call] = raised
    assert call["title"] == "Loop needs a decision"
    assert "reached 6 iterations" in call["body"]
    # `loop` is what every loop surface deep-links by; `workflow` is what a delete resolves by.
    assert call["refs"] == {"loop": "r1", "loop_kind": "general", "workflow": "r1"}
    assert call["dedup_key"] == "loop-run:r1:escalated"


def test_a_cancel_and_a_template_run_say_nothing(raised: list) -> None:
    state = _State()
    attention.announce_loop_end(state, _run(RunStatus.CANCELLED), RunStatus.CANCELLED)
    attention.announce_loop_end(state, _run(RunStatus.COMPLETE, loop_kind=""), RunStatus.COMPLETE)
    assert state.notes == [] and raised == []


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id, self.result = agent_id, result
        self.done, self.error, self.reaped, self.agent = True, "", False, ""


class _Subagents:
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
                "reasoning": "ok",
                "verdict": "PASS",
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

    async def cancel(self, agent_id: str) -> bool:  # pragma: no cover
        return False


def test_the_engine_announces_a_loop_run_that_hit_its_cycle_ceiling(raised: list) -> None:
    """Through the real controller. At `origin/main` the run escalates and nothing is raised."""
    spec = read_template("general-project").to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="general-project",
            loop_kind="general",
            title="Autumn haiku",
            inputs={"task": "write a haiku", "exit_condition": "it exists"},
            policy_overrides={"attended": False, "max_cycles": 1},
        )
    )
    store.write_spec(run.id, spec)
    state = _State()
    controller = RunController(
        run, spec, services=EngineServices(subagents=_Subagents(), attention_state=state)
    )

    async def _go() -> RunStatus:
        await controller.start()
        return await asyncio.wait_for(controller.run_to_completion(), timeout=20.0)

    assert asyncio.run(_go()) is RunStatus.ESCALATED
    assert [c["title"] for c in raised] == ["Loop needs a decision"], raised
