"""Budgeted search over PClaw's own harness artifacts.

The proactive half of the eval substrate: a hill-climbing search that tries to improve one
of PClaw's *own* artifacts — a workflow template's prompt blocks, a skill body, an SOP —
and hands the winner to a human as a proposal. The bundled ``optimize-harness`` template is
the declarative packaging; this module is the machinery its steps call, and :func:`run_search`
is the same search driven in one process, through the same functions.

Five properties make the difference between a search and a liability, and each is a
mechanism here rather than a promise in a prompt:

* **Nothing live mutates.** Candidates are written into a throwaway sandbox and only ever
  read from the live artifact. :class:`LiveWitness` records the bytes of the live artifact
  (and any other path the caller names) BEFORE the search and re-reads them after; a
  mismatch raises :class:`LiveMutationError`. That is deliberately an *observation* and not
  an assertion of intent: a search that wrote into the live tree and then restored it would
  fail this check, which is the only version of the guarantee worth having.
* **A frozen-region touch is fatal regardless of score.** :func:`scope_check` reuses the
  engine's own snapshot/diff (``workflows.scope``) and adds the one rule the engine's
  ``allowed_write_paths`` cannot express on its own: a *frozen* root — the live artifact and
  its ``.pclaw-lock.json`` — is a violation even when it is also inside an allowed root.
  Frozen wins over allowed, because "allowed" is a scoping default and "frozen" is a
  decision.
* **A score is measured, never claimed.** Every candidate is scored by the product's own
  evaluation of it against the target's own runs (:mod:`personalclaw.evals.candidate_score`).
  The proposer proposes; nothing it says about its own candidate is read as a score.
* **The gate is DUAL, and its bar rises.** :class:`DualGate` admits a candidate only when it
  clears BOTH the harvested-suite threshold AND the best score so far. Either half alone is
  decoration: the threshold alone re-admits a candidate that already lost to a better one, and
  the best score alone admits a candidate that beats a bad incumbent while still failing the
  suite. The best score starts at the best-ever floor read from ``results.tsv`` ONCE, before
  the search starts (:func:`capture_best_ever`) — a floor re-read from the rows the search is
  writing would be pinned by the value it is meant to pin — and rises with every candidate the
  search admits, so a later candidate that scores below an earlier winner is not admitted and
  cannot become the winner.
* **The search halts, and holds its budget.** Four declared conditions, decided after each
  candidate by one function for both drivers (:func:`halt_for`): ``hypothesis_abandon_after``
  (the same fix tried N times without the gate admitting it — the diagnosis is wrong),
  ``no_improvement_halt`` (N candidates in a row that did not raise the best score),
  ``max_iterations``, and ``budget_usd``. The budget is held to what the search actually spent,
  measured from its own model calls (a template run's usage-ledger rows,
  :func:`personalclaw.workflows.ownership.run_spend`; the in-process driver's call log), and
  :func:`budget_stop` is asked before every cycle and before every scoring: the search stops
  when the next one would pass the budget, and says so. Each scoring call is held to what is
  left too (:func:`held_to`), so the model-call guard refuses a call that would pass it.

The stop-condition arithmetic is deliberately the SAME shape as
:mod:`personalclaw.loop.tick`'s ``hypothesis_exhausted`` / ``no_progress`` detectors —
identical failed fixes in a window, and a progress window whose max never rises above its first
mark. Two dialects of "it stopped improving" would be one more than this codebase can
afford; the subjects differ (loop cycles there, search candidates here) but the rule does
not.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.evals import candidate_score
from personalclaw.evals.candidate_score import CandidateScore, CannotScore, unmeasured
from personalclaw.guardrails.calls import DONE, CallLog, capture_model_calls
from personalclaw.workflows import scope as scope_mod

logger = logging.getLogger(__name__)

#: The ``kind`` column this search writes to ``results.tsv``, and the key its best-ever
#: floor is read back under. One value, so a later reader cannot half-match the history.
SEARCH_KIND = "optimize_harness"

#: The lock file that pins an installed artifact's content hashes. Never written by a
#: search — it is the second half of the frozen region, alongside the artifact itself.
LOCK_NAME = ".pclaw-lock.json"

#: The per-iteration experience directory. Prior candidates' diffs, scores and check
#: results, indexed — MetaHarness's finding was that agentic proposers do measurably better
#: reading the raw prior artifacts than a compressed summary of them.
EXPERIENCE_DIR = ".experience"

# ── declared stop conditions ─────────────────────────────────────────────────

#: The auto-harness field values. Kept equal to ``loop.tick``'s defaults on
#: purpose: the same rule with two different numbers would be two rules.
DEFAULT_HYPOTHESIS_ABANDON_AFTER = 3
DEFAULT_NO_IMPROVEMENT_HALT = 5
DEFAULT_MAX_ITERATIONS = 12

#: The most candidates one search may try, whatever its ``max_iterations`` asks for. The bundled
#: template's loop is capped at exactly this (the engine bounds a loop only by a literal cap:
#: ``template_lint``'s ``WFL_TANGLED_LOOP``), and the search halts itself at its own
#: ``max_iterations`` before the loop's cap is reached, so a larger request is refused at preflight
#: rather than cut short by the loop and handed to a person with its winner unfiled.
ITERATION_CEILING = 50


class HaltReason(str, Enum):
    """Why a search stopped. Closed, and every member is reachable from :func:`run_search`.

    There is no ``UNKNOWN``: a search that stopped for a reason nobody named is a search
    whose budget nobody can account for, and the ledger row would carry a blank where the
    only interesting column is.
    """

    #: The same fix tried ``hypothesis_abandon_after`` times without being admitted — the
    #: diagnosis is wrong.
    HYPOTHESIS_ABANDONED = "hypothesis_abandoned"
    #: ``no_improvement_halt`` consecutive candidates without raising the best score.
    NO_IMPROVEMENT = "no_improvement_halt"
    #: What the search spent reached ``budget_usd``, its next cycle or scoring would pass it, or
    #: part of its spend had no price and so could not be held to it (:func:`budget_stop`).
    BUDGET_EXHAUSTED = "budget_usd"
    #: ``max_iterations`` reached with the other three still quiet.
    ITERATIONS_EXHAUSTED = "iterations_exhausted"
    #: The proposer ran out of candidates before any ceiling bit.
    PROPOSER_EXHAUSTED = "proposer_exhausted"


class CandidateOutcome(str, Enum):
    """One candidate's fate. ``SCOPE_VIOLATION`` is terminal for the candidate and is
    recorded whether or not the score would have won — "dead regardless of score"."""

    ADMITTED = "admitted"
    SCOPE_VIOLATION = "scope_violation"
    NO_CHANGE = "no_change"
    #: The scorer did not measure it: its edit did not apply, too few runs could be judged, or the
    #: budget stopped the scoring. The row's ``note`` says which.
    NOT_SCORED = "not_scored"
    BELOW_SUITE_THRESHOLD = "below_suite_threshold"
    NOT_BEST_EVER = "not_best_ever"


#: The outcomes a candidate reaches WITHOUT a measurement: a scope violation is dead regardless
#: of score, an empty candidate inherits the incumbent rather than being re-evaluated, and a
#: candidate the scorer could not measure has no score at all. Closed, and every
#: :class:`CandidateOutcome` member sits on exactly one side of it (railed in
#: ``tests/test_evals_optimize.py``) — an outcome added without being classified would default to
#: "scored" and publish its ``0.0`` as a measurement.
UNSCORED_OUTCOMES: frozenset[str] = frozenset(
    {
        CandidateOutcome.SCOPE_VIOLATION.value,
        CandidateOutcome.NO_CHANGE.value,
        CandidateOutcome.NOT_SCORED.value,
    }
)

#: The three states a score column can be in, and the whole reason they are NAMED rather than
#: left to a number.
#:
#: ``results.tsv`` is pinned::func:`personalclaw.evals.store.append_result` refuses a row
#: without a complete :class:`~personalclaw.evals.pinning.RunPin`, and a candidate that a
#: caller-supplied scorer never ran has no honest model fingerprint to pin. So an unscored
#: candidate writes NO results row — and inventing a fingerprint to force one would poison every
#: per-fingerprint baseline that reads the same file, which surfaces months later as an
#: inexplicable regression. The absence is therefore RENDERED, not filled: a reader sees
#: :data:`SCORE_UNSCORED` where a number would be, exactly as an unknown evidence tier renders
#: ``ungraded`` rather than falling back to a grade.
#:
#: Three values because a COUNT cannot tell them apart. "the scorer ran", "the scorer never ran"
#: and "there was nothing to run it on" all leave the same empty score, and only the first of
#: them means the candidate was measured and came up at nothing.
SCORE_SCORED = "scored"
SCORE_UNSCORED = "unscored"
#: The same string ``retrieval_bench.REASON_NO_CANDIDATES`` uses for the same state, on purpose:
#: two spellings of "there was nothing to measure" would be two vocabularies.
SCORE_NO_CANDIDATES = "no_candidates"


class LiveMutationError(RuntimeError):
    """The live artifact changed while the search was running.

    Raised by :meth:`LiveWitness.assert_unchanged`. This is the one failure in this module
    that must never be swallowed: everything else the search can get wrong costs tokens,
    and this one costs the user's artifact.
    """


class OptimizeRefusedError(ValueError):
    """A search was asked for that cannot be run honestly (no budget, sandbox inside the
    frozen region, a target no candidate of which can be scored). Refusing before the first
    model call is the point."""


@dataclass(frozen=True)
class StopConditions:
    """The declared halt envelope. ``budget_usd`` has no default on purpose.

    An unbudgeted search is the failure mode this whole section exists to prevent, so
    ``budget_usd <= 0`` is a refusal (:meth:`validate`) rather than "unlimited" — which is
    what a 0 means everywhere else in :class:`~personalclaw.guardrails.budgets.Budget` and
    exactly why it is checked here.
    """

    budget_usd: float = 0.0
    hypothesis_abandon_after: int = DEFAULT_HYPOTHESIS_ABANDON_AFTER
    no_improvement_halt: int = DEFAULT_NO_IMPROVEMENT_HALT
    max_iterations: int = DEFAULT_MAX_ITERATIONS

    @classmethod
    def from_config(cls, raw: Any) -> StopConditions:
        """Parse a template node's declared stop conditions.

        A missing window falls back to the declared default; a NON-POSITIVE one does too,
        because a window of 0 would disable the halt it names, and a template that names a
        halt has asked for it. ``budget_usd`` is the exception: it is passed through
        unchanged so :meth:`validate` can refuse it rather than invent a ceiling nobody
        approved.
        """
        cfg = raw if isinstance(raw, dict) else {}
        return cls(
            budget_usd=_as_float(cfg.get("budget_usd")),
            hypothesis_abandon_after=_window(
                cfg.get("hypothesis_abandon_after"), DEFAULT_HYPOTHESIS_ABANDON_AFTER
            ),
            no_improvement_halt=_window(
                cfg.get("no_improvement_halt"), DEFAULT_NO_IMPROVEMENT_HALT
            ),
            max_iterations=_window(cfg.get("max_iterations"), DEFAULT_MAX_ITERATIONS),
        )

    def validate(self) -> None:
        if self.budget_usd <= 0.0:
            raise OptimizeRefusedError(
                "optimize-harness refuses to search without a positive `budget_usd`: "
                "an unbudgeted search over model calls has no ceiling at all, and 0 means "
                "UNLIMITED to the guardrails Budget it would be handed"
            )
        if self.max_iterations > ITERATION_CEILING:
            raise OptimizeRefusedError(
                f"optimize-harness tries at most {ITERATION_CEILING} candidates, and "
                f"`max_iterations` asks for {self.max_iterations}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_usd": self.budget_usd,
            "hypothesis_abandon_after": self.hypothesis_abandon_after,
            "no_improvement_halt": self.no_improvement_halt,
            "max_iterations": self.max_iterations,
        }


def _window(raw: Any, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _as_float(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _maybe_float(raw: Any) -> float | None:
    """A recorded amount, or ``None`` when none was recorded: an absent cost is not a free one."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# ── the frozen region + the scope check ──────────────────────────────────────


