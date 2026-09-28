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

A ``json_file`` that holds user records (``StateEntry.records``: the automations in
``triggers.json``, the inbox's items, the tags, …) is exported as one row, the whole file, but
reconciled record by record (:func:`entity_rows`): each record is an entity with its own id,
conflict and common ancestor, and the merged records are written back into the one file, each
where this home keeps it (:func:`_merged_document`), through the file's lock
(``record_files.rewrite``) — the lock the store's own writes hold, so neither writes over the
other. Merged as one row, the file this home already had won whole, so a peer's records reached
only a home that had none. A restore's merge and an import bring an archive's records in by the
same rule (:func:`bring_in`).

A ``replace_only`` entry is restored whole or not at all — the configuration, and the stores
whose every record is a grant this home's owner gave — so a sync leaves it exactly as it is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any, Mapping, Optional

from personalclaw import record_files
from personalclaw.durability import conflicts as conflicts_mod
from personalclaw.durability import inventory as inv
from personalclaw.durability import writeback
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD
from personalclaw.durability.merge import MergeResult, _is_tombstone, merge_rows
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


def entity_rows(entry: inv.StateEntry, rows: list[dict]) -> list[dict]:
    """The entities a sync reconciles among *rows* — the exporter's rows for *entry*, this home's
    or a peer's: the rows themselves, or for a store of records (``StateEntry.records``) the
    records its one document holds.

    Only records with an id: one without cannot be told from another, so it is never merged. This
    home's stays where it is in the file (:func:`_merged_document`); a peer's is not taken in.
    """
    if entry.records is None:
        return rows
    out: list[dict] = []
    for row in rows:
        out.extend(_identified(entry, row.get("data")))
    return out


def _identified(entry: inv.StateEntry, document: Any) -> list[dict]:
    """The records with an id in *document*, one of *entry*'s files (none for another shape)."""
    assert entry.records is not None
    return _with_ids(entry.records.records(document) or [])


def _with_ids(records: list) -> list[dict]:
    """The records that have an id: the only ones anything can tell apart."""
    return [r for r in records if isinstance(r, dict) and record_files.record_id(r)]


def is_record(entry: inv.StateEntry, row: object) -> bool:
    """Whether *row* can be written as one of the records of *entry* (a store of records): it has
    an id, and is not the store's whole file. A conflict recorded before the store was reconciled
    record by record holds the whole file as each version — the exporter's one row, named for the
    file — and writing that into the file as one record would break it."""
    rid = record_files.record_id(row)
    return bool(rid) and rid != Path(entry.path).name


def _here(entry: inv.StateEntry, document: Any) -> list:
    """This home's records, in its file's order — none when it has no file. Raises when the file
    holds another shape: a write built without it would replace records it could not see, so the
    file is left for its owner to inspect, as the store's own reader leaves it."""
    assert entry.records is not None
    if document is None:
        return []
    held = entry.records.records(document)
    if held is None:
        raise ValueError(f"{entry.path} holds no records here, so it is left as it is")
    return held


def _merged_document(
    entry: inv.StateEntry,
    document: Any,
    peer_document: Any,
    arrived_order: list[str],
    merged: list[dict],
) -> Any:
    """*document* — this home's file, or None — with *merged* (its records after a merge) in it,
    or None when that is the file exactly as it is.

    Each record where this home keeps it, and the ones that arrived after them in the order the
    peer keeps them, so a sync never reorders the list a person sees. A record the merge did not
    take — one with no id, or a second with an id already placed — stays as it is. A home with no
    file gets the peer's around the records, as its store writes one.
    """
    assert entry.records is not None
    by_id = {record_files.record_id(r): r for r in merged if record_files.record_id(r)}
    items: list[object] = []
    placed: set[str] = set()
    for item in _here(entry, document):
        rid = record_files.record_id(item)
        if rid in by_id and rid not in placed:
            items.append(by_id[rid])
            placed.add(rid)
        else:
            items.append(item)
    for rid in arrived_order:
        if rid in by_id and rid not in placed:
            items.append(by_id[rid])
            placed.add(rid)
    if document is None and not items:
        return None
    updated = entry.records.document(document if document is not None else peer_document, items)
    return None if updated == document else updated


def write_record(
    entry: inv.StateEntry, dest: Path, entity_id: str, record: dict
) -> writeback.ApplyResult:
    """Put *record* in place of *entity_id*'s in a store of records, or after the others when this
    home has none — the conflict resolver's write, through the file's lock and the same guard as a
    sync's (:func:`_here`)."""
    assert entry.records is not None
    shape = entry.records

    def change(document: Any) -> Any:
        items: list[object] = []
        placed = False
        for item in _here(entry, document):
            if record_files.record_id(item) == entity_id:
                if not placed:
                    items.append(record)
                    placed = True
                continue
            items.append(item)
        if not placed:
            items.append(record)
        return shape.document(document, items)

    wrote = record_files.rewrite(dest, change)
    return writeback.ApplyResult(written=1 if wrote else 0)


def bring_in(home: Path, entry: inv.StateEntry, archived: Path) -> int:
    """Bring the records of *archived* — *entry*'s file in an archive: a snapshot, an export —
    into this home's store, by the rule a sync brings another machine's in. Returns how many came.

    One record at a time, by its ``id``: each one this home does not have arrives, by the store's
    arrival rule (``StateEntry.arrives``), after the ones it has, which stay exactly as they are —
    a merge only fills in what the home lacks. A home with no store gets the archive's, every
    record in it brought in by the same rule. Written through the file's lock
    (``record_files.rewrite``), which the store's own writes hold. Raises ``ValueError`` when
    either file cannot be read or holds another shape, leaving this home's as it is.
    """
    assert entry.records is not None
    shape = entry.records
    archived_document = record_files.read(archived)
    if shape.records(archived_document) is None:
        raise ValueError(f"the archive's {entry.path} holds no records, so nothing came from it")
    arriving = _identified(entry, archived_document)
    came = 0

    def change(document: Any) -> Any:
        nonlocal came
        here = _here(entry, document)
        known = {record_files.record_id(r) for r in here if record_files.record_id(r)}
        added: list[object] = []
        for record in arriving:
            rid = record_files.record_id(record)
            if rid in known:
                continue
            known.add(rid)
            added.append(record if entry.arrives is None else entry.arrives(record))
        came = len(added)
        if document is not None and not added:
            return None
        return shape.document(document if document is not None else archived_document, here + added)

    record_files.rewrite(Path(home) / entry.path, change)
    return came


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
    and tree via the blob store. Consumes a ``replace_only`` entry and writes nothing: it is
    restored whole or not at all, and never merged (``merge.merge_rows`` refuses one). A row
    kind that throws mid-merge is caught and reported ``payload-bad`` so a single bad entry
    advances the cursor past itself rather than wedging every later seq (§4.1).

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
    if entry.merge == inv.MERGE_REPLACE_ONLY:
        return ReconcileResult(entry.id, detail="restored whole or not at all; left as it is")
    dest = Path(home) / entry.path
    remote = entity_rows(entry, remote_rows)
    outcome: dict[str, Any] = {}

    def merge_into(local: list[dict]) -> MergeResult:
        held, recorded = _record_conflicts(entry, local, remote, ancestors, queue, now)
        effective_remote = (
            [r for r in remote if conflicts_mod.row_id(r) not in held] if held else remote
        )
        outcome.update(held=held, recorded=recorded, effective_remote=effective_remote)
        return merge_rows(
            entry.merge,
            local,
            _as_they_arrive(entry, effective_remote),
            tombstones=entry.tombstones,
            dedup_key="id",
        )

    try:
        if entry.records is not None:
            peer_document = next((r.get("data") for r in remote_rows if "data" in r), None)

            def change(document: Any) -> Any:
                merged = merge_into(_with_ids(_here(entry, document)))
                outcome["merged"] = merged
                arrived_order = [conflicts_mod.row_id(r) for r in outcome["effective_remote"]]
                return _merged_document(entry, document, peer_document, arrived_order, merged.rows)

            record_files.rewrite(dest, change)
            removed = 0
        else:
            merged = merge_into(read_local_rows(entry, dest))
            outcome["merged"] = merged
            removed = writeback.apply_rows(entry.kind, dest, merged.rows).removed
    except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the whole pull
        logger.warning("reconcile: %s failed (%s) — advancing past it", entry.id, exc)
        return ReconcileResult(entry.id, verdict=PAYLOAD_BAD, detail=str(exc))
    merged = outcome["merged"]
    held, recorded = outcome["held"], outcome["recorded"]
    detail = f"+{merged.added} ~{merged.updated} -{removed}"
    if recorded or held:
        detail += f" !{recorded} conflict(s), {len(held)} id(s) held local"
    return ReconcileResult(
        entry.id,
        verdict=CONSUMED,
        added=merged.added,
        updated=merged.updated,
        removed=removed,
        detail=detail,
        conflicts=recorded,
        new_ancestors=_agreed_shas(entry, outcome["effective_remote"], merged.rows, held),
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
