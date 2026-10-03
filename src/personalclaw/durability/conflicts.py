"""Conflict records + the review queue.

The rule, verbatim: sha-divergence on the same entity id with **both sides edited
since the common ancestor** is a *conflict*, not an LWW coin-flip. This module owns the
detection rule and the durable queue the conflict lands in; the propose-only LLM draft
lives in :mod:`durability.conflict_merge`, and the live store is written by nobody here.

Three properties this module exists to guarantee:

* **Both-sides-edited is the trigger.** A divergence where only ONE side moved since the
  common ancestor is that side's edit, which the reconcile applies (the peer's is taken in,
  this home's stays) — recording it would make every ordinary sync produce review noise. So a
  conflict needs three shas to disagree: ``ancestor != local != remote != ancestor``. The
  ancestor is the version this home and that peer last agreed on, or a later one this home
  published that the peer now holds (:mod:`durability.ancestors`). No ancestor on record (a
  record the two have never agreed on) is likewise NOT a conflict: with no common point there
  is nothing to say both sides moved *from*, and §4.2 item 1 hands those to the deterministic
  merge.
* **The local version stays authoritative.** A recorded conflict HOLDS its id: the caller
  (:func:`reconcile.reconcile_entry`) drops the remote row for that id before merging, so
  the local bytes are untouched until a human resolves. Nothing is lost — the remote
  version is inside the record, and the peer's shard is insert-only in the shared store.
* **A conflict is never a merge input.** Record ids are deterministic
  (``entry|id|ancestor|local|remote``), so re-detecting the same divergence next cycle
  dedups into the same row instead of piling up, and a resolved-then-recurring divergence
  is legible rather than duplicated.

**A delete is weighed by the same rule** (:func:`weigh_deletions`). It stands against every
version of the record the deleting home held before it (``ancestors.Deletion.held``): a copy
that still holds one of those is older than the delete, so a record deleted here stays deleted
when a peer's copy made before the delete reached it is pulled, and a delete that arrives is
applied to a record this home holds as the deleting home held it. Any other version is an edit
the delete never saw — made after it, or before it reached the home that deleted — and that is a
conflict for review, recorded with the delete as its side (``ConflictRecord.deleted``): neither
the edit comes back nor is it dropped until a person chooses.

Storage is one append-only JSONL under the sync root (``sync/conflicts.jsonl``) —
machine-local, alongside the pull cursor and the outbox, because a conflict is *this*
machine's unresolved decision and both versions durably persist in the shared store
regardless. It is deliberately NOT an inventory entry: an inventory entry under
``sync/`` would be exported into the very shards a pull rewrites mid-cycle, i.e. a
self-referential synced store. ``sync/`` is IGNORED by the home audit for that reason.

Routing (§4.2 item 3): the record carries the surface its entry's ``domain`` maps to —
memory-domain conflicts to the memory review surface, knowledge-domain to the knowledge
UI, everything else to the Durability queue. The boundary holds in the failure path too:
a conflict with no drafted proposal still carries its surface.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from personalclaw.atomic_write import atomic_write
from personalclaw.durability import inventory as inv
from personalclaw.durability.ancestors import Deletion
from personalclaw.durability.merge import _is_tombstone
from personalclaw.durability.shards import canonical_json

logger = logging.getLogger(__name__)

#: The queue's path under the home (the sync root's third machine-local file).
CONFLICTS_PATH = "sync/conflicts.jsonl"

#: The status a detected conflict lands in. Nothing is applied while a record holds it.
STATUS_NEEDS_REVIEW = "needs-review"

#: The status a REVIEWED record lands in (:mod:`durability.conflict_resolve`). The
#: record is kept — the queue is the audit trail of what needed a decision, and which
#: version the user chose is exactly the part worth keeping.
STATUS_RESOLVED = "resolved"

# ── review surfaces (§4.2 item 3) ────────────────────────────────────────────
SURFACE_MEMORY = "memory"
SURFACE_KNOWLEDGE = "knowledge"
SURFACE_DURABILITY = "durability"

#: ``ConflictRecord.deleted``: this machine deleted the record, and the other machine's version
#: is an edit the delete never saw.
DELETED_HERE = "here"
#: ``ConflictRecord.deleted``: the other machine deleted it, and this machine's version is an edit
#: that delete never saw.
DELETED_THERE = "there"

#: Merge strategies whose same-id collisions can be a conflict, or an edit made on one side.
#: ``append_dedup`` cannot: a stable event id means "the same append", so a re-import is a
#: no-op, never a divergence. The DB/tree strategies are not row-merged at all (see
#: :mod:`durability.merge`).
ID_KEYED_MERGES = frozenset({inv.MERGE_UNION_BY_ID, inv.MERGE_LWW})


def surface_for_domain(domain: str) -> str:
    """The review surface a conflict in ``domain`` routes to (§4.2 item 3)."""
    if domain == inv.DOMAIN_MEMORY:
        return SURFACE_MEMORY
    if domain == inv.DOMAIN_KNOWLEDGE:
        return SURFACE_KNOWLEDGE
    return SURFACE_DURABILITY


def row_id(row: dict) -> str:
    """The row's entity id, in either exporter row shape (both carry ``id`` at the top)."""
    return str(row.get("id", ""))