def frozen_roots(target: str | os.PathLike[str]) -> list[str]:
    """The paths a candidate may never touch: the live artifact and its lock file.

    Returned normalized, because the comparison is against ``scope.diff``'s already
    normalized paths and a symlinked target compared as-written would be invisible.
    """
    live = Path(scope_mod.normalize(target))
    lock = live / LOCK_NAME if live.is_dir() else live.parent / LOCK_NAME
    return [str(live), scope_mod.normalize(lock)]


@dataclass(frozen=True)
class FrozenScopeReport:
    """The engine's ``ScopeReport`` plus the frozen-region touches it cannot express.

    A REPORT, not a verdict, and named that way deliberately: this is a scope diff in the
    write-scope domain, not a judge verdict, and the ``verdict-type`` ratchet's own rationale
    says a decision in a different domain should not carry the verdict name. Composing the
    engine's ``ScopeReport`` rather than restating its fields keeps one description of "what
    changed" and adds exactly the one thing it lacks.
    """

    report: scope_mod.ScopeReport
    frozen_touched: tuple[str, ...] = ()

    @property
    def violation(self) -> bool:
        """A violation is an escape from the allowed set OR any frozen-region touch.

        ``report.incomplete`` also counts: a truncated snapshot means the diff did not
        observe the whole tree, and reading "no violations found" off an incomplete
        observation is how a search quietly acquires write access.
        """
        return bool(self.frozen_touched) or not self.report.clean or self.report.incomplete

    @property
    def outcome(self) -> CandidateOutcome | None:
        return CandidateOutcome.SCOPE_VIOLATION if self.violation else None

    def to_dict(self) -> dict[str, Any]:
        return {**self.report.to_dict(), "frozen_touched": list(self.frozen_touched)}


def scope_check(
    before: scope_mod.Snapshot,
    after: scope_mod.Snapshot,
    *,
    allowed: Sequence[str],
    frozen: Sequence[str],
) -> FrozenScopeReport:
    """Classify a candidate's writes against the allowed scope AND the frozen region.

    The frozen check runs over ``report.changed`` — every created/modified/deleted path —
    rather than over ``report.violations``, and that ordering is the whole point: a frozen
    path that is *also* inside an allowed root produces no violation from
    ``scope.diff``, so a frozen-region touch inside the sandbox's own allowed tree would
    otherwise pass. Frozen beats allowed.
    """
    report = scope_mod.diff(before, after, list(allowed))
    frozen_norm = [scope_mod.normalize(p) for p in frozen if str(p or "").strip()]
    touched = tuple(sorted(p for p in report.changed if scope_mod.in_scope(p, frozen_norm)))
    return FrozenScopeReport(report=report, frozen_touched=touched)


# ── the live witness (the "nothing live mutates" observation) ─────────────────


#: The witness file the ``preflight`` subcommand leaves in the sandbox so the per-iteration
#: ``adjudicate`` subcommand can re-check the frozen region in a LATER process. The in-process
#: driver keeps its witness in memory; the template's bash nodes cannot, and a guarantee that
#: only holds inside one process is not the guarantee.
WITNESS_FILE = "witness.json"

#: What ``preflight`` measured for the steps after it: the runs every candidate is scored
#: against, and what the run had spent when the search started. Kept in the sandbox for the same
#: reason the witness is: the steps that read it run in later processes.
PREFLIGHT_FILE = "preflight.json"


def content_digest(roots: Sequence[str | os.PathLike[str]]) -> dict[str, str]:
    """path → sha256, for every file under every named root.

    Content, not mtimes: a search that rewrote a file with identical bytes has not mutated
    anything a user can observe, and one that rewrote it with different bytes and restored
    the mtime absolutely has. ``scope.Snapshot`` is the right tool for detecting *writes*
    within one process; this is the right tool for proving *state* across two.
    """
    out: dict[str, str] = {}
    for root in roots:
        path = Path(scope_mod.normalize(root))
        files = [path] if path.is_file() else (sorted(path.rglob("*")) if path.is_dir() else [])
        for child in files:
            if child.is_file():
                out[scope_mod.normalize(child)] = _sha256_file(child)
    return out


def digest_drift(recorded: dict[str, str], roots: Sequence[str | os.PathLike[str]]) -> list[str]:
    """Paths that appeared, vanished, or changed content since ``recorded`` was taken."""
    now = content_digest(roots)
    changed = {p for p in recorded if recorded.get(p) != now.get(p)}
    return sorted(set(recorded) ^ set(now) | changed)


def _sha256_file(path: Path) -> str:
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:  # pragma: no cover - raced unlink mid-walk
        return "<unreadable>"


@dataclass
class LiveWitness:
    """Content digests of every live path the search must not touch, taken before it starts."""

    files: dict[str, str] = field(default_factory=dict)
    roots: tuple[str, ...] = ()

    @classmethod
    def capture(cls, roots: Sequence[str | os.PathLike[str]]) -> LiveWitness:
        norm = tuple(scope_mod.normalize(r) for r in roots if str(r or "").strip())
        return cls(files=content_digest(norm), roots=norm)

    def drift(self) -> list[str]:
        return digest_drift(self.files, self.roots)

    def assert_unchanged(self, *, context: str = "") -> None:
        drifted = self.drift()
        if drifted:
            where = f" ({context})" if context else ""
            raise LiveMutationError(
                f"the live artifact changed during the search{where}: " f"{', '.join(drifted[:5])}"
            )

    def persist(self, sandbox: str | os.PathLike[str]) -> Path:
        """Write the witness into the sandbox for a later process to re-check against."""
        path = Path(sandbox) / WITNESS_FILE
        atomic_write(
            path,
            json.dumps({"roots": list(self.roots), "files": self.files}, indent=2, sort_keys=True),
        )
        return path

    @classmethod
    def restore(cls, sandbox: str | os.PathLike[str]) -> LiveWitness | None:
        """Read a persisted witness back, or ``None`` when the sandbox has none.

        ``None`` is NOT "clean": every caller treats a missing witness as un-adjudicable,
        because a frozen-region check with nothing to compare against would pass every
        candidate, which is the same as not checking.
        """
        path = Path(sandbox) / WITNESS_FILE
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # pragma: no cover - a half-written witness
            return None
        if not isinstance(payload, dict):  # pragma: no cover - hand-edited witness
            return None
        return cls(
            files={str(k): str(v) for k, v in (payload.get("files") or {}).items()},
            roots=tuple(str(r) for r in (payload.get("roots") or [])),
        )


# ── the dual gate ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BestEver:
    """The monotonic best-ever score, captured ONCE from ``results.tsv``.

    ``rows_considered`` is carried so a surprising floor is explainable without re-reading
    the ledger, and so "no history" (0 rows, floor 0.0) is distinguishable from "everything
    scored 0" (n rows, floor 0.0). Those two are the same number and completely different
    situations.
    """

    value: float = 0.0
    rows_considered: int = 0
    subject: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_ever": self.value,
            "rows_considered": self.rows_considered,
            "subject": self.subject,
        }


