"""Budgeted search over PClaw's own harness artifacts (EVALUATION-SUBSTRATE §8, ES-11).

The proactive half of the eval substrate: a hill-climbing search that tries to improve one
of PClaw's *own* artifacts — a workflow template's prompt blocks, a skill body, an SOP —
and hands the winner to a human as a proposal. The bundled ``optimize-harness`` template is
the declarative packaging; this module is the machinery its nodes call.

Four properties make the difference between a search and a liability, and each is a
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
* **The gate is DUAL.** :class:`DualGate` admits a candidate only when it clears BOTH the
  harvested-suite threshold AND the monotonic best-ever score read from ``results.tsv``.
  Either half alone is decoration: the threshold alone re-admits a candidate that already
  lost to a better one, and best-ever alone admits a candidate that beats a bad incumbent
  while still failing the suite. The best-ever floor is captured ONCE, before the search
  starts (:func:`capture_best_ever`) — a floor recomputed from the rows the search is
  writing would be pinned by the value it is meant to pin, and would rise to whatever the
  last candidate scored.
* **The search halts.** Three declared conditions, three call sites in :func:`run_search`:
  ``hypothesis_abandon_after`` (the same fix attempted N× — the diagnosis is wrong),
  ``no_improvement_halt`` (N consecutive iterations that did not improve on the best score
  at the start of the window), and ``budget_usd`` (the guardrails :class:`SpendMeter`
  ceiling). A fourth, ``max_iterations``, is the trivial floor under all three.

The stop-condition arithmetic is deliberately the SAME shape as
:mod:`personalclaw.loop.tick`'s ``hypothesis_exhausted`` / ``no_progress`` detectors —
identical fixes in a window, and a progress window whose max never rises above its first
mark. Two dialects of "it stopped improving" would be one more than this codebase can
afford; the subjects differ (loop cycles there, search candidates here) but the rule does
not.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from personalclaw.guardrails.budgets import Budget, BudgetVerdict, SpendMeter
from personalclaw.workflows import scope as scope_mod

logger = logging.getLogger(__name__)

#: The ``kind`` column this search writes to ``results.tsv``, and the key its best-ever
#: floor is read back under. One value, so a later reader cannot half-match the history.
SEARCH_KIND = "optimize_harness"

#: The lock file that pins an installed artifact's content hashes. Never written by a
#: search — it is the second half of the frozen region, alongside the artifact itself.
LOCK_NAME = ".pclaw-lock.json"

#: The per-iteration experience directory (§8.4). Prior candidates' diffs, scores and check
#: results, indexed — MetaHarness's finding was that agentic proposers do measurably better
#: reading the raw prior artifacts than a compressed summary of them.
EXPERIENCE_DIR = ".experience"

# ── declared stop conditions ─────────────────────────────────────────────────

#: The auto-harness field values §8.2 names. Kept equal to ``loop.tick``'s defaults on
#: purpose: the same rule with two different numbers would be two rules.
DEFAULT_HYPOTHESIS_ABANDON_AFTER = 3
DEFAULT_NO_IMPROVEMENT_HALT = 5
DEFAULT_MAX_ITERATIONS = 12


class HaltReason(str, Enum):
    """Why a search stopped. Closed, and every member is reachable from :func:`run_search`.

    There is no ``UNKNOWN``: a search that stopped for a reason nobody named is a search
    whose budget nobody can account for, and the ledger row would carry a blank where the
    only interesting column is.
    """

    #: The same fix attempted ``hypothesis_abandon_after`` times — the diagnosis is wrong.
    HYPOTHESIS_ABANDONED = "hypothesis_abandoned"
    #: ``no_improvement_halt`` consecutive iterations without improving on the best score.
    NO_IMPROVEMENT = "no_improvement_halt"
    #: The ``budget_usd`` ceiling bit (guardrails :class:`SpendMeter`).
    BUDGET_EXHAUSTED = "budget_usd"
    #: ``max_iterations`` reached with the other three still quiet.
    ITERATIONS_EXHAUSTED = "iterations_exhausted"
    #: The proposer ran out of candidates before any ceiling bit.
    PROPOSER_EXHAUSTED = "proposer_exhausted"


class CandidateOutcome(str, Enum):
    """One candidate's fate. ``SCOPE_VIOLATION`` is terminal for the candidate and is
    recorded whether or not the score would have won — §8.1's "dead regardless of score"."""

    ADMITTED = "admitted"
    SCOPE_VIOLATION = "scope_violation"
    NO_CHANGE = "no_change"
    BELOW_SUITE_THRESHOLD = "below_suite_threshold"
    NOT_BEST_EVER = "not_best_ever"


