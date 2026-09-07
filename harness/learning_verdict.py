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
* the one thing this module *declares* rather than inherits is the token gate's DENOMINATOR
  (`SPEND_BASIS`). It is not a threshold and not a check order — it is which spend the gate
  divides, and it has no design-free answer: the fan-out design matches budget by giving the
  cheaper arm more samples, so its arms' trial counts are unequal on purpose and TOTALS are
  right there. This is a paired design (§3: `k` trials per arm on identical work), so its
  totals are commensurable only while the counts match, and §6's absent cells are exactly
  what makes them not.

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
    SPEND_PER_TRIAL,
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
    "SPEND_BASIS",
    "TOKEN_MATCH_TOLERANCE",
    "UNRECORDED",
    "VERDICT_INCONCLUSIVE",
    "VERDICT_INSUFFICIENT_TRIALS",
    "VERDICT_NOT_TOKEN_MATCHED",
    "VERDICT_SKILLS_OFF_WINS",
    "VERDICT_SKILLS_ON_WINS",
    "VERDICT_TOKENS_UNRECORDED",
    "VERDICTS",
    "Arm",
    "IncommensurableSpendError",
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

#: The ONE word this repo uses for "this fact was never recorded", spelled here to match
#: `src/personalclaw/evals/provenance.py` and `web/src/lib/unrecorded.ts`. Not IMPORTED from there:
#: `harness` is a repo-root dev package outside the wheel and this module's whole point is that the
#: dependency runs one way. `tests/test_evals_unrecorded_vocabulary.py` reds on a second spelling.
UNRECORDED = "unrecorded"

#: The SIXTH verdict, and the one member of this vocabulary that `fanout_measure` has no
#: counterpart for — which is why the relabel map below cannot produce it.
#:
#: §5 offers a direction only when the two arms were spend-matched. When a contributing cell's
#: provider did not report its usage (#2540) the token gate has no denominator to divide, so no
#: direction may be offered — and none of the five existing verdicts can say that honestly:
#:
#: * `not_token_matched` asserts a comparison HAPPENED and came out unmatched. Here none happened.
#:   Widening it would make "the arms differ by 40%" and "we cannot tell" the same published
#:   string, and the second is the one a reader must not read as the first.
#: * a `verdict` of `None` means the arms could not be ASSEMBLED (no cell scored). Here they were:
#:   the scores are real and are published beside this verdict. Collapsing them would hide a
#:   measured score delta behind "not measured".
#:
#: So this is a new word rather than a widened one — the same call #2630 made about `priced`.
VERDICT_TOKENS_UNRECORDED = f"tokens_{UNRECORDED}"

#: The denominator the token gate divides for THIS design, and the only knob this module declares.
#: §3 runs `k` trials per arm on identical work, so per-trial and total spend are the same
#: comparison whenever the run is whole — they diverge in exactly one case, and it is §6's: an arm
#: that lost cells to `VERIFIER_ABSENT`. Over unequal counts a totals ratio is not a spend
#: comparison at all; it reports the missing attempts. MEASURED (`learnbench-20260907T003211Z`):
#: `sk_task_project` lost two `skills_on` cells, was verdicted on 3 trials against 5, and published
#: `token_ratio` 0.5139 as "a 48.6% spend difference" where the per-trial value is 0.8565, 14.3%.
#: Recomputing both runs of #2587 on this basis changes no verdict CLASS — it corrects a published
#: number, which is why it is a miscomputation fix and not a protocol edit.
SPEND_BASIS = SPEND_PER_TRIAL


class IncommensurableSpendError(RuntimeError):
    """The token gate ran on a basis this design cannot use.

    Raised rather than noted, and raised even though the line above it passes :data:`SPEND_BASIS`
    into `compare()`: `compare()`'s own default is TOTALS (correct for the fan-out design it
    belongs to), so an edit that drops the argument, or that flips :data:`SPEND_BASIS`, would
    silently restore the totals comparison and publish an "N% spend difference" that is mostly
    missing trials. §8 publishes the ratio beside the verdict, so a wrong ratio is read as evidence
    — a crash the tests catch beats a number a reader believes.

    Two bases are unusable, and this covers both. The second (#2540) is an UNRECORDED token count:
    `Trial.tokens` is an `int`, so a cell whose provider omitted its `usage` block arrives carrying
    a placeholder `0`, and a gate that divides it reports a spend match it never measured.
    """


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

