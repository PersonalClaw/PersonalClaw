"""ES-11's budgeted optimize-harness search (EVALUATION-SUBSTRATE §8).

The criterion this file answers has five clauses, and four of them are the kind that reads as
satisfied while being quietly false, so each is railed with its own negative:

* **"nothing live mutates during the search"** is proven by OBSERVATION, not by asserting
  intent. Every end-to-end test hashes the live artifact tree itself — with this file's own
  ``_digest`` helper, deliberately not the module's :class:`LiveWitness`, so a broken witness
  cannot certify itself — before and after a real search. Two negatives make the observation
  non-vacuous: a scorer that mutates the live file raises, and a proposer that writes the live
  file and restores identical bytes is still caught (by the mtime/size snapshot, which is why
  there are two detectors rather than one).
* **the DUAL gate** gets one test per half, each holding the other half satisfied, plus a tie
  case. A gate whose halves are only ever observed together could be admitting on one.
* **the frozen region** is asserted to REFUSE rather than record: the violating candidate is
  scored higher than everything else in its search and still is not the winner.
* **the three halts** each get a firing test and a non-firing counterpart, so none of them is
  a condition that would fire on any input.
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

import hashlib
import json
from pathlib import Path

import pytest

from personalclaw.evals import optimize
from personalclaw.guardrails.budgets import SpendMeter
from personalclaw.workflows import scope as scope_mod

TEMPLATE = "optimize-harness"


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
def meter(tmp_path: Path) -> SpendMeter:
    """A meter whose ``spend.json`` lives in ``tmp_path`` — never the real home."""
    home = tmp_path / "meter-home"
    home.mkdir()
    return SpendMeter(config_dir=home)


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


def _proposer(scores: list[float], *, fingerprints: list[str] | None = None):
    """A deterministic proposer over a fixed score list, with a matching scorer."""
    prints = fingerprints or [f"fix-{i}" for i in range(len(scores))]

    def propose(iteration: int, sandbox: Path, experience: list[dict]):
        idx = iteration - 1
        if idx >= len(scores):
            return None
        return optimize.Candidate(
            iteration=iteration,
            fix_fingerprint=prints[idx],
            diff_text=f"--- candidate {iteration}\n+++ score {scores[idx]}\n",
            ops=[{"op": "update_node", "id": "audit", "prompt": f"v{iteration}"}],
            rationale=f"iteration {iteration}",
        )

    def score(candidate: optimize.Candidate, cand_dir: Path):
        return scores[candidate.iteration - 1], {"suite": "harvested"}

    return propose, score


def _run(live: Path, sandbox: Path, meter: SpendMeter, **kw):
    defaults = {
        "subject": "code-project",
        "live_target": str(live),
        "sandbox": str(sandbox),
        "suite_threshold": 0.5,
        "stops": _stops(),
        "meter": meter,
        "best_ever": optimize.BestEver(value=0.0, rows_considered=0, subject="code-project"),
    }
    defaults.update(kw)
    return optimize.run_search(**defaults)  # type: ignore[arg-type]


# ── "nothing live mutates during the search" ─────────────────────────────────


class TestNothingLiveMutates:
    def test_a_completed_search_leaves_the_live_artifact_BYTE_IDENTICAL(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """The headline clause, as an observation of before/after bytes.

        The proposer here *tries* to write the live artifact on its second iteration, so the
        search under test is one that had a candidate reach for the frozen region — a clean
        after-image from a search that never tried would prove nothing.
        """
        propose, score = _proposer([0.6, 0.7, 0.8])
        before = _digest(live)
        assert before, "the live fixture is empty — the comparison would be vacuous"

        original = propose

        def grabby(iteration: int, sb: Path, experience: list[dict]):
            if iteration == 2:
                (live / "workflow.json").write_text("HIJACKED", encoding="utf-8")
                (live / "workflow.json").write_text(
                    json.dumps({"name": "code-project"}), encoding="utf-8"
                )
            return original(iteration, sb, experience)

        outcome = _run(live, sandbox, meter, propose=grabby, score=score)

        assert outcome.halt_reason in tuple(optimize.HaltReason)
        assert _digest(live) == before

    def test_a_scorer_that_MUTATES_the_live_artifact_is_caught(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """The vacuity proof for the witness: it can fail, and it fails loudly.

        Without this, ``test_..._BYTE_IDENTICAL`` above would also pass against a witness that
        compared an empty dict to an empty dict.
        """
        propose, _score = _proposer([0.9])

        def sabotage(candidate: optimize.Candidate, cand_dir: Path):
            (live / "workflow.json").write_text("permanently changed", encoding="utf-8")
            return 0.9, {}

        with pytest.raises(optimize.LiveMutationError) as exc:
            _run(live, sandbox, meter, propose=propose, score=sabotage)
        assert "workflow.json" in str(exc.value)

    def test_a_write_then_RESTORE_is_still_a_scope_violation(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """ "Wrote into the live tree and put it back" is not "nothing mutated".

        Restoring identical bytes defeats a content hash by construction, which is exactly why
        the search also brackets each iteration with the engine's mtime/size snapshot. This
        test is the only one that distinguishes the two detectors.
        """
        propose, score = _proposer([0.9])

        def restoring(iteration: int, sb: Path, experience: list[dict]):
            path = live / "workflow.json"
            keep = path.read_text(encoding="utf-8")
            path.write_text("temporarily different", encoding="utf-8")
            path.write_text(keep, encoding="utf-8")
            return propose(iteration, sb, experience)

        outcome = _run(live, sandbox, meter, propose=restoring, score=score)
        assert [r.outcome for r in outcome.rows] == ["scope_violation"]
        assert outcome.winner is None

    def test_a_sandbox_inside_the_frozen_region_is_REFUSED_before_any_work(
        self, live: Path, meter: SpendMeter
    ) -> None:
        propose, score = _proposer([1.0])
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            _run(live, live / "candidates", meter, propose=propose, score=score)
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
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """ "Dead regardless of score" — the whole clause. The violator scores 1.0 against a
        clean candidate's 0.6, and loses."""

        def propose(iteration: int, sb: Path, experience: list[dict]):
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
                ops=[{"op": "update_node", "id": "audit"}],
            )

        def score(candidate: optimize.Candidate, cand_dir: Path):
            return (1.0 if candidate.iteration == 1 else 0.6), {}

        outcome = _run(live, sandbox, meter, propose=propose, score=score)
        rows = {r.iteration: r for r in outcome.rows}
        assert rows[1].outcome == "scope_violation"
        assert rows[1].score == 0.0, "a violator is never scored — that is what makes it cheap"
        assert outcome.winner is not None and outcome.winner.iteration == 2
        assert outcome.winner_score == 0.6