class LiveMutationError(RuntimeError):
    """The live artifact changed while the search was running.

    Raised by :meth:`LiveWitness.assert_unchanged`. This is the one failure in this module
    that must never be swallowed: everything else the search can get wrong costs tokens,
    and this one costs the user's artifact.
    """


class OptimizeRefusedError(ValueError):
    """A search was asked for that cannot be run honestly (no budget, sandbox inside the
    frozen region, an empty suite). Refusing before the first model call is the point."""


@dataclass(frozen=True)
class StopConditions:
    """The declared halt envelope. ``budget_usd`` has no default on purpose.

    An unbudgeted search is the failure mode this whole section exists to prevent, so
    ``budget_usd <= 0`` is a refusal (:meth:`validate`) rather than "unlimited" — which is
    what a 0 means everywhere else in :class:`Budget` and exactly why it is checked here.
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

    def budget(self) -> Budget:
        """The guardrails ceiling this envelope maps to (dollars only — the token ceiling
        belongs to whatever runs the model calls, not to the search that counts them)."""
        return Budget(max_dollars=self.budget_usd)

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"roots": list(self.roots), "files": self.files}, indent=2, sort_keys=True),
            encoding="utf-8",
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
    candidate does.
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
    """§8.1's keep/discard rule: BOTH halves, or the candidate is discarded.

    The halves are separate predicates rather than one boolean expression so each can be
    railed on its own — a gate whose two halves are only ever observed together is a gate
    that could be admitting on one of them.
    """

    #: Half A — the harvested regression suite's pass threshold (LEARN-R2's GateOK floor).
    suite_threshold: float
    #: Half B — the monotonic best-ever from ``results.tsv``, frozen at capture time.
    best_ever: BestEver

    def clears_suite_threshold(self, score: float) -> bool:
        """At-or-above the suite floor. Inclusive: the threshold IS the passing mark."""
        return score >= self.suite_threshold

    def beats_best_ever(self, score: float) -> bool:
        """STRICTLY above the best-ever. Ties lose — hill-climbing on equal scores is how
        a search spends a budget wandering a plateau and calls the last step a win."""
        return score > self.best_ever.value

    def decide(self, score: float) -> CandidateOutcome:
        if not self.clears_suite_threshold(score):
            return CandidateOutcome.BELOW_SUITE_THRESHOLD
        if not self.beats_best_ever(score):
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
        """An empty edit. §8.1: these inherit the incumbent score without re-evaluation —
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "outcome": self.outcome,
            "score": self.score,
            "fix_fingerprint": self.fix_fingerprint,
            "best_so_far": self.best_so_far,
            "scope": dict(self.scope),
            "note": self.note,
        }