def capture_best_ever(subject: str, *, rows: Sequence[dict] | None = None) -> BestEver:
    """Read the best-ever score for ``subject`` out of the append-only results ledger.

    Called ONCE, before the search's first iteration. The floor must not be recomputed
    per-iteration: the search appends its own rows to the same ledger, so a re-read would
    fold the candidate being scored into the floor that is supposed to pin it, and the
    "monotonic best-ever" half of the gate would degrade to "beat yourself", which every
    candidate does. What rises during the search is the bar above the floor
    (:meth:`DualGate.bar`), and it rises only with the candidates the gate admits.
    """
    if rows is None:
        from personalclaw.evals import store

        rows = store.read_results()
    best = 0.0
    seen = 0
    for row in rows:
        if str(row.get("kind") or "") != SEARCH_KIND:
            continue
        if subject and str(row.get("study_id") or "") != subject:
            continue
        seen += 1
        best = max(best, _as_float(row.get("score_new")))
    return BestEver(value=best, rows_considered=seen, subject=subject)


@dataclass(frozen=True)
class DualGate:
    """The keep/discard rule: BOTH halves, or the candidate is discarded.

    The halves are separate predicates rather than one boolean expression so each can be
    railed on its own — a gate whose two halves are only ever observed together is a gate
    that could be admitting on one of them.
    """

    #: Half A — the harvested regression suite's pass threshold (the GateOK floor).
    suite_threshold: float
    #: Where half B's bar starts: the monotonic best-ever from ``results.tsv``, frozen at capture.
    best_ever: BestEver

    def clears_suite_threshold(self, score: float) -> bool:
        """At-or-above the suite floor. Inclusive: the threshold IS the passing mark."""
        return score >= self.suite_threshold

    def bar(self, rows: Sequence[LedgerRow]) -> float:
        """Half B's bar after *rows*: the best-ever floor, raised by every candidate admitted.

        Read from the rows themselves rather than carried in a variable, so both drivers — one
        process holding the rows, and the template's steps each reading them back from the
        sandbox — compute the same bar from the same ledger.
        """
        admitted = [r.score for r in rows if r.outcome == CandidateOutcome.ADMITTED.value]
        return max([self.best_ever.value, *admitted])

    def beats(self, score: float, best_so_far: float) -> bool:
        """STRICTLY above the best so far. Ties lose — hill-climbing on equal scores is how
        a search spends a budget wandering a plateau and calls the last step a win."""
        return score > best_so_far

    def decide(self, score: float, *, best_so_far: float) -> CandidateOutcome:
        """The verdict on a candidate that scored *score* when the bar was *best_so_far*.

        Required, with no default: a caller that forgot the bar would compare every candidate
        with the floor the search started from, and admit a later candidate that lost to an
        earlier winner.
        """
        if not self.clears_suite_threshold(score):
            return CandidateOutcome.BELOW_SUITE_THRESHOLD
        if not self.beats(score, best_so_far):
            return CandidateOutcome.NOT_BEST_EVER
        return CandidateOutcome.ADMITTED

    def to_dict(self) -> dict[str, Any]:
        return {"suite_threshold": self.suite_threshold, **self.best_ever.to_dict()}


# ── candidates and the per-iteration ledger ──────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    """One proposed edit, as the proposer handed it over.

    ``fix_fingerprint`` is the proposer's own identity for the *fix it is attempting* — not
    for the diff text. Two textually different edits that attack the same misdiagnosed
    cause share a fingerprint, and that is precisely what ``hypothesis_abandon_after``
    needs to see in order to abandon the hypothesis rather than the wording.
    """

    iteration: int
    fix_fingerprint: str
    diff_text: str = ""
    ops: tuple[dict[str, Any], ...] = ()
    rationale: str = ""

    @property
    def no_change(self) -> bool:
        """An empty edit. These inherit the incumbent score without re-evaluation —
        scoring an unchanged artifact spends the suite's whole cost to learn nothing."""
        return not self.diff_text.strip() and not self.ops


@dataclass
class LedgerRow:
    """One iteration's row, written for EVERY candidate including the discards.

    A ledger of winners is a ledger that cannot answer "why did this cost $4" — the
    discards are most of the spend, so they are most of the record.
    """

    iteration: int
    outcome: str
    score: float
    fix_fingerprint: str = ""
    best_so_far: float = 0.0
    scope: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    #: The candidate's typed edit, as the proposer gave it: what a winner is FILED with
    #: (``propose_template_diff``), so the row of an admitted candidate carries it.
    ops: list[dict[str, Any]] = field(default_factory=list)
    #: Why the proposer made the edit, in its words: what a filed winner's rationale opens with.
    rationale: str = ""
    #: What the search had spent when this candidate's cycle ended, measured from its own model
    #: calls. The difference between two rows is what one cycle cost (:func:`search_spend`).
    spent_usd: float | None = None
    #: What scoring this candidate cost, ``None`` when nothing was spent scoring it or part of
    #: what was spent had no price.
    score_usd: float | None = None

    @property
    def scored(self) -> bool:
        """Whether the scorer actually ran on this candidate.

        Derived from ``outcome`` rather than carried as its own flag because ``outcome`` is the
        field that survives the experience index's write → read → rewrite round trip:
        :func:`_cmd_adjudicate` reads prior rows back through :func:`_as_float`, which turns the
        rendered ``None`` into ``0.0`` — the very zero this property exists to keep out of the
        record. A flag stored beside the score would be lost on the same trip.
        """
        return self.outcome not in UNSCORED_OUTCOMES

    def to_dict(self) -> dict[str, Any]:
        """The row as a human (and the report node's prompt) reads it out of ``index.json``.

        ``score`` is ``None`` and never ``0.0`` for an unscored candidate, and ``score_state``
        says which of the three states that is in words — a bare ``null`` is an empty cell, and
        an empty cell reads the same as "measured, and it was nothing".
        """
        scored = self.scored
        return {
            "iteration": self.iteration,
            "outcome": self.outcome,
            "score": self.score if scored else None,
            "score_state": SCORE_SCORED if scored else SCORE_UNSCORED,
            "fix_fingerprint": self.fix_fingerprint,
            "best_so_far": self.best_so_far,
            "scope": dict(self.scope),
            "note": self.note,
            "ops": [dict(op) for op in self.ops],
            "rationale": self.rationale,
            "spent_usd": self.spent_usd,
            "score_usd": self.score_usd,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LedgerRow:
        """A row read back out of ``index.json``, every field it was written with kept.

        :func:`_cmd_adjudicate` rewrites the whole index from the rows it reads back, so a field
        this dropped would be gone from the ledger after the next iteration. ``score`` is read
        through :func:`_as_float`, and the rendered ``None`` of an unscored row comes back
        ``0.0``; :attr:`scored` reads ``outcome``, so the row still renders unscored. The two
        amounts keep ``None``: an amount nobody recorded must not come back as a free one.
        """
        scope = raw.get("scope")
        return cls(
            iteration=int(raw.get("iteration") or 0),
            outcome=str(raw.get("outcome") or ""),
            score=_as_float(raw.get("score")),
            fix_fingerprint=str(raw.get("fix_fingerprint") or ""),
            best_so_far=_as_float(raw.get("best_so_far")),
            scope=dict(scope) if isinstance(scope, dict) else {},
            note=str(raw.get("note") or ""),
            ops=_ops(raw.get("ops")),
            rationale=str(raw.get("rationale") or ""),
            spent_usd=_maybe_float(raw.get("spent_usd")),
            score_usd=_maybe_float(raw.get("score_usd")),
        )


def _ops(raw: Any) -> list[dict[str, Any]]:
    """A candidate's typed ops from a list, or from the JSON text of one (the ``PC_OPT_OPS``
    environment value); ``[]`` for anything else, so a malformed value files nothing."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except ValueError:
            return []
    return [dict(op) for op in raw if isinstance(op, dict)] if isinstance(raw, list) else []


def _winner(rows: Sequence[LedgerRow]) -> LedgerRow | None:
    """The search's winner: its last ADMITTED candidate, which the rising bar makes its best."""
    admitted = [r for r in rows if r.outcome == CandidateOutcome.ADMITTED.value]
    return admitted[-1] if admitted else None


@dataclass
class SearchOutcome:
    """What a completed search hands back. ``winner`` is ``None`` when nothing was admitted.

    ``needs_from_human`` is populated for exactly the halts that deserve one — a search
    that abandoned its hypothesis, ran out of improvement or reached its budget has learned
    something a person should read, whereas one that simply exhausted its iteration count has
    not. ``summary`` is the sentence the search ends with, for every halt: why it stopped and
    what it kept.
    """

    halt_reason: HaltReason
    halt_detail: str = ""
    iterations: int = 0
    winner: Candidate | None = None
    winner_score: float = 0.0
    rows: list[LedgerRow] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    needs_from_human: str = ""
    summary: str = ""
    #: What the search spent, measured from its own model calls; ``None`` when part of it had no
    #: price.
    spent_usd: float | None = None
    #: The runs the candidates were scored against: the evidence a filed winner names.
    evidence_runs: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.winner is not None

    @property
    def scored_candidates(self) -> int:
        """How many candidates the scorer actually ran on — the denominator under any headline
        number here, in the same shape ``retrieval_bench``'s ``scored_queries`` reports it."""
        return sum(1 for row in self.rows if row.scored)

    @property
    def results_state(self) -> str:
        """Which of the three states this search's results are in, as one of the
        :data:`SCORE_SCORED` / :data:`SCORE_UNSCORED` / :data:`SCORE_NO_CANDIDATES` names.

        ``no_candidates`` is its own value rather than a zero count because "the search measured
        nothing" and "the search had nothing to measure" are the same empty ledger to a count and
        entirely different situations to a person: the first spent its budget and learned nothing
        measurable, the second never got a candidate to spend it on.
        """
        if not self.rows:
            return SCORE_NO_CANDIDATES
        return SCORE_SCORED if self.scored_candidates else SCORE_UNSCORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "halt_reason": self.halt_reason.value,
            "halt_detail": self.halt_detail,
            "summary": self.summary,
            "iterations": self.iterations,
            "admitted": self.admitted,
            "winner_score": self.winner_score,
            "winner_fingerprint": self.winner.fix_fingerprint if self.winner else "",
            "results_state": self.results_state,
            "candidates": len(self.rows),
            "scored_candidates": self.scored_candidates,
            "unscored_candidates": len(self.rows) - self.scored_candidates,
            "spent_usd": self.spent_usd,
            "evidence_runs": list(self.evidence_runs),
            "rows": [r.to_dict() for r in self.rows],
            "gate": dict(self.gate),
            "needs_from_human": self.needs_from_human,
        }


