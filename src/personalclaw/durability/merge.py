"""Deterministic row-level merge for the sync cycle (DURABILITY-AND-SYNC §4, DAS-6c-i).

The pure core the sync cycle (DAS-6c-ii) orchestrates: given local rows and the
rows imported from a remote's shards (:func:`shards.import_shards`), reconcile them
by the entry's declared ``merge`` strategy — never losing a row, never silently
picking a loser. No CRDTs: per-id union + last-write-wins + tombstones (§Anti-goals).

Every function here is PURE (no I/O, no clock) and DETERMINISTIC — same inputs →
byte-identical output — so a merge is reviewable and a re-merge of unchanged state
is a no-op (the property the cycle's free-retry-on-CAS-race depends on). The cycle
layer owns the transport, the CAS registry, and writing the merged rows back.

Strategies (mirror ``inventory.MERGE_*``):
- ``union_by_id``       — keep every row present on either side, keyed by ``id``;
                          a tombstone (``deleted_at``) on either side wins over a
                          live row with the same id (deletion survives the union).
- ``lww_by_updated_at`` — same union, but a same-id row present on both sides
                          resolves to the one with the greater ``updated_at``
                          (ties → the local row, so a merge is stable).
- ``append_dedup``      — concatenate two append-only streams, dedup by a stable
                          per-row id (a re-import is a no-op); order preserved.
- ``sqlite_attach_ignore`` / ``replace_only`` — NOT row-merged here (the cycle
                          handles DBs via the attach-ignore path and skips
                          replace_only entries); calling this module for them is a
                          programming error, raised loudly.
"""

from __future__ import annotations

from dataclasses import dataclass

# Re-exported strategy names so callers merge against one vocabulary.
from personalclaw.durability.inventory import (
    MERGE_APPEND_DEDUP,
    MERGE_LWW,
    MERGE_REPLACE_ONLY,
    MERGE_SQLITE_ATTACH_IGNORE,
    MERGE_UNION_BY_ID,
)

# The row field a tombstone sets to mark a delete (plan §4: {"id", "deleted_at"}).
_TOMBSTONE_FIELD = "deleted_at"
_DEFAULT_LWW_FIELD = "updated_at"


@dataclass
class MergeResult:
    """The reconciled rows plus a reviewable tally of what the merge did."""

    rows: list[dict]
    added: int = 0  # rows present only on the remote, brought in
    updated: int = 0  # same-id rows where the remote won (LWW) or a tombstone applied
    kept: int = 0  # local rows retained unchanged
    tombstoned: int = 0  # ids whose surviving row is a tombstone


def _id_of(row: dict) -> str:
    return str(row.get("id", ""))


def _field(row: dict, name: str):
    """A control field's value, wherever it lives in the row's two shapes.

    A JSONL stream row carries its fields at the top level; an entity-dir row is the
    exporter's ``{"id": <stem>, "data": <the file's JSON>}`` wrapper, so ``deleted_at`` /
    ``updated_at`` live under ``data``. Check the top level first (JSONL, and a hand-built
    row), then fall back to the ``data`` payload (entity-dir), so tombstones and LWW work
    for BOTH shapes without the caller knowing which it holds."""
    if name in row:
        return row[name]
    data = row.get("data")
    if isinstance(data, dict):
        return data.get(name)
    return None


def _is_tombstone(row: dict) -> bool:
    return bool(_field(row, _TOMBSTONE_FIELD))


def _lww_key(row: dict, field: str) -> str:
    """The comparable last-write value as a string; missing → "" so a row that
    carries a timestamp always beats one that doesn't (a dated edit wins over an
    undated). ISO-8601 timestamps sort chronologically as strings, which is why the
    convention is strings; a numeric ``created_at`` is stringified consistently on
    both sides so the comparison stays total and deterministic."""
    v = _field(row, field)
    return str(v) if v else ""


def _tomb_time(row: dict) -> str:
    v = _field(row, _TOMBSTONE_FIELD)
    return str(v) if v else ""


