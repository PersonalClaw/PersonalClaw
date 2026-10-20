"""Scoring one candidate of an optimize-harness search against its target's own runs.

The search proposes an edit to a workflow template; this measures it, with the product's own
evaluation rather than a second one:

* **The cases are the target's own runs.** :func:`load_cases` turns the target template's runs
  that ended into harvested cases (:func:`personalclaw.evals.harvest.harvest`, writing nothing):
  each carries the run's redacted inputs and the criterion the harvest states for it, "carry out
  the recorded request at least as well as the recorded run did".
* **The candidate is the live definition with its ops applied**, through the engine's own
  ``mutations.apply_batch`` (:func:`personalclaw.evals.study_arms.arm_bodies_for_ops`), on a copy:
  nothing is written, and an edit that does not apply is said as that instead of being scored.
* **One arm per case**, as a template study runs one (:mod:`personalclaw.evals.study_arms`): the
  candidate's prompts bound to the case's recorded inputs and answered by one model call.
* **The eval judge decides each case** (:class:`personalclaw.eval.judge.LLMJudge`): the case
  passes when the judge scores the answer at or above its pass mark, the rule
  ``personalclaw eval --judge`` applies. A case whose arm fails, or whose verdict cannot be read,
  is rejected and never scored ``0`` (the replay harness's rule,
  :mod:`personalclaw.learning.replay`): a broken judge is not evidence against the candidate.

The score is the share of the judged cases that passed, and it is a measurement only when it rests
on at least :data:`MIN_CASES` judged runs: the evidence a template diff must carry to be filed
(``refiner.MIN_RUNS_FOR_EVIDENCE``). Below that the score is ``None`` and the reason says why,
because a pass rate over one run is an anecdote, and a winner scored on fewer runs could never be
filed.

Every model call made here is guarded (``one_shot_completion``, and the judge through the same
bridge), so it is charged and admitted like any other call: a caller that holds the calls to a
ceiling (``optimize.held_to``) has each one refused before it would pass, and the result then says
the budget stopped it. What scoring cost is measured from the calls themselves
(:func:`personalclaw.guardrails.calls.capture_model_calls`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

logger = logging.getLogger(__name__)

#: How many of the target's runs a candidate is scored against, the newest first. Each costs two
#: model calls per candidate (its arm and the judge's verdict on it), so this bounds what scoring a
#: candidate can cost, and every candidate of one search is scored against the same runs.
MAX_CASES = 5


#: The fewest judged runs a score may rest on: the refiner's evidence floor
#: (``refiner.MIN_RUNS_FOR_EVIDENCE``), kept equal to it on purpose and railed in
#: ``tests/test_optimize_harness_runs_to_its_proposal.py``, because a winner scored on fewer runs
#: than a filed diff needs could never be filed.
MIN_CASES = 3


class CannotScore(ValueError):
    """No candidate of this target can be scored: its definition or its runs are missing.

    Raised before any model call, so a search that could only ever produce unscored candidates is
    refused before it spends anything. The message is a sentence a person acts on.
    """


@dataclass(frozen=True)
class CandidateScore:
    """What scoring one candidate measured, and what it cost.

    ``score`` is ``None`` whenever the candidate was not measured, and ``reason`` then says why in a
    clause: its edit did not apply, too few runs could be judged, or the budget stopped it
    (``budget_stopped``). ``spent_usd`` is what its model calls cost, ``None`` when some of them had
    no price.
    """

    score: float | None
    reason: str = ""
    passed: int = 0
    judged: int = 0
    rejected: int = 0
    cases: int = 0
    spent_usd: float | None = 0.0
    budget_stopped: bool = False

    @property
    def scored(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reason": self.reason,
            "passed": self.passed,
            "judged": self.judged,
            "rejected": self.rejected,
            "cases": self.cases,
            "spent_usd": self.spent_usd,
            "budget_stopped": self.budget_stopped,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> CandidateScore:
        """A score read back from a step's output. Anything unreadable is an unmeasured score:
        a value that cannot be read must never become a number."""
        data = raw if isinstance(raw, dict) else {}
        score = data.get("score")
        spent = data.get("spent_usd")
        return cls(
            score=_number(score),
            reason=str(data.get("reason") or ""),
            passed=_count(data.get("passed")),
            judged=_count(data.get("judged")),
            rejected=_count(data.get("rejected")),
            cases=_count(data.get("cases")),
            spent_usd=_number(spent),
            budget_stopped=data.get("budget_stopped") is True,
        )


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _count(raw: Any) -> int:
    value = _number(raw)
    return max(0, int(value)) if value is not None else 0


def unmeasured(reason: str, *, cases: int = 0, budget_stopped: bool = False) -> CandidateScore:
    """A candidate that was not scored, and why, before any model call was made for it."""
    return CandidateScore(
        score=None, reason=reason, cases=cases, spent_usd=0.0, budget_stopped=budget_stopped
    )


# ── the cases and the definition ─────────────────────────────────────────────


def load_cases(subject: str, *, limit: int = MAX_CASES) -> list[dict[str, Any]]:
    """The target's runs that ended, as harvested cases, the newest first: at most *limit*.

    Built by the harvest and written nowhere: a search scores against the runs as the Run Ledger
    holds them when it starts, and leaves the scenario library as it found it. Raises
    :class:`CannotScore` when fewer than :data:`MIN_CASES` runs qualify.
    """
    from personalclaw.evals import harvest

    report = harvest.harvest(workflow_name=subject, write=False)
    cases = [case.scenario for case in report.cases][: max(0, int(limit))]
    if len(cases) < MIN_CASES:
        found = "no run" if not cases else ("1 run" if len(cases) == 1 else f"{len(cases)} runs")
        # One clause, the way a refused step's reason is read: the run's ending carries it on
        # (", so nothing after it ran") and says what to change.
        raise CannotScore(
            f"optimize-harness cannot score a candidate for {subject}: it found {found} of "
            f"{subject} that ended to score against, and a score must rest on at least "
            f"{MIN_CASES} runs that ended"
        )
    return cases


async def live_definition(subject: str) -> dict[str, Any]:
    """The target's stored definition, from every registered definition provider.

    Raises :class:`CannotScore` when no provider holds one: a candidate is the live definition
    with its edit applied, so without it there is nothing to score.
    """
    from personalclaw.evals import study_arms

    spec = await study_arms.live_spec(subject)
    if spec is None:
        raise CannotScore(
            f"optimize-harness cannot score a candidate for {subject}: no workflow template of "
            "that name is installed"
        )
    return spec


def case_run_ids(cases: Sequence[dict[str, Any]]) -> list[str]:
    """The run each case was harvested from: the evidence a filed winner names."""
    out: list[str] = []
    for case in cases:
        block = case.get("harvest") if isinstance(case, dict) else None
        run_id = str((block or {}).get("run_id") or "") if isinstance(block, dict) else ""
        if run_id and run_id not in out:
            out.append(run_id)
    return out


def _case_input(case: dict[str, Any]) -> str:
    """The case's recorded inputs as the arm renders them: the canonical JSON the study uses."""
    from personalclaw.evals import studies

    raw = case.get("harvest")
    block: dict[str, Any] = raw if isinstance(raw, dict) else {}
    inputs = block.get("inputs")
    return studies.canonical_json(inputs if isinstance(inputs, dict) else {})