@dataclass
class SearchOutcome:
    """What a completed search hands back. ``winner`` is ``None`` when nothing was admitted.

    ``needs_from_human`` is populated for exactly the halts §8.2 says deserve one — a search
    that abandoned its hypothesis or ran out of improvement has learned something a person
    should read, whereas one that simply exhausted its iteration count has not.
    """

    halt_reason: HaltReason
    halt_detail: str = ""
    iterations: int = 0
    winner: Candidate | None = None
    winner_score: float = 0.0
    rows: list[LedgerRow] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    needs_from_human: str = ""

    @property
    def admitted(self) -> bool:
        return self.winner is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "halt_reason": self.halt_reason.value,
            "halt_detail": self.halt_detail,
            "iterations": self.iterations,
            "admitted": self.admitted,
            "winner_score": self.winner_score,
            "winner_fingerprint": self.winner.fix_fingerprint if self.winner else "",
            "rows": [r.to_dict() for r in self.rows],
            "gate": dict(self.gate),
            "needs_from_human": self.needs_from_human,
        }


# ── the halt detectors (one call site each in run_search) ─────────────────────


def hypothesis_abandoned(fingerprints: Sequence[str], window: int) -> bool:
    """The same fix attempted ``window`` times in a row.

    Same arithmetic as ``loop.tick``'s ``hypothesis_exhausted``: a full window of identical
    fingerprints. Deliberately not "N attempts total" — a proposer that alternates two fixes
    is exploring, and abandoning it would be abandoning the search, not the hypothesis.
    """
    if window <= 0 or len(fingerprints) < window:
        return False
    return len(set(fingerprints[-window:])) == 1


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


def budget_exhausted(meter: SpendMeter, run_key: str, stops: StopConditions) -> tuple[bool, str]:
    """Whether the ``budget_usd`` ceiling has bitten for this search's run scope."""
    verdict, reason = meter.check_run(run_key, stops.budget())
    return verdict is BudgetVerdict.EXCEEDED, reason


# ── the search ───────────────────────────────────────────────────────────────

#: A proposer is handed the iteration number and the experience index, and returns the next
#: candidate or ``None`` when it has nothing left. It NEVER receives a writable handle to
#: the live artifact — the sandbox path is all it gets.
Proposer = Callable[[int, Path, list[dict[str, Any]]], Candidate | None]

#: A scorer is handed the candidate and its sandbox, and returns (score, checks). It is the
#: only step allowed to spend model calls, which is why it is also the step that charges the
#: meter.
Scorer = Callable[[Candidate, Path], tuple[float, dict[str, Any]]]


