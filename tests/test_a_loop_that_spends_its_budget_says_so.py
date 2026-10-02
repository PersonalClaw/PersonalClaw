"""A loop that stops at the budget it was given says so, and names no step it does not have to.

Setup's one-cycle loop ran its one cycle, the judge did not accept it, and the loop stopped: the
budget of 1 was setup's own. The bell said "Loop stopped at its budget", but the run's own ending
read "“project” escalated." — the id of the template's root loop, which is the whole run, beside a
verb that sends the reader looking for a step that gave up. Measured here on the real
`general-project` spec driven through a real `RunController`, with only the subagent manager
faked:

* the run's ending is the budget sentence the bell already carries, and it names no step, because
  the loop that stopped IS the run;
* the escalation record says it was a budget stop (`budget: true`) where it is written, so every
  surface that reads the record (the run page, the Workflows list, the chat card, the Inbox row)
  reads one classification instead of re-deriving it from reason tokens;
* a loop that escalates for any other reason is not called a budget stop (the negative control).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import ending_sentence, service, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import Node, NodeKind, RunStatus, WorkflowRun
from personalclaw.workflows.resilience import BUDGET_TRIPS

TEMPLATE = "general-project"
TASK = "draft a short note describing what an agent could do this week"
RUN_TIMEOUT = 20.0
BUDGET_SENTENCE = "It used its budget of 1 cycle, and the judge did not accept the last one."


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: home)
    return home


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Subagents:
    """The work stage reports progress; the judge never accepts it."""

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("task") or "")
        if "You are verifying work you did not do" in prompt:
            payload: dict[str, Any] = {
                "reasoning": "no note was saved anywhere",
                "verdict": "REJECT",
                "scores": {"the step accomplished something real": 0, "evidence is checkable": 0},
                "evidence_refs": [],
                "proof": "",
                "cannot_judge": "",
            }
        else:
            payload = {"summary": "drafted a note", "meaningful_progress": True, "evidence": ""}
        info = _Info(f"sub{len(self.infos) + 1}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


def _drive(overrides: dict[str, Any]) -> WorkflowRun:
    wf = read_template(TEMPLATE)
    assert wf is not None
    spec = wf.to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name=TEMPLATE,
            inputs={"task": TASK, "exit_condition": "the note exists"},
            policy_overrides=dict(overrides),
        )
    )
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(subagents=_Subagents()))
    asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))
    final = store.get(run.id)
    assert final is not None
    return final


def test_a_one_cycle_loop_ends_with_the_budget_sentence_and_names_no_step() -> None:
    run = _drive({"attended": False, "max_cycles": 1})

    assert run.status == RunStatus.ESCALATED, run.status
    assert run.error_message == BUDGET_SENTENCE, run.error_message
    root_id = read_template(TEMPLATE).to_dict()["root"]["id"]
    assert root_id not in run.error_message
    assert "escalated" not in run.error_message


def test_the_record_says_it_was_a_budget_stop_where_it_is_written() -> None:
    run = _drive({"attended": False, "max_cycles": 1})

    assert (run.attention or {}).get("cause") == "budget", run.attention
    escalations = service.status(run.id)["escalations"]
    assert escalations, "the run raised no escalation at all"
    assert escalations[-1]["cause"] == "budget", escalations[-1]
    assert escalations[-1]["reason"] in BUDGET_TRIPS
    assert "Max cycles" in str(escalations[-1]["remedy"]), escalations[-1]


class _Quiet:
    """A loop's controller with nothing failed and no judge ruling: the budget, alone, decides."""

    instances: dict = {}
    _iterations: dict = {}
    _outputs: dict = {}
    root = Node(kind=NodeKind.LOOP, id="loop")


@pytest.mark.parametrize("reason", sorted(BUDGET_TRIPS))
def test_each_budget_trip_is_a_budget_stop(reason: str) -> None:
    _, stop = ending_sentence.loop_stop(_Quiet(), "root", _Quiet.root, reason=reason, detail="d")
    assert stop.cause == "budget"


@pytest.mark.parametrize("reason", ["repeated_error", "identical_output", "no_progress"])
def test_an_escalation_for_any_other_reason_is_not_a_budget_stop(reason: str) -> None:
    _, stop = ending_sentence.loop_stop(_Quiet(), "root", _Quiet.root, reason=reason, detail="d")
    assert stop.cause == "step"
