"""A loop that finishes on the last cycle its budget allows ends complete, not escalated.

The engine asked the budget before the loop's own exit. `check_breaker` tripped `max_iterations`
at the end of the last allowed iteration and escalated the run before `loop_should_continue` read
the exit that iteration had met, and `loop_should_continue` itself checked its cap before its exit
test. So a loop that met its exit on its last cycle ended "needs a decision", and onboarding's
one-cycle loop always did: after its only cycle the Inbox said *Loop needs a decision*, and opening
the row said *This run was escalated, so the request can no longer be answered.*

Now the loop's own test runs first, and the budget stops the loop only when it would otherwise go
on. On the iteration the budget ends on, a judge that accepted the work is the loop's done. A loop
that genuinely runs out escalates with a sentence that names the budget, and its Inbox row says it
stopped rather than asking for an answer nothing can give.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import loop_aliases, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun
from personalclaw.workflows.tick import loop_should_continue

TEMPLATE = loop_aliases.KIND_TO_TEMPLATE["general"]
RUN_TIMEOUT = 20.0


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Subagents:
    """`SubagentManager` on the two methods a stage dispatch uses. The work stage reports real
    progress each time (so `until_dry` alone never ends the loop) unless told otherwise, and the
    judge answers with *verdict*."""

    def __init__(self, *, verdict: str, progress: bool = True) -> None:
        self.verdict = verdict
        self.progress = progress
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        if "You are verifying work you did not do" in prompt:
            payload: dict[str, Any] = {
                "reasoning": "re-ran the cited command; the artifact is present",
                "verdict": self.verdict,
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["README.md"],
                "proof": "cat README.md printed the added line",
                "cannot_judge": "",
            }
        else:
            payload = {
                "summary": "wrote the line the task asked for",
                "meaningful_progress": self.progress,
                "evidence": "README.md now contains it",
            }
        info = _Info(f"sub{len(self.infos) + 1}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


class _Inbox:
    """The attention state a run's end is announced to: every row and notification it raised."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, kind: str, title: str, body: str = "", **_kw: Any) -> None:
        self.notes.append((kind, title, body))


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


@pytest.fixture
def inbox(monkeypatch: pytest.MonkeyPatch) -> _Inbox:
    box = _Inbox()

    def emit(_state: Any, **kw: Any) -> str:
        box.rows.append(kw)
        return f"item{len(box.rows)}"

    monkeypatch.setattr("personalclaw.inbox.emit_attention_item", emit)
    return box


def _drive(subagents: _Subagents, *, cycles: int, inbox: _Inbox | None = None) -> WorkflowRun:
    """The REAL bundled template through a REAL controller, with the run's own cycle budget."""
    wf = read_template(TEMPLATE)
    assert wf is not None
    spec = wf.to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={
                "task": "add a line to README.md",
                "exit_condition": "README.md contains the line",
            },
            policy_overrides={"max_cycles": cycles},
            loop_kind="general",
        )
    )
    store.write_spec(run.id, spec)
    services = EngineServices(subagents=subagents, attention_state=inbox)
    controller = RunController(run, spec, services=services)
    asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    ended = store.get(run.id)
    assert ended is not None
    return ended


def _outcomes(run_id: str) -> list[str]:
    return [str(e.get("outcome") or "") for e in J.journal_records(run_id, kinds={"iteration"})]


def test_a_one_cycle_loop_whose_judge_accepts_its_cycle_ends_complete(inbox: _Inbox) -> None:
    """Onboarding's loop: one cycle, and the judge accepted it. On `main` it escalated."""
    run = _drive(_Subagents(verdict="PASS"), cycles=1, inbox=inbox)

    assert run.status is RunStatus.COMPLETE, (run.status, run.attention)
    assert not run.attention, run.attention
    assert _outcomes(run.id) == ["judge_done"], _outcomes(run.id)
    assert inbox.rows == [], "a loop that finished leaves no row that asks for anything"
    assert [title for _kind, title, _body in inbox.notes] == ["Loop complete"]


def test_a_one_cycle_loop_whose_judge_did_not_accept_it_escalates_naming_its_budget(
    inbox: _Inbox,
) -> None:
    run = _drive(_Subagents(verdict="REJECT"), cycles=1, inbox=inbox)

    assert run.status is RunStatus.ESCALATED, run.status
    assert run.attention["reason"] == "max_iterations"
    assert (
        run.attention["detail"] == "It used its budget of 1 cycle, and the judge did not accept "
        "the last one."
    )
    [row] = inbox.rows
    assert row["title"] == "Loop stopped at its budget", row
    assert row["body"].endswith(run.attention["detail"]), row
    assert "workflow_node" not in row["refs"], "the row is not a gate an answer can reach"


def test_a_loop_that_meets_its_exit_on_its_last_allowed_cycle_is_complete() -> None:
    """Two cycles allowed, and the `until_dry` streak of two is met on the second."""
    run = _drive(_Subagents(verdict="REJECT", progress=False), cycles=2)

    assert run.status is RunStatus.COMPLETE, (run.status, run.attention)
    assert _outcomes(run.id)[-1] == "dry_streak", _outcomes(run.id)


def test_a_loop_that_runs_out_before_its_exit_escalates_naming_its_budget() -> None:
    """No judge accepted anything and the work kept reporting progress: out of budget."""
    run = _drive(_Subagents(verdict="REJECT"), cycles=2)

    assert run.status is RunStatus.ESCALATED, run.status
    assert run.attention["detail"] == (
        "It used its budget of 2 cycles, and the judge did not accept the last one."
    )


def _loop(**config: Any) -> Node:
    return Node.from_dict(
        {
            "kind": "loop",
            "id": "l",
            "config": config,
            "body": {"kind": "transform", "id": "t", "config": {"expr": "1"}},
        }
    )


@pytest.mark.parametrize(
    "node, dry_streak, expected",
    [
        (_loop(mode="until_dry", streak=2, max_iterations=2), 2, (False, "dry_streak")),
        (_loop(mode="counted", n=3, max_iterations=3), 0, (False, "counted_complete")),
        (_loop(mode="until_dry", streak=2, max_iterations=2), 1, (False, "max_iterations")),
    ],
)
def test_the_loops_own_exit_is_asked_before_its_cap(node, dry_streak, expected) -> None:
    iteration = node.config["max_iterations"]
    assert loop_should_continue(node, iteration=iteration, dry_streak=dry_streak) == expected