# ── what the search spent, and the one budget check ──────────────────────────

#: What a budget check is asked before: the next cycle of the search, or the next scoring of a
#: candidate. A cycle's first costly step is its proposal, so the check before a cycle is the
#: check before its proposal too.
BEFORE_CYCLE = "cycle"
BEFORE_SCORING = "scoring"

#: Two amounts closer than this are one amount (charges round to 6 places).
_EPSILON = 1e-9


@dataclass(frozen=True)
class Spend:
    """What a search has spent, measured from the model calls it made, and what its steps cost.

    ``priced`` is ``False`` when some of those calls had no price: ``dollars`` is then a floor,
    and a dollar budget cannot be held to it (:func:`budget_stop`).
    """

    dollars: float
    priced: bool = True
    #: What each finished cycle of the search cost, oldest first.
    cycles: tuple[float, ...] = ()
    #: What scoring each candidate cost, oldest first, for the scorings that were priced.
    scorings: tuple[float, ...] = ()


def search_spend(dollars: float, priced: bool, rows: Sequence[LedgerRow], *, start: float) -> Spend:
    """The search's :class:`Spend`: *dollars* measured now, and its steps' costs from *rows*.

    A cycle's cost is the difference between what the search had spent when two consecutive
    cycles ended (each row's ``spent_usd``; *start* before the first), so it holds everything the
    cycle spent, its proposal and its scoring alike, as the measurement saw it.
    """
    marks = [float(start), *(r.spent_usd for r in rows if r.spent_usd is not None)]
    cycles = tuple(max(0.0, round(b - a, 6)) for a, b in zip(marks, marks[1:]))
    scorings = tuple(float(r.score_usd) for r in rows if r.score_usd is not None)
    return Spend(dollars=float(dollars), priced=bool(priced), cycles=cycles, scorings=scorings)


def _money(value: float) -> str:
    """Dollars as a person reads them: to the cent, or to four figures below a cent."""
    amount = float(value)
    return f"${amount:.2f}" if amount >= 0.01 or amount == 0 else f"${amount:.4g}"


def budget_stop(spend: Spend, budget_usd: float, *, before: str) -> str:
    """Why the search stops before its next cycle or scoring, as a clause; ``""`` to go on.

    THE budget check, for both drivers and at every point the search is asked to spend: before
    each cycle (*before* ``BEFORE_CYCLE``) and before each scoring (``BEFORE_SCORING``). It stops
    the search when:

    * part of what it spent had no price: the dollars it counted are a floor, and a budget held
      to a floor holds nothing (the guard refuses such a call for the same reason);
    * what it spent has reached the budget;
    * the next cycle or scoring would pass it: what was spent, plus the most one of them has cost
      so far, is more than the budget. Before the first of each there is no such measurement, and
      a scoring is then held call by call instead (:func:`held_to`).
    """
    budget = float(budget_usd)
    spent = _money(spend.dollars)
    if not spend.priced:
        return (
            f"some of its model calls had no price, so the {spent} it could count is only part "
            f"of what it spent, and its {_money(budget)} budget cannot be held to it"
        )
    if spend.dollars >= budget - _EPSILON:
        return f"it has spent {spent} of its {_money(budget)} budget"
    costs = spend.cycles if before == BEFORE_CYCLE else spend.scorings
    if costs:
        most = max(costs)
        if spend.dollars + most > budget + _EPSILON:
            step = "a cycle of the search" if before == BEFORE_CYCLE else "scoring a candidate"
            return (
                f"it has spent {spent} of its {_money(budget)} budget, and {step} has cost up to "
                f"{_money(most)}, so another would pass it"
            )
    return ""


@contextlib.contextmanager
def held_to(key: str, budget_usd: float, spent_usd: float) -> Iterator[None]:
    """Hold every model call made inside the block to what is left of the search's budget.

    The model-call guard admits each call against the ambient run's ceiling before the call is
    made (``guardrails.model_call.admit_call``) and charges it to the ambient run's key after.
    Binding the search's ``budget_usd`` there, with what the search had already spent charged to a
    fresh account under *key*, makes the guard refuse the call that would pass the budget — even
    on a search's first scoring, which no earlier scoring can project (:func:`budget_stop`). The
    account is dropped on the way out: what the search spent is read from its own ledger, never
    from this in-process counter.

    A ceiling the block is already inside still holds (``budgets.held_within``): a search an
    automation or a capped run started is held to the tighter of the two, and what its calls cost
    is charged to the enclosing account as the block ends.
    """
    from personalclaw.guardrails.budgets import Budget, held_within

    spent = max(0.0, float(spent_usd))
    with held_within(key, Budget(max_dollars=float(budget_usd)), starting_at=(0, spent)):
        yield


# ── the halt detectors, and the one decision both drivers take ───────────────


def hypothesis_abandoned(attempts: Sequence[tuple[str, bool]], window: int) -> bool:
    """The same fix tried ``window`` times without the gate admitting it.

    ``attempts`` is ``(fix_fingerprint, admitted)`` for each candidate, oldest first. The same
    arithmetic as ``loop.tick``'s ``hypothesis_exhausted``, applied the same way: a fix counts
    only when its attempt FAILED (``record_failure``), an admitted candidate clears the streak
    (``reset_after_success``), and an attempt that names no fix adds nothing. A fix the gate
    admitted raised the best score, so trying it again is the search climbing, not a wrong
    diagnosis. A full window of one fix fires; a proposer alternating two fixes is exploring,
    and abandoning it would be abandoning the search, not the hypothesis.
    """
    streak: list[str] = []
    for fingerprint, admitted in attempts:
        if admitted:
            streak = []
        elif fingerprint:
            streak.append(fingerprint)
    if window <= 0 or len(streak) < window:
        return False
    return len(set(streak[-window:])) == 1


def no_improvement(marks: Sequence[float], window: int) -> bool:
    """``window`` consecutive iterations whose best score never rose above the window's first.

    Same arithmetic as ``loop.tick``'s ``no_progress`` (``max(recent) <= recent[0]``), which
    is what makes a plateau a halt rather than a slowly-climbing search: a mark that ties the
    window's opening value is not improvement, and a strictly-greater one anywhere in the
    window keeps the search alive.
    """
    if window <= 0 or len(marks) < window:
        return False
    recent = list(marks[-window:])
    return max(recent) <= recent[0]


def halt_for(
    rows: Sequence[LedgerRow],
    stops: StopConditions,
    spend: Spend,
    *,
    scorer_stopped: str = "",
) -> tuple[HaltReason | None, str]:
    """Whether the search stops after its latest candidate, and why: ``(reason, clause)``.

    ONE decision for both drivers — :func:`run_search` after each candidate, and the template's
    ``adjudicate`` step after each cycle — so a halt cannot fire in one and not the other. In
    order: the budget stopped this candidate's scoring (*scorer_stopped*, the clause it stopped
    with); the hypothesis is abandoned; the best score has stopped rising; the candidates
    ``max_iterations`` allows are tried; the next cycle would pass the budget, which is the check
    before every cycle after the first.
    """
    if scorer_stopped:
        return HaltReason.BUDGET_EXHAUSTED, scorer_stopped
    window = stops.hypothesis_abandon_after
    attempts = [(r.fix_fingerprint, r.outcome == CandidateOutcome.ADMITTED.value) for r in rows]
    if hypothesis_abandoned(attempts, window):
        return HaltReason.HYPOTHESIS_ABANDONED, (
            f"its last {window} attempts at a fix all tried the same one "
            f"({rows[-1].fix_fingerprint}) and the gate admitted none of them, so the diagnosis "
            "behind it is wrong"
        )
    marks = [r.best_so_far for r in rows]
    if no_improvement(marks, stops.no_improvement_halt):
        return HaltReason.NO_IMPROVEMENT, (
            f"the best score stayed at {marks[-1]:.2f} across its last "
            f"{stops.no_improvement_halt} candidates"
        )
    if len(rows) >= stops.max_iterations:
        return HaltReason.ITERATIONS_EXHAUSTED, (
            f"it tried {len(rows)} candidates, the most its max_iterations allows"
        )
    clause = budget_stop(spend, stops.budget_usd, before=BEFORE_CYCLE)
    if clause:
        return HaltReason.BUDGET_EXHAUSTED, clause
    return None, ""


def kept_clause(rows: Sequence[LedgerRow]) -> str:
    """What the search kept, as a sentence: its winner and that winner's score, or nothing."""
    winner = _winner(rows)
    if winner is None:
        return "It kept no candidate: the gate admitted none of them."
    return f"It kept candidate {winner.iteration}, which scored {winner.score:.2f}."


