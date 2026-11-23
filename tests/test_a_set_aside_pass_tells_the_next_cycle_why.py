"""A loop's next cycle is told why the judge's last pass was set aside.

🔴 The defect (Backlog: "A judge's pass that was set aside reaches the next cycle's worker as a
plain PASS with nothing to fix"). A loop's worker prompt hands it the last ruling as
`last.output.verdict` and the criteria scored below target as `last.output.shortfalls`. A pass the
contract set aside because the judge named a forbidden success mode is still a PASS, with no
shortfall, so the next worker read "What the judge ruled on it: PASS" and "Criteria it scored below
target: (none yet)": nothing told it the pass did not stand, or why, and nothing steered it off
repeating the work the judge refused.

Now a judge stage's output carries `set_aside`, the engine's clause for a pass set aside for what
the work did, in the contract's own words (`JudgeVerdict.set_aside`), and every bundled template
that hands its worker the verdict hands it beside it. A pass that stood, a REJECT, and a pass set
aside for the judge's own answer (no proof cited) add nothing: the last says nothing about the
work.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import general_kind_as_run_shared
import pytest

from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template, template_names
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.judge_contract import JudgeHints, validate_verdict
from personalclaw.workflows.models import WorkflowRun

_isolated = general_kind_as_run_shared._isolated

FORBIDDEN = "exit condition reinterpreted to fit"
#: The contract's own sentence for a pass it set aside for that mode.
SET_ASIDE = f"PASS names a forbidden success mode: {FORBIDDEN}"
HINTS = JudgeHints(forbidden_success_modes=[FORBIDDEN, "work claimed without evidence"])


def _ruling(**kw: Any) -> dict[str, Any]:
    return {"verdict": "PASS", "proof": "cat README.md printed the line", "reasoning": "ok", **kw}


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        (_ruling(forbidden_modes_found=[FORBIDDEN]), SET_ASIDE),
        (_ruling(), ""),
        ({"verdict": "PASS", "reasoning": "looks done"}, ""),
        ({"verdict": "REJECT", "reasoning": "the line is missing"}, ""),
    ],
    ids=["a pass set aside for the work", "a pass that stood", "no proof cited", "a REJECT"],
)
def test_only_a_pass_set_aside_for_the_work_carries_why(raw, said):
    note = validate_verdict(raw, HINTS).set_aside
    if said:
        assert note.endswith(said) and "does not stand" in note, note
    else:
        assert note == "", note


# ── end to end: the real general-project spec through a real RunController ──


class _Info:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Subagents:
    """`spawn` + `get`, as `dispatch_stage` calls them. The judge's first ruling is *first*; every
    later one stands. The worker reports no progress, so the loop dries out in two cycles."""

    def __init__(self, first: dict[str, Any]) -> None:
        self.first = first
        self.infos: dict[str, _Info] = {}
        self.prompts: list[str] = []
        self.judged = 0

    def spawn(self, **kw: Any) -> _Info:
        prompt = str(kw.get("prompt") or kw.get("task") or "")
        self.prompts.append(prompt)
        if "You are verifying work you did not do" in prompt:
            self.judged += 1
            payload = {
                "reasoning": "re-ran the cited command; the artifact is present",
                "verdict": "PASS",
                "scores": {"the step accomplished something real": 2, "evidence is checkable": 2},
                "evidence_refs": ["README.md"],
                # A quote of its prompt (the run's exit condition): a pass stands on a proof of
                # what it was shown, so the one set aside is set aside for the mode it names.
                "proof": "README.md has it",
                "cannot_judge": "",
                **(self.first if self.judged == 1 else {}),
            }
        else:
            payload = {
                "summary": "wrote the line the task asked for",
                "meaningful_progress": False,
                "evidence": "README.md now contains it",
            }
        info = _Info(f"sub{len(self.prompts)}", json.dumps(payload))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


def _second_work_prompt(first: dict[str, Any]) -> str:
    wf = read_template("general-project")
    assert wf is not None
    spec = wf.to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="general-project",
            inputs={"task": "add a line to README.md", "exit_condition": "README.md has it"},
        )
    )
    store.write_spec(run.id, spec)
    fake = _Subagents(first)
    controller = RunController(run, spec, services=EngineServices(subagents=fake))
    asyncio.run(controller.run_to_completion(timeout=20.0))
    work = [p for p in fake.prompts if "Do the next meaningful step" in p]
    assert len(work) >= 2, f"the loop never reached a second cycle: {len(work)} work prompt(s)"
    return work[1]


def test_the_next_cycle_is_told_why_the_last_pass_was_set_aside():
    """🔴 Before: it read "What the judge ruled on it: PASS", with nothing below target."""
    second = _second_work_prompt({"forbidden_modes_found": [FORBIDDEN]})
    ruled = next(line for line in second.splitlines() if line.startswith("What the judge ruled"))
    assert ruled.startswith("What the judge ruled on it: PASS — set aside"), ruled
    assert ruled.endswith(SET_ASIDE), ruled


def test_a_pass_that_stood_reads_as_it_did():
    """The control: no clause, and no stray empty text beside the verdict."""
    second = _second_work_prompt({})
    assert "What the judge ruled on it: PASS\nCriteria it scored below target:" in second, second
    assert "set aside" not in second, second


# ── the rail: a template that hands its worker the ruling hands it why a pass was set aside ──


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def test_every_template_that_reads_the_verdict_reads_why_a_pass_was_set_aside():
    readers = {}
    for name in template_names():
        wf = read_template(name)
        if wf is None:
            continue
        prompts = [s for s in _strings(wf.to_dict()) if "last.output.verdict" in s]
        if prompts:
            readers[name] = all("last.output.set_aside" in p for p in prompts)
    assert len(readers) >= 3, readers
    assert all(readers.values()), {n: ok for n, ok in readers.items() if not ok}
