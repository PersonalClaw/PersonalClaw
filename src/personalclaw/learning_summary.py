"""The learning summary block — new / refined / pending counts + names (LV-3).

What this is: a bounded, read-only gather over the learning artifacts that ALREADY
have live writers, rendered as one small block. It answers "what has this thing
learned lately" in four groups:

* **new** — skills whose provenance ``created_at`` falls inside the window.
* **refined** — skills whose provenance ``refined_at`` falls inside the window, plus
  skills carrying a sidecar overlay refinement stamped inside it.
* **pending** — skill proposals still awaiting a human decision (unwindowed by
  design: a proposal from five weeks ago is *more* interesting, not less).
* **facts** — preference facets and lessons touched inside the window.

Where it renders: LV-3's task row wanted this registered with plan 42's digest
builder. **That builder does not exist** — there is no digest-section registry in
the tree — so the same block renders on the skills page header instead, which the
task row and the atom's ``done_when`` both name as the sanctioned fallback. When a
digest builder does arrive, it consumes THIS function; the block is not reimplemented
there. One owner, one mechanism.

Two deliberate properties:

* **Propose-don't-write.** Every read here is a snapshot. Nothing in this module
  writes to a skill, a proposal, a facet or a lesson — so opening the panel cannot
  change what it reports.
* **Counts are true, names are a sample.** ``count`` is the full group size;
  ``names`` is capped at :data:`_MAX_NAMES`. A UI that showed ``len(names)`` as the
  count would under-report as soon as a group got busy, so the two are separate
  fields and the count is never derived from the truncated list.

LEARNING-VISIBILITY's LV-4 identity report composes the long-horizon view over these
same seams; it reads this gather rather than re-deriving it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

#: Names carried per group. The count stays exact; this only bounds the sample the
#: block renders, so a home with 200 facets does not ship 200 strings on a page load.
_MAX_NAMES = 8

#: Fact text is user prose and can be a paragraph. Truncated for the chip label only —
#: the full text stays readable on its own management surface (Memory → Studio).
_MAX_NAME_LEN = 80

#: Window bounds. 7 = the weekly digest cadence the plan's S2(c) names.
DEFAULT_WINDOW_DAYS = 7
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 90


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse(ts: str) -> datetime | None:
    """ISO-8601 → aware datetime, or None. Naive input is read as UTC.

    Naive-as-UTC matters: ``AutoSkillProvenance.now_iso`` and ``SkillUsageStore`` both
    write aware stamps, but a hand-edited SKILL.md frontmatter can carry a bare date,
    and comparing that to an aware ``now`` raises rather than excluding it.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _within(ts: str, cutoff: datetime) -> bool:
    dt = _parse(ts)
    return dt is not None and dt >= cutoff


def _clip(text: str) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= _MAX_NAME_LEN else t[: _MAX_NAME_LEN - 1].rstrip() + "…"


@dataclass(frozen=True)
class SummaryGroup:
    """One group of the block: an exact ``count`` plus a bounded ``names`` sample."""

    count: int = 0
    names: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, names: list[str]) -> "SummaryGroup":
        """Build from the FULL list — count from the whole, names from the head."""
        return cls(count=len(names), names=names[:_MAX_NAMES])

    def to_payload(self) -> dict[str, Any]:
        return {"count": self.count, "names": list(self.names)}


@dataclass(frozen=True)
class LearningSummary:
    """The whole block. ``total`` is what makes the block worth rendering at all."""

    window_days: int = DEFAULT_WINDOW_DAYS
    new_skills: SummaryGroup = field(default_factory=SummaryGroup)
    refined_skills: SummaryGroup = field(default_factory=SummaryGroup)
    pending_proposals: SummaryGroup = field(default_factory=SummaryGroup)
    facts: SummaryGroup = field(default_factory=SummaryGroup)

    @property
    def total(self) -> int:
        return (
            self.new_skills.count
            + self.refined_skills.count
            + self.pending_proposals.count
            + self.facts.count
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "total": self.total,
            "new_skills": self.new_skills.to_payload(),
            "refined_skills": self.refined_skills.to_payload(),
            "pending_proposals": self.pending_proposals.to_payload(),
            "facts": self.facts.to_payload(),
        }