def _criteria(case: dict[str, Any]) -> str:
    """What the judge is asked of the case: its judge assertion, as the harvest wrote it."""
    for session in case.get("sessions") or []:
        for turn in (session or {}).get("turns") or []:
            for assertion in (turn or {}).get("assertions") or []:
                if str((assertion or {}).get("type") or "") == "judge":
                    value = str(assertion.get("value") or "").strip()
                    if value:
                        return value
    return str(case.get("judge_criteria") or case.get("description") or "").strip()


# ── scoring ──────────────────────────────────────────────────────────────────


def _judge_factory(usage: Any) -> Any:
    """The eval judge, resolved through the same guarded bridge the arms use (the model itself,
    never an agent CLI) and booked to *usage*, so its calls are charged, held to a bound ceiling
    and counted as the caller's."""
    from personalclaw.eval.judge import LLMJudge
    from personalclaw.providers.provider_bridge import resolve_metered_model

    return LLMJudge(lambda _key: resolve_metered_model("reasoning"), usage=usage)


def _parse_failure(verdict: Any) -> bool:
    """Whether a verdict is the judge's unreadable-reply sentinel rather than a real zero."""
    return float(getattr(verdict, "score", 0.0) or 0.0) == 0.0 and str(
        getattr(verdict, "reason", "") or ""
    ).startswith("parse_error")


