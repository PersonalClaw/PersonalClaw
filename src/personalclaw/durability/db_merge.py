"""The sqlite DB-merge seam for the sync cycle.

The pull engine reconciles row entries itself but DECLINES `sqlite`/`tree` entries, calling an
injected ``db_merger(entry, shard_dir) -> verdict`` for them. This module is that
callback. It finds the whole-DB copy the exporter staged under ``db/<entry_id>.db``
and ATTACH-merges it into the live database, reusing the proven snapshot merge machinery rather
than inventing a second one:

* ``memory.db`` → ``snapshot._merge_memory`` — a four-table allowlist that filters
  ``WHERE is_deleted=0`` so a synced-in copy never RESURRECTS a memory the user deleted (the
  reason memory.db keeps its own executor instead of the generic all-tables path).
* every other ``KIND_SQLITE`` entry → ``snapshot._merge_sqlite_attach`` — every real table
  merged with ``INSERT OR IGNORE``, FTS shadow tables skipped and the index rebuilt, a table the
  store numbers by the key or identity that names its rows, and a store that declares how it is
  opened (the learning log) opened that way on both sides first.

Embeddings ride the ATTACH (they live IN the DB, carried by the merge — matching the snapshot
precedent, so a synced memory embedded by the model bound here is searchable without
re-embedding). One embedded by another model is not: each merge says rows arrived
(``embedding_arrivals``), and the gateway's one re-index path re-embeds what the model bound here
did not embed. Derived indexes (``memory.faiss`` / ``memory_index.db``, `derived=True`) are NOT
synced and are rebuilt locally by their owning subsystems (boot rebuild + the heartbeat reindex) —
this merger touches only the authoritative DB: indexes are rebuilt on import, never synced.

Verdicts returned to the cursor: ``consumed`` on a clean merge; ``prerequisite-absent`` if this
seq carried no DB copy for the entry (a row-only export mis-routed here — hold, don't advance
past unmerged data); ``payload-bad`` if the merge itself fails or leaves the DB, or a table of
it, unchanged (advance past a poison DB so it can't wedge every later seq).
"""

from __future__ import annotations

import logging
from pathlib import Path

from personalclaw import embedding_arrivals
from personalclaw.durability import home_paths
from personalclaw.durability import inventory as inv
from personalclaw.durability import sqlite_files
from personalclaw.durability.cursor import CONSUMED, PAYLOAD_BAD, PREREQ_ABSENT
from personalclaw.durability.pull_engine import DbMerger

logger = logging.getLogger(__name__)

_MEMORY_DB_ENTRY = "memory_db"


def make_db_merger(home: Path) -> DbMerger:
    """Build the ``db_merger`` callback the pull engine calls for sqlite/tree entries.

    Closes over the live ``home`` so the pull engine's ``(entry, shard_dir)`` signature is
    honored. The staged DB copy is at ``shard_dir/db/<entry_id>.db``.
    """

    def _merge(entry: inv.StateEntry, shard_dir: Path) -> str:
        # Only sqlite entries carry a mergeable DB copy, and the shards carry no folder of files
        # (`inventory.shard_entries`): anything else is consumed, and nothing written.
        if entry.kind != inv.KIND_SQLITE:
            return CONSUMED
        if entry.machine_local:
            # What stays on each machine (`StateEntry.machine_local`: a workflow run's records, a
            # loop's) never takes another machine's in, from a build that still sends it too: the
            # watchdogs here would resume its running ones.
            logger.info("db_merge: %s is this machine's own; left as it is", entry.id)
            return CONSUMED
        src = Path(shard_dir) / "db" / f"{entry.id}.db"
        if not src.is_file():
            # The peer exported rows but no DB copy for this entry (an older/row-only export).
            # Hold rather than advance past a sqlite entry we can't losslessly merge from rows.
            logger.info("db_merge: %s has no staged DB copy — holding", entry.id)
            return PREREQ_ABSENT
        try:
            dst = home_paths.home_path(home, entry.path)
        except home_paths.LinkInTheWay as link:
            # Nothing of the copy goes through a link this home holds, nor into the file a hard
            # link shares: held, so it comes in once the link is gone (the pull names it).
            logger.warning("db_merge: %s left as it is: %s — holding", entry.id, link)
            return PREREQ_ABSENT
        try:
            left = _apply_db_merge(entry, src, dst)
        except Exception as exc:  # noqa: BLE001 — one bad DB must not wedge the whole pull
            logger.warning("db_merge: %s failed (%s) — advancing past it", entry.id, exc)
            return PAYLOAD_BAD
        embedding_arrivals.arrived()
        if left:
            # The merge functions report what they could not bring in rather than raising, so
            # a restore goes on to the next store; here that is this seq's verdict.
            logger.warning(
                "db_merge: %s left unchanged: %s — advancing past it", entry.id, ", ".join(left)
            )
            return PAYLOAD_BAD
        return CONSUMED

    return _merge


def _apply_db_merge(entry: inv.StateEntry, src: Path, dst: Path) -> list[str]:
    """ATTACH-merge ``src`` into the live ``dst`` DB, a path ``home_paths.home_path`` gave, via the
    right snapshot merge function, and return what it left unchanged (empty when everything came
    in).

    A live DB that doesn't exist yet is created by copying the source wholesale (the first sync
    onto a fresh machine), on its own (`sqlite_files.bring_in`: a log left at its path with no
    database would be read into it); otherwise the merge functions ATTACH and INSERT OR IGNORE,
    so the live machine's own rows are never overwritten and — for memory.db — deletions survive.
    """
    from personalclaw import snapshot

    if sqlite_files.bring_in(src, dst):
        # No local DB yet: the source IS the merged result. Byte-copy is correct and lossless.
        return []
    left: list[str] = []
    if entry.id == _MEMORY_DB_ENTRY:
        # memory.db: the is_deleted=0 allowlist merge, so deletes are not resurrected.
        snapshot._merge_memory(src, dst, left_unchanged=left)
    else:
        snapshot._merge_sqlite_attach(src, dst, entry.path, left_unchanged=left)
    return left
