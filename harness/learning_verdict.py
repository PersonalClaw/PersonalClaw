"""The skills-on/off verdict — `fanout_measure`'s posture, one arm vocabulary wider.

`harness/fanout_measure.py` owns the statistical posture this repo verdicts paired arms
with: the 5-point inconclusive band, the 5% token-match tolerance, the 3-trial floor, and
the rule that within-arm spread beats the delta. The learning benchmark
(`docs/roadmap/research/learning-benchmark-protocol.md` §5) is prescribed against exactly
those constants — and cannot use that module's entry points, because `load_observations`
requires arms literally named `fanout` and `single` and refuses anything else. Measured:
`harness fanout-measure` on arms named `skills_on`/`skills_off` exits 2.

§5 states the two honest options and rules out the third. Generalising the arm vocabulary
inside `fanout_measure` is an owner call (the names are deliberately fixed there), and
relabelling `skills_on` as `fanout` to get a green run would be a lie in the output file.
So this is the sanctioned second option: **a thin sibling that imports the same constants**
— and, more than that, calls the same `compare()`. Nothing here re-derives a threshold, a
check order, or an aggregate:

* `Trial`, `Arm` and `compare()` are imported and used as-is. `compare()` is pure and its
  logic never reads an arm's *name*, only its trials — the names bind in `load_observations`,
  which this module does not use.
* the only thing added is a **directional relabel** of the two winner verdicts, so an output
  file about skills does not say `fanout_wins`. The closed set stays the same size: three of
  the five verdicts pass through byte-identical.

Why this lives in `harness/` and not under `src/personalclaw/`: `harness` is a repo-root dev
package that is deliberately NOT in the shipped wheel (`harness/README.md`), so a module under
`src/` importing it would strand an import at install time — and `mypy`'s
`ignore_missing_imports` would not catch it. The consequence is a design rule with teeth: the
verdict is computed HERE, by the runner, and **written into the report artifact**. The gateway
and the dashboard only ever *read* a verdict string. A surface that cannot recompute a verdict
cannot invent one, which is why a report with no verdict renders as "not measured" rather than
as a zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harness.fanout_measure import (
    INCONCLUSIVE_BAND_POINTS,
    MIN_TRIALS_PER_ARM,
    TOKEN_MATCH_TOLERANCE,
    VERDICT_FANOUT_WINS,
    VERDICT_INCONCLUSIVE,
    VERDICT_INSUFFICIENT_TRIALS,
    VERDICT_NOT_TOKEN_MATCHED,
    VERDICT_SINGLE_WINS,
    Arm,
    Trial,
    compare,
)

__all__ = [
    "ARM_SKILLS_OFF",
    "ARM_SKILLS_ON",
    "INCONCLUSIVE_BAND_POINTS",
    "MIN_TRIALS_PER_ARM",
    "TOKEN_MATCH_TOLERANCE",
    "VERDICT_INCONCLUSIVE",
    "VERDICT_INSUFFICIENT_TRIALS",
    "VERDICT_NOT_TOKEN_MATCHED",
    "VERDICT_SKILLS_OFF_WINS",
    "VERDICT_SKILLS_ON_WINS",
    "VERDICTS",
    "Arm",
    "TaskVerdict",
    "Trial",
    "verdict_class",
    "verdict_task",
]

#: The treatment arm — the approved skill is available to surfacing.
ARM_SKILLS_ON = "skills_on"
#: The control arm — the same skill is suppressed in the child, and only there.
ARM_SKILLS_OFF = "skills_off"

#: The two directional verdicts, renamed off `fanout`/`single` and nothing else changed.
VERDICT_SKILLS_ON_WINS = "skills_on_wins"
VERDICT_SKILLS_OFF_WINS = "skills_off_wins"

#: `fanout_measure`'s verdict → this benchmark's. Three of five are identity: the
#: withheld verdicts are withheld for reasons that have nothing to do with which arm is
#: which, so renaming them would mint vocabulary for no gain.
_RELABEL: dict[str, str] = {
    VERDICT_FANOUT_WINS: VERDICT_SKILLS_ON_WINS,
    VERDICT_SINGLE_WINS: VERDICT_SKILLS_OFF_WINS,
    VERDICT_INCONCLUSIVE: VERDICT_INCONCLUSIVE,
    VERDICT_NOT_TOKEN_MATCHED: VERDICT_NOT_TOKEN_MATCHED,
    VERDICT_INSUFFICIENT_TRIALS: VERDICT_INSUFFICIENT_TRIALS,
}

VERDICTS: frozenset[str] = frozenset(_RELABEL.values())

#: The verdict classes V4 reproduction compares on (protocol §8). Two runs "land a verdict
#: of the same class" when their verdicts are equal as STRINGS — the classes are the verdicts.
#: Named separately so a future coarsening (e.g. collapsing the three withheld verdicts into
#: one class) is one edit here rather than a second definition of "same class" per caller.
VERDICT_CLASS = {v: v for v in VERDICTS}


def verdict_class(verdict: str) -> str:
    """The reproduction class of a verdict string. Unknown verdicts map to themselves,
    so an unrecognised value never silently compares EQUAL to a recognised one."""
    return VERDICT_CLASS.get(verdict, verdict)


@dataclass(frozen=True)
class TaskVerdict:
    """One benchmark task's verdict, with everything needed to disbelieve it.

    `verdict` is `None` when the two arms could not be assembled at all — no cell scored,
    or an arm is missing. That is deliberately NOT one of the withheld verdicts: "we did not
    measure this" and "we measured it and withheld a direction" are different claims, and a
    reader that cannot tell them apart will read the first as the second.
    """

    task_id: str
    skill: str
    verdict: str | None = None
    reason: str = ""
    delta_points: float | None = None
    token_ratio: float | None = None
    #: Per-arm aggregates, straight off `Comparison.to_dict()["arms"]` (trials, mean_score,
    #: spread, tokens, tokens_per_point) — keyed by THIS module's arm names.
    arms: dict[str, dict] = field(default_factory=dict)
    #: Cells the matrix mapped to VERIFIER_ABSENT. §6 reports these as a count, never drops them.
    absent_cells: int = 0
    #: Total tool calls observed per arm (protocol §4's third metric, reachable since G3).
    tool_calls: dict[str, int] = field(default_factory=dict)
    #: `True` only when every contributing cell actually observed its own spend rows. A
    #: `token_ratio` computed over unobserved spend is not a token match, it is a guess.
    spend_observed: bool = False
    #: Carried from `AttemptRecord.estimated`: tokens are heuristic, not provider-reported (§4).
    spend_estimated: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "skill": self.skill,
            "verdict": self.verdict,
            "verdict_class": None if self.verdict is None else verdict_class(self.verdict),
            "reason": self.reason,
            "delta_points": self.delta_points,
            "token_ratio": self.token_ratio,
            "arms": {k: dict(v) for k, v in self.arms.items()},
            "absent_cells": self.absent_cells,
            "tool_calls": dict(self.tool_calls),
            "spend_observed": self.spend_observed,
            "spend_estimated": self.spend_estimated,
            "notes": list(self.notes),
        }


def verdict_task(
    *,
    task_id: str,
    skill: str,
    on_trials: list[Trial],
    off_trials: list[Trial],
    absent_cells: int = 0,
    tool_calls: dict[str, int] | None = None,
    spend_observed: bool = False,
    spend_estimated: bool = False,
) -> TaskVerdict:
    """Verdict one task's two arms through `fanout_measure.compare`, then relabel.

    Refuses BEFORE calling `compare` when an arm has no trials at all — `compare` would
    return `insufficient_trials`, which reads as "we measured too little" when the truth is
    "we measured nothing". §6's absent-cell count is the honest report of that state.
    """
    tool_calls = dict(tool_calls or {})
    if not on_trials or not off_trials:
        empty = [
            name
            for name, trials in ((ARM_SKILLS_ON, on_trials), (ARM_SKILLS_OFF, off_trials))
            if not trials
        ]
        return TaskVerdict(
            task_id=task_id,
            skill=skill,
            verdict=None,
            reason=(
                # 🔑 `empty` holds ONE or TWO arm names, and one is the ordinary case: a paired
                # run where a single arm produced no scored cell. So `arm(s)` was wrong on its
                # commonest input — and this string is not a log line. The runner writes it into
                # the persisted report, `GET /api/evals/learning-benchmark` serves it as
                # `BenchmarkTaskRow.reason`, and `learning/BenchmarkPanel.tsx` renders it VERBATIM.
                f"arm{'s' if len(empty) != 1 else ''} {', '.join(empty)} produced no scored "
                "cell — this task was not measured, which is not a tie and not a zero delta"
            ),
            absent_cells=absent_cells,
            tool_calls=tool_calls,
            spend_observed=spend_observed,
            spend_estimated=spend_estimated,
        )

    on_arm = Arm(name=ARM_SKILLS_ON, trials=list(on_trials))
    off_arm = Arm(name=ARM_SKILLS_OFF, trials=list(off_trials))
    # `compare(work, a, b)` treats its second argument as the arm a positive delta favours.
    # Passing the treatment arm there is what makes a positive delta mean "the skill helped".
    comparison = compare(task_id, on_arm, off_arm)
    payload = comparison.to_dict()
    notes = list(payload.get("notes") or [])
    if not spend_observed:
        notes.append(
            "spend was NOT observed for every contributing cell, so the token ratio is not "
            "evidence of a token match"
        )
    elif spend_estimated:
        notes.append("tokens and dollars are ESTIMATED, not provider-reported")
    return TaskVerdict(
        task_id=task_id,
        skill=skill,
        verdict=_RELABEL.get(comparison.verdict, comparison.verdict),
        delta_points=payload.get("delta_points"),
        token_ratio=payload.get("token_ratio"),
        arms=dict(payload.get("arms") or {}),
        absent_cells=absent_cells,
        tool_calls=tool_calls,
        spend_observed=spend_observed,
        spend_estimated=spend_estimated,
        notes=notes,
    )
