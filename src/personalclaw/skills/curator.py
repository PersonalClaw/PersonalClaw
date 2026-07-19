"""Skill-library curator — lifecycle GC of the ``auto/`` namespace (#27).

The **grooming** counterpart to skill *creation* (learn-after-turn-review, #22).
PClaw auto-creates skills under ``auto/`` but never grooms them, so the namespace
accrues narrow, stale, overlapping skills that bloat the index and dilute surfacing.

This curator runs two passes (only the first ships here):

1. **Aging (pure, no LLM)** — each ``auto/`` skill transitions
   ``active → stale (≥30d unused) → archived (≥90d unused)`` by its
   ``last_used_at`` from the #25 usage counter, reactivating to ``active`` the
   moment it's used again. Archived skills are **filtered from surfacing** but
   **kept on disk** (a ``status: archived`` frontmatter flag) — archive is the
   maximum destructive action, always reversible via :func:`restore`.

2. **LLM umbrella-consolidation** *(deferred follow-up, like #22's skill ladder)* —
   cluster overlapping ``auto/`` skills and merge them into broader "umbrella"
   skills, demoting detail to support files, rewriting references. Noted, not silently
   dropped. The aging pass — the high-value, fully-reversible half — lands now.

**Invariants (non-negotiable):** only ``auto/`` skills are touched (bundled +
hand-authored never); never deletes (archive only); pinned skills (``pinned: true``)
bypass aging entirely; aging is idempotent and reversible.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from personalclaw.skills.loader import AUTO_SKILL_NAMESPACE, SkillsLoader

logger = logging.getLogger(__name__)

# Inactivity thresholds (days) over last_used_at.
STALE_AFTER_DAYS = 30
ARCHIVE_AFTER_DAYS = 90

# Skill lifecycle states (stored in the SKILL.md ``status`` frontmatter; absence
# == active, so untouched skills need no rewrite).
STATE_ACTIVE = "active"
STATE_STALE = "stale"
STATE_ARCHIVED = "archived"


@dataclass
class CuratorReport:
    """What an aging run did — surfaced to the user (Skills UI / CLI / log)."""

    scanned: int = 0
    to_stale: list[str] = field(default_factory=list)
    to_archived: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    skipped_pinned: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def changed(self) -> int:
        return len(self.to_stale) + len(self.to_archived) + len(self.reactivated)

    def summary(self) -> str:
        if not self.changed:
            return f"Curator: scanned {self.scanned} auto skill(s), no changes."
        bits = []
        if self.to_archived:
            bits.append(f"archived {len(self.to_archived)}")
        if self.to_stale:
            bits.append(f"marked {len(self.to_stale)} stale")
        if self.reactivated:
            bits.append(f"reactivated {len(self.reactivated)}")
        prefix = "Curator (dry-run): would have " if self.dry_run else "Curator: "
        return prefix + ", ".join(bits) + f" (of {self.scanned} scanned)."


def _parse_iso(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _age_days(last_used_at: str, created_at: str, now: datetime) -> float | None:
    """Days since the skill was last useful. Falls back to created_at when never
    used (so a freshly-created-but-never-surfaced skill still ages out). None when
    no timestamp is parseable (→ never aged, conservative)."""
    ref = _parse_iso(last_used_at) or _parse_iso(created_at)
    if ref is None:
        return None
    return max(0.0, (now - ref).total_seconds() / 86400.0)


def _target_state(age_days: float | None) -> str:
    if age_days is None:
        return STATE_ACTIVE
    if age_days >= ARCHIVE_AFTER_DAYS:
        return STATE_ARCHIVED
    if age_days >= STALE_AFTER_DAYS:
        return STATE_STALE
    return STATE_ACTIVE


def _set_status_frontmatter(content: str, status: str) -> str:
    """Set/replace the ``status:`` line in a SKILL.md's frontmatter.

    ``active`` removes the line (the default needs no flag). Other states upsert it.
    Leaves a file without frontmatter untouched (returns it unchanged).
    """
    m = re.match(r"^(---\n)(.*?)(\n---\n?)(.*)$", content, re.DOTALL)
    if not m:
        return content
    head, body, close, rest = m.group(1), m.group(2), m.group(3), m.group(4)
    lines = [ln for ln in body.split("\n") if not ln.strip().startswith("status:")]
    if status != STATE_ACTIVE:
        lines.append(f"status: {status}")
    return head + "\n".join(lines) + close + rest


def run_aging(
    loader: SkillsLoader | None = None,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CuratorReport:
    """Pure time-based lifecycle pass over the ``auto/`` namespace.

    Reads each auto skill's ``last_used_at`` (#25 usage store) + ``created_at``
    (frontmatter), computes the target state, and rewrites only the ``status``
    frontmatter line of skills whose state changed. Pinned skills are skipped.
    Idempotent, reversible, no LLM.
    """
    loader = loader or SkillsLoader()
    now = now or datetime.now(tz=timezone.utc)
    report = CuratorReport(dry_run=dry_run)

    try:
        from personalclaw.skills.usage import SkillUsageStore

        usage = SkillUsageStore().all_usage()
    except Exception:
        usage = {}

    # list_skills() merges the loader's own dir with the read-only discovery roots
    # (builtins, project skills). The curator only ages skills in its OWN managed
    # library — a builtin or a skill discovered from an external root is not ours to
    # rewrite — so restrict to auto/ skills physically under the loader's dir.
    managed_root = str(getattr(loader, "_dir", "")).rstrip("/")
    prefix = f"{AUTO_SKILL_NAMESPACE}/"
    for s in loader.list_skills():
        name = s["key"]
        if not name.startswith(prefix):
            continue  # invariant: only auto/ skills
        if managed_root and not str(s.get("dir", "")).startswith(managed_root):
            continue  # discovered from a read-only external root → not the curator's
        report.scanned += 1

        content = loader.load_skill(name)
        if content is None:
            continue
        meta = _frontmatter(content)
        if meta.get("pinned", "").lower() == "true":
            report.skipped_pinned.append(name)
            continue

        cur = (meta.get("status", "") or STATE_ACTIVE).lower()
        u = usage.get(name)
        last_used = u.last_used_at if u else ""
        target = _target_state(_age_days(last_used, meta.get("created_at", ""), now))
        if target == cur:
            continue

        # Classify the transition for the report.
        if target == STATE_ARCHIVED:
            report.to_archived.append(name)
        elif target == STATE_STALE:
            report.to_stale.append(name)
        elif target == STATE_ACTIVE:
            report.reactivated.append(name)

        if not dry_run:
            new_content = _set_status_frontmatter(content, target)
            if new_content != content:
                loader.update_skill(name, new_content)

    if report.changed:
        logger.info(report.summary())
    return report


def restore(loader: SkillsLoader, name: str) -> bool:
    """Reactivate an archived/stale auto skill (status → active). Reversal of aging."""
    if not name.startswith(f"{AUTO_SKILL_NAMESPACE}/"):
        return False
    content = loader.load_skill(name)
    if content is None:
        return False
    return loader.update_skill(name, _set_status_frontmatter(content, STATE_ACTIVE))


def is_archived(meta: dict) -> bool:
    """True if a skill's parsed frontmatter marks it archived (→ skip surfacing)."""
    return (meta.get("status", "") or "").lower() == STATE_ARCHIVED


def _frontmatter(content: str) -> dict[str, str]:
    """Parse simple ``key: value`` YAML frontmatter (mirrors loader._parse_frontmatter)."""
    if not content.startswith("---"):
        return {}
    m = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out
