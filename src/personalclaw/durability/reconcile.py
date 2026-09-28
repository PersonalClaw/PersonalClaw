"""Reconcile a peer's rows into the live store (DAS-6c-ii-d).

The bridge that turns "I pulled a peer's shards" into "the peer's rows are now in my live
store", composing the three pure pieces already built:

    local rows  ←  read the entry's on-disk form the same way the exporter extracts it
    merged      ←  merge.merge_rows(entry.merge, local, remote, tombstones=entry.tombstones)
    live store  →  writeback.apply_rows(entry.kind, dest, merged)

It is deliberately the ROW path only — the kinds whose merge is a deterministic row
reconciliation (`json_entity_dir`, `json_file`, `jsonl_append`). A `sqlite` entry is merged
by the ATTACH-OR-IGNORE path in ``snapshot.py`` and a `tree` entry is rehydrated from the
content-addressed blob store, so :func:`reconcile_entry` DECLINES those (returns a
``handled=False`` outcome) rather than raising — the cycle engine (the change above this) reads
that verdict and routes the entry to its DB/blob path. A row-mergeable entry that raises
mid-reconcile is caught and reported as a `payload-bad` verdict so one poison entry can't
abort the whole pull; the caller advances its cursor past it rather than looping.

Reads the local rows exactly as ``shards.export_shards`` would, so the merge sees the same
row shapes on both sides — the invariant that makes convergence hold (criterion 4).

A ``json_file`` that is a list of records (``StateEntry.records``: the automations in
``triggers.json``, the hooks in ``hooks.json``) is exported as one row, the whole file, but
reconciled record by record (:func:`entity_rows`): each record is an entity with its own id,
conflict and common ancestor, and the merged records are written back into the one file, each
where this home keeps it (:func:`_write_records`). Merged as one row, the file this home already
had won whole, so a peer's records reached only a home that had none.
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
from personalclaw.durability.merge import _is_tombstone, merge_rows
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
    #: Both-sides-edited divergences recorded for review this reconcile. Their
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


def _record_id(record: object) -> str:
    """A record's id, or ``""`` for one without a string id (which nothing can tell apart)."""
    rid = record.get("id") if isinstance(record, dict) else None
    return rid if isinstance(rid, str) else ""


def entity_rows(entry: inv.StateEntry, rows: list[dict]) -> list[dict]:
    """The entities a sync reconciles among *rows* — the exporter's rows for *entry*, this home's
    or a peer's: the rows themselves, or for a store of records (``StateEntry.records``) the
    records its one document holds.

    Only records with an id: one without cannot be told from another, so it is never merged. This
    home's stays where it is in the file (:func:`_write_records`); a peer's is not taken in.
    """
    if not entry.records:
        return rows
    out: list[dict] = []
    for row in rows:
        data = row.get("data")
        items = data.get(entry.records) if isinstance(data, dict) else None
        if isinstance(items, list):
            out.extend(item for item in items if _record_id(item))
    return out


def is_record(entry: inv.StateEntry, row: object) -> bool:
    """Whether *row* can be written as one of the records of *entry* (a store of records): it has
    an id and is not a whole document of them. A conflict recorded before the store was reconciled
    record by record holds the whole file as each version, and writing that into the file as one
    record would break it."""
    if not isinstance(row, dict) or not _record_id(row):
        return False
    data = row.get("data")
    return not (isinstance(data, dict) and entry.records in data)


def _local_document(entry: inv.StateEntry, dest: Path, stored: list[dict]) -> dict | None:
    """This home's document for a store of records, or ``None`` when it has none.

    Raises when the file is there and is not a list of records under the store's key — unreadable,
    or another shape: a write built without it would replace records it could not see, so the file
    is left for its owner to inspect, as the store's own reader leaves it.
    """
    if not stored:
        if dest.exists():
            raise ValueError(f"{entry.path} could not be read here, so it is left as it is")
        return None
    data = stored[0].get("data")
    if not isinstance(data, dict) or not isinstance(data.get(entry.records), list):
        raise ValueError(
            f"{entry.path} holds no list of {entry.records} here, so it is left as it is"
        )
    return data


