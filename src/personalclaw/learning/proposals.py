"""One proposal queue for every kind of autonomous synthesis.

**The invariant this module exists to enforce: autonomous synthesis PROPOSES; the
human installs.** It is the flywheel's trust anchor — the system may notice
anything, and change nothing on its own.

Generalized from `skills/proposals.py`, which held exactly this shape for one kind
(synthesized skills). Six kinds now share it, because they share the property that
matters: each is a durable change to how the system will behave, inferred from
evidence rather than instructed.

## Why a queue rather than direct writes

The per-kind write policy is deliberate, not uniform:

- **Direct write** for facets, voice aspects, procedural priors, episodic/semantic
  memory. Reversible, decaying, low blast radius — a queue would cost more attention
  than the change is worth.
- **Direct write** for a lesson from an *explicit user correction*: the user just
  said it, so asking them to confirm it is noise.
- **Proposal** for a lesson *inferred* from consolidation or a run failure. This is a
  change from prior behaviour, where consolidation lessons wrote live — and it
  closes a real hole: a prompt injection that reached a transcript could otherwise
  become a standing instruction with no human in the loop.
- **Proposal, always** for skills, templates, and template diffs. These execute.

## Decision memory — the anti-nag machinery

A proposer that re-files a rejected suggestion trains the user to ignore the queue,
which destroys the queue's value faster than a wrong proposal does. So every
proposal carries a content fingerprint, and every proposer consults the decision
store *before* filing:

- a fingerprint matching a prior ACCEPTED or REJECTED decision is silently skipped;
- an exact duplicate of something PENDING **reinforces** it (a counter and a fresh
  timestamp) rather than inserting a second row;
- a rejected proposal is kept as a negative exemplar, with escalating cooldowns,
  because "no" is information worth keeping.

The resolve cascade (`resolve`) is the deterministic pre-LLM triage that decides
which of those four things is happening. It runs contradiction detection BEFORE the
reinforce shortcut, because near-identical text with opposite polarity ("always use
X" vs "never use X") must REPLACE, not reinforce — reinforcing it would average two
opposite instructions into one confident wrong one.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.learning.hygiene import MIN_EVIDENCE_DEFAULT, fingerprint

logger = logging.getLogger(__name__)

_DIRNAME = "proposals"
_DECISIONS_FILE = "decisions.json"
_EXCERPT_MAX = 4_000

#: Pending-queue cap. Beyond this the oldest expire — an unbounded queue is an
#: unread queue, and a proposal nobody will ever reach is worse than none.
MAX_PENDING = 100

#: Per-run cap on how many proposals one pass may file. A pass that files twenty
#: is not being thorough, it is being unreadable.
DEFAULT_QUOTA_PER_RUN = 5

#: Cosine bands for the resolve cascade. Below NEW it is a new thing; at or above
#: REINFORCE it is the same thing said again; between them it is a variant that
#: specializes its parent rather than replacing it.
SIM_NEW = 0.85
SIM_REINFORCE = 0.92
#: The subject guard: two statements about DIFFERENT subjects are never the same
#: proposal however similar their wording.
SIM_SUBJECT = 0.60

#: Re-propose cooldown after a rejection, in days, escalating per rejection.
_COOLDOWN_DAYS = (7, 30, 90, 365)


class Kind(str, Enum):
    """The proposal kinds. Closed, so a typo cannot invent an unlisted one.

    The three ``project_*`` kinds (LEA-12) are the review's typed output: a self-updating
    project context that PROPOSES rather than writes, so a run's learnings reach the
    project's overview/ledger/inlined-file/skill only through the same human gate every
    other kind clears.

    ``KNOWLEDGE_DRAFT`` (KNOWLEDGE-SYNTHESIS §3.3/§3.4, WF2KNO-8) is the same bargain for
    the knowledge store: a gap-healing or schema-edit draft reaches
    ``workspace/knowledge`` only after a human accepts it. Before this kind existed the
    gap-healing template had no way to file one — ``enqueue`` SKIPS an unlisted kind and
    logs at debug — so the template wrote a TTL'd probe straight into the store instead,
    which is the self-citation anti-pattern the template's own doctrine warns about.
    """

    SKILL = "skill"
    LESSON_BATCH = "lesson_batch"
    TEMPLATE = "template"
    TEMPLATE_DIFF = "template_diff"
    RETIREMENT = "retirement"
    TIER_MIGRATION = "tier_migration"
    PROJECT_INSTRUCTION = "project_instruction"
    PROJECT_FILE = "project_file"
    PROJECT_SKILL = "project_skill"
    KNOWLEDGE_DRAFT = "knowledge_draft"


class Status(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    #: "later" — kept for the next pass rather than decided.
    DRAFT = "draft"
    SUPERSEDED = "superseded"


class Verdict(str, Enum):
    """The four outcomes of the deterministic resolve cascade."""

    NEW = "new"
    REINFORCE = "reinforce"
    REPLACE = "replace"
    MERGE = "merge"
    #: Not a cascade outcome but a gate one: a prior decision forbids re-filing.
    SKIP = "skip"


@dataclass
class ChangeManifest:
    """Why this change, and what it is predicted to fix (LEARN-R16).

    Validation is lenient-but-recording: a missing or thin manifest yields
    ``manifest_valid=False`` on the record and a warning in the inbox, never a
    hard reject. A proposal blocked for a metadata problem is a proposal the user
    never gets to judge, and the judgment is the point.
    """

    component: str = ""
    files: list[str] = field(default_factory=list)
    failure_pattern: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    root_cause: str = ""
    # A prose description for most kinds; for a `template_diff` it carries the refiner's TYPED
    # ops list (the inbox's `_tier_for` reads exactly this to stamp a risk tier, and the accept
    # path applies it), so the type is honestly either.
    targeted_fix: str | list[dict[str, Any]] = ""
    predicted_fixes: list[str] = field(default_factory=list)
    risk_tasks: list[str] = field(default_factory=list)

    def issues(self) -> list[str]:
        """What's missing. Empty means complete."""
        missing = []
        if not self.component:
            missing.append("component")
        if not self.failure_pattern:
            missing.append("failure_pattern")
        if not self.evidence_refs:
            missing.append("evidence_refs")
        if not self.root_cause:
            missing.append("root_cause")
        if not self.targeted_fix:
            missing.append("targeted_fix")
        return missing

    def is_valid(self) -> bool:
        return not self.issues()