#: This benchmark's closed verdict set: the five `fanout_measure` verdicts (two relabelled) plus
#: the one state that design has and fan-out does not — see :data:`VERDICT_TOKENS_UNRECORDED`.
VERDICTS: frozenset[str] = frozenset(_RELABEL.values()) | {VERDICT_TOKENS_UNRECORDED}

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
        on_n = int(on.get("trials") or 0)
        off_n = int(off.get("trials") or 0)
        if not on_tokens or not off_tokens or not on_n or not off_n:
            return (
                f"an arm spent zero tokens ({ARM_SKILLS_ON} {on_tokens}, {ARM_SKILLS_OFF} "
                f"{off_tokens}) — a comparison against an arm that called no model measures "
                "nothing about the skill"
            )
        # PER TRIAL, on `SPEND_BASIS`, and derived from the same quantity the gate divided rather
        # than from the payload's 4-decimal `token_ratio`: rounding the ratio first moves the last
        # digit (13.6% became 13.7% on one real pair), and two published numbers about the same
        # spend must not disagree, even by a tenth.
        on_rate = on_tokens / on_n
        off_rate = off_tokens / off_n
        drift = f"{abs(on_rate / off_rate - 1.0) * 100:.1f}% "
        return (
            f"token spend differs by {drift}per trial ({ARM_SKILLS_ON} {on_rate:.0f} over {on_n} "
            f"trial(s), {ARM_SKILLS_OFF} {off_rate:.0f} over {off_n}), over the "
            f"{TOKEN_MATCH_TOLERANCE * 100:.0f}% match tolerance — the arms are not spend-matched, "
            "so no direction is offered. This is the measurement declining a question it did not "
            "ask, NOT a finding that the skill does not help"
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
    was verdicted on 3 trials against 5. On TOTALS the ratio came out `0.5139` — a 48.6% "spend
    difference" that is mostly two missing trials rather than spend (8,099/trial against
    9,456/trial is 0.857). :data:`SPEND_BASIS` now divides per trial, so the published ratio is the
    per-trial one; what remains to say is that the pair is thin, because a per-trial ratio over 3
    trials is a weaker observation than the same ratio over 5 and the score delta beside it is no
    longer a paired comparison at all.

    §6 already says an absent cell is reported as a count and never counted as a skills-off win.
    It does not say the surviving arms stay comparable. Emitted whatever the verdict, because it is
    a property of the pair and not of the verdict.
    """
    arms = payload.get("arms") or {}
    on = int((arms.get(ARM_SKILLS_ON) or {}).get("trials") or 0)
    off = int((arms.get(ARM_SKILLS_OFF) or {}).get("trials") or 0)
    if on == off:
        return ""
    return (
        f"the arms are UNEQUAL — {ARM_SKILLS_ON} {on} trial(s) against {ARM_SKILLS_OFF} {off}. The "
        "token ratio above is a PER-TRIAL comparison, which is the only one unequal counts admit; "
        "over totals it would have reported the missing trial(s) as a spend difference. The score "
        "delta beside it is NOT a paired result — re-run the missing trial(s) before reading it"
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
    #: `False` when a contributing cell's PROVIDER did not report its usage (#2540) — a different
    #: fact from `spend_observed`, which is about whether the cell could read its own audit file
    #: at all. `spend_observed=True, tokens_recorded=False` is the exact state that published a
    #: silent zero: the cell ran, its rows were read, and the provider omitted the numbers.
    tokens_recorded: bool = True
    #: How many contributing cells that was. A count, so the absence is legible rather than only
    #: boolean, and so a reader can see it was one cell of ten rather than all ten.
    unrecorded_spend_cells: int = 0
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
            "tokens_recorded": self.tokens_recorded,
            "unrecorded_spend_cells": self.unrecorded_spend_cells,
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
    tokens_recorded: bool = True,
    unrecorded_spend_cells: int = 0,
) -> TaskVerdict:
    """Verdict one task's two arms through `fanout_measure.compare`, then relabel.

    The token gate runs on :data:`SPEND_BASIS` — per trial, because this is a paired design — and
    the basis is re-checked off the returned comparison rather than trusted from the call site.

    Refuses BEFORE calling `compare` when an arm has no trials at all — `compare` would
    return `insufficient_trials`, which reads as "we measured too little" when the truth is
    "we measured nothing". §6's absent-cell count is the honest report of that state.

    Refuses BEFORE calling `compare` a second time, and for the same kind of reason, when
    `tokens_recorded` is `False` (#2540): `Trial.tokens` is an `int` by construction, so a cell
    whose provider omitted its usage arrives here as a `0`, and `compare` would divide it as a
    measurement. §4's token ratio exists to answer whether the arms were spend-matched — averaging
    across cells that could not report is not a weaker answer to that question, it is not an
    answer — so the ratio REFUSES (`None`) and the verdict is
    :data:`VERDICT_TOKENS_UNRECORDED`. The score aggregates are still published, because they
    were measured.
    """
    tool_calls = dict(tool_calls or {})
    if not tokens_recorded and on_trials and off_trials:
        # The score aggregates come off `Arm`, which is `fanout_measure`'s own accessor set — the
        # means and spreads are not re-derived here. `.tokens` / `.spend()` / `token_ratio` are
        # the ones that would read the placeholder zeros, and none of them is touched: the arm
        # dicts below carry `None` where `compare()` would have carried a number.
        on_arm = Arm(name=ARM_SKILLS_ON, trials=list(on_trials))
        off_arm = Arm(name=ARM_SKILLS_OFF, trials=list(off_trials))
        return TaskVerdict(
            task_id=task_id,
            skill=skill,
            verdict=VERDICT_TOKENS_UNRECORDED,
            reason=(
                f"{unrecorded_spend_cells or 'one or more'} contributing cell(s) reported no token "
                "usage — their provider omitted it — so the arms' spend cannot be compared and no "
                "direction is offered. The scores below WERE measured; the token match was not"
            ),
            delta_points=round(on_arm.mean_score - off_arm.mean_score, 2),
            token_ratio=None,
            arms={
                arm.name: {
                    "trials": len(arm.trials),
                    "mean_score": round(arm.mean_score, 2),
                    "spread": round(arm.spread, 2),
                    "tokens": None,
                    "tokens_per_point": None,
                }
                for arm in (on_arm, off_arm)
            },
            absent_cells=absent_cells,
            tool_calls=tool_calls,
            spend_observed=spend_observed,
            spend_estimated=spend_estimated,
            tokens_recorded=False,
            unrecorded_spend_cells=unrecorded_spend_cells,
            notes=[
                "the token ratio is REFUSED, not zero: a ratio over cells that could not report "
                "their usage is not a weaker measurement of a spend match, it is not one"
            ],
        )
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
            tokens_recorded=tokens_recorded,
            unrecorded_spend_cells=unrecorded_spend_cells,
        )

    on_arm = Arm(name=ARM_SKILLS_ON, trials=list(on_trials))
    off_arm = Arm(name=ARM_SKILLS_OFF, trials=list(off_trials))
    # The SECOND rail, and it guards the same class of edit as the basis rail below. `Trial.tokens`
    # is an `int`, so a cell whose provider reported no usage arrives as a `0`; deleting the refusal
    # above would hand those zeros to `compare()` and publish a ratio over them. A crash the tests
    # catch beats a number a reader believes.
    if not tokens_recorded:
        raise IncommensurableSpendError(
            f"the token-match gate was reached for {task_id!r} with {unrecorded_spend_cells} "
            f"contributing cell(s) whose token usage is {UNRECORDED} — `Trial.tokens` carries a "
            "placeholder 0 for those, and dividing it publishes an absence as a spend match"
        )
    # `compare(work, a, b)` treats its second argument as the arm a positive delta favours.
    # Passing the treatment arm there is what makes a positive delta mean "the skill helped".
    comparison = compare(task_id, on_arm, off_arm, spend_basis=SPEND_BASIS)
    # The rail. See `IncommensurableSpendError`: `compare()`'s default basis is TOTALS, so this
    # cannot be left to the argument above staying where it is.
    if comparison.spend_basis != SPEND_PER_TRIAL:
        raise IncommensurableSpendError(
            f"the token-match gate ran on {comparison.spend_basis!r} for {task_id!r}, over "
            f"{len(on_arm.trials)} {ARM_SKILLS_ON} trial(s) against {len(off_arm.trials)} "
            f"{ARM_SKILLS_OFF} — a paired design's arms are only comparable per trial"
        )
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
        tokens_recorded=True,
        unrecorded_spend_cells=unrecorded_spend_cells,
        notes=notes,
    )
