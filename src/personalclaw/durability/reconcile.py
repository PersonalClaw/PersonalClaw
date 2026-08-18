"""Reconcile a peer's rows into the live store (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-d).

The bridge that turns "I pulled a peer's shards" into "the peer's rows are now in my live
store", composing the three pure pieces already built:

    local rows  ←  read the entry's on-disk form the same way the exporter extracts it
    merged      ←  merge.merge_rows(entry.merge, local, remote, tombstones=entry.tombstones)
    live store  →  writeback.apply_rows(entry.kind, dest, merged)

It is deliberately the ROW path only — the kinds whose merge is a deterministic row
reconciliation (`json_entity_dir`, `json_file`, `jsonl_append`). A `sqlite` entry is merged
by the ATTACH-OR-IGNORE path in ``snapshot.py`` and a `tree` entry is rehydrated from the
content-addressed blob store, so :func:`reconcile_entry` DECLINES those (returns a
``handled=False`` outcome) rather than raising — the cycle engine (the atom above this) reads
that verdict and routes the entry to its DB/blob path. A row-mergeable entry that raises
mid-reconcile is caught and reported as a `payload-bad` verdict so one poison entry can't
abort the whole pull; the caller advances its cursor past it (per §4.1) rather than looping.

Reads the local rows exactly as ``shards.export_shards`` would, so the merge sees the same
row shapes on both sides — the invariant that makes convergence hold (criterion 4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Mapping, Optional

from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import writeback
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD
from personalclaw.durability.merge import merge_rows
from personalclaw.durability.shards import (
    _json_rows_from_entity_dir,
    _json_rows_from_file,
    _jsonl_rows_by_year,
)

logger = logging.getLogger(__name__)

# The row-mergeable kinds this bridge handles; everything else is another path's job.
_ROW_KINDS = frozenset({inv.KIND_JSON_ENTITY_DIR, inv.KIND_JSON_FILE, inv.KIND_JSONL_APPEND})


@dataclass
class ReconcileResult:
    """The outcome of reconciling one entry — a consume verdict the cursor understands.

    ``handled`` is False when the entry is not a row-merge kind (sqlite/tree) — the caller
    routes it elsewhere and does NOT treat that as consumed. ``verdict`` is the
    cursor verdict for a handled entry: ``consumed`` on a clean merge, ``payload-bad`` when
    the entry's rows were structurally unusable (advance past it, don't loop).
    """

    entry_id: str
    handled: bool = True
    verdict: str = CONSUMED
    added: int = 0
    updated: int = 0
    removed: int = 0
    detail: str = ""
    #: Both-sides-edited divergences recorded for review this reconcile (DAS-7, §4.2). Their
    #: remote rows were HELD — the local rows are byte-identical to before.
    conflicts: int = 0
    #: ``entity id → content sha`` for the ids where the merge landed on the row the PEER
    #: also holds (converged, or the remote won) — the only shas that are evidence of a
    #: common ancestor. Excludes every held (conflicted) id, and every id where the LOCAL
    #: row won: the peer has not seen that row yet, so claiming agreement on it would mask
    #: the next real divergence as a one-sided fast-forward. The peer records it (as its own
    #: remote fast-forward) once it pulls our export, and the shared registry hands it back.
    new_ancestors: dict[str, str] = dataclass_field(default_factory=dict)


def read_local_rows(entry: inv.StateEntry, src: Path) -> list[dict]:
    """The entry's current on-disk rows, read the same way the exporter extracts them, so
    both sides of the merge speak the same row shape. A missing store is an empty list.

    Public because the conflict resolver reads the same rows for the same reason (DAS-10):
    a resolution substitutes one row into this exact set, so reading it any other way would
    let a review write reshape the store."""
    if entry.kind == inv.KIND_JSON_ENTITY_DIR:
        return _json_rows_from_entity_dir(src) if src.is_dir() else []
    if entry.kind == inv.KIND_JSON_FILE:
        return _json_rows_from_file(src) if src.is_file() else []
    if entry.kind == inv.KIND_JSONL_APPEND:
        files = [src] if src.is_file() else (sorted(src.rglob("*.jsonl")) if src.is_dir() else [])
        rows: list[dict] = []
        for path in files:
            for _year, bucket in _jsonl_rows_by_year(path).items():
                rows.extend(bucket)
        return rows
    return []  # non-row kind — never reached (caller checks handles_kind first)


def handles_kind(kind: str) -> bool:
    """Whether :func:`reconcile_entry` owns this inventory kind (a row-merge kind)."""
    return kind in _ROW_KINDS


def reconcile_entry(
    home: Path,
    entry: inv.StateEntry,
    remote_rows: list[dict],
    *,
    ancestors: Optional[Mapping[str, str]] = None,
    queue: Optional[conflicts_mod.ConflictQueue] = None,
    now: str = "",
) -> ReconcileResult:
    """Merge ``remote_rows`` into ``entry``'s live store under ``home`` and write the result
    back. Returns a :class:`ReconcileResult` carrying the cursor verdict.

    Declines (``handled=False``) a non-row kind — the cycle routes sqlite via ATTACH-IGNORE
    and tree via the blob store. A row kind that throws mid-merge is caught and reported
    ``payload-bad`` so a single bad entry advances the cursor past itself rather than
    wedging every later seq (§4.1).

    **Conflict handling (DAS-7, §4.2).** With ``ancestors`` (the shared registry's agreed
    shas for this family) and a ``queue``, every id whose local AND remote row both moved
    since the ancestor is recorded for review and then **HELD**: its remote row is dropped
    before the merge, so the local bytes are untouched and the local version stays
    authoritative until a human resolves. Held ids also keep their old ancestor, so the
    conflict re-detects next cycle instead of quietly self-resolving. A conflicted entry is
    still ``consumed`` — the divergence is durably recorded, so re-pulling the same seq
    forever would add nothing and would wedge the cursor.
    """
    if not handles_kind(entry.kind):
        return ReconcileResult(entry.id, handled=False, detail=f"non-row kind {entry.kind}")
    dest = Path(home) / entry.path
    try:
        local = read_local_rows(entry, dest)
        held, recorded = _record_conflicts(entry, local, remote_rows, ancestors, queue, now)
        effective_remote = (
            [r for r in remote_rows if conflicts_mod.row_id(r) not in held] if held else remote_rows
        )
        merged = merge_rows(
            entry.merge,
            local,
            effective_remote,
            tombstones=entry.tombstones,
            dedup_key="id",
        )
        applied = writeback.apply_rows(entry.kind, dest, merged.rows)
    except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the whole pull
        logger.warning("reconcile: %s failed (%s) — advancing past it", entry.id, exc)
        return ReconcileResult(entry.id, verdict=PAYLOAD_BAD, detail=str(exc))
    detail = f"+{merged.added} ~{merged.updated} -{applied.removed}"
    if recorded or held:
        detail += f" !{recorded} conflict(s), {len(held)} id(s) held local"
    return ReconcileResult(
        entry.id,
        verdict=CONSUMED,
        added=merged.added,
        updated=merged.updated,
        removed=applied.removed,
        detail=detail,
        conflicts=recorded,
        new_ancestors=_agreed_shas(effective_remote, merged.rows, held),
    )


def _agreed_shas(
    remote_rows: list[dict], merged_rows: list[dict], held: set[str]
) -> dict[str, str]:
    """The ids whose merged row is byte-identical to the row the peer published — the only
    ones we can honestly call a common ancestor (see ``ReconcileResult.new_ancestors``)."""
    remote_shas = {
        conflicts_mod.row_id(r): conflicts_mod.row_sha(r)
        for r in remote_rows
        if conflicts_mod.row_id(r)
    }
    out: dict[str, str] = {}
    for row in merged_rows:
        rid = conflicts_mod.row_id(row)
        if not rid or rid in held:
            continue
        sha = conflicts_mod.row_sha(row)
        if remote_shas.get(rid) == sha:
            out[rid] = sha
    return out


def _record_conflicts(
    entry: inv.StateEntry,
    local: list[dict],
    remote_rows: list[dict],
    ancestors: Optional[Mapping[str, str]],
    queue: Optional[conflicts_mod.ConflictQueue],
    now: str,
) -> tuple[set[str], int]:
    """Detect + queue this entry's both-sides-edited divergences.

    Returns ``(held ids, newly recorded count)``. Held is the union of what was detected now
    and what is still unresolved in the queue from an earlier cycle — "local stays
    authoritative until resolved" has to survive across cycles, not just the cycle that
    detected the conflict. Without a queue nothing is held: a caller that cannot record a
    conflict must not silently suppress a remote row either (the merge stays as it was).
    """
    if queue is None:
        return set(), 0
    detected = conflicts_mod.detect_conflicts(entry, local, remote_rows, ancestors or {}, now=now)
    recorded = 0
    for rec in detected:
        if queue.record(rec):
            recorded += 1
            logger.warning(
                "reconcile: %s/%s diverged on both sides — queued for review (local held)",
                entry.id,
                rec.entity_id,
            )
    held = {rec.entity_id for rec in detected} | queue.held_ids(entry.id)
    return held, recorded
