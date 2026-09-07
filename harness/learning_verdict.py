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

#: Words that only mean something in a FAN-OUT comparison. `compare()` writes its `notes` in that
#: vocabulary, and the verdict relabel above never touched them — so a measured skills report
#: published this, verbatim, about a suppressed SKILL:
#:
#:     "token spend differs by 13.7% (fanout 42517 vs single 37409) … give the cheaper arm more
#:      budget (more single-agent samples, or a wider fan-out) and re-measure; the largest published
#:      fan-out win was ~3.75x tokens…"
#:
#: §8 publishes the verdict WITH its notes, so that sentence is not a log line — it is part of the
#: published result, and it points a reader at a lever this experiment does not have. A note
#: carrying any of these is REPLACED by :func:`_arm_note`, not forwarded.
#:
#: Detected by VOCABULARY rather than by matching `compare()`'s exact prose: an upstream wording
#: change would silently stop matching a prose-keyed rule, and would still be caught by this one
#: (and by the test that asserts no published note contains any of these words).
_FANOUT_VOCABULARY: tuple[str, ...] = ("fanout", "fan-out", "single-agent", "topology")

#: NOT in the list above, deliberately: the inconclusive-band note's parenthetical "scorer swaps
#: move scores further than architecture does" is the literature citation that JUSTIFIES the
#: 5-point band this module imports. Rewording a citation to suit a different experiment would
#: misquote it, and the band is not this experiment's to restate. It passes through as written.


def _has_fanout_vocabulary(note: str) -> bool:
    lowered = note.lower()
    return any(word in lowered for word in _FANOUT_VOCABULARY)


def _arm_note(verdict: str, payload: dict) -> str:
    """The skills-arm sentence for a withheld verdict, from the numbers `compare()` already
    computed. Re-derives NOTHING: the verdict, the delta, the ratio and the per-arm aggregates are
    all read off `payload`. Only the prose is this module's.
    """
    arms = payload.get("arms") or {}
    on = arms.get(ARM_SKILLS_ON) or {}
    off = arms.get(ARM_SKILLS_OFF) or {}
    if verdict == VERDICT_NOT_TOKEN_MATCHED:
        on_tokens = int(on.get("tokens") or 0)
        off_tokens = int(off.get("tokens") or 0)
        if not on_tokens or not off_tokens:
            return (
                f"an arm spent zero tokens ({ARM_SKILLS_ON} {on_tokens}, {ARM_SKILLS_OFF} "
                f"{off_tokens}) — a comparison against an arm that called no model measures "
                "nothing about the skill"
            )
        # From the integer TOTALS, not from the payload's rounded `token_ratio`: `compare()`
        # computes the percentage off `fanout.tokens / single.tokens`, and deriving it from the
        # 4-decimal ratio instead moves the last digit (13.6% became 13.7% on one real pair). Two
        # published numbers about the same spend must not disagree, even by a tenth.
        drift = f"{abs(on_tokens / off_tokens - 1.0) * 100:.1f}% "
        return (
            f"token spend differs by {drift}({ARM_SKILLS_ON} {on_tokens} vs {ARM_SKILLS_OFF} "
            f"{off_tokens}), over the {TOKEN_MATCH_TOLERANCE * 100:.0f}% match tolerance — the "
            "arms are not spend-matched, so no direction is offered. This is the measurement "
            "declining a question it did not ask, NOT a finding that the skill does not help"
        )
    return (
        f"|delta| {abs(float(payload.get('delta_points') or 0.0)):.2f} clears the "
        f"{INCONCLUSIVE_BAND_POINTS}-point band, but within-arm spread is "
        f"{max(float(on.get('spread') or 0.0), float(off.get('spread') or 0.0)):.2f} points "
        f"({ARM_SKILLS_ON} {float(on.get('spread') or 0.0):.2f}, {ARM_SKILLS_OFF} "
        f"{float(off.get('spread') or 0.0):.2f}) — a delta smaller than the variance it sits in "
        "is unresolved; add trials or reduce per-trial nondeterminism before claiming a direction"
    )


def _imbalance_note(payload: dict) -> str:
    """The note an UNEQUAL pair needs, or ``""``. Measured, not hypothetical.

    `sk_task_project` in the 2026-09-07 run lost two `skills_on` cells to `VERIFIER_ABSENT` and
    was verdicted on 3 trials against 5. `compare()` divides the arms' token TOTALS, so the ratio
    came out `0.5139` — a 48.6% "spend difference" that is mostly two missing trials, not a
    per-trial difference (8,099/trial vs 9,456/trial, a ratio of 0.857).

    §6 already says an absent cell is reported as a count and never counted as a skills-off win.
    It does not say the surviving arms stay COMPARABLE, and over unequal trial counts they do not.
    So the imbalance is stated beside the ratio rather than left for a reader to spot in the
    `trials` column, and it is emitted whatever the verdict, because it is a property of the pair
    and not of the verdict.

    The ratio is not silently corrected here: normalising it would change a number `compare()`
    produced, which is the owner's module and this one's whole contract is not to.
    """
    arms = payload.get("arms") or {}
    on = int((arms.get(ARM_SKILLS_ON) or {}).get("trials") or 0)
    off = int((arms.get(ARM_SKILLS_OFF) or {}).get("trials") or 0)
    if on == off:
        return ""
    return (
        f"the arms are UNEQUAL — {ARM_SKILLS_ON} {on} trial(s) against {ARM_SKILLS_OFF} {off}, so "
        "the token ratio above is computed over unequal totals and is not a per-trial comparison. "
        "Re-run the missing trials before reading the ratio as a spend difference"
    )


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
    # Forward every upstream note that is about THIS experiment; replace the ones written in
    # fan-out vocabulary with the same numbers in skills-arm words. See `_FANOUT_VOCABULARY`.
    notes = [n for n in (payload.get("notes") or []) if not _has_fanout_vocabulary(str(n))]
    if len(notes) != len(payload.get("notes") or []):
        notes.append(_arm_note(str(payload.get("verdict") or ""), payload))
    imbalance = _imbalance_note(payload)
    if imbalance:
        notes.append(imbalance)
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
