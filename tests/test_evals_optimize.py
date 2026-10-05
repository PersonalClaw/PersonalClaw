"""The budgeted optimize-harness search.

The search's contract has six clauses, and most of them are the kind that reads as
satisfied while being quietly false, so each is railed with its own negative:

* **"nothing live mutates during the search"** is proven by OBSERVATION, not by asserting
  intent. Every end-to-end test hashes the live artifact tree itself — with this file's own
  ``_digest`` helper, deliberately not the module's :class:`LiveWitness`, so a broken witness
  cannot certify itself — before and after a real search. Two negatives make the observation
  non-vacuous: a scorer that mutates the live file raises, and a proposer that writes the live
  file and restores identical bytes is still caught (by the mtime/size snapshot, which is why
  there are two detectors rather than one).
* **the DUAL gate** gets one test per half, each holding the other half satisfied, plus a tie
  case, and its bar is shown to RISE: a later candidate that scores below an earlier winner is
  not admitted, in both drivers. A gate whose halves are only ever observed together could be
  admitting on one.
* **the frozen region** is asserted to REFUSE rather than record: the violating candidate is
  scored higher than everything else in its search and still is not the winner.
* **the halts** each get a firing test and a non-firing counterpart, so none of them is a
  condition that would fire on any input — the abandoned hypothesis among them, which fires
  only for a fix the gate kept refusing and says so.
* **the budget** is measured from the calls the search made, and the search stops before the
  cycle or scoring that would pass it.
* **the winner is a PROPOSAL**: the only filing path is ``refiner_tools.file_template_diff``,
  and nothing in this module can apply a template.
* **an unscored candidate reads as "unscored"** rather than as a ``0.0``. Railed on the files a
  real search wrote, and non-vacuously: the three states a score column can be in are compared
  as a SET, so two of them collapsing into one rendering is a red even though each state's own
  test would stay green.

The last group is the call-site half: the bundled template's ``bash`` nodes name subcommands
and ``PC_OPT_*`` env keys, and those names are asserted against the module's own tables. The
decisive question for those — would deleting the caller be caught — is yes in both directions:
renaming a subcommand fails the template's test, and dropping the template's env key fails the
coverage test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from personalclaw.evals import optimize
from personalclaw.evals.candidate_score import CandidateScore
from personalclaw.guardrails import calls as calls_mod
from personalclaw.workflows import scope as scope_mod

TEMPLATE = "optimize-harness"
TARGET = "code-project"


# ── fixtures + independent observation helpers ───────────────────────────────


def _digest(root: Path) -> dict[str, str]:
    """This file's OWN content hash of a tree.

    Deliberately not :func:`optimize.content_digest`: using the module's witness to prove the
    module's witness works is circular, and a witness that hashed nothing would pass such a
    test with an empty dict on both sides.
    """
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture
def live(tmp_path: Path) -> Path:
    """A live artifact directory with content and a lock file — the frozen region."""
    root = tmp_path / "live" / "code-project"
    root.mkdir(parents=True)
    (root / "workflow.json").write_text(json.dumps({"name": "code-project"}), encoding="utf-8")
    (root / optimize.LOCK_NAME).write_text(json.dumps({"hashes": {}}), encoding="utf-8")
    return root


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    d = tmp_path / "sandbox"
    d.mkdir()
    return d


@pytest.fixture
def isolated_home(tmp_path, monkeypatch) -> Path:
    """Redirect ``config_dir()`` at the ENV, which every binding of it re-reads per call.

    ``evals/store.py`` binds the ``config_dir`` FUNCTION at import, so patching the loader's
    attribute would be missed by it; the env var is the one lever both bindings honour. The
    redirect is asserted rather than assumed — a patch that did not take would let
    ``read_results`` touch the user's real ledger.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    from personalclaw.evals import store

    assert store.evals_root().is_relative_to(home), store.evals_root()
    return home


def _stops(**kw) -> optimize.StopConditions:
    base = {
        "budget_usd": 5.0,
        "hypothesis_abandon_after": 3,
        "no_improvement_halt": 5,
        "max_iterations": 8,
    }
    base.update(kw)
    return optimize.StopConditions(**base)  # type: ignore[arg-type]


def _spend(dollars: float) -> None:
    """A model call that cost *dollars*, recorded where the model-call guard records one."""
    call = calls_mod.open_call("scripted", "scripted-1", temperature=None)
    assert call is not None, "no call log is bound — the search did not measure its calls"
    call.state = calls_mod.DONE
    call.cost_usd = dollars
    call.priced = True
    call.usage_reported = True


def _measured(score: float) -> CandidateScore:
    return CandidateScore(score=score, passed=1, judged=3, cases=3, spent_usd=0.0)


def _proposer(scores: list[float], *, fingerprints: list[str] | None = None):
    """A deterministic proposer over a fixed score list, with a matching scorer."""
    prints = fingerprints or [f"fix-{i}" for i in range(len(scores))]

    async def propose(iteration: int, sandbox: Path, experience: list[dict]):
        idx = iteration - 1
        if idx >= len(scores):
            return None
        return optimize.Candidate(
            iteration=iteration,
            fix_fingerprint=prints[idx],
            diff_text=f"--- candidate {iteration}\n+++ score {scores[idx]}\n",
            ops=({"op": "update_node", "id": "audit", "prompt": f"v{iteration}"},),
            rationale=f"iteration {iteration}",
        )

    async def score(candidate: optimize.Candidate, cand_dir: Path):
        return _measured(scores[candidate.iteration - 1])

    return propose, score


def _scoring(value: float):
    async def score(candidate: optimize.Candidate, cand_dir: Path) -> CandidateScore:
        return _measured(value)

    return score


async def _nothing(*_a):
    return None


def _run(live: Path, sandbox: Path, **kw) -> optimize.SearchOutcome:
    defaults = {
        "subject": "code-project",
        "live_target": str(live),
        "sandbox": str(sandbox),
        "suite_threshold": 0.5,
        "stops": _stops(),
        "best_ever": optimize.BestEver(value=0.0, rows_considered=0, subject="code-project"),
    }
    defaults.update(kw)
    return asyncio.run(optimize.run_search(**defaults))  # type: ignore[arg-type]


# ── "nothing live mutates during the search" ─────────────────────────────────


class TestNothingLiveMutates:
    def test_a_completed_search_leaves_the_live_artifact_BYTE_IDENTICAL(
        self, live: Path, sandbox: Path
    ) -> None:
        """The headline clause, as an observation of before/after bytes.

        The proposer here *tries* to write the live artifact on its second iteration, so the
        search under test is one that had a candidate reach for the frozen region — a clean
        after-image from a search that never tried would prove nothing.
        """
        propose, score = _proposer([0.6, 0.7, 0.8])
        before = _digest(live)
        assert before, "the live fixture is empty — the comparison would be vacuous"

        async def grabby(iteration: int, sb: Path, experience: list[dict]):
            if iteration == 2:
                (live / "workflow.json").write_text("HIJACKED", encoding="utf-8")
                (live / "workflow.json").write_text(
                    json.dumps({"name": "code-project"}), encoding="utf-8"
                )
            return await propose(iteration, sb, experience)

        outcome = _run(live, sandbox, propose=grabby, score=score)

        assert outcome.halt_reason in tuple(optimize.HaltReason)
        assert _digest(live) == before

    def test_a_scorer_that_MUTATES_the_live_artifact_is_caught(
        self, live: Path, sandbox: Path
    ) -> None:
        """The vacuity proof for the witness: it can fail, and it fails loudly.

        Without this, ``test_..._BYTE_IDENTICAL`` above would also pass against a witness that
        compared an empty dict to an empty dict.
        """
        propose, _score = _proposer([0.9])

        async def sabotage(candidate: optimize.Candidate, cand_dir: Path):
            (live / "workflow.json").write_text("permanently changed", encoding="utf-8")
            return _measured(0.9)

        with pytest.raises(optimize.LiveMutationError) as exc:
            _run(live, sandbox, propose=propose, score=sabotage)
        assert "workflow.json" in str(exc.value)

    def test_a_write_then_RESTORE_is_still_a_scope_violation(
        self, live: Path, sandbox: Path
    ) -> None:
        """ "Wrote into the live tree and put it back" is not "nothing mutated".

        Restoring identical bytes defeats a content hash by construction, which is exactly why
        the search also brackets each iteration with the engine's mtime/size snapshot. This
        test is the only one that distinguishes the two detectors.
        """
        propose, score = _proposer([0.9])

        async def restoring(iteration: int, sb: Path, experience: list[dict]):
            path = live / "workflow.json"
            keep = path.read_text(encoding="utf-8")
            path.write_text("temporarily different", encoding="utf-8")
            path.write_text(keep, encoding="utf-8")
            return await propose(iteration, sb, experience)

        outcome = _run(live, sandbox, propose=restoring, score=score)
        assert [r.outcome for r in outcome.rows] == ["scope_violation"]
        assert outcome.winner is None

    def test_a_sandbox_inside_the_frozen_region_is_REFUSED_before_any_work(
        self, live: Path
    ) -> None:
        propose, score = _proposer([1.0])
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            _run(live, live / "candidates", propose=propose, score=score)
        assert "frozen region" in str(exc.value)


# ── the frozen region refuses rather than records ────────────────────────────


