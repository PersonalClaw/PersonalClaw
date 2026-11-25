"""A loop whose judge would not decide stops before another round, and asks you.

🔴 The defect (Backlog: "A judge's ESCALATE verdict does not stop a loop's next round"). A judge
stage inside a loop can rule ESCALATE, handing the decision to a person, and its contract escalates
a ruling on its own (one that says what it could not check, or a PASS the run's own check failed).
The ruling was recorded and the loop went on: another worker round, another judge call, round
after round, until the loop ran out of rounds; only then was the ruling read, as the reason it
ended.

Now the ruling is read at each cycle's own advance (`loop_iteration._what_ends_the_cycle`): a judge
that would not decide ends the loop there, escalated in the judge's words, and a budget that ran
out beside it is not why it stopped. A judge that rejected the work keeps the loop going.

Driven on the REAL bundled `general-project` spec through a REAL `RunController`, with six cycles
to spend and a worker that always claims progress, so nothing but the judge could end it early.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

TEMPLATE = "general-project"
JUDGE_PROMPT = "You are verifying work you did not do"
WORK_PROMPT = "Do the next meaningful step"
COULD_NOT_CHECK = "Whether the note covers every deadline: neither of us could read the folder."
NEEDS_A_PERSON = "The two drafts contradict each other, and a person must choose."


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Subagents:
    """`spawn` + `get`, as `dispatch_stage` calls them: the worker always claims progress, and the
    judge answers *judge* every round."""

    def __init__(self, judge: dict[str, Any]) -> None:
        self.judge = judge
        self.infos: dict[str, _Info] = {}
        self.prompts: list[str] = []

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        self.prompts.append(prompt)
        payload = (
            self.judge
            if JUDGE_PROMPT in prompt
            else {
                "summary": "drafted the note",
                "meaningful_progress": True,
                "evidence": "the note is in notes.md",
            }
        )
        info = _Info(f"sub{len(self.prompts)}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info

    def rounds(self) -> int:
        return sum(WORK_PROMPT in p for p in self.prompts)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


def _drive(judge: dict[str, Any], *, cycles: int = 6) -> tuple[RunController, _Subagents]:
    spec = read_template(TEMPLATE).to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={
                "task": "Draft a short note of this week's deadlines.",
                "exit_condition": "The note lists every deadline",
            },
            loop_kind="general",
            policy_overrides={"max_cycles": cycles},
        )
    )
    store.write_spec(run.id, spec)
    fake = _Subagents(judge)
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    asyncio.run(controller.run_to_completion(timeout=30.0))
    return controller, fake


def _judge(verdict: str, **extra: Any) -> dict[str, Any]:
    return {
        "reasoning": NEEDS_A_PERSON,
        "verdict": verdict,
        "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
        "evidence_refs": ["notes.md"],
        "proof": "notes.md lists the deadlines",
        "forbidden_modes_found": [],
        "cannot_judge": "",
        **extra,
    }


@pytest.mark.parametrize(
    ("judge", "said"),
    [
        (_judge("ESCALATE"), NEEDS_A_PERSON.rstrip(".")),
        (_judge("REJECT", cannot_judge=COULD_NOT_CHECK), COULD_NOT_CHECK.rstrip(".")),
    ],
    ids=["it ruled ESCALATE", "its contract escalated what it could not check"],
)
def test_a_judge_that_would_not_decide_stops_the_loop_after_its_round(judge, said):
    """🔴 Before: rounds 2 to 6 ran, a worker and a judge each, before the loop asked anyone."""
    controller, fake = _drive(judge)
    assert fake.rounds() == 1, f"the loop ran {fake.rounds()} rounds after its judge handed it over"
    run = controller.run
    assert run.status is RunStatus.ESCALATED, run.status
    attention = run.attention or {}
    assert attention.get("reason") == "judge_escalated", attention
    assert said in str(attention.get("detail") or ""), attention


def test_on_the_round_its_budget_ends_the_judges_stop_is_why_it_stopped():
    """A budget that ran out beside a judge that would not decide is not the reason, and the
    run's timeline does not say the budget tripped either: a ``breaker:`` row there is what a
    reader sees as why the loop stopped (`run_cockpit`)."""
    controller, fake = _drive(_judge("ESCALATE"), cycles=1)
    assert fake.rounds() == 1
    attention = controller.run.attention or {}
    assert attention.get("reason") == "judge_escalated", attention
    assert attention.get("cause") == "judge", attention
    rows = J.journal_records(controller.run.id, kinds={"iteration"})
    tripped = [r.get("outcome") for r in rows if str(r.get("outcome") or "").startswith("breaker:")]
    assert tripped == [], f"the timeline says the budget ended the loop: {tripped}"


def test_the_control_a_judge_that_rejected_keeps_the_loop_going():
    controller, fake = _drive(_judge("REJECT"), cycles=3)
    assert fake.rounds() == 3, fake.rounds()
    assert (controller.run.attention or {}).get("reason") == "max_iterations"