# ── the dual gate, one half at a time ────────────────────────────────────────


class TestDualGate:
    def test_half_A_alone_cannot_admit(self) -> None:
        """Beats the best-ever, fails the suite threshold → discarded."""
        gate = optimize.DualGate(
            suite_threshold=0.8, best_ever=optimize.BestEver(value=0.1, rows_considered=3)
        )
        assert gate.beats_best_ever(0.5), "precondition: half B is satisfied"
        assert not gate.clears_suite_threshold(0.5)
        assert gate.decide(0.5) is optimize.CandidateOutcome.BELOW_SUITE_THRESHOLD

    def test_half_B_alone_cannot_admit(self) -> None:
        """Clears the suite threshold, does not beat the best-ever → discarded."""
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.9, rows_considered=3)
        )
        assert gate.clears_suite_threshold(0.7), "precondition: half A is satisfied"
        assert not gate.beats_best_ever(0.7)
        assert gate.decide(0.7) is optimize.CandidateOutcome.NOT_BEST_EVER

    def test_both_halves_admit(self) -> None:
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.6, rows_considered=1)
        )
        assert gate.decide(0.7) is optimize.CandidateOutcome.ADMITTED

    def test_a_TIE_with_the_best_ever_loses(self) -> None:
        """Ties lose: hill-climbing on equal scores is how a search spends a budget wandering
        a plateau and then calls its last step a win."""
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.7, rows_considered=1)
        )
        assert gate.decide(0.7) is optimize.CandidateOutcome.NOT_BEST_EVER

    def test_the_threshold_itself_passes(self) -> None:
        gate = optimize.DualGate(
            suite_threshold=0.5, best_ever=optimize.BestEver(value=0.0, rows_considered=0)
        )
        assert gate.clears_suite_threshold(0.5)

    def test_the_best_ever_floor_is_read_ONCE_and_does_not_follow_the_search(
        self, live: Path, sandbox: Path, meter: SpendMeter, monkeypatch
    ) -> None:
        """A floor recomputed from the rows the search is writing is pinned by the value it is
        meant to pin.

        Counted rather than argued: ``capture_best_ever`` is wrapped, the search runs three
        scored iterations, and the call count must be exactly one. A per-iteration re-read
        would make every candidate "the best ever" and the gate's second half free.
        """
        calls: list[str] = []
        real = optimize.capture_best_ever

        def counting(subject: str, *, rows=None):
            calls.append(subject)
            return real(subject, rows=rows or [])

        monkeypatch.setattr(optimize, "capture_best_ever", counting)
        propose, score = _proposer([0.6, 0.7, 0.8])
        outcome = _run(live, sandbox, meter, propose=propose, score=score, best_ever=None)

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


