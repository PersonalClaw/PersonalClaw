"""A call you decline in a loop's judge, or in a read-only verifier, refuses that call and nothing
else: the loop goes on.

A loop waits for you after your Deny because its next cycle reruns the work with the judge's
critique, and the critique of work you declined is to do it: the same call put to you again
(`declines`). A judge's read is not that. Measured on a deep-research run: cycle 1's judge asked to
search the whole disk for a file, the owner declined it, the judge ruled without it, and the run
paused for a manual Resume that cycle 2 never needed. Now a judge's or a verifier's Deny leaves the
cycle as it was: the steps after it run, the judge's ruling reaches the next cycle, nothing waits,
and the run page still lists what you declined. A Deny in the work still ends its cycle and waits
(`test_a_loop_cycle_you_declined_waits_for_you`).

Every case drives the REAL bundled spec (or a small spec of its own) through a REAL
`RunController`; only the subagent manager is scripted, as a stage's subagent ends.
"""

from __future__ import annotations

from typing import Any

import live_runs
import pytest
from test_a_loop_cycle_you_declined_waits_for_you import (  # noqa: F401 - fixtures used by name
    JUDGE_PROMPT,
    TIMEOUT,
    WROTE_NOTE,
    _controller,
    _declined_rows,
    _Info,
    _isolated,
    _loop,
    _Subagents,
    inbox,
)

from personalclaw.declined_calls import declined_step, said
from personalclaw.workflows import declines, service, store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.models import InstanceState, Node, RunStatus

#: Her Deny of a read the judge asked for, as the subagent keeps it.
SEARCHED = declined_step("bash", {"command": "find / -name RESEARCH.md"})


class _JudgeAsks(_Subagents):
    """`_Subagents`, whose judge of cycle *n* ends with ``judge_denies[n]`` declined (none past
    the list)."""

    def __init__(self, *, judge_denies: list[list[dict[str, Any]]], **kw: Any) -> None:
        super().__init__(**kw)
        self.judge_denies = judge_denies

    def spawn(self, **kw: Any) -> _Info:
        info = super().spawn(**kw)
        if JUDGE_PROMPT in str(kw.get("task") or ""):
            n = len(self.judged) - 1
            info.declined_calls = self.judge_denies[n] if n < len(self.judge_denies) else []
        return info


def test_a_judges_declined_read_leaves_its_loop_running(inbox: dict) -> None:  # noqa: F811
    """🔴 Before: the run paused at the end of cycle 1 and waited for a Resume."""
    subagents = _JudgeAsks(judge_denies=[[SEARCHED]], denies=[[]], verdicts=("REJECT", "PASS"))
    run, spec = _loop()
    status = live_runs.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))

    assert status is not RunStatus.PAUSED, store.get(run.id).error_message
    assert not store.pause_requested(run.id)
    # Cycle 2 ran with the judge's ruling on cycle 1, as after any cycle.
    assert len(subagents.work) >= 2, subagents.work
    assert "REJECT" in subagents.work[1]
    assert _declined_rows(run.id) == []
    assert inbox["raised"] == [], "nothing asks her to resume a run that goes on"
    # What she declined is still said, as the judge's, in its cycle.
    judge = store.read_state(run.id)["root.body@0.children[1]"]
    assert (judge.state, judge.declined) == (InstanceState.DONE, [SEARCHED])
    assert service.status(run.id)["declined"] == [
        f"You declined {said([SEARCHED])} in “judge”, in cycle 1."
    ]


def test_a_verifiers_declined_read_skips_none_of_its_cycles_steps(
    inbox: dict,  # noqa: F811
) -> None:
    """A read-only verifier ahead of the work: its Deny does not end the cycle at it."""
    spec = {
        "name": "survey-then-work",
        "root": {
            "kind": "loop",
            "id": "rounds",
            "config": {"mode": "counted", "n": 2},
            "body": {
                "kind": "sequence",
                "id": "round",
                "children": [
                    {
                        "kind": "stage",
                        "id": "survey",
                        "config": {"prompt": "Read the folder.", "tools_posture": "verify"},
                    },
                    {"kind": "stage", "id": "work", "config": {"prompt": "Draft the plan."}},
                ],
            },
        },
    }
    run, spec = _loop(spec)
    subagents = _Subagents(denies=[[SEARCHED], []])
    status = live_runs.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))

    assert status is RunStatus.COMPLETE, (status, store.get(run.id).error_message)
    assert len(subagents.work) == 4, "both cycles ran both steps"
    work = store.read_state(run.id)["root.body@0.children[1]"]
    assert work.state is InstanceState.DONE, work.degraded_reason
    assert service.status(run.id)["declined"] == [
        f"You declined {said([SEARCHED])} in “survey”, in cycle 1."
    ]
    assert inbox["raised"] == []


def test_a_deny_in_the_work_still_ends_its_cycle_and_waits(inbox: dict) -> None:  # noqa: F811
    """The control: the work's Deny is the one the next cycle would put to her again."""
    subagents = _JudgeAsks(judge_denies=[[SEARCHED]], denies=[[WROTE_NOTE], []])
    run, spec = _loop()
    status = live_runs.run(_controller(run, spec, subagents).run_to_completion(timeout=TIMEOUT))
    assert status is RunStatus.PAUSED, status
    assert subagents.judged == [], "the judge ruled on work she declined"
    assert len(_declined_rows(run.id)) == 1


def _general(step: str) -> Node:
    spec = read_template("general-project").to_dict()
    body = Node.from_dict(spec["root"]["body"])
    return next(child for child in body.children if child.id == step)


@pytest.mark.parametrize(
    ("node", "answers"),
    [
        (_general("work"), True),
        (_general("judge"), False),
        (Node.from_dict({"kind": "gate", "id": "accept", "config": {"kind": "judge"}}), False),
        (
            Node.from_dict(
                {"kind": "stage", "id": "survey", "config": {"tools_posture": "verify"}}
            ),
            False,
        ),
        # The posture is read as the engine reads it, whatever its case or spacing.
        (
            Node.from_dict(
                {"kind": "stage", "id": "survey", "config": {"tools_posture": " Verify "}}
            ),
            False,
        ),
        (
            Node.from_dict({"kind": "stage", "id": "draft", "config": {"tools_posture": "full"}}),
            True,
        ),
        # CORE's should-fix: a declared capability wins (`engine.stage_capability`), so a verifier
        # that may change things is work its next cycle reruns.
        (
            Node.from_dict(
                {
                    "kind": "stage",
                    "id": "fixer",
                    "config": {"tools_posture": "verify", "capability": "mutating"},
                }
            ),
            True,
        ),
        (None, True),
    ],
    ids=[
        "the work",
        "a judge stage",
        "a judge gate",
        "a read-only verifier",
        "a read-only verifier written Verify",
        "a writer",
        "a verifier that may change things",
        "gone",
    ],
)
def test_which_steps_the_next_cycle_reruns_with_the_critique(node: Node | None, answers: bool):
    assert declines.answers_to_the_critique(node) is answers
