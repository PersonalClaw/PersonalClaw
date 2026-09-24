"""The sqlite DB-merge seam for the sync cycle (DURABILITY-AND-SYNC §4.1, DAS-6c-ii-h).

The pull engine reconciles row entries itself but DECLINES `sqlite`/`tree` entries, calling an
injected ``db_merger(entry, shard_dir) -> verdict`` for them (DAS-6c-ii-e). This module is that
callback. It finds the whole-DB copy the exporter staged under ``db/<entry_id>.db`` (DAS-6c-ii-g)
and ATTACH-merges it into the live database, reusing the proven snapshot merge machinery rather
than inventing a second one:

* ``memory.db`` → ``snapshot._merge_memory`` — a four-table allowlist that filters
  ``WHERE is_deleted=0`` so a synced-in copy never RESURRECTS a memory the user deleted (the
  reason memory.db keeps its own executor instead of the generic all-tables path).
* every other ``KIND_SQLITE`` entry → ``snapshot._merge_sqlite_attach`` — every real table
  merged with ``INSERT OR IGNORE``, FTS shadow tables skipped and the index rebuilt.

Embeddings ride the ATTACH (they live IN the DB, carried by the merge — matching the snapshot
precedent, so a synced memory is searchable without re-embedding). Derived indexes
(``memory.faiss`` / ``memory_index.db``, `derived=True`) are NOT synced and are rebuilt locally
by their owning subsystems (boot rebuild + the heartbeat reindex) — this merger touches only the
authoritative DB, per §4.1 "indexes rebuilt on import, never synced".

Verdicts returned to the cursor: ``consumed`` on a clean merge; ``prerequisite-absent`` if this
seq carried no DB copy for the entry (a row-only export mis-routed here — hold, don't advance
past unmerged data); ``payload-bad`` if the merge itself fails (advance past a poison DB so it
can't wedge every later seq).
"""

from __future__ import annotations

import logging
from pathlib import Path

from personalclaw.durability import inventory as inv
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD, PREREQ_ABSENT
from personalclaw.durability.pull_engine import DbMerger

logger = logging.getLogger(__name__)

_MEMORY_DB_ENTRY = "memory_db"


def make_db_merger(home: Path) -> DbMerger:
    """Build the ``db_merger`` callback the pull engine calls for sqlite/tree entries.

    Closes over the live ``home`` so the pull engine's ``(entry, shard_dir)`` signature is
    honored. The staged DB copy is at ``shard_dir/db/<entry_id>.db`` (DAS-6c-ii-g).
    """

    def _merge(entry: inv.StateEntry, shard_dir: Path) -> str:
        # Only sqlite entries carry a mergeable DB copy. A `tree` entry (faiss) is derived and
        # rebuilt locally — nothing to merge, so treat it as consumed (no data to lose).
        if entry.kind != inv.KIND_SQLITE:
            return CONSUMED
        src = Path(shard_dir) / "db" / f"{entry.id}.db"
        if not src.is_file():
            # The peer exported rows but no DB copy for this entry (an older/row-only export).
            # Hold rather than advance past a sqlite entry we can't losslessly merge from rows.
            logger.info("db_merge: %s has no staged DB copy — holding", entry.id)
            return PREREQ_ABSENT
        dst = Path(home) / entry.path
        try:
            _apply_db_merge(entry.id, src, dst)
        except Exception as exc:  # noqa: BLE001 — one bad DB must not wedge the whole pull
            logger.warning("db_merge: %s failed (%s) — advancing past it", entry.id, exc)
            return PAYLOAD_BAD
        return CONSUMED

    return _merge


def _apply_db_merge(entry_id: str, src: Path, dst: Path) -> None:
    """ATTACH-merge ``src`` into the live ``dst`` DB via the right snapshot merge function.

    A live DB that doesn't exist yet is created by copying the source wholesale (the first sync
    onto a fresh machine); otherwise the merge functions ATTACH and INSERT OR IGNORE, so the
    live machine's own rows are never overwritten and — for memory.db — deletions survive.
    """
    from personalclaw import snapshot

    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        # No local DB yet: the source IS the merged result. Byte-copy is correct and lossless.
        import shutil

        shutil.copy2(src, dst)
        return
    if entry_id == _MEMORY_DB_ENTRY:
        # memory.db: the is_deleted=0 allowlist merge, so deletes are not resurrected.
        snapshot._merge_memory(src, dst)
    else:
        snapshot._merge_sqlite_attach(src, dst, entry_id)