def search_summary(halt: HaltReason, detail: str, rows: Sequence[LedgerRow]) -> str:
    """The sentence a search ends with: why it stopped, and what it kept."""
    lead = (
        "The search stopped at its budget"
        if halt is HaltReason.BUDGET_EXHAUSTED
        else "The search stopped"
    )
    return f"{lead}: {detail}. {kept_clause(rows)}"


def _needs_from_human(halt: HaltReason, summary: str) -> str:
    """The structured ``needs_from_human`` note — for the halts that earned one.

    ``ITERATIONS_EXHAUSTED`` and ``PROPOSER_EXHAUSTED`` do not: the first means the envelope
    was too small and the second means there was nothing to try, and neither is a question
    only a person can answer. Filing one for every halt would make the queue unreadable,
    which is the same as not filing them.
    """
    earned = {
        HaltReason.HYPOTHESIS_ABANDONED,
        HaltReason.NO_IMPROVEMENT,
        HaltReason.BUDGET_EXHAUSTED,
    }
    return summary if halt in earned else ""


# ── a candidate's row, from what its scoring measured ────────────────────────


def _score_note(result: CandidateScore) -> str:
    """What the scoring measured, as the row's note: how many runs passed of those judged."""
    note = f"the judge passed {result.passed} of the {result.judged} runs it could score"
    if result.rejected:
        note += f"; {result.rejected} could not be scored"
    return note


def scored_row(
    gate: DualGate,
    rows: Sequence[LedgerRow],
    candidate: Candidate,
    result: CandidateScore,
    *,
    scope: dict[str, Any],
) -> LedgerRow:
    """The ledger row for a candidate the scorer was run on, measured against the bar *rows* set.

    ``score_usd`` is what the scoring spent when it made a call at all: a scoring the budget
    refused before its first call spent nothing, and is no measurement of what a scoring costs.
    """
    bar = gate.bar(rows)
    spent_scoring = result.judged + result.rejected > 0 or bool(result.spent_usd)
    base = LedgerRow(
        iteration=candidate.iteration,
        outcome=CandidateOutcome.NOT_SCORED.value,
        score=0.0,
        fix_fingerprint=candidate.fix_fingerprint,
        best_so_far=bar,
        scope=scope,
        note=result.reason or "the scorer recorded no measurement for this candidate",
        ops=[dict(op) for op in candidate.ops],
        rationale=candidate.rationale,
        score_usd=result.spent_usd if spent_scoring else None,
    )
    if result.score is None:
        return base
    outcome = gate.decide(result.score, best_so_far=bar)
    admitted = outcome is CandidateOutcome.ADMITTED
    return replace(
        base,
        outcome=outcome.value,
        score=result.score,
        best_so_far=result.score if admitted else bar,
        note=_score_note(result),
    )


#: What a violation's and an empty edit's rows say. One wording for both drivers.
SCOPE_VIOLATION_NOTE = "frozen-region touch or write outside allowed_write_paths"
NO_CHANGE_NOTE = "empty candidate — inherited the incumbent score unscored"


def violation_row(
    gate: DualGate, rows: Sequence[LedgerRow], candidate: Candidate, *, scope: dict[str, Any]
) -> LedgerRow:
    """The row of a candidate that touched the frozen region or wrote outside its sandbox: dead
    regardless of score, so it reaches no scorer, scores nothing and does not move the bar."""
    return LedgerRow(
        iteration=candidate.iteration,
        outcome=CandidateOutcome.SCOPE_VIOLATION.value,
        score=0.0,
        fix_fingerprint=candidate.fix_fingerprint,
        best_so_far=gate.bar(rows),
        scope=scope,
        note=SCOPE_VIOLATION_NOTE,
        ops=[dict(op) for op in candidate.ops],
        rationale=candidate.rationale,
    )


def no_change_row(
    gate: DualGate, rows: Sequence[LedgerRow], candidate: Candidate, *, scope: dict[str, Any]
) -> LedgerRow:
    """The row of an empty candidate: it reaches no scorer and inherits the incumbent's score,
    so it does not move the bar either."""
    bar = gate.bar(rows)
    return replace(
        violation_row(gate, rows, candidate, scope=scope),
        outcome=CandidateOutcome.NO_CHANGE.value,
        score=bar,
        note=NO_CHANGE_NOTE,
    )


# ── the search ───────────────────────────────────────────────────────────────

#: A proposer is handed the iteration number and the experience index, and returns the next
#: candidate or ``None`` when it has nothing left. It NEVER receives a writable handle to
#: the live artifact — the sandbox path is all it gets.
Proposer = Callable[[int, Path, list[dict[str, Any]]], Awaitable[Candidate | None]]

#: A scorer is handed the candidate and its sandbox folder and returns what it measured. The
#: product's own (:func:`product_scorer`) is the default; it is the step that spends the most,
#: which is why the search checks its budget before every scoring and holds its calls to it.
Scorer = Callable[[Candidate, Path], Awaitable[CandidateScore]]


def product_scorer(subject: str, cases: Sequence[dict[str, Any]]) -> Scorer:
    """The product's evaluation of a candidate (:func:`candidate_score.score_candidate`), as a
    :data:`Scorer`: the same one the bundled template's scoring step runs, booking its calls to
    the search."""
    from personalclaw.usage_ledger import Attribution

    usage = Attribution(source="eval", session_key=f"{SEARCH_KIND}:{subject}")
    suite = list(cases)

    async def score(candidate: Candidate, cand_dir: Path) -> CandidateScore:
        return await candidate_score.score_candidate(
            subject=subject, ops=list(candidate.ops), cases=suite, usage=usage
        )

    return score


def _call_spend(calls: CallLog) -> tuple[float, bool]:
    """What the calls in *calls* cost: the dollars the priced ones did, and whether all were."""
    return float(calls.floor_cost_usd or 0.0), calls.cost_usd is not None


def _cost_since(calls: CallLog, mark: int) -> float | None:
    """What the calls made after the first *mark* cost, ``None`` when part of it is unknown."""
    later = calls.calls[mark:]
    if any(c.state != DONE or not c.priced for c in later):
        return None
    return round(sum(c.cost_usd for c in later), 6)


async def run_search(
    *,
    subject: str,
    live_target: str | os.PathLike[str],
    sandbox: str | os.PathLike[str],
    propose: Proposer,
    suite_threshold: float,
    stops: StopConditions,
    score: Scorer | None = None,
    cases: Sequence[dict[str, Any]] | None = None,
    best_ever: BestEver | None = None,
    witness_extra: Sequence[str | os.PathLike[str]] = (),
) -> SearchOutcome:
    """Run the budgeted hill-climb and return its outcome. Writes NOTHING outside ``sandbox``.

    The ordering is MetaHarness's and is not an implementation detail: scope-check and the
    cheap ``no_change`` validation both run BEFORE the scorer, because the scorer is the only
    expensive step and a candidate that escaped its scope or changed nothing must not be paid
    for. A search that scored first and scope-checked afterwards would have the same verdicts
    and a much larger bill.

    *score* defaults to the product's own (:func:`product_scorer`) over *cases*, which default
    to the target's own runs (:func:`candidate_score.load_cases`); a target none of whose
    candidates could be scored is refused before anything runs. What the search spent is
    measured from every guarded model call made inside it — its proposer's and its scorer's —
    and held to ``stops.budget_usd`` by the same checks the template's steps make.

    Raises :class:`OptimizeRefusedError` when the envelope is unbudgeted, the sandbox sits
    inside the frozen region (which would make every candidate a violation, or worse, make
    the frozen region writable), or no scorer can run. Raises :class:`LiveMutationError` if the
    live artifact moved.
    """
    stops.validate()
    frozen = frozen_roots(live_target)
    sandbox_path = Path(scope_mod.normalize(sandbox))
    if scope_mod.in_scope(str(sandbox_path), frozen):
        raise OptimizeRefusedError(
            f"the search sandbox {sandbox_path} is inside the frozen region "
            f"({', '.join(frozen)}) — candidates would be written over the live artifact"
        )
    suite = list(cases or ())
    if score is None:
        try:
            suite = suite or candidate_score.load_cases(subject)
        except CannotScore as exc:
            raise OptimizeRefusedError(str(exc)) from exc
        score = product_scorer(subject, suite)
    sandbox_path.mkdir(parents=True, exist_ok=True)
    allowed = [str(sandbox_path)]
    watch = sorted({*allowed, *frozen})

    gate = DualGate(
        suite_threshold=suite_threshold,
        best_ever=best_ever if best_ever is not None else capture_best_ever(subject),
    )
    # The witness hashes every file it watches, so it runs off the event loop, as whole-file
    # work must (`tests/test_event_loop_whole_file_census.py`).
    witness = await asyncio.to_thread(LiveWitness.capture, [*frozen, *witness_extra])
    key = f"{SEARCH_KIND}:{subject}"

    rows: list[LedgerRow] = []
    halt: HaltReason | None = None
    detail = ""
    iteration = 0
    with capture_model_calls() as calls:

        def spent() -> Spend:
            dollars, priced = _call_spend(calls)
            return search_spend(dollars, priced, rows, start=0.0)

        # The check before the first cycle, as the template's preflight makes it.
        opening = budget_stop(spent(), stops.budget_usd, before=BEFORE_CYCLE)
        if opening:
            halt, detail = HaltReason.BUDGET_EXHAUSTED, opening
        while halt is None:
            iteration += 1
            experience = read_experience(sandbox_path)
            # The snapshot BRACKETS the proposer, not just the candidate write. The proposer is
            # what a real search hands an agent, so it is the step that can escape its scope; a
            # diff taken after it returned would observe only this function's own writes and
            # report every escape as clean.
            before = scope_mod.snapshot(watch)
            candidate = await propose(iteration, sandbox_path, experience)
            if candidate is None:
                halt = HaltReason.PROPOSER_EXHAUSTED
                detail = f"the proposer had no candidate at iteration {iteration}"
                iteration -= 1
                break

            cand_dir = sandbox_path / f"candidate-{iteration:03d}"
            cand_dir.mkdir(parents=True, exist_ok=True)
            (cand_dir / "candidate.diff").write_text(candidate.diff_text, encoding="utf-8")
            after = scope_mod.snapshot(watch)
            scope_report = scope_check(before, after, allowed=allowed, frozen=frozen)

            stopped = ""
            if scope_report.violation:
                # Terminal for the candidate, and unscored: the "dead regardless of score".
                # It still costs an iteration and still counts as non-improving, because a
                # proposer that keeps escaping its scope must be allowed to exhaust the halts.
                row = violation_row(gate, rows, candidate, scope=scope_report.to_dict())
            elif candidate.no_change:
                # Cheap validation, before any LLM spend: an empty candidate inherits the
                # incumbent score rather than being re-evaluated.
                row = no_change_row(gate, rows, candidate, scope=scope_report.to_dict())
            else:
                now = spent()
                refusal = budget_stop(now, stops.budget_usd, before=BEFORE_SCORING)
                if refusal:
                    result = unmeasured(refusal, cases=len(suite), budget_stopped=True)
                else:
                    mark = len(calls.calls)
                    with held_to(key, stops.budget_usd, now.dollars):
                        result = await score(candidate, cand_dir)
                    result = replace(result, spent_usd=_cost_since(calls, mark))
                if result.budget_stopped:
                    stopped = result.reason
                row = scored_row(gate, rows, candidate, result, scope=scope_report.to_dict())

            row.spent_usd = _call_spend(calls)[0]
            rows.append(row)
            write_experience(sandbox_path, rows, candidate=candidate, iteration=iteration)
            halt, detail = halt_for(rows, stops, spent(), scorer_stopped=stopped)
        dollars, priced = _call_spend(calls)

    await asyncio.to_thread(witness.assert_unchanged, context=f"subject={subject}")
    assert halt is not None  # the loop above ends only on a halt
    winner_row = _winner(rows)
    summary = search_summary(halt, detail, rows)
    outcome = SearchOutcome(
        halt_reason=halt,
        halt_detail=detail,
        iterations=iteration,
        winner=(
            Candidate(
                iteration=winner_row.iteration,
                fix_fingerprint=winner_row.fix_fingerprint,
                ops=tuple(winner_row.ops),
                rationale=winner_row.rationale,
            )
            if winner_row is not None
            else None
        ),
        winner_score=winner_row.score if winner_row is not None else 0.0,
        rows=rows,
        gate=gate.to_dict(),
        needs_from_human=_needs_from_human(halt, summary),
        summary=summary,
        spent_usd=dollars if priced else None,
        evidence_runs=tuple(candidate_score.case_run_ids(suite)),
    )
    atomic_write(
        sandbox_path / "search.json",
        json.dumps(outcome.to_dict(), indent=2, sort_keys=True, default=str),
    )
    return outcome