# ── the three declared halts, each firing and each not ───────────────────────


class TestHalts:
    def test_hypothesis_abandon_after_HALTS(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        propose, score = _proposer(
            [0.1, 0.1, 0.1, 0.1], fingerprints=["same", "same", "same", "same"]
        )
        outcome = _run(
            live, sandbox, meter, propose=propose, score=score, stops=_stops(no_improvement_halt=99)
        )
        assert outcome.halt_reason is optimize.HaltReason.HYPOTHESIS_ABANDONED
        assert outcome.iterations == 3
        assert outcome.needs_from_human

    def test_hypothesis_abandon_does_NOT_fire_on_an_alternating_proposer(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """A proposer trying two fixes in turn is exploring. Abandoning it would be abandoning
        the search, not the hypothesis — so the detector must not fire on any repetition."""
        propose, score = _proposer([0.1] * 6, fingerprints=["a", "b", "a", "b", "a", "b"])
        outcome = _run(
            live, sandbox, meter, propose=propose, score=score, stops=_stops(no_improvement_halt=99)
        )
        assert outcome.halt_reason is not optimize.HaltReason.HYPOTHESIS_ABANDONED

    def test_no_improvement_halt_HALTS(self, live: Path, sandbox: Path, meter: SpendMeter) -> None:
        """The clause the census found ABSENT from the tree. Its call site is
        ``run_search``'s third halt check; deleting it leaves this search running to
        ``max_iterations`` with a different halt reason, which is what this asserts against."""
        propose, score = _proposer([0.1] * 8, fingerprints=[f"f{i}" for i in range(8)])
        outcome = _run(
            live,
            sandbox,
            meter,
            propose=propose,
            score=score,
            stops=_stops(no_improvement_halt=3, hypothesis_abandon_after=99, max_iterations=8),
        )
        assert outcome.halt_reason is optimize.HaltReason.NO_IMPROVEMENT
        assert outcome.iterations == 3
        assert "without improving" in outcome.halt_detail
        assert outcome.needs_from_human

    def test_no_improvement_does_NOT_fire_on_a_climbing_search(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        propose, score = _proposer([0.6, 0.7, 0.8, 0.9])
        outcome = _run(
            live,
            sandbox,
            meter,
            propose=propose,
            score=score,
            stops=_stops(no_improvement_halt=3, max_iterations=8),
        )
        assert outcome.halt_reason is optimize.HaltReason.PROPOSER_EXHAUSTED
        assert outcome.winner_score == 0.9

    def test_budget_usd_HALTS(self, live: Path, sandbox: Path, meter: SpendMeter) -> None:
        propose, score = _proposer([0.6] * 8)

        def charging(candidate: optimize.Candidate, cand_dir: Path):
            meter.charge(0, 0.4, run_key=f"{optimize.SEARCH_KIND}:code-project")
            return 0.6, {}

        outcome = _run(
            live,
            sandbox,
            meter,
            propose=propose,
            score=charging,
            stops=_stops(budget_usd=1.0, no_improvement_halt=99, hypothesis_abandon_after=99),
        )
        assert outcome.halt_reason is optimize.HaltReason.BUDGET_EXHAUSTED
        assert "budget exceeded" in outcome.halt_detail
        assert outcome.iterations == 3, "0.4 × 3 crosses 1.0; the fourth never starts"

    def test_budget_does_NOT_fire_when_the_ceiling_is_not_reached(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        propose, score = _proposer([0.6, 0.7])

        def charging(candidate: optimize.Candidate, cand_dir: Path):
            meter.charge(0, 0.01, run_key=f"{optimize.SEARCH_KIND}:code-project")
            return (0.6 if candidate.iteration == 1 else 0.7), {}

        outcome = _run(
            live, sandbox, meter, propose=propose, score=charging, stops=_stops(budget_usd=1.0)
        )
        assert outcome.halt_reason is not optimize.HaltReason.BUDGET_EXHAUSTED

    def test_an_UNBUDGETED_envelope_is_refused(self) -> None:
        """0 means UNLIMITED to :class:`Budget`, so it must not be a default here."""
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize.StopConditions(budget_usd=0.0).validate()
        assert "budget_usd" in str(exc.value)

    def test_max_iterations_is_the_floor_under_the_other_three(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        propose, score = _proposer([0.1] * 20, fingerprints=[f"f{i}" for i in range(20)])
        outcome = _run(
            live,
            sandbox,
            meter,
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
        assert not optimize.hypothesis_abandoned(["a", "a"], 3)
        assert optimize.hypothesis_abandoned(["a", "a", "a"], 3)
        assert not optimize.no_improvement([0.1, 0.2], 3)
        assert optimize.no_improvement([0.5, 0.5, 0.5], 3)
        assert not optimize.no_improvement([0.5, 0.5, 0.6], 3)


# ── no_change inherits, and the experience directory ─────────────────────────


class TestCheapPaths:
    def test_a_no_change_candidate_is_NOT_scored(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """MetaHarness's ordering: the cheap validation runs before any LLM spend. Asserted by
        counting scorer calls, because "we did not pay for it" is a claim about the caller."""
        scored: list[int] = []

        def propose(iteration: int, sb: Path, experience: list[dict]):
            if iteration > 2:
                return None
            return optimize.Candidate(
                iteration=iteration,
                fix_fingerprint=f"f{iteration}",
                diff_text="" if iteration == 1 else "real diff",
                ops=() if iteration == 1 else ({"op": "update_node", "id": "a"},),
            )

        def score(candidate: optimize.Candidate, cand_dir: Path):
            scored.append(candidate.iteration)
            return 0.9, {}

        outcome = _run(live, sandbox, meter, propose=propose, score=score)
        assert scored == [2], "the empty candidate must not reach the scorer"
        assert outcome.rows[0].outcome == "no_change"

    def test_the_experience_dir_carries_the_RAW_prior_diffs(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        seen: list[int] = []
        propose, score = _proposer([0.6, 0.7, 0.8])

        def watching(iteration: int, sb: Path, experience: list[dict]):
            seen.append(len(experience))
            return propose(iteration, sb, experience)

        _run(live, sandbox, meter, propose=watching, score=score)
        # Four calls for three candidates: the fourth is the one that returns None and ends the
        # search, and it still sees the full ledger the third wrote.
        assert seen == [0, 1, 2, 3], "each iteration reads the ledger the previous one wrote"
        exp = sandbox / optimize.EXPERIENCE_DIR
        assert (exp / "index.json").is_file()
        assert sorted(p.name for p in exp.glob("*.diff")) == ["001.diff", "002.diff", "003.diff"]
        index = json.loads((exp / "index.json").read_text(encoding="utf-8"))
        assert all(row["diff_ref"].endswith(".diff") for row in index)

    def test_every_iteration_including_the_discards_lands_in_the_ledger(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """A ledger of winners cannot answer "why did this cost so much" — the discards are
        most of the spend, so they are most of the record."""
        propose, score = _proposer([0.9, 0.2, 0.95])
        outcome = _run(live, sandbox, meter, propose=propose, score=score)
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
                workflow_name=workflow_name, ops=ops, rationale=rationale, fixes=predicted_fixes
            )
            return {"filed": True, "proposal_id": "p-1"}

        from personalclaw.learning import refiner_tools

        monkeypatch.setattr(refiner_tools, "file_template_diff", fake_file)
        outcome = optimize.SearchOutcome(
            halt_reason=optimize.HaltReason.NO_IMPROVEMENT,
            halt_detail="plateau",
            iterations=4,
            winner=optimize.Candidate(
                iteration=3, fix_fingerprint="fp", ops=({"op": "update_node", "id": "a"},)
            ),
            winner_score=0.82,
            gate={"suite_threshold": 0.5, "best_ever": 0.7},
        )
        result = optimize.propose_winner(outcome, workflow_name="code-project")

        assert result["filed"] is True and result["proposal_id"] == "p-1"
        assert seen["workflow_name"] == "code-project"
        assert seen["ops"] == [{"op": "update_node", "id": "a"}]
        assert "0.5" in seen["rationale"] and "0.7" in seen["rationale"]

    def test_a_search_that_admitted_NOTHING_files_nothing(self, monkeypatch) -> None:
        from personalclaw.learning import refiner_tools

        def explode(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("filed a proposal for a search with no winner")

        monkeypatch.setattr(refiner_tools, "file_template_diff", explode)
        outcome = optimize.SearchOutcome(
            halt_reason=optimize.HaltReason.ITERATIONS_EXHAUSTED, iterations=8
        )
        result = optimize.propose_winner(outcome, workflow_name="code-project")
        assert result["filed"] is False
        assert result["halt_reason"] == "iterations_exhausted"

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
            assert "personalclaw.evals.optimize" in command, command
            parts = command.split()
            invoked.add(parts[parts.index("personalclaw.evals.optimize") + 1])
        assert invoked, "extracted no subcommand names"
        assert invoked <= set(optimize.COMMANDS), invoked - set(optimize.COMMANDS)
        assert {"preflight", "scope-check", "adjudicate"} <= invoked

    def test_every_PC_OPT_env_key_the_template_sets_is_READ_by_the_module(self) -> None:
        """A key the template sets and the module ignores is an input silently dropped — which
        is how a declared ``budget_usd`` becomes no budget at all."""
        declared: set[str] = set()
        for node in _nodes(_template_spec()["root"]):
            payload = (node.get("config") or {}).get("payload") or {}
            declared |= {k for k in payload if k.startswith("PC_OPT_")}
        assert declared, "no PC_OPT_* keys found — the extraction is broken"
        known = (
            set(optimize.ENV_PAYLOAD_KEYS)
            | set(optimize.ENV_STOP_KEYS)
            | set(optimize.ENV_LIST_KEYS)
        )
        assert declared <= known, declared - known

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
        assert providers and agents

        for name in providers:
            assert get_action_provider(name) is not None, f"{name} is not registered"
            assert name in ALLOWED_HOOK_PROVIDERS, f"{name} is not in ALLOWED_HOOK_PROVIDERS"

        from personalclaw.agents.defaults import TEMPLATE_REFINER_AGENT_NAME, is_reserved_agent

        # RESERVED is the stronger claim than "exists in some list": a reserved name is one the
        # gateway provisions itself, so a template naming it cannot resolve to nothing.
        for agent in agents:
            assert is_reserved_agent(agent), f"{agent} is not a reserved built-in agent"
        assert agents == {TEMPLATE_REFINER_AGENT_NAME}, (
            "the search's only agent must be the propose-only refiner — §8.3's tool-scoping "
            "is what stops the optimizer applying its own winner"
        )

    def test_the_proposing_agent_gets_PROPOSE_ONLY_tools(self) -> None:
        """§8.3's refiner tool-scoping, carried over verbatim rather than re-derived."""
        from personalclaw.learning.refiner_tools import REFINER_TOOL_NAMES

        assert all(name.startswith(("refiner_", "propose_")) for name in REFINER_TOOL_NAMES)
        assert not any("apply" in name or "install" in name for name in REFINER_TOOL_NAMES)


class TestCli:
    def test_preflight_leaves_a_witness_the_next_process_can_use(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        out = optimize._cmd_preflight(
            {
                "subject": "code-project",
                "live_target": str(live),
                "sandbox": str(sandbox),
                "stops": {"budget_usd": "2.0"},
            }
        )
        assert out["ok"] and out["witnessed_files"] == 2
        assert (sandbox / optimize.WITNESS_FILE).is_file()
        assert optimize._cmd_scope_check({"sandbox": str(sandbox)})["clean"] is True

    def test_scope_check_reports_a_frozen_touch_that_happened_between_processes(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        optimize._cmd_preflight(
            {
                "subject": "s",
                "live_target": str(live),
                "sandbox": str(sandbox),
                "stops": {"budget_usd": "2.0"},
            }
        )
        (live / "workflow.json").write_text("changed by a candidate", encoding="utf-8")
        out = optimize._cmd_scope_check({"sandbox": str(sandbox)})
        assert out["outcome"] == "scope_violation"
        assert out["frozen_touched"]

    def test_adjudicate_REFUSES_without_a_witness(self, sandbox: Path) -> None:
        """A frozen-region check with nothing to compare against passes everything, so a
        missing witness must be a refusal and not a clean verdict."""
        with pytest.raises(optimize.OptimizeRefusedError) as exc:
            optimize._cmd_adjudicate({"sandbox": str(sandbox), "score": "0.9"})
        assert optimize.WITNESS_FILE in str(exc.value)

    def test_adjudicate_derives_its_halt_windows_from_the_experience_LEDGER(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """The windows come from the search's persisted ledger, not from a model's memory of
        it — a proposer that forgot to carry them would otherwise disable both halts."""
        optimize._cmd_preflight(
            {
                "subject": "s",
                "live_target": str(live),
                "sandbox": str(sandbox),
                "stops": {"budget_usd": "2.0"},
            }
        )
        payload = {
            "sandbox": str(sandbox),
            "subject": "s",
            "suite_threshold": "0.9",
            "best_ever": "0.9",
            "score": "0.1",
            "fix_fingerprint": "same-diagnosis",
            "stops": {"budget_usd": "2.0", "hypothesis_abandon_after": "3"},
        }
        halts = [optimize._cmd_adjudicate(dict(payload))["halt"] for _ in range(3)]
        assert halts[:2] == ["", ""], halts
        assert halts[2] == "hypothesis_abandoned"

    def test_payload_from_env_maps_every_declared_key(self) -> None:
        env = {
            "PC_OPT_SUBJECT": "s",
            "PC_OPT_BUDGET_USD": "3",
            "PC_OPT_NO_IMPROVEMENT_HALT": "4",
            "PC_OPT_FIX_FINGERPRINTS": "a, b ,c",
        }
        payload = optimize.payload_from_env(env)
        assert payload["subject"] == "s"
        assert payload["stops"] == {"budget_usd": "3", "no_improvement_halt": "4"}
        assert payload["fix_fingerprints"] == ["a", "b", "c"]

    def test_an_empty_list_env_value_becomes_an_empty_window(self) -> None:
        """``[""]`` would be a one-element window of the empty string, which makes
        ``hypothesis_abandoned`` fire on the very first iteration."""
        assert optimize.payload_from_env({"PC_OPT_MARKS": ""})["marks"] == []

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


# ── the unscored candidate is LEGIBLE, not a zero ─────────────────────────────


def _violator_only(live: Path, sandbox: Path, meter: SpendMeter):
    """A search whose ONLY candidate is a scope violation — every row unscored.

    The touch is left in place long enough to be seen and then reverted, so the search reaches
    its end and its rendered ledger can be inspected (a persisted change correctly raises).
    """

    def propose(iteration: int, sb: Path, experience: list[dict]):
        if iteration > 1:
            return None
        path = live / "workflow.json"
        keep = path.read_text(encoding="utf-8")
        path.write_text("smuggled", encoding="utf-8")
        path.write_text(keep, encoding="utf-8")
        return optimize.Candidate(iteration=iteration, fix_fingerprint="fix-1", diff_text="diff 1")

    return _run(live, sandbox, meter, propose=propose, score=lambda *a: (1.0, {}))


def _one_unscored_one_scored(live: Path, sandbox: Path, meter: SpendMeter):
    """A search with one of each, which is the only shape where the two renderings can be
    compared side by side out of a single file."""

    def propose(iteration: int, sb: Path, experience: list[dict]):
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
            ops=[{"op": "update_node", "id": "audit"}],
        )

    return _run(live, sandbox, meter, propose=propose, score=lambda *a: (0.6, {}))


class TestUnscoredIsLegible:
    """A candidate the scorer never ran on must SAY so, in words, at the read surface.

    ``store.append_result`` requires a complete ``RunPin`` (ES-2) and a candidate scored by a
    caller-supplied scorer has no honest model fingerprint, so an unscored candidate writes NO
    ``results.tsv`` row — and must not, because an invented fingerprint would poison every
    per-fingerprint baseline that reads the same file. What is left is the reader's obligation:
    a missing row, an empty cell and a ``0.0`` all read as "measured, and it was nothing", so
    the absence gets its own named state instead.
    """

    def test_the_ledger_a_human_reads_renders_the_unscored_candidate_unscored(
        self, live: Path, sandbox: Path, meter: SpendMeter, isolated_home: Path
    ) -> None:
        """Asserted on the FILES a real ``run_search`` wrote, not on ``to_dict()`` in isolation.

        The bundled template's report node reads ``.experience/index.json`` and the search's own
        output by path, so a rendering only reachable by calling the method by hand would be an
        inert control dressed as a fix.
        """
        outcome = _one_unscored_one_scored(live, sandbox, meter)

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

    def test_a_SCORED_candidate_is_never_labelled_unscored(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """The other side of the discrimination. Every outcome the dual gate itself reaches was
        scored by definition, and a label that is always on is the same as no label at all."""
        propose, score = _proposer([0.9, 0.2, 0.95])
        outcome = _run(live, sandbox, meter, propose=propose, score=score)
        assert [r.outcome for r in outcome.rows] == [
            "admitted",
            "below_suite_threshold",
            "admitted",
        ]
        rendered = [row.to_dict() for row in outcome.rows]
        assert {row["score_state"] for row in rendered} == {optimize.SCORE_SCORED}
        assert [row["score"] for row in rendered] == [0.9, 0.2, 0.95]

    def test_a_search_with_NO_candidates_renders_its_own_third_state(
        self, live: Path, sandbox: Path, meter: SpendMeter
    ) -> None:
        """A search that never got a candidate must not borrow the ``unscored`` label: it did not
        fail to measure anything, it had nothing to measure, and only one of those is a proposer
        problem."""
        outcome = _run(live, sandbox, meter, propose=lambda *a: None, score=lambda *a: (0.0, {}))
        assert outcome.rows == []
        search = json.loads((sandbox / "search.json").read_text(encoding="utf-8"))
        assert search["candidates"] == 0
        assert search["results_state"] == optimize.SCORE_NO_CANDIDATES
        assert search["results_state"] not in {optimize.SCORE_SCORED, optimize.SCORE_UNSCORED}

    def test_the_three_states_are_THREE_different_renderings(
        self, tmp_path: Path, live: Path, meter: SpendMeter
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
            "scored": _run(
                live, boxes["scored"], meter, propose=propose, score=score
            ).results_state,
            "unscored": _violator_only(live, boxes["unscored"], meter).results_state,
            "none": _run(
                live, boxes["none"], meter, propose=lambda *a: None, score=lambda *a: (0.0, {})
            ).results_state,
        }
        assert len(set(states.values())) == 3, states
        assert states == {
            "scored": optimize.SCORE_SCORED,
            "unscored": optimize.SCORE_UNSCORED,
            "none": optimize.SCORE_NO_CANDIDATES,
        }

    def test_every_candidate_outcome_is_classified_scored_or_unscored(self) -> None:
        """A sixth outcome added without being classified defaults to "scored" and publishes its
        placeholder 0.0 as a measurement. Both sides are asserted non-empty: an
        ``UNSCORED_OUTCOMES`` that swallowed the whole enum would make ``scored`` unreachable and
        every assertion in this class vacuous."""
        values = {o.value for o in optimize.CandidateOutcome}
        assert optimize.UNSCORED_OUTCOMES < values, optimize.UNSCORED_OUTCOMES - values
        assert values - optimize.UNSCORED_OUTCOMES
        # The membership itself, stated once: moving a gate-decided outcome in here would hide a
        # real measurement, which is the mirror of the defect this whole class is about.
        assert optimize.UNSCORED_OUTCOMES == {"scope_violation", "no_change"}

    def test_an_unscored_row_stays_unscored_through_the_index_ROUND_TRIP(
        self, live: Path, sandbox: Path, isolated_home: Path
    ) -> None:
        """``_cmd_adjudicate`` rewrites the whole index from the rows it read back, and it reads
        ``score`` through ``_as_float`` — which turns the rendered ``None`` into ``0.0``. Deriving
        the state from ``outcome`` survives that; a flag stored beside the score would not, and
        iteration 2 would quietly resurrect the zero iteration 1 removed."""
        payload = {
            "subject": "code-project",
            "live_target": str(live),
            "sandbox": str(sandbox),
            "stops": {"budget_usd": "2.0"},
        }
        optimize._cmd_preflight(dict(payload))

        path = live / "workflow.json"
        keep = path.read_text(encoding="utf-8")
        path.write_text("smuggled", encoding="utf-8")
        first = optimize._cmd_adjudicate(
            {**payload, "suite_threshold": "0.5", "score": "1.0", "fix_fingerprint": "fix-1"}
        )
        assert first["outcome"] == "scope_violation"
        assert first["score"] is None and first["score_state"] == optimize.SCORE_UNSCORED

        path.write_text(keep, encoding="utf-8")
        second = optimize._cmd_adjudicate(
            {**payload, "suite_threshold": "0.5", "score": "0.9", "fix_fingerprint": "fix-2"}
        )
        assert second["score"] == 0.9 and second["score_state"] == optimize.SCORE_SCORED

        index = json.loads(
            (sandbox / optimize.EXPERIENCE_DIR / "index.json").read_text(encoding="utf-8")
        )
        rows = {row["iteration"]: row for row in index}
        assert rows[1]["score"] is None and rows[1]["score_state"] == optimize.SCORE_UNSCORED
        assert rows[2]["score_state"] == optimize.SCORE_SCORED