class TestFrozenRegion:
    def test_frozen_BEATS_allowed(self, tmp_path: Path) -> None:
        """A frozen path nested inside an allowed root is still a violation.

        The one rule ``allowed_write_paths`` cannot express on its own, and the one this
        module adds. Without it a search whose sandbox happened to contain the live artifact
        would report every escape as clean.
        """
        root = tmp_path / "ws"
        nested = root / "live"
        nested.mkdir(parents=True)
        before = scope_mod.snapshot([str(root)])
        (nested / "f.txt").write_text("x", encoding="utf-8")
        after = scope_mod.snapshot([str(root)])

        allowed_only = scope_mod.diff(before, after, [str(root)])
        assert allowed_only.clean, "precondition: plain allowed-scope diff sees no violation"

        verdict = optimize.scope_check(before, after, allowed=[str(root)], frozen=[str(nested)])
        assert verdict.violation
        assert verdict.outcome is optimize.CandidateOutcome.SCOPE_VIOLATION
        assert verdict.frozen_touched

    def test_an_incomplete_snapshot_is_not_read_as_clean(self, tmp_path: Path) -> None:
        """A truncated snapshot did not observe the whole tree; "no violations found" there is
        an absence of observation, not a pass."""
        before = scope_mod.Snapshot(entries={}, truncated=True)
        after = scope_mod.Snapshot(entries={}, truncated=False)
        assert optimize.scope_check(before, after, allowed=["/x"], frozen=[]).violation

    def test_a_violating_candidate_with_the_TOP_score_is_not_the_winner(
        self, live: Path, sandbox: Path
    ) -> None:
        """ "Dead regardless of score" — the whole clause. The violator scores 1.0 against a
        clean candidate's 0.6, and loses."""

        async def propose(iteration: int, sb: Path, experience: list[dict]):
            if iteration > 2:
                return None
            if iteration == 1:
                # Touch the live artifact and put its bytes back. The touch is the violation;
                # restoring keeps the SEARCH legal so the run reaches its end and the winner
                # can be inspected — a persisted change would (correctly) raise instead, which
                # is what `test_a_scorer_that_MUTATES_...` covers.
                path = live / "workflow.json"
                keep = path.read_text(encoding="utf-8")
                path.write_text("smuggled", encoding="utf-8")
                path.write_text(keep, encoding="utf-8")
            return optimize.Candidate(
                iteration=iteration,
                fix_fingerprint=f"fix-{iteration}",
                diff_text=f"diff {iteration}",
                ops=({"op": "update_node", "id": "audit"},),
            )

        async def score(candidate: optimize.Candidate, cand_dir: Path):
            return _measured(1.0 if candidate.iteration == 1 else 0.6)

        outcome = _run(live, sandbox, propose=propose, score=score)
        rows = {r.iteration: r for r in outcome.rows}
        assert rows[1].outcome == "scope_violation"
        assert rows[1].score == 0.0, "a violator is never scored — that is what makes it cheap"
        assert outcome.winner is not None and outcome.winner.iteration == 2
        assert outcome.winner_score == 0.6


# ── the dual gate, one half at a time, and its rising bar ────────────────────


class TestDualGate:
    def test_half_A_alone_cannot_admit(self) -> None:
        """Beats the best so far, fails the suite threshold → discarded."""
        gate = optimize.DualGate(
            suite_threshold=0.8, best_ever=optimize.BestEver(value=0.1, rows_considered=3)
        )
        assert gate.beats(0.5, 0.1), "precondition: half B is satisfied"
        assert not gate.clears_suite_threshold(0.5)
        assert gate.decide(0.5, best_so_far=0.1) is optimize.CandidateOutcome.BELOW_SUITE_THRESHOLD

    def test_half_B_alone_cannot_admit(self) -> None:
        """Clears the suite threshold, does not beat the best so far → discarded."""
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.9, rows_considered=3)
        )
        assert gate.clears_suite_threshold(0.7), "precondition: half A is satisfied"
        assert not gate.beats(0.7, 0.9)
        assert gate.decide(0.7, best_so_far=0.9) is optimize.CandidateOutcome.NOT_BEST_EVER

    def test_both_halves_admit(self) -> None:
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.6, rows_considered=1)
        )
        assert gate.decide(0.7, best_so_far=0.6) is optimize.CandidateOutcome.ADMITTED

    def test_a_TIE_with_the_best_so_far_loses(self) -> None:
        """Ties lose: hill-climbing on equal scores is how a search spends a budget wandering
        a plateau and then calls its last step a win."""
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.7, rows_considered=1)
        )
        assert gate.decide(0.7, best_so_far=0.7) is optimize.CandidateOutcome.NOT_BEST_EVER

    def test_the_threshold_itself_passes(self) -> None:
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.0, rows_considered=0)
        )
        assert gate.clears_suite_threshold(0.5)

    def test_the_bar_starts_at_the_floor_and_rises_only_with_admitted_candidates(self) -> None:
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.4, rows_considered=2)
        )
        row = optimize.LedgerRow
        assert gate.bar([]) == 0.4
        assert gate.bar([row(1, "admitted", 0.9), row(2, "below_suite_threshold", 0.95)]) == 0.9
        assert gate.bar([row(1, "not_best_ever", 0.3)]) == 0.4

    def test_a_later_candidate_that_scores_BELOW_the_winner_is_not_admitted(
        self, live: Path, sandbox: Path
    ) -> None:
        """🔴 Red before: the gate compared every candidate with the floor the search started
        from, so a later 0.85 was admitted after a 0.9, and the worse one became the winner."""
        propose, score = _proposer([0.9, 0.85])
        outcome = _run(live, sandbox, propose=propose, score=score)
        assert [r.outcome for r in outcome.rows] == ["admitted", "not_best_ever"]
        assert [r.best_so_far for r in outcome.rows] == [0.9, 0.9]
        assert outcome.winner is not None and outcome.winner.iteration == 1
        assert outcome.winner_score == 0.9

    def test_the_best_ever_floor_is_read_ONCE_and_does_not_follow_the_search(
        self, live: Path, sandbox: Path, monkeypatch
    ) -> None:
        """A floor recomputed from the rows the search is writing is pinned by the value it is
        meant to pin.

        Counted rather than argued: ``capture_best_ever`` is wrapped, the search runs three
        scored iterations, and the call count must be exactly one. What rises is the bar above
        the floor, and only with the candidates the gate admits.
        """
        calls: list[str] = []
        real = optimize.capture_best_ever

        def counting(subject: str, *, rows=None):
            calls.append(subject)
            return real(subject, rows=rows or [])

        monkeypatch.setattr(optimize, "capture_best_ever", counting)
        propose, score = _proposer([0.6, 0.7, 0.8])
        outcome = _run(live, sandbox, propose=propose, score=score, best_ever=None)

        assert calls == ["code-project"]
        assert outcome.gate["best_ever"] == 0.0
        assert len([r for r in outcome.rows if r.outcome == "admitted"]) == 3

    def test_capture_best_ever_ignores_rows_of_another_kind_or_subject(self) -> None:
        rows = [
            {"kind": optimize.SEARCH_KIND, "study_id": "code-project", "score_new": "0.4"},
            {"kind": optimize.SEARCH_KIND, "study_id": "other", "score_new": "0.99"},
            {"kind": "template_study", "study_id": "code-project", "score_new": "0.98"},
        ]
        best = optimize.capture_best_ever("code-project", rows=rows)
        assert (best.value, best.rows_considered) == (0.4, 1)

    def test_no_history_and_all_zeroes_are_distinguishable(self) -> None:
        """Both floors are 0.0 and they are completely different situations, which is what
        ``rows_considered`` exists to say."""
        empty = optimize.capture_best_ever("s", rows=[])
        zeroed = optimize.capture_best_ever(
            "s", rows=[{"kind": optimize.SEARCH_KIND, "study_id": "s", "score_new": "0"}]
        )
        assert empty.value == zeroed.value == 0.0
        assert (empty.rows_considered, zeroed.rows_considered) == (0, 1)

    def test_capture_reads_the_REAL_ledger_through_the_store(self, isolated_home: Path) -> None:
        """The default path (``rows=None``) goes through ``store.read_results``, against an
        isolated home. Without this the ledger read is only ever exercised with injected rows."""
        from personalclaw.evals import store

        path = store.results_path()
        # The ledger's folder, made as its writer makes it: naming the path creates nothing.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\t".join(store.RESULTS_COLUMNS)
            + "\n"
            + "\t".join(
                {
                    "study_id": "code-project",
                    "kind": optimize.SEARCH_KIND,
                    "score_new": "0.75",
                }.get(col, "")
                for col in store.RESULTS_COLUMNS
            )
            + "\n",
            encoding="utf-8",
        )
        assert optimize.capture_best_ever("code-project").value == 0.75


# ── the declared halts, each firing and each not ─────────────────────────────