# ── the experience directory ─────────────────────────────────────────────────


def write_experience(
    sandbox: Path, rows: Sequence[LedgerRow], *, candidate: Candidate, iteration: int
) -> Path:
    """Index the search's own history for the next proposer, RAW.

    The diffs are kept as files and the index points at them, rather than the index
    carrying a summary: MetaHarness measured +7.7pts for proposers reading the raw prior
    artifacts against ones reading a compression of them, and a summary written here is a
    compression nobody asked for.
    """
    exp = sandbox / EXPERIENCE_DIR
    exp.mkdir(parents=True, exist_ok=True)
    diff_path = exp / f"{iteration:03d}.diff"
    diff_path.write_text(candidate.diff_text, encoding="utf-8")
    index = [
        {**row.to_dict(), "diff_ref": f"{EXPERIENCE_DIR}/{row.iteration:03d}.diff"} for row in rows
    ]
    atomic_write(exp / "index.json", json.dumps(index, indent=2, sort_keys=True, default=str))
    return exp


def read_experience(sandbox: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """The experience index, or ``[]`` before the first iteration writes one."""
    path = Path(sandbox) / EXPERIENCE_DIR / "index.json"
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # pragma: no cover - a half-written index
        return []
    return payload if isinstance(payload, list) else []


def _ledger_rows(sandbox: str | os.PathLike[str]) -> list[LedgerRow]:
    return [LedgerRow.from_dict(r) for r in read_experience(sandbox) if isinstance(r, dict)]


# ── the winner becomes a PROPOSAL ────────────────────────────────────────────


def file_winner(
    subject: str,
    rows: Sequence[LedgerRow],
    *,
    halt: HaltReason,
    detail: str,
    gate: dict[str, Any],
    evidence_runs: Sequence[str],
) -> dict[str, Any]:
    """File the search's winner as a template-diff PROPOSAL. Applies NOTHING.

    ONE filing for both drivers (:func:`propose_winner` for :func:`run_search`, the template's
    ``file`` step), and no model is asked to do it: the winner, its ops, what it scored and why
    the search stopped are all in the ledger, so filing costs nothing and happens whatever the
    budget left, and what the proposal says about its score is the measurement rather than
    anybody's account of it.

    Routed through ``learning.refiner_tools.file_template_diff`` rather than through a
    second filing path of this module's own: that function already runs the frozen-field +
    legal-op gate and already enqueues into the one human-gated queue, and a search that
    filed its winner some other way would be a second way to install a template. The runs the
    candidates were scored against are the proposal's evidence.

    A search that admitted nothing files nothing — ``{"filed": False}`` with the halt
    reason, because "the search ran and found no improvement" is a result, not a failure to
    report. ``summary`` says, in every case, why the search stopped, what it kept and what
    became of it.
    """
    summary = search_summary(halt, detail, rows)
    winner = _winner(rows)
    if winner is None:
        return {
            "filed": False,
            "rejected": [f"no candidate was admitted (halted: {halt.value})"],
            "halt_reason": halt.value,
            "summary": f"{summary} Nothing was filed.",
        }
    from personalclaw.learning import refiner_tools

    before = [r for r in rows if r.iteration < winner.iteration]
    bar = max(
        [_as_float(gate.get("best_ever"))]
        + [r.score for r in before if r.outcome == CandidateOutcome.ADMITTED.value]
    )
    measured = (
        f"Found by an optimize-harness search over {len(rows)} candidates. Candidate "
        f"{winner.iteration} scored {winner.score:.2f} against {subject}'s own runs "
        f"({winner.note}), clearing BOTH the suite threshold "
        f"{_as_float(gate.get('suite_threshold')):g} and the best score before it, {bar:g}. "
        f"{summary}"
    )
    result = refiner_tools.file_template_diff(
        subject,
        ops=[dict(op) for op in winner.ops],
        rationale=f"{winner.rationale}\n\n{measured}".strip(),
        run_ids=list(evidence_runs),
        predicted_fixes=[winner.fix_fingerprint] if winner.fix_fingerprint else [],
    )
    if result.get("filed"):
        became = (
            f" It filed candidate {winner.iteration} as proposal {result.get('proposal_id')} "
            "for you to review; nothing was installed."
        )
    else:
        why = "; ".join(str(r) for r in (result.get("rejected") or [])) or str(
            result.get("verdict") or "the proposal queue did not take it"
        )
        became = f" Filing candidate {winner.iteration} was refused: {why}."
    return {**result, "halt_reason": halt.value, "summary": f"{summary}{became}"}


def propose_winner(outcome: SearchOutcome, *, workflow_name: str) -> dict[str, Any]:
    """File an in-process search's winner (:func:`file_winner`). Applies NOTHING."""
    return file_winner(
        workflow_name,
        outcome.rows,
        halt=outcome.halt_reason,
        detail=outcome.halt_detail,
        gate=outcome.gate,
        evidence_runs=outcome.evidence_runs,
    )


# ── the entry points the bundled template's steps call ───────────────────────

#: Env key → payload field, for the bundled template's ``bash`` nodes. CLOSED, and the
#: template is asserted against it (``tests/test_evals_optimize.py``): a ``PC_OPT_*`` key the
#: template sets and this map does not name is an input that is silently dropped, which is
#: how a declared ``budget_usd`` becomes no budget at all.
#:
#: Env rather than string-templating the command, for the reason ``bash_provider`` documents
#: at length: a payload VALUE interpolated into a command line is code. Read through
#: ``os.environ`` it is data.
#:
#: There is no score key: a candidate's score reaches ``adjudicate`` only as the scoring step's
#: whole recorded output (``PC_OPT_SCORE_RECORD``), never as a number a proposer could write.
ENV_PAYLOAD_KEYS: dict[str, str] = {
    "PC_OPT_SUBJECT": "subject",
    "PC_OPT_LIVE_TARGET": "live_target",
    "PC_OPT_SANDBOX": "sandbox",
    "PC_OPT_SUITE_THRESHOLD": "suite_threshold",
    "PC_OPT_BEST_EVER": "best_ever",
    "PC_OPT_ROWS_CONSIDERED": "rows_considered",
    "PC_OPT_SCORE_RECORD": "score_record",
    "PC_OPT_FIX_FINGERPRINT": "fix_fingerprint",
    "PC_OPT_DIFF_TEXT": "diff_text",
    "PC_OPT_OPS": "ops",
    "PC_OPT_RATIONALE": "rationale",
    "PC_OPT_HALT": "halt",
    "PC_OPT_HALT_DETAIL": "halt_detail",
}

#: The same, for the fields that nest under ``stops`` — the three declared halt conditions
#: plus the iteration floor under them.
ENV_STOP_KEYS: dict[str, str] = {
    "PC_OPT_BUDGET_USD": "budget_usd",
    "PC_OPT_ABANDON_AFTER": "hypothesis_abandon_after",
    "PC_OPT_NO_IMPROVEMENT_HALT": "no_improvement_halt",
    "PC_OPT_MAX_ITERATIONS": "max_iterations",
}

#: The run a step belongs to, as the engine hands it to every action step
#: (``workflows.engine.dispatch_action`` puts ``run_id`` in the payload, and the bash action
#: makes each payload key a variable). Read from the engine rather than set by the template: the
#: search's spend is that run's, and a template cannot forget to name it.
RUN_ID_ENV = "run_id"


def payload_from_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a subcommand payload out of the ``PC_OPT_*`` environment and the step's run.

    The alternative to stdin, and the one the bundled template uses: it keeps the template's
    bash command a single readable line instead of a shell-quoted JSON literal, which is the
    form that breaks the first time an input contains a double quote.
    """
    src = os.environ if env is None else env
    payload: dict[str, Any] = {}
    for key, field_name in ENV_PAYLOAD_KEYS.items():
        if key in src:
            payload[field_name] = src[key]
    stops = {name: src[key] for key, name in ENV_STOP_KEYS.items() if key in src}
    if stops:
        payload["stops"] = stops
    if src.get(RUN_ID_ENV):
        payload["run_id"] = src[RUN_ID_ENV]
    return payload


def _run_id(payload: dict[str, Any], step: str) -> str:
    """The run the step belongs to. Required: the search's spend is measured from that run's
    own model calls, and a step that cannot say whose they are cannot hold a budget."""
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise OptimizeRefusedError(
            f"{step} needs the run it belongs to: the search's spend is measured from that run's "
            "own model calls"
        )
    return run_id


def _run_spend(run_id: str) -> tuple[float, bool]:
    """What the run's model calls have cost so far, from the usage ledger, and whether all of
    them were priced (:func:`personalclaw.workflows.ownership.run_spend`)."""
    from personalclaw.workflows import ownership

    totals = ownership.run_spend(run_id)
    # To the micro-dollar, as every charge is: the ledger's sum of its rows carries float noise
    # (0.7000000000000001) that would otherwise reach the run's output.
    return round(float(totals.get("cost_usd") or 0.0), 6), bool(totals.get("priced", True))


def _preflight_record(sandbox: str) -> dict[str, Any]:
    """What ``preflight`` measured, read back from the sandbox. Refused when absent: every step
    after it scores against the runs it chose and budgets from the spend it recorded."""
    path = Path(sandbox) / PREFLIGHT_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if not isinstance(record, dict):
        raise OptimizeRefusedError(
            f"no {PREFLIGHT_FILE} in {sandbox} — run the `preflight` subcommand first; the search "
            "scores every candidate against the runs it chose"
        )
    return record


def _cmd_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    """Refuse-or-report BEFORE the first model call: the envelope, the scorer, the floor, the
    sandbox, the spend.

    Everything expensive about a search is downstream of this, so everything that can make
    the search dishonest is checked here — an unbudgeted envelope, a sandbox inside the
    frozen region, a target none of whose candidates could be scored (no definition, too few
    runs that ended), a run that has already spent its budget. Nothing is written until all of
    them pass.
    """
    stops = StopConditions.from_config(payload.get("stops"))
    stops.validate()
    subject = str(payload.get("subject") or "")
    run_id = _run_id(payload, "preflight")
    frozen = frozen_roots(str(payload.get("live_target") or ""))
    sandbox = Path(scope_mod.normalize(str(payload.get("sandbox") or ".")))
    if scope_mod.in_scope(str(sandbox), frozen):
        raise OptimizeRefusedError(f"sandbox {sandbox} is inside the frozen region")
    try:
        asyncio.run(candidate_score.live_definition(subject))
        cases = candidate_score.load_cases(subject)
    except CannotScore as exc:
        raise OptimizeRefusedError(str(exc)) from exc
    dollars, priced = _run_spend(run_id)
    spend = Spend(dollars=dollars, priced=priced)
    opening = budget_stop(spend, stops.budget_usd, before=BEFORE_CYCLE)
    if opening:
        raise OptimizeRefusedError(f"optimize-harness will not start: {opening}")
    best = capture_best_ever(subject)
    sandbox.mkdir(parents=True, exist_ok=True)
    witness = LiveWitness.capture(frozen)
    witness.persist(sandbox)
    atomic_write(
        sandbox / PREFLIGHT_FILE,
        json.dumps(
            {"subject": subject, "spent_usd": dollars, "cases": cases},
            indent=2,
            sort_keys=True,
            default=str,
        ),
    )
    return {
        "ok": True,
        "subject": subject,
        "stops": stops.to_dict(),
        "frozen_roots": frozen,
        "sandbox": str(sandbox),
        "witnessed_files": len(witness.files),
        "cases": len(cases),
        "case_runs": candidate_score.case_run_ids(cases),
        "spent_usd": dollars,
        **best.to_dict(),
    }


def _frozen_touched(sandbox: str) -> list[str]:
    """Re-check the frozen region against the witness ``preflight`` left in the sandbox.

    A missing witness is un-adjudicable rather than clean: ``preflight`` writes it, so its
    absence means the search skipped its own preflight, and a frozen-region check with
    nothing to compare against passes every candidate — which is the same as not checking.
    """
    witness = LiveWitness.restore(sandbox)
    if witness is None:
        raise OptimizeRefusedError(
            f"no {WITNESS_FILE} in {sandbox} — run the `preflight` subcommand first; "
            "a frozen-region check with nothing to compare against passes everything"
        )
    return witness.drift()


def _cmd_scope_check(payload: dict[str, Any]) -> dict[str, Any]:
    """The frozen-region diff, alone, so the template can REFUSE on it in a gate.

    Its own subcommand rather than a field of :func:`_cmd_adjudicate`'s output for a reason
    the engine imposes: a loop's exit ``condition`` must bind the LAST node of its body
    (anything else is ``WF_UNORDERED_DEP``), so the node the halt comes from cannot also be
    the node a mid-body gate depends on. Splitting it also puts the check where MetaHarness
    puts it — before the expensive half, not beside it.
    """
    sandbox = str(payload.get("sandbox") or "")
    if not sandbox:
        raise OptimizeRefusedError("scope-check needs a `sandbox` — it holds the witness")
    touched = _frozen_touched(sandbox)
    return {
        "ok": True,
        "outcome": (CandidateOutcome.SCOPE_VIOLATION.value if touched else "clean"),
        "clean": not touched,
        "frozen_touched": touched,
    }


def _record(raw: Any) -> dict[str, Any]:
    """The scoring step's recorded output, from a dict or the JSON text of one; ``{}`` else."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


async def score_step(payload: dict[str, Any]) -> dict[str, Any]:
    """The template's scoring step: measure this cycle's candidate, within the budget.

    Run IN the gateway by the ``optimize-score`` action, not in a child process, so the step's
    model calls are the engine's to measure: they are booked to the step on the run's own ledger
    and in the usage ledger under the run, which is where the search's spend is read back from
    (:func:`personalclaw.workflows.ownership.run_spend`).

    In MetaHarness's order, cheapest first: a candidate that touched the frozen region is dead and
    is not paid for; an empty edit inherits the incumbent; the budget is checked
    (:func:`budget_stop`, before a scoring) and a scoring that would pass it does not start; then
    the product's own scorer runs (:func:`candidate_score.score_candidate`), each of its calls
    held to what is left (:func:`held_to`). The answer is the measurement, which
    :func:`_cmd_adjudicate` reads as the candidate's only score.
    """
    from personalclaw.usage_ledger import Attribution
    from personalclaw.workflows import ownership

    sandbox = str(payload.get("sandbox") or "")
    if not sandbox:
        raise OptimizeRefusedError("the scoring step needs a `sandbox` — it holds the search")
    run_id = _run_id(payload, "the scoring step")
    subject = str(payload.get("target") or payload.get("subject") or "")
    stops = StopConditions.from_config({"budget_usd": payload.get("budget_usd")})
    stops.validate()
    # Off the event loop: the frozen-region check hashes the files the witness watches, and this
    # step runs in the gateway.
    touched = await asyncio.to_thread(_frozen_touched, sandbox)
    record = _preflight_record(sandbox)
    rows = _ledger_rows(sandbox)
    cases = [c for c in (record.get("cases") or []) if isinstance(c, dict)]
    ops = _ops(payload.get("ops"))
    iteration = len(rows) + 1

    def answer(result: CandidateScore, **extra: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "iteration": iteration,
            "score_state": SCORE_SCORED if result.scored else SCORE_UNSCORED,
            **result.to_dict(),
            **extra,
        }

    if touched:
        reason = "it touched the frozen region, which makes it dead whatever it would score"
        return answer(unmeasured(reason, cases=len(cases)), frozen_touched=touched)
    if not ops and not str(payload.get("diff_text") or "").strip():
        return answer(unmeasured(NO_CHANGE_NOTE, cases=len(cases)), no_change=True)
    dollars, priced = _run_spend(run_id)
    now = search_spend(dollars, priced, rows, start=_as_float(record.get("spent_usd")))
    refusal = budget_stop(now, stops.budget_usd, before=BEFORE_SCORING)
    if refusal:
        return answer(unmeasured(refusal, cases=len(cases), budget_stopped=True))
    node_id = str(payload.get("node_id") or "score")
    usage = Attribution(source="eval", session_key=ownership.owned_key(run_id, node_id))
    with held_to(f"{SEARCH_KIND}:{run_id}", stops.budget_usd, now.dollars):
        result = await candidate_score.score_candidate(
            subject=subject, ops=ops, cases=cases, usage=usage
        )
    return answer(result)


def _cmd_adjudicate(payload: dict[str, Any]) -> dict[str, Any]:
    """One iteration's verdict: frozen-region check, then the dual gate, then the halts.

    Split out from :func:`run_search` so the template's per-iteration bash node consults the
    SAME predicates the in-process driver does (:func:`scored_row`, :func:`halt_for`). Two
    implementations of "did this candidate win" is the shape this program keeps having to
    delete.

    The candidate's score is the scoring step's recorded measurement (``score_record``) and
    nothing else: there is no score field a proposer's answer could fill. The halt windows and
    the bar are read from the sandbox's own ``.experience`` index rather than passed in, so the
    accumulated history is the search's persisted ledger and not a model's memory of it, and
    what the search spent is read from the run's own model calls.
    """
    stops = StopConditions.from_config(payload.get("stops"))
    sandbox = str(payload.get("sandbox") or "")
    if not sandbox:
        raise OptimizeRefusedError("adjudicate needs a `sandbox` — it holds the search's ledger")
    run_id = _run_id(payload, "adjudicate")
    gate = DualGate(
        suite_threshold=_as_float(payload.get("suite_threshold")),
        best_ever=BestEver(
            value=_as_float(payload.get("best_ever")),
            rows_considered=int(payload.get("rows_considered") or 0),
            subject=str(payload.get("subject") or ""),
        ),
    )
    # The frozen-region half, re-checked here as well as in `scope-check`: the gate that
    # refuses on it is a template node, and a template node can be deleted. A candidate that
    # touched the frozen region must lose on the score path too, not only on the gate path.
    frozen_touched = _frozen_touched(sandbox)
    record = _preflight_record(sandbox)
    prior = _ledger_rows(sandbox)
    measured = _record(payload.get("score_record"))
    ops = _ops(payload.get("ops"))
    candidate = Candidate(
        iteration=len(prior) + 1,
        fix_fingerprint=str(payload.get("fix_fingerprint") or ""),
        diff_text=str(payload.get("diff_text") or ""),
        ops=tuple(ops),
        rationale=str(payload.get("rationale") or ""),
    )
    scope = {"frozen_touched": frozen_touched}
    result = CandidateScore.from_dict(measured)
    if frozen_touched:
        row = violation_row(gate, prior, candidate, scope=scope)
    elif measured.get("no_change") is True:
        row = no_change_row(gate, prior, candidate, scope=scope)
    else:
        row = scored_row(gate, prior, candidate, result, scope=scope)

    dollars, priced = _run_spend(run_id)
    row.spent_usd = dollars
    rows = [*prior, row]
    write_experience(Path(sandbox), rows, candidate=candidate, iteration=candidate.iteration)
    spend = search_spend(dollars, priced, rows, start=_as_float(record.get("spent_usd")))
    stopped = result.reason if result.budget_stopped and not frozen_touched else ""
    halt, detail = halt_for(rows, stops, spend, scorer_stopped=stopped)

    # The same rendering the ledger row carries, on the per-iteration verdict too: this dict is
    # what the template's later steps read as `{{nodes.search.output}}`, so echoing a number back
    # beside a `scope_violation` would hand a person a score for a candidate never scored.
    scored = row.scored
    return {
        "ok": True,
        "iteration": candidate.iteration,
        "outcome": row.outcome,
        "score": row.score if scored else None,
        "score_state": SCORE_SCORED if scored else SCORE_UNSCORED,
        "note": row.note,
        "admitted": row.outcome == CandidateOutcome.ADMITTED.value,
        "clears_suite_threshold": scored and gate.clears_suite_threshold(row.score),
        "frozen_touched": frozen_touched,
        "best_so_far": row.best_so_far,
        "spent_usd": dollars if priced else None,
        "halt": halt.value if halt is not None else "",
        "halt_detail": detail,
        "summary": search_summary(halt, detail, rows) if halt is not None else "",
        "continue": halt is None,
        "gate": gate.to_dict(),
    }


#: How much of one prior candidate's raw diff the ``experience`` step hands on, and of all of them
#: together. Its answer is bound into a model step's prompt, and a step's output past
#: ``ledger.writer.MAX_INLINE_OUTPUT_BYTES`` (64 KiB) is kept behind a stub that no binding can read
#: a field of, so the newest diffs are kept whole first and every cut one says it was cut.
EXPERIENCE_DIFF_CHARS = 6000
EXPERIENCE_TOTAL_DIFF_CHARS = 24000


def _cmd_experience(payload: dict[str, Any]) -> dict[str, Any]:
    """The search's own ledger as the template's model step reads it.

    ``candidates`` is every candidate so far, oldest first, each ledger row with its raw diff
    beside it; ``winner`` is the last ADMITTED one, the candidate the in-process search ends with
    too (:func:`run_search`), with the typed ops it is filed with, or ``None`` when nothing was
    admitted. A step of its own because the model step cannot open these files: the template
    refiner holds only its evidence and proposal tools (``TEMPLATE_REFINER_TOOLS``), so a prompt
    that told it to read ``.experience/index.json`` asked for a read it is refused. The candidates
    carry no ops (the diff says the same), which keeps the answer small enough to stay a value a
    binding can read; the winner carries them.

    A diff is read from the iteration's own file in this sandbox, never from a path the index
    names.
    """
    sandbox = str(payload.get("sandbox") or "")
    if not sandbox:
        raise OptimizeRefusedError("experience needs a `sandbox` — it holds the ledger")
    exp = Path(sandbox) / EXPERIENCE_DIR
    budget = EXPERIENCE_TOTAL_DIFF_CHARS
    candidates: list[dict[str, Any]] = []
    winner: dict[str, Any] | None = None
    for row in reversed(read_experience(sandbox)):
        path = exp / f"{int(row.get('iteration') or 0):03d}.diff"
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        keep = min(len(text), EXPERIENCE_DIFF_CHARS, budget)
        budget -= keep
        shown = {**row, "diff": text[:keep], "diff_cut": keep < len(text)}
        if winner is None and row.get("outcome") == CandidateOutcome.ADMITTED.value:
            winner = shown
        candidates.append({k: v for k, v in shown.items() if k != "ops"})
    candidates.reverse()
    return {"ok": True, "candidates": candidates, "winner": winner}


def _cmd_file(payload: dict[str, Any]) -> dict[str, Any]:
    """The template's last step: file the search's winner, and say how the search ended.

    The halt and its clause are the search loop's own last verdict (``PC_OPT_HALT``,
    ``PC_OPT_HALT_DETAIL``); the winner and the evidence are read from the sandbox
    (:func:`file_winner`).
    """
    sandbox = str(payload.get("sandbox") or "")
    if not sandbox:
        raise OptimizeRefusedError("file needs a `sandbox` — it holds the search's ledger")
    try:
        halt = HaltReason(str(payload.get("halt") or ""))
    except ValueError as exc:
        raise OptimizeRefusedError(
            "file needs the reason the search stopped — it reads the search's last verdict"
        ) from exc
    record = _preflight_record(sandbox)
    cases = [c for c in (record.get("cases") or []) if isinstance(c, dict)]
    gate = {
        "suite_threshold": _as_float(payload.get("suite_threshold")),
        "best_ever": _as_float(payload.get("best_ever")),
    }
    filed = file_winner(
        str(payload.get("subject") or record.get("subject") or ""),
        _ledger_rows(sandbox),
        halt=halt,
        detail=str(payload.get("halt_detail") or ""),
        gate=gate,
        evidence_runs=candidate_score.case_run_ids(cases),
    )
    return {"ok": True, **filed}


#: The subcommand table. The bundled ``optimize-harness`` template names these in its bash
#: nodes, so ``tests/test_evals_optimize.py`` asserts the template's names against THIS
#: dict — a renamed subcommand fails the template, not just this module. The scoring step is
#: not here: it runs in the gateway (:func:`score_step`, the ``optimize-score`` action).
COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "preflight": _cmd_preflight,
    "scope-check": _cmd_scope_check,
    "adjudicate": _cmd_adjudicate,
    "experience": _cmd_experience,
    "file": _cmd_file,
}