@dataclass
class Proposal:
    """One pending, human-reviewable change."""

    id: str
    kind: str
    title: str
    body: str
    target: str = ""
    fingerprint: str = ""
    status: str = Status.PENDING.value
    created_at: str = ""
    updated_at: str = ""
    #: How many times this exact content has been independently observed. The
    #: evidence floor (``MIN_EVIDENCE_DEFAULT``) applies to INFERRED proposals.
    reinforcements: int = 1
    #: A variant links to the proposal it narrows rather than merging into it —
    #: merging would lose the distinction that made it worth filing.
    specializes: str = ""
    supersedes: str = ""
    #: "human" outranks "inferred" in scoring and decays slower: a correction the
    #: user actually made is gold, a pattern the system noticed is a hypothesis.
    provenance: str = "inferred"
    source_cadence: str = ""
    session_key: str = ""
    run_id: str = ""
    #: FENCED excerpt of the driving evidence — review-only, never executable.
    source_excerpt: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    staging_refs: list[int] = field(default_factory=list)
    change_manifest: dict[str, Any] = field(default_factory=dict)
    manifest_valid: bool = True
    manifest_issues: list[str] = field(default_factory=list)
    #: Labeled "correlated" unless a causal link was actually established. A
    #: proposal that claims causation from co-occurrence is a confident lie.
    evidence_strength: str = "correlated"
    confidence: float = 0.0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        """The compact list view — no full body."""
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "target": self.target,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "reinforcements": self.reinforcements,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "evidence_strength": self.evidence_strength,
            "manifest_valid": self.manifest_valid,
            "manifest_issues": self.manifest_issues,
            "specializes": self.specializes,
            "body_preview": self.body[:280],
        }