class TestHalts:
    def test_hypothesis_abandon_after_HALTS(self, live: Path, sandbox: Path) -> None:
        propose, score = _proposer(
            [0.1, 0.1, 0.1, 0.1], fingerprints=["same", "same", "same", "same"]
        )
        outcome = _run(
            live, sandbox, propose=propose, score=score, stops=_stops(no_improvement_halt=99)
        )
        assert outcome.halt_reason is optimize.HaltReason.HYPOTHESIS_ABANDONED
        assert outcome.iterations == 3
        assert outcome.needs_from_human

    def test_a_repeated_fix_the_gate_ADMITS_is_not_abandoned(
        self, live: Path, sandbox: Path
    ) -> None:
        """🔴 Red before: the same fix three times halted the search as "failed" even when every
        try was admitted, so a search climbing on one good diagnosis was stopped mid-climb."""
        propose, score = _proposer([0.6, 0.7, 0.8, 0.9], fingerprints=["same"] * 4)
        outcome = _run(
            live, sandbox, propose=propose, score=score, stops=_stops(no_improvement_halt=99)
        )
        assert [r.outcome for r in outcome.rows] == ["admitted"] * 4
        assert outcome.halt_reason is optimize.HaltReason.PROPOSER_EXHAUSTED
        assert outcome.winner_score == 0.9

    def test_the_abandoned_hypothesis_says_what_happened(self, live: Path, sandbox: Path) -> None:
        """🔴 Red before: it said the fix "failed" whatever the gate had made of it. The streak
        is counted from the last admitted try, and the sentence says what the gate did."""
        propose, score = _proposer([0.9, 0.2, 0.3], fingerprints=["same"] * 3)
        outcome = _run(
            live,
            sandbox,
            propose=propose,
            score=score,
            stops=_stops(hypothesis_abandon_after=2, no_improvement_halt=99),
        )
        assert [r.outcome for r in outcome.rows] == ["admitted"] + ["below_suite_threshold"] * 2
        assert outcome.halt_reason is optimize.HaltReason.HYPOTHESIS_ABANDONED
        assert outcome.halt_detail == (
            "its last 2 attempts at a fix all tried the same one (same) and the gate admitted "
            "none of them, so the diagnosis behind it is wrong"
        )
        assert outcome.winner is not None and outcome.winner.iteration == 1

    def test_hypothesis_abandon_does_NOT_fire_on_an_alternating_proposer(
        self, live: Path, sandbox: Path
    ) -> None:
        """A proposer trying two fixes in turn is exploring. Abandoning it would be abandoning
        the search, not the hypothesis — so the detector must not fire on any repetition."""
        propose, score = _proposer([0.1] * 6, fingerprints=["a", "b", "a", "b", "a", "b"])
        outcome = _run(
            live, sandbox, propose=propose, score=score, stops=_stops(no_improvement_halt=99)
        )
        assert outcome.halt_reason is not optimize.HaltReason.HYPOTHESIS_ABANDONED

    def test_no_improvement_halt_HALTS(self, live: Path, sandbox: Path) -> None:
        """The clause the census found ABSENT from the tree. Its call site is the search's halt
        decision; deleting it leaves this search running to ``max_iterations`` with a different
        halt reason, which is what this asserts against."""
        propose, score = _proposer([0.1] * 8, fingerprints=[f"f{i}" for i in range(8)])
        outcome = _run(
            live,
            sandbox,
            propose=propose,
            score=score,
            stops=_stops(no_improvement_halt=3, hypothesis_abandon_after=99, max_iterations=8),
        )
        assert outcome.halt_reason is optimize.HaltReason.NO_IMPROVEMENT
        assert outcome.iterations == 3
        assert outcome.halt_detail == "the best score stayed at 0.00 across its last 3 candidates"
        assert outcome.needs_from_human

    def test_no_improvement_does_NOT_fire_on_a_climbing_search(
        self, live: Path, sandbox: Path
    ) -> None:
        propose, score = _proposer([0.6, 0.7, 0.8, 0.9])
        outcome = _run(
            live,
            sandbox,
            propose=propose,
            score=score,
            stops=_stops(no_improvement_halt=3, max_iterations=8),
        )
        assert outcome.halt_reason is optimize.HaltReason.PROPOSER_EXHAUSTED
        assert outcome.winner_score == 0.9

    def test_budget_usd_HALTS_before_the_cycle_that_would_pass_it(
        self, live: Path, sandbox: Path
    ) -> None:
        """🔴 Red before: the search charged a meter its scorer had to remember to charge, and
        checked it only once a cycle had already passed the ceiling. Now the spend is what the
        search's model calls cost, and a cycle that would pass the budget does not start."""
        propose, _score = _proposer([0.6] * 8, fingerprints=[f"f{i}" for i in range(8)])

        async def costly(candidate: optimize.Candidate, cand_dir: Path):
            _spend(0.4)
            return _measured(0.6)

        outcome = _run(
            live,
            sandbox,
            propose=propose,
            score=costly,
            stops=_stops(budget_usd=1.0, no_improvement_halt=99, hypothesis_abandon_after=99),
        )
        assert outcome.halt_reason is optimize.HaltReason.BUDGET_EXHAUSTED
        assert outcome.iterations == 2, "two cycles spend $0.80; a third would pass $1.00"
        assert outcome.spent_usd == pytest.approx(0.8)
        assert outcome.summary == (
            "The search stopped at its budget: it has spent $0.80 of its $1.00 budget, and a "
            "cycle of the search has cost up to $0.40, so another would pass it. It kept "
            "candidate 1, which scored 0.60."
        )
        assert outcome.needs_from_human == outcome.summary
        assert [r.score_usd for r in outcome.rows] == [0.4, 0.4]

    def test_budget_does_NOT_fire_when_the_ceiling_is_not_reached(
        self, live: Path, sandbox: Path
    ) -> None:
        propose, _score = _proposer([0.6, 0.7])

        async def cheap(candidate: optimize.Candidate, cand_dir: Path):
            _spend(0.01)
            return _measured(0.6 if candidate.iteration == 1 else 0.7)

        outcome = _run(live, sandbox, propose=propose, score=cheap, stops=_stops(budget_usd=1.0))
        assert outcome.halt_reason is not optimize.HaltReason.BUDGET_EXHAUSTED

    def test_a_scoring_that_would_pass_the_budget_does_not_start(
        self, live: Path, sandbox: Path
    ) -> None:
        """The check before a scoring, not only before a cycle: a proposer that spends enough to
        leave too little for the next scoring stops the search before that scoring runs."""
        scored: list[int] = []

        async def spender(iteration: int, sb: Path, experience: list[dict]):
            _spend(0.05 if iteration == 1 else 0.3)
            return optimize.Candidate(
                iteration=iteration, fix_fingerprint=f"f{iteration}", diff_text="+x"
            )

        async def score(candidate: optimize.Candidate, cand_dir: Path):
            scored.append(candidate.iteration)
            _spend(0.4)
            return _measured(0.6 + candidate.iteration / 100)

        outcome = _run(live, sandbox, propose=spender, score=score, stops=_stops(budget_usd=1.0))
        assert scored == [1], "the second scoring would have passed the budget"
        assert outcome.rows[-1].outcome == optimize.CandidateOutcome.NOT_SCORED.value
        assert outcome.halt_reason is optimize.HaltReason.BUDGET_EXHAUSTED
        assert outcome.halt_detail == (
            "it has spent $0.75 of its $1.00 budget, and scoring a candidate has cost up to "
            "$0.40, so another would pass it"
        )

    def test_an_UNBUDGETED_envelope_is_refused(self) -> None:
        """0 means UNLIMITED to :class:`Budget`, so it must not be a default here."""
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize.StopConditions(budget_usd=0.0).validate()
        assert "budget_usd" in str(exc.value)

    def test_more_candidates_than_the_ceiling_are_refused(self) -> None:
        """The template's loop is capped at the ceiling; a search asking for more would be cut
        short by the loop and handed to a person, so it is refused instead, saying the limit."""
        too_many = optimize.ITERATION_CEILING + 1
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize.StopConditions(budget_usd=1.0, max_iterations=too_many).validate()
        assert f"at most {optimize.ITERATION_CEILING} candidates" in str(exc.value)
        optimize.StopConditions(
            budget_usd=1.0, max_iterations=optimize.ITERATION_CEILING
        ).validate()

    def test_max_iterations_is_the_floor_under_the_other_three(
        self, live: Path, sandbox: Path
    ) -> None:
        propose, score = _proposer([0.1] * 20, fingerprints=[f"f{i}" for i in range(20)])
        outcome = _run(
            live,
            sandbox,
            propose=propose,
            score=score,
            stops=_stops(max_iterations=4, no_improvement_halt=99, hypothesis_abandon_after=99),
        )
        assert outcome.halt_reason is optimize.HaltReason.ITERATIONS_EXHAUSTED
        assert outcome.iterations == 4
        assert not outcome.needs_from_human, "a too-small envelope is not a question for a human"

    def test_a_zero_window_falls_back_rather_than_disabling_its_halt(self) -> None:
        """A declared halt with a window of 0 would be a halt that never fires. A template that
        named the halt asked for it, so the default wins over the disabling value."""
        stops = optimize.StopConditions.from_config(
            {"budget_usd": 1.0, "no_improvement_halt": 0, "hypothesis_abandon_after": -2}
        )
        assert stops.no_improvement_halt == optimize.DEFAULT_NO_IMPROVEMENT_HALT
        assert stops.hypothesis_abandon_after == optimize.DEFAULT_HYPOTHESIS_ABANDON_AFTER

    def test_the_detectors_never_fire_on_a_window_they_have_not_filled(self) -> None:
        failed = [("a", False), ("a", False)]
        assert not optimize.hypothesis_abandoned(failed, 3)
        assert optimize.hypothesis_abandoned([*failed, ("a", False)], 3)
        assert not optimize.hypothesis_abandoned([*failed, ("a", True)], 3)
        assert not optimize.hypothesis_abandoned([("", False)] * 3, 3), "a blank names no fix"
        assert not optimize.no_improvement([0.1, 0.2], 3)
        assert optimize.no_improvement([0.5, 0.5, 0.5], 3)
        assert not optimize.no_improvement([0.5, 0.5, 0.6], 3)