def main(argv: Sequence[str], stdin: Any = None) -> int:
    """``personalclaw optimize-harness <subcommand>``, the CLI command the bundled template's bash
    steps run, so a step reaches this install's own code wherever it is installed (a bash step's
    ``personalclaw`` is this install's CLI, ``bash_provider.own_cli_function``). The CLI hands
    the subcommand on as *argv*.

    The payload comes from stdin as JSON, or — when stdin is empty, which is how the bundled
    template calls it — from the ``PC_OPT_*`` environment (:func:`payload_from_env`).

    Errors come back as JSON on stdout with a non-zero exit, not as a traceback: the caller
    is a bash action whose output the engine parses, and a traceback there is an opaque
    failed node rather than a reason.
    """
    args = list(argv)
    name = args[0] if args else ""
    handler = COMMANDS.get(name)
    if handler is None:
        print(
            json.dumps(
                {"ok": False, "error": f"unknown subcommand {name!r}", "commands": sorted(COMMANDS)}
            )
        )
        return 2
    raw = (stdin if stdin is not None else sys.stdin).read().strip()
    try:
        payload = json.loads(raw) if raw else payload_from_env()
        result = handler(payload if isinstance(payload, dict) else {})
    except (OptimizeRefusedError, LiveMutationError, ValueError, OSError) as exc:
        # OSError too: a sandbox that cannot be made or a witness that cannot be written is a
        # reason the step can name, and a traceback would leave the node's output empty.
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True, default=str))
    return 0