@dataclass
class Decision:
    """A remembered accept/reject, keyed by fingerprint.

    Rejections are kept, not deleted. A store that forgets its rejections re-files
    the same suggestion forever, and the user's only defence is to stop reading.
    """

    fingerprint: str
    verdict: str  # accepted | rejected
    kind: str
    title: str
    decided_at: str
    rejections: int = 0
    cooldown_until: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def content_fingerprint(kind: str, target: str, body: str) -> str:
    """Order-independent fingerprint of (kind, target, normalized body).

    The target is part of it because the same advice about two different templates
    is two proposals; the body is normalized (via ``hygiene.fingerprint``) so a
    reflowed paragraph is recognised as the same content rather than as a new one.
    """
    return fingerprint(f"{kind}\x1f{target}\x1f{body}")


# ── Storage ──


def _dir() -> Path:
    """Resolve the config dir dynamically so a test repointing it is honored.

    A module-level ``from ... import config_dir`` would bind the original and leak
    writes into the real home — the mistake the skills queue documents.
    """
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / "learning" / _DIRNAME


def _decisions_path() -> Path:
    return _dir() / _DECISIONS_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_decisions() -> dict[str, Decision]:
    try:
        raw = json.loads(_decisions_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, Decision] = {}
    for fp, data in (raw or {}).items():
        try:
            out[str(fp)] = Decision(**data)
        except (TypeError, ValueError):
            continue
    return out


def save_decisions(decisions: dict[str, Decision]) -> None:
    try:
        atomic_write(
            _decisions_path(),
            json.dumps({fp: d.to_dict() for fp, d in decisions.items()}, indent=2),
        )
    except OSError:
        logger.debug("decision store write failed", exc_info=True)


def record_decision(prop: Proposal, verdict: str) -> None:
    """Remember an accept/reject so the same content is never re-filed.

    A repeat rejection escalates the cooldown rather than resetting it: the second
    "no" to the same idea means more than the first.
    """
    decisions = load_decisions()
    existing = decisions.get(prop.fingerprint)
    rejections = (existing.rejections if existing else 0) + (1 if verdict == "rejected" else 0)
    cooldown = 0.0
    if verdict == "rejected":
        days = _COOLDOWN_DAYS[min(rejections, len(_COOLDOWN_DAYS)) - 1]
        cooldown = time.time() + days * 86400
    decisions[prop.fingerprint] = Decision(
        fingerprint=prop.fingerprint,
        verdict=verdict,
        kind=prop.kind,
        title=prop.title,
        decided_at=_now(),
        rejections=rejections,
        cooldown_until=cooldown,
    )
    save_decisions(decisions)


def _load(pid: str) -> Proposal | None:
    try:
        data = json.loads((_dir() / f"{pid}.json").read_text(encoding="utf-8"))
        return Proposal(**data)
    except (OSError, ValueError, TypeError):
        return None


def _save(prop: Proposal) -> bool:
    try:
        atomic_write(_dir() / f"{prop.id}.json", json.dumps(prop.to_dict(), indent=2))
        return True
    except OSError:
        logger.debug("proposal write failed", exc_info=True)
        return False