def _write_records(
    entry: inv.StateEntry,
    dest: Path,
    document: dict | None,
    peer_rows: list[dict],
    arrived_order: list[str],
    merged: list[dict],
) -> writeback.ApplyResult:
    """Write *merged* (a store's records after a merge) back into its one document.

    Each record where this home keeps it, and the ones that arrived after them in the order the
    peer keeps them, so a sync never reorders the list a person sees. A record the merge did not
    take — one with no id, or a second with an id already placed — stays as it is. Nothing is
    written when the document would not change: a pull that brings nothing leaves the file exactly
    as the store wrote it.
    """
    by_id = {_record_id(r): r for r in merged if _record_id(r)}
    items: list[object] = []
    placed: set[str] = set()
    for item in (document or {}).get(entry.records, []):
        rid = _record_id(item)
        if rid in by_id and rid not in placed:
            items.append(by_id[rid])
            placed.add(rid)
        else:
            items.append(item)
    for rid in arrived_order:
        if rid in by_id and rid not in placed:
            items.append(by_id[rid])
            placed.add(rid)
    if document is None:
        if not items:
            return writeback.ApplyResult()
        # No store here yet: the peer's envelope around the records, as its store writes one.
        peer: dict = next((r["data"] for r in peer_rows if isinstance(r.get("data"), dict)), {})
        base = {key: value for key, value in peer.items() if key != entry.records}
    else:
        base = document
    updated = {**base, entry.records: items}
    if updated == document:
        return writeback.ApplyResult()
    return writeback.apply_rows(entry.kind, dest, [{"id": dest.name, "data": updated}])


def write_record(
    entry: inv.StateEntry, dest: Path, entity_id: str, record: dict
) -> writeback.ApplyResult:
    """Put *record* in place of *entity_id*'s in a store of records, or after the others when this
    home has none — the conflict resolver's write, through the same document read and the same
    guard as a sync's (:func:`_local_document`)."""
    document = _local_document(entry, dest, read_local_rows(entry, dest))
    items: list[object] = []
    placed = False
    for item in (document or {}).get(entry.records, []):
        if _record_id(item) == entity_id:
            if not placed:
                items.append(record)
                placed = True
            continue
        items.append(item)
    if not placed:
        items.append(record)
    updated = {**(document or {}), entry.records: items}
    return writeback.apply_rows(entry.kind, dest, [{"id": dest.name, "data": updated}])


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
        stored = read_local_rows(entry, dest)
        document = _local_document(entry, dest, stored) if entry.records else None
        local = entity_rows(entry, stored)
        remote = entity_rows(entry, remote_rows)
        held, recorded = _record_conflicts(entry, local, remote, ancestors, queue, now)
        effective_remote = (
            [r for r in remote if conflicts_mod.row_id(r) not in held] if held else remote
        )
        merged = merge_rows(
            entry.merge,
            local,
            _as_they_arrive(entry, effective_remote),
            tombstones=entry.tombstones,
            dedup_key="id",
        )
        if entry.records:
            arrived_order = [conflicts_mod.row_id(r) for r in effective_remote]
            applied = _write_records(entry, dest, document, remote_rows, arrived_order, merged.rows)
        else:
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
        new_ancestors=_agreed_shas(entry, effective_remote, merged.rows, held),
    )


def _as_they_arrive(entry: inv.StateEntry, remote_rows: list[dict]) -> list[dict]:
    """The peer's rows as this home's store takes them in (``StateEntry.arrives``). A tombstone is
    a deletion rather than a row, and passes as it is.

    Only the merge sees them: the conflict check and :func:`_agreed_shas` read the rows the peer
    published, through what two homes compare of them (``conflicts.compared``)."""
    if entry.arrives is None:
        return remote_rows
    return [row if _is_tombstone(row) else entry.arrives(row) for row in remote_rows]


def _agreed_shas(
    entry: inv.StateEntry, remote_rows: list[dict], merged_rows: list[dict], held: set[str]
) -> dict[str, str]:
    """The ids whose merged row is the row the peer published, as two homes compare it
    (``conflicts.compared``) — the only ones we can honestly call a common ancestor (see
    ``ReconcileResult.new_ancestors``).

    For a store that holds what is one home's, a row it brought in is not the peer's byte for byte
    (``StateEntry.arrives``), and what both homes do with it afterwards changes it on each; what a
    person makes of it is the same on both, which is what the next divergence is measured from."""
    remote_shas = {
        conflicts_mod.row_id(r): conflicts_mod.row_sha(conflicts_mod.compared(entry, r))
        for r in remote_rows
        if conflicts_mod.row_id(r)
    }
    out: dict[str, str] = {}
    for row in merged_rows:
        rid = conflicts_mod.row_id(row)
        if not rid or rid in held:
            continue
        sha = conflicts_mod.row_sha(conflicts_mod.compared(entry, row))
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
