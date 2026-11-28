"""A judge gate that refuses the work ends the run saying to fix what the judge found.

🔴 The defect (Backlog: "A judge gate that refuses the work ends the run telling you to change the
step that gave up"). A judge gate that did not accept the work (a REJECT, or a PASS its contract
set aside for a forbidden success mode or a rubric shortfall) failed as the user's failure with no
cause of its own, so the run's ending read it as a step that gave up: "change the step that gave
up, then run the workflow again", and the run page offered Change the workflow. Nothing gave up:
the judge read the work and refused it.

Now the gate names its refusal (`models.JUDGE_REFUSED`, its failure's `terminal_reason`), and the
ending reads it as its own cause: the judge did not accept the work, fix what it found, then run
again; the run page heads it so and offers no Change the workflow. A judge that answered in the
wrong shape stays the step's, its rubric's, to change.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from personalclaw.workflows import ending_sentence, store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import _judge_gate_outcome
from personalclaw.workflows.judge_contract import JudgeHints, validate_verdict
from personalclaw.workflows.models import JUDGE_REFUSED, Node, WorkflowRun

GATE = Node.from_dict({"kind": "gate", "id": "accept", "config": {"kind": "judge"}})


def _verdict(**kw: Any) -> Any:
    raw = {"verdict": "PASS", "proof": "the tests pass", "reasoning": "read it", **kw}
    hints = JudgeHints(forbidden_success_modes=["test deleted or skipped"])
    return validate_verdict(raw, hints)


@pytest.mark.parametrize(
    "decision",
    [
        {"verdict": "REJECT", "reasoning": "the change breaks two callers"},
        {"forbidden_modes_found": ["test deleted or skipped"]},
    ],
    ids=["a REJECT", "a PASS set aside for what the work did"],
)
def test_a_refused_gate_ends_saying_fix_what_the_judge_found(decision):
    """🔴 Before: its failure named no cause, so its stop was the step's: change the step."""
    _state, failure = _judge_gate_outcome(_verdict(**decision), GATE)
    assert failure is not None and failure.terminal_reason == JUDGE_REFUSED, failure
    stop = ending_sentence.step_stop(failure)
    assert stop.cause == ending_sentence.JUDGE_REFUSED, stop
    assert "fix what it found in the work" in stop.remedy, stop
    assert "change the step" not in stop.remedy, stop


def test_a_judge_that_answered_in_the_wrong_shape_is_still_the_steps_to_change():
    """The control: no proof is the judge's answer failing the contract, not a verdict on the
    work, and its rubric or its step is what to change."""
    _state, failure = _judge_gate_outcome(validate_verdict({"verdict": "PASS"}, JudgeHints()), GATE)
    assert failure is not None and failure.terminal_reason == "", failure
    assert ending_sentence.step_stop(failure).cause == ending_sentence.STEP


def test_a_run_whose_judge_gate_refuses_the_work_ends_on_the_judges_cause(tmp_path, monkeypatch):
    """End to end: the run's own record of why it stopped carries the cause the page heads."""
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)

    async def judge(instruction, use_case=None, output_type=None, **_kw):
        return json.dumps(
            {"verdict": "REJECT", "proof": "Is it done", "reasoning": "the change breaks callers"}
        )

    spec = {
        "name": "judged",
        "version": 1,
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "transform", "id": "draft", "config": {"expr": {"ran": "draft"}}},
                {
                    "kind": "gate",
                    "id": "accept",
                    "config": {"kind": "judge", "prompt": "Is it done?"},
                },
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="judged", inputs={}))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(completion=judge))
    asyncio.run(controller.run_to_completion(timeout=20.0))
    attention = controller.run.attention or {}
    assert attention.get("cause") == ending_sentence.JUDGE_REFUSED, attention
    assert "fix what it found in the work" in str(attention.get("remedy") or ""), attention
    said = json.dumps(attention) + str(controller.run.error_message or "")
    assert "change the step that gave up" not in said, said