async def score_candidate(
    *,
    subject: str,
    ops: Sequence[dict[str, Any]],
    cases: Sequence[dict[str, Any]],
    usage: Any,
    spec: dict[str, Any] | None = None,
    completion: Any = None,
    judge_factory: Any = None,
) -> CandidateScore:
    """Score the candidate *ops* make of *subject* against *cases*. Never raises for a candidate.

    *usage* is whose spend the calls are (a :class:`~personalclaw.usage_ledger.Attribution`): a
    search books them to itself, so the spend it reads back holds them. *completion* and
    *judge_factory* are injected for tests as the whole composition, as the replay harness does; by
    default they are ``one_shot_completion`` and the eval judge.

    Refused calls are the one stop here: a ceiling the caller bound refusing a call
    (``BudgetExceededError``) ends the scoring at once, because every later call would be refused
    the same way, and the result says so (``budget_stopped``).
    """
    from personalclaw.evals import study_arms
    from personalclaw.guardrails.calls import capture_model_calls
    from personalclaw.guardrails.failure import BudgetExceededError

    total = len(cases)
    if spec is None:
        try:
            spec = await live_definition(subject)
        except CannotScore as exc:
            return unmeasured(str(exc), cases=total)
    try:
        _old, body = study_arms.arm_bodies_for_ops(spec, list(ops))
    except study_arms.StudyArmError as exc:
        return unmeasured(f"its edit does not apply to {subject}: {exc}", cases=total)

    if completion is None:
        from personalclaw.llm_helpers import one_shot_completion

        completion = one_shot_completion
    judge = judge_factory() if judge_factory is not None else _judge_factory(usage)

    passed = judged = rejected = 0
    stopped = ""
    with capture_model_calls() as calls:
        try:
            await judge.start()
        except BudgetExceededError as exc:
            return unmeasured(_stopped_clause(exc), cases=total, budget_stopped=True)
        except Exception as exc:  # noqa: BLE001 - a judge that cannot start scores nothing
            logger.warning("optimize-harness: the judge could not start", exc_info=True)
            return unmeasured(f"the judge could not start: {exc}", cases=total)
        try:
            for case in cases:
                prompt = study_arms.render_arm_prompt(body, case_input=_case_input(case))
                if not prompt:
                    rejected += 1
                    continue
                try:
                    answer = await completion(prompt, use_case=study_arms.ARM_USE_CASE, usage=usage)
                    if not str(answer or "").strip():
                        rejected += 1
                        continue
                    verdict = await judge.judge_turn(
                        str(case.get("description") or ""),
                        _criteria(case),
                        prompt,
                        str(answer),
                    )
                except BudgetExceededError as exc:
                    stopped = _stopped_clause(exc)
                    break
                except Exception:  # noqa: BLE001 - one failed case is a rejected case
                    logger.debug("optimize-harness: a case could not be scored", exc_info=True)
                    rejected += 1
                    continue
                if _parse_failure(verdict):
                    rejected += 1
                    continue
                judged += 1
                if float(verdict.score) >= float(judge.pass_threshold):
                    passed += 1
        finally:
            try:
                await judge.shutdown()
            except Exception:  # noqa: BLE001 - shutting the judge down never loses a score
                logger.debug("optimize-harness: judge shutdown failed", exc_info=True)

    measured = CandidateScore(
        score=None,
        passed=passed,
        judged=judged,
        rejected=rejected,
        cases=total,
        spent_usd=calls.cost_usd,
    )
    if stopped:
        return replace(measured, reason=stopped, budget_stopped=True)
    if judged < MIN_CASES:
        return replace(
            measured,
            reason=(
                f"the judge could score {judged} of the {total} runs it was given, and a score "
                f"must rest on at least {MIN_CASES}"
            ),
        )
    return replace(measured, score=round(passed / judged, 6))


def _stopped_clause(exc: Any) -> str:
    """The refusal that stopped the scoring, as the clause a person reads."""
    reason = getattr(exc, "reason", None)
    said = reason() if callable(reason) else str(exc)
    return f"the next model call was refused before it was made: {said}"


__all__ = [
    "CandidateScore",
    "CannotScore",
    "MAX_CASES",
    "MIN_CASES",
    "case_run_ids",
    "live_definition",
    "load_cases",
    "score_candidate",
    "unmeasured",
]