def row_sha(row: dict) -> str:
    """The content sha of a row — sha256 over its canonical JSON.

    Canonical (sorted, compact) so two machines that hold the same logical row compute the
    same sha: the whole divergence test is sha equality, so a key-order difference must not
    read as an edit.
    """
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def compared(entry: inv.StateEntry, row: dict) -> dict:
    """What of *row* two homes compare: the part a person makes (``StateEntry.compared``), or the
    whole row for a store that holds nothing of one home's.

    Every sha that says whether two homes agree on a row is taken over this — detection here, and
    the common ancestors a reconcile records — so they cannot disagree about what counts as an
    edit. An automation's run, switch or grant changes it in ONE home, and read as an edit it made
    every pull a conflict once both homes had run anything. A delete is compared as it is: it
    holds nothing of either home's.
    """
    return row if entry.compared is None or _is_tombstone(row) else entry.compared(row)


def deleted_row(entity_id: str, at: str) -> dict:
    """A delete, as a conflict records and shows it: the record's id and when it was deleted."""
    return {"id": entity_id, "deleted_at": at}


@dataclass
class ConflictRecord:
    """One unresolved divergence: both versions, all three shas, and where it is reviewed."""

    entry_id: str
    entity_id: str
    domain: str
    surface: str
    ancestor_sha: str
    local_sha: str
    remote_sha: str
    local_row: dict = field(default_factory=dict)
    remote_row: dict = field(default_factory=dict)
    detected_at: str = ""
    status: str = STATUS_NEEDS_REVIEW
    #: The LLM-drafted merged row — a PROPOSAL, never applied. ``None`` means
    #: "not drafted": either not attempted yet, or the model was unavailable.
    proposal: dict | None = None
    rationale: str = ""
    proposed_at: str = ""
    #: Why there is no proposal, when a draft was attempted and failed (fail-open evidence).
    proposal_error: str = ""
    #: Which version the user chose, once reviewed — one of
    #: :mod:`durability.conflict_resolve`'s ``CHOICE_*``. Empty while unreviewed.
    resolution: str = ""
    resolved_at: str = ""
    #: Which side of the conflict is a delete: :data:`DELETED_HERE`, :data:`DELETED_THERE`, or
    #: ``""`` for two edits.
    deleted: str = ""

    @property
    def id(self) -> str:
        """A deterministic id for this exact divergence — same three shas → same id, so a
        re-detection dedups instead of appending a second row."""
        seed = "|".join(
            [self.entry_id, self.entity_id, self.ancestor_sha, self.local_sha, self.remote_sha]
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "entity_id": self.entity_id,
            "domain": self.domain,
            "surface": self.surface,
            "ancestor_sha": self.ancestor_sha,
            "local_sha": self.local_sha,
            "remote_sha": self.remote_sha,
            "local_row": self.local_row,
            "remote_row": self.remote_row,
            "detected_at": self.detected_at,
            "status": self.status,
            "proposal": self.proposal,
            "rationale": self.rationale,
            "proposed_at": self.proposed_at,
            "proposal_error": self.proposal_error,
            "resolution": self.resolution,
            "resolved_at": self.resolved_at,
            "deleted": self.deleted,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConflictRecord:
        proposal = d.get("proposal")
        local_row = d.get("local_row")
        remote_row = d.get("remote_row")
        return cls(
            entry_id=str(d.get("entry_id", "")),
            entity_id=str(d.get("entity_id", "")),
            domain=str(d.get("domain", "")),
            surface=str(d.get("surface", "") or SURFACE_DURABILITY),
            ancestor_sha=str(d.get("ancestor_sha", "")),
            local_sha=str(d.get("local_sha", "")),
            remote_sha=str(d.get("remote_sha", "")),
            local_row=local_row if isinstance(local_row, dict) else {},
            remote_row=remote_row if isinstance(remote_row, dict) else {},
            detected_at=str(d.get("detected_at", "")),
            status=str(d.get("status", "") or STATUS_NEEDS_REVIEW),
            proposal=proposal if isinstance(proposal, dict) else None,
            rationale=str(d.get("rationale", "")),
            proposed_at=str(d.get("proposed_at", "")),
            proposal_error=str(d.get("proposal_error", "")),
            resolution=str(d.get("resolution", "")),
            resolved_at=str(d.get("resolved_at", "")),
            deleted=str(d.get("deleted", "")),
        )


def detect_conflicts(
    entry: inv.StateEntry,
    local: list[dict],
    remote: list[dict],
    ancestors: Mapping[str, str],
    *,
    now: str = "",
) -> list[ConflictRecord]:
    """Every both-sides-edited divergence between ``local`` and ``remote`` for ``entry``.

    ``ancestors`` maps ``entity id → the content sha this home and the peer last agreed on``
    (:meth:`ancestors.Ancestors.of`). An id is a conflict only when all three shas differ:

    ==================================  ===============================================
    ancestor == local, remote differs   edited there — the reconcile takes the peer's
    ancestor == remote, local differs   edited here — this home's stays
    local == remote                     converged — nothing to review
    no ancestor recorded                no common point — deterministic merge (§4.2.1)
    all three differ                    **CONFLICT** — a record, local held
    ==================================  ===============================================

    Rows are compared, and recorded, as :func:`compared` projects them: the part a person makes.
    That is also what the review shows and what a resolution writes, so a record never holds a
    difference that is not the conflict.

    Pure: no I/O, no clock (``now`` is passed), deterministic order (sorted by entity id),
    so a re-detection of unchanged state produces byte-identical records.
    """
    if entry.merge not in ID_KEYED_MERGES:
        return []
    local_by_id = {row_id(r): r for r in local if row_id(r)}
    remote_by_id = {row_id(r): r for r in remote if row_id(r)}
    out: list[ConflictRecord] = []
    for rid in sorted(set(local_by_id) & set(remote_by_id)):
        ancestor = str(ancestors.get(rid, "") or "")
        if not ancestor:
            continue  # no common ancestor on record → not provably a both-sides edit
        lrow, rrow = compared(entry, local_by_id[rid]), compared(entry, remote_by_id[rid])
        lsha, rsha = row_sha(lrow), row_sha(rrow)
        if lsha == rsha:
            continue  # converged
        if lsha == ancestor or rsha == ancestor:
            continue  # one side moved — a fast-forward, not a conflict
        out.append(
            ConflictRecord(
                entry_id=entry.id,
                entity_id=rid,
                domain=entry.domain,
                surface=surface_for_domain(entry.domain),
                ancestor_sha=ancestor,
                local_sha=lsha,
                remote_sha=rsha,
                local_row=lrow,
                remote_row=rrow,
                detected_at=now,
            )
        )
    return out


@dataclass
class Weighed:
    """What :func:`weigh_deletions` made of the deletes between this home's records and a peer's."""

    #: The peer's copies of records deleted here that hold a version the delete saw: never taken.
    declined: set[str] = field(default_factory=set)
    #: ``entity id → the peer's delete`` of a record this home holds as the delete saw it.
    applied: dict[str, dict] = field(default_factory=dict)
    #: The peer's deletes of records this home does not hold: nothing to do.
    moot: set[str] = field(default_factory=set)
    #: A delete and an edit it never saw, in either direction, for review.
    conflicts: list[ConflictRecord] = field(default_factory=list)


def held_by_the_delete(row: dict) -> set[str]:
    """The versions a peer's delete (*row*, a tombstone) says the deleting home held. A delete
    that names none saw no version of anything here."""
    held = row.get("held")
    return {s for s in held if isinstance(s, str) and s} if isinstance(held, list) else set()


def weigh_deletions(
    entry: inv.StateEntry,
    live: Mapping[str, dict],
    remote: list[dict],
    removed: Mapping[str, Deletion],
    *,
    now: str = "",
) -> Weighed:
    """Every delete between this home and a peer, weighed by the rule in the module docstring.

    ``live`` is this home's records by id; ``remote`` the peer's rows, its deletes among them;
    ``removed`` the records this home deleted, or took another machine's delete of, that it does
    not hold now (and read whole, so their absence says so). For a store a sync merges by id;
    any other merges no record by id and has no delete to weigh.

    ============================================  ===========================================
    the peer's copy of one deleted here, held     declined: it stays deleted here
    the peer's copy of one deleted here, edited   **CONFLICT** — deleted here, held
    the peer's delete of one here, as it saw it   applied: it is deleted here
    the peer's delete of one here, edited here    **CONFLICT** — deleted there, held
    the peer's delete of one not here             moot
    ============================================  ===========================================

    Pure: no I/O, no clock (``now`` is passed), records in entity-id order.
    """
    out = Weighed()
    if entry.merge not in ID_KEYED_MERGES:
        return out
    for row in sorted(remote, key=row_id):
        rid = row_id(row)
        if not rid:
            continue
        if _is_tombstone(row):
            here = live.get(rid)
            if here is None:
                out.moot.add(rid)
                continue
            mine = compared(entry, here)
            if row_sha(mine) in held_by_the_delete(row):
                out.applied[rid] = row
                continue
            gone = deleted_row(rid, str(row.get("deleted_at", "")))
            out.conflicts.append(_deletion_conflict(entry, rid, mine, gone, DELETED_THERE, now))
            continue
        mark = removed.get(rid)
        if mark is None or rid in live:
            continue
        theirs = compared(entry, row)
        if row_sha(theirs) in mark.held:
            out.declined.add(rid)
            continue
        gone = deleted_row(rid, mark.at)
        out.conflicts.append(_deletion_conflict(entry, rid, gone, theirs, DELETED_HERE, now))
    return out


def _deletion_conflict(
    entry: inv.StateEntry, rid: str, local: dict, remote: dict, deleted: str, now: str
) -> ConflictRecord:
    """A delete and an edit it never saw, as a record for review. The two never agreed on a
    version both still hold, so it has no ancestor."""
    return ConflictRecord(
        entry_id=entry.id,
        entity_id=rid,
        domain=entry.domain,
        surface=surface_for_domain(entry.domain),
        ancestor_sha="",
        local_sha=row_sha(local),
        remote_sha=row_sha(remote),
        local_row=local,
        remote_row=remote,
        detected_at=now,
        deleted=deleted,
    )


class ConflictQueue:
    """The durable review queue — append-only JSONL under the sync root.

    Deduped by :attr:`ConflictRecord.id`, so recording the same divergence twice is a
    no-op. Small by construction (a conflict is rare and a resolution removes nothing —
    the file is the audit trail of what needed review), so the whole file is read on
    demand and rewritten whole on an update.
    """

    def __init__(self, home: Path) -> None:
        self._path = Path(home) / CONFLICTS_PATH

    @property
    def path(self) -> Path:
        return self._path

    def _read(self) -> list[ConflictRecord]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return []
        out: list[ConflictRecord] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                # One corrupt line must never hide the rest of the queue.
                logger.warning("conflicts: skipping unparseable queue line")
                continue
            if isinstance(obj, dict):
                out.append(ConflictRecord.from_dict(obj))
        return out

    def _write_all(self, records: list[ConflictRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(
            json.dumps(r.to_dict(), sort_keys=True, ensure_ascii=False) + "\n" for r in records
        )
        atomic_write(self._path, body)

    def items(
        self, *, surface: str = "", status: str = "", entry_id: str = ""
    ) -> list[ConflictRecord]:
        """Queue contents, newest last, optionally filtered by surface/status/entry.

        The surface filter is what routes a memory-domain conflict to the memory review
        surface and a knowledge-domain one to the knowledge UI (§4.2 item 3) without either
        surface knowing about the other's records.
        """
        out = self._read()
        if surface:
            out = [r for r in out if r.surface == surface]
        if status:
            out = [r for r in out if r.status == status]
        if entry_id:
            out = [r for r in out if r.entry_id == entry_id]
        return out

    def get(self, record_id: str) -> ConflictRecord | None:
        """The queued record with this id, or ``None``. The review surface's read (DAS-10):
        a resolve acts on one record, and "not queued" has to be distinguishable from
        "queued but already reviewed" rather than both collapsing into an empty list."""
        for rec in self._read():
            if rec.id == record_id:
                return rec
        return None

    def record(self, rec: ConflictRecord) -> bool:
        """Append ``rec`` unless its id is already queued. Returns True if it was added."""
        existing = self._read()
        if any(r.id == rec.id for r in existing):
            return False
        existing.append(rec)
        self._write_all(existing)
        return True

    def update(self, rec: ConflictRecord) -> bool:
        """Replace the queued record with ``rec``'s id (the proposal-draft write path).

        Returns False when the id is not queued — a draft can never CREATE a conflict, only
        annotate one, so a vanished record is a no-op rather than a resurrection.
        """
        existing = self._read()
        for i, cur in enumerate(existing):
            if cur.id == rec.id:
                existing[i] = rec
                self._write_all(existing)
                return True
        return False

    def held_ids(self, entry_id: str) -> set[str]:
        """Entity ids in ``entry_id`` with an unresolved conflict — the ids whose LOCAL row
        stays authoritative, so the caller drops the remote side for them before merging.
        Holds across cycles: a conflict recorded last cycle still holds this cycle."""
        return {
            r.entity_id
            for r in self._read()
            if r.entry_id == entry_id and r.status == STATUS_NEEDS_REVIEW
        }
