"""Skill proposals — propose-only auto-skill evolution (skill-evolution-proposal-only).

Auto-skill synthesis used to write straight into the live ``auto/`` skill namespace.
After the documented malicious-skill-drift risk (OpenForge B2), the stance is
**propose, never install**: synthesized skills land in a review QUEUE, and a human
accepts (moves to live) or rejects them. Nothing the system authored autonomously
runs until a person approves it.

A proposal is a JSON record under ``~/.personalclaw/skills/.proposals/<id>.json``
carrying the synthesized skill (slug/description/triggers/procedure) + provenance +
a **fenced** excerpt of the source trace (so the reviewer sees what drove it without
that text being executable if it's ever re-fed to a model). Accept writes the skill
via the normal auto-skill writer; reject drops the record. The queue is the single
sink for autonomous synthesis — there is no auto-install path (by design).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

_PROPOSALS_DIRNAME = ".proposals"
_SOURCE_EXCERPT_MAX = 4_000
# Per-source cap so a chatty source can't flood the queue (mirrors evolution.py).
_MAX_PENDING = 100


def _proposals_dir() -> Path:
    # Resolve config_dir dynamically (via the loader module) so a test that
    # repoints config_dir is honored — a module-level `from ... import config_dir`
    # would bind the original and leak writes into the real home dir.
    from personalclaw.skills import loader as _loader

    return _loader.config_dir() / "skills" / _PROPOSALS_DIRNAME


@dataclass
class SkillProposal:
    """One pending, human-reviewable auto-skill."""

    id: str
    slug: str
    description: str
    triggers: str
    procedure_md: str
    session_key: str
    created_at: str
    kind: str = "new"  # "new" | "refine"
    refine_target: str = ""  # for kind="refine", the existing skill name
    source_excerpt: str = ""  # FENCED excerpt of the driving trace (review only)
    status: str = "pending"  # pending | accepted | rejected

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        """The compact view for the inbox list (no full procedure body)."""
        return {
            "id": self.id, "slug": self.slug, "description": self.description,
            "triggers": self.triggers, "kind": self.kind,
            "refine_target": self.refine_target, "session_key": self.session_key,
            "created_at": self.created_at, "status": self.status,
            "procedure_preview": self.procedure_md[:280],
        }


def _make_id(slug: str, session_key: str, created_at: str) -> str:
    h = hashlib.sha1(f"{slug}|{session_key}|{created_at}".encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{h}"


def enqueue(
    *,
    slug: str,
    description: str,
    triggers: str,
    procedure_md: str,
    session_key: str,
    created_at: str,
    kind: str = "new",
    refine_target: str = "",
    source_excerpt: str = "",
) -> SkillProposal | None:
    """Add a synthesized skill to the review queue. Returns the proposal, or None
    if the queue is full or inputs are empty. The source excerpt is FENCED so a
    poisoned trace can't direct any model that later renders it."""
    if not (slug and description and procedure_md):
        return None
    d = _proposals_dir()
    if d.is_dir() and len(list(d.glob("*.json"))) >= _MAX_PENDING:
        logger.info("skill-proposal queue full (%d); dropping %r", _MAX_PENDING, slug)
        return None
    fenced = ""
    if source_excerpt:
        try:
            from personalclaw.security import fence_untrusted

            fenced = fence_untrusted(source_excerpt[:_SOURCE_EXCERPT_MAX], source="skill-synthesis-trace")
        except Exception:
            fenced = ""  # never let fencing failure block the proposal
    pid = _make_id(slug, session_key, created_at)
    prop = SkillProposal(
        id=pid, slug=slug, description=description, triggers=triggers,
        procedure_md=procedure_md, session_key=session_key, created_at=created_at,
        kind=kind, refine_target=refine_target, source_excerpt=fenced,
    )
    try:
        atomic_write(d / f"{pid}.json", json.dumps(prop.to_dict(), indent=2))
    except OSError:
        logger.debug("skill proposal write failed", exc_info=True)
        return None
    logger.info("Queued skill proposal %s (session %s)", pid, session_key)
    return prop


def _load(pid: str) -> SkillProposal | None:
    try:
        data = json.loads((_proposals_dir() / f"{pid}.json").read_text(encoding="utf-8"))
        return SkillProposal(**data)
    except (OSError, ValueError, TypeError):
        return None


def list_pending() -> list[SkillProposal]:
    """All pending proposals, newest-first by created_at."""
    d = _proposals_dir()
    if not d.is_dir():
        return []
    out: list[SkillProposal] = []
    for p in d.glob("*.json"):
        try:
            rec = SkillProposal(**json.loads(p.read_text(encoding="utf-8")))
            if rec.status == "pending":
                out.append(rec)
        except (OSError, ValueError, TypeError):
            continue
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


def get(pid: str) -> SkillProposal | None:
    return _load(pid)


def reject(pid: str) -> bool:
    """Drop a proposal (never installed). Returns True if it existed."""
    try:
        (_proposals_dir() / f"{pid}.json").unlink()
        logger.info("Rejected skill proposal %s", pid)
        return True
    except OSError:
        return False


class AcceptError(Exception):
    """Raised when a proposal can't be accepted (invalid / write failed)."""


def accept(pid: str, *, description: str | None = None, procedure_md: str | None = None) -> str:
    """Install a pending proposal into the live ``auto/`` skill namespace and clear
    it from the queue. Optional ``description``/``procedure_md`` apply reviewer edits.
    Returns the written skill name. Raises ``AcceptError`` on failure."""
    prop = _load(pid)
    if prop is None:
        raise AcceptError(f"no proposal {pid!r}")
    from personalclaw.skills.loader import AutoSkillProvenance, SkillsLoader

    loader = SkillsLoader(install_builtins=False)
    prov = AutoSkillProvenance(session_key=prop.session_key, created_at=prop.created_at)
    name = loader.create_auto_skill(
        prop.slug,
        description=(description or prop.description),
        triggers=prop.triggers,
        procedure_md=(procedure_md or prop.procedure_md),
        provenance=prov,
    )
    if not name:
        raise AcceptError(f"could not write skill {prop.slug!r} (invalid, oversized, or exists)")
    reject(pid)  # clear the now-accepted proposal
    logger.info("Accepted skill proposal %s → %s", pid, name)
    return name