class TestTheBudgetCheck:
    """``budget_stop``: the one check both drivers ask before every cycle and scoring."""

    def test_it_lets_a_search_with_room_go_on(self) -> None:
        spend = optimize.Spend(dollars=0.4, cycles=(0.4,), scorings=(0.3,))
        assert optimize.budget_stop(spend, 1.0, before=optimize.BEFORE_CYCLE) == ""
        assert optimize.budget_stop(spend, 1.0, before=optimize.BEFORE_SCORING) == ""

    def test_it_stops_a_search_that_has_reached_its_budget(self) -> None:
        spend = optimize.Spend(dollars=1.0)
        assert optimize.budget_stop(spend, 1.0, before=optimize.BEFORE_CYCLE) == (
            "it has spent $1.00 of its $1.00 budget"
        )

    def test_it_stops_before_the_step_that_would_pass_it(self) -> None:
        spend = optimize.Spend(dollars=0.7, cycles=(0.2, 0.4), scorings=(0.25,))
        assert "a cycle of the search has cost up to $0.40" in optimize.budget_stop(
            spend, 1.0, before=optimize.BEFORE_CYCLE
        )
        assert "scoring a candidate has cost up to $0.25" in optimize.budget_stop(
            optimize.Spend(dollars=0.8, scorings=(0.25,)), 1.0, before=optimize.BEFORE_SCORING
        )

    def test_spend_it_cannot_price_is_spend_it_cannot_hold(self) -> None:
        """Fail closed: a dollar figure that leaves calls out is a floor, and a budget held to a
        floor holds nothing."""
        clause = optimize.budget_stop(
            optimize.Spend(dollars=0.1, priced=False), 1.0, before=optimize.BEFORE_CYCLE
        )
        assert clause.startswith("some of its model calls had no price"), clause

    def test_a_cycles_cost_is_what_was_spent_between_two_cycles(self) -> None:
        row = optimize.LedgerRow
        rows = [
            row(1, "admitted", 0.6, spent_usd=0.5, score_usd=0.3),
            row(2, "not_best_ever", 0.6, spent_usd=0.9, score_usd=0.25),
        ]
        spend = optimize.search_spend(0.9, True, rows, start=0.1)
        assert spend.cycles == (0.4, 0.4) and spend.scorings == (0.3, 0.25)


class TestTheCeilingAScoringIsHeldTo:
    """``held_to``: the ceiling the model-call guard holds a scoring's calls to, and the account
    it charges them to."""

    @pytest.fixture(autouse=True)
    def meter(self):
        from personalclaw.guardrails import budgets

        budgets.reset_meter()
        yield budgets.get_meter()
        budgets.reset_meter()

    def test_alone_it_holds_the_calls_to_what_is_left_of_the_search_budget(self, meter) -> None:
        from personalclaw.guardrails import budgets

        with optimize.held_to("search-1", 2.0, 0.5):
            assert budgets.current_run_key() == "search-1"
            assert budgets.current_run_budget() == budgets.Budget(max_dollars=2.0)
            assert meter.run_totals("search-1").dollars == 0.5
        assert budgets.current_run_key() == "" and budgets.current_run_budget().is_unlimited
        assert meter.run_totals("search-1").dollars == 0.0, "the in-process account outlived it"

    def test_an_enclosing_ceiling_still_holds_and_counts_what_was_spent_inside(self, meter) -> None:
        """A search an automation started stays inside the automation's per-run ceiling while it
        scores, and the automation's total counts what the scoring spent: a scoring bound to its
        own ceiling alone would lift the automation's for its calls, and hide them from it."""
        from personalclaw.guardrails import budgets

        outer = budgets.Budget(max_tokens=1000, max_dollars=0.5)
        key_token = budgets.set_current_run_key("automation-fire")
        budget_token = budgets.set_current_run_budget(outer)
        try:
            meter.charge_run("automation-fire", 400, 0.2)
            with optimize.held_to("search-1", 5.0, 1.0):
                # The tighter in each dimension: $1.00 spent plus the $0.30 the automation has
                # left, and the 600 tokens it has left.
                assert budgets.current_run_budget() == budgets.Budget(
                    max_tokens=600, max_dollars=pytest.approx(1.3)
                )
                meter.charge_run("search-1", 120, 0.25)  # as the guard charges a call
            assert budgets.current_run_key() == "automation-fire"
            assert budgets.current_run_budget() == outer
            spent = meter.run_totals("automation-fire")
            assert (spent.tokens, round(spent.dollars, 6)) == (520, 0.45)
        finally:
            budgets.reset_current_run_budget(budget_token)
            budgets.reset_current_run_key(key_token)

    def test_an_enclosing_ceiling_with_no_room_left_is_not_read_as_unlimited(self, meter) -> None:
        """0 is UNLIMITED to a Budget, so a ceiling with nothing left must not become one."""
        from personalclaw.guardrails import budgets

        key_token = budgets.set_current_run_key("automation-fire")
        budget_token = budgets.set_current_run_budget(
            budgets.Budget(max_tokens=100, max_dollars=0.5)
        )
        try:
            meter.charge_run("automation-fire", 100, 0.5)
            with optimize.held_to("search-1", 5.0, 0.0):
                held = budgets.current_run_budget()
                assert not held.is_unlimited
                assert 0 < held.max_dollars < 0.01 and held.max_tokens == 1
        finally:
            budgets.reset_current_run_budget(budget_token)
            budgets.reset_current_run_key(key_token)


# ── no_change inherits, and the experience directory ─────────────────────────


class TestCheapPaths:
    def test_a_no_change_candidate_is_NOT_scored(self, live: Path, sandbox: Path) -> None:
        """MetaHarness's ordering: the cheap validation runs before any LLM spend. Asserted by
        counting scorer calls, because "we did not pay for it" is a claim about the caller."""
        scored: list[int] = []

        async def propose(iteration: int, sb: Path, experience: list[dict]):
            if iteration > 2:
                return None
            return optimize.Candidate(
                iteration=iteration,
                fix_fingerprint=f"f{iteration}",
                diff_text="" if iteration == 1 else "real diff",
                ops=() if iteration == 1 else ({"op": "update_node", "id": "a"},),
            )

        async def score(candidate: optimize.Candidate, cand_dir: Path):
            scored.append(candidate.iteration)
            return _measured(0.9)

        outcome = _run(live, sandbox, propose=propose, score=score)
        assert scored == [2], "the empty candidate must not reach the scorer"
        assert outcome.rows[0].outcome == "no_change"

    def test_the_experience_dir_carries_the_RAW_prior_diffs(
        self, live: Path, sandbox: Path
    ) -> None:
        seen: list[int] = []
        propose, score = _proposer([0.6, 0.7, 0.8])

        async def watching(iteration: int, sb: Path, experience: list[dict]):
            seen.append(len(experience))
            return await propose(iteration, sb, experience)

        _run(live, sandbox, propose=watching, score=score)
        # Four calls for three candidates: the fourth is the one that returns None and ends the
        # search, and it still sees the full ledger the third wrote.
        assert seen == [0, 1, 2, 3], "each iteration reads the ledger the previous one wrote"
        exp = sandbox / optimize.EXPERIENCE_DIR
        assert (exp / "index.json").is_file()
        assert sorted(p.name for p in exp.glob("*.diff")) == ["001.diff", "002.diff", "003.diff"]
        index = json.loads((exp / "index.json").read_text(encoding="utf-8"))
        assert all(row["diff_ref"].endswith(".diff") for row in index)

    def test_every_iteration_including_the_discards_lands_in_the_ledger(
        self, live: Path, sandbox: Path
    ) -> None:
        """A ledger of winners cannot answer "why did this cost so much" — the discards are
        most of the spend, so they are most of the record."""
        propose, score = _proposer([0.9, 0.2, 0.95])
        outcome = _run(live, sandbox, propose=propose, score=score)
        assert [r.outcome for r in outcome.rows] == [
            "admitted",
            "below_suite_threshold",
            "admitted",
        ]
        assert json.loads((sandbox / "search.json").read_text(encoding="utf-8"))["iterations"] == 3


# ── the winner is a PROPOSAL a human installs ────────────────────────────────


