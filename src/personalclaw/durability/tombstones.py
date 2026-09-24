"""Sync-only tombstone side-log for hard-delete entity stores (DAS-6c-iii-a, owner Fork B).

Some entity-dir stores (``tasks``, ``projects``) delete by removing the file — a hard
``unlink``, chosen so the store's own UX carries no soft-delete/undo/retention surface. But
sync's union merge needs a DELETE MARKER to survive, or a peer that still holds the row live
would resurrect it. Reconciling those two: the delete stays a hard unlink, and the store ALSO
appends ``{"id": <row_id>, "deleted_at": <iso>}`` to a **sync-only** side-log
``<entry_path>/_tombstones.jsonl`` that the durability layer folds into the export as tombstone
rows. The store never reads this file back — it is invisible to the store's own read paths (a
``.jsonl`` under an entity dir the store globs as ``*.json``); it exists purely so a delete
propagates across machines.

This module owns the log's read/write + the export fold. The delete write-sites call
:func:`record_tombstone` (wired in DAS-6c-iii-b). A GC (:func:`prune`) trims entries past the
sync horizon so the log can't grow without bound — a tombstone only needs to outlive the window
in which a peer could still be carrying the deleted row live.

Clock-free at the seam: ``now`` / ``keep_after`` are passed in, so a replay is deterministic.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: The per-entity-dir side-log filename. Leading underscore + .jsonl ⇒ invisible to the
#: store's own ``*.json`` entity glob AND to the exporter's entity-dir extraction.
TOMBSTONE_FILE = "_tombstones.jsonl"
_TOMBSTONE_FIELD = "deleted_at"


def _log_path(entry_dir: Path) -> Path:
    return Path(entry_dir) / TOMBSTONE_FILE


def record_tombstone(entry_dir: Path, row_id: str, *, now: str) -> None:
    """Append a delete marker for ``row_id`` to the entity dir's side-log.

    Best-effort and never raises: a delete must not fail because its sync breadcrumb
    couldn't be written (the worst case is a missed cross-machine delete, not a broken
    delete). Creates the dir if needed (a store may delete its last row and the dir may
    already be gone — but the marker still needs a home for the next sync)."""
    if not row_id:
        return
    path = _log_path(entry_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"id": row_id, _TOMBSTONE_FIELD: now}) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        logger.debug("tombstones: could not record delete for %s", row_id, exc_info=True)


def read_tombstones(entry_dir: Path) -> list[dict]:
    """The side-log's rows as ``{"id", "deleted_at"}`` dicts, newest-per-id winning.

    Deduped by id (a re-delete of a recreated-then-deleted row keeps the later marker), so
    the fold emits at most one tombstone per id. Order-stable (by id) for deterministic
    export bytes. A missing/corrupt log reads as empty — never fatal."""
    path = _log_path(entry_dir)
    latest: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = row.get("id")
        ts = row.get(_TOMBSTONE_FIELD, "")
        if not isinstance(rid, str) or not rid:
            continue
        # Keep the later marker (string ISO timestamps sort chronologically).
        if rid not in latest or str(ts) > latest[rid]:
            latest[rid] = str(ts)
    return [{"id": rid, _TOMBSTONE_FIELD: latest[rid]} for rid in sorted(latest)]


def merge_into_rows(entry_dir: Path, live_rows: list[dict]) -> list[dict]:
    """Fold the side-log's tombstones into an entity dir's live export rows.

    A tombstone whose id is NOT among the live rows is appended (the row was hard-deleted;
    its marker is all that remains to carry the delete). A tombstone whose id IS still live
    (the row was recreated after a delete) is DROPPED — the live row is the current truth,
    and merge's own tombstone precedence would otherwise let a stale marker delete a
    resurrected entity. Returns a new list sorted by id (deterministic export bytes)."""
    live_ids = {str(r.get("id", "")) for r in live_rows}
    extra = [t for t in read_tombstones(entry_dir) if t["id"] not in live_ids]
    if not extra:
        return live_rows
    combined = list(live_rows) + extra
    combined.sort(key=lambda r: str(r.get("id", "")))
    return combined


def prune(entry_dir: Path, *, keep_after: str) -> int:
    """Drop side-log entries whose ``deleted_at`` is <= ``keep_after`` (past the sync
    horizon). Returns how many were removed. Rewrites the log atomically; a log that
    empties is left as an empty file (cheap, and its absence already means "no tombstones").
    ``keep_after`` is an ISO timestamp the caller computes from the staleness window."""
    path = _log_path(entry_dir)
    rows = read_tombstones(entry_dir)
    if not rows:
        return 0
    kept = [r for r in rows if str(r.get(_TOMBSTONE_FIELD, "")) > keep_after]
    removed = len(rows) - len(kept)
    if removed:
        body = "".join(json.dumps(r) + "\n" for r in kept)
        atomic_write(path, body)
    return removed