def _gather_skills(cutoff: datetime) -> tuple[list[str], list[str]]:
    """``(new_names, refined_names)`` from skill provenance + sidecar overlays.

    A skill created inside the window counts as NEW only — the refine seam
    (``history.py``) stamps ``refined_at`` alongside ``created_at``, so a
    just-synthesized-then-refined skill would otherwise be double-reported as one new
    thing and one refinement.
    """
    from personalclaw.skills import overlays
    from personalclaw.skills.loader import SkillsLoader

    try:
        rows = SkillsLoader(install_builtins=False).list_skills(with_provenance=True)
    except Exception:
        logger.debug("learning summary: skill listing failed", exc_info=True)
        return [], []

    new_names: list[str] = []
    refined_names: list[str] = []
    for row in rows:
        name = str(row.get("key") or row.get("name") or "")
        if not name:
            continue
        if _within(str(row.get("created_at") or ""), cutoff):
            new_names.append(name)
            continue
        if _within(str(row.get("refined_at") or ""), cutoff):
            refined_names.append(name)
            continue
        # Accepted `kind="refine"` proposals land as a sidecar overlay and never rewrite
        # SKILL.md, so frontmatter alone cannot see them. The overlay refinement carries
        # the PROPOSAL's created_at (`proposals.accept`), which is the honest stamp for
        # "when this refinement was authored".
        try:
            overlay = overlays.load_overlay(name)
        except Exception:
            logger.debug("learning summary: overlay read failed for %s", name, exc_info=True)
            continue
        if not isinstance(overlay, dict):
            continue
        refinements = overlay.get("refinements")
        if not isinstance(refinements, list):
            continue
        if any(
            isinstance(r, dict) and _within(str(r.get("created_at") or ""), cutoff)
            for r in refinements
        ):
            refined_names.append(name)
    return new_names, refined_names


def _gather_pending() -> list[str]:
    """Pending skill-proposal labels. A refine names its target, a new names its slug."""
    from personalclaw.skills import proposals

    try:
        pending = proposals.list_pending()
    except Exception:
        logger.debug("learning summary: proposal listing failed", exc_info=True)
        return []
    out: list[str] = []
    for prop in pending:
        kind = str(getattr(prop, "kind", "") or "new")
        target = str(getattr(prop, "refine_target", "") or "")
        slug = str(getattr(prop, "slug", "") or "")
        out.append(f"{target or slug} (refine)" if kind == "refine" and target else slug)
    return [n for n in out if n]


def _gather_facts(vs: Any, cutoff: datetime, now: datetime) -> list[str]:
    """Preference facets + lessons touched inside the window, as chip labels.

    Facets carry their live state (Active/Provisional/…) from the SAME derivation the
    ambient PROFILE block uses, so the block cannot claim a facet is shaping replies
    when the decay says it is fading.
    """
    if vs is None:
        return []
    out: list[str] = []
    try:
        from personalclaw.preference_facets import facet_state, load_facets

        for _key, facet in load_facets(vs):
            if getattr(facet, "forgotten", False):
                continue
            if _within(str(getattr(facet, "updated_at", "") or ""), cutoff):
                out.append(f"{_clip(facet.text)} ({facet_state(facet, now=now)})")
    except Exception:
        logger.debug("learning summary: facet read failed", exc_info=True)
    try:
        # `over_vector_store`, NOT `service_for`. `service_for(provider)` discovers the
        # store on `provider.vector_store`, so handing it a VectorMemoryStore yields
        # `_vs = None` and `get_lessons()` returns [] — every lesson would read as absent
        # and the group would render an honest-looking zero forever. Measured, not assumed.
        from personalclaw.memory_service import MemoryService

        for row in MemoryService.over_vector_store(vs).get_lessons():
            if not _within(str(row.get("updated_at") or ""), cutoff):
                continue
            try:
                rule = json.loads(row.get("value_json") or '""')
            except (json.JSONDecodeError, TypeError):
                continue
            text = (
                rule
                if isinstance(rule, str)
                else str(rule.get("rule", "") if isinstance(rule, dict) else "")
            )
            if text:
                out.append(_clip(text))
    except Exception:
        logger.debug("learning summary: lesson read failed", exc_info=True)
    return out


def compose_learning_summary(
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    vs: Any = None,
    now: datetime | None = None,
) -> LearningSummary:
    """Gather the learning summary block. Read-only; never raises.

    ``vs`` is a :class:`~personalclaw.vector_memory.VectorMemoryStore` (facets +
    lessons live there). Omitting it yields the skill/proposal groups only, which is
    the honest degrade for an API-only home with no memory store attached — the fact
    group reads as empty because there is nothing to read, not because a key is
    missing.
    """
    at = now or _now()
    days = max(MIN_WINDOW_DAYS, min(int(window_days), MAX_WINDOW_DAYS))
    cutoff = at - timedelta(days=days)
    new_names, refined_names = _gather_skills(cutoff)
    return LearningSummary(
        window_days=days,
        new_skills=SummaryGroup.of(sorted(new_names)),
        refined_skills=SummaryGroup.of(sorted(refined_names)),
        pending_proposals=SummaryGroup.of(_gather_pending()),
        facts=SummaryGroup.of(_gather_facts(vs, cutoff, at)),
    )
