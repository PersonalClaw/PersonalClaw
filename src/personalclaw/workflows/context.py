"""Context lifecycle for long-horizon nodes (WF2-R6).

The templates this engine ships — `deep-research`, `audit-sweep` — are exactly the shapes where
compaction alone demonstrably fails. Compaction keeps the WHAT and drops the WHY, so a compacted
loop re-litigates decisions it already settled, re-reads files it already verified, and reports
confident conclusions built on summaries of summaries.

Three mechanisms, each addressing a different way that goes wrong:

**Handoffs** (`session: fresh`). An iteration ends by writing a structured record of where it got
to — verified state, what changed, what is broken or unverified, and the next action — and the next
iteration STARTS from that instead of from a compacted transcript. It is a smaller, denser and more
honest input than a summary, because it was written by the iteration that actually did the work
while it still remembered. Journaled, so rewind and fork replay it correctly rather than
reconstructing it.

**Carryover buckets.** Bounded, deduped, TYPED facts that survive any reset: files touched with
line spans, work verified, children spawned. Prose handoffs degrade under summarization —
"I checked the auth module" loses the line numbers — and these do not, because they are structure
rather than narrative.

**Decision records.** `{choice, reason, rejected_alternatives, constraints}`. The rejected
alternatives are the load-bearing half: without them a resumed run re-proposes the option that was
already considered and dismissed, and nothing in a compacted transcript says it was.

Everything here is **bounded**. An unbounded carryover bucket is just a transcript with extra
steps, and the failure mode it would reintroduce — context exhaustion on a long run — is the one it
exists to prevent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Per-bucket item ceiling. Chosen to be small: a bucket is a working set, not a log. Fifty files
#: touched is already more than a handoff can usefully act on, and the oldest entries are the ones
#: least likely to matter to the next iteration.
MAX_BUCKET_ITEMS = 50

#: Handoff free-text field ceiling. Long enough for a real paragraph, short enough that fifty
#: iterations of handoffs cannot themselves exhaust the context they exist to protect.
MAX_HANDOFF_FIELD = 2000

#: Session policies (WF2-R6). `fresh` is the default for iterated bodies: a continuous session
#: across twenty iterations is precisely the case where compaction fails.
SESSION_FRESH = "fresh"
SESSION_CONTINUOUS = "continuous"
SESSION_POLICIES = (SESSION_FRESH, SESSION_CONTINUOUS)


def session_policy(node_config: dict[str, Any] | None) -> str:
    """The declared session policy, defaulting to `fresh`.

    Fresh by default, deliberately: the long-horizon iterated shapes are the common case for this
    engine, and `continuous` is the choice that needs justifying. An unrecognized value reads as
    `fresh` rather than raising — the safe direction, since a typo'd policy that silently kept a
    session alive is the failure this exists to prevent.
    """
    raw = str((node_config or {}).get("session", "") or "").strip().lower()
    return raw if raw in SESSION_POLICIES else SESSION_FRESH


@dataclass
class Handoff:
    """What one iteration tells the next (WF2-R6).

    The four fields are not arbitrary — each answers a question the next iteration would otherwise
    have to re-derive from a transcript it no longer has:

    * `verified_state` — what is known to be TRUE, having been checked. The expensive part to
      rebuild, and the part a summary renders as plausible-but-unchecked.
    * `changes` — what this iteration actually altered. Without it the next iteration cannot tell
      its own effects from the world's.
    * `unverified` — what is broken, assumed, or was not reached. The field that stops a loop
      reporting success over an unexamined gap.
    * `next_action` — what to do first. A handoff that describes state without naming the next move
      makes the reader re-plan from scratch.
    """

    verified_state: str = ""
    changes: str = ""
    unverified: str = ""
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified_state": _clip(self.verified_state),
            "changes": _clip(self.changes),
            "unverified": _clip(self.unverified),
            "next_action": _clip(self.next_action),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Handoff:
        d = d or {}
        return cls(
            verified_state=str(d.get("verified_state", "") or ""),
            changes=str(d.get("changes", "") or ""),
            unverified=str(d.get("unverified", "") or ""),
            next_action=str(d.get("next_action", "") or ""),
        )

    @property
    def empty(self) -> bool:
        """True when there is nothing worth handing over.

        An empty handoff is NOT rendered into the next prompt: a heading followed by four blank
        fields teaches a model that this section is noise, which is how the whole mechanism stops
        working.
        """
        return not any((self.verified_state, self.changes, self.unverified, self.next_action))

    def render(self) -> str:
        """The handoff as prompt text for the next iteration.

        Labelled sections rather than prose, because the next reader is a model that benefits from
        knowing which claims were VERIFIED and which were not — a paragraph blurs exactly that
        distinction.
        """
        parts: list[str] = []
        if self.verified_state:
            parts.append(f"Verified so far:\n{_clip(self.verified_state)}")
        if self.changes:
            parts.append(f"Changed by the previous iteration:\n{_clip(self.changes)}")
        if self.unverified:
            parts.append("NOT verified (do not assume these hold):\n" + _clip(self.unverified))
        if self.next_action:
            parts.append(f"Start with:\n{_clip(self.next_action)}")
        return "\n\n".join(parts)


@dataclass
class Carryover:
    """Typed facts that survive a session reset (WF2-R6).

    Structure, not narrative — that is the entire point. A prose handoff summarized twice loses the
    line spans and the file names; a list of `{path, lines}` does not, because there is nothing in
    it for a summarizer to compress away.

    Bounded and deduped. An unbounded bucket is a transcript with extra steps, and it would
    reintroduce the context exhaustion this mechanism exists to prevent.
    """

    #: `{path, lines?}` — what was read or written, with spans where known.
    files_touched: list[dict[str, Any]] = field(default_factory=list)
    #: Claims that were CHECKED, not merely believed.
    verified: list[str] = field(default_factory=list)
    #: Child run ids this node spawned, so a resumed iteration does not spawn them twice.
    spawned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_touched": list(self.files_touched),
            "verified": list(self.verified),
            "spawned": list(self.spawned),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Carryover:
        d = d or {}
        return cls(
            files_touched=[f for f in (d.get("files_touched") or []) if isinstance(f, dict)],
            verified=[str(v) for v in (d.get("verified") or [])],
            spawned=[str(s) for s in (d.get("spawned") or [])],
        )

    @property
    def empty(self) -> bool:
        return not (self.files_touched or self.verified or self.spawned)

    def merge(self, other: Carryover) -> Carryover:
        """Fold another iteration's carryover in, deduped and bounded.

        Returns a NEW Carryover: the caller usually holds the previous iteration's, and mutating it
        would make a rewind replay a bucket that had already absorbed the future.

        The OLDEST entries are dropped when a bucket overflows, because recency is the best cheap
        proxy for relevance to the next iteration — and keeping a bounded tail is what makes the
        bound safe to raise later without changing semantics.
        """
        files = _dedupe_dicts(self.files_touched + other.files_touched, key="path")
        return Carryover(
            files_touched=files[-MAX_BUCKET_ITEMS:],
            verified=_dedupe(self.verified + other.verified)[-MAX_BUCKET_ITEMS:],
            spawned=_dedupe(self.spawned + other.spawned)[-MAX_BUCKET_ITEMS:],
        )

    def render(self) -> str:
        """The carryover as prompt text. Compact on purpose — this is reference material the next
        iteration consults, not a narrative it reads."""
        parts: list[str] = []
        if self.files_touched:
            shown = ", ".join(_file_label(f) for f in self.files_touched[-12:])
            parts.append(f"Files already touched: {shown}")
        if self.verified:
            parts.append("Already verified:\n" + "\n".join(f"- {v}" for v in self.verified[-12:]))
        if self.spawned:
            parts.append(f"Children already spawned: {', '.join(self.spawned[-12:])}")
        return "\n\n".join(parts)


@dataclass
class Decision:
    """A settled choice and WHY (WF2-R6).

    `rejected` is the load-bearing field. Compaction keeps "we used Postgres" and drops "we
    rejected SQLite because the write concurrency did not fit", so a resumed or forked run
    re-proposes SQLite and nothing in its context says that was already considered. A decision
    record with no rejected alternatives is a note; with them it is a constraint.
    """

    choice: str = ""
    reason: str = ""
    rejected: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "choice": _clip(self.choice),
            "reason": _clip(self.reason),
            "rejected_alternatives": [_clip(str(r), 300) for r in self.rejected[:12]],
            "constraints": [_clip(str(c), 300) for c in self.constraints[:12]],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Decision:
        d = d or {}
        return cls(
            choice=str(d.get("choice", "") or ""),
            reason=str(d.get("reason", "") or ""),
            # Both spellings accepted: the journal writes `rejected_alternatives` (the plan's
            # field name) and a model authoring one naturally writes `rejected`.
            rejected=[str(r) for r in (d.get("rejected_alternatives") or d.get("rejected") or [])],
            constraints=[str(c) for c in (d.get("constraints") or [])],
        )

    @property
    def empty(self) -> bool:
        return not self.choice.strip()

    def render(self) -> str:
        parts = [f"Decided: {_clip(self.choice)}"]
        if self.reason:
            parts.append(f"  because {_clip(self.reason, 400)}")
        if self.rejected:
            # Named, not counted: "3 alternatives rejected" is exactly the compaction artifact
            # this record exists to prevent.
            parts.append("  rejected: " + "; ".join(str(r) for r in self.rejected[:6]))
        if self.constraints:
            parts.append("  constraints: " + "; ".join(str(c) for c in self.constraints[:6]))
        return "\n".join(parts)


def render_context(
    *,
    handoff: Handoff | None = None,
    carryover: Carryover | None = None,
    decisions: list[Decision] | None = None,
) -> str:
    """Assemble the context block a fresh session starts from.

    Order is deliberate: decisions first (they are CONSTRAINTS — a reader who learns them last has
    already started planning around them), then the carryover facts, then the handoff's narrative
    and next action. The next action lands last because it is what the reader should act on
    immediately after finishing.

    Returns "" when there is nothing to say. A heading with nothing under it teaches a model that
    this section is noise.
    """
    blocks: list[str] = []
    real_decisions = [d for d in (decisions or []) if not d.empty]
    if real_decisions:
        rendered = "\n\n".join(d.render() for d in real_decisions[-6:])
        blocks.append("[SETTLED DECISIONS — do not re-litigate these]\n" + rendered)
    if carryover is not None and not carryover.empty:
        blocks.append("[CARRIED OVER]\n" + carryover.render())
    if handoff is not None and not handoff.empty:
        blocks.append("[HANDOFF FROM THE PREVIOUS ITERATION]\n" + handoff.render())
    return "\n\n".join(blocks)


# ── helpers ─────────────────────────────────────────────────────────────────


def _clip(text: str, limit: int = MAX_HANDOFF_FIELD) -> str:
    """Bound one field. Fifty iterations of unbounded handoffs would exhaust the context the
    handoff mechanism exists to protect."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving dedupe. Order matters because the tail is what survives the bound."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _dedupe_dicts(items: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    """Dedupe by one key, keeping the LAST occurrence.

    Last, not first: a file touched again later has a newer line span, and the older entry is the
    stale one. Keeping the first would make the carryover progressively less accurate the longer a
    run went on.
    """
    by_key: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = str(item.get(key, "") or "").strip()
        if not ident:
            continue
        by_key[ident] = item
    return list(by_key.values())


def _file_label(entry: dict[str, Any]) -> str:
    path = str(entry.get("path", "") or "?")
    lines = entry.get("lines")
    return f"{path}:{lines}" if lines else path
