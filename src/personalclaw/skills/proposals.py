"""Skill proposals — propose-only auto-skill evolution (skill-evolution-proposal-only).

Auto-skill synthesis used to write straight into the live ``auto/`` skill namespace.
After the documented malicious-skill-drift risk (OpenForge B2), the stance is
**propose, never install**: synthesized skills land in a review QUEUE, and a human
accepts (moves to live) or rejects them. Nothing the system authored autonomously
runs until a person approves it.

A proposal is a JSON record under ``~/.personalclaw/skills/.proposals/<id>.json``
carrying the synthesized skill (slug/description/triggers/procedure) + provenance +
a **fenced** excerpt of the source trace (so the reviewer sees what drove it without
that text being executable if it's ever re-fed to a model). Accept installs the
proposal — a ``kind="new"`` proposal via the auto-skill writer, a ``kind="refine"``
proposal as a SIDECAR OVERLAY on its named target skill (``overlays.py``; the base
``SKILL.md`` is never rewritten, so a locked skill stays verifiable and revert is a
one-file delete) — and reject drops the record.
The queue is the single sink for autonomous synthesis — there is no auto-install
path (by design).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.record_ids import record_path

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


def _path(proposal_id: object) -> Path:
    """The ONE expression turning a proposal id into a file in this store.

    ``proposal_id`` reaches here from ``/api/skills/proposals/{id}`` unvalidated, which
    gave a traversal a read and an ``unlink`` outside the home (#459). ``UnsafeRecordId``
    is not an ``OSError``, so ``reject()``'s ``except OSError`` reports the refusal
    instead of swallowing it into ``False``.
    """
    return record_path(_proposals_dir(), proposal_id, kind="proposal_id")


def _last_review_path() -> Path:
    """Where the ladder's most recent pass records itself.

    Deliberately a SIBLING of ``.proposals/`` rather than a file inside it:
    :func:`list_pending` globs ``*.json`` in that directory and coerces every hit
    to a :class:`SkillProposal`, so a marker living there would be a sentinel
    sharing a namespace with real records — silently skipped today by the
    ``except (OSError, ValueError, TypeError)``, and a latent mis-parse the first
    time that constructor grows a default.
    """
    from personalclaw.skills import loader as _loader

    return _loader.config_dir() / "skills" / ".ladder_last_review.json"


def record_review(*, verdict: str, elapsed_ms: float, session_key: str, detail: str = "") -> None:
    """Record that a skill-ladder pass RAN, and how it ended.

    This exists because an empty proposals list is two different facts wearing one
    face: "the ladder ran and had nothing to propose" and "the ladder never ran"
    are the same observation from outside (`G44`). The per-pass log line added by
    `G47` does not separate them on a shipped install either — the verdicts that
    mean the pass worked, ``no_action`` chief among them, log at INFO while the
    default ``log_level`` is WARNING, so the common success is invisible.

    Overwrites: one marker, always the latest pass. A history would be a second
    unbounded store to cap and prune, and the question this answers ("did it run
    at all, and what did it decide?") is answered by the most recent pass.

    Best-effort by construction. This is called from the ``finally`` of the pass,
    so a raising write here would replace the pass's real verdict with an
    unrelated failure — instrumentation must never be the thing that breaks the
    mechanism it observes.
    """
    rec = {
        "verdict": str(verdict),
        "elapsed_ms": int(elapsed_ms),
        "session_key": str(session_key or ""),
        "detail": str(detail or ""),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        p = _last_review_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(p, json.dumps(rec, indent=2))
    except OSError:
        logger.debug("skill-ladder review: could not record the last-run marker", exc_info=True)


def last_review() -> dict | None:
    """The most recent ladder pass, or ``None`` if no pass has ever run.

    ``None`` is the load-bearing value: it is what distinguishes a home where the
    ladder has never fired from one where it fired and proposed nothing.
    """
    try:
        data = json.loads(_last_review_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
    # Which stumble produced this refine proposal (``after_turn_review.STUMBLE_TRIGGERS``), or
    # "" for a model-proposed one. Carried rather than inferred because it is what the review
    # surface answers "why am I being asked this?" with, and it rides through accept into the
    # overlay record so the applied refinement keeps saying where it came from.
    trigger: str = ""
    source_excerpt: str = ""  # FENCED excerpt of the driving trace (review only)
    status: str = "pending"  # pending | accepted | rejected

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        """The compact view for the inbox list (no full procedure body)."""
        return {
            "id": self.id,
            "slug": self.slug,
            "description": self.description,
            "triggers": self.triggers,
            "kind": self.kind,
            "refine_target": self.refine_target,
            "trigger": self.trigger,
            "session_key": self.session_key,
            "created_at": self.created_at,
            "status": self.status,
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
    trigger: str = "",
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

            fenced = fence_untrusted(
                source_excerpt[:_SOURCE_EXCERPT_MAX], source="skill-synthesis-trace"
            )
        except Exception:
            fenced = ""  # never let fencing failure block the proposal
    pid = _make_id(slug, session_key, created_at)
    prop = SkillProposal(
        id=pid,
        slug=slug,
        description=description,
        triggers=triggers,
        procedure_md=procedure_md,
        session_key=session_key,
        created_at=created_at,
        kind=kind,
        refine_target=refine_target,
        trigger=trigger,
        source_excerpt=fenced,
    )
    try:
        atomic_write(d / f"{pid}.json", json.dumps(prop.to_dict(), indent=2))
    except OSError:
        logger.debug("skill proposal write failed", exc_info=True)
        return None
    logger.info("Queued skill proposal %s (session %s)", pid, session_key)
    _surface_in_inbox(prop)
    return prop


def _surface_in_inbox(prop: SkillProposal) -> None:
    """Raise the proposal as a durable inbox item (plan 42 S4).

    A proposal is a standing request: it waits until the user decides. Before this it lived
    only in the skills page's approval tab, so a proposal synthesized while the user was
    away was invisible unless they went looking. Deduped by proposal id so a re-enqueue of
    the same synthesis can't stack rows.

    Best-effort: if the inbox is unreachable the proposal still exists in its own store and
    the skills page still shows it — surfacing must never be able to fail the enqueue.
    """
    try:
        from personalclaw.inbox import ItemKind, emit_attention_item

        state = None
        try:
            # The same process-wide accessor the inbox service uses; None when headless.
            from personalclaw.inbox_providers.native_source import get_dashboard_state

            state = get_dashboard_state()
        except Exception:
            logger.debug("proposal inbox surface: no dashboard state", exc_info=True)

        label = "Refine a skill" if prop.kind == "refine" else "New skill proposed"
        emit_attention_item(
            state,
            source="skills",
            kind="proposal",
            item_kind=ItemKind.PROPOSAL.value,
            title=label,
            body=f"{prop.slug} — {prop.description}",
            refs={"skill_proposal": prop.id, "session": prop.session_key},
            dedup_key=f"skill_proposal:{prop.id}",
        )
    except Exception:
        logger.debug("proposal inbox surface failed", exc_info=True)


def _inbox_store_for_write() -> Any:
    """The inbox store a writer in this module must use, or ``None``.

    The RUNNING service's store when one is up, else a fresh file-backed `InboxStore` (headless: a
    CLI accept, a test, a background pass with no gateway — there the file IS the truth).

    🔴 This exists because both writers here constructed `InboxStore()` unconditionally, against
    `inbox.live_store`'s own warning that a writer doing so "writes a row the API cannot see … and
    that the service's next save silently overwrites". Measured on a live instance: three orphan
    rows, each referencing a proposal already accepted, still open and un-clearable, because the
    resolve wrote to a detached copy the service then overwrote (#336).

    The right accessor was already in this file — `_surface_in_inbox`, the WRITE path, goes through
    `get_dashboard_state()` + `emit_attention_item`. Only the RESOLVE path was left behind, which is
    the one-sided shape: whoever fixed the writer did not fix the reader of the same rows.
    """
    from personalclaw.inbox import InboxStore, live_store

    state = None
    try:
        from personalclaw.inbox_providers.native_source import get_dashboard_state

        state = get_dashboard_state()
    except Exception:  # noqa: BLE001 — headless is normal, not an error
        logger.debug("proposal inbox write: no dashboard state", exc_info=True)
    live = live_store(state) if state is not None else None
    if live is not None:
        return live
    store = InboxStore()
    store.load()
    return store


def _resolve_inbox_item(pid: str, status: str) -> None:
    """Move the inbox item for *pid* to a terminal status once the user decides.

    Without this the row would sit unresolved forever after the user accepted or rejected
    the proposal on either surface — the inbox would keep claiming attention for work
    already done, which is precisely the "second attention store" problem this plan exists
    to end.

    Accepts a transition FROM a terminal status, because ``accept()`` runs after
    ``reject()`` has already marked the item dismissed and needs to correct it to handled.
    Never moves an item backwards into an open state, which would resurrect it.
    """
    open_or_resolved = ("pending", "seen", "dismissed", "handled")
    try:
        store = _inbox_store_for_write()
        changed = False
        for item in store.items.values():
            if item.refs.get("skill_proposal") == pid and item.status in open_or_resolved:
                if item.status != status:
                    item.status = status
                    changed = True
        if changed:
            store.save()
    except Exception:
        logger.debug("proposal inbox resolve failed", exc_info=True)


def _load(pid: str) -> SkillProposal | None:
    try:
        data = json.loads(_path(pid).read_text(encoding="utf-8"))
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
    backfill_inbox_items(out)
    return out


def backfill_inbox_items(pending: "list[SkillProposal] | None" = None) -> int:
    """Give every pending proposal an inbox item if it doesn't have one. Returns how many.

    T4.2, as an **idempotent backfill keyed on data inspection** rather than a
    `lifecycle/migrations/m_*.py` file (see the plan's *Change discipline*). Proposals
    enqueued before S4 have no item; without this they'd stay invisible in the inbox forever
    while `enqueue` only covers new ones.

    Idempotent **by pid**: `emit_attention_item`'s dedup key is the proposal id, so an
    existing OPEN item is reused and no second notification fires. A proposal the user
    already resolved is deliberately skipped — re-creating an item for it would resurrect a
    decision they'd made.

    Runs from `list_pending()` (the read path both the skills page and the API use), so the
    first look at either surface after an upgrade is already correct.
    """
    props = pending if pending is not None else []
    if pending is None:
        d = _proposals_dir()
        if not d.is_dir():
            return 0
        for p in d.glob("*.json"):
            try:
                rec = SkillProposal(**json.loads(p.read_text(encoding="utf-8")))
                if rec.status == "pending":
                    props.append(rec)
            except (OSError, ValueError, TypeError):
                continue
    if not props:
        return 0

    try:
        # The SAME store the resolve path writes: reading a detached copy would miss every row the
        # running service holds in memory, and the backfill would re-surface a duplicate for a
        # proposal that already has a live row.
        store = _inbox_store_for_write()
        # Any item referencing the pid counts as "has one", INCLUDING a resolved one —
        # otherwise every read would re-raise items for proposals the user has answered.
        seen = {
            i.refs.get("skill_proposal")
            for i in store.items.values()
            if i.refs.get("skill_proposal")
        }
    except Exception:
        logger.debug("proposal backfill: inbox read failed", exc_info=True)
        return 0

    made = 0
    for prop in props:
        if prop.id in seen:
            continue
        _surface_in_inbox(prop)
        made += 1
    if made:
        logger.info("surfaced %d pre-existing skill proposal(s) in the inbox", made)
    return made


def get(pid: str) -> SkillProposal | None:
    return _load(pid)


def reject(pid: str) -> bool:
    """Drop a proposal (never installed). Returns True if it existed.

    ``accept()`` also calls this to clear the queue entry, so the inbox resolution here is
    deliberately DISMISSED and `accept()` overwrites it with HANDLED afterwards — the
    distinction matters because "I said no" and "I installed it" are different answers, and
    the item's terminal status is the only record of which one the user gave.
    """
    try:
        _path(pid).unlink()
        logger.info("Rejected skill proposal %s", pid)
        _resolve_inbox_item(pid, "dismissed")
        return True
    except OSError:
        return False


class AcceptError(Exception):
    """Raised when a proposal can't be accepted (invalid / write failed)."""


@dataclass(frozen=True)
class AcceptResult:
    """What an accept DID — the skill it touched and, for a refine, which version it wrote.

    ``accept`` used to return the bare name, so the one question a refinement raises — "which
    version of this skill did I just approve?" — had no answer anywhere on the accept path.
    ``version`` is the 1-based overlay refinement version (see ``overlays.Refinement``), and
    ``0`` for a ``kind="new"`` accept, which creates a skill rather than versioning one.
    """

    name: str
    version: int = 0


def accept(
    pid: str, *, description: str | None = None, procedure_md: str | None = None
) -> AcceptResult:
    """Accept a pending proposal and clear it from the queue.

    A ``kind="refine"`` proposal that names a resolvable ``refine_target`` applies as a SIDECAR
    OVERLAY on that skill (``skills/overlays.py``) — a single file merged onto the base body at
    load time, never a rewrite of ``SKILL.md``. This is WF2LEA-6's clean break over the old
    in-body append: the base bytes (and a marketplace skill's ``.pclaw-lock.json`` hashes) stay
    intact, and reverting the refinement is the deletion of exactly one file. Everything else —
    ``kind="new"``, or a refine whose target no longer exists — CREATES a new ``auto/`` skill.

    Optional ``description``/``procedure_md`` apply reviewer edits. Returns an
    :class:`AcceptResult` naming the written/updated skill AND, for a refine, the refinement
    version it wrote. Raises ``AcceptError`` on failure."""
    prop = _load(pid)
    if prop is None:
        raise AcceptError(f"no proposal {pid!r}")
    from personalclaw.skills import overlays
    from personalclaw.skills.loader import (
        AUTO_SKILL_NAMESPACE,
        AutoSkillProvenance,
        SkillsLoader,
    )

    loader = SkillsLoader(install_builtins=False)
    eff_description = description or prop.description
    eff_procedure = procedure_md or prop.procedure_md

    # ── overlay an EXISTING skill, or mint a new one ──
    #
    # ONE decision, asked once: is there already a skill this proposal is about?
    #
    #   * `kind="refine"` names its target explicitly. This is #303, fixed earlier: accept() used to
    #     route EVERY proposal through `create_auto_skill(slug)`, so a refine of an existing skill
    #     returned falsy and 409'd forever.
    # * `kind="new"` for a slug that ALREADY EXISTS is the same situation without the label, and it
    #     was still 409ing (#323). The generator files `kind="new"` by default and its only
    # duplicate guard is `find_similar(description)` — a similarity check on the DESCRIPTION, not
    #     on whether the slug exists — so a differently-worded proposal for an installed skill sails
    #     through and then cannot be accepted, ever. Measured on a live instance: 26 of 30 pending
    #     proposals targeted an already-installed slug, 20 of them the same one.
    #
    # A 21st proposal for `loop-worker` IS a refinement of `loop-worker`, whatever the row is
    # labelled, so it overlays. That is also the recovery path for a queue the bug already filled:
    # no generator fix can reach a proposal already on disk.
    target = ""
    if prop.kind == "refine" and prop.refine_target:
        if loader.load_skill(prop.refine_target) is not None:
            target = prop.refine_target
        else:
            # Target vanished (deleted since proposal) — create instead of 500'ing, so the Accept
            # button still resolves the proposal.
            logger.info(
                "refine target %r for proposal %s no longer exists; creating new skill",
                prop.refine_target,
                pid,
            )
    if not target:
        implied = f"{AUTO_SKILL_NAMESPACE}/{prop.slug}"
        if loader.load_skill(implied) is not None:
            logger.info(
                "proposal %s is labelled %r but %s already exists; overlaying it",
                pid,
                prop.kind,
                implied,
            )
            target = implied

    if target:
        try:
            version = overlays.apply_overlay(
                target,
                description=eff_description,
                procedure_md=eff_procedure,
                created_at=prop.created_at,
                trigger=prop.trigger,
            )
        except (OSError, ValueError) as exc:
            raise AcceptError(f"could not overlay skill {target!r}: {exc}") from exc
        reject(pid)  # clear the now-accepted proposal
        _resolve_inbox_item(pid, "handled")
        logger.info("Accepted proposal %s → overlaid %s v%d", pid, target, version)
        return AcceptResult(target, version)

    # ── nothing to refine: create a fresh auto/ skill ──
    prov = AutoSkillProvenance(session_key=prop.session_key, created_at=prop.created_at)
    created = loader.create_auto_skill(
        prop.slug,
        description=eff_description,
        triggers=prop.triggers,
        procedure_md=eff_procedure,
        provenance=prov,
    )
    if not created:
        raise AcceptError(f"could not write skill {prop.slug!r} (invalid, oversized, or exists)")
    reject(pid)  # clear the now-accepted proposal
    # reject() marked the item DISMISSED; correct it to HANDLED. Order matters: doing this
    # before reject() would let reject() overwrite it back to dismissed.
    _resolve_inbox_item(pid, "handled")
    logger.info("Accepted skill proposal %s → %s", pid, created)
    return AcceptResult(created)
