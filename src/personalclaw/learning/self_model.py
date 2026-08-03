"""The capped self-model — reinforcement-promoted, propose-don't-write (LEARN-R21 / §2.6 — S72).

The flywheel's ONLY mechanism that learns from what quietly WORKS. Every other cadence learns from
corrections and failures, which means the system can only ever discover what it did wrong; this one
notices a habit that keeps succeeding and offers to make it a principle.

That asymmetry is also what makes it dangerous, so §2.6 constrains it three ways and this module
enforces all three mechanically rather than by convention:

1. **Propose, never install.** A crossed threshold produces a PROPOSAL in the unified queue, exactly
   like a lesson. Nothing here writes a principle into memory. A system that promotes its own
   behavioural rules is a system whose behaviour the user cannot predict, and the plan says "never
   self-installed" for that reason.
2. **Bounded by construction.** ~6 active principles, ~4 working theories, ~4 current-focus entries,
   a small retrospection ring. Promotion into a FULL cap requires DISPLACING an existing entry, so
   bloat is impossible at the schema level rather than policed by a later cleanup that may not run.
3. **Only a compact snapshot injects.** One budgeted slot in §2.4's allocator, never the history.

**Measured before writing.** `user.selfmodel.*` was NOT in `_NON_FACT_KEY_CLAUSE`, so a principle
the harness observed about its own working patterns would have rendered as a FACT ABOUT THE USER — a
category error and a leak. The exclusion landed with this session. `user.*` was already in
`_BUILTIN_PREFIXES`, so no allowlist change was needed; measuring both saved inventing one.

Pure decisions over records. The observer's writes go through `MemoryService`, and the proposals go
through `learning.proposals` — this module builds neither store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: The memory key prefix for every self-model artifact. Under `user.*` (already allowlisted) and
#: adjacent to the existing `user.persona.*` seam, because a self-model entry is the same KIND of
#: thing: harness-internal, agent-facing, and never a user fact.
KEY_PREFIX = "user.selfmodel"

#: Reinforcement thresholds (§2.6). BOTH are required: `seen_count` alone promotes a coincidence
#: happened twice, and confidence alone promotes one strongly-felt observation. The conjunction is
#: what makes "repeated AND reliable" the bar.
MIN_SEEN_COUNT = 2
MIN_CONFIDENCE = 0.72

#: Hard caps, per §2.6. Bloat is prevented STRUCTURALLY: `plan_promotion` refuses to grow a full
#: tier and returns a displacement instead, so no path quietly appends a seventh principle.
CAPS: dict[str, int] = {
    "principle": 6,
    "theory": 4,
    "focus": 4,
    "retrospection": 8,
}


class Facet(str, Enum):
    """What kind of self-knowledge an entry holds.

    Four kinds rather than one bag, because they have different lifetimes and different authority. A
    principle is always-on and constraint-like; a theory is explicitly provisional; a focus is
    short-lived; a retrospection is history. Collapsing them would let a guess inject with the
    weight of a rule.
    """

    #: A behavioural principle — lessons-shaped, always-on, earned by reinforcement.
    PRINCIPLE = "principle"
    #: A working theory: believed, unproven, and labelled as such wherever it renders.
    THEORY = "theory"
    #: What the user is currently working on. Expires by nature.
    FOCUS = "focus"
    #: A ring buffer of past observations, kept for evidence rather than injection.
    RETROSPECTION = "retrospection"


FACETS: tuple[str, ...] = tuple(f.value for f in Facet)


class Reaction(str, Enum):
    """The user's observed response to a turn — the reinforcement signal.

    Mechanically observed, never asked for. §2.5's rule applies here too: a voluntary "was that
    good?" is ornamental, so the signal has to be something the user DID.
    """

    #: The user built on the result — accepted a diff, ran the thing, moved on to the next step.
    ACCEPTED = "accepted"
    #: The user corrected or redid it. The strongest negative available.
    CORRECTED = "corrected"
    #: The user abandoned the thread. Weak evidence: silence is ambiguous.
    ABANDONED = "abandoned"
    #: Nothing observable. Contributes NO evidence in either direction.
    NEUTRAL = "neutral"


#: How each reaction moves confidence. `CORRECTED` outweighs `ACCEPTED` deliberately: being told you
#: were wrong is stronger evidence than not being told you were wrong, and a symmetric scale lets a
#: habit that fails a third of the time still promote.
REACTION_WEIGHT: dict[str, float] = {
    Reaction.ACCEPTED.value: 1.0,
    Reaction.CORRECTED.value: -2.0,
    Reaction.ABANDONED.value: -0.5,
    Reaction.NEUTRAL.value: 0.0,
}


@dataclass
class Observation:
    """One recorded turn: what the harness did, and what happened next.

    The tuple §2.6 names — route, tools, outcome, reaction. Deliberately NOT the turn's content: the
    self-model is about working patterns, and storing prompts would make it a transcript with a cap.
    """

    pattern: str
    route: str = ""
    tools: tuple[str, ...] = ()
    succeeded: bool = True
    reaction: str = Reaction.NEUTRAL.value
    at: str = ""

    @property
    def evidence(self) -> float:
        """This observation's contribution to a pattern's confidence.

        A FAILED turn contributes nothing positive even when the user accepted the result — they may
        have accepted a partial answer and moved on. Reading acceptance-after-failure as
        reinforcement is how a broken habit gets promoted.
        """
        weight = REACTION_WEIGHT.get(self.reaction, 0.0)
        if not self.succeeded:
            return min(0.0, weight)
        return weight

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "route": self.route,
            "tools": list(self.tools),
            "succeeded": self.succeeded,
            "reaction": self.reaction,
            "at": self.at,
        }


@dataclass
class Reinforcement:
    """The accumulated evidence for one candidate pattern."""

    pattern: str
    seen_count: int = 0
    score: float = 0.0
    observations: list[Observation] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """Positive evidence as a share of total evidence, in [0, 1].

        Not a raw average: a pattern seen twice with both accepted would score 1.0 on an average and
        promote instantly. Dividing by the count of NON-NEUTRAL observations while the threshold
        also requires `seen_count` is what makes repetition and reliability both necessary.
        """
        graded = [o for o in self.observations if o.reaction != Reaction.NEUTRAL.value]
        if not graded:
            return 0.0
        positive = sum(max(0.0, o.evidence) for o in graded)
        negative = sum(abs(min(0.0, o.evidence)) for o in graded)
        total = positive + negative
        return (positive / total) if total > 0 else 0.0

    @property
    def promotable(self) -> bool:
        """Both §2.6 thresholds, as a conjunction.

        `seen_count` alone promotes a coincidence that happened twice; confidence alone promotes one
        strongly-felt observation. Neither is evidence of a habit on its own.
        """
        return self.seen_count >= MIN_SEEN_COUNT and self.confidence >= MIN_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "seen_count": self.seen_count,
            "confidence": round(self.confidence, 4),
            "promotable": self.promotable,
            "observations": len(self.observations),
        }


def reinforce(existing: Reinforcement | None, observation: Observation) -> Reinforcement:
    """Fold one observation into a pattern's evidence. Returns a NEW record.

    A NEUTRAL observation still increments `seen_count` — the pattern DID recur, and pretending it
    did not would let an unobservable outcome erase the repetition half of the threshold. It
    contributes nothing to confidence, which is the honest split.
    """
    record = Reinforcement(
        pattern=observation.pattern,
        seen_count=(existing.seen_count if existing else 0) + 1,
        score=(existing.score if existing else 0.0) + observation.evidence,
        observations=list(existing.observations) if existing else [],
    )
    record.observations.append(observation)
    return record


@dataclass
class Entry:
    """One live self-model entry.

    `evidence` carries the reinforcement provenance §2.6 requires: an accepted principle must be
    able to show WHY it exists, because "the system decided this about itself" is not an auditable
    explanation.
    """

    facet: str
    key: str
    body: str
    seen_count: int = 0
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    created_at: str = ""
    last_seen_at: str = ""

    @property
    def memory_key(self) -> str:
        return f"{KEY_PREFIX}.{self.facet}.{self.key}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "facet": self.facet,
            "key": self.key,
            "body": self.body,
            "seen_count": self.seen_count,
            "confidence": round(self.confidence, 4),
            "evidence": list(self.evidence),
            "memory_key": self.memory_key,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
        }


@dataclass
class PromotionPlan:
    """What promoting a pattern would do — including who gets displaced.

    A PLAN rather than an action, for the same reason the proposal is a proposal: displacing an
    existing principle is a real loss, and the user should see it named before it happens.
    """

    facet: str
    pattern: str
    allowed: bool
    reason: str = ""
    displaces: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "facet": self.facet,
            "pattern": self.pattern,
            "allowed": self.allowed,
            "reason": self.reason,
            "displaces": self.displaces,
        }


def plan_promotion(
    *,
    facet: str,
    reinforcement: Reinforcement,
    current: list[Entry],
) -> PromotionPlan:
    """Whether a pattern may become an entry, and what it would displace. Pure.

    The cap is enforced HERE, before anything is written, and a full tier does not simply refuse —
    it names the weakest entry as a displacement candidate. Refusing outright would freeze the
    self-model at its first six principles, so the cap would prevent bloat by preventing learning.

    "Weakest" is the lowest confidence, then the lowest `seen_count`, then the oldest. A new pattern
    must BEAT it to displace: an equal-confidence newcomer does not evict an entry that has already
    proven itself, which is what stops the tier churning between two similar principles.
    """
    if facet not in FACETS:
        return PromotionPlan(
            facet=facet,
            pattern=reinforcement.pattern,
            allowed=False,
            reason=f"unknown facet {facet!r}; expected one of {', '.join(FACETS)}",
        )
    if not reinforcement.promotable:
        return PromotionPlan(
            facet=facet,
            pattern=reinforcement.pattern,
            allowed=False,
            reason=(
                f"seen {reinforcement.seen_count}× at {reinforcement.confidence:.0%} confidence; "
                f"needs {MIN_SEEN_COUNT}× and {MIN_CONFIDENCE:.0%}"
            ),
        )

    same_facet = [e for e in current if e.facet == facet]
    cap = CAPS.get(facet, 0)
    if len(same_facet) < cap:
        return PromotionPlan(facet=facet, pattern=reinforcement.pattern, allowed=True)

    weakest = min(same_facet, key=lambda e: (e.confidence, e.seen_count, e.created_at))
    if reinforcement.confidence <= weakest.confidence:
        return PromotionPlan(
            facet=facet,
            pattern=reinforcement.pattern,
            allowed=False,
            reason=(
                f"the {facet} cap of {cap} is full and the weakest entry ({weakest.key}) is at "
                f"{weakest.confidence:.0%} — a newcomer must BEAT it, not tie it"
            ),
        )
    return PromotionPlan(
        facet=facet,
        pattern=reinforcement.pattern,
        allowed=True,
        reason=f"the {facet} cap of {cap} is full; this would displace {weakest.key}",
        displaces=weakest.key,
    )


def over_cap(entries: list[Entry]) -> dict[str, int]:
    """Facets currently over their cap, and by how much. `{}` when everything is within bounds.

    A guard for data that predates the caps or arrived by hand-editing memory.db. The caps are
    structural for anything going through `plan_promotion`, but a file on disk can say anything,
    and a self-model quietly holding twelve principles would blow the injection budget it must fit.
    """
    counts: dict[str, int] = {}
    for entry in entries or []:
        counts[entry.facet] = counts.get(entry.facet, 0) + 1
    return {
        facet: count - CAPS[facet]
        for facet, count in counts.items()
        if facet in CAPS and count > CAPS[facet]
    }


def trim_ring(entries: list[Entry], facet: str = Facet.RETROSPECTION.value) -> list[Entry]:
    """The retrospection ring, trimmed to its cap, newest kept.

    A ring rather than a growing log: retrospections are evidence, and evidence older than the cap
    has already either produced a principle or failed to. Keeping it forever would make the
    self-model a transcript.
    """
    ring = [e for e in entries or [] if e.facet == facet]
    others = [e for e in entries or [] if e.facet != facet]
    cap = CAPS.get(facet, 0)
    ring.sort(key=lambda e: e.last_seen_at or e.created_at, reverse=True)
    return others + ring[:cap]


# ── proposing (never installing) ──

#: The proposal kind a promoted principle files under. `lesson_batch` rather than a new kind: §2.6
#: an accepted principle is "lessons-shaped (constraint-like, always-on)", and the existing kind
#: already carries the review UI, the fingerprint dedup, and the decision store. A new kind would
#: mean a second review surface for the same shape of thing.
PROPOSAL_KIND = "lesson_batch"

#: Provenance marker on the proposal body, so a reviewer can tell an OBSERVED principle from a
#: correction-derived lesson at a glance. They are shaped alike and earned very differently: one is
#: what the user told the system, the other is what the system noticed about itself.
PROVENANCE = "observed-reinforcement"


@dataclass
class PrincipleProposal:
    """A behavioural principle offered for review. Never applied by this module."""

    facet: str
    pattern: str
    body: str
    seen_count: int
    confidence: float
    evidence: list[str] = field(default_factory=list)
    displaces: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": PROPOSAL_KIND,
            "provenance": PROVENANCE,
            "facet": self.facet,
            "pattern": self.pattern,
            "body": self.body,
            "seen_count": self.seen_count,
            "confidence": round(self.confidence, 4),
            "evidence": list(self.evidence),
            "displaces": self.displaces,
        }


def build_proposal(
    *,
    facet: str,
    reinforcement: Reinforcement,
    plan: PromotionPlan,
    body: str = "",
) -> PrincipleProposal | None:
    """The proposal for a promotable pattern, or None when the plan refused.

    Returns None rather than an un-promotable proposal so a caller cannot file one by ignoring the
    plan — the cap and the thresholds are enforced on the path to the queue, not after it.

    The evidence lines are the reinforcement provenance §2.6 requires. Bounded to the most recent
    few: a proposal a reviewer will not read is not evidence, it is volume.
    """
    if not plan.allowed:
        return None
    recent = [
        f"{o.reaction} after {'success' if o.succeeded else 'failure'}"
        + (f" via {o.route}" if o.route else "")
        for o in reinforcement.observations[-3:]
    ]
    return PrincipleProposal(
        facet=facet,
        pattern=reinforcement.pattern,
        body=body or reinforcement.pattern,
        seen_count=reinforcement.seen_count,
        confidence=reinforcement.confidence,
        evidence=recent,
        displaces=plan.displaces,
    )


def proposal_fingerprint(proposal: PrincipleProposal) -> str:
    """The dedup fingerprint, via the shared `proposals.content_fingerprint`.

    Reuses the existing function rather than hashing here, so a re-proposed principle collides with
    its own prior ACCEPTED/REJECTED decision in the shared store. A second hashing scheme would
    make the self-model the one proposer that can re-file something the user already declined.
    """
    from personalclaw.learning.proposals import content_fingerprint

    return content_fingerprint(PROPOSAL_KIND, f"{KEY_PREFIX}.{proposal.facet}", proposal.body)


# ── the compact snapshot (§2.6's one budgeted slot) ──

#: Hard ceiling on the snapshot. One budgeted slot in §2.4's allocator means a bounded string, and a
#: self-model that grew its own injection would be the bloat the caps exist to prevent, arriving by
#: a different door.
SNAPSHOT_MAX_CHARS = 700

#: Facets that inject, in render order. Retrospections are EXCLUDED: they are evidence for
#: promotion, not guidance for a turn, and injecting history spends the slot on the least
#: actionable content.
SNAPSHOT_FACETS: tuple[str, ...] = (
    Facet.PRINCIPLE.value,
    Facet.FOCUS.value,
    Facet.THEORY.value,
)


def snapshot(entries: list[Entry], *, limit: int = SNAPSHOT_MAX_CHARS) -> str:
    """The compact self-model block for a planning/recovery prompt. Never the full history.

    Theories are rendered under an explicit "unproven" heading. A working theory that reads like a
    principle is worse than no theory at all: the model would treat a guess as a constraint, which
    is exactly the authority confusion the four separate facets exist to prevent.

    Truncation drops whole ENTRIES, never mid-line. Half a principle is an instruction whose reader
    cannot tell it is half — the same rule `learning/surfacing.py` applies to lessons.
    """
    if not entries:
        return ""
    by_facet: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.facet in SNAPSHOT_FACETS:
            by_facet.setdefault(entry.facet, []).append(entry)

    lines: list[str] = []
    for facet in SNAPSHOT_FACETS:
        found = sorted(by_facet.get(facet, []), key=lambda e: -e.confidence)
        if not found:
            continue
        heading = {
            Facet.PRINCIPLE.value: "How I work with you:",
            Facet.FOCUS.value: "Currently working on:",
            Facet.THEORY.value: "Unproven working theories:",
        }[facet]
        block = [heading]
        for entry in found:
            block.append(f"- {entry.body}")
        lines.extend(block)

    out: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + 1
        if used + cost > limit:
            break
        out.append(line)
        used += cost
    # A dangling heading with no entries under it reads as a bug. Drop it rather than render it.
    while out and out[-1].endswith(":"):
        out.pop()
    return "\n".join(out)