def run_search(
    *,
    subject: str,
    live_target: str | os.PathLike[str],
    sandbox: str | os.PathLike[str],
    propose: Proposer,
    score: Scorer,
    suite_threshold: float,
    stops: StopConditions,
    meter: SpendMeter | None = None,
    best_ever: BestEver | None = None,
    witness_extra: Sequence[str | os.PathLike[str]] = (),
) -> SearchOutcome:
    """Run the budgeted hill-climb and return its outcome. Writes NOTHING outside ``sandbox``.

    The ordering is MetaHarness's and is not an implementation detail: scope-check and the
    cheap ``no_change`` validation both run BEFORE the scorer, because the scorer is the only
    expensive step and a candidate that escaped its scope or changed nothing must not be paid
    for. A search that scored first and scope-checked afterwards would have the same verdicts
    and a much larger bill.

    Raises :class:`OptimizeRefusedError` when the envelope is unbudgeted or the sandbox sits
    inside the frozen region (which would make every candidate a violation, or worse, make
    the frozen region writable). Raises :class:`LiveMutationError` if the live artifact moved.
    """
    stops.validate()
    frozen = frozen_roots(live_target)
    sandbox_path = Path(scope_mod.normalize(sandbox))
    if scope_mod.in_scope(str(sandbox_path), frozen):
        raise OptimizeRefusedError(
            f"the search sandbox {sandbox_path} is inside the frozen region "
            f"({', '.join(frozen)}) — candidates would be written over the live artifact"
        )
    sandbox_path.mkdir(parents=True, exist_ok=True)
    allowed = [str(sandbox_path)]
    watch = sorted({*allowed, *frozen})

    meter = meter or SpendMeter()
    run_key = f"{SEARCH_KIND}:{subject}"
    gate = DualGate(
        suite_threshold=suite_threshold,
        best_ever=best_ever if best_ever is not None else capture_best_ever(subject),
    )
    witness = LiveWitness.capture([*frozen, *witness_extra])

    rows: list[LedgerRow] = []
    fingerprints: list[str] = []
    marks: list[float] = []
    best = gate.best_ever.value
    winner: Candidate | None = None
    winner_score = 0.0
    halt = HaltReason.ITERATIONS_EXHAUSTED
    detail = f"{stops.max_iterations} iterations without a halt condition firing"

    iteration = 0
    while iteration < stops.max_iterations:
        # ── halt call site 1: budget_usd ────────────────────────────────────
        spent, reason = budget_exhausted(meter, run_key, stops)
        if spent:
            halt, detail = HaltReason.BUDGET_EXHAUSTED, reason
            break

        iteration += 1
        experience = read_experience(sandbox_path)
        # The snapshot BRACKETS the proposer, not just the candidate write. The proposer is
        # what a real search hands an agent, so it is the step that can escape its scope; a
        # diff taken after it returned would observe only this function's own writes and
        # report every escape as clean.
        before = scope_mod.snapshot(watch)
        candidate = propose(iteration, sandbox_path, experience)
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

        if scope_report.violation:
            # Terminal for the candidate, and unscored: §8.1's "dead regardless of score".
            # It still costs an iteration and still counts as non-improving, because a
            # proposer that keeps escaping its scope must be allowed to exhaust the halts.
            rows.append(
                LedgerRow(
                    iteration=iteration,
                    outcome=CandidateOutcome.SCOPE_VIOLATION.value,
                    score=0.0,
                    fix_fingerprint=candidate.fix_fingerprint,
                    best_so_far=best,
                    scope=scope_report.to_dict(),
                    note="frozen-region touch or write outside allowed_write_paths",
                )
            )
        elif candidate.no_change:
            # Cheap validation, before any LLM spend: an empty candidate inherits the
            # incumbent score rather than being re-evaluated.
            rows.append(
                LedgerRow(
                    iteration=iteration,
                    outcome=CandidateOutcome.NO_CHANGE.value,
                    score=best,
                    fix_fingerprint=candidate.fix_fingerprint,
                    best_so_far=best,
                    scope=scope_report.to_dict(),
                    note="empty candidate — inherited the incumbent score unscored",
                )
            )
        else:
            value, checks = score(candidate, cand_dir)
            gate_outcome = gate.decide(value)
            if gate_outcome is CandidateOutcome.ADMITTED:
                winner, winner_score, best = candidate, value, value
            rows.append(
                LedgerRow(
                    iteration=iteration,
                    outcome=gate_outcome.value,
                    score=value,
                    fix_fingerprint=candidate.fix_fingerprint,
                    best_so_far=best,
                    scope=scope_report.to_dict(),
                    note=json.dumps(checks, sort_keys=True, default=str)[:400],
                )
            )

        fingerprints.append(candidate.fix_fingerprint)
        marks.append(best)
        write_experience(sandbox_path, rows, candidate=candidate, iteration=iteration)

        # ── halt call site 2: hypothesis_abandon_after ──────────────────────
        if hypothesis_abandoned(fingerprints, stops.hypothesis_abandon_after):
            halt = HaltReason.HYPOTHESIS_ABANDONED
            detail = (
                f"the same fix ({candidate.fix_fingerprint}) failed "
                f"{stops.hypothesis_abandon_after}× — the diagnosis is wrong"
            )
            break

        # ── halt call site 3: no_improvement_halt ──────────────────────────
        if no_improvement(marks, stops.no_improvement_halt):
            halt = HaltReason.NO_IMPROVEMENT
            detail = (
                f"{stops.no_improvement_halt} iterations without improving on "
                f"{marks[-stops.no_improvement_halt]}"
            )
            break

    witness.assert_unchanged(context=f"subject={subject}")
    outcome = SearchOutcome(
        halt_reason=halt,
        halt_detail=detail,
        iterations=iteration,
        winner=winner,
        winner_score=winner_score,
        rows=rows,
        gate=gate.to_dict(),
        needs_from_human=_needs_from_human(halt, detail, winner is not None),
    )
    (sandbox_path / "search.json").write_text(
        json.dumps(outcome.to_dict(), indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return outcome


def _needs_from_human(halt: HaltReason, detail: str, admitted: bool) -> str:
    """§8.2's structured ``needs_from_human`` note — for the halts that earned one.

    ``ITERATIONS_EXHAUSTED`` and ``PROPOSER_EXHAUSTED`` do not: the first means the envelope
    was too small and the second means there was nothing to try, and neither is a question
    only a person can answer. Filing one for every halt would make the queue unreadable,
    which is the same as not filing them.
    """
    if halt is HaltReason.HYPOTHESIS_ABANDONED:
        return f"The search kept re-attempting one fix and it kept failing ({detail}). "
    if halt is HaltReason.NO_IMPROVEMENT:
        return f"The search plateaued ({detail}). "
    if halt is HaltReason.BUDGET_EXHAUSTED:
        kept = "a winner was already found" if admitted else "no winner was found"
        return f"The search hit its dollar ceiling before halting on its own — {kept}. "
    return ""


# ── the experience directory (§8.4) ──────────────────────────────────────────


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
    (exp / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
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


# ── the winner becomes a PROPOSAL (§8.3) ─────────────────────────────────────


def propose_winner(outcome: SearchOutcome, *, workflow_name: str) -> dict[str, Any]:
    """File the winning candidate as a template-diff PROPOSAL. Applies NOTHING.

    Routed through ``learning.refiner_tools.file_template_diff`` rather than through a
    second filing path of this module's own: that function already runs the frozen-field +
    legal-op gate and already enqueues into the one human-gated queue, and a search that
    filed its winner some other way would be a second way to install a template.

    A search that admitted nothing files nothing — ``{"filed": False}`` with the halt
    reason, because "the search ran and found no improvement" is a result, not a failure to
    report.
    """
    if outcome.winner is None:
        return {
            "filed": False,
            "rejected": [f"no candidate was admitted (halted: {outcome.halt_reason.value})"],
            "halt_reason": outcome.halt_reason.value,
        }
    from personalclaw.learning import refiner_tools

    result = refiner_tools.file_template_diff(
        workflow_name,
        ops=[dict(op) for op in outcome.winner.ops],
        rationale=(
            f"{outcome.winner.rationale}\n\n"
            f"Found by an optimize-harness search over {outcome.iterations} candidates "
            f"(halted: {outcome.halt_reason.value} — {outcome.halt_detail}). "
            f"Score {outcome.winner_score} cleared BOTH the suite threshold "
            f"{outcome.gate.get('suite_threshold')} and the best-ever "
            f"{outcome.gate.get('best_ever')}."
        ).strip(),
        run_ids=[],
        predicted_fixes=[outcome.winner.fix_fingerprint],
    )
    return {**result, "halt_reason": outcome.halt_reason.value}


# ── the module entry point the bundled template's bash nodes call ─────────────

#: Env key → payload field, for the bundled template's ``bash`` nodes. CLOSED, and the
#: template is asserted against it (``tests/test_evals_optimize.py``): a ``PC_OPT_*`` key the
#: template sets and this map does not name is an input that is silently dropped, which is
#: how a declared ``budget_usd`` becomes no budget at all.
#:
#: Env rather than string-templating the command, for the reason ``bash_provider`` documents
#: at length: a payload VALUE interpolated into a command line is code. Read through
#: ``os.environ`` it is data.
ENV_PAYLOAD_KEYS: dict[str, str] = {
    "PC_OPT_SUBJECT": "subject",
    "PC_OPT_LIVE_TARGET": "live_target",
    "PC_OPT_SANDBOX": "sandbox",
    "PC_OPT_SUITE_THRESHOLD": "suite_threshold",
    "PC_OPT_SCORE": "score",
    "PC_OPT_BEST_EVER": "best_ever",
    "PC_OPT_ROWS_CONSIDERED": "rows_considered",
    "PC_OPT_FIX_FINGERPRINT": "fix_fingerprint",
    "PC_OPT_DIFF_TEXT": "diff_text",
}

#: The same, for the fields that nest under ``stops`` — the three declared halt conditions
#: plus the iteration floor under them.
ENV_STOP_KEYS: dict[str, str] = {
    "PC_OPT_BUDGET_USD": "budget_usd",
    "PC_OPT_ABANDON_AFTER": "hypothesis_abandon_after",
    "PC_OPT_NO_IMPROVEMENT_HALT": "no_improvement_halt",
    "PC_OPT_MAX_ITERATIONS": "max_iterations",
}

#: Comma-separated env values that become lists. Both are windows the halt detectors read,
#: so an empty string must become ``[]`` and not ``[""]`` — a one-element window of the empty
#: string would make ``hypothesis_abandoned`` fire on the first iteration.
ENV_LIST_KEYS: dict[str, str] = {
    "PC_OPT_FIX_FINGERPRINTS": "fix_fingerprints",
    "PC_OPT_MARKS": "marks",
}


def payload_from_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a subcommand payload out of the ``PC_OPT_*`` environment.

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
    for key, field_name in ENV_LIST_KEYS.items():
        raw = str(src.get(key) or "").strip()
        payload[field_name] = [part for part in (p.strip() for p in raw.split(",")) if part]
    return payload


def _cmd_preflight(payload: dict[str, Any]) -> dict[str, Any]:
    """Refuse-or-report BEFORE the first model call: the envelope, the floor, the sandbox.

    Everything expensive about a search is downstream of this, so everything that can make
    the search dishonest is checked here — an unbudgeted envelope, a sandbox inside the
    frozen region, a floor read from a ledger nobody has written to yet.
    """
    stops = StopConditions.from_config(payload.get("stops"))
    stops.validate()
    subject = str(payload.get("subject") or "")
    frozen = frozen_roots(str(payload.get("live_target") or ""))
    sandbox = Path(scope_mod.normalize(str(payload.get("sandbox") or ".")))
    if scope_mod.in_scope(str(sandbox), frozen):
        raise OptimizeRefusedError(f"sandbox {sandbox} is inside the frozen region")
    best = capture_best_ever(subject)
    sandbox.mkdir(parents=True, exist_ok=True)
    witness = LiveWitness.capture(frozen)
    witness.persist(sandbox)
    return {
        "ok": True,
        "subject": subject,
        "stops": stops.to_dict(),
        "frozen_roots": frozen,
        "sandbox": str(sandbox),
        "witnessed_files": len(witness.files),
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


def _cmd_adjudicate(payload: dict[str, Any]) -> dict[str, Any]:
    """One iteration's verdict: frozen-region check, then the dual gate, then the halts.

    Split out from :func:`run_search` so the template's per-iteration bash node consults the
    SAME predicates the in-process driver does. Two implementations of "did this candidate
    win" is the shape this program keeps having to delete.

    The halt windows are read from the sandbox's own ``.experience`` index rather than passed
    in, so the accumulated history is the search's persisted ledger and not a model's memory
    of it — a proposer that forgot to carry its window forward would otherwise disable both
    halts by omission. Explicit ``fix_fingerprints``/``marks`` in the payload still win, which
    is what makes the detectors unit-testable without a sandbox.
    """
    stops = StopConditions.from_config(payload.get("stops"))
    gate = DualGate(
        suite_threshold=_as_float(payload.get("suite_threshold")),
        best_ever=BestEver(
            value=_as_float(payload.get("best_ever")),
            rows_considered=int(payload.get("rows_considered") or 0),
            subject=str(payload.get("subject") or ""),
        ),
    )
    score_value = _as_float(payload.get("score"))
    fingerprint = str(payload.get("fix_fingerprint") or "")
    sandbox = str(payload.get("sandbox") or "")

    # The frozen-region half, re-checked here as well as in `scope-check`: the gate that
    # refuses on it is a template node, and a template node can be deleted. A candidate that
    # touched the frozen region must lose on the score path too, not only on the gate path.
    frozen_touched = _frozen_touched(sandbox) if sandbox else []

    prior = read_experience(sandbox) if sandbox else []
    if frozen_touched:
        outcome = CandidateOutcome.SCOPE_VIOLATION
    else:
        outcome = gate.decide(score_value)
    best_so_far = max(
        [gate.best_ever.value, *(_as_float(r.get("best_so_far")) for r in prior)]
        + ([score_value] if outcome is CandidateOutcome.ADMITTED else [])
    )

    fingerprints = [str(f) for f in (payload.get("fix_fingerprints") or [])] or [
        *(str(r.get("fix_fingerprint") or "") for r in prior),
        fingerprint,
    ]
    marks = [_as_float(m) for m in (payload.get("marks") or [])] or [
        *(_as_float(r.get("best_so_far")) for r in prior),
        best_so_far,
    ]

    halt = ""
    if hypothesis_abandoned(fingerprints, stops.hypothesis_abandon_after):
        halt = HaltReason.HYPOTHESIS_ABANDONED.value
    elif no_improvement(marks, stops.no_improvement_halt):
        halt = HaltReason.NO_IMPROVEMENT.value

    if sandbox:
        iteration = len(prior) + 1
        write_experience(
            Path(sandbox),
            [
                *(
                    LedgerRow(
                        iteration=int(r.get("iteration") or 0),
                        outcome=str(r.get("outcome") or ""),
                        score=_as_float(r.get("score")),
                        fix_fingerprint=str(r.get("fix_fingerprint") or ""),
                        best_so_far=_as_float(r.get("best_so_far")),
                    )
                    for r in prior
                ),
                LedgerRow(
                    iteration=iteration,
                    outcome=outcome.value,
                    score=score_value,
                    fix_fingerprint=fingerprint,
                    best_so_far=best_so_far,
                    scope={"frozen_touched": frozen_touched},
                ),
            ],
            candidate=Candidate(
                iteration=iteration,
                fix_fingerprint=fingerprint,
                diff_text=str(payload.get("diff_text") or ""),
            ),
            iteration=iteration,
        )

    return {
        "ok": True,
        "outcome": outcome.value,
        "admitted": outcome is CandidateOutcome.ADMITTED,
        "clears_suite_threshold": gate.clears_suite_threshold(score_value),
        "beats_best_ever": gate.beats_best_ever(score_value),
        "frozen_touched": frozen_touched,
        "best_so_far": best_so_far,
        "halt": halt,
        "continue": not halt,
        "gate": gate.to_dict(),
    }


#: The subcommand table. The bundled ``optimize-harness`` template names these in its bash
#: nodes, so ``tests/test_evals_optimize.py`` asserts the template's names against THIS
#: dict — a renamed subcommand fails the template, not just this module.
COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "preflight": _cmd_preflight,
    "scope-check": _cmd_scope_check,
    "adjudicate": _cmd_adjudicate,
}


def main(argv: Sequence[str] | None = None, stdin: Any = None) -> int:
    """``python -m personalclaw.evals.optimize <subcommand>``.

    The payload comes from stdin as JSON, or — when stdin is empty, which is how the bundled
    template calls it — from the ``PC_OPT_*`` environment (:func:`payload_from_env`).

    Errors come back as JSON on stdout with a non-zero exit, not as a traceback: the caller
    is a bash action whose output the engine parses, and a traceback there is an opaque
    failed node rather than a reason.
    """
    args = list(sys.argv[1:] if argv is None else argv)
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
    except (OptimizeRefusedError, LiveMutationError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
