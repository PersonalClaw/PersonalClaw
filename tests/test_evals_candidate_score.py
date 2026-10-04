"""Scoring an optimize-harness candidate against its target's own runs.

The scorer is the only source of a candidate's score, so each way it could quietly produce a
number it did not measure is railed: a case whose arm failed, an empty answer and a verdict the
judge could not read are REJECTED rather than scored 0; a score resting on fewer judged runs than a
filed diff needs is no score; a ceiling refusing a call stops the scoring and says so; an edit that
does not apply is said as that, before any model call. What scoring cost is read from the calls it
made, and is unknown, not free, when one of them had no price.

The composition is injected (a completion and a judge), as the replay harness injects its own: the
model calls themselves, their guard and their price are driven end to end in
``tests/test_optimize_harness_runs_to_its_proposal.py``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from personalclaw.evals import candidate_score
from personalclaw.evals.candidate_score import CandidateScore, CannotScore
from personalclaw.guardrails import calls as calls_mod
from personalclaw.guardrails.failure import BudgetExceededError

TARGET = "code-project"

#: The candidate's edit: the target's handoff prompt, bound to each recorded run's task.
OPS = [
    {
        "op": "update_node",
        "node_id": "handoff",
        "fields": {"prompt": "Summarize {{inputs.task}} for review."},
    }
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "pc-home"
    root.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    return root


def _seed(count: int) -> list[str]:
    """*count* finished runs of the target, the oldest first, with distinct creation times."""
    from personalclaw.workflows import journal as journal_mod
    from personalclaw.workflows import store
    from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

    ids: list[str] = []
    for i in range(count):
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=TARGET,
                inputs={"task": f"case-{i + 1}"},
                created_at=f"2026-01-0{i + 1}T00:00:00Z",
            )
        )
        run.status = RunStatus.COMPLETE
        store.save(run)
        journal = journal_mod.Journal(run.id)
        journal.run_started(TARGET, inputs={"task": f"case-{i + 1}"}, spec_version=1)
        journal.step_completed(
            "root.children[0]", "init", epoch=1, cache_key=f"ck{i}", state=InstanceState.DONE
        )
        journal.run_finished(RunStatus.COMPLETE.value, elapsed_secs=1.0, tokens=1)
        ids.append(run.id)
    return ids


# ── the cases, and the definition ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("runs", "found"), [(0, "no run"), (1, "1 run"), (2, "2 runs")], ids=["none", "one", "two"]
)
def test_a_target_with_too_few_finished_runs_cannot_be_scored(home, runs, found) -> None:
    _seed(runs)
    with pytest.raises(CannotScore) as exc:
        candidate_score.load_cases(TARGET)
    assert str(exc.value) == (
        f"optimize-harness cannot score a candidate for {TARGET}: it found {found} of {TARGET} "
        "that ended to score against, and a score must rest on at least 3 runs that ended"
    )


def test_the_cases_are_the_newest_finished_runs_up_to_the_limit(home) -> None:
    ids = _seed(4)
    cases = candidate_score.load_cases(TARGET, limit=3)
    assert candidate_score.case_run_ids(cases) == list(reversed(ids))[:3]
    assert [c["harvest"]["inputs"] for c in cases] == [
        {"task": "case-4"},
        {"task": "case-3"},
        {"task": "case-2"},
    ]


def test_a_template_that_is_not_installed_cannot_be_scored() -> None:
    with pytest.raises(CannotScore) as exc:
        asyncio.run(candidate_score.live_definition("no-such-template"))
    assert "no workflow template of that name is installed" in str(exc.value)


def test_the_evidence_is_each_case_s_run_once_in_order() -> None:
    cases: list[dict[str, Any]] = [
        {"harvest": {"run_id": "b"}},
        {"harvest": {"run_id": "a"}},
        {"harvest": {"run_id": "b"}},
        {"harvest": "not a block"},
        {},
    ]
    assert candidate_score.case_run_ids(cases) == ["b", "a"]


# ── a score read back is a measurement or nothing ────────────────────────────


@pytest.mark.parametrize("raw", ["nope", True, None, [0.5], {"v": 1}], ids=repr)
def test_an_unreadable_score_reads_back_as_no_score(raw) -> None:
    assert CandidateScore.from_dict({"score": raw, "spent_usd": raw}).score is None
    assert CandidateScore.from_dict({"score": raw, "spent_usd": raw}).spent_usd is None


def test_a_score_reads_back_as_the_number_it_was() -> None:
    back = CandidateScore.from_dict(
        CandidateScore(score=0.5, passed=1, judged=2, cases=3, spent_usd=0.3).to_dict()
    )
    assert (back.score, back.passed, back.judged, back.cases, back.spent_usd) == (
        0.5,
        1,
        2,
        3,
        0.3,
    )
    assert CandidateScore.from_dict("not a record").scored is False
    assert CandidateScore.from_dict({"score": "0.25", "passed": -2}).passed == 0
    assert CandidateScore.from_dict({"budget_stopped": "yes"}).budget_stopped is False


# ── scoring ──────────────────────────────────────────────────────────────────


class _Verdict:
    def __init__(self, score: float, reason: str = "") -> None:
        self.score = score
        self.reason = reason


class _Judge:
    """Passes an answer that says PASS, fails one that says FAIL, and cannot read the rest."""

    pass_threshold = 3.0

    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.start_error = start_error
        self.started = self.stopped = 0
        self.judged: list[tuple[str, str]] = []

    async def start(self) -> None:
        self.started += 1
        if self.start_error is not None:
            raise self.start_error

    async def shutdown(self) -> None:
        self.stopped += 1

    async def judge_turn(self, description: str, criteria: str, prompt: str, answer: str):
        self.judged.append((criteria, answer))
        if answer.startswith("PASS"):
            return _Verdict(4.0, "it does what was asked")
        if answer.startswith("FAIL"):
            return _Verdict(1.0, "it does not")
        return _Verdict(0.0, "parse_error: the reply was not JSON")


def _completion(answers: dict[int, Any], *, cost: float | None = 0.05, made: list | None = None):
    """Answers the arm of case-<i> with ``answers[i]``: text, or an exception it raises.

    Each call is recorded on the bound call logs at *cost*, as the model-call guard records one;
    ``cost=None`` records an unpriced call."""

    async def complete(prompt: str, *, use_case: str, usage: Any) -> str:
        case = int(re.search(r"case-(\d+)", prompt).group(1))  # type: ignore[union-attr]
        if made is not None:
            made.append((case, use_case, usage))
        call = calls_mod.open_call("scripted", "scripted-1", temperature=None)
        if call is not None:
            call.state = calls_mod.DONE
            call.usage_reported = True
            call.cost_usd = cost or 0.0
            call.priced = cost is not None
        answer = answers[case]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return complete


async def _score(home: Path, answers: dict[int, Any], *, judge: _Judge, runs: int = 3, **kw):
    _seed(runs)
    cases = candidate_score.load_cases(TARGET)
    return await candidate_score.score_candidate(
        subject=TARGET,
        ops=kw.pop("ops", OPS),
        cases=cases,
        usage="the-search",
        completion=kw.pop("completion", None) or _completion(answers, **kw),
        judge_factory=lambda: judge,
    )


@pytest.mark.anyio
async def test_the_score_is_the_share_of_judged_runs_that_passed(home) -> None:
    judge = _Judge()
    made: list = []
    result = await _score(home, {1: "PASS", 2: "FAIL", 3: "PASS"}, judge=judge, made=made)
    assert (result.score, result.passed, result.judged, result.rejected, result.cases) == (
        0.666667,
        2,
        3,
        0,
        3,
    )
    assert not result.budget_stopped and result.reason == ""
    # Every case's arm ran on the arm's use case, booked to the caller, and the judge was asked
    # the criterion the harvest wrote for the case.
    assert sorted(case for case, _uc, _u in made) == [1, 2, 3]
    assert {(uc, u) for _c, uc, u in made} == {("reasoning", "the-search")}
    assert all("recorded request" in criteria for criteria, _a in judge.judged)
    assert (judge.started, judge.stopped) == (1, 1)


@pytest.mark.anyio
async def test_a_case_whose_arm_failed_is_rejected_not_scored_zero(home) -> None:
    """🔴 A failed arm scored 0 would be evidence against the candidate that nothing measured."""
    answers = {1: "PASS", 2: RuntimeError("the model went away"), 3: "PASS", 4: "PASS"}
    result = await _score(home, answers, judge=_Judge(), runs=4)
    assert (result.passed, result.judged, result.rejected) == (3, 3, 1)
    assert result.score == 1.0


@pytest.mark.anyio
async def test_an_empty_answer_and_an_unreadable_verdict_are_rejected(home) -> None:
    answers = {1: "PASS", 2: "   ", 3: "the judge cannot read this", 4: "FAIL", 5: "PASS"}
    result = await _score(home, answers, judge=_Judge(), runs=5)
    assert (result.passed, result.judged, result.rejected) == (2, 3, 2)
    assert result.score == 0.666667


@pytest.mark.anyio
async def test_a_score_on_fewer_judged_runs_than_a_filed_diff_needs_is_no_score(home) -> None:
    result = await _score(home, {1: "PASS", 2: "PASS", 3: "garbled"}, judge=_Judge())
    assert result.score is None and not result.scored
    assert (result.passed, result.judged, result.rejected) == (2, 2, 1)
    assert result.reason == (
        "the judge could score 2 of the 3 runs it was given, and a score must rest on at least 3"
    )


@pytest.mark.anyio
async def test_a_refused_call_stops_the_scoring_and_says_the_budget_stopped_it(home) -> None:
    """The ceiling the search bound refuses a call: every later call would be refused the same way,
    so no further call is made, and the result says what stopped it."""
    refused = BudgetExceededError("run", "dollars", 1.0, 1.0)
    made: list = []
    judge = _Judge()
    result = await _score(home, {3: "PASS", 2: refused, 1: "PASS"}, judge=judge, made=made)
    assert result.budget_stopped is True and result.score is None
    assert result.reason == (
        "the next model call was refused before it was made: the per-run dollar budget is spent "
        "($1.00 of $1.00)"
    )
    assert len(made) == 2, "a call was made after the ceiling refused one"
    assert judge.stopped == 1, "the judge was left running"


@pytest.mark.anyio
async def test_an_edit_that_does_not_apply_is_said_as_that_before_any_call(home) -> None:
    made: list = []
    ops = [{"op": "update_node", "node_id": "no-such-node", "fields": {"prompt": "x"}}]
    result = await _score(home, {}, judge=_Judge(), ops=ops, made=made)
    assert result.score is None
    assert result.reason.startswith(f"its edit does not apply to {TARGET}: "), result.reason
    assert made == []


@pytest.mark.anyio
async def test_a_judge_that_cannot_start_scores_nothing(home) -> None:
    made: list = []
    judge = _Judge(start_error=RuntimeError("no judge model is set"))
    result = await _score(home, {1: "PASS", 2: "PASS", 3: "PASS"}, judge=judge, made=made)
    assert result.score is None
    assert result.reason == "the judge could not start: no judge model is set"
    assert made == []


@pytest.mark.anyio
async def test_what_scoring_cost_is_read_from_the_calls_it_made(home) -> None:
    result = await _score(home, {1: "PASS", 2: "FAIL", 3: "PASS"}, judge=_Judge(), cost=0.05)
    assert result.spent_usd == pytest.approx(0.15)


@pytest.mark.anyio
async def test_an_unpriced_call_makes_what_scoring_cost_unknown_not_free(home) -> None:
    result = await _score(home, {1: "PASS", 2: "FAIL", 3: "PASS"}, judge=_Judge(), cost=None)
    assert result.score == 0.666667
    assert result.spent_usd is None


def test_the_judge_books_its_calls_to_the_caller() -> None:
    """The judge's verdicts are the search's spend, so they are booked where the search reads its
    spend back from; the eval CLI's own judging keeps its default."""
    from personalclaw.eval.judge import LLMJudge
    from personalclaw.usage_ledger import Attribution

    usage = Attribution(source="eval", session_key="workflow:run-1:score")
    judge = candidate_score._judge_factory(usage)
    assert isinstance(judge, LLMJudge) and judge._usage is usage
    assert LLMJudge(lambda _key: None)._usage is None
