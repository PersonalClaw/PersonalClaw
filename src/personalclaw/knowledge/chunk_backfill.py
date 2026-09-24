"""Resumable batched chunk backfill for pre-chunking knowledge items (KL-12, H1.5).

Chunking (KL-9), the chunk-level vector arm (KL-10) and its ANN index (KL-11) all only
reach items that HAVE chunks. Every item ingested before KL-9 has none, so on a real
library those three atoms do nothing until this backfill runs. It walks the un-chunked
items in bounded batches, runs each through the ingest path's own chunk-write unit, and
reports progress — the payload is that an existing library becomes searchable by content.

**Resume state is the data, not a cursor.** The backlog is defined by a query
(``store.count_items_missing_chunks`` / ``store.items_missing_chunks``: active,
non-archived, content-bearing, no chunk rows), so an item leaves the backlog the instant its
chunks
land. A killed run therefore resumes simply by asking again — nothing to persist, nothing
to reconcile, and no window in which a crash could strand a cursor that disagrees with the
rows. This is the same idiom as the store's ``count_items_missing_embedding`` boot
auto-resume and the ingest queue's ``recover_pending``, which likewise re-derive their
pending set from the rows rather than remembering it.

**Idempotence.** A completed backfill is a no-op: every item it chunked now fails the
backlog predicate. That matters more than tidiness — re-chunking mints fresh chunk uuids
(see ``store.replace_chunks``), so a careless re-run would churn the ANN index and orphan
rows for no gain.

**Not a re-embed.** The item rows' own whole-item vectors are never touched; that is
``store.reembed_all``'s job, driven by the embedding-model-switch re-index. This backfill
only adds the chunk layer beneath them, which is why a half-backfilled library still
searches: the vector arm falls back to whole-item vectors for items that have no chunks
yet (KL-10), so results degrade in specificity, never to zero.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Items pulled from the backlog per batch. Bounds peak memory: a backfill holds at most
#: this many items' full content at once, so a library of any size costs the same. Small
#: because item content is unbounded (a 30+ page PDF is ~100 KB of text), and since KL-15 an
#: item's chunks are embedded in ONE batched call inside ``embed_item_chunks`` — the provider
#: round trips already amortize within each item, so raising this bound would buy memory
#: pressure rather than throughput. Batching ACROSS items is deliberately not done: each
#: item's chunk write is its own transaction, which is what lets a killed run resume from the
#: rows instead of a cursor.
BATCH_SIZE = 25


def backfill_item_chunks(
    store: Any,
    embedder: Any,
    *,
    batch_size: int = BATCH_SIZE,
    max_items: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Chunk every active content-bearing item that has no chunks yet.

    ``on_progress(done, total)`` fires after each item, matching ``reembed_all``'s
    signature so the same job/SSE plumbing can drive either. *max_items* bounds one
    invocation (the rest stays in the backlog for the next run — that is the whole point of
    a resumable backfill). Returns counts::

        {"chunked": n, "unchanged": n, "failed": n, "done": n, "remaining": n, "total": n}

    - ``chunked`` — items that gained at least one chunk row.
    - ``unchanged`` — items that wrote no chunk rows: either the chunker declined to split
      them (content that is blank to Python's ``str.strip()`` but not to SQLite's), or the
      ingest chunk unit swallowed a fault, which by design it never raises. They stay in
      the backlog for the next run; the keyset cursor still steps past them within this
      one, so a run always terminates.
    - ``failed`` — items whose chunk write raised out of the ingest unit anyway.

    Never raises: a backfill fault must leave the library exactly as searchable as it was.
    """
    total = int(store.count_items_missing_chunks())
    result = {
        "chunked": 0,
        "unchanged": 0,
        "failed": 0,
        "done": 0,
        "remaining": total,
        "total": total,
    }
    if total <= 0:
        return result

    # A usable embedder is a precondition, not a per-item concern. Without one,
    # ``embed_item_chunks`` writes nothing at all, so the run would report progress while
    # leaving the backlog untouched — a silent no-op dressed as a success.
    if not callable(getattr(embedder, "embed", None)):
        logger.info(
            "Knowledge chunk backfill deferred: %d item(s) need chunking but the active "
            "embedding model has no embed() — the library stays keyword-searchable and "
            "the backfill resumes once a model is bound.",
            total,
        )
        result["skipped_no_embedder"] = True
        return result

    from personalclaw.knowledge.pipeline.runner import embed_item_chunks

    logger.info("Knowledge chunk backfill starting: %d item(s) to chunk", total)
    after_id: str | None = None
    budget = total if max_items is None else min(total, max(0, int(max_items)))
    while result["done"] < budget:
        want = min(int(batch_size), budget - result["done"])
        batch = store.items_missing_chunks(limit=want, after_id=after_id)
        if not batch:
            break  # backlog drained (or another writer took the rest) — done either way
        for row in batch:
            item_id = row["id"]
            after_id = item_id
            try:
                # Through the ingest path's own unit, so the write lands via
                # store.replace_chunks and the ANN index is updated with it.
                embed_item_chunks(store, item_id, row["content"], embedder)
                wrote = len(store.get_chunks(item_id))
                if wrote:
                    result["chunked"] += 1
                else:
                    result["unchanged"] += 1
            except Exception:  # noqa: BLE001 — one bad item must not end the backfill
                logger.debug("chunk backfill failed for %s", item_id, exc_info=True)
                result["failed"] += 1
            result["done"] += 1
            if on_progress is not None:
                on_progress(result["done"], total)

    result["remaining"] = int(store.count_items_missing_chunks())
    logger.info(
        "Knowledge chunk backfill: chunked %d, unchanged %d, failed %d (%d still pending)",
        result["chunked"],
        result["unchanged"],
        result["failed"],
        result["remaining"],
    )
    return result
