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
from pathlib import Path

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


def _read_local_rows(entry: inv.StateEntry, src: Path) -> list[dict]:
    """The entry's current on-disk rows, read the same way the exporter extracts them, so
    both sides of the merge speak the same row shape. A missing store is an empty list."""
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


def reconcile_entry(home: Path, entry: inv.StateEntry, remote_rows: list[dict]) -> ReconcileResult:
    """Merge ``remote_rows`` into ``entry``'s live store under ``home`` and write the result
    back. Returns a :class:`ReconcileResult` carrying the cursor verdict.

    Declines (``handled=False``) a non-row kind — the cycle routes sqlite via ATTACH-IGNORE
    and tree via the blob store. A row kind that throws mid-merge is caught and reported
    ``payload-bad`` so a single bad entry advances the cursor past itself rather than
    wedging every later seq (§4.1).
    """
    if not handles_kind(entry.kind):
        return ReconcileResult(entry.id, handled=False, detail=f"non-row kind {entry.kind}")
    dest = Path(home) / entry.path
    try:
        local = _read_local_rows(entry, dest)
        merged = merge_rows(
            entry.merge,
            local,
            remote_rows,
            tombstones=entry.tombstones,
            dedup_key="id",
        )
        applied = writeback.apply_rows(entry.kind, dest, merged.rows)
    except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the whole pull
        logger.warning("reconcile: %s failed (%s) — advancing past it", entry.id, exc)
        return ReconcileResult(entry.id, verdict=PAYLOAD_BAD, detail=str(exc))
    return ReconcileResult(
        entry.id,
        verdict=CONSUMED,
        added=merged.added,
        updated=merged.updated,
        removed=applied.removed,
        detail=f"+{merged.added} ~{merged.updated} -{applied.removed}",
    )