class TestProposalNotInstall:
    def test_the_winner_is_filed_through_the_ONE_human_gated_queue(self, monkeypatch) -> None:
        seen: dict = {}

        def fake_file(workflow_name, *, ops, rationale, run_ids, predicted_fixes):
            seen.update(
                workflow_name=workflow_name,
                ops=ops,
                rationale=rationale,
                fixes=predicted_fixes,
                run_ids=run_ids,
            )
            return {"filed": True, "proposal_id": "p-1"}

        from personalclaw.learning import refiner_tools

        monkeypatch.setattr(refiner_tools, "file_template_diff", fake_file)
        winner = optimize.LedgerRow(
            iteration=3,
            outcome="admitted",
            score=0.82,
            fix_fingerprint="fp",
            best_so_far=0.82,
            note="the judge passed 2 of the 3 runs it could score",
            ops=[{"op": "update_node", "id": "a"}],
            rationale="the audit never says what a pass is",
        )
        outcome = optimize.SearchOutcome(
            halt_reason=optimize.HaltReason.NO_IMPROVEMENT,
            halt_detail="the best score stayed at 0.82 across its last 3 candidates",
            iterations=4,
            rows=[optimize.LedgerRow(1, "below_suite_threshold", 0.2), winner],
            gate={"suite_threshold": 0.5, "best_ever": 0.7},
            evidence_runs=("run-a", "run-b", "run-c"),
        )
        result = optimize.propose_winner(outcome, workflow_name="code-project")

        assert result["filed"] is True and result["proposal_id"] == "p-1"
        assert seen["workflow_name"] == "code-project"
        assert seen["ops"] == [{"op": "update_node", "id": "a"}]
        assert seen["run_ids"] == ["run-a", "run-b", "run-c"], "the evidence is the scored runs"
        assert seen["rationale"].startswith("the audit never says what a pass is")
        assert "Candidate 3 scored 0.82" in seen["rationale"]
        assert "0.5" in seen["rationale"] and "0.7" in seen["rationale"]
        assert result["summary"].endswith(
            "It filed candidate 3 as proposal p-1 for you to review; nothing was installed."
        )

    def test_a_search_that_admitted_NOTHING_files_nothing(self, monkeypatch) -> None:
        from personalclaw.learning import refiner_tools

        def explode(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("filed a proposal for a search with no winner")

        monkeypatch.setattr(refiner_tools, "file_template_diff", explode)
        outcome = optimize.SearchOutcome(
            halt_reason=optimize.HaltReason.ITERATIONS_EXHAUSTED,
            halt_detail="it tried 8 candidates, the most its max_iterations allows",
            iterations=8,
        )
        result = optimize.propose_winner(outcome, workflow_name="code-project")
        assert result["filed"] is False
        assert result["halt_reason"] == "iterations_exhausted"
        assert result["summary"].endswith(
            "It kept no candidate: the gate admitted none of them. Nothing was filed."
        )

    def test_this_module_has_no_template_WRITE_path(self) -> None:
        """The structural half of propose-don't-write: nothing here reaches an installer.

        A text scan, and a coarse one, but it is the assertion that would fail the day someone
        adds a convenience "just apply it" call to this module — which is the whole risk.
        """
        source = Path(optimize.__file__).read_text(encoding="utf-8")
        for forbidden in ("save_def", "install_skill", "proposals.accept", "template_store"):
            assert forbidden not in source, forbidden


# ── the CLI the bundled template shells into ─────────────────────────────────


def _template_spec() -> dict:
    from personalclaw.workflows.bundled_defs import bundled_root

    return json.loads((bundled_root() / TEMPLATE / "workflow.json").read_text(encoding="utf-8"))


def _nodes(node: dict) -> list[dict]:
    out = [node]
    for child in node.get("children") or []:
        out.extend(_nodes(child))
    for key in ("body", "then", "otherwise"):
        if isinstance(node.get(key), dict):
            out.extend(_nodes(node[key]))
    return out


class TestTemplateCallSites:
    """The template ↔ module seam. Every assertion here answers "would deleting the caller be
    caught?" — because the template is the caller and the module's tables are the callee."""

    def test_the_template_ships(self) -> None:
        from personalclaw.workflows.bundled_defs import template_names

        assert TEMPLATE in template_names()

    def test_every_subcommand_the_template_invokes_EXISTS(self) -> None:
        commands = [
            str((n.get("config") or {}).get("with", {}).get("command", ""))
            for n in _nodes(_template_spec()["root"])
            if (n.get("config") or {}).get("provider") == "bash"
        ]
        assert commands, "no bash nodes found — the extraction is broken, not the template"
        invoked = set()
        for command in commands:
            # This install's own CLI, which a bash step's `personalclaw` names, and its command.
            parts = command.split()
            assert parts[:2] == ["personalclaw", "optimize-harness"], command
            invoked.add(parts[2])
        assert invoked, "extracted no subcommand names"
        assert invoked <= set(optimize.COMMANDS), invoked - set(optimize.COMMANDS)
        assert {"preflight", "scope-check", "adjudicate", "file"} <= invoked

    def test_every_PC_OPT_env_key_the_template_sets_is_READ_by_the_module(self) -> None:
        """A key the template sets and the module ignores is an input silently dropped — which
        is how a declared ``budget_usd`` becomes no budget at all."""
        declared: set[str] = set()
        for node in _nodes(_template_spec()["root"]):
            payload = (node.get("config") or {}).get("payload") or {}
            declared |= {k for k in payload if k.startswith("PC_OPT_")}
        assert declared, "no PC_OPT_* keys found — the extraction is broken"
        known = set(optimize.ENV_PAYLOAD_KEYS) | set(optimize.ENV_STOP_KEYS)
        assert declared <= known, declared - known

    def test_a_candidates_score_reaches_adjudicate_ONLY_from_the_scoring_step(self) -> None:
        """🔴 Red before: adjudicate's score was bound to the proposer's own answer. Now the only
        value that carries a score into it is the scoring step's whole recorded output, and the
        proposer is not asked for a score at all."""
        nodes = {n.get("id"): n for n in _nodes(_template_spec()["root"])}
        score = nodes["score"]["config"]
        assert score["provider"] == "optimize-score"
        adjudicate = nodes["adjudicate"]["config"]["payload"]
        assert adjudicate["PC_OPT_SCORE_RECORD"] == "{{nodes.score.output | json}}"
        for node in nodes.values():
            config = json.dumps(node.get("config") or {})
            assert "propose.output.score" not in config, node.get("id")
        propose = nodes["propose"]["config"]
        assert "score" not in propose["schema"]
        assert '"score"' not in propose["prompt"]

    def test_the_loop_runs_no_more_cycles_than_the_ceiling_preflight_holds_it_to(self) -> None:
        """The loop's cap is the most a search may ask for, and the search halts itself at its
        own ``max_iterations`` first; a cap below the ceiling would cut a search short and hand
        it to a person with its winner unfiled."""
        nodes = {n.get("id"): n for n in _nodes(_template_spec()["root"])}
        assert nodes["search"]["config"]["max_iterations"] == optimize.ITERATION_CEILING
        payload = nodes["adjudicate"]["config"]["payload"]
        assert payload["PC_OPT_MAX_ITERATIONS"] == "{{inputs.max_iterations}}"

    def test_the_template_declares_all_three_halts_AND_the_budget(self) -> None:
        spec = _template_spec()
        payloads: dict[str, str] = {}
        for node in _nodes(spec["root"]):
            payloads.update((node.get("config") or {}).get("payload") or {})
        for key in (
            "PC_OPT_BUDGET_USD",
            "PC_OPT_ABANDON_AFTER",
            "PC_OPT_NO_IMPROVEMENT_HALT",
        ):
            assert key in payloads, key
        assert set(spec["inputs"]) >= {
            "budget_usd",
            "hypothesis_abandon_after",
            "no_improvement_halt",
            "suite_threshold",
        }
        assert spec["inputs"]["budget_usd"]["required"] is True
        assert "default" not in spec["inputs"]["budget_usd"]

    def test_the_frozen_region_gate_REFUSES_rather_than_records(self) -> None:
        """The template half of "dead regardless of score": a gate node whose expression fails
        the iteration, not a field somebody might read."""
        gates = [
            n
            for n in _nodes(_template_spec()["root"])
            if n.get("kind") == "gate" and (n.get("config") or {}).get("kind") == "expression"
        ]
        assert gates, "the loop body has no refusal gate"
        exprs = [str((g.get("config") or {}).get("expr", "")) for g in gates]
        assert any("scope_violation" in e and "scope_check" in e for e in exprs), exprs

    def test_the_agent_and_the_action_provider_are_both_REGISTERED(self) -> None:
        """A provider in one set but not the others saves and then fails to run."""
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        # The registry is populated lazily on first action dispatch (`personalclaw.hooks`), so a
        # bare unit test sees it empty. Bootstrapping is what makes the assertion below about
        # registration rather than about import order.
        _ensure_default_providers_registered()

        agents: set[str] = set()
        providers: set[str] = set()
        for node in _nodes(_template_spec()["root"]):
            cfg = node.get("config") or {}
            if cfg.get("agent"):
                agents.add(str(cfg["agent"]))
            if cfg.get("provider"):
                providers.add(str(cfg["provider"]))
        assert providers == {"bash", "optimize-score"} and agents

        for name in providers:
            assert get_action_provider(name) is not None, f"{name} is not registered"
            assert name in ALLOWED_HOOK_PROVIDERS, f"{name} is not in ALLOWED_HOOK_PROVIDERS"

        from personalclaw.agents.defaults import TEMPLATE_REFINER_AGENT_NAME, is_reserved_agent

        # RESERVED is the stronger claim than "exists in some list": a reserved name is one the
        # gateway provisions itself, so a template naming it cannot resolve to nothing.
        for agent in agents:
            assert is_reserved_agent(agent), f"{agent} is not a reserved built-in agent"
        assert agents == {TEMPLATE_REFINER_AGENT_NAME}, (
            "the search's only agent must be the propose-only refiner — the refiner's tool-scoping "
            "is what stops the optimizer applying its own winner"
        )

    def test_the_proposing_agent_gets_PROPOSE_ONLY_tools(self) -> None:
        """The refiner tool-scoping, carried over verbatim rather than re-derived."""
        from personalclaw.learning.refiner_tools import REFINER_TOOL_NAMES

        assert all(name.startswith(("refiner_", "propose_")) for name in REFINER_TOOL_NAMES)
        assert not any("apply" in name or "install" in name for name in REFINER_TOOL_NAMES)


# ── the steps, called as the template's processes call them ──────────────────

RUN = "run-optimize-test"


def _seed_target_runs(count: int = 3) -> list[str]:
    """*count* finished runs of the target, journaled as the controller journals them."""
    from personalclaw.workflows import journal as journal_mod
    from personalclaw.workflows import store as store_mod
    from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

    ids: list[str] = []
    for i in range(count):
        run = store_mod.create(
            WorkflowRun(id="", workflow_name=TARGET, inputs={"task": f"case-{i + 1}"})
        )
        run.status = RunStatus.COMPLETE
        store_mod.save(run)
        journal = journal_mod.Journal(run.id)
        journal.run_started(TARGET, inputs={"task": f"case-{i + 1}"}, spec_version=1)
        journal.step_completed(
            "root.children[0]", "init", epoch=1, cache_key=f"ck{i}", state=InstanceState.DONE
        )
        journal.run_finished(RunStatus.COMPLETE.value, elapsed_secs=1.0, tokens=1)
        ids.append(run.id)
    return ids


def _book(dollars: float, *, step: str = "propose", priced: bool = True) -> None:
    """A model call of the test run's, booked in the usage ledger under its step."""
    from personalclaw.usage_ledger import TurnUsage, record_turn
    from personalclaw.workflows.ownership import owned_key

    record_turn(
        TurnUsage(
            ts=datetime.now(timezone.utc).isoformat(),
            session_key=owned_key(RUN, step),
            source="subagent",
            agent="",
            provider="scripted",
            model="scripted-1",
            cost_usd=dollars,
            priced=priced,
        )
    )


def _preflighted(live: Path, sandbox: Path, **stops) -> dict:
    _seed_target_runs()
    payload = {
        "subject": TARGET,
        "live_target": str(live),
        "sandbox": str(sandbox),
        "suite_threshold": "0.5",
        "run_id": RUN,
        "stops": {"budget_usd": "2.0", **stops},
    }
    optimize._cmd_preflight(dict(payload))
    return payload


def _record(score: float | None, **kw) -> str:
    return json.dumps(CandidateScore(score=score, judged=3, cases=3, **kw).to_dict())


class TestCli:
    def test_preflight_leaves_a_witness_and_the_runs_to_score_against(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        runs = _seed_target_runs()
        out = optimize._cmd_preflight(
            {
                "subject": TARGET,
                "live_target": str(live),
                "sandbox": str(sandbox),
                "run_id": RUN,
                "stops": {"budget_usd": "2.0"},
            }
        )
        assert out["ok"] and out["witnessed_files"] == 2
        assert (sandbox / optimize.WITNESS_FILE).is_file()
        assert out["cases"] == 3 and sorted(out["case_runs"]) == sorted(runs)
        record = json.loads((sandbox / optimize.PREFLIGHT_FILE).read_text(encoding="utf-8"))
        assert [c["harvest"]["run_id"] for c in record["cases"]] == out["case_runs"]
        assert optimize._cmd_scope_check({"sandbox": str(sandbox)})["clean"] is True

    def test_preflight_REFUSES_a_target_no_candidate_of_which_could_be_scored(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """🔴 Red before: a target with no finished runs started a search whose every score could
        only be the proposer's claim."""
        _seed_target_runs(2)
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize._cmd_preflight(
                {
                    "subject": TARGET,
                    "live_target": str(live),
                    "sandbox": str(sandbox / "fresh"),
                    "run_id": RUN,
                    "stops": {"budget_usd": "2.0"},
                }
            )
        assert str(exc.value).startswith(
            f"optimize-harness cannot score a candidate for {TARGET}: it found 2 runs"
        ), exc.value
        assert not (sandbox / "fresh").exists(), "it wrote before it refused"

    def test_preflight_REFUSES_a_run_that_has_already_spent_its_budget(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        _seed_target_runs()
        _book(2.5)
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize._cmd_preflight(
                {
                    "subject": TARGET,
                    "live_target": str(live),
                    "sandbox": str(sandbox),
                    "run_id": RUN,
                    "stops": {"budget_usd": "2.0"},
                }
            )
        assert "it has spent $2.50 of its $2.00 budget" in str(exc.value)

    def test_scope_check_reports_a_frozen_touch_that_happened_between_processes(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        _preflighted(live, sandbox)
        (live / "workflow.json").write_text("changed by a candidate", encoding="utf-8")
        out = optimize._cmd_scope_check({"sandbox": str(sandbox)})
        assert out["outcome"] == "scope_violation"
        assert out["frozen_touched"]

    def test_adjudicate_REFUSES_without_a_witness(self, sandbox: Path) -> None:
        """A frozen-region check with nothing to compare against passes everything, so a
        missing witness must be a refusal and not a clean verdict."""
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize._cmd_adjudicate(
                {"sandbox": str(sandbox), "run_id": RUN, "score_record": _record(0.9)}
            )
        assert optimize.WITNESS_FILE in str(exc.value)

    def test_adjudicate_REFUSES_without_the_run_whose_spend_it_holds(self, sandbox: Path) -> None:
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize._cmd_adjudicate({"sandbox": str(sandbox), "score_record": _record(0.9)})
        assert "needs the run it belongs to" in str(exc.value)

    def test_adjudicate_reads_NO_score_from_its_payload(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """🔴 Red before: a ``score`` in the payload was the candidate's score. Now only the
        scoring step's record carries one, and a candidate with none is not scored."""
        payload = _preflighted(live, sandbox)
        out = optimize._cmd_adjudicate({**payload, "score": "0.99", "fix_fingerprint": "a"})
        assert out["outcome"] == optimize.CandidateOutcome.NOT_SCORED.value
        assert out["score"] is None and out["admitted"] is False

    def test_adjudicate_admits_only_a_candidate_that_beats_the_best_so_far(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """🔴 Red before: the bar was the floor preflight read, so the 0.85 after a 0.9 was
        admitted and became the winner the last step filed."""
        payload = _preflighted(live, sandbox)
        first = optimize._cmd_adjudicate(
            {**payload, "score_record": _record(0.9), "fix_fingerprint": "a"}
        )
        second = optimize._cmd_adjudicate(
            {**payload, "score_record": _record(0.85), "fix_fingerprint": "b"}
        )
        assert (first["outcome"], second["outcome"]) == ("admitted", "not_best_ever")
        assert second["best_so_far"] == 0.9
        winner = optimize.COMMANDS["experience"]({"sandbox": str(sandbox)})["winner"]
        assert winner["iteration"] == 1

    def test_adjudicate_derives_its_halt_windows_from_the_experience_LEDGER(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """The windows come from the search's persisted ledger, not from a model's memory of
        it — a proposer that forgot to carry them would otherwise disable both halts."""
        payload = {
            **_preflighted(live, sandbox),
            "suite_threshold": "0.9",
            "best_ever": "0.9",
            "score_record": _record(0.1),
            "fix_fingerprint": "same-diagnosis",
            "stops": {"budget_usd": "2.0", "hypothesis_abandon_after": "3"},
        }
        halts = [optimize._cmd_adjudicate(dict(payload))["halt"] for _ in range(3)]
        assert halts[:2] == ["", ""], halts
        assert halts[2] == "hypothesis_abandoned"

    def test_adjudicate_halts_on_the_budget_its_scoring_stopped_on(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        payload = _preflighted(live, sandbox)
        clause = (
            "it has spent $1.90 of its $2.00 budget, and scoring a candidate has cost up to $0.30, "
            "so another would pass it"
        )
        out = optimize._cmd_adjudicate(
            {
                **payload,
                "score_record": _record(None, reason=clause, budget_stopped=True),
                "fix_fingerprint": "a",
            }
        )
        assert out["halt"] == optimize.HaltReason.BUDGET_EXHAUSTED.value
        assert out["summary"] == (
            f"The search stopped at its budget: {clause}. It kept no candidate: the gate admitted "
            "none of them."
        )

    def test_adjudicate_measures_the_runs_spend_and_halts_before_the_cycle_that_would_pass_it(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """🔴 Red before: adjudicate had no spend check at all. Each cycle here books $0.80 to the
        run; after the second the run has spent $1.60 of $2.00 and a third would pass it."""
        payload = _preflighted(live, sandbox)
        halts = []
        for i in range(2):
            _book(0.5)
            _book(0.3, step="score")
            halts.append(
                optimize._cmd_adjudicate(
                    {**payload, "score_record": _record(0.6 + i / 10), "fix_fingerprint": f"f{i}"}
                )
            )
        assert [h["halt"] for h in halts] == ["", optimize.HaltReason.BUDGET_EXHAUSTED.value]
        assert halts[1]["spent_usd"] == pytest.approx(1.6)
        assert halts[1]["halt_detail"] == (
            "it has spent $1.60 of its $2.00 budget, and a cycle of the search has cost up to "
            "$0.80, so another would pass it"
        )

    def test_payload_from_env_maps_every_declared_key_and_the_steps_run(self) -> None:
        env = {
            "PC_OPT_SUBJECT": "s",
            "PC_OPT_BUDGET_USD": "3",
            "PC_OPT_NO_IMPROVEMENT_HALT": "4",
            "PC_OPT_SCORE_RECORD": '{"score": 0.5}',
            "run_id": "run-1",
        }
        payload = optimize.payload_from_env(env)
        assert payload["subject"] == "s"
        assert payload["stops"] == {"budget_usd": "3", "no_improvement_halt": "4"}
        assert payload["score_record"] == '{"score": 0.5}'
        assert payload["run_id"] == "run-1"

    def test_main_reports_an_unknown_subcommand_as_JSON(self, capsys) -> None:
        import io

        assert optimize.main(["nope"], stdin=io.StringIO("")) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False and sorted(optimize.COMMANDS) == payload["commands"]

    def test_main_reports_a_refusal_as_JSON_rather_than_a_traceback(self, capsys) -> None:
        import io

        code = optimize.main(
            ["preflight"], stdin=io.StringIO(json.dumps({"stops": {"budget_usd": 0}}))
        )
        assert code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False and "budget_usd" in payload["error"]


class TestTheScoringStep:
    """``score_step``, as the ``optimize-score`` action runs it in the gateway: the cheap refusals
    before any model call, and the budget checked before the scoring starts."""

    @staticmethod
    def _score(payload: dict) -> dict:
        return asyncio.run(optimize.score_step(payload))

    @staticmethod
    def _step(payload: dict, **kw) -> dict:
        return {
            "target": TARGET,
            "sandbox": payload["sandbox"],
            "budget_usd": "2.0",
            "run_id": RUN,
            "node_id": "score",
            "ops": [{"op": "update_node", "node_id": "handoff", "fields": {"prompt": "x"}}],
            "diff_text": "+x",
            **kw,
        }

    @pytest.fixture
    def no_scorer(self, monkeypatch) -> list:
        """The product scorer, replaced by a recorder that fails the test if it is reached."""
        reached: list = []

        async def reached_scorer(**kw):
            reached.append(kw)
            return CandidateScore(score=0.5, judged=3, cases=3)

        monkeypatch.setattr("personalclaw.evals.candidate_score.score_candidate", reached_scorer)
        return reached

    def test_it_REFUSES_without_the_preflight(self, sandbox: Path) -> None:
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            self._score(self._step({"sandbox": str(sandbox)}))
        assert optimize.WITNESS_FILE in str(exc.value)

    def test_a_candidate_that_touched_the_frozen_region_is_not_paid_for(
        self, live: Path, sandbox: Path, isolated_home: Path, no_scorer: list
    ) -> None:
        payload = _preflighted(live, sandbox)
        (live / "workflow.json").write_text("touched", encoding="utf-8")
        out = self._score(self._step(payload))
        assert out["score"] is None and out["frozen_touched"]
        assert no_scorer == []

    def test_an_empty_edit_inherits_without_being_scored(
        self, live: Path, sandbox: Path, isolated_home: Path, no_scorer: list
    ) -> None:
        payload = _preflighted(live, sandbox)
        out = self._score(self._step(payload, ops=[], diff_text=""))
        assert out["no_change"] is True and out["score"] is None
        assert no_scorer == []

    def test_a_scoring_that_would_pass_the_budget_does_not_start(
        self, live: Path, sandbox: Path, isolated_home: Path, no_scorer: list
    ) -> None:
        payload = _preflighted(live, sandbox)
        # The first cycle spent $1.80, $0.30 of it scoring (the ledger's row says so): another
        # scoring like it would pass the $2.00 budget.
        _book(1.5)
        _book(0.3, step="score")
        optimize._cmd_adjudicate(
            {**payload, "score_record": _record(0.6, spent_usd=0.3), "fix_fingerprint": "a"}
        )
        out = self._score(self._step(payload))
        assert out["budget_stopped"] is True and out["score"] is None
        assert out["reason"] == (
            "it has spent $1.80 of its $2.00 budget, and scoring a candidate has cost up to "
            "$0.30, so another would pass it"
        )
        assert no_scorer == []

    def test_a_scoring_within_the_budget_is_the_products_own_and_booked_to_the_run(
        self, live: Path, sandbox: Path, isolated_home: Path, no_scorer: list
    ) -> None:
        payload = _preflighted(live, sandbox)
        out = self._score(self._step(payload))
        assert out["score"] == 0.5 and out["score_state"] == optimize.SCORE_SCORED
        (call,) = no_scorer
        assert call["subject"] == TARGET and len(call["cases"]) == 3
        assert call["usage"].session_key == f"workflow:{RUN}:score"


class TestTheLedgerTheTemplateReads:
    """What the template's model step is handed comes from the search's own ledger, through the
    ``experience`` step: the refiner holds only its evidence and proposal tools, so it cannot open
    the files itself. The ledger has to carry what the last step files: the winner's ops."""

    @staticmethod
    def _index(sandbox: Path) -> list[dict]:
        return json.loads((sandbox / optimize.EXPERIENCE_DIR / "index.json").read_text())

    def test_adjudicate_keeps_each_candidates_ops_and_scope_in_the_ledger(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """🔴 Red before: no row carried ops, and the rewrite that adds each iteration's row
        dropped every earlier row's scope, so the frozen-region evidence lasted one iteration."""
        payload = _preflighted(live, sandbox)
        first_ops = [{"op": "update_node", "node_id": "audit", "fields": {"label": "Audit"}}]
        optimize._cmd_adjudicate(
            {
                **payload,
                "score_record": _record(0.9),
                "fix_fingerprint": "a",
                "ops": json.dumps(first_ops),
            }
        )
        optimize._cmd_adjudicate(
            {**payload, "score_record": _record(0.4), "fix_fingerprint": "b", "ops": []}
        )

        rows = self._index(sandbox)
        assert [row["ops"] for row in rows] == [first_ops, []]
        assert [row["scope"] for row in rows] == [{"frozen_touched": []}] * 2

    def test_experience_hands_on_each_candidates_raw_diff_and_the_winners_ops(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """🔴 Red before: there was no such step, and the prompts sent the refiner to the files."""
        payload = _preflighted(live, sandbox)
        ops = [{"op": "set_input", "name": "depth", "default": 2}]
        optimize._cmd_adjudicate(
            {
                **payload,
                "score_record": _record(0.9),
                "fix_fingerprint": "a",
                "diff_text": "+one",
                "ops": ops,
            }
        )
        optimize._cmd_adjudicate(
            {**payload, "score_record": _record(0.1), "fix_fingerprint": "b", "diff_text": "+two"}
        )

        answer = optimize.COMMANDS["experience"]({"sandbox": str(sandbox)})
        assert answer["ok"] is True
        assert [(c["iteration"], c["outcome"], c["diff"]) for c in answer["candidates"]] == [
            (1, "admitted", "+one"),
            (2, "below_suite_threshold", "+two"),
        ]
        assert all("ops" not in c for c in answer["candidates"]), "the diff already says it"
        assert answer["winner"]["iteration"] == 1 and answer["winner"]["ops"] == ops

    def test_experience_with_nothing_admitted_has_no_winner(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        payload = _preflighted(live, sandbox)
        optimize._cmd_adjudicate({**payload, "score_record": _record(0.1), "fix_fingerprint": "a"})

        answer = optimize.COMMANDS["experience"]({"sandbox": str(sandbox)})
        assert answer["winner"] is None and len(answer["candidates"]) == 1

    def test_experience_keeps_the_newest_diffs_whole_and_says_which_it_cut(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """Its answer is bound into a prompt and must stay a value a binding can read (an output
        past 64 KiB is kept behind a stub), so the diffs it carries are bounded, newest first."""
        payload = _preflighted(live, sandbox)
        long = "x" * (optimize.EXPERIENCE_DIFF_CHARS + 50)
        count = optimize.EXPERIENCE_TOTAL_DIFF_CHARS // optimize.EXPERIENCE_DIFF_CHARS + 1
        for i in range(count):
            optimize._cmd_adjudicate(
                {
                    **payload,
                    "score_record": _record(0.1),
                    "fix_fingerprint": f"f{i}",
                    "diff_text": long,
                }
            )

        candidates = optimize.COMMANDS["experience"]({"sandbox": str(sandbox)})["candidates"]
        shown = [len(c["diff"]) for c in candidates]
        assert sum(shown) == optimize.EXPERIENCE_TOTAL_DIFF_CHARS
        assert shown[-1] == optimize.EXPERIENCE_DIFF_CHARS and shown[0] == 0
        assert all(c["diff_cut"] for c in candidates)

    def test_experience_refuses_without_a_sandbox(self) -> None:
        with pytest.raises(optimize.OptimizeRefusedError):
            optimize.COMMANDS["experience"]({})

    def test_adjudicate_halts_at_the_iteration_floor(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """🔴 Red before: the floor halted only the in-process search; the template's loop got no
        halt from it, ran out of cycles and was handed to a person, so its winner was never
        filed."""
        payload = {
            **_preflighted(live, sandbox),
            "stops": {"budget_usd": "2.0", "max_iterations": "2"},
        }
        halts = [
            optimize._cmd_adjudicate(
                {**payload, "score_record": _record(0.9 + i / 100), "fix_fingerprint": f"f{i}"}
            )["halt"]
            for i in range(2)
        ]
        assert halts == ["", optimize.HaltReason.ITERATIONS_EXHAUSTED.value]

    def test_the_file_step_files_the_ledgers_winner_with_the_runs_it_was_scored_on(
        self, live: Path, sandbox: Path, isolated_home: Path, monkeypatch
    ) -> None:
        seen: dict = {}

        def fake_file(workflow_name, *, ops, rationale, run_ids, predicted_fixes):
            seen.update(workflow_name=workflow_name, ops=ops, run_ids=run_ids)
            return {"filed": True, "proposal_id": "p-9"}

        from personalclaw.learning import refiner_tools

        monkeypatch.setattr(refiner_tools, "file_template_diff", fake_file)
        payload = _preflighted(live, sandbox)
        ops = [{"op": "update_node", "node_id": "handoff", "fields": {"prompt": "y"}}]
        optimize._cmd_adjudicate(
            {**payload, "score_record": _record(0.9), "fix_fingerprint": "a", "ops": ops}
        )
        out = optimize.COMMANDS["file"](
            {
                **payload,
                "halt": optimize.HaltReason.ITERATIONS_EXHAUSTED.value,
                "halt_detail": "it tried 1 candidates, the most its max_iterations allows",
            }
        )
        record = json.loads((sandbox / optimize.PREFLIGHT_FILE).read_text(encoding="utf-8"))
        assert out["filed"] is True and seen["ops"] == ops
        assert seen["run_ids"] == [c["harvest"]["run_id"] for c in record["cases"]]

    def test_the_in_process_search_writes_each_candidates_ops_into_its_ledger(
        self, live: Path, sandbox: Path
    ) -> None:
        """The same ledger shape from both drivers: :func:`run_search` writes the ops too."""
        ops = ({"op": "update_node", "node_id": "audit"},)

        async def propose(iteration: int, _sandbox: Path, _experience: list):
            return optimize.Candidate(
                iteration=iteration, fix_fingerprint="same", diff_text="+x", ops=ops
            )

        outcome = _run(live, sandbox, propose=propose, score=_scoring(0.95))
        assert all(row.ops == [dict(op) for op in ops] for row in outcome.rows)
        assert all(row["ops"] == [dict(op) for op in ops] for row in self._index(sandbox))


# ── the unscored candidate is LEGIBLE, not a zero ─────────────────────────────


def _violator_only(live: Path, sandbox: Path):
    """A search whose ONLY candidate is a scope violation — every row unscored.

    The touch is left in place long enough to be seen and then reverted, so the search reaches
    its end and its rendered ledger can be inspected (a persisted change correctly raises).
    """

    async def propose(iteration: int, sb: Path, experience: list[dict]):
        if iteration > 1:
            return None
        path = live / "workflow.json"
        keep = path.read_text(encoding="utf-8")
        path.write_text("smuggled", encoding="utf-8")
        path.write_text(keep, encoding="utf-8")
        return optimize.Candidate(iteration=iteration, fix_fingerprint="fix-1", diff_text="diff 1")

    return _run(live, sandbox, propose=propose, score=_scoring(1.0))


def _one_unscored_one_scored(live: Path, sandbox: Path):
    """A search with one of each, which is the only shape where the two renderings can be
    compared side by side out of a single file."""

    async def propose(iteration: int, sb: Path, experience: list[dict]):
        if iteration > 2:
            return None
        if iteration == 1:
            path = live / "workflow.json"
            keep = path.read_text(encoding="utf-8")
            path.write_text("smuggled", encoding="utf-8")
            path.write_text(keep, encoding="utf-8")
        return optimize.Candidate(
            iteration=iteration,
            fix_fingerprint=f"fix-{iteration}",
            diff_text=f"diff {iteration}",
            ops=({"op": "update_node", "id": "audit"},),
        )

    return _run(live, sandbox, propose=propose, score=_scoring(0.6))


class TestUnscoredIsLegible:
    """A candidate the scorer never ran on must SAY so, in words, at the read surface.

    ``store.append_result`` requires a complete ``RunPin`` and a candidate scored by a
    caller-supplied scorer has no honest model fingerprint, so an unscored candidate writes NO
    ``results.tsv`` row — and must not, because an invented fingerprint would poison every
    per-fingerprint baseline that reads the same file. What is left is the reader's obligation:
    a missing row, an empty cell and a ``0.0`` all read as "measured, and it was nothing", so
    the absence gets its own named state instead.
    """

    def test_the_ledger_a_human_reads_renders_the_unscored_candidate_unscored(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """Asserted on the FILES a real ``run_search`` wrote, not on ``to_dict()`` in isolation.

        The bundled template's model step is handed ``.experience/index.json`` by its own
        ``experience`` step, so a rendering only reachable by calling the method by hand would be
        an inert control dressed as a fix.
        """
        outcome = _one_unscored_one_scored(live, sandbox)

        index = json.loads(
            (sandbox / optimize.EXPERIENCE_DIR / "index.json").read_text(encoding="utf-8")
        )
        rows = {row["iteration"]: row for row in index}
        assert rows[1]["outcome"] == "scope_violation"
        assert rows[1]["score"] is None, "a 0.0 here reads as a measurement that came up empty"
        assert rows[1]["score_state"] == optimize.SCORE_UNSCORED

        search = json.loads((sandbox / "search.json").read_text(encoding="utf-8"))
        assert (search["scored_candidates"], search["unscored_candidates"]) == (1, 1)
        assert [row["score_state"] for row in search["rows"]] == [
            optimize.SCORE_UNSCORED,
            optimize.SCORE_SCORED,
        ]

        # The absence the rendering stands in for is real: no `results.tsv` row exists for that
        # candidate. If a fingerprint were ever invented to force one, the reader above would be
        # describing a state the ledger no longer has.
        from personalclaw.evals import store

        assert [r for r in store.read_results() if r.get("kind") == optimize.SEARCH_KIND] == []
        assert outcome.results_state == optimize.SCORE_SCORED

    def test_a_SCORED_candidate_is_never_labelled_unscored(self, live: Path, sandbox: Path) -> None:
        """The other side of the discrimination. Every outcome the dual gate itself reaches was
        scored by definition, and a label that is always on is the same as no label at all."""
        propose, score = _proposer([0.9, 0.2, 0.95])
        outcome = _run(live, sandbox, propose=propose, score=score)
        assert [r.outcome for r in outcome.rows] == [
            "admitted",
            "below_suite_threshold",
            "admitted",
        ]
        rendered = [row.to_dict() for row in outcome.rows]
        assert {row["score_state"] for row in rendered} == {optimize.SCORE_SCORED}
        assert [row["score"] for row in rendered] == [0.9, 0.2, 0.95]

    def test_a_candidate_the_scorer_could_not_measure_renders_unscored(
        self, live: Path, sandbox: Path
    ) -> None:
        """The scorer's own "not measured" (an edit that does not apply, too few runs judged) is
        the third unscored outcome, said in its own words, never a 0.0 the gate weighed."""
        propose, _score = _proposer([0.9])

        async def unmeasured(candidate: optimize.Candidate, cand_dir: Path):
            return CandidateScore(score=None, reason="its edit does not apply to code-project")

        outcome = _run(live, sandbox, propose=propose, score=unmeasured)
        (row,) = outcome.rows
        assert row.outcome == optimize.CandidateOutcome.NOT_SCORED.value
        assert row.to_dict()["score"] is None
        assert row.note == "its edit does not apply to code-project"
        assert outcome.winner is None

    def test_a_search_with_NO_candidates_renders_its_own_third_state(
        self, live: Path, sandbox: Path
    ) -> None:
        """A search that never got a candidate must not borrow the ``unscored`` label: it did not
        fail to measure anything, it had nothing to measure, and only one of those is a proposer
        problem."""
        outcome = _run(live, sandbox, propose=_nothing, score=_scoring(0.0))
        assert outcome.rows == []
        search = json.loads((sandbox / "search.json").read_text(encoding="utf-8"))
        assert search["candidates"] == 0
        assert search["results_state"] == optimize.SCORE_NO_CANDIDATES
        assert search["results_state"] not in {optimize.SCORE_SCORED, optimize.SCORE_UNSCORED}

    def test_the_three_states_are_THREE_different_renderings(
        self, tmp_path: Path, live: Path
    ) -> None:
        """The anti-vacuity leg: it fails if two different inputs render the same thing.

        Against a search with no candidates at all, every "unscored" assertion above would pass
        for the wrong reason, so all three surfaces are built from three real searches and their
        states compared as a SET — two collapsing into one is a red here even though each of the
        single-state tests would stay green.
        """
        propose, score = _proposer([0.9])
        boxes = {name: tmp_path / f"sb-{name}" for name in ("scored", "unscored", "none")}
        for box in boxes.values():
            box.mkdir()

        states = {
            "scored": _run(live, boxes["scored"], propose=propose, score=score).results_state,
            "unscored": _violator_only(live, boxes["unscored"]).results_state,
            "none": _run(live, boxes["none"], propose=_nothing, score=_scoring(0.0)).results_state,
        }
        assert len(set(states.values())) == 3, states
        assert states == {
            "scored": optimize.SCORE_SCORED,
            "unscored": optimize.SCORE_UNSCORED,
            "none": optimize.SCORE_NO_CANDIDATES,
        }

    def test_every_candidate_outcome_is_classified_scored_or_unscored(self) -> None:
        """An outcome added without being classified defaults to "scored" and publishes its
        placeholder 0.0 as a measurement. Both sides are asserted non-empty: an
        ``UNSCORED_OUTCOMES`` that swallowed the whole enum would make ``scored`` unreachable and
        every assertion in this class vacuous."""
        values = {o.value for o in optimize.CandidateOutcome}
        assert optimize.UNSCORED_OUTCOMES < values, optimize.UNSCORED_OUTCOMES - values
        assert values - optimize.UNSCORED_OUTCOMES
        # The membership itself, stated once: moving a gate-decided outcome in here would hide a
        # real measurement, which is the mirror of the defect this whole class is about.
        assert optimize.UNSCORED_OUTCOMES == {"scope_violation", "no_change", "not_scored"}

    def test_an_unscored_row_stays_unscored_through_the_index_ROUND_TRIP(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """``_cmd_adjudicate`` rewrites the whole index from the rows it read back, and it reads
        ``score`` through ``_as_float`` — which turns the rendered ``None`` into ``0.0``. Deriving
        the state from ``outcome`` survives that; a flag stored beside the score would not, and
        iteration 2 would quietly resurrect the zero iteration 1 removed."""
        payload = _preflighted(live, sandbox)

        path = live / "workflow.json"
        keep = path.read_text(encoding="utf-8")
        path.write_text("smuggled", encoding="utf-8")
        first = optimize._cmd_adjudicate(
            {**payload, "score_record": _record(1.0), "fix_fingerprint": "fix-1"}
        )
        assert first["outcome"] == "scope_violation"
        assert first["score"] is None and first["score_state"] == optimize.SCORE_UNSCORED

        path.write_text(keep, encoding="utf-8")
        second = optimize._cmd_adjudicate(
            {**payload, "score_record": _record(0.9), "fix_fingerprint": "fix-2"}
        )
        assert second["score"] == 0.9 and second["score_state"] == optimize.SCORE_SCORED

        index = json.loads(
            (sandbox / optimize.EXPERIENCE_DIR / "index.json").read_text(encoding="utf-8")
        )
        rows = {row["iteration"]: row for row in index}
        assert rows[1]["score"] is None and rows[1]["score_state"] == optimize.SCORE_UNSCORED
        assert rows[2]["score_state"] == optimize.SCORE_SCORED