def _all() -> list[Proposal]:
    d = _dir()
    if not d.is_dir():
        return []
    out: list[Proposal] = []
    for p in sorted(d.glob("*.json")):
        if p.name == _DECISIONS_FILE:
            continue
        try:
            out.append(Proposal(**json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return out


def list_pending(kind: str = "") -> list[Proposal]:
    """Pending proposals, newest first."""
    props = [p for p in _all() if p.status == Status.PENDING.value]
    if kind:
        props = [p for p in props if p.kind == kind]
    props.sort(key=lambda r: r.created_at, reverse=True)
    return props


def get(pid: str) -> Proposal | None:
    return _load(pid)


# ── The resolve cascade ──


def _polarity(text: str) -> int:
    """Crude negation polarity: -1 negated, +1 otherwise.

    Deliberately lexical and zero-cost. It only has to catch the case that matters
    — "always X" vs "never X" — and it runs on every write, so an LLM call here
    would tax the whole pipeline for a distinction a word list resolves.
    """
    lowered = f" {text.lower()} "
    negations = (
        " never ",
        " don't ",
        " do not ",
        " avoid ",
        " stop ",
        " no longer ",
        " not ",
        " without ",
    )
    return -1 if any(n in lowered for n in negations) else 1


#: Words that carry no subject information. Stripped before taking the subject span
#: because they are exactly what shifts when a statement is negated: "always use uv"
#: vs "always never use uv" have different first-two-words but the SAME subject, and
#: measuring showed that shift silently defeated contradiction detection — the pair
#: resolved as NEW, so two opposite instructions both stayed pending.
_SUBJECT_STOPWORDS = frozenset(
    {
        "a",
        "always",
        "an",
        "avoid",
        "dont",
        "don't",
        "do",
        "never",
        "no",
        "not",
        "prefer",
        "should",
        "stop",
        "the",
        "to",
        "use",
        "using",
        "we",
        "you",
    }
)


def _subject_span(text: str) -> str:
    """The first two CONTENT words — a cheap stand-in for "what is this about".

    Polarity and modal words are stripped first, so negating a statement does not
    change its subject. Without that, the guard meant to prevent cross-subject
    matches instead prevented same-subject contradiction detection.
    """
    words = [w.strip(".,;:!?\"'") for w in (text or "").lower().split()]
    content = [w for w in words if w and w not in _SUBJECT_STOPWORDS]
    return " ".join(content[:2])


def _numbers(text: str) -> set[str]:
    return {tok for tok in (text or "").replace(",", " ").split() if tok.replace(".", "").isdigit()}


#: How similar two statements must be before a NUMBER difference counts as a
#: contradiction rather than as two unrelated facts. Measured: four distinct
#: lessons that merely contained different digits ("… number 0 about topic0" vs
#: "… number 1 about topic1") scored 0.6 similarity and were all judged
#: contradictory, so each replaced the last and the queue collapsed to ONE row.
#: Polarity needs no such guard — a negation barely moves the tokens, which is
#: exactly why it must be caught at low similarity.
_NUMBER_CONFLICT_MIN_SIM = 0.75


def contradicts(a: str, b: str) -> bool:
    """True if two statements assert opposite things about the same subject.

    Checked BEFORE the reinforce shortcut. Near-identical wording with flipped
    polarity is the dangerous case: reinforcing "always use X" with "never use X"
    would produce one confidently wrong instruction out of two correct ones.
    """
    if _subject_span(a) != _subject_span(b):
        return False
    if _polarity(a) != _polarity(b):
        return True
    na, nb = _numbers(a), _numbers(b)
    if not (na and nb and na != nb):
        return False
    # Same subject, same polarity, different numbers — a conflict only if the rest
    # of the statement is substantially the same. Otherwise two unrelated facts
    # that happen to mention different quantities would supersede each other.
    return _similarity(a, b) >= _NUMBER_CONFLICT_MIN_SIM


def _similarity(a: str, b: str) -> float:
    """Token-overlap similarity in [0, 1].

    Jaccard rather than embeddings: the cascade runs on EVERY proposal write, so it
    must not depend on an embedder being configured — the no-embedder path is a
    supported configuration, and a cascade that silently degrades to "everything is
    new" there would fill the queue with duplicates exactly where nobody notices.
    """
    ta = set((a or "").lower().split())
    tb = set((b or "").lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def resolve(candidate: Proposal, existing: list[Proposal]) -> tuple[Verdict, Proposal | None]:
    """Decide what this candidate IS relative to what's already pending.

    Returns the verdict and the row it applies to (None for NEW). Deterministic and
    LLM-free, so it can run on every write.
    """
    best: Proposal | None = None
    best_sim = 0.0
    for other in existing:
        if other.kind != candidate.kind or other.status != Status.PENDING.value:
            continue
        # A different target is a different proposal, full stop. The same advice
        # about two templates is two changes to make, and the fingerprint already
        # includes the target — comparing bodies across targets would let one
        # template's fix silently supersede another's.
        if other.target != candidate.target:
            continue
        if other.fingerprint == candidate.fingerprint:
            return Verdict.REINFORCE, other
        # Contradiction is checked on SUBJECT match, not on similarity rank, and
        # before any threshold. Measured: "always use uv …" vs "always avoid uv …"
        # scores 0.80 by token overlap — below SIM_NEW — so a similarity-gated
        # check would file the opposite instruction as a NEW proposal and leave
        # both pending. Negation barely changes the tokens but completely changes
        # the meaning, which is what makes overlap the wrong gate here.
        if contradicts(candidate.body, other.body):
            return Verdict.REPLACE, other
        sim = _similarity(candidate.body, other.body)
        if sim > best_sim:
            best_sim, best = sim, other

    if best is None or best_sim < SIM_NEW:
        return Verdict.NEW, None

    if _subject_span(candidate.body) != _subject_span(best.body):
        return Verdict.NEW, None

    if best_sim >= SIM_REINFORCE:
        return Verdict.REINFORCE, best
    return Verdict.MERGE, best


# ── Filing ──


def _prior_decision_blocks(fp: str, decisions: dict[str, Decision]) -> str:
    """Why a prior decision forbids re-filing this, or "" if it doesn't."""
    prior = decisions.get(fp)
    if prior is None:
        return ""
    if prior.verdict == "accepted":
        return "already accepted"
    if prior.cooldown_until and time.time() < prior.cooldown_until:
        return f"rejected {prior.rejections}x, cooling down"
    return "previously rejected"


def enqueue(
    *,
    kind: str,
    title: str,
    body: str,
    target: str = "",
    provenance: str = "inferred",
    source_cadence: str = "",
    session_key: str = "",
    run_id: str = "",
    source_excerpt: str = "",
    evidence_refs: list[str] | None = None,
    staging_refs: list[int] | None = None,
    change_manifest: ChangeManifest | dict | None = None,
    evidence_strength: str = "correlated",
    confidence: float = 0.0,
    tags: list[str] | None = None,
    occurrences: int = 0,
    min_evidence: int = MIN_EVIDENCE_DEFAULT,
) -> tuple[Verdict, Proposal | None]:
    """File a proposal, honoring decision memory and the resolve cascade.

    Returns ``(verdict, proposal)``. A SKIP means a prior decision forbids it and
    nothing was written — the caller should treat that as success, not failure:
    not nagging is the feature.

    The evidence floor applies to INFERRED proposals only. A human correction is
    evidence by itself; requiring three occurrences of it would mean ignoring the
    user twice before listening.
    """
    if not (kind and title and body):
        return Verdict.SKIP, None
    try:
        Kind(kind)
    except ValueError:
        logger.debug("unknown proposal kind %r", kind)
        return Verdict.SKIP, None

    if provenance != "human" and occurrences and occurrences < max(1, min_evidence):
        logger.debug(
            "proposal below evidence floor (%d < %d): %s", occurrences, min_evidence, title
        )
        return Verdict.SKIP, None

    fp = content_fingerprint(kind, target, body)
    decisions = load_decisions()
    blocked = _prior_decision_blocks(fp, decisions)
    if blocked:
        logger.info("skipping re-file of %r (%s)", title, blocked)
        return Verdict.SKIP, None

    manifest = change_manifest
    if isinstance(manifest, ChangeManifest):
        issues = manifest.issues()
        manifest_dict = asdict(manifest)
    elif isinstance(manifest, dict) and manifest:
        try:
            issues = ChangeManifest(**manifest).issues()
        except (TypeError, ValueError):
            issues = ["malformed"]
        manifest_dict = manifest
    else:
        manifest_dict, issues = {}, []
        if kind in (Kind.TEMPLATE_DIFF.value, Kind.SKILL.value):
            issues = ["missing"]

    fenced = ""
    if source_excerpt:
        try:
            from personalclaw.security import fence_untrusted

            fenced = fence_untrusted(source_excerpt[:_EXCERPT_MAX], source=f"{kind}-evidence")
        except Exception:
            fenced = ""  # fencing failure must never block the proposal

    now = _now()
    candidate = Proposal(
        id="",
        kind=kind,
        title=title,
        body=body,
        target=target,
        fingerprint=fp,
        created_at=now,
        updated_at=now,
        provenance=provenance,
        source_cadence=source_cadence,
        session_key=session_key,
        run_id=run_id,
        source_excerpt=fenced,
        evidence_refs=list(evidence_refs or []),
        staging_refs=list(staging_refs or []),
        change_manifest=manifest_dict,
        manifest_valid=not issues,
        manifest_issues=issues,
        evidence_strength=evidence_strength,
        confidence=float(confidence),
        reinforcements=max(1, int(occurrences or 1)),
        tags=list(tags or []),
    )

    existing = _all()
    verdict, match = resolve(candidate, existing)

    if verdict is Verdict.REINFORCE and match is not None:
        match.reinforcements += 1
        match.updated_at = now
        match.tags = sorted(set(match.tags) | set(candidate.tags))
        # Human provenance wins on merge: a pattern later confirmed by the user is
        # human-originated from then on, and outranks inferred rows accordingly.
        if provenance == "human":
            match.provenance = "human"
        _save(match)
        return Verdict.REINFORCE, match

    candidate.id = f"{kind}-{fp[:12]}"

    if verdict is Verdict.REPLACE and match is not None:
        candidate.supersedes = match.id
        match.status = Status.SUPERSEDED.value
        match.updated_at = now
        _save(match)
        # A superseded proposal can never be acted on — it no longer appears in the
        # queue — so its inbox row has to be resolved here. Found by driving the real
        # dev home: the row sat PENDING forever, claiming attention for a decision
        # the user could not reach from any surface.
        _resolve_inbox_item(match.id, "dismissed")
    elif verdict is Verdict.MERGE and match is not None:
        # A variant SPECIALIZES its parent rather than merging into it — merging
        # would erase the narrower case that justified filing it.
        candidate.specializes = match.id

    if len(existing) >= MAX_PENDING:
        _expire_oldest(existing)

    if not _save(candidate):
        return Verdict.SKIP, None
    logger.info("Queued %s proposal %s (%s)", kind, candidate.id, verdict.value)
    _surface_in_inbox(candidate)
    if verdict is Verdict.REPLACE:
        prune_superseded()
    return verdict, candidate


#: How many superseded records to keep. They are the supersession lineage — "what
#: did this replace" — so they cannot be deleted on sight, but they are also never
#: actionable, so an unbounded pile is pure disk growth.
_SUPERSEDED_KEEP = 50


def prune_superseded(keep: int = _SUPERSEDED_KEEP) -> int:
    """Drop the oldest superseded records beyond *keep*. Returns how many went.

    Found by driving the real dev home: a REPLACE left its predecessor's file on
    disk with no path that ever removes it. Lineage is worth keeping, an unbounded
    pile is not.
    """
    superseded = sorted(
        (p for p in _all() if p.status == Status.SUPERSEDED.value),
        key=lambda p: p.updated_at or p.created_at,
    )
    removed = 0
    for prop in superseded[: max(0, len(superseded) - max(0, keep))]:
        try:
            (_dir() / f"{prop.id}.json").unlink()
            removed += 1
        except OSError:
            logger.debug("superseded prune failed for %s", prop.id, exc_info=True)
    if removed:
        logger.info("pruned %d superseded proposal record(s)", removed)
    return removed


def _expire_oldest(existing: list[Proposal]) -> None:
    """Drop the oldest pending proposal to make room.

    Oldest rather than newest: a proposal that has waited longest without a
    decision is the one the user is least likely to ever act on.
    """
    pending = sorted(
        (p for p in existing if p.status == Status.PENDING.value), key=lambda p: p.created_at
    )
    if not pending:
        return
    victim = pending[0]
    try:
        (_dir() / f"{victim.id}.json").unlink()
        logger.info("proposal queue full; expired oldest %s", victim.id)
    except OSError:
        logger.debug("proposal expiry failed", exc_info=True)


# ── Inbox surfacing ──

_KIND_LABELS = {
    Kind.SKILL.value: "New skill proposed",
    Kind.LESSON_BATCH.value: "Lessons to review",
    Kind.TEMPLATE.value: "New template proposed",
    Kind.TEMPLATE_DIFF.value: "Template change proposed",
    Kind.RETIREMENT.value: "Retire something unused",
    Kind.TIER_MIGRATION.value: "Move to a different tier",
    Kind.PROJECT_INSTRUCTION.value: "Project instruction proposed",
    Kind.PROJECT_FILE.value: "Project context update proposed",
    Kind.PROJECT_SKILL.value: "Project skill proposed",
    Kind.KNOWLEDGE_DRAFT.value: "Knowledge entry drafted for review",
}


def _surface_in_inbox(prop: Proposal) -> None:
    """Raise the proposal as a durable inbox item.

    A proposal is a standing request: it waits until the user decides. Best-effort —
    surfacing must never fail the enqueue, because the proposal still exists in its
    own store and the Proposal Inbox reads from there.
    """
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item

        state = None
        try:
            from personalclaw.inbox_providers.native_source import get_dashboard_state

            state = get_dashboard_state()
        except Exception:
            logger.debug("proposal inbox surface: no dashboard state", exc_info=True)

        title = _KIND_LABELS.get(prop.kind, "Proposal")
        # INU-7: the row carries the C6 payload, so approving it dispatches through the
        # ONE proposals contract (`apply.skill_promotion` → this module's `accept`) instead
        # of the inbox handler hard-wiring this queue by name. `refs["learning_proposal"]`
        # stays for the existing readers — the contract is additive, not a replacement.
        from personalclaw.proposals_contract import REFS_KEY, Proposal

        payload = Proposal(
            title=prop.title or title,
            preview=prop.body or prop.title or "",
            preview_kind="text",
            provenance="learning",
            editable=False,
            apply={"skill_promotion": {"pid": prop.id}},
        )
        emit_attention_item(
            state,
            source="learning",
            kind="proposal",
            item_kind=ItemKind.PROPOSAL.value,
            title=title,
            body=f"{prop.title}",
            refs={
                "learning_proposal": prop.id,
                "session": prop.session_key,
                REFS_KEY: payload.to_dict(),
            },
            dedup_key=f"learning_proposal:{prop.id}",
        )
    except Exception:
        logger.debug("proposal inbox surface failed", exc_info=True)


def _resolve_inbox_item(pid: str, status: str) -> None:
    """Move the proposal's inbox item to a terminal status once decided.

    Without this the row claims attention forever for work already done. Uses the
    LIVE store when the service is running: the running InboxService holds items in
    memory and never re-reads the file, so writing only to disk would leave the row
    visible in the UI until a restart.
    """
    try:
        from personalclaw.inbox import InboxStore, live_store

        state = None
        try:
            from personalclaw.inbox_providers.native_source import get_dashboard_state

            state = get_dashboard_state()
        except Exception:
            state = None

        store = live_store(state) if state is not None else None
        persist = store is None
        if store is None:
            store = InboxStore()
            store.load()
        changed = False
        for item in store.items.values():
            if item.refs.get("learning_proposal") == pid and item.status != status:
                item.status = status
                changed = True
        if changed:
            store.save()
            if not persist:
                logger.debug("resolved proposal inbox item in the live store")
    except Exception:
        logger.debug("proposal inbox resolve failed", exc_info=True)


# ── Decide ──


def _audit(operation: str, prop: Proposal, outcome: str) -> None:
    """SEL-audit an accept/reject, like a skill install.

    Accepting a proposal installs autonomously-authored behaviour. That is exactly
    the class of act the security event log exists to make reviewable after the
    fact, so it is audited whether the decision came from the UI, the API, or chat.
    """
    try:
        from personalclaw.sel import SecurityEvent, sel

        sel().log(
            SecurityEvent(
                event_id=os.urandom(8).hex(),
                timestamp=_now(),
                event_type="api_access",
                caller_identity=prop.session_key or os.environ.get("USER", "owner"),
                agent="personalclaw",
                source="dashboard",
                operation=operation,
                outcome=outcome,
                resources=f"{prop.kind}:{prop.id} target={prop.target or '-'}",
                metadata={
                    "kind": prop.kind,
                    "provenance": prop.provenance,
                    "reinforcements": prop.reinforcements,
                    "fingerprint": prop.fingerprint,
                },
            )
        )
    except Exception:
        logger.debug("proposal SEL audit failed", exc_info=True)


def reject(pid: str, *, actor: str = "user") -> bool:
    """Reject a proposal and REMEMBER it. Returns True if it existed.

    The remembering is the point: the record outlives the row, so the same content
    is skipped rather than re-filed on the next pass.

    ``actor`` is gated for a subtler reason than accepting (S75): an agent that could
    reject would clear its own bad proposals out of the queue before a human ever
    read them — and the rejection exemplars §2.2 learns from would silently stop
    accumulating. Returns False on a refusal rather than raising, matching the
    not-found path: a caller that cannot reject and a row that does not exist are
    the same outcome from the caller's side.
    """
    from personalclaw.learning.inbox import require_human

    prop = _load(pid)
    if prop is None:
        return False
    gate = require_human(action="reject", actor=actor, status=prop.status)
    if not gate.allowed:
        _audit("learning_proposal_reject", prop, "blocked")
        logger.warning("Blocked %s reject of %s: %s", actor, pid, gate.reason)
        return False
    prop.status = Status.REJECTED.value
    prop.updated_at = _now()
    record_decision(prop, "rejected")
    try:
        (_dir() / f"{pid}.json").unlink()
    except OSError:
        logger.debug("proposal delete failed", exc_info=True)
    _resolve_inbox_item(pid, "dismissed")
    _audit("learning_proposal_reject", prop, "rejected")
    logger.info("Rejected %s proposal %s", prop.kind, pid)
    return True


def defer(pid: str) -> bool:
    """ "Later" — keep the row as a DRAFT for the next pass, decide nothing.

    Deliberately records NO decision: a deferral is not a rejection, and treating
    it as one would suppress a proposal the user meant to revisit.
    """
    prop = _load(pid)
    if prop is None:
        return False
    prop.status = Status.DRAFT.value
    prop.updated_at = _now()
    return _save(prop)


class AcceptError(Exception):
    """Raised when a proposal cannot be accepted."""


def accept(pid: str, *, installer=None, actor: str = "user") -> Proposal:
    """Accept a proposal: install via *installer*, then remember the decision.

    The installer is injected rather than dispatched here on purpose. This module
    owns the queue and the decision memory; it must not also know how to write a
    skill, a template and a tier migration — that coupling is what made the old
    single-kind queue impossible to generalize.

    The decision is recorded ONLY after the install succeeds. Recording first would
    mean a failed install permanently suppresses its own retry.

    ``actor`` gates the call (LEARNING-FLYWHEEL §7 — S75). Measured before it existed:
    NOTHING here knew who was accepting, so "the model cannot accept its own
    proposals" held only because no agent tool happened to call this — an absence,
    not a control, and one new MCP tool would have removed it silently. It defaults
    to ``user`` so every existing human-facing caller is unaffected, and an agent or
    engine caller is refused outright regardless of trust mode.
    """
    from personalclaw.learning.inbox import audit_denial, require_human

    prop = _load(pid)
    if prop is None:
        raise AcceptError(f"no proposal {pid!r}")

    gate = require_human(action="accept", actor=actor, status=prop.status)
    if not gate.allowed:
        row = audit_denial(action="accept", actor=actor, pid=pid, gate=gate)
        _audit("learning_proposal_accept", prop, "blocked")
        logger.warning("Blocked %s accept of %s: %s", actor, pid, row["reason"])
        raise AcceptError(gate.reason)
    if installer is not None:
        try:
            installer(prop)
        except Exception as exc:
            _audit("learning_proposal_accept", prop, "failed")
            raise AcceptError(f"install failed for {pid!r}: {exc}") from exc

    prop.status = Status.ACCEPTED.value
    prop.updated_at = _now()
    record_decision(prop, "accepted")
    # Snapshot the bet for predict-then-verify grading (§3.1 / WF2LEA-5). This is the ONLY moment
    # the change's predicted_fixes + target + pre-acceptance failure rates are still knowable: the
    # proposal file is unlinked below and only a manifest-less Decision survives. Best-effort and
    # self-guarded: recording a change for LATER grading must never fail the accept just made.
    try:
        from personalclaw.learning import attribution

        attribution.record_accepted_change(prop)
    except Exception:
        logger.debug("attribution record failed for %s", pid, exc_info=True)
    try:
        (_dir() / f"{pid}.json").unlink()
    except OSError:
        logger.debug("proposal delete failed", exc_info=True)
    _resolve_inbox_item(pid, "handled")
    _audit("learning_proposal_accept", prop, "completed")
    logger.info("Accepted %s proposal %s", prop.kind, pid)
    return prop


def quota_remaining(filed: int, quota: int | None = None) -> int:
    """How many more proposals this pass may file.

    Reads ``learning.propose_quota_per_run`` when no explicit quota is given, so the
    owner's knob is what actually bounds a pass — a module constant that config
    cannot override is a knob that does nothing.
    """
    if quota is None:
        try:
            from personalclaw.config.loader import AppConfig

            quota = int(getattr(AppConfig.load().learning, "propose_quota_per_run", 0) or 0)
        except Exception:
            quota = 0
        if quota <= 0:
            quota = DEFAULT_QUOTA_PER_RUN
    return max(0, quota - max(0, filed))