def merge_union_by_id(
    local: list[dict], remote: list[dict], *, tombstones: bool = False, lww_field: str = ""
) -> MergeResult:
    """Union two id-keyed row lists (entity dirs).

    Every id present on either side survives. A same-id collision resolves by:
      1. if either side is a tombstone → the tombstone wins (deletion survives),
         and when both are tombstones the later ``deleted_at`` wins;
      2. else, if ``lww_field`` is set → the greater ``lww_field`` wins (ties: local);
      3. else → the local row is kept (union is additive; it never overwrites a
         live local row it has no ordering signal for).

    ``tombstones=False`` disables tombstone precedence (an entry that doesn't write
    delete markers). Output rows are sorted by id for deterministic bytes.
    """
    by_id: dict[str, dict] = {}
    origin: dict[str, str] = {}  # id -> "local" | "remote" | "merged"
    for row in local:
        by_id[_id_of(row)] = row
        origin[_id_of(row)] = "local"
    result = MergeResult(rows=[])
    for row in remote:
        rid = _id_of(row)
        if rid not in by_id:
            by_id[rid] = row
            origin[rid] = "remote"
            continue
        cur = by_id[rid]
        winner = _resolve_pair(cur, row, tombstones=tombstones, lww_field=lww_field)
        if winner is not cur:
            by_id[rid] = winner
            origin[rid] = "merged"
    for rid in sorted(by_id):
        row = by_id[rid]
        result.rows.append(row)
        if origin[rid] == "remote":
            result.added += 1
        elif origin[rid] == "merged":
            result.updated += 1
        else:
            result.kept += 1
        if tombstones and _is_tombstone(row):
            result.tombstoned += 1
    return result


def _resolve_pair(local: dict, remote: dict, *, tombstones: bool, lww_field: str) -> dict:
    """Pick the winner of a same-id collision. Deterministic; ties favor local."""
    if tombstones:
        lt, rt = _is_tombstone(local), _is_tombstone(remote)
        if lt or rt:
            if lt and rt:
                # Both deleted — the later deletion is authoritative.
                return remote if _tomb_time(remote) > _tomb_time(local) else local
            return local if lt else remote
    if lww_field:
        return remote if _lww_key(remote, lww_field) > _lww_key(local, lww_field) else local
    return local


def merge_lww_by_updated_at(
    local: list[dict],
    remote: list[dict],
    *,
    tombstones: bool = False,
    field: str = _DEFAULT_LWW_FIELD,
) -> MergeResult:
    """Union-by-id with last-write-wins on ``field`` (default ``updated_at``) for
    same-id collisions. Tombstones still take precedence when enabled."""
    return merge_union_by_id(local, remote, tombstones=tombstones, lww_field=field)


def merge_append_dedup(local: list[dict], remote: list[dict], *, key: str = "id") -> MergeResult:
    """Concatenate two append-only streams, deduping by a stable per-row ``key``.

    Local order is preserved; remote rows whose key is new are appended in their
    remote order. A re-import (remote ⊆ local) adds nothing — the no-op property
    stable ids give an append stream. Rows lacking the key are kept as-is (a
    keyless row can't be a duplicate of anything), positionally after locals.
    """
    result = MergeResult(rows=list(local))
    seen = {str(r.get(key)) for r in local if r.get(key) is not None}
    result.kept = len(local)
    for row in remote:
        k = row.get(key)
        if k is not None and str(k) in seen:
            continue
        if k is not None:
            seen.add(str(k))
        result.rows.append(row)
        result.added += 1
    return result


def merge_rows(
    strategy: str,
    local: list[dict],
    remote: list[dict],
    *,
    tombstones: bool = False,
    dedup_key: str = "id",
    lww_field: str = _DEFAULT_LWW_FIELD,
) -> MergeResult:
    """Dispatch to the row-level merge for ``strategy`` (an ``inventory.MERGE_*``).

    ``sqlite_attach_ignore`` and ``replace_only`` are NOT row-merged here — the cycle
    handles a DB via ATTACH-OR-IGNORE and skips replace_only entries entirely — so
    routing one here is a caller bug, raised rather than silently mis-merged.
    """
    if strategy == MERGE_UNION_BY_ID:
        return merge_union_by_id(local, remote, tombstones=tombstones, lww_field="")
    if strategy == MERGE_LWW:
        return merge_lww_by_updated_at(local, remote, tombstones=tombstones, field=lww_field)
    if strategy == MERGE_APPEND_DEDUP:
        return merge_append_dedup(local, remote, key=dedup_key)
    if strategy in (MERGE_SQLITE_ATTACH_IGNORE, MERGE_REPLACE_ONLY):
        raise ValueError(
            f"{strategy!r} is not a row-level merge — the sync cycle handles DBs via "
            "attach-ignore and skips replace_only; do not route it through merge_rows"
        )
    raise ValueError(f"unknown merge strategy {strategy!r}")
