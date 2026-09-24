"""Predict-then-verify: did an accepted change actually help? (LEARN-R16 / §3.1 — S77).

The last spoke, and the one that closes the loop. Everything upstream files proposals; this measures
what happened AFTER a human accepted one — and files a revert when the answer is "it made things
worse".

§3.1's rule is predict-then-verify, which is stronger than measure-after: an accepted proposal
DECLARED which failures it would fix (`predicted_fixes` on the change manifest), so the verdict can
compare prediction against outcome rather than just looking at a delta and inventing a story.

That comparison produces the class §3.1 calls the scariest: **`unattributed_regressions`** — things
that broke which nobody predicted. A change scored only on its own predictions looks fine while
having broken something adjacent, and that is exactly the failure a five-way verdict exists to
catch.

**Measured before writing.** `refiner.canary_verdict` (S73) already returns the five verdict names
from a scalar before/after, so this module does NOT re-derive them — it reuses that function and
adds
the per-cluster attribution it cannot see from one number. And `learning/gate.py`'s permission gate
is
genuinely closed: probed across all three cadences, a restricted (incognito) session is refused
whatever the tool count or correction signal. What is NOT closed is coverage — `Cadence.SESSION_END`
and `Cadence.RUN_END` are declared and have ZERO live callers, so the gate cannot suppress a path
nobody routes through it. `assert_gate_covers_cadences` makes that a checkable fact rather than a
comment.

Pure functions. Filing the revert is the caller's; this decides that one is owed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """§3.1's five-way attribution verdict, plus the honest not-yet state.

    Reuses `refiner.canary_verdict`'s vocabulary rather than defining a parallel one — the refiner
    computes the scalar case and this module the per-cluster case, and two verdict scales would make
    a proposal's history unreadable when it passed through both.
    """

    EFFECTIVE = "EFFECTIVE"
    PARTIALLY_EFFECTIVE = "PARTIALLY_EFFECTIVE"
    INEFFECTIVE = "INEFFECTIVE"
    MIXED = "MIXED"
    HARMFUL = "HARMFUL"
    #: Not enough post-acceptance runs yet. A distinct state, never a guess: declaring a change
    #: effective after one run is how a lucky run becomes a permanent change.
    PENDING = "PENDING"


VERDICTS: tuple[str, ...] = tuple(v.value for v in Verdict)

#: Verdicts that auto-FILE a revert proposal (§3.1). Only `HARMFUL`, deliberately: an INEFFECTIVE
#: change is clutter, not damage, and auto-filing a revert for every change that did not help would
#: bury the queue in noise — which is how the one revert that mattered gets skipped.
REVERT_VERDICTS: frozenset[str] = frozenset({Verdict.HARMFUL.value})

#: Post-acceptance runs before a verdict is anything but PENDING. Matches the refiner's floor so the
#: two agree about when evidence is sufficient.
MIN_RUNS = 3

#: How much a cluster must move to count as fixed or regressed. Below this it is noise, and treating
#: noise as a regression would file reverts against changes that did nothing at all.
DELTA_EPS = 0.02


@dataclass
class Outcome:
    """Per-cluster failure rates before and after a change landed.

    Rates rather than counts, because run volume changes: five failures out of ten runs is worse
    than
    five out of five hundred, and a count-based comparison would call a busier week a regression.
    """

    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)
    runs_after: int = 0

    def delta(self, cluster: str) -> float:
        """Improvement for one cluster: positive means the failure rate FELL."""
        return float(self.before.get(cluster, 0.0)) - float(self.after.get(cluster, 0.0))

    @property
    def fixed(self) -> list[str]:
        """Clusters that measurably improved."""
        return sorted(c for c in self.before if self.delta(c) >= DELTA_EPS)

    @property
    def regressed(self) -> list[str]:
        """Clusters that measurably worsened, INCLUDING ones absent from `before`.

        A cluster that only appears in `after` is a NEW failure mode the change introduced — the
        most
        important kind of regression and the one a `before`-keyed loop would miss entirely.
        """
        out = {c for c in self.before if self.delta(c) <= -DELTA_EPS}
        for cluster, rate in self.after.items():
            if cluster not in self.before and float(rate) >= DELTA_EPS:
                out.add(cluster)
        return sorted(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runs_after": self.runs_after,
            "fixed": self.fixed,
            "regressed": self.regressed,
        }


@dataclass
class Attribution:
    """The full predict-then-verify result for one accepted change."""

    verdict: str
    predicted: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    regressed: list[str] = field(default_factory=list)
    #: Predicted fixes that did NOT materialize. The proposer over-promised, which is a calibration
    #: signal about that proposer rather than a fault in this change alone.
    unfulfilled: list[str] = field(default_factory=list)
    #: Regressions nobody predicted. §3.1: "the scariest class, surfaced loudly."
    unattributed_regressions: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def owes_revert(self) -> bool:
        return self.verdict in REVERT_VERDICTS

    @property
    def precision(self) -> float:
        """Share of predicted fixes that materialized, in [0, 1].

        The per-proposal input to §3.1's proposer trust signal — "the flywheel learns which of its
        own
        proposers to believe". A change that predicted three fixes and delivered one is a different
        thing from one that predicted one and delivered it.
        """
        if not self.predicted:
            return 0.0
        landed = len(set(self.predicted) & set(self.fixed))
        return landed / len(self.predicted)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "predicted": list(self.predicted),
            "fixed": list(self.fixed),
            "regressed": list(self.regressed),
            "unfulfilled": list(self.unfulfilled),
            "unattributed_regressions": list(self.unattributed_regressions),
            "precision": round(self.precision, 4),
            "owes_revert": self.owes_revert,
            "reason": self.reason,
        }


def attribute(
    *,
    predicted_fixes: list[str] | None,
    outcome: Outcome,
    min_runs: int = MIN_RUNS,
) -> Attribution:
    """Score one accepted change against what it PREDICTED. Pure.

    The verdict ladder, and why each rung sits where it does:

    * **PENDING** under `min_runs`. Not a guess — one post-acceptance run is an anecdote, and a
    change
      declared effective on one run is a lucky run made permanent.
    * **HARMFUL** when anything regressed and nothing predicted was fixed. Damage with no upside is
    the
      unambiguous case, and it is the only one that auto-files a revert.
    * **MIXED** when regressions coexist with real fixes. Deliberately NOT harmful: the change did
      something the user wanted, so the decision is theirs rather than an automatic rollback.
    * **INEFFECTIVE** when nothing moved either way. Clutter, not damage.
    * **PARTIALLY_EFFECTIVE** when some predictions landed and some did not.
    * **EFFECTIVE** only when every prediction landed and nothing regressed.

    A change with NO predictions is scoreable but never `EFFECTIVE`: without a prediction there is
    nothing to have been right about, so the best it earns is `PARTIALLY_EFFECTIVE` on an
    unpredicted
    improvement. Letting it reach `EFFECTIVE` would reward filing manifests with empty
    `predicted_fixes`, which is exactly the shortcut §3.1's lenient validation makes tempting.
    """
    predicted = sorted({str(p) for p in (predicted_fixes or []) if str(p).strip()})
    fixed = outcome.fixed
    regressed = outcome.regressed
    unfulfilled = sorted(set(predicted) - set(fixed))
    unattributed = sorted(set(regressed) - set(predicted))

    def _build(verdict: str, reason: str) -> Attribution:
        return Attribution(
            verdict=verdict,
            predicted=predicted,
            fixed=fixed,
            regressed=regressed,
            unfulfilled=unfulfilled,
            unattributed_regressions=unattributed,
            reason=reason,
        )

    if outcome.runs_after < max(1, min_runs):
        return _build(
            Verdict.PENDING.value,
            f"only {outcome.runs_after} run(s) since acceptance; {min_runs} are needed before a "
            "verdict is evidence rather than an anecdote",
        )

    landed = sorted(set(predicted) & set(fixed))

    if regressed and not landed:
        return _build(
            Verdict.HARMFUL.value,
            f"{len(regressed)} cluster(s) regressed and none of the predicted fixes landed"
            + (f"; {len(unattributed)} regression(s) nobody predicted" if unattributed else ""),
        )
    if regressed:
        return _build(
            Verdict.MIXED.value,
            f"{len(landed)} predicted fix(es) landed but {len(regressed)} regressed — the "
            "change did something wanted, so reverting is the user's call, not automatic",
        )
    if not fixed:
        return _build(
            Verdict.INEFFECTIVE.value,
            "nothing measurably moved in either direction; clutter rather than damage",
        )
    if predicted and not unfulfilled:
        return _build(
            Verdict.EFFECTIVE.value,
            f"every predicted fix landed ({len(landed)}) and nothing regressed",
        )
    return _build(
        Verdict.PARTIALLY_EFFECTIVE.value,
        (
            f"{len(landed)} of {len(predicted)} predicted fixes landed"
            if predicted
            else f"{len(fixed)} cluster(s) improved, none of them predicted"
        )
        + " and nothing regressed",
    )


@dataclass
class RevertProposal:
    """A revert offered through the QUEUE, never applied.

    §3.1: HARMFUL verdicts "auto-generate revert proposals through the queue, making version-pin
    rollback mechanical instead of requiring user vigilance". Mechanical means the proposal appears
    without anyone noticing the regression — it does not mean the revert happens on its own, and
    S75's
    gate refuses a non-human accept regardless.
    """

    target: str
    kind: str = "retirement"
    title: str = ""
    body: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    provenance: str = "accountability"

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "evidence_refs": list(self.evidence_refs),
            "provenance": self.provenance,
        }


def revert_proposal(
    *, target: str, attribution: Attribution, run_ids: list[str] | None = None
) -> RevertProposal | None:
    """The revert a HARMFUL verdict owes, or None.

    Returns None for every other verdict so a caller cannot file one by ignoring the verdict — the
    same path-not-postcondition discipline S73 used for `build_proposal`.

    The body NAMES the regressed clusters, including the unattributed ones. A revert proposal that
    said only "this made things worse" would be un-reviewable: the user cannot weigh a rollback
    without knowing what broke.
    """
    if not attribution.owes_revert:
        return None
    lines = [
        f"Accepted change to {target} scored {attribution.verdict}.",
        f"Regressed: {', '.join(attribution.regressed) or 'none recorded'}.",
    ]
    if attribution.unattributed_regressions:
        lines.append(
            "Nobody predicted: "
            + ", ".join(attribution.unattributed_regressions)
            + " — these are the regressions no proposer anticipated."
        )
    if attribution.unfulfilled:
        lines.append(f"Predicted but never landed: {', '.join(attribution.unfulfilled)}.")
    return RevertProposal(
        target=target,
        title=f"Revert {target} — scored {attribution.verdict}",
        body=" ".join(lines),
        evidence_refs=sorted({str(r) for r in (run_ids or [])})[:20],
    )


# ── proposer trust (§3.1: "which of its own proposers to believe") ──


@dataclass
class ProposerTrust:
    """Verdict history for one proposal SOURCE (refiner / detector / user / self-model)."""

    source: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def decided(self) -> int:
        """Verdicts that are actually evidence — PENDING is not."""
        return self.total - self.counts.get(Verdict.PENDING.value, 0)

    @property
    def harm_rate(self) -> float:
        """Share of DECIDED verdicts that were harmful.

        Over decided rather than total, because a proposer with many pending changes would otherwise
        look safer than one whose changes have been measured — which inverts the signal exactly when
        a new proposer starts filing.
        """
        if self.decided <= 0:
            return 0.0
        return self.counts.get(Verdict.HARMFUL.value, 0) / self.decided

    @property
    def effective_rate(self) -> float:
        if self.decided <= 0:
            return 0.0
        wins = self.counts.get(Verdict.EFFECTIVE.value, 0) + self.counts.get(
            Verdict.PARTIALLY_EFFECTIVE.value, 0
        )
        return wins / self.decided

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "counts": dict(sorted(self.counts.items())),
            "total": self.total,
            "decided": self.decided,
            "harm_rate": round(self.harm_rate, 4),
            "effective_rate": round(self.effective_rate, 4),
        }


def proposer_trust(records: list[tuple[str, str]]) -> list[ProposerTrust]:
    """Aggregate `(source, verdict)` history per proposer, worst harm-rate first.

    Worst-first because this feeds calibration: the useful question is "which proposer should I
    trust
    less", and a list sorted by name buries the answer. Unknown verdicts are counted under their own
    name rather than dropped — a verdict this module does not recognize is a drift signal, and
    silently discarding it would hide the drift.
    """
    trust: dict[str, ProposerTrust] = {}
    for source, verdict in records or []:
        entry = trust.setdefault(str(source), ProposerTrust(source=str(source)))
        entry.counts[str(verdict)] = entry.counts.get(str(verdict), 0) + 1
    return sorted(trust.values(), key=lambda t: (-t.harm_rate, -t.total, t.source))


# ── the incognito capture gate, made checkable ──


def assert_gate_covers_cadences() -> list[str]:
    """Cadences declared by `learning.gate` that NO live call site routes through. Returns the gaps.

    §7 asks for the incognito capture gate "closed + regression-tested". Probed across all three
    cadences, the permission half genuinely IS closed: a restricted session is refused whatever its
    tool count or correction signal. The gap is COVERAGE — a gate cannot suppress a path nobody
    routes
    through it, and `SESSION_END`/`RUN_END` were declared with zero callers when this was written.

    So the check is a source scan rather than a behavioural assertion: the failure mode is a cadence
    (or a new proposer) that never asks the gate, which testing the gate itself cannot catch.
    A test asserts the CURRENT gap set, so wiring one — or adding a fourth cadence — has to be a
    deliberate edit to that list rather than a silent hole.

    THIS FILE is excluded from the scan, and so is `gate.py`. Measured while writing it: the first
    version matched its own docstring's `Cadence.SESSION_END` mention and reported ZERO gaps for two
    cadences that genuinely had no callers — a checker that certifies coverage by finding itself.
    The
    same self-referential trap S67's fire-site scan fell into, one module over.
    """
    import re
    from pathlib import Path

    from personalclaw.learning.gate import Cadence

    here = Path(__file__).resolve()
    root = here.parent.parent
    used: set[str] = set()
    for path in root.rglob("*.py"):
        if path.resolve() == here:
            continue
        if path.name == "gate.py" and path.parent.name == "learning":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"Cadence\.([A-Z_]+)", text):
            used.add(match.group(1))
    return sorted(c.name for c in Cadence if c.name not in used)
